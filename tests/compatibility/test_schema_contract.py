"""Schema -> questions contract (``laya.structured``, laya 0.3.23) and DecisionSpec on top of it.

Part 1 freezes the upstream conversion. Part 2 checks that DecisionSpec preserves it: a schema or a
question is valid for the spec **exactly** when the upstream accepts it -- the spec neither rejects
what the upstream accepts nor accepts what it rejects. The spec's other rules (configuration safety
and the platform's own envelope) live in tests/unit/test_decision_spec.py and never touch what may
be asked.
"""

from __future__ import annotations

import copy
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


def _schema(**properties: Any) -> dict[str, Any]:
    return {"type": "object", "properties": properties}


def _spec(**fields: Any) -> dict[str, Any]:
    return {"id": "test.spec", "version": 1, "languages": ["pt"], **fields}


# ================================================================ 1. the upstream conversion
def test_limits() -> None:
    assert (MAX_PROPERTIES, MAX_OPTIONS, MAX_SCORE_LEVELS) == (32, 32, 10)


#: (id, property, the question the upstream makes of it)
PROPERTY_CASES: list[tuple[str, dict[str, Any], dict[str, Any]]] = [
    (
        "enum->choice",
        {"type": "string", "enum": ["billing", "technical"], "description": "Team?"},
        {
            "type": "choice",
            "instructions": "Team?",
            "criteria": {"billing": None, "technical": None},
        },
    ),
    (
        "int-enum->string-labels",
        {"enum": [1, 2, 3], "description": "Tier?"},
        {"type": "choice", "instructions": "Tier?", "criteria": {"1": None, "2": None, "3": None}},
    ),
    (
        "null-enum-value->'null'-label",
        {"enum": ["a", None], "description": "A?"},
        {"type": "choice", "instructions": "A?", "criteria": {"a": None, "null": None}},
    ),
    (
        "const->single-option-choice",
        {"const": "billing", "description": "Team?"},
        {"type": "choice", "instructions": "Team?", "criteria": {"billing": None}},
    ),
    (
        "boolean-enum->noul",
        {"enum": [True, False], "description": "Flag?"},
        {"type": "noul", "instructions": "Flag?"},
    ),
    (
        "boolean->noul",
        {"type": "boolean", "description": "Flag?"},
        {"type": "noul", "instructions": "Flag?"},
    ),
    (
        "bounded-integer->score",
        {"type": "integer", "minimum": 0, "maximum": 4, "description": "Level?"},
        {"type": "score", "instructions": "Level?", "criteria": ["0", "1", "2", "3", "4"]},
    ),
    (
        "score-levels-start-at-minimum",
        {"type": "integer", "minimum": 1, "maximum": 3, "description": "Level?"},
        {"type": "score", "instructions": "Level?", "criteria": ["1", "2", "3"]},
    ),
    (
        "number-with-integer-bounds->score",
        {"type": "number", "minimum": 0, "maximum": 2, "description": "Level?"},
        {"type": "score", "instructions": "Level?", "criteria": ["0", "1", "2"]},
    ),
    (
        "optional-anyOf-unwrapped",
        {"anyOf": [{"type": "boolean"}, {"type": "null"}], "description": "Flag?"},
        {"type": "noul", "instructions": "Flag?"},
    ),
    (
        "nullable-type-list-unwrapped",
        {"type": ["boolean", "null"], "description": "Flag?"},
        {"type": "noul", "instructions": "Flag?"},
    ),
    (
        "no-description->generic-choice",
        {"enum": ["a", "b"]},
        {"type": "choice", "instructions": "What is `field`?", "criteria": {"a": None, "b": None}},
    ),
    (
        "no-description->generic-noul",
        {"type": "boolean"},
        {"type": "noul", "instructions": "Is `field` true?"},
    ),
    (
        "no-description->generic-score",
        {"type": "integer", "minimum": 0, "maximum": 2},
        {"type": "score", "instructions": "Score `field` from 0 to 2", "criteria": ["0", "1", "2"]},
    ),
]

