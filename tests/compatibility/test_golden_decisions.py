"""Reference decisions must not regress across upstream upgrades (weights-gated).

The cases in ``golden/decisions.json`` are synthetic and public (no real data). Their baseline is
recorded from real checkpoints at the upstream's reviewed revision, on CPU in fp32, one state per
call, and committed. After an upgrade the new release is compared with that baseline; once it
passes (or a reviewed difference is accepted) the baseline is recorded again.

Record (on a machine that can reach huggingface.co)::

    LAYA_PLATFORM_RECORD_GOLDEN=1 uv run pytest --run-weights -k golden_decision tests/compatibility

Without a baseline the test fails instead of passing vacuously.

Tolerances, and why:

* ``PROBABILITY_TOLERANCE = 0.02`` absolute, on every option probability, ``answer_confidence`` and
  ``noul``. The upstream rounds to 1e-4 and one release on one machine reproduces to that
  precision; another CPU, BLAS build or torch version moves fp32 results in the third or fourth
  decimal, and the upstream documents such floating-point drift across devices and batch shapes.
  0.02 is an order of magnitude above that noise and well below anything decision-relevant
  (confidence bands are tenths apart); it is also the per-metric tolerance the upstream's own
  ``laya-evals compare`` examples use.
* The winning label must stay the same only where the recorded winner led the runner-up by more
  than twice the tolerance; a closer race may flip without being a regression.
* ``score`` (an expected value) may move by at most what the per-level tolerance allows:
  ``tolerance * sum(range(k))``.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import laya
import pytest
from laya import Router

GOLDEN = Path(__file__).resolve().parent / "golden" / "decisions.json"
RECORD = os.environ.get("LAYA_PLATFORM_RECORD_GOLDEN") == "1"
PROBABILITY_TOLERANCE = 0.02
LABEL_MARGIN = 2 * PROBABILITY_TOLERANCE

DATA: dict[str, Any] = json.loads(GOLDEN.read_text(encoding="utf-8"))
CASES: dict[str, dict[str, Any]] = {case["id"]: case for case in DATA["cases"]}


def _fingerprint(case: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(case, sort_keys=True).encode("utf-8")).hexdigest()


def _observe(payload: dict[str, Any]) -> dict[str, Any]:
    observed: dict[str, Any] = {"routing_model": payload["routing"]["model"], "answers": {}}
    for qid, answer in payload["answers"].items():
        if answer["type"] == "noul":
            probabilities = {"false": round(1 - answer["noul"], 4), "true": answer["noul"]}
        else:
            probabilities = dict(answer["probabilities"])
        entry = {
            "type": answer["type"],
            "label": max(probabilities, key=probabilities.__getitem__),
            "probabilities": probabilities,
            "answer_confidence": answer["answer_confidence"],
        }
        if answer["type"] == "score":
            entry["score"] = answer["score"]
        observed["answers"][qid] = entry
    return observed


def _run(router: Router, case: dict[str, Any]) -> dict[str, Any]:
    return _observe(router.predict(case["state"], case["questions"], model=case["checkpoint"]))


@pytest.fixture(scope="module")
def baseline(weighted_router: Router) -> dict[str, Any]:
    if RECORD:
        recorded = {
            "laya": laya.__version__,
            "revision": weighted_router.revision,
            "device": "cpu",
            "cases": {
                case_id: {"fingerprint": _fingerprint(case), **_run(weighted_router, case)}
                for case_id, case in CASES.items()
            },
        }
        GOLDEN.write_text(
            json.dumps({**DATA, "baseline": recorded}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        pytest.skip(f"baseline recorded to {GOLDEN}; review, commit and rerun without recording")
    if DATA["baseline"] is None:
        pytest.fail(
            "no golden baseline recorded yet: record one with LAYA_PLATFORM_RECORD_GOLDEN=1 "
            "(see this module's docstring); comparing against nothing would pass vacuously"
        )
    recorded_baseline: dict[str, Any] = DATA["baseline"]
    return recorded_baseline


def _differences(expected: dict[str, Any], observed: dict[str, Any]) -> list[str]:
    problems = []
    if observed["routing_model"] != expected["routing_model"]:
        problems.append(
            f"routed to {observed['routing_model']}, baseline {expected['routing_model']}"
        )
    for qid, want in expected["answers"].items():
        got = observed["answers"][qid]
        for option, p in want["probabilities"].items():
            if abs(got["probabilities"][option] - p) > PROBABILITY_TOLERANCE:
                problems.append(f"{qid}[{option}] {got['probabilities'][option]} vs {p}")
        if abs(got["answer_confidence"] - want["answer_confidence"]) > PROBABILITY_TOLERANCE:
            problems.append(
                f"{qid} answer_confidence {got['answer_confidence']} vs {want['answer_confidence']}"
            )
        ranked = sorted(want["probabilities"].values(), reverse=True)
        decisive = len(ranked) == 1 or ranked[0] - ranked[1] > LABEL_MARGIN
        if decisive and got["label"] != want["label"]:
            problems.append(f"{qid} answered {got['label']!r}, baseline {want['label']!r}")
        if want["type"] == "score":
            allowed = PROBABILITY_TOLERANCE * sum(range(len(want["probabilities"])))
            if abs(got["score"] - want["score"]) > allowed:
                problems.append(
                    f"{qid} score {got['score']} vs {want['score']} (allowed {allowed})"
                )
    return problems


@pytest.mark.weights
@pytest.mark.parametrize("case_id", sorted(CASES))
def test_golden_decision(weighted_router: Router, baseline: dict[str, Any], case_id: str) -> None:
    case = CASES[case_id]
    expected = baseline["cases"].get(case_id)
    assert expected is not None, f"{case_id} has no baseline; record again"
    assert expected["fingerprint"] == _fingerprint(case), f"{case_id} changed since the baseline"
    assert _differences(expected, _run(weighted_router, case)) == []


def test_the_comparison_catches_a_regression() -> None:
    # The comparison itself, on a synthetic baseline: runs in every PR, without weights.
    want = {
        "routing_model": "english",
        "answers": {
            "q": {
                "type": "choice",
                "label": "a",
                "probabilities": {"a": 0.7, "b": 0.3},
                "answer_confidence": 0.7,
            }
        },
    }
    assert _differences(want, want) == []
    drifted = json.loads(json.dumps(want))
    drifted["answers"]["q"].update(label="b", probabilities={"a": 0.3, "b": 0.7})
    assert len(_differences(want, drifted)) == 3
