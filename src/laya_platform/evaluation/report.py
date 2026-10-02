"""Evaluation report: identity, metrics overall / per question / per slice, JSON and Markdown.

The ``id`` is a hash of what was measured and what came out, so a policy's ``calibration_ref``
names one exact report.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import laya
from laya.evals import file_fingerprint

from laya_platform import __version__
from laya_platform.core.spec import DecisionSpec
from laya_platform.evaluation.metrics import (
    binary_rates,
    confusion,
    coverage_risk,
    per_class,
    reliability,
    summarize,
)
from laya_platform.evaluation.run import Case

REPORT_SCHEMA = "laya-platform-eval/1"
SLICES = ("language", "model", "tag")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def _slice_values(case: Case, dimension: str) -> list[str]:
    if dimension == "tag":
        return list(case.tags)
    value = getattr(case, dimension)
    return [] if value is None else [str(value)]


def build_report(
    spec: DecisionSpec, cases: Sequence[Case], *, dataset: str | Path, engine: str
) -> dict[str, Any]:
    identity = {
        "spec": spec.id,
        "spec_version": spec.version,
        "questions_sha256": hashlib.sha256(_canonical(spec.to_questions()).encode()).hexdigest(),
        "dataset_sha256": file_fingerprint(str(dataset)),
        "laya_version": laya.__version__,
        "laya_platform_version": __version__,
        "engine": engine,
        "models": sorted({case.model for case in cases if case.model}),
    }
    questions: dict[str, Any] = {}
    for qid in dict.fromkeys(case.qid for case in cases):
        subset = [case for case in cases if case.qid == qid]
        entry: dict[str, Any] = {**summarize(subset), "classes": per_class(subset)}
        entry["confusion"] = confusion(subset)
        if subset[0].kind == "noul":
            entry.update(binary_rates(subset))
        questions[qid] = entry
    slices: dict[str, dict[str, Any]] = {}
    for dimension in SLICES:
        groups: dict[str, list[Case]] = {}
        for case in cases:
            for value in _slice_values(case, dimension):
                groups.setdefault(value, []).append(case)
        if groups:
            slices[dimension] = {value: summarize(group) for value, group in sorted(groups.items())}
    overall = summarize(cases)
    digest = hashlib.sha256(_canonical({"identity": identity, "overall": overall}).encode())
    return {
        "schema": REPORT_SCHEMA,
        "id": f"eval-{digest.hexdigest()[:12]}",
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "identity": identity,
        "overall": overall,
        "questions": questions,
        "slices": slices,
        "reliability": reliability(cases),
        "coverage_risk": coverage_risk(cases),
        "cases": [asdict(case) for case in cases],
    }


def _row(name: str, metrics: dict[str, Any], columns: Sequence[str]) -> str:
    cells = [
        f"{metrics[c]:.4f}" if isinstance(metrics.get(c), float) else str(metrics.get(c, ""))
        for c in columns
    ]
    return f"| {name} | " + " | ".join(cells) + " |"


def to_markdown(report: dict[str, Any]) -> str:
    identity = report["identity"]
    columns = ("n", "accuracy", "ece", "brier", "nll", "mean_answer_confidence", "latency_p95_ms")
    lines = [
        f"# Evaluation {report['id']}",
        "",
        f"- spec: `{identity['spec']}` v{identity['spec_version']}",
        f"- dataset sha256: `{identity['dataset_sha256']}`",
        f"- laya {identity['laya_version']}, laya-platform {identity['laya_platform_version']}, "
        f"engine `{identity['engine']}`, models {identity['models']}",
        "",
        "| scope | " + " | ".join(columns) + " |",
        "|---|" + "---|" * len(columns),
        _row("overall", report["overall"], columns),
    ]
    lines += [_row(f"question `{q}`", m, columns) for q, m in report["questions"].items()]
    for dimension, groups in report["slices"].items():
        lines += [_row(f"{dimension} = {value}", m, columns) for value, m in groups.items()]
    lines += ["", "Confidence is `answer_confidence` throughout (never the entropy `confidence`)."]
    return "\n".join(lines) + "\n"