#: (schema, the path-naming error the upstream raises)
UNSUPPORTED_SCHEMAS: list[tuple[Any, str]] = [
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
    (_schema(f="boolean"), "property must be an object"),
    ({"type": "array"}, "top level must be an object"),
    (_schema(), "'properties' must be a non-empty object"),
    (_schema(**{f"p{i}": {"type": "boolean"} for i in range(33)}), "33 properties exceeds"),
]


@pytest.mark.parametrize(
    ("prop", "question"),
    [(prop, question) for _, prop, question in PROPERTY_CASES],
    ids=[name for name, *_ in PROPERTY_CASES],
)
def test_property_mapping(prop: dict[str, Any], question: dict[str, Any]) -> None:
    assert questions_from_json_schema(_schema(field=prop)) == {"field": question}


def test_required_does_not_change_the_questions() -> None:
    # Every property is asked, required or not; `required` naming a missing property is ignored.
    schema = _schema(a={"type": "boolean"}, b={"type": "boolean"}) | {"required": ["a", "zzz"]}
    assert list(questions_from_json_schema(schema)) == ["a", "b"]


def test_property_order_is_kept() -> None:
    schema = _schema(z={"type": "boolean"}, a={"type": "boolean"}, m={"type": "boolean"})
    assert list(questions_from_json_schema(schema)) == ["z", "a", "m"]


@pytest.mark.parametrize(("schema", "message"), UNSUPPORTED_SCHEMAS)
def test_unsupported_schemas_are_rejected_with_the_path(schema: Any, message: str) -> None:
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


# ================================================= 2. DecisionSpec preserves the upstream contract
@pytest.mark.parametrize(
    "prop", [prop for _, prop, _ in PROPERTY_CASES], ids=[name for name, *_ in PROPERTY_CASES]
)
def test_every_schema_the_upstream_accepts_is_a_valid_spec(prop: dict[str, Any]) -> None:
    schema = _schema(field=prop)
    spec = parse_decision_spec(_spec(schema=schema))
    assert spec.to_questions() == questions_from_json_schema(schema)


def test_a_schema_without_any_description_asks_what_the_upstream_asks() -> None:
    # The upstream's own documented example has no descriptions; the spec must not refuse it.
    schema = _schema(
        department={"enum": ["billing", "support", "sales"]},
        urgency={"type": "integer", "minimum": 0, "maximum": 2},
        needs_human={"type": "boolean"},
    )
    questions = parse_decision_spec(_spec(schema=schema)).to_questions()
    assert questions == questions_from_json_schema(schema)
    assert [q["instructions"] for q in questions.values()] == [
        "What is `department`?",
        "Score `urgency` from 0 to 2",
        "Is `needs_human` true?",
    ]


@pytest.mark.parametrize(("schema", "message"), UNSUPPORTED_SCHEMAS)
def test_every_schema_the_upstream_rejects_is_rejected(schema: Any, message: str) -> None:
    with pytest.raises(DecisionSpecError, match=r"not supported by laya\.structured: "):
        parse_decision_spec(_spec(schema=schema))


def test_to_questions_is_a_fresh_copy() -> None:
    spec = parse_decision_spec(_spec(schema=_schema(flag={"type": "boolean"})))
    assert spec.to_questions() is not spec.to_questions()


