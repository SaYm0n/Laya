"""Schema -> questions contract (``laya.structured``, laya 0.3.23) and DecisionSpec on top of it.

The conversion is the upstream's; DecisionSpec calls it and never re-implements it. These tests
freeze what the conversion produces, what it rejects, and the few places where DecisionSpec is
deliberately stricter (each one named below).
"""

from __future__ import annotations

from typing import Any

import pytest
from laya.structured import (
    MAX_OPTIONS,
    MAX_PROPERTIES,
    MAX_SCORE_LEVELS,
    SchemaError,
    answers_to_json,
    questions_from_json_schema,
)

from laya_platform.core import DecisionSpecError, parse_decision_spec
from laya_platform.core.upstream_compat import check_question


def _schema(**properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": properties}


def _spec(**fields: Any) -> dict[str, Any]:
    return {"id": "test.spec", "version": 1, "languages": ["pt"], **fields}


def test_limits() -> None:
    assert (MAX_PROPERTIES, MAX_OPTIONS, MAX_SCORE_LEVELS) == (32, 32, 10)


# --------------------------------------------------------------------- what each property becomes
@pytest.mark.parametrize(
    ("prop", "question"),
    [
        (
            {"type": "string", "enum": ["billing", "technical"], "description": "Team?"},
            {
                "type": "choice",
                "instructions": "Team?",
                "criteria": {"billing": None, "technical": None},
            },
        ),
        (
            {"enum": [1, 2, 3], "description": "Tier?"},
            {
                "type": "choice",
                "instructions": "Tier?",
                "criteria": {"1": None, "2": None, "3": None},
            },
        ),
        (
            {"enum": ["a", None], "description": "A?"},
            {"type": "choice", "instructions": "A?", "criteria": {"a": None, "null": None}},
        ),
        (
            {"const": "billing", "description": "Team?"},
            {"type": "choice", "instructions": "Team?", "criteria": {"billing": None}},
        ),
        (
            {"enum": [True, False], "description": "Flag?"},
            {"type": "noul", "instructions": "Flag?"},
        ),
        ({"type": "boolean", "description": "Flag?"}, {"type": "noul", "instructions": "Flag?"}),
        (
            {"type": "integer", "minimum": 0, "maximum": 4, "description": "Level?"},
            {"type": "score", "instructions": "Level?", "criteria": ["0", "1", "2", "3", "4"]},
        ),
        (
            {"type": "integer", "minimum": 1, "maximum": 3, "description": "Level?"},
            {"type": "score", "instructions": "Level?", "criteria": ["1", "2", "3"]},
        ),
        (
            {"type": "number", "minimum": 0, "maximum": 2, "description": "Level?"},
            {"type": "score", "instructions": "Level?", "criteria": ["0", "1", "2"]},
        ),
        (
            {"anyOf": [{"type": "boolean"}, {"type": "null"}], "description": "Flag?"},
            {"type": "noul", "instructions": "Flag?"},
        ),
        (
            {"type": ["boolean", "null"], "description": "Flag?"},
            {"type": "noul", "instructions": "Flag?"},
        ),
    ],
    ids=[
        "enum->choice",
        "int-enum->string-labels",
        "null-enum-value->'null'-label",
        "const->single-option-choice",
        "boolean-enum->noul",
        "boolean->noul",
        "bounded-integer->score",
        "score-levels-start-at-minimum",
        "number-with-integer-bounds->score",
        "optional-anyOf-unwrapped",
        "nullable-type-list-unwrapped",
    ],
)
def test_property_mapping(prop: dict[str, Any], question: dict[str, Any]) -> None:
    assert questions_from_json_schema(_schema(field=prop)) == {"field": question}


def test_without_a_description_the_upstream_asks_a_generic_question() -> None:
    questions = questions_from_json_schema(
        _schema(
            dept={"enum": ["a", "b"]},
            flag={"type": "boolean"},
            level={"type": "integer", "minimum": 0, "maximum": 4},
        )
    )
    assert [q["instructions"] for q in questions.values()] == [
        "What is `dept`?",
        "Is `flag` true?",
        "Score `level` from 0 to 4",
    ]


def test_required_does_not_change_the_questions() -> None:
    # Every property is asked, required or not; `required` naming a missing property is ignored.
    schema = _schema(a={"type": "boolean"}, b={"type": "boolean"}) | {"required": ["a", "zzz"]}
    assert list(questions_from_json_schema(schema)) == ["a", "b"]


def test_property_order_is_kept() -> None:
    schema = _schema(z={"type": "boolean"}, a={"type": "boolean"}, m={"type": "boolean"})
    assert list(questions_from_json_schema(schema)) == ["z", "a", "m"]


@pytest.mark.parametrize(
    ("schema", "message"),
    [
        (_schema(f={"type": "string"}), "a free string cannot be a fixed option set"),
        (_schema(f={"type": "array", "items": {"type": "boolean"}}), "arrays are not supported"),
        (_schema(f={"type": "object", "properties": {}}), "nested objects are not supported"),
        (_schema(f={"$ref": "#/defs/x"}), r"\$ref/recursion is not supported"),
        (_schema(f={"type": "integer"}), "needs integer 'minimum' and 'maximum'"),
        (_schema(f={"type": "number", "minimum": 0.5, "maximum": 2}), "needs integer 'minimum'"),
        (_schema(f={"type": "integer", "exclusiveMinimum": 0, "maximum": 3}), "needs integer"),
        (_schema(f={"type": "integer", "minimum": 3, "maximum": 1}), "'maximum' 1 is below"),
        (_schema(f={"type": "integer", "minimum": 0, "maximum": 10}), "11 levels exceeds"),
        (_schema(f={"enum": []}), "'enum' must not be empty"),
        (_schema(f={"enum": [1, "1"]}), "duplicate choice labels"),
        (_schema(f={"enum": [str(i) for i in range(33)]}), "33 options exceeds MAX_OPTIONS=32"),
        (_schema(f={"anyOf": [{"type": "boolean"}, {"type": "integer"}]}), "only 'Optional"),
        (_schema(f={"type": ["string", "integer"]}), "multiple non-null types"),
        (_schema(f="boolean"), "property must be an object"),  # type: ignore[arg-type]
        ({"type": "array"}, "top level must be an object"),
        (_schema(), "'properties' must be a non-empty object"),
        (_schema(**{f"p{i}": {"type": "boolean"} for i in range(33)}), "33 properties exceeds"),
    ],
)
def test_unsupported_schemas_are_rejected_with_the_path(
    schema: dict[str, Any], message: str
) -> None:
    with pytest.raises(SchemaError, match=message):
        questions_from_json_schema(schema)


def test_schema_error_is_a_value_error() -> None:
    assert issubclass(SchemaError, ValueError)


def test_projection_onto_schema_values() -> None:
    schema = _schema(
        team={"enum": [10, 20]},
        level={"type": "integer", "minimum": 1, "maximum": 3},
        flag={"type": "boolean"},
        gated={"type": "boolean"},
    )
    answers = {
        "team": {"type": "choice", "choice": "20"},
        "level": {"type": "score", "score": 1.4, "probabilities": {"0": 0.1, "1": 0.2, "2": 0.7}},
        "flag": {"type": "noul", "noul": 0.5},
        "gated": {"type": "noul", "noul": 0.9, "low_confidence": True},
    }
    # The enum value (not its label), the level shifted by `minimum`, P(true) >= 0.5, and None
    # for an answer the gate flagged.
    assert answers_to_json(answers, schema) == {"team": 20, "level": 3, "flag": True, "gated": None}


# --------------------------------------------------------------------- DecisionSpec on top of it
def test_a_schema_spec_asks_exactly_the_upstream_questions() -> None:
    schema = _schema(
        team={"enum": ["a", "b"], "description": "Team?"},
        level={"type": "integer", "minimum": 0, "maximum": 2, "description": "Level?"},
    )
    spec = parse_decision_spec(_spec(schema=schema))
    assert spec.to_questions() == questions_from_json_schema(schema)
    assert spec.to_questions() is not spec.to_questions()


def test_a_schema_the_upstream_rejects_is_rejected_by_the_spec() -> None:
    with pytest.raises(
        DecisionSpecError, match=r"not supported by laya\.structured: .*free string"
    ):
        parse_decision_spec(_spec(schema=_schema(f={"type": "string", "description": "x"})))


def test_the_spec_requires_a_description_on_every_property() -> None:
    # Stricter than the upstream on purpose: without one the model reads "What is `team`?".
    schema = _schema(team={"enum": ["a"]}, flag={"type": "boolean", "description": "   "})
    with pytest.raises(DecisionSpecError, match=r"without a 'description': \['team', 'flag'\]"):
        parse_decision_spec(_spec(schema=schema))


def test_a_questions_spec_keeps_the_upstream_format() -> None:
    questions = {
        "team": {"type": "choice", "instructions": "Team?", "criteria": {"a": "first", "b": None}},
        "tier": {
            "type": "choice",
            "instructions": "Tier?",
            "criteria": ["x", "y"],
            "option_order": [1, 0],
        },
        "level": {"type": "score", "instructions": "Level?", "criteria": ["low", "high"]},
        "flag": {"type": "noul", "instructions": "Flag?"},
        "named": {
            "type": "noul",
            "instructions": "Named?",
            "criteria": {"true": "it is", "false": "it is not"},
            "labels": {"false": "B", "true": "A"},
        },
    }
    assert parse_decision_spec(_spec(questions=questions)).to_questions() == questions


def test_boolean_noul_keys_are_written_as_the_upstream_reads_them() -> None:
    questions = {
        "flag": {"type": "noul", "instructions": "F?", "criteria": {True: "y", False: "n"}}
    }
    spec = parse_decision_spec(_spec(questions=questions))
    assert spec.to_questions()["flag"]["criteria"] == {"true": "y", "false": "n"}


@pytest.mark.parametrize(
    ("questions", "message"),
    [
        (
            {f"q{i}": {"type": "noul", "instructions": "x"} for i in range(65)},
            r"too many questions \(65 > 64\)",
        ),
        (
            {
                "q": {
                    "type": "choice",
                    "instructions": "x",
                    "criteria": [f"o{i}" for i in range(101)],
                }
            },
            r"too many choice options for 'q' \(101 > 100\)",
        ),
        (
            {"q": {"type": "score", "instructions": "x", "criteria": [f"l{i}" for i in range(33)]}},
            r"too many score levels for 'q' \(33 > 32\)",
        ),
        (
            {
                f"q{i}": {
                    "type": "choice",
                    "instructions": "x",
                    "criteria": [f"o{j}" for j in range(90)],
                }
                for i in range(6)
            },
            r"too many answer options across questions \(540 > 512\)",
        ),
    ],
    ids=["questions", "choice-options", "score-levels", "total-options"],
)
def test_a_questions_spec_fits_the_http_limits(questions: dict[str, Any], message: str) -> None:
    with pytest.raises(DecisionSpecError, match=message):
        parse_decision_spec(_spec(questions=questions))


# ------------------------------------------- question rules against the upstream's own validator
#: (definition, accepted by DecisionSpec, accepted by the upstream Agent)
QUESTION_CASES: list[tuple[str, dict[str, Any], bool, bool]] = [
    (
        "choice-dict",
        {"type": "choice", "instructions": "x", "criteria": {"a": "A", "b": None}},
        True,
        True,
    ),
    ("choice-list", {"type": "choice", "instructions": "x", "criteria": ["a", "b"]}, True, True),
    (
        "choice-order",
        {"type": "choice", "instructions": "x", "criteria": ["a", "b"], "option_order": [1, 0]},
        True,
        True,
    ),
    ("score", {"type": "score", "instructions": "x", "criteria": ["low", "high"]}, True, True),
    ("noul", {"type": "noul", "instructions": "x"}, True, True),
    (
        "noul-criteria",
        {"type": "noul", "instructions": "x", "criteria": {"true": "y", "false": "n"}},
        True,
        True,
    ),
    ("noul-bool-keys", {"type": "noul", "instructions": "x", "criteria": {True: "y"}}, True, True),
    (
        "noul-labels",
        {"type": "noul", "instructions": "x", "labels": {"false": "B", "true": "A"}},
        True,
        True,
    ),
    ("unknown-type", {"type": "rating", "instructions": "x", "criteria": ["a"]}, False, False),
    ("no-instructions", {"type": "noul"}, False, False),
    ("blank-instructions", {"type": "noul", "instructions": "   "}, False, False),
    ("choice-empty", {"type": "choice", "instructions": "x", "criteria": {}}, False, False),
    (
        "choice-duplicate",
        {"type": "choice", "instructions": "x", "criteria": ["a", "a"]},
        False,
        False,
    ),
    ("choice-string", {"type": "choice", "instructions": "x", "criteria": "a,b"}, False, False),
    (
        "choice-null-label",
        {"type": "choice", "instructions": "x", "criteria": ["a", None]},
        False,
        False,
    ),
    ("score-dict", {"type": "score", "instructions": "x", "criteria": {"0": "low"}}, False, False),
    (
        "score-null-level",
        {"type": "score", "instructions": "x", "criteria": ["a", None]},
        False,
        False,
    ),
    ("score-empty", {"type": "score", "instructions": "x", "criteria": []}, False, False),
    (
        "noul-other-key",
        {"type": "noul", "instructions": "x", "criteria": {"maybe": "?"}},
        False,
        False,
    ),
    ("noul-list", {"type": "noul", "instructions": "x", "criteria": ["y", "n"]}, False, False),
    (
        "labels-on-choice",
        {
            "type": "choice",
            "instructions": "x",
            "criteria": ["a"],
            "labels": {"false": "B", "true": "A"},
        },
        False,
        False,
    ),
    (
        "labels-incomplete",
        {"type": "noul", "instructions": "x", "labels": {"true": "A"}},
        False,
        False,
    ),
    (
        "labels-equal",
        {"type": "noul", "instructions": "x", "labels": {"false": "A", "true": " A "}},
        False,
        False,
    ),
    (
        "order-repeats",
        {"type": "choice", "instructions": "x", "criteria": ["a", "b"], "option_order": [0, 0]},
        False,
        False,
    ),
    (
        "order-short",
        {"type": "score", "instructions": "x", "criteria": ["a", "b"], "option_order": [0]},
        False,
        False,
    ),
    # Deliberately stricter than the upstream: config files name options with non-blank text.
    ("stricter-dict-instructions", {"type": "noul", "instructions": {"text": "x"}}, False, True),
    (
        "stricter-number-labels",
        {"type": "choice", "instructions": "x", "criteria": [1, 2]},
        False,
        True,
    ),
    (
        "stricter-empty-label",
        {"type": "choice", "instructions": "x", "criteria": ["", "b"]},
        False,
        True,
    ),
    (
        "stricter-empty-level",
        {"type": "score", "instructions": "x", "criteria": ["", "b"]},
        False,
        True,
    ),
]


def _spec_accepts(definition: dict[str, Any]) -> bool:
    try:
        parse_decision_spec(_spec(questions={"q": definition}))
    except DecisionSpecError:
        return False
    return True


def _upstream_accepts(definition: dict[str, Any]) -> bool:
    try:
        check_question("q", definition)
    except ValueError:
        return False
    return True


@pytest.mark.parametrize(
    ("definition", "accepted"),
    [(definition, spec) for _, definition, spec, _ in QUESTION_CASES],
    ids=[name for name, *_ in QUESTION_CASES],
)
def test_decision_spec_question_rules(definition: dict[str, Any], accepted: bool) -> None:
    assert _spec_accepts(definition) is accepted


@pytest.mark.torch
@pytest.mark.parametrize(
    ("definition", "accepted"),
    [(definition, upstream) for _, definition, _, upstream in QUESTION_CASES],
    ids=[name for name, *_ in QUESTION_CASES],
)
def test_the_upstream_agent_agrees(definition: dict[str, Any], accepted: bool) -> None:
    # Everything DecisionSpec accepts, the upstream accepts; everything it rejects for a reason the
    # upstream shares, the upstream rejects. Runs wherever torch is installed (Full install).
    assert _upstream_accepts(definition) is accepted


def test_the_spec_is_never_more_permissive_than_the_upstream() -> None:
    assert all(upstream for _, _, spec, upstream in QUESTION_CASES if spec)
