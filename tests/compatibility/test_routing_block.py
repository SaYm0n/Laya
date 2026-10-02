"""The ``routing`` block of real payloads (weights-gated): the route that actually answered."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from laya import Router
from laya.serve import create_app

pytestmark = pytest.mark.weights

QUESTIONS: dict[str, Any] = {"refund": {"type": "noul", "instructions": "Is a refund requested?"}}
STATES = {
    "english": "Hi, we were billed twice for March and need a refund for the duplicate charge.",
    "multilingual": "Fui cobrado duas vezes em março, quero o reembolso",
}


@pytest.mark.parametrize("expected", sorted(STATES))
def test_routing_block_matches_route(weighted_router: Router, expected: str) -> None:
    state = STATES[expected]
    payload = weighted_router.predict(state, QUESTIONS)
    assert payload["routing"] == dict(weighted_router.route(state, QUESTIONS))
    assert payload["routing"]["model"] == expected
    assert expected in weighted_router.loaded
    assert payload["model"] == "laya-rl-agent"


def test_batch_items_keep_their_own_routing(weighted_router: Router) -> None:
    payloads = weighted_router.predict_batch(
        [
            {"state": STATES["multilingual"], "questions": QUESTIONS},
            {"state": STATES["english"], "questions": QUESTIONS},
        ]
    )
    assert [p["routing"]["model"] for p in payloads] == ["multilingual", "english"]


def test_the_http_app_returns_the_same_routing(weighted_router: Router) -> None:
    with TestClient(create_app(weighted_router)) as client:
        body = client.post(
            "/v1/systemone", json={"state": STATES["multilingual"], "questions": QUESTIONS}
        ).json()
    assert body["routing"] == dict(weighted_router.route(STATES["multilingual"], QUESTIONS))