def test_questions_reach_the_engine_exactly_as_written() -> None:
    questions: dict[Any, Any] = {
        "team": {"type": "choice", "instructions": "Team?", "criteria": {"a": "first", "b": None}},
        "tier": {"type": "choice", "instructions": {"ask": "Tier?"}, "criteria": [1, 2, 3]},
        "order": {
            "type": "choice",
            "instructions": "x",
            "criteria": ["", "y"],
            "option_order": [1, 0],
        },
        "level": {"type": "score", "instructions": "Level?", "criteria": ["", "low", 3]},
        "flag": {"type": "noul", "instructions": "Flag?", "criteria": {True: "y", "FALSE": "n"}},
        "named": {"type": "noul", "instructions": "N?", "labels": {"false": "B", "true": "A"}},
        "noted": {"type": "noul", "instructions": "x", "notes": "ignored by the upstream"},
        7: {"type": "noul", "instructions": "an integer id, as the upstream allows"},
    }
    spec = parse_decision_spec(_spec(questions=copy.deepcopy(questions)))
    out = spec.to_questions()
    assert out == questions
    assert list(out["flag"]["criteria"]) == [True, "FALSE"]  # the upstream normalises, not the spec
    out["team"]["criteria"]["c"] = None
    assert "c" not in spec.to_questions()["team"]["criteria"]


def test_http_limits_are_not_decision_spec_rules() -> None:
    # laya.serve refuses these over HTTP (413); the in-process Agent accepts them. Applying the
    # HTTP limits to every spec would be a platform decision, left open (see the F2 report).
    questions: dict[str, Any] = {f"q{i}": {"type": "noul", "instructions": "x"} for i in range(65)}
    questions["wide"] = {"type": "choice", "instructions": "x", "criteria": list(range(101))}
    assert len(parse_decision_spec(_spec(questions=questions)).to_questions()) == 66


