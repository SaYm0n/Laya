"""DecisionSpec rules that are not about what may be asked.

What a schema or a question may contain is the upstream's decision, tested in
tests/compatibility/test_schema_contract.py (the spec accepts exactly what the upstream accepts).
This module covers the two other kinds of rule, and only these:

* the platform's own envelope, as scoped for F2: ``id``, ``version``, ``languages``,
  ``engine.checkpoint``, exactly one of ``schema``/``questions``, keys of later phases refused;
* configuration safety of the file: YAML 1.2 booleans, duplicate keys, NaN, Python tags.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from laya_platform.core import (
    DecisionSpec,
    DecisionSpecError,
    load_decision_spec,
    parse_decision_spec,
)

SCHEMA = {
    "type": "object",
    "properties": {
        "department": {
            "type": "string",
            "enum": ["billing", "technical"],
            "description": "Qual equipe?",
        },
        "urgency": {"type": "integer", "minimum": 0, "maximum": 4, "description": "Quão urgente?"},
        "churn_risk": {"type": "boolean", "description": "Ameaça cancelar?"},
    },
}
QUESTIONS = {"flag": {"type": "noul", "instructions": "Is it?"}}

YAML_SPEC = """\
id: support.ticket_triage
version: 3
schema:
  type: object
  properties:
    department: {type: string, enum: [billing, technical], description: "Qual equipe?"}
    urgency: {type: integer, minimum: 0, maximum: 4, description: "Quão urgente?"}
    churn_risk: {type: boolean, description: "Ameaça cancelar?"}
languages: [pt, en]
engine:
  checkpoint: ml
