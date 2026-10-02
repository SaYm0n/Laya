"""Shape of real answers from the published checkpoints (weights-gated).

Run with ``--run-weights`` on a machine that can reach huggingface.co (downloads ~1.6 GB per
checkpoint on first use). Checks structure and internal consistency, not accuracy.
"""

from __future__ import annotations

import math
from typing import Any

import pytest
from laya import Router

pytestmark = pytest.mark.weights

STATE = "Hi, we were billed twice for March and need a refund for the duplicate charge."
QUESTIONS: dict[str, Any] = {
    "team": {
        "type": "choice",
        "instructions": "Which team should handle this?",
        "criteria": {
            "billing": "payments and invoices",
            "technical": "bugs and access",
            "sales": None,
        },
    },
    "urgency": {
        "type": "score",
        "instructions": "How urgent is this?",
        "criteria": ["not urgent", "low", "medium", "high", "critical"],
    },
    "refund": {"type": "noul", "instructions": "Is the customer asking for a refund?"},
    "named": {
        "type": "noul",
        "instructions": "Is the customer angry?",
        "labels": {"false": "calm", "true": "angry"},
    },
}
# Upstream rounds every probability to 4 decimals, so a sum may be off by k * 0.00005.
ROUNDING = 1e-3


@pytest.fixture(scope="module", params=["english", "multilingual"])
def payload(request: pytest.FixtureRequest, weighted_router: Router) -> dict[str, Any]:
    result: dict[str, Any] = weighted_router.predict(STATE, QUESTIONS, model=request.param)
    return result


def test_payload_keys(payload: dict[str, Any]) -> None:
    assert set(payload) == {"model", "answers", "usage", "routing"}
    assert payload["model"] == "laya-rl-agent"
    assert set(payload["answers"]) == set(QUESTIONS)


def test_choice(payload: dict[str, Any]) -> None:
    answer = payload["answers"]["team"]
    assert answer["type"] == "choice"
    assert list(answer["probabilities"]) == ["billing", "technical", "sales"]
    assert math.isclose(sum(answer["probabilities"].values()), 1.0, abs_tol=ROUNDING)
    assert answer["choice"] == max(answer["probabilities"], key=answer["probabilities"].get)
    assert answer["answer_confidence"] == pytest.approx(
        max(answer["probabilities"].values()), abs=1e-4
    )
    assert 0.0 <= answer["confidence"] <= 1.0
    assert 0.0 <= answer["action"]["act_probability"] <= 1.0


def test_score(payload: dict[str, Any]) -> None:
    answer = payload["answers"]["urgency"]
    levels = QUESTIONS["urgency"]["criteria"]
    assert answer["type"] == "score"
    assert answer["legend"] == {str(i): level for i, level in enumerate(levels)}
    probabilities = [answer["probabilities"][str(i)] for i in range(len(levels))]
    assert math.isclose(sum(probabilities), 1.0, abs_tol=ROUNDING)
    assert 0.0 <= answer["score"] <= len(levels) - 1
    expected = sum(i * p for i, p in enumerate(probabilities))
    assert answer["score"] == pytest.approx(expected, abs=len(levels) * ROUNDING)
    assert answer["answer_confidence"] == pytest.approx(max(probabilities), abs=1e-4)


@pytest.mark.parametrize("qid", ["refund", "named"])
def test_noul(payload: dict[str, Any], qid: str) -> None:
    answer = payload["answers"][qid]
    assert answer["type"] == "noul"
    assert "probabilities" not in answer
    assert 0.0 <= answer["noul"] <= 1.0
    strongest = max(answer["noul"], 1 - answer["noul"])
    assert answer["confidence"] == pytest.approx(strongest, abs=1e-4)
    assert answer["answer_confidence"] == pytest.approx(strongest, abs=1e-4)


def test_option_order_does_not_change_the_answer_keys(weighted_router: Router) -> None:
    reordered = {"team": {**QUESTIONS["team"], "option_order": [2, 0, 1]}}
    answer = weighted_router.predict(STATE, reordered, model="english")["answers"]["team"]
    assert list(answer["probabilities"]) == ["billing", "technical", "sales"]