#: (id, definition, whether the pinned upstream Agent accepts it). The spec must give the same
#: verdict for every case; test_the_upstream_agent_gives_these_verdicts proves the third column
#: against the upstream itself wherever torch is installed.
QUESTION_CASES: list[tuple[str, Any, bool]] = [
    (
        "choice-dict",
        {"type": "choice", "instructions": "x", "criteria": {"a": "A", "b": None}},
        True,
    ),
    (
        "choice-dict-structured-descriptions",
        {"type": "choice", "instructions": "x", "criteria": {"a": {"hint": "A"}, "b": 3}},
        True,
    ),
    ("choice-list", {"type": "choice", "instructions": "x", "criteria": ["a", "b"]}, True),
    ("choice-single", {"type": "choice", "instructions": "x", "criteria": ["only"]}, True),
    ("choice-number-labels", {"type": "choice", "instructions": "x", "criteria": [1, 2]}, True),
    (
        "choice-bool-labels",
        {"type": "choice", "instructions": "x", "criteria": [True, False]},
        True,
    ),
    ("choice-empty-label", {"type": "choice", "instructions": "x", "criteria": ["", "b"]}, True),
    (
        "choice-order",
        {"type": "choice", "instructions": "x", "criteria": ["a", "b"], "option_order": [1, 0]},
        True,
    ),
    (
        "choice-order-tuple",
        {"type": "choice", "instructions": "x", "criteria": ["a", "b"], "option_order": (1, 0)},
        True,
    ),
    ("score", {"type": "score", "instructions": "x", "criteria": ["low", "high"]}, True),
    ("score-empty-level", {"type": "score", "instructions": "x", "criteria": ["", "b"]}, True),
    ("score-number-levels", {"type": "score", "instructions": "x", "criteria": [1, 2, 3]}, True),
    ("score-object-level", {"type": "score", "instructions": "x", "criteria": [{"n": 0}]}, True),
    ("noul", {"type": "noul", "instructions": "x"}, True),
    (
        "noul-criteria",
        {"type": "noul", "instructions": "x", "criteria": {"true": "y", "false": "n"}},
        True,
    ),
    ("noul-bool-keys", {"type": "noul", "instructions": "x", "criteria": {True: "y"}}, True),
    (
        "noul-any-case-keys",
        {"type": "noul", "instructions": "x", "criteria": {"True": "y", "FALSE": "n"}},
        True,
    ),
    (
        "noul-colliding-keys",
        {"type": "noul", "instructions": "x", "criteria": {"true": "a", True: "b"}},
        True,
    ),
    ("noul-empty-criteria", {"type": "noul", "instructions": "x", "criteria": {}}, True),
    (
        "noul-labels",
        {"type": "noul", "instructions": "x", "labels": {"false": "B", "true": "A"}},
        True,
    ),
    ("noul-order", {"type": "noul", "instructions": "x", "option_order": [1, 0]}, True),
    ("dict-instructions", {"type": "noul", "instructions": {"text": "x"}}, True),
    ("list-instructions", {"type": "noul", "instructions": ["x", "y"]}, True),
    ("int-instructions", {"type": "noul", "instructions": 3}, True),
    ("float-instructions", {"type": "noul", "instructions": 1.5}, True),
    ("bool-instructions", {"type": "noul", "instructions": True}, True),
    ("unknown-keys-ignored", {"type": "noul", "instructions": "x", "notes": "n"}, True),
    ("not-a-dict", "noul", False),
    ("unknown-type", {"type": "rating", "instructions": "x", "criteria": ["a"]}, False),
    ("no-type", {"instructions": "x"}, False),
    ("type-not-text", {"type": 1, "instructions": "x"}, False),
    ("no-instructions", {"type": "noul"}, False),
    ("null-instructions", {"type": "noul", "instructions": None}, False),
    ("blank-instructions", {"type": "noul", "instructions": "   "}, False),
    ("empty-list-instructions", {"type": "noul", "instructions": []}, False),
    ("empty-dict-instructions", {"type": "noul", "instructions": {}}, False),
    ("set-instructions", {"type": "noul", "instructions": {"x"}}, False),
    ("choice-no-criteria", {"type": "choice", "instructions": "x"}, False),
    ("choice-empty-dict", {"type": "choice", "instructions": "x", "criteria": {}}, False),
    ("choice-empty-list", {"type": "choice", "instructions": "x", "criteria": []}, False),
    ("choice-string", {"type": "choice", "instructions": "x", "criteria": "a,b"}, False),
    ("choice-tuple", {"type": "choice", "instructions": "x", "criteria": ("a", "b")}, False),
    ("choice-null-label", {"type": "choice", "instructions": "x", "criteria": ["a", None]}, False),
    (
        "choice-null-key",
        {"type": "choice", "instructions": "x", "criteria": {None: "x", "a": None}},
        False,
    ),
    ("choice-list-label", {"type": "choice", "instructions": "x", "criteria": [["a"]]}, False),
    ("choice-duplicate", {"type": "choice", "instructions": "x", "criteria": ["a", "a"]}, False),
    ("choice-1-and-1.0", {"type": "choice", "instructions": "x", "criteria": [1, 1.0]}, False),
    ("choice-1-and-True", {"type": "choice", "instructions": "x", "criteria": [1, True]}, False),
    ("score-dict", {"type": "score", "instructions": "x", "criteria": {"0": "low"}}, False),
    ("score-null-level", {"type": "score", "instructions": "x", "criteria": ["a", None]}, False),
    ("score-empty", {"type": "score", "instructions": "x", "criteria": []}, False),
    ("score-no-criteria", {"type": "score", "instructions": "x"}, False),
    ("noul-other-key", {"type": "noul", "instructions": "x", "criteria": {"maybe": "?"}}, False),
    ("noul-list", {"type": "noul", "instructions": "x", "criteria": ["y", "n"]}, False),
    (
        "labels-on-choice",
        {
            "type": "choice",
            "instructions": "x",
            "criteria": ["a"],
            "labels": {"false": "B", "true": "A"},
        },
        False,
    ),
    (
        "labels-on-score",
        {"type": "score", "instructions": "x", "criteria": ["a"], "labels": {}},
        False,
    ),
    ("labels-incomplete", {"type": "noul", "instructions": "x", "labels": {"true": "A"}}, False),
    (
        "labels-equal",
        {"type": "noul", "instructions": "x", "labels": {"false": "A", "true": " A "}},
        False,
    ),
    (
        "labels-not-text",
        {"type": "noul", "instructions": "x", "labels": {"false": 0, "true": 1}},
        False,
    ),
    (
        "labels-blank",
        {"type": "noul", "instructions": "x", "labels": {"false": " ", "true": "A"}},
        False,
    ),
    (
        "order-repeats",
        {"type": "choice", "instructions": "x", "criteria": ["a", "b"], "option_order": [0, 0]},
        False,
    ),
    (
        "order-short",
        {"type": "score", "instructions": "x", "criteria": ["a", "b"], "option_order": [0]},
        False,
    ),
    (
        "order-null",
        {"type": "choice", "instructions": "x", "criteria": ["a", "b"], "option_order": None},
        False,
    ),
    (
        "order-text",
        {"type": "choice", "instructions": "x", "criteria": ["a", "b"], "option_order": "10"},
        False,
    ),
    (
        "order-bools",
        {
            "type": "choice",
            "instructions": "x",
            "criteria": ["a", "b"],
            "option_order": [False, True],
        },
        False,
    ),
    (
        "order-floats",
        {
            "type": "choice",
            "instructions": "x",
            "criteria": ["a", "b"],
            "option_order": [1.0, 0.0],
        },
        False,
    ),
    ("noul-order-three", {"type": "noul", "instructions": "x", "option_order": [0, 1, 2]}, False),
]