"""


def _spec(**overrides: Any) -> dict[str, Any]:
    return {"id": "support.triage", "version": 1, "languages": ["pt"], "schema": SCHEMA} | overrides


def _error(data: Any) -> str:
    with pytest.raises(DecisionSpecError) as error:
        parse_decision_spec(data)
    return str(error.value)


# ============================================================== the platform's envelope (F2 scope)
def test_a_minimal_spec() -> None:
    spec = parse_decision_spec(_spec())
    assert (spec.id, spec.version, spec.languages) == ("support.triage", 1, ("pt",))
    assert spec.engine.checkpoint is None
    assert spec.predict_controls() == {}
    assert list(spec.to_questions()) == ["department", "urgency", "churn_risk"]


def test_a_spec_is_immutable() -> None:
    spec = parse_decision_spec(_spec())
    with pytest.raises(ValueError, match="frozen"):
        spec.version = 2


@pytest.mark.parametrize("identifier", ["support.triage", "a", "a1.b_2.c", "billing"])
def test_valid_ids(identifier: str) -> None:
    assert parse_decision_spec(_spec(id=identifier)).id == identifier


@pytest.mark.parametrize(
    "identifier", ["", "Support.triage", "1abc", "a..b", "a.", "a-b", "a b", "x" * 129, 7]
)
def test_invalid_ids(identifier: Any) -> None:
    assert "id:" in _error(_spec(id=identifier))


@pytest.mark.parametrize("version", [0, -1, "3", 1.0, True, None])
def test_invalid_versions(version: Any) -> None:
    assert "version:" in _error(_spec(version=version))


@pytest.mark.parametrize("languages", [["pt"], ["pt", "en"], ["pt-BR", "es-419", "en-US"], ["por"]])
def test_valid_languages(languages: list[str]) -> None:
    assert parse_decision_spec(_spec(languages=languages)).languages == tuple(languages)


@pytest.mark.parametrize(
    "languages", [[], ["PT"], ["portuguese"], ["pt_BR"], [""], ["pt", "pt"], "pt", [False]]
)
def test_invalid_languages(languages: Any) -> None:
    assert "languages" in _error(_spec(languages=languages))


def test_exactly_one_source() -> None:
    assert "exactly one of 'schema' or 'questions'" in _error(_spec(questions=QUESTIONS))
    assert "exactly one of 'schema' or 'questions'" in _error(
        {"id": "a", "version": 1, "languages": ["pt"]}
    )
    spec = parse_decision_spec(_spec(schema=None, questions=QUESTIONS))
    assert spec.to_questions() == QUESTIONS


def test_the_schema_key_is_schema() -> None:
    message = _error({"id": "a", "version": 1, "languages": ["pt"], "json_schema": SCHEMA})
    assert "json_schema: Extra inputs are not permitted" in message


@pytest.mark.parametrize("unknown", ["description", "owner", "bands", "tags"])
def test_unknown_keys_are_refused(unknown: str) -> None:
    assert f"{unknown}: Extra inputs are not permitted" in _error(_spec(**{unknown: "x"}))


@pytest.mark.parametrize(
    ("key", "phase"),
    [("policy", "F6"), ("calibration_ref", "F4"), ("risk", "F6"), ("mode", "F3")],
)
def test_keys_of_later_phases_name_their_phase(key: str, phase: str) -> None:
    message = _error(_spec(**{key: "x"}))
    assert f"'{key}' is not supported yet" in message
    assert phase in message


@pytest.mark.parametrize(
    ("engine", "message"),
    [
        ({"specialist": "laya-support"}, "'engine.specialist' is not supported yet"),
        (
            {"fallback_checkpoint": "multilingual"},
            "'engine.fallback_checkpoint' is not supported yet",
        ),
        ({"checkpoint": "portuguese"}, "unknown model 'portuguese'"),
        ({"checkpoint": 3}, "engine.checkpoint: Input should be a valid string"),
        ({"device": "cuda"}, "engine.device: Extra inputs are not permitted"),
    ],
)
def test_engine_settings_errors(engine: dict[str, Any], message: str) -> None:
    assert message in _error(_spec(engine=engine))


@pytest.mark.parametrize(
    ("name", "canonical"),
    [("ml", "multilingual"), ("English", "english"), ("typed", "typed-decisions")],
)
def test_checkpoint_aliases_are_stored_canonically(name: str, canonical: str) -> None:
    spec = parse_decision_spec(_spec(engine={"checkpoint": name}))
    assert spec.engine.checkpoint == canonical
    assert spec.predict_controls() == {"model": canonical}


def test_a_spec_must_be_a_mapping() -> None:
    assert "must be a mapping, got list" in _error([_spec()])


def test_errors_name_every_field() -> None:
    message = _error({"id": "Bad", "version": 0, "languages": []})
    assert message.startswith("<data>: invalid DecisionSpec")
    for field in ("  - id:", "  - version:", "  - languages:"):
        assert field in message
    assert "errors.pydantic.dev" not in message


# ======================================================== configuration safety of the spec file
def test_yaml_and_json_load_the_same_spec(tmp_path: Path) -> None:
    yaml_file = tmp_path / "triage.yaml"
    yaml_file.write_text(YAML_SPEC, encoding="utf-8")
    json_file = tmp_path / "triage.json"
    json_file.write_text(
        json.dumps(
            {
                "id": "support.ticket_triage",
                "version": 3,
                "schema": SCHEMA,
                "languages": ["pt", "en"],
                "engine": {"checkpoint": "ml"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    from_yaml, from_json = load_decision_spec(yaml_file), load_decision_spec(json_file)
    assert from_yaml == from_json
    assert isinstance(from_yaml, DecisionSpec)
    assert from_yaml.predict_controls() == {"model": "multilingual"}


@pytest.mark.parametrize("suffix", [".yml", ".YAML"])
def test_yaml_suffixes(tmp_path: Path, suffix: str) -> None:
    path = tmp_path / f"spec{suffix}"
    path.write_text(YAML_SPEC, encoding="utf-8")
    assert load_decision_spec(path).id == "support.ticket_triage"


def test_a_byte_order_mark_is_tolerated(tmp_path: Path) -> None:
    path = tmp_path / "spec.yaml"
    path.write_bytes(b"\xef\xbb\xbf" + YAML_SPEC.encode("utf-8"))
    assert load_decision_spec(path).version == 3


@pytest.mark.parametrize("suffix", [".toml", ".txt", ""])
def test_unsupported_formats(tmp_path: Path, suffix: str) -> None:
    path = tmp_path / f"spec{suffix}"
    path.write_text(YAML_SPEC, encoding="utf-8")
    with pytest.raises(DecisionSpecError, match="unsupported DecisionSpec format"):
        load_decision_spec(path)


def test_errors_name_the_file(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text(YAML_SPEC.replace("version: 3", "version: three"), encoding="utf-8")
    with pytest.raises(
        DecisionSpecError, match=r"broken\.yaml: invalid DecisionSpec\n  - version:"
    ):
        load_decision_spec(path)


def test_yaml_booleans_are_yaml_1_2(tmp_path: Path) -> None:
    # YAML 1.1 reads `no`, `yes`, `on`, `off` as booleans: `no` is also Norwegian's language code,
    # and `enum: [yes, no]` would silently turn a choice into a noul.
    path = tmp_path / "spec.yaml"
    path.write_text(
        YAML_SPEC.replace("languages: [pt, en]", "languages: [no, pt]").replace(
            "enum: [billing, technical]", "enum: [yes, no, maybe]"
        ),
        encoding="utf-8",
    )
    spec = load_decision_spec(path)
    assert spec.languages == ("no", "pt")
    assert spec.to_questions()["department"]["criteria"] == {"yes": None, "no": None, "maybe": None}
    path.write_text(
        YAML_SPEC.replace("enum: [billing, technical]", "enum: [true, false]"), encoding="utf-8"
    )
    assert load_decision_spec(path).to_questions()["department"]["type"] == "noul"


@pytest.mark.parametrize(
    ("suffix", "text"),
    [
        (".yaml", YAML_SPEC + "version: 4\n"),
        (".json", '{"id": "a", "id": "b", "version": 1, "languages": ["pt"], "questions": {}}'),
    ],
)
def test_duplicate_keys_are_refused(tmp_path: Path, suffix: str, text: str) -> None:
    path = tmp_path / f"spec{suffix}"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(DecisionSpecError, match="duplicate key"):
        load_decision_spec(path)


@pytest.mark.parametrize(
    ("suffix", "text", "message"),
    [
        (".json", '{"id": "a", "version": NaN}', "NaN is not valid JSON"),
        (".json", "{not json", "cannot parse JSON"),
        (".yaml", "id: [unclosed", "cannot parse YAML"),
        (".yaml", "!!python/object:os.system {}", "cannot parse YAML"),
        (".yaml", "? [a, b]\n: 1\n", "found an unhashable key"),
    ],
)
def test_unparseable_files(tmp_path: Path, suffix: str, text: str, message: str) -> None:
    path = tmp_path / f"spec{suffix}"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(DecisionSpecError, match=message):
        load_decision_spec(path)


def test_an_empty_yaml_file_is_not_a_spec(tmp_path: Path) -> None:
    path = tmp_path / "spec.yaml"
    path.write_text("", encoding="utf-8")
    with pytest.raises(DecisionSpecError, match="must be a mapping, got NoneType"):
        load_decision_spec(path)
