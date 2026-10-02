"""FakeEngine: a deterministic DecisionEngine with no weights, no torch and no network.

For tests of the layers built on top of the core. It does not read the state and holds no domain
rules: every answer is scripted per question id, or else the first option wins with
``default_probability``. Payloads have the upstream shape (``model``, ``answers``, ``usage``,
``routing``), ``confidence`` and ``answer_confidence`` follow the upstream definitions, and
``min_confidence`` goes through the upstream gate (``laya.apply_confidence_gate``), so abstention
looks exactly as it does in production.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Unpack

from laya import apply_confidence_gate, check_min_confidence

from laya_platform.core.types import (
    AnswerPayload,
    BatchControls,
    DecisionPayload,
    DecisionRequest,
    PredictControls,
    Questions,
    RouteHints,
    RoutePayload,
    State,
)

FAKE_MODEL = "fake-engine"
FAKE_ROUTE: RoutePayload = {
    "model": "fake",
    "repo": None,
    "reason": "FakeEngine: fixed route",
    "detection": None,
    "workflow": None,
}


@dataclass(frozen=True)
class FakeAnswer:
    """A scripted answer: the winning option and the probability mass on it.

    ``option`` is a label for ``choice``, a level index for ``score`` and a bool for ``noul``.
    The other options share the rest of the mass evenly, so ``probability`` must exceed ``1/k``.
    """

    option: str | int | bool
    probability: float = 1.0


@dataclass(frozen=True)
class FakeCall:
    """One recorded call: the method, its arguments and the controls it received."""

    method: str
    arguments: tuple[Any, ...]
    controls: Mapping[str, Any] = field(default_factory=dict)


class FakeEngine:
    def __init__(
        self,
        answers: Mapping[str, FakeAnswer] | None = None,
        *,
        default_probability: float = 1.0,
        route: RoutePayload | None = None,
        error: Exception | None = None,
    ) -> None:
        if not 0.0 < default_probability <= 1.0:
            raise ValueError(f"default_probability must be in (0, 1], got {default_probability}")
        self._answers = dict(answers or {})
        self._default_probability = default_probability
        self._route: RoutePayload = (route or FAKE_ROUTE).copy()
        self._error = error
        self.calls: list[FakeCall] = []

    def predict(
        self, state: State, questions: Questions, **controls: Unpack[PredictControls]
    ) -> DecisionPayload:
        self.calls.append(FakeCall("predict", (state, questions), dict(controls)))
        return self._payload(questions, controls.get("min_confidence"))

    def predict_batch(
        self, requests: Sequence[DecisionRequest], **controls: Unpack[BatchControls]
    ) -> list[DecisionPayload]:
        self.calls.append(FakeCall("predict_batch", (list(requests),), dict(controls)))
        threshold = controls.get("min_confidence")
        return [self._payload(request["questions"], threshold) for request in requests]

    def route(
        self, state: State, questions: Questions | None = None, **hints: Unpack[RouteHints]
    ) -> RoutePayload:
        self.calls.append(FakeCall("route", (state, questions), dict(hints)))
        if self._error is not None:
            raise self._error
        return self._route.copy()

    def _payload(self, questions: Questions, min_confidence: float | None) -> DecisionPayload:
        if self._error is not None:
            raise self._error
        threshold = None if min_confidence is None else check_min_confidence(min_confidence)
        answers = {qid: self._answer(qid, question) for qid, question in questions.items()}
        payload: DecisionPayload = {
            "model": FAKE_MODEL,
            "answers": answers,
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "state_tokens": 0,
                "state_tokens_dropped": 0,
                "truncated": False,
                "truncated_questions": [],
            },
            "routing": self._route.copy(),
        }
        apply_confidence_gate([payload], threshold)
        return payload

    def _answer(self, qid: str, question: Mapping[str, Any]) -> AnswerPayload:
        kind = question.get("type")
        scripted = self._answers.get(qid)
        probability = self._default_probability if scripted is None else scripted.probability
        if kind == "noul":
            if scripted is not None and not isinstance(scripted.option, bool):
                raise ValueError(f"question {qid!r}: a noul answer is True or False")
            winner = 1 if scripted is not None and scripted.option is True else 0
            p = _distribution(qid, 2, winner, probability)
            return {
                "type": "noul",
                "noul": round(p[1], 4),
                "confidence": round(max(p), 4),
                "answer_confidence": round(max(p), 4),
                "action": {"act_probability": 0.0},
            }
        criteria = question.get("criteria")
        if kind == "choice" and isinstance(criteria, dict | list) and criteria:
            labels = list(criteria)
            winner = 0 if scripted is None else _index_of(qid, labels, scripted.option)
            p = _distribution(qid, len(labels), winner, probability)
            return {
                "type": "choice",
                "choice": labels[winner],
                "probabilities": {
                    str(label): round(v, 4) for label, v in zip(labels, p, strict=True)
                },
                "confidence": round(_entropy_confidence(p), 4),
                "answer_confidence": round(max(p), 4),
                "action": {"act_probability": 0.0},
            }
        if kind == "score" and isinstance(criteria, list) and criteria:
            winner = 0 if scripted is None else _level_of(qid, len(criteria), scripted.option)
            p = _distribution(qid, len(criteria), winner, probability)
            return {
                "type": "score",
                "score": round(sum(i * v for i, v in enumerate(p)), 4),
                "legend": {str(i): _render(level) for i, level in enumerate(criteria)},
                "probabilities": {str(i): round(v, 4) for i, v in enumerate(p)},
                "confidence": round(_entropy_confidence(p), 4),
                "answer_confidence": round(max(p), 4),
                "action": {"act_probability": 0.0},
            }
        raise ValueError(
            f"question {qid!r}: FakeEngine answers choice/score questions with non-empty criteria "
            f"and noul questions, got type {kind!r}"
        )


def _distribution(qid: str, count: int, winner: int, probability: float) -> list[float]:
    if count == 1:
        if probability != 1.0:
            raise ValueError(f"question {qid!r}: a single option always has probability 1.0")
        return [1.0]
    if not 1.0 / count < probability <= 1.0:
        raise ValueError(
            f"question {qid!r}: probability {probability} must be in (1/{count}, 1] for the "
            "scripted option to be the answer"
        )
    rest = (1.0 - probability) / (count - 1)
    return [probability if index == winner else rest for index in range(count)]


def _entropy_confidence(p: list[float]) -> float:
    """``1 - H(p)/log(k)``: the upstream ``confidence`` of choice/score (not calibrated)."""
    entropy = -sum(v * math.log(max(v, 1e-12)) for v in p)
    return min(1.0, max(0.0, 1.0 - entropy / math.log(len(p))))


def _index_of(qid: str, labels: list[Any], option: str | int | bool) -> int:
    if option not in labels:
        raise ValueError(f"question {qid!r}: {option!r} is not one of {labels}")
    return labels.index(option)


def _level_of(qid: str, count: int, option: str | int | bool) -> int:
    if isinstance(option, bool) or not isinstance(option, int) or not 0 <= option < count:
        raise ValueError(f"question {qid!r}: a score answer is a level index in range({count})")
    return option


def _render(level: Any) -> str:
    return level if isinstance(level, str) else json.dumps(level, ensure_ascii=False)
