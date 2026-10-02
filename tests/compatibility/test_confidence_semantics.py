"""``confidence`` is not ``answer_confidence`` (laya 0.3.23), and the platform keeps them apart.

* ``answer_confidence`` = max(p): what temperature scaling fits, what ECE measures, what a gate
  must compare against.
* ``confidence`` = 1 - H(p)/log(k) for choice/score (normalized entropy, not calibrated, drifts with
  the number of options); max(p, 1-p) for noul, where the two coincide.
* Jev's ``(n·p_max - 1)/(n - 1)`` is a third quantity: its thresholds do not transfer.
"""

from __future__ import annotations

import ast
import math
from pathlib import Path
from typing import Any

import laya
import pytest
from laya.confidence import answer_confidence_value, apply_confidence_gate

from laya_platform.core.adapters import FakeAnswer, FakeEngine

SRC = Path(__file__).resolve().parents[2] / "src" / "laya_platform"
QUESTIONS: dict[str, Any] = {
    "team": {"type": "choice", "instructions": "Team?", "criteria": ["a", "b", "c", "d"]},
    "level": {"type": "score", "instructions": "Level?", "criteria": ["low", "mid", "high"]},
    "urgent": {"type": "noul", "instructions": "Urgent?"},
}


def _entropy_confidence(p: list[float]) -> float:
    return 1 - (-sum(v * math.log(v) for v in p if v > 0)) / math.log(len(p))


def test_the_two_numbers_differ_for_choice_and_score() -> None:
    answers = FakeEngine(
        {"team": FakeAnswer("a", 0.4), "level": FakeAnswer(2, 0.5), "urgent": FakeAnswer(True, 0.8)}
    ).predict("state", QUESTIONS)["answers"]
    team, level, urgent = answers["team"], answers["level"], answers["urgent"]
    assert team["answer_confidence"] == 0.4
    assert team["confidence"] == round(_entropy_confidence([0.4, 0.2, 0.2, 0.2]), 4) == 0.039
    assert level["answer_confidence"] == 0.5
    assert level["confidence"] == round(_entropy_confidence([0.25, 0.25, 0.5]), 4)
    # noul: both are max(p, 1 - p)
    assert urgent["confidence"] == urgent["answer_confidence"] == 0.8
    jev = (4 * 0.4 - 1) / (4 - 1)
    assert len({team["answer_confidence"], team["confidence"], round(jev, 4)}) == 3


def test_answer_confidence_value_never_falls_back_to_confidence() -> None:
    assert answer_confidence_value({"answer_confidence": 0.7, "confidence": 0.1}) == 0.7
    assert answer_confidence_value({"confidence": 0.95}) is None
    assert answer_confidence_value({"answer_confidence": True}) is None
    assert answer_confidence_value({"answer_confidence": math.nan}) is None


def test_the_upstream_gate_falls_back_to_confidence_when_answer_confidence_is_missing() -> None:
    # Frozen because it matters for F6: an answer without answer_confidence (an old server, a
    # hand-built payload) is gated on the uncalibrated entropy number by the upstream. The
    # platform's own policy must read answer_confidence_value() and treat None as unevaluated.
    payload: dict[str, Any] = {
        "answers": {"high": {"confidence": 0.95}, "low": {"confidence": 0.5}, "none": {}}
    }
    apply_confidence_gate([payload], 0.9)
    states = {name: answer["abstention"] for name, answer in payload["answers"].items()}
    assert states == {"high": "passed", "low": "abstained", "none": "unevaluated"}


def test_decide_reports_both_numbers_under_their_own_names() -> None:
    details = laya.decide(
        FakeEngine({"team": FakeAnswer("a", 0.4)}),
        "state",
        questions={"team": QUESTIONS["team"]},
        return_details=True,
    )
    assert details.answer_confidence == {"team": 0.4}
    assert details.confidence == {"team": 0.039}


def test_platform_code_never_reads_the_entropy_confidence() -> None:
    # Reading answer["confidence"] (or .get("confidence")) anywhere in src/ would be the first step
    # towards gating on it. Writing it (the FakeEngine builds upstream-shaped payloads) is fine.
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            key = None
            if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
                key = node.slice
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and node.args
            ):
                key = node.args[0]
            if isinstance(key, ast.Constant) and key.value == "confidence":
                offenders.append(f"{path.relative_to(SRC)}:{getattr(node, 'lineno', '?')}")
    assert not offenders


@pytest.mark.torch
@pytest.mark.parametrize(
    "p", [[0.4, 0.2, 0.2, 0.2], [0.25, 0.25, 0.5], [0.9, 0.1], [1.0, 0.0, 0.0], [0.34, 0.33, 0.33]]
)
def test_fake_engine_numbers_follow_the_upstream_definitions(p: list[float]) -> None:
    import numpy as np

    k = len(p)
    winner = int(np.argmax(p))
    answer = FakeEngine({"q": FakeAnswer(f"o{winner}", p[winner])}).predict(
        "s", {"q": {"type": "choice", "instructions": "x", "criteria": [f"o{i}" for i in range(k)]}}
    )["answers"]["q"]
    probabilities = np.array(list(answer["probabilities"].values()))
    assert answer["answer_confidence"] == round(laya.answer_confidence(probabilities, k), 4)
    assert answer["confidence"] == pytest.approx(
        laya.confidence_from_probs(probabilities, k), abs=2e-4
    )
