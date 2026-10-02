"""The upstream confidence gate (``min_confidence``, laya 0.3.23): states, threshold, abstention.

The gate is the upstream's (``laya.confidence``) and every engine reuses it, the FakeEngine
included. With no ``min_confidence`` the payload is untouched; with one, every answer reports
``abstention`` (passed/abstained/unevaluated) and the threshold it was compared against.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Callable
from typing import Any

import laya
import pytest
from laya import GATE_STATES, Router, apply_confidence_gate, check_min_confidence

from laya_platform.core.adapters import FakeAnswer, FakeEngine, UpstreamRouterEngine


def _payload(**answers: dict[str, Any]) -> dict[str, Any]:
    return {
        "model": "laya-rl-agent",
        "answers": answers,
        "usage": {"input_tokens": 1, "output_tokens": 0},
    }


CONFIDENT = {"type": "choice", "choice": "a", "confidence": 0.2, "answer_confidence": 0.95}
UNSURE = {"type": "choice", "choice": "b", "confidence": 0.9, "answer_confidence": 0.5}
NO_NUMBER = {"type": "choice", "choice": "c"}
QUESTIONS: dict[str, Any] = {
    "team": {"type": "choice", "instructions": "Team?", "criteria": ["a", "b", "c", "d"]},
    "urgent": {"type": "noul", "instructions": "Urgent?"},
}


def test_gate_states() -> None:
    assert GATE_STATES == ("passed", "abstained", "unevaluated")


@pytest.mark.parametrize("value", [0, 0.0, 0.5, 1, 1.0])
def test_valid_thresholds(value: float) -> None:
    assert check_min_confidence(value) == float(value)


@pytest.mark.parametrize("value", [True, False, -0.01, 1.01, math.nan, math.inf, "0.5", None])
def test_invalid_thresholds(value: Any) -> None:
    with pytest.raises(ValueError, match=r"min_confidence must be a float in \[0.0, 1.0\]"):
        check_min_confidence(value)


def test_without_a_threshold_the_payload_is_untouched() -> None:
    payload = _payload(a=dict(CONFIDENT), b=dict(UNSURE), c=dict(NO_NUMBER))
    before = copy.deepcopy(payload)
    apply_confidence_gate([payload], None)
    assert payload == before


def test_each_answer_reports_its_state_and_the_threshold() -> None:
    payload = _payload(a=dict(CONFIDENT), b=dict(UNSURE), c=dict(NO_NUMBER))
    apply_confidence_gate([payload], 0.9)
    answers = payload["answers"]
    assert {name: answers[name]["abstention"] for name in answers} == {
        "a": "passed",
        "b": "abstained",
        "c": "unevaluated",
    }
    assert {answer["abstention_threshold"] for answer in answers.values()} == {0.9}
    assert answers["b"]["low_confidence"] is True
    assert "low_confidence" not in answers["a"]
    assert "low_confidence" not in answers["c"]  # nothing to compare is not a low confidence
    # The raw answer stays for inspection.
    assert (answers["b"]["choice"], answers["b"]["answer_confidence"]) == ("b", 0.5)


def test_a_zero_threshold_still_reports() -> None:
    payload = _payload(b=dict(UNSURE))
    apply_confidence_gate([payload], 0.0)
    assert payload["answers"]["b"]["abstention"] == "passed"
    assert payload["answers"]["b"]["abstention_threshold"] == 0.0


def test_the_router_gates_after_inference(
    router_factory: Callable[..., Router], make_agent: Any
) -> None:
    agent = make_agent(answers={"team": FakeAnswer("a", 0.6)})
    router = router_factory(agent)
    gated = router.predict(
        "Hello there, this is plain English text.", QUESTIONS, min_confidence=0.7
    )
    assert gated["answers"]["team"]["abstention"] == "abstained"
    assert gated["answers"]["urgent"]["abstention"] == "passed"
    assert "min_confidence" not in agent.calls[-1][1]  # the Router gates, not the agent
    plain = router.predict("Hello there, this is plain English text.", QUESTIONS)
    assert "abstention" not in plain["answers"]["team"]

    batch = router.predict_batch(
        [
            {"state": "Hello", "questions": QUESTIONS},
            {"state": "Olá, tudo bem?", "questions": QUESTIONS},
        ],
        min_confidence=0.7,
    )
    assert [p["answers"]["team"]["abstention"] for p in batch] == ["abstained", "abstained"]


def test_the_router_refuses_an_invalid_threshold(router_factory: Callable[..., Router]) -> None:
    engine = UpstreamRouterEngine(router_factory())
    with pytest.raises(ValueError, match="min_confidence"):
        engine.predict("Hello", QUESTIONS, min_confidence=1.5)


def test_the_fake_engine_uses_the_upstream_gate() -> None:
    engine = FakeEngine({"team": FakeAnswer("b", 0.6)})
    gated = engine.predict("state", QUESTIONS, min_confidence=0.7)
    ungated = engine.predict("state", QUESTIONS)
    apply_confidence_gate([ungated], 0.7)
    assert gated == ungated
    with pytest.raises(ValueError, match="min_confidence"):
        engine.predict("state", QUESTIONS, min_confidence=True)


def test_decide_projects_an_abstained_answer_to_none() -> None:
    engine = FakeEngine({"team": FakeAnswer("b", 0.6), "urgent": FakeAnswer(True, 0.95)})
    schema = {
        "type": "object",
        "properties": {
            "team": {"enum": ["a", "b", "c", "d"]},
            "urgent": {"type": "boolean"},
        },
    }
    assert laya.decide(engine, "state", schema) == {"team": "b", "urgent": True}
    assert laya.decide(engine, "state", schema, min_confidence=0.7) == {
        "team": None,
        "urgent": True,
    }
