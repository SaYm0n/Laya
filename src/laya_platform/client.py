"""Minimal client for an existing system: ask the gateway, or shadow a decision without waiting.

``shadow()`` is the integration that cannot hurt the caller: it queues the request on a small
background pool and returns at once; a slow, failing or unreachable gateway is counted, never
raised. Standard library only (it reuses the core's HTTP transport).
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from laya_platform.core.adapters.remote import HttpTransport, UrllibTransport
from laya_platform.core.errors import RemoteEngineError

DECIDE_PATH = "/api/v1/decide"


class PlatformClient:
    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        timeout: float = 2.0,
        transport: HttpTransport | None = None,
        max_pending: int = 1000,
        workers: int = 2,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._transport: HttpTransport = transport or UrllibTransport()
        self._slots = threading.BoundedSemaphore(max_pending)
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="laya-shadow")
        self._lock = threading.Lock()
        self.sent = self.failed = self.dropped = 0

    def __repr__(self) -> str:  # never shows the API key
        return f"PlatformClient({self.base_url!r})"

    def decide(
        self, spec: str, state: Any, *, incumbent: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Ask for a decision and wait for it. Raises ``RemoteEngineError`` on failure."""
        body: dict[str, Any] = {"spec": spec, "state": state}
        if incumbent is not None:
            body["incumbent"] = incumbent
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        response = self._transport.post(self.base_url + DECIDE_PATH, data, headers, self._timeout)
        try:
            decoded = json.loads(response.body)
        except ValueError:
            decoded = None
        if response.status != 200 or not isinstance(decoded, dict):
            detail = decoded.get("detail") if isinstance(decoded, dict) else None
            raise RemoteEngineError(
                f"POST {DECIDE_PATH} returned HTTP {response.status}",
                status=response.status,
                detail=None if detail is None else str(detail),
            )
        return decoded

    def shadow(self, spec: str, state: Any, *, incumbent: dict[str, Any] | None = None) -> bool:
        """Queue a shadow decision and return at once; False when the queue is full or closed."""
        if not self._slots.acquire(blocking=False):
            self._count("dropped")
            return False
        try:
            self._pool.submit(self._shadow_one, spec, state, incumbent)
        except RuntimeError:  # the pool was closed
            self._slots.release()
            self._count("dropped")
            return False
        return True

    def close(self, wait: bool = True) -> None:
        self._pool.shutdown(wait=wait, cancel_futures=not wait)

    def _shadow_one(self, spec: str, state: Any, incumbent: dict[str, Any] | None) -> None:
        try:
            self.decide(spec, state, incumbent=incumbent)
        except Exception:  # noqa: BLE001 -- shadow traffic must never reach the caller
            self._count("failed")
        else:
            self._count("sent")
        finally:
            self._slots.release()

    def _count(self, name: str) -> None:
        with self._lock:
            setattr(self, name, getattr(self, name) + 1)
