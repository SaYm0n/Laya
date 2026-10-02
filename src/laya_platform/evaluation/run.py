"""Run a DecisionEngine over a labelled dataset of one DecisionSpec and keep one case per answer.

Dataset: JSONL, one example per line -- ``{"state": ..., "expected": {question: value},
"language": "pt", "tags": ["short"]}``. Questions come from the spec, so rows do not repeat them;
``expected`` is in the spec's terms (schema values, or label / level index / bool for raw
questions).

``laya.evals.evaluate`` is not reused on purpose: its ECE imports torch, and its confidence falls
back to the entropy ``confidence`` when ``answer_confidence`` is missing -- the platform never
does that (docs/COMPATIBILITY_MATRIX.md §11.1).
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from laya_platform.core.answers import (
    answer_confidence_value,
    label_probabilities,
    predicted_label,
)
from laya_platform.core.engine import DecisionEngine
from laya_platform.core.spec import DecisionSpec
from laya_platform.core.types import DecisionRequest


class DatasetError(ValueError):
    """A dataset row is malformed; the message names the line."""


@dataclass(frozen=True)
class Example:
    state: Any
    expected: dict[str, Any]
    language: str | None = None
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Case:
    """One answer of one example, scored against its expected value."""

    qid: str
    kind: str
    expected_label: str
    predicted_label: str | None
    correct: bool
    answer_confidence: float | None
    probabilities: dict[str, float]
    abstention: str | None = None
    expected_level: int | None = None
    score: float | None = None
    language: str | None = None
    tags: tuple[str, ...] = ()
    model: str | None = None
    latency_ms: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


def load_examples(path: str | os.PathLike[str], spec: DecisionSpec) -> list[Example]:
    questions = spec.to_questions()
    examples = []
    lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
    for number, line in enumerate(lines, 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        where = f"{path}:{number}"
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise DatasetError(f"{where}: not JSON: {exc}") from None
        if (
            not isinstance(row, dict)
            or "state" not in row
            or not isinstance(row.get("expected"), dict)
        ):
            raise DatasetError(f"{where}: needs 'state' and an 'expected' object")
        unknown = sorted(set(row["expected"]) - set(questions))
        if unknown:
            raise DatasetError(f"{where}: 'expected' names unknown question(s) {unknown}")
        tags = row.get("tags") or []
        if not isinstance(tags, list):
            raise DatasetError(f"{where}: 'tags' must be a list")
        examples.append(
            Example(row["state"], row["expected"], row.get("language"), tuple(map(str, tags)))
        )
    if not examples:
        raise DatasetError(f"{path}: no examples")
    return examples


def expected_label(spec: DecisionSpec, question: dict[str, Any], expected: Any) -> str:
    """``expected`` written as the label the answer reports (and as the level index for score)."""
    kind = question.get("type")
    if kind == "noul":
        if not isinstance(expected, bool):
            raise DatasetError(f"a noul expects true or false, got {expected!r}")
        return "true" if expected else "false"
    if kind == "score":
        if spec.json_schema is not None:
            levels = [str(level) for level in question.get("criteria", [])]
            if str(expected) not in levels:
                raise DatasetError(f"{expected!r} is not one of the score levels {levels}")
            return str(levels.index(str(expected)))
        return str(int(expected))
    return "null" if expected is None else str(expected)


def evaluate(
    engine: DecisionEngine,
    spec: DecisionSpec,
    examples: Sequence[Example],
    *,
    batch_size: int = 16,
    routed: bool = True,
) -> list[Case]:
    """``routed=False`` for a single checkpoint (a specialist): no Router control is sent."""
    questions = spec.to_questions()
    controls = spec.predict_controls() if routed else {}
    cases: list[Case] = []
    for start in range(0, len(examples), batch_size):
        chunk = examples[start : start + batch_size]
        requests: list[DecisionRequest] = []
        for example in chunk:
            request: DecisionRequest = {"state": example.state, "questions": questions}
            if "model" in controls:  # the only control a spec implies (predict_controls)
                request["model"] = controls["model"]
            requests.append(request)
        started = time.perf_counter()
        payloads = engine.predict_batch(requests)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        for example, payload in zip(chunk, payloads, strict=True):
            model = payload.get("model")
            for qid, expected in example.expected.items():
                answer: dict[str, Any] = dict(payload["answers"][qid])
                label = expected_label(spec, questions[qid], expected)
                predicted = predicted_label(answer)
                cases.append(
                    Case(
                        qid=qid,
                        kind=str(answer.get("type")),
                        expected_label=label,
                        predicted_label=predicted,
                        correct=predicted == label,
                        answer_confidence=answer_confidence_value(answer),
                        probabilities=label_probabilities(answer),
                        abstention=answer.get("abstention"),
                        expected_level=int(label) if answer.get("type") == "score" else None,
                        score=answer.get("score"),
                        language=example.language,
                        tags=example.tags,
                        model=model,
                        latency_ms=elapsed_ms,
                    )
                )
    return cases
