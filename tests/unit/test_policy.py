"""DecisionPolicy (Block B, F6): bands, the blocks that stop automation, the canary."""

from __future__ import annotations

from typing import Any

import pytest

from laya_platform.core import DecisionSpec
from laya_platform.core.policy import canary_selected, evaluate

POLICY = {
    "calibration_ref": "eval-0123456789ab",
    "bands": [
        {"min": 0.9, "outcome": "auto"},
        {"min": 0.6, "outcome": "review"},
        {"outcome": "escalate"},
    ],
    "never_auto_if": [{"question": "churn", "equals": True}],
    "canary": 0.5,
}
QUESTIONS = {
    "team": {"type": "choice", "instructions": "Team?", "criteria": ["billing", "tech"]},
    "churn": {"type": "noul", "instructions": "Will they cancel?"},
}


def _spec(**overrides: Any) -> DecisionSpec:
    data = {"id": "t.policy", "version": 1, "languages": ["pt", "en-US"], "questions": QUESTIONS}
    return DecisionSpec.model_validate(data | {"policy": POLICY} | overrides)


def _payload(team: float = 0.95, churn: float = 0.95, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": "fake",
        "answers": {
            "team": {"type": "choice", "choice": "billing", "answer_confidence": team},
            "churn": {"type": "noul", "noul": 0.05, "answer_confidence": churn},
        },
        "usage": {"input_tokens": 10, "output_tokens": 0},
        "routing": {"model": "multilingual", "detection": {"language": "pt"}},
    }
    for key, value in extra.items():
        payload[key] = {**payload.get(key, {}), **value}
    return payload


VALUES = {"team": "billing", "churn": False}


def test_confident_answers_are_automated() -> None:
    verdict = evaluate(_spec(), _payload(), VALUES)
    assert verdict is not None
    assert (verdict.outcome, verdict.bands, verdict.reasons) == (
        "auto",
        {"team": "auto", "churn": "auto"},
        [],
    )


def test_the_most_cautious_band_wins() -> None:
    review = evaluate(_spec(), _payload(team=0.7), VALUES)
    escalate = evaluate(_spec(), _payload(team=0.7, churn=0.3), VALUES)
    assert review is not None
    assert escalate is not None
    assert (review.outcome, review.reasons) == ("review", ["band:team=review"])
    assert escalate.outcome == "escalate"
    assert escalate.reasons == ["band:team=review", "band:churn=escalate"]


def test_no_answer_confidence_falls_to_the_catch_all() -> None:
    payload = _payload()
    del payload["answers"]["team"]["answer_confidence"]
    payload["answers"]["team"]["confidence"] = 0.99  # the entropy number is never used
    verdict = evaluate(_spec(), payload, VALUES)
    assert verdict is not None
    assert verdict.bands["team"] == "escalate"


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (
            {
                "answers": {
                    "team": {
                        "type": "choice",
                        "choice": None,
                        "abstention": "abstained",
                        "answer_confidence": 0.99,
                    }
                }
            },
            "abstained:team",
        ),
        ({"usage": {"truncated": True}}, "truncated"),
        ({"usage": {"options": {"team": {"total": 58, "kept": 42}}}}, "options_collapsed:team"),
        ({"routing": {"detection": {"language": "de"}}}, "language:de"),
    ],
)
def test_what_the_model_could_not_see_blocks_automation(
    change: dict[str, Any], reason: str
) -> None:
    verdict = evaluate(_spec(), _payload(**change), VALUES)
    assert verdict is not None
    assert verdict.outcome == "review"
    assert reason in verdict.reasons


def test_a_language_of_the_spec_does_not_block() -> None:
    for language in ("pt", "en", "EN"):
        verdict = evaluate(_spec(), _payload(routing={"detection": {"language": language}}), VALUES)
        assert verdict is not None, language
        assert verdict.outcome == "auto", language
    undetected = evaluate(_spec(), _payload(routing={"detection": None}), VALUES)
    assert undetected is not None
    assert undetected.outcome == "auto"


def test_high_risk_and_never_auto_if_block_automation() -> None:
    risky = evaluate(_spec(risk="high"), _payload(), VALUES)
    assert risky is not None
    assert (risky.outcome, risky.reasons) == ("review", ["risk:high"])
    flagged = evaluate(_spec(), _payload(), {"team": "billing", "churn": True})
    assert flagged is not None
    assert flagged.reasons == ["never_auto_if:churn"]


def test_never_auto_if_compares_strictly() -> None:
    rule = [{"question": "level", "equals": True}]
    questions = {"level": {"type": "score", "instructions": "?", "criteria": ["a", "b"]}}
    spec = _spec(questions=questions, policy=POLICY | {"never_auto_if": rule})
    payload = {"answers": {"level": {"type": "score", "answer_confidence": 0.99}}}
    verdict = evaluate(spec, payload, {"level": 1})  # 1 == True in Python, not here
    assert verdict is not None
    assert verdict.outcome == "auto"


def test_a_block_never_raises_an_escalation_to_auto_or_lowers_it() -> None:
    verdict = evaluate(_spec(risk="high"), _payload(churn=0.3), VALUES)
    assert verdict is not None
    assert verdict.outcome == "escalate"
    assert verdict.reasons == ["band:churn=escalate", "risk:high"]


def test_no_policy_no_verdict() -> None:
    assert evaluate(_spec(policy=None), _payload(), VALUES) is None


def test_the_canary_is_a_deterministic_share_of_the_inputs() -> None:
    assert canary_selected("0" * 64, 0.01)
    assert not canary_selected("f" * 64, 0.99)
    assert not canary_selected("8" + "0" * 63, 0.5)
    assert canary_selected("7fffffff" + "0" * 56, 0.5)
    assert not canary_selected("0" * 64, 0.0)
    hmacs = [f"{i:08x}" + "0" * 56 for i in range(0, 0x1_0000_0000, 0x0100_0000)]
    assert sum(canary_selected(h, 0.25) for h in hmacs) == len(hmacs) // 4
