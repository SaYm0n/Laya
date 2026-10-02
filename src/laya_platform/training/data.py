"""Datasets for specialists (F8): what to label, joining labels to inputs, splits, a teacher.

The platform never stores inputs (only their HMAC), so a dataset is built by joining what the
source system exports -- ``{"trace_id", "state", "group"?, "language"?, "tags"?}`` per line -- with
the labels the platform holds for those decisions, after checking that each exported state is
the one that was decided (same HMAC). Labels, best first:

* ``human``: the resolution of a review item (``/api/v1/reviews/{id}/resolve``);
* ``incumbent``: the values the existing system decided (when you trust it);
* ``teacher``: System-2's values (an LLM tier), or the vote distribution of :func:`teacher_label`.

Rows come out in the evaluation format (``laya-platform eval``) plus provenance, so the same
files train, calibrate and evaluate. Nothing here runs with real data before DG-1.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from laya_platform.core.spec import DecisionSpec
from laya_platform.evaluation.run import expected_label
from laya_platform.llm import LLMError, LLMGateway
from laya_platform.privacy import input_hmac, redact
from laya_platform.storage import AuditEvent, Database, ReviewItem

SOURCES = ("human", "incumbent", "teacher")
SPLITS = ("train", "validation", "calibration", "test")


class DatasetBuildError(ValueError):
    """The inputs cannot be turned into a dataset (the message says why)."""


# ------------------------------------------------------------------------------- what to label
def candidates(database: Database, spec_id: str, limit: int = 100) -> list[dict[str, Any]]:
    """Decisions most worth a label, most useful first.

    Disagreement first (with the incumbent, or with System-2), then what the policy did not
    automate, then the least confident; decisions already answered by a human are left out.
    """
    with database.reader() as session:
        events = list(session.scalars(select(AuditEvent).where(AuditEvent.spec_id == spec_id)))
        resolved = set(
            session.scalars(
                select(ReviewItem.trace_id).where(
                    ReviewItem.spec_id == spec_id, ReviewItem.status == "resolved"
                )
            )
        )
    ranked = []
    for event in events:
        if event.trace_id in resolved or event.error is not None:
            continue
        confidences = [
            a.get("answer_confidence")
            for a in (event.answers or {}).values()
            if a.get("answer_confidence") is not None
        ]
        lowest = min(confidences) if confidences else 0.0
        system2_agreement = (event.system2 or {}).get("agreement") or {}
        disagrees = not all((event.agreement or {}).values()) or not all(system2_agreement.values())
        if disagrees:
            reason, rank = "disagreement", 0
        elif event.outcome in ("review", "escalate"):
            reason, rank = f"outcome {event.outcome}", 1
        else:
            reason, rank = "low confidence", 2
        ranked.append((rank, lowest, event.trace_id, reason))
    ranked.sort()
    return [
        {"trace_id": trace_id, "reason": reason, "lowest_answer_confidence": lowest}
        for _, lowest, trace_id, reason in ranked[:limit]
    ]


# -------------------------------------------------------------------------------------- build
def read_jsonl(path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    rows = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8-sig").splitlines(), 1):
        if line.strip():
            row = json.loads(line)
            if not isinstance(row, dict):
                raise DatasetBuildError(f"{path}:{number}: not a JSON object")
            rows.append(row)
    return rows


def write_jsonl(rows: Iterable[Mapping[str, Any]], path: str | os.PathLike[str]) -> str:
    """Write rows and return the file's sha256."""
    text = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    Path(path).write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_dataset(
    database: Database,
    spec: DecisionSpec,
    exported: Sequence[Mapping[str, Any]],
    *,
    hmac_key: bytes,
    sources: Sequence[str] = SOURCES,
    redact_pii: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Labelled rows for ``spec`` and what was skipped and why."""
    unknown = sorted(set(sources) - set(SOURCES))
    if unknown:
        raise DatasetBuildError(f"unknown label source(s) {unknown}; use {list(SOURCES)}")
    asked = set(spec.to_questions())
    trace_ids = [str(row.get("trace_id")) for row in exported]
    with database.reader() as session:
        events = {
            e.trace_id: e
            for e in session.scalars(select(AuditEvent).where(AuditEvent.trace_id.in_(trace_ids)))
        }
        resolutions = {
            r.trace_id: r.resolution
            for r in session.scalars(
                select(ReviewItem).where(
                    ReviewItem.trace_id.in_(trace_ids), ReviewItem.status == "resolved"
                )
            )
        }
    stats: Counter[str] = Counter()
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for row in exported:
        trace_id, state = str(row.get("trace_id")), row.get("state")
        event = events.get(trace_id)
        if event is None or event.spec_id != spec.id:
            stats["unknown_trace"] += 1
            continue
        if input_hmac(hmac_key, state) != event.input_hmac:
            stats["hmac_mismatch"] += 1  # not the input that was decided
            continue
        if event.input_hmac in seen:
            stats["duplicate"] += 1
            continue
        label = _label(event, resolutions.get(trace_id), sources, asked)
        if label is None:
            stats["unlabelled"] += 1
            continue
        source, expected = label
        seen.add(event.input_hmac)
        if redact_pii:
            state, counts = redact(state)
            stats["redacted"] += sum(counts.values())
        rows.append(
            {
                "state": state,
                "expected": expected,
                "language": row.get("language"),
                "tags": list(row.get("tags") or []),
                "group": row.get("group") or trace_id,
                "label_source": source,
                "trace_id": trace_id,
                "created_at": event.created_at.isoformat(),
            }
        )
        stats[f"label_{source}"] += 1
    return rows, dict(stats)


def _label(
    event: AuditEvent,
    resolution: Mapping[str, Any] | None,
    sources: Sequence[str],
    asked: set[str],
) -> tuple[str, dict[str, Any]] | None:
    candidates_by_source = {
        "human": (resolution or {}).get("values"),
        "incumbent": event.incumbent,
        "teacher": (event.system2 or {}).get("values"),
    }
    for source in SOURCES:
        values = candidates_by_source[source]
        if source in sources and values:
            expected = {qid: v for qid, v in values.items() if qid in asked}
            if expected:
                return source, expected
    return None


# -------------------------------------------------------------------------------------- split
def _bucket(key: str, seed: str) -> float:
    digest = hashlib.sha256(f"{seed}:{key}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def split(
    rows: Sequence[Mapping[str, Any]],
    *,
    by: str = "group",
    ratios: Sequence[float] = (0.7, 0.1, 0.1, 0.1),
    seed: str = "laya-platform",
) -> dict[str, list[dict[str, Any]]]:
    """train / validation / calibration / test, never sharing a group (``by="group"``) or with
    the newest decisions in test (``by="time"``). Deterministic."""
    if len(ratios) != len(SPLITS) or any(r < 0 for r in ratios) or abs(sum(ratios) - 1) > 1e-9:
        raise DatasetBuildError(f"ratios must be {len(SPLITS)} non-negative numbers summing to 1")
    bounds: list[float] = []
    for ratio in ratios:
        bounds.append((bounds[-1] if bounds else 0.0) + ratio)

    def place(position: float) -> str:
        return next((n for n, b in zip(SPLITS, bounds, strict=True) if position < b), SPLITS[-1])

    out: dict[str, list[dict[str, Any]]] = {name: [] for name in SPLITS}
    if by == "group":
        for row in rows:
            key = str(row.get("group") or row.get("trace_id") or row["state"])
            out[place(_bucket(key, seed))].append(dict(row))
    elif by == "time":
        ordered = sorted(rows, key=lambda r: str(r.get("created_at", "")))
        for index, row in enumerate(ordered):
            out[place(index / len(ordered))].append(dict(row))
    else:
        raise DatasetBuildError("split by 'group' or 'time'")
    return out


def write_splits(
    splits: Mapping[str, Sequence[Mapping[str, Any]]],
    out_dir: str | os.PathLike[str],
    spec: DecisionSpec,
    *,
    by: str,
    seed: str,
) -> dict[str, Any]:
    """One JSONL per split and a ``manifest.json`` (digests, counts, label provenance)."""
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "schema": "laya-platform-dataset/1",
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "spec": spec.id,
        "spec_version": spec.version,
        "questions_sha256": spec.questions_sha256(),
        "split_by": by,
        "seed": seed,
        "splits": {},
    }
    for name, rows in splits.items():
        file = directory / f"{name}.jsonl"
        manifest["splits"][name] = {
            "file": file.name,
            "sha256": write_jsonl(rows, file),
            "rows": len(rows),
            "label_sources": dict(Counter(r.get("label_source", "?") for r in rows)),
            "languages": dict(Counter(str(r.get("language")) for r in rows)),
        }
    (directory / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return manifest


# ------------------------------------------------------------------------------------ teacher
def teacher_label(
    llm: LLMGateway,
    tier: str,
    spec: DecisionSpec,
    rows: Sequence[Mapping[str, Any]],
    *,
    samples: int = 3,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Ask ``tier`` ``samples`` times per row: the vote shares become ``gold`` (the soft targets
    the upstream recipe trains on) and the majority becomes ``expected``.

    A provider that returns the same answer every time gives one-hot votes; measure the spread
    before trusting it, and keep a human-labelled subset as the reference (docs: F8).
    """
    if samples < 1:
        raise DatasetBuildError("samples must be at least 1")
    questions = spec.to_questions()
    labelled: list[dict[str, Any]] = []
    cost, failures = 0.0, 0
    for row in rows:
        votes: dict[str, Counter[str]] = {qid: Counter() for qid in questions}
        values_by_label: dict[str, dict[str, Any]] = {qid: {} for qid in questions}
        for _ in range(samples):
            try:
                values, call = llm.decide(tier, spec, row["state"])
            except LLMError as exc:
                failures += 1
                cost += exc.call.cost if exc.call else 0.0
                continue
            cost += call.cost
            for qid, value in values.items():
                label = expected_label(spec, questions[qid], value)
                votes[qid][label] += 1
                values_by_label[qid][label] = value
        counted = {qid: c for qid, c in votes.items() if c}
        if not counted:
            continue
        gold = {
            qid: {"probabilities": {lbl: n / sum(c.values()) for lbl, n in c.items()}}
            for qid, c in counted.items()
        }
        expected = {qid: values_by_label[qid][c.most_common(1)[0][0]] for qid, c in counted.items()}
        labelled.append({**row, "expected": expected, "gold": gold, "label_source": "teacher"})
    return labelled, {"rows": len(labelled), "failures": failures, "cost": cost, "tier": tier}
