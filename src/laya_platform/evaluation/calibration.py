"""Fit per-type temperatures for one checkpoint on a labelled dataset of one DecisionSpec.

A thin wrapper: the fitting is the upstream's (``Agent.fit_temperatures``, records built by
``laya.calibrate.records_from_labeled`` through ``upstream_compat``) and so is the file
(``Agent.save_calibration``, loaded back with ``laya.load(..., calibration=path)``). Needs torch
and real weights. Fit on a calibration split, then evaluate on a different one.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any

from laya_platform.core import upstream_compat
from laya_platform.core.spec import DecisionSpec
from laya_platform.evaluation.run import Example, expected_label

RECORDS_FROM_LABELED = next(
    api for api in upstream_compat.INTERNAL_APIS if api.qualname == "records_from_labeled"
)


def option_labels(question: dict[str, Any]) -> list[str]:
    """Option labels in the order the model reads them (noul: false, true)."""
    if question["type"] == "noul":
        return ["false", "true"]
    if question["type"] == "score":
        return [str(i) for i in range(len(question["criteria"]))]
    return ["null" if label is None else str(label) for label in question["criteria"]]


def one_hot_targets(spec: DecisionSpec, example: Example) -> dict[str, list[float]]:
    """One-hot targets in the slot order the logits come in.

    With ``option_order`` the upstream puts option ``option_order[s]`` in slot ``s``
    (``laya.common.build_head``), and the calibration logits are per slot, so the targets are too.
    """
    questions = spec.to_questions()
    targets = {}
    for qid, expected in example.expected.items():
        labels = option_labels(questions[qid])
        label = expected_label(spec, questions[qid], expected)
        canonical = [1.0 if option == label else 0.0 for option in labels]
        order = questions[qid].get("option_order")
        targets[qid] = canonical if order is None else [canonical[int(i)] for i in order]
    return targets


def fit_temperatures(
    agent: Any, spec: DecisionSpec, examples: Sequence[Example], out_path: str | os.PathLike[str]
) -> dict[str, Any]:
    """Fit, install on ``agent`` and write the calibration file; returns the upstream's report."""
    questions = spec.to_questions()
    pairs = []
    for example in examples:
        asked = {qid: questions[qid] for qid in example.expected}
        pairs.append((example.state, asked, one_hot_targets(spec, example)))
    records = upstream_compat.resolve(RECORDS_FROM_LABELED)(agent, pairs)
    result: dict[str, Any] = agent.fit_temperatures(records, compute_ece=True)
    agent.save_calibration(os.fspath(out_path))
    return result
