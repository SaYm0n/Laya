"""Evaluation (Block A, F4): datasets, metrics with known values, reports, bands, calibration."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest

from laya_platform.core import load_decision_spec, parse_decision_spec, upstream_compat
from laya_platform.core.adapters import FakeAnswer, FakeEngine
from laya_platform.core.spec import Policy
from laya_platform.evaluation import (
    Case,
    DatasetError,
    build_report,
    calibration,
    choose_threshold,
    evaluate,
    load_examples,
    policy_for,
    to_markdown,
)
from laya_platform.evaluation.metrics import (
    binary_rates,
    brier,
    confusion,
    coverage_risk,
    ece,
    nll,
    per_class,
    reliability,
    summarize,
)
from laya_platform.evaluation.run import Example, expected_label

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = REPO_ROOT / "examples" / "support_triage"
SPEC_PATH = EXAMPLE / "specs" / "support_triage.yaml"
DATA_PATH = EXAMPLE / "eval_ptbr.jsonl"


def _case(
    correct: bool,
    confidence: float | None,
    *,
    expected: str = "true",
    probabilities: dict[str, float] | None = None,
    **extra: Any,
) -> Case:
    predicted = expected if correct else ("false" if expected == "true" else "true")
    return Case(
        qid=extra.pop("qid", "q"),
        kind=extra.pop("kind", "noul"),
        expected_label=expected,
        predicted_label=predicted,
        correct=correct,
        answer_confidence=confidence,
        probabilities=probabilities or {},
        **extra,
    )


# ------------------------------------------------------------------------------------ datasets
def test_the_example_dataset_loads_against_the_example_spec() -> None:
    spec = load_decision_spec(SPEC_PATH)
    examples = load_examples(DATA_PATH, spec)
    assert len(examples) >= 20
    assert {e.language for e in examples} == {"pt", "en"}
    assert {"short", "negation", "noise", "mixed"} <= {t for e in examples for t in e.tags}
    assert all(set(e.expected) == {"department", "urgency", "churn_risk"} for e in examples)


@pytest.mark.parametrize(
    ("line", "message"),
    [
        ("not json", "not JSON"),
        ('{"state": "x"}', "needs 'state' and an 'expected' object"),
        ('{"state": "x", "expected": {"nope": 1}}', "unknown question"),
        ('{"state": "x", "expected": {}, "tags": "short"}', "'tags' must be a list"),
    ],
)
def test_malformed_rows_name_their_line(tmp_path: Path, line: str, message: str) -> None:
    data = tmp_path / "d.jsonl"
    data.write_text('{"state": "ok", "expected": {}}\n' + line + "\n", encoding="utf-8")
    with pytest.raises(DatasetError, match=f"d.jsonl:2: .*{message}"):
        load_examples(data, load_decision_spec(SPEC_PATH))


def test_an_empty_dataset_is_refused(tmp_path: Path) -> None:
    data = tmp_path / "d.jsonl"
    data.write_text("# only a comment\n\n", encoding="utf-8")
    with pytest.raises(DatasetError, match="no examples"):
        load_examples(data, load_decision_spec(SPEC_PATH))


def test_expected_values_become_the_labels_answers_report() -> None:
    spec = load_decision_spec(SPEC_PATH)
    questions = spec.to_questions()
    assert expected_label(spec, questions["churn_risk"], True) == "true"
    assert expected_label(spec, questions["urgency"], 2) == "2"
    assert expected_label(spec, questions["department"], "account") == "account"
    with pytest.raises(DatasetError, match="true or false"):
        expected_label(spec, questions["churn_risk"], "yes")
    with pytest.raises(DatasetError, match="score levels"):
        expected_label(spec, questions["urgency"], 7)


# ------------------------------------------------------------------------------------- metrics
def test_ece_and_reliability_on_known_values() -> None:
    cases = [_case(True, 0.9), _case(False, 0.9), _case(True, 0.3), _case(True, None)]
    rows = reliability(cases)
    assert [(r["low"], r["count"], r["accuracy"]) for r in rows] == [(0.3, 1, 1.0), (0.9, 2, 0.5)]
    assert ece(cases) == pytest.approx(1 / 3 * 0.7 + 2 / 3 * 0.4)
    assert ece([_case(True, None)]) is None
    assert reliability([_case(True, 1.0)])[0]["high"] == 1.0  # 1.0 falls in the last bin


def test_brier_and_nll_on_known_values() -> None:
    cases = [_case(True, 0.8, probabilities={"true": 0.8, "false": 0.2})]
    assert brier(cases) == pytest.approx(0.08)
    assert nll(cases) == pytest.approx(-math.log(0.8))
    assert brier([_case(True, 0.8)]) is None


def test_coverage_risk_on_known_values() -> None:
    cases = [_case(True, 0.9), _case(False, 0.7), _case(True, 0.7), _case(True, None)]
    assert coverage_risk(cases) == [
        {"threshold": 0.9, "coverage": 0.25, "risk": 0.0},
        {"threshold": 0.7, "coverage": 0.75, "risk": pytest.approx(1 / 3)},
    ]


def test_classes_confusion_and_binary_rates() -> None:
    cases = [
        _case(True, 0.9, expected="true"),
        _case(False, 0.6, expected="true"),
        _case(True, 0.8, expected="false"),
        _case(True, 0.7, expected="false"),
    ]
    assert per_class(cases)["true"] == {
        "precision": 1.0,
        "recall": 0.5,
        "f1": pytest.approx(2 / 3),
        "support": 2,
    }
    assert confusion(cases) == {"labels": ["false", "true"], "matrix": [[2, 0], [1, 1]]}
    assert binary_rates(cases) == {"fpr": 0.0, "fnr": 0.5}


def test_summary_never_falls_back_to_entropy_confidence() -> None:
    cases = [
        _case(True, 0.9, latency_ms=10.0, abstention="answered"),
        _case(False, None, latency_ms=30.0, abstention="abstained"),
    ]
    summary = summarize(cases)
    assert summary["n"] == 2
    assert summary["accuracy"] == 0.5
    assert summary["confidence_coverage"] == 0.5
    assert summary["mean_answer_confidence"] == 0.9
    assert summary["abstention_rate"] == 0.5
    assert (summary["latency_p50_ms"], summary["latency_p99_ms"]) == (10.0, 30.0)
    assert summarize([]) == {"n": 0}


def test_score_mae_uses_the_expected_level() -> None:
    cases = [
        _case(True, 0.9, kind="score", expected="2", expected_level=2, score=1.5),
        _case(True, 0.9, kind="score", expected="0", expected_level=0, score=0.5),
    ]
    assert summarize(cases)["score_mae"] == 0.5


# ----------------------------------------------------------------------------- run and report
def test_evaluate_and_report_over_a_fake_engine(tmp_path: Path) -> None:
    spec = load_decision_spec(SPEC_PATH)
    examples = load_examples(DATA_PATH, spec)
    engine = FakeEngine({"department": FakeAnswer("billing", 0.8)})
    cases = evaluate(engine, spec, examples, batch_size=5)
    assert len(cases) == 3 * len(examples)
    assert all(call.method == "predict_batch" for call in engine.calls)
    first_request = engine.calls[0].arguments[0][0]
    assert first_request["model"] == "multilingual"  # engine.checkpoint of the spec
    department = [c for c in cases if c.qid == "department"]
    billing = sum(e.expected["department"] == "billing" for e in examples)
    assert sum(c.correct for c in department) == billing
    assert {c.answer_confidence for c in department} == {0.8}

    report = build_report(spec, cases, dataset=DATA_PATH, engine="fake")
    assert report["schema"] == "laya-platform-eval/1"
    assert report["id"].startswith("eval-")
    assert report["identity"]["spec"] == "examples.support_triage"
    assert report["identity"]["models"] == ["fake-engine"]
    assert report["questions"]["department"]["accuracy"] == pytest.approx(billing / len(examples))
    assert "fpr" in report["questions"]["churn_risk"]
    assert set(report["slices"]) == {"language", "model", "tag"}
    again = build_report(spec, cases, dataset=DATA_PATH, engine="fake")
    assert again["id"] == report["id"]  # the id names what was measured, not when
    assert build_report(spec, cases, dataset=DATA_PATH, engine="other")["id"] != report["id"]
    serialized = json.dumps(report, ensure_ascii=False)
    assert examples[0].state not in serialized  # reports carry labels and scores, no input text
    markdown = to_markdown(report)
    assert report["id"] in markdown
    assert "answer_confidence" in markdown


# --------------------------------------------------------------------------------------- bands
def test_the_cheapest_threshold_wins() -> None:
    cases: list[dict[str, Any]] = [
        {"answer_confidence": 0.95, "correct": True},
        {"answer_confidence": 0.9, "correct": True},
        {"answer_confidence": 0.6, "correct": False},
        {"answer_confidence": 0.5, "correct": True},
        {"answer_confidence": None, "correct": True},
    ]
    choice = choose_threshold(cases, error_cost=5, review_cost=1)
    assert choice.threshold == 0.9
    assert (choice.coverage, choice.auto_error_rate, choice.n) == (0.4, 0.0, 5)
    assert choice.expected_cost == pytest.approx(3 / 5)
    automate_all = choose_threshold(cases, error_cost=0.01, review_cost=1)
    assert automate_all.threshold == 0.5  # an answer without answer_confidence is never automated


def test_ties_go_to_the_safer_choice() -> None:
    choice = choose_threshold(
        [{"answer_confidence": 0.9, "correct": False}], error_cost=1, review_cost=1
    )
    assert choice.threshold is None
    assert choice.coverage == 0.0


def test_costs_must_be_positive_and_cases_present() -> None:
    with pytest.raises(ValueError, match="positive"):
        choose_threshold([{"answer_confidence": 0.9, "correct": True}], error_cost=0, review_cost=1)
    with pytest.raises(ValueError, match="no cases"):
        choose_threshold([], error_cost=1, review_cost=1)


def test_the_policy_names_its_report_and_is_a_valid_spec_policy() -> None:
    choice = choose_threshold(
        [{"answer_confidence": 0.91234567, "correct": True}], error_cost=5, review_cost=1
    )
    policy = policy_for({"id": "eval-0123456789ab"}, choice)
    assert policy == {
        "calibration_ref": "eval-0123456789ab",
        "bands": [{"min": 0.9123, "outcome": "auto"}, {"outcome": "review"}],
    }
    Policy.model_validate(policy)
    never = choose_threshold(
        [{"answer_confidence": 0.9, "correct": False}], error_cost=5, review_cost=1
    )
    assert Policy.model_validate(policy_for({"id": "r"}, never)).bands[0].outcome == "review"


# --------------------------------------------------------------------------------- calibration
def test_targets_are_one_hot_in_the_option_order() -> None:
    spec = load_decision_spec(SPEC_PATH)
    questions = spec.to_questions()
    assert calibration.option_labels(questions["department"]) == ["billing", "technical", "account"]
    assert calibration.option_labels(questions["urgency"]) == ["0", "1", "2", "3"]
    assert calibration.option_labels(questions["churn_risk"]) == ["false", "true"]
    example = Example("x", {"department": "account", "urgency": 2, "churn_risk": True}, "pt", ())
    assert calibration.one_hot_targets(spec, example) == {
        "department": [0.0, 0.0, 1.0],
        "urgency": [0.0, 0.0, 1.0, 0.0],
        "churn_risk": [0.0, 1.0],
    }


def test_targets_follow_the_option_order_slots() -> None:
    spec = parse_decision_spec(
        {
            "id": "examples.ordered",
            "version": 1,
            "languages": ["en"],
            "questions": {
                "team": {
                    "type": "choice",
                    "instructions": "Team?",
                    "criteria": ["billing", "tech", "sales"],
                    "option_order": [2, 0, 1],
                }
            },
        }
    )
    example = Example("x", {"team": "billing"}, "en", ())
    # slot 0 holds option 2 (sales), slot 1 option 0 (billing), slot 2 option 1 (tech)
    assert calibration.one_hot_targets(spec, example) == {"team": [0.0, 1.0, 0.0]}


def test_fit_temperatures_hands_the_upstream_its_pairs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    def records_from_labeled(agent: Any, pairs: list[Any]) -> list[str]:
        seen["pairs"] = pairs
        return ["record"]

    class Agent:
        def fit_temperatures(self, records: list[str], compute_ece: bool) -> dict[str, Any]:
            seen["fit"] = (records, compute_ece)
            return {"temperatures": {"choice": 1.3}}

        def save_calibration(self, path: str) -> None:
            seen["saved"] = path

    api = calibration.RECORDS_FROM_LABELED
    assert (api.module, api.qualname) == ("laya.calibrate", "records_from_labeled")
    monkeypatch.setattr(
        upstream_compat, "resolve", lambda wanted: {api: records_from_labeled}[wanted]
    )
    spec = load_decision_spec(SPEC_PATH)
    example = Example("Ajuda", {"churn_risk": False}, "pt", ())
    out = tmp_path / "calibration.json"
    assert calibration.fit_temperatures(Agent(), spec, [example], out) == {
        "temperatures": {"choice": 1.3}
    }
    ((state, asked, targets),) = seen["pairs"]
    assert state == "Ajuda"
    assert list(asked) == ["churn_risk"]  # only the labelled questions are asked
    assert targets == {"churn_risk": [1.0, 0.0]}
    assert seen["fit"] == (["record"], True)
    assert seen["saved"] == str(out)
