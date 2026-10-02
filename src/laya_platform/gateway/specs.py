"""The DecisionSpecs a gateway serves, loaded from one directory at start-up."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from laya.serve import MAX_CHOICE_OPTIONS, MAX_QUESTIONS, MAX_SCORE_LEVELS, MAX_TOTAL_OPTIONS

from laya_platform.core.spec import SUPPORTED_SUFFIXES, DecisionSpec, load_decision_spec


def wire_limit_problems(questions: Mapping[Any, Any]) -> list[str]:
    """Why ``questions`` would be refused by ``/v1/systemone`` (laya.serve's limits), if at all.

    These are transport limits, not DecisionSpec rules: they matter only when the engine is a
    remote endpoint.
    """
    problems = []
    if len(questions) > MAX_QUESTIONS:
        problems.append(f"{len(questions)} questions > {MAX_QUESTIONS}")
    total = 0
    for qid, question in questions.items():  # counted as laya.serve counts them
        if not isinstance(question, dict):
            continue
        kind, criteria = question.get("type"), question.get("criteria")
        if kind == "choice" and isinstance(criteria, dict | list):
            total += len(criteria)
            if len(criteria) > MAX_CHOICE_OPTIONS:
                problems.append(f"{qid!r}: {len(criteria)} choice options > {MAX_CHOICE_OPTIONS}")
        elif kind == "score" and isinstance(criteria, list):
            total += len(criteria)
            if len(criteria) > MAX_SCORE_LEVELS:
                problems.append(f"{qid!r}: {len(criteria)} score levels > {MAX_SCORE_LEVELS}")
    if total > MAX_TOTAL_OPTIONS:
        problems.append(f"{total} options in total > {MAX_TOTAL_OPTIONS}")
    return problems


class SpecStore:
    def __init__(self, specs: Mapping[str, DecisionSpec]) -> None:
        self._specs = dict(specs)

    @classmethod
    def load(cls, directory: Path, *, over_http: bool = False) -> SpecStore:
        """Every spec file in ``directory``; one version per id.

        With ``over_http`` (a remote engine) a spec that ``/v1/systemone`` would refuse is
        refused here, at start-up, instead of failing on every request.
        """
        if not directory.is_dir():
            raise ValueError(f"specs directory {directory} does not exist")
        specs: dict[str, DecisionSpec] = {}
        for path in sorted(directory.iterdir()):
            if path.suffix.lower() not in SUPPORTED_SUFFIXES:
                continue
            spec = load_decision_spec(path)
            if spec.id in specs:
                raise ValueError(f"{path}: DecisionSpec {spec.id!r} is defined twice")
            problems = wire_limit_problems(spec.to_questions()) if over_http else []
            if problems:
                raise ValueError(f"{path}: does not fit /v1/systemone: {'; '.join(problems)}")
            specs[spec.id] = spec
        return cls(specs)

    def get(self, spec_id: str) -> DecisionSpec | None:
        return self._specs.get(spec_id)

    def __iter__(self) -> Iterator[DecisionSpec]:
        return iter(self._specs.values())

    def __len__(self) -> int:
        return len(self._specs)