#: (question id, whether the pinned upstream Agent accepts it)
QUESTION_ID_CASES: list[tuple[Any, bool]] = [
    ("q", True),
    ("needs human?", True),
    (7, True),
    ("", False),
    ("   ", False),
    (None, False),
    (1.5, False),
    (("a",), False),
]
VALID = {"type": "noul", "instructions": "x"}


def _spec_accepts(question_id: Any, definition: Any) -> bool:
    try:
        parse_decision_spec(_spec(questions={question_id: definition}))
    except DecisionSpecError:
        return False
    return True


def _upstream_accepts(question_id: Any, definition: Any) -> bool:
    try:
        check_question(question_id, definition)
    except ValueError:
        return False
    return True


@pytest.mark.parametrize(
    ("definition", "accepted"),
    [(definition, accepted) for _, definition, accepted in QUESTION_CASES],
    ids=[name for name, *_ in QUESTION_CASES],
)
def test_the_spec_accepts_a_question_exactly_when_the_upstream_does(
    definition: Any, accepted: bool
) -> None:
    assert _spec_accepts("q", definition) is accepted


@pytest.mark.parametrize(("question_id", "accepted"), QUESTION_ID_CASES, ids=repr)
def test_the_spec_accepts_a_question_id_exactly_when_the_upstream_does(
    question_id: Any, accepted: bool
) -> None:
    assert _spec_accepts(question_id, VALID) is accepted


@pytest.mark.torch
@pytest.mark.parametrize(
    ("definition", "accepted"),
    [(definition, accepted) for _, definition, accepted in QUESTION_CASES],
    ids=[name for name, *_ in QUESTION_CASES],
)
def test_the_upstream_agent_gives_these_verdicts(definition: Any, accepted: bool) -> None:
    assert _upstream_accepts("q", definition) is accepted


@pytest.mark.torch
@pytest.mark.parametrize(("question_id", "accepted"), QUESTION_ID_CASES, ids=repr)
def test_the_upstream_agent_gives_these_id_verdicts(question_id: Any, accepted: bool) -> None:
    assert _upstream_accepts(question_id, VALID) is accepted


def test_the_cases_cover_both_verdicts() -> None:
    verdicts = [accepted for *_, accepted in QUESTION_CASES]
    assert verdicts.count(True) >= 20
    assert verdicts.count(False) >= 20
