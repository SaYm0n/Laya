"""Metrics beyond ``laya.evals``: per-class P/R/F1, confusion, Brier, NLL, ECE with reliability
bins, coverage x risk, FPR/FNR, abstention and latency percentiles. Pure Python over cases.

Every confidence-based metric uses ``answer_confidence``; a case without one is left out of them.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from laya_platform.evaluation.run import Case

NLL_FLOOR = 1e-12


def _percentile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    rank = max(1, math.ceil(q * len(ordered)))  # nearest rank, as laya.evals
    return ordered[rank - 1]


def _scored(cases: Sequence[Case]) -> list[tuple[Case, float]]:
    """The cases that carry an ``answer_confidence``, with it."""
    return [(case, case.answer_confidence) for case in cases if case.answer_confidence is not None]


def ece(cases: Sequence[Case], bins: int = 10) -> float | None:
    rows = reliability(cases, bins)
    total = sum(row["count"] for row in rows)
    if not total:
        return None
    return sum(
        row["count"] / total * abs(row["accuracy"] - row["mean_answer_confidence"]) for row in rows
    )


def reliability(cases: Sequence[Case], bins: int = 10) -> list[dict[str, float]]:
    """Equal-width bins of ``answer_confidence`` with their mean confidence and accuracy."""
    rows = []
    for index in range(bins):
        low, high = index / bins, (index + 1) / bins
        members = [
            (case, confidence)
            for case, confidence in _scored(cases)
            if low <= confidence
            and (confidence < high or (index == bins - 1 and confidence <= high))
        ]
        if members:
            rows.append(
                {
                    "low": low,
                    "high": high,
                    "count": len(members),
                    "mean_answer_confidence": sum(c for _, c in members) / len(members),
                    "accuracy": sum(case.correct for case, _ in members) / len(members),
                }
            )
    return rows


def coverage_risk(cases: Sequence[Case]) -> list[dict[str, float]]:
    """Answering only at ``answer_confidence >= threshold``: how many answers, how many wrong."""
    scored = sorted(_scored(cases), key=lambda pair: pair[1], reverse=True)
    points: list[dict[str, float]] = []
    wrong = 0
    for index, (case, confidence) in enumerate(scored, 1):
        wrong += not case.correct
        if index == len(scored) or scored[index][1] != confidence:
            points.append(
                {"threshold": confidence, "coverage": index / len(cases), "risk": wrong / index}
            )
    return points


def brier(cases: Sequence[Case]) -> float | None:
    values = [
        sum((p - (label == case.expected_label)) ** 2 for label, p in case.probabilities.items())
        + (0.0 if case.expected_label in case.probabilities else 1.0)
        for case in cases
        if case.probabilities
    ]
    return sum(values) / len(values) if values else None


def nll(cases: Sequence[Case]) -> float | None:
    values = [
        -math.log(max(case.probabilities.get(case.expected_label, 0.0), NLL_FLOOR))
        for case in cases
        if case.probabilities
    ]
    return sum(values) / len(values) if values else None


def per_class(cases: Sequence[Case]) -> dict[str, dict[str, float]]:
    labels = sorted(
        {c.expected_label for c in cases} | {c.predicted_label or "" for c in cases} - {""}
    )
    out = {}
    for label in labels:
        tp = sum(c.predicted_label == label and c.expected_label == label for c in cases)
        fp = sum(c.predicted_label == label and c.expected_label != label for c in cases)
        fn = sum(c.predicted_label != label and c.expected_label == label for c in cases)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        out[label] = {"precision": precision, "recall": recall, "f1": f1, "support": tp + fn}
    return out


def confusion(cases: Sequence[Case]) -> dict[str, Any]:
    """Rows are expected labels, columns predicted labels (``None`` when no answer)."""
    labels = sorted({c.expected_label for c in cases} | {str(c.predicted_label) for c in cases})
    index = {label: i for i, label in enumerate(labels)}
    matrix = [[0] * len(labels) for _ in labels]
    for case in cases:
        matrix[index[case.expected_label]][index[str(case.predicted_label)]] += 1
    return {"labels": labels, "matrix": matrix}


def binary_rates(cases: Sequence[Case]) -> dict[str, float]:
    """FPR and FNR of noul answers at the upstream's decision threshold (P(true) >= 0.5)."""
    negatives = [c for c in cases if c.expected_label == "false"]
    positives = [c for c in cases if c.expected_label == "true"]
    rates = {}
    if negatives:
        rates["fpr"] = sum(c.predicted_label == "true" for c in negatives) / len(negatives)
    if positives:
        rates["fnr"] = sum(c.predicted_label == "false" for c in positives) / len(positives)
    return rates


def summarize(cases: Sequence[Case]) -> dict[str, float]:
    """Headline metrics of a set of cases (keys are absent when they cannot be computed)."""
    if not cases:
        return {"n": 0}
    out: dict[str, float] = {
        "n": len(cases),
        "accuracy": sum(c.correct for c in cases) / len(cases),
    }
    for name, value in (("ece", ece(cases)), ("brier", brier(cases)), ("nll", nll(cases))):
        if value is not None:
            out[name] = value
    scored = _scored(cases)
    out["confidence_coverage"] = len(scored) / len(cases)
    if scored:
        out["mean_answer_confidence"] = sum(confidence for _, confidence in scored) / len(scored)
    gated = [c for c in cases if c.abstention is not None]
    if gated:
        out["abstention_rate"] = sum(c.abstention == "abstained" for c in gated) / len(gated)
    errors = [
        abs(c.score - c.expected_level)
        for c in cases
        if c.kind == "score" and c.score is not None and c.expected_level is not None
    ]
    if errors:
        out["score_mae"] = sum(errors) / len(errors)
    latencies = [c.latency_ms for c in cases]
    for q in (0.5, 0.95, 0.99):
        out[f"latency_p{round(q * 100)}_ms"] = _percentile(latencies, q)
    return out
