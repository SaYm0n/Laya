"""Choose confidence bands by the cost of errors, from an evaluation report.

For every candidate threshold ``t`` on ``answer_confidence``, answers at or above ``t`` would be
automated and the rest sent to review; the expected cost per answer is

    (wrong automated answers x error_cost + reviewed answers x review_cost) / answers

and the cheapest threshold wins (ties go to the higher, safer one). An answer without
``answer_confidence`` always goes to review. Nothing here acts: the bands are written into the
DecisionSpec policy, with the report id as ``calibration_ref``, and reported by the gateway.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BandChoice:
    threshold: float | None  # None: never automate
    coverage: float
    auto_error_rate: float
    expected_cost: float
    n: int


def choose_threshold(
    cases: Sequence[Mapping[str, Any]], *, error_cost: float, review_cost: float
) -> BandChoice:
    """``cases`` are report cases (``answer_confidence`` and ``correct``)."""
    if error_cost <= 0 or review_cost <= 0:
        raise ValueError("error_cost and review_cost must be positive")
    if not cases:
        raise ValueError("no cases to choose a threshold from")
    n = len(cases)
    candidates: list[float | None] = sorted(
        {c["answer_confidence"] for c in cases if c.get("answer_confidence") is not None},
        reverse=True,
    )
    choices = []
    for threshold in [None, *candidates]:
        automated = [
            c
            for c in cases
            if threshold is not None
            and c.get("answer_confidence") is not None
            and c["answer_confidence"] >= threshold
        ]
        wrong = sum(not c["correct"] for c in automated)
        choices.append(
            BandChoice(
                threshold=threshold,
                coverage=len(automated) / n,
                auto_error_rate=wrong / len(automated) if automated else 0.0,
                expected_cost=(wrong * error_cost + (n - len(automated)) * review_cost) / n,
                n=n,
            )
        )
    # Cheapest first; on a tie the safer choice (never automating, then the higher threshold).
    return min(
        choices,
        key=lambda c: (round(c.expected_cost, 12), -(2.0 if c.threshold is None else c.threshold)),
    )


def policy_for(report: Mapping[str, Any], choice: BandChoice) -> dict[str, Any]:
    """The ``policy`` block of a DecisionSpec for this choice."""
    bands: list[dict[str, Any]] = []
    if choice.threshold is not None:
        bands.append({"min": round(choice.threshold, 4), "outcome": "auto"})
    bands.append({"outcome": "review"})
    return {"calibration_ref": report["id"], "bands": bands}
