"""Reading an upstream answer: its value, its label and its probabilities.

Values follow the upstream's own projection (``laya.structured``): a choice is its label, a noul
is ``P(true) >= 0.5``, a score is its most probable level index. A schema spec projects through
``laya.structured.answers_to_json`` itself. Confidence is always ``answer_confidence``
(``laya.confidence.answer_confidence_value``), never the entropy ``confidence``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from laya.confidence import answer_confidence_value
from laya.structured import answers_to_json

from laya_platform.core.spec import DecisionSpec

__all__ = [
    "answer_confidence_value",
    "answer_value",
    "label_probabilities",
    "predicted_label",
    "spec_values",
]


def label_probabilities(answer: Mapping[str, Any]) -> dict[str, float]:
    """Probability per option label (noul: ``false``/``true``)."""
    if answer.get("type") == "noul":
        p = float(answer.get("noul", 0.0))
        return {"false": 1.0 - p, "true": p}
    probabilities = answer.get("probabilities") or {}
    return {str(label): float(p) for label, p in probabilities.items()}


def predicted_label(answer: Mapping[str, Any]) -> str | None:
    kind = answer.get("type")
    if kind == "choice":
        return None if answer.get("choice") is None else str(answer["choice"])
    if kind == "noul":
        return "true" if float(answer.get("noul", 0.0)) >= 0.5 else "false"
    probabilities = label_probabilities(answer)
    if not probabilities:
        return None
    return max(probabilities, key=probabilities.__getitem__)


def answer_value(answer: Mapping[str, Any]) -> Any:
    """The decided value of one raw answer (choice label, noul bool, score level index)."""
    kind = answer.get("type")
    if kind == "choice":
        return answer.get("choice")
    if kind == "noul":
        return float(answer.get("noul", 0.0)) >= 0.5
    label = predicted_label(answer)
    return None if label is None else int(label)


def spec_values(spec: DecisionSpec, answers: Mapping[str, Any]) -> dict[str, Any]:
    """The decided value of every answer, in the spec's own terms."""
    if spec.json_schema is not None:
        values: dict[str, Any] = answers_to_json(dict(answers), spec.json_schema)
        return values
    return {qid: answer_value(answer) for qid, answer in answers.items()}
