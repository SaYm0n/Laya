"""PlatformClient: decide waits and raises; shadow never blocks and never raises."""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from typing import Any

import pytest

from laya_platform.client import DECIDE_PATH, PlatformClient
from laya_platform.core.adapters import HttpResponse
from laya_platform.core.errors import RemoteEngineError

DECISION = {"trace_id": "t", "mode": "shadow", "act": False, "suggestion": None}


class Transport:
    def __init__(self, response: HttpResponse | None = None, gate: threading.Event | None = None):
        self.response = response or HttpResponse(200, json.dumps(DECISION).encode())
        self.gate = gate
        self.requests: list[tuple[str, dict[str, Any], Mapping[str, str], float]] = []

    def post(
        self, url: str, body: bytes, headers: Mapping[str, str], timeout: float
    ) -> HttpResponse:
        if self.gate is not None:
            self.gate.wait(5)
        self.requests.append((url, json.loads(body), headers, timeout))
        if self.response.status == 0:
            raise OSError("connection refused")
        return self.response


def test_decide_posts_the_spec_and_state_with_the_key() -> None:
    transport = Transport()
    client = PlatformClient("http://gw/", api_key="secret", transport=transport, timeout=1.5)
    assert client.decide("examples.triage", "texto", incumbent={"q": 1}) == DECISION
    ((url, body, headers, timeout),) = transport.requests
    assert url == "http://gw" + DECIDE_PATH
    assert body == {"spec": "examples.triage", "state": "texto", "incumbent": {"q": 1}}
    assert headers["Authorization"] == "Bearer secret"
    assert timeout == 1.5
    assert "secret" not in repr(client)
    client.close()


def test_decide_raises_with_the_gateway_detail() -> None:
    transport = Transport(HttpResponse(409, b'{"detail": "examples.triage is offline"}'))
    client = PlatformClient("http://gw", transport=transport)
    with pytest.raises(RemoteEngineError) as exc:
        client.decide("examples.triage", "x")
    assert exc.value.status == 409
    assert exc.value.detail == "examples.triage is offline"
    client.close()


@pytest.mark.parametrize("status", [0, 500, 401])
def test_shadow_counts_failures_and_never_raises(status: int) -> None:
    client = PlatformClient("http://gw", transport=Transport(HttpResponse(status, b"oops")))
    assert client.shadow("examples.triage", "x") is True
    client.close(wait=True)
    assert (client.sent, client.failed, client.dropped) == (0, 1, 0)


def test_shadow_returns_at_once_and_drops_when_the_queue_is_full() -> None:
    gate = threading.Event()
    transport = Transport(gate=gate)
    client = PlatformClient("http://gw", transport=transport, max_pending=2, workers=1)
    assert client.shadow("s", "a") is True
    assert client.shadow("s", "b") is True
    assert client.shadow("s", "c") is False  # the gateway is stuck; the caller is not
    gate.set()
    client.close(wait=True)
    assert (client.sent, client.failed, client.dropped) == (2, 0, 1)


def test_shadow_after_close_is_dropped() -> None:
    client = PlatformClient("http://gw", transport=Transport())
    client.close()
    assert client.shadow("s", "x") is False
    assert client.dropped == 1
