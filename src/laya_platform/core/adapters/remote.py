"""RemoteEngine: a DecisionEngine that is a client of a ``/v1/systemone`` endpoint.

The endpoint is the upstream wire protocol (``laya-serve``, or any server that keeps its contract):
``POST /v1/systemone`` for one state and ``POST /v1/systemone/batch`` for many states that share
one question set. Payloads come back exactly as the server sent them. There is no server here, no
platform authentication, and no retries, circuit breaker or micro-batching (later phases).

The transport is injectable: anything with :meth:`HttpTransport.post` works, which is how tests
drive the upstream app in-process with no network. The default uses the standard library.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, Unpack

from laya.serve import MAX_BATCH_STATES

from laya_platform.core.adapters._requests import check_requests, group_requests
from laya_platform.core.errors import (
    RemoteEngineError,
    UnsupportedControlError,
    UnsupportedOperationError,
)
from laya_platform.core.types import (
    BatchControls,
    DecisionPayload,
    DecisionRequest,
    PredictControls,
    Questions,
    RouteHints,
    RoutePayload,
    State,
)

PREDICT_PATH = "/v1/systemone"
BATCH_PATH = "/v1/systemone/batch"
#: Per-request fields the HTTP batch body carries once for all of its states.
ITEM_FIELDS = ("model", "task", "lang", "lang_guess", "max_len", "head_max_len")


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes
    headers: Mapping[str, str] = field(default_factory=dict)


class HttpTransport(Protocol):
    def post(
        self, url: str, body: bytes, headers: Mapping[str, str], timeout: float
    ) -> HttpResponse:
        """Send one POST; return the response whatever its status, raise only on no response."""
        ...


class UrllibTransport:
    """Standard-library transport (honours the usual ``HTTP(S)_PROXY`` environment variables)."""

    def post(
        self, url: str, body: bytes, headers: Mapping[str, str], timeout: float
    ) -> HttpResponse:
        # S310: RemoteEngine only builds http(s) URLs (checked in its constructor).
        request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")  # noqa: S310
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                return HttpResponse(response.status, response.read(), dict(response.headers))
        except urllib.error.HTTPError as error:
            with error:
                return HttpResponse(error.code, error.read(), dict(error.headers or {}))
        except (urllib.error.URLError, OSError) as error:
            reason = getattr(error, "reason", error)
            raise RemoteEngineError(f"POST {url} failed: {reason}") from error


class RemoteEngine:
    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        timeout: float = 30.0,
        transport: HttpTransport | None = None,
    ) -> None:
        parts = urllib.parse.urlsplit(base_url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError(f"base_url must be an http(s) URL, got {base_url!r}")
        if parts.query or parts.fragment:
            raise ValueError("base_url must not carry a query or a fragment")
        if not timeout > 0:
            raise ValueError(f"timeout must be a positive number of seconds, got {timeout!r}")
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = float(timeout)
        self._transport: HttpTransport = transport or UrllibTransport()

    @property
    def base_url(self) -> str:
        return self._base_url

    def __repr__(self) -> str:  # never shows the API key
        return f"RemoteEngine({self._base_url!r})"

    def predict(
        self, state: State, questions: Questions, **controls: Unpack[PredictControls]
    ) -> DecisionPayload:
        _refuse_callable(controls)
        payload: DecisionPayload = self._post(
            PREDICT_PATH, {"state": state, "questions": questions, **controls}
        )
        if not isinstance(payload.get("answers"), dict):
            raise RemoteEngineError(f"POST {PREDICT_PATH} returned something that is not a payload")
        return payload

    def predict_batch(
        self, requests: Sequence[DecisionRequest], **controls: Unpack[BatchControls]
    ) -> list[DecisionPayload]:
        check_requests(requests, allowed=ITEM_FIELDS, engine="RemoteEngine", routing=True)
        for request in requests:
            _refuse_callable(request)
        results: dict[int, DecisionPayload] = {}
        for indices, first in group_requests(requests, ITEM_FIELDS):
            fields: Mapping[str, Any] = first
            shared = {key: fields[key] for key in ITEM_FIELDS if fields.get(key) is not None}
            for start in range(0, len(indices), MAX_BATCH_STATES):
                chunk = indices[start : start + MAX_BATCH_STATES]
                body = {
                    "states": [requests[index]["state"] for index in chunk],
                    "questions": first["questions"],
                    **shared,
                    **controls,
                }
                payloads = self._post(BATCH_PATH, body).get("results")
                if not isinstance(payloads, list) or len(payloads) != len(chunk):
                    raise RemoteEngineError(
                        f"POST {BATCH_PATH} did not return one result per state ({len(chunk)})"
                    )
                results.update(zip(chunk, payloads, strict=True))
        return [results[index] for index in range(len(requests))]

    def route(
        self, state: State, questions: Questions | None = None, **hints: Unpack[RouteHints]
    ) -> RoutePayload:
        raise UnsupportedOperationError(
            "the /v1/systemone protocol has no routing endpoint; each payload carries the routing "
            "block the server decided"
        )

    def _post(self, path: str, body: Mapping[str, Any]) -> Any:
        try:
            data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise TypeError(f"the request cannot be sent as JSON: {error}") from None
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        response = self._transport.post(self._base_url + path, data, headers, self._timeout)
        try:
            decoded = json.loads(response.body)
        except ValueError:
            decoded = None
        if response.status != 200:
            detail = decoded.get("detail") if isinstance(decoded, dict) else None
            message = f"POST {path} returned HTTP {response.status}"
            raise RemoteEngineError(
                f"{message}: {detail}" if detail is not None else message,
                status=response.status,
                detail=None if detail is None else str(detail),
            )
        if not isinstance(decoded, dict):
            raise RemoteEngineError(f"POST {path} returned a body that is not a JSON object")
        return decoded


def _refuse_callable(controls: Mapping[str, Any]) -> None:
    if callable(controls.get("lang_guess")):
        raise UnsupportedControlError(
            "lang_guess must be a language code for RemoteEngine: a callable cannot be sent over "
            "HTTP (install a detector on the server's Router instead)"
        )
