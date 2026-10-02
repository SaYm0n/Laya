"""Specialist lifecycle: the states, the moves between them, and the objective gates.

::

    experimental --(evaluation report: right spec, minimums, no regression)--> shadow
    shadow --(enough real decisions run beside production, few failures)--> candidate
    candidate --(recorded human approval)--> production
    any --> deprecated            production --(rollback)--> the previous version, at once

Gates only read evidence -- evaluation reports (``laya-platform eval``) and shadow results the
gateway recorded -- and say what is missing. Nothing here loads a model.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from laya_platform.core.spec import DecisionSpec
from laya_platform.registry.manifest import SpecialistManifest

STATES = ("experimental", "shadow", "candidate", "production", "deprecated")
ALLOWED: dict[str, frozenset[str]] = {
    "experimental": frozenset({"shadow", "deprecated"}),
    "shadow": frozenset({"candidate", "experimental", "deprecated"}),
    "candidate": frozenset({"production", "shadow", "deprecated"}),
    "production": frozenset({"deprecated"}),
    "deprecated": frozenset(),
}


@dataclass(frozen=True)
class Gates:
    min_accuracy: float = 0.0
    max_ece: float | None = None
    #: Accuracy a candidate may lose against the baseline report, overall and per language.
    max_regression: float = 0.0
    min_shadow_samples: int = 200
    min_shadow_agreement: float = 0.0
    max_shadow_failure_rate: float = 0.01


def engine_label(manifest: SpecialistManifest) -> str:
    """How an evaluation of this specialist names its engine."""
    return f"specialist {manifest.key}"


def report_problems(
    manifest: SpecialistManifest,
    spec: DecisionSpec,
    report: Mapping[str, Any],
    gates: Gates,
    baseline: Mapping[str, Any] | None = None,
) -> list[str]:
    """Why ``report`` does not justify moving ``manifest`` to shadow (empty: it does)."""
    identity = report.get("identity", {})
    overall = report.get("overall", {})
    problems = []
    if spec.id not in manifest.decision_specs:
        problems.append(f"{manifest.key} does not serve {spec.id}")
    if (
        identity.get("spec") != spec.id
        or identity.get("questions_sha256") != spec.questions_sha256()
    ):
        problems.append("the report evaluated another spec or other questions")
    if identity.get("engine") != engine_label(manifest):
        problems.append(f"the report evaluated {identity.get('engine')!r}, not {manifest.key}")
    accuracy = overall.get("accuracy")
    if accuracy is None or accuracy < gates.min_accuracy:
        problems.append(f"accuracy {accuracy} < minimum {gates.min_accuracy}")
    ece = overall.get("ece")
    if gates.max_ece is not None and (ece is None or ece > gates.max_ece):
        problems.append(f"ECE {ece} > maximum {gates.max_ece}")
    if baseline is not None:
        problems += _regressions(report, baseline, gates.max_regression)
    return problems


def _regressions(
    report: Mapping[str, Any], baseline: Mapping[str, Any], tolerance: float
) -> list[str]:
    if report.get("identity", {}).get("dataset_sha256") != baseline.get("identity", {}).get(
        "dataset_sha256"
    ):
        return ["the baseline was measured on another dataset"]
    problems = []
    pairs = [("overall", report.get("overall", {}), baseline.get("overall", {}))]
    languages = report.get("slices", {}).get("language", {})
    for language, metrics in baseline.get("slices", {}).get("language", {}).items():
        if language in languages:
            pairs.append((f"language {language}", languages[language], metrics))
    for scope, mine, theirs in pairs:
        if mine.get("accuracy", 0.0) < theirs.get("accuracy", 0.0) - tolerance:
            problems.append(
                f"{scope}: accuracy {mine.get('accuracy'):.4f} below baseline "
                f"{theirs.get('accuracy'):.4f} (tolerance {tolerance})"
            )
    return problems


@dataclass(frozen=True)
class ShadowEvidence:
    samples: int
    failures: int
    agreement: float | None  # share of answers equal to what was served

    def as_dict(self) -> dict[str, Any]:
        return {"samples": self.samples, "failures": self.failures, "agreement": self.agreement}


def shadow_evidence(results: Sequence[Any]) -> ShadowEvidence:
    """Summarize ``ChallengerResult`` rows (any object with ``error`` and ``agreement``)."""
    failures = sum(r.error is not None for r in results)
    answers = [agree for r in results if r.error is None for agree in (r.agreement or {}).values()]
    agreement = sum(bool(a) for a in answers) / len(answers) if answers else None
    return ShadowEvidence(len(results), failures, agreement)


def shadow_problems(evidence: ShadowEvidence, gates: Gates) -> list[str]:
    problems = []
    if evidence.samples < gates.min_shadow_samples:
        problems.append(f"{evidence.samples} shadow decisions < {gates.min_shadow_samples}")
    if evidence.samples and evidence.failures / evidence.samples > gates.max_shadow_failure_rate:
        problems.append(f"{evidence.failures} of {evidence.samples} shadow runs failed")
    if (evidence.agreement or 0.0) < gates.min_shadow_agreement:
        problems.append(f"agreement {evidence.agreement} < {gates.min_shadow_agreement}")
    return problems
