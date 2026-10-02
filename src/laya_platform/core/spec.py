"""DecisionSpec: the versioned, validated declaration of one decision.

A spec says what is asked (a JSON schema, converted by ``laya.structured``, or questions in the
upstream format), which languages it is written for, and the minimum engine configuration of this
phase (an optional upstream checkpoint). It is loaded from YAML or JSON::

    id: support.ticket_triage
    version: 3
    schema:
      type: object
      properties:
        department: {type: string, enum: [billing, technical], description: "Qual equipe?"}
        urgency: {type: integer, minimum: 0, maximum: 4}
        churn_risk: {type: boolean, description: "O cliente ameaça cancelar?"}
    languages: [pt, en]
    engine:
      checkpoint: multilingual

Three kinds of rule apply, and only these:

1. **The upstream contract** decides what may be asked. A ``schema`` is valid exactly when
   ``laya.structured.questions_from_json_schema`` accepts it, and it is converted by that function
   (a property without ``description`` gets the upstream's generic instruction, as in the
   upstream). ``questions`` are valid exactly when the pinned ``Agent._check_question`` accepts them
   (mirrored below, because the upstream check lives in a torch-importing module; the parity is
   asserted against the upstream itself in tests/compatibility/test_schema_contract.py) and are
   handed to the engine unchanged.
2. **Configuration safety** of the file: YAML 1.2 booleans (``no`` stays the string "no", e.g.
   the Norwegian language code), duplicate keys refused in YAML and JSON, no ``NaN``/``Infinity``,
   no Python object tags.
3. **The platform's own envelope** (this phase's scope): ``id``, ``version``, ``languages`` and
   ``engine.checkpoint`` (a name the upstream Router accepts); keys of later phases -- confidence
   bands and ``calibration_ref`` (F4/F6), risk (F6), integration mode (F3), specialists (F7) -- are
   refused with the phase instead of being accepted and ignored.

Recommended, never enforced: a ``description`` on every schema property (docs/UPSTREAM_ANALYSIS.md
§3.9 shows the generic instruction is a poor question).
"""

from __future__ import annotations

import copy
import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any, Self

import yaml
from laya.router import normalise_name
from laya.structured import SchemaError, questions_from_json_schema
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from laya_platform.core.types import PredictControls, Questions

ID_PATTERN = r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$"
LANGUAGE_PATTERN = r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$"
SUPPORTED_SUFFIXES = (".yaml", ".yml", ".json")

#: Keys of the documented DecisionSpec format (ARCHITECTURE_PROPOSAL.md §6.2) that belong to later
#: phases. Refused with the reason, so a spec written ahead of time fails loudly.
NOT_YET_SUPPORTED = {
    "policy": "confidence bands belong to the DecisionPolicy (F6), derived from calibration (F4)",
    "calibration_ref": "calibration references arrive with calibration (F4)",
    "risk": "risk levels are enforced by the DecisionPolicy (F6)",
    "mode": "integration modes (offline/shadow/advisory/gated/production) arrive in F3",
}
ENGINE_NOT_YET_SUPPORTED = {
    "specialist": "specialists are registered and selected from F7 (SpecialistRegistry)",
    "fallback_checkpoint": "a fallback exists only once specialists do (F7); use 'checkpoint'",
}


class DecisionSpecError(ValueError):
    """A DecisionSpec could not be read or is invalid; the message names the file and the field."""


LanguageCode = Annotated[StrictStr, StringConstraints(pattern=LANGUAGE_PATTERN)]

#: Question types of the pinned upstream (``laya.common.QTYPES``, frozen in the contract tests).
QUESTION_TYPES = ("choice", "score", "noul")


def _refuse_not_yet(data: Any, reasons: Mapping[str, str], where: str) -> None:
    if isinstance(data, Mapping):
        for key, reason in reasons.items():
            if key in data:
                raise ValueError(f"'{where}{key}' is not supported yet: {reason}")


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ---------------------------------------------------- questions: the upstream's rules, mirrored
def validate_question(question_id: Any, definition: Any) -> None:
    """Accept or reject one question exactly as the pinned ``laya.agent.Agent._check_question``.

    Same rules, in the same order, so a definition is valid here if and only if the upstream agent
    accepts it; nothing is added. Kept in step by tests/compatibility/test_schema_contract.py, which
    runs every case through the upstream validator too wherever torch is installed.
    """
    if question_id is None:
        raise ValueError("question id must not be None")
    if not isinstance(question_id, str | int) or (
        isinstance(question_id, str) and not question_id.strip()
    ):
        raise ValueError(f"question id must be a non-empty string, got {question_id!r}")
    name = f"question {question_id!r}"
    if not isinstance(definition, dict):
        raise ValueError(f"{name}: definition must be a dict, got {type(definition).__name__}")
    kind = definition.get("type")
    if not isinstance(kind, str) or kind not in QUESTION_TYPES:
        raise ValueError(f"{name}: unknown type {kind!r}; use one of {sorted(QUESTION_TYPES)}")
    if "instructions" not in definition:
        raise ValueError(f"{name}: no 'instructions'; add the text the model should answer")
    instructions = definition["instructions"]
    if (
        instructions is None
        or (isinstance(instructions, str) and not instructions.strip())
        or (isinstance(instructions, list | dict) and not instructions)
        or not isinstance(instructions, str | dict | list | int | float)
    ):
        raise ValueError(
            f"{name}: 'instructions' must be non-blank text, a non-empty object or list, "
            f"or a number; got {instructions!r}"
        )
    criteria = definition.get("criteria")
    if kind == "choice":
        _check_choice_criteria(name, criteria)
    elif kind == "score":
        if not isinstance(criteria, list) or not criteria:
            raise ValueError(
                f"{name}: a score question takes 'criteria' as a non-empty list of levels"
            )
        if None in criteria:
            raise ValueError(f"{name}: score level {criteria.index(None)} is null")
    elif criteria is not None and (
        not isinstance(criteria, dict)
        or not {str(k).lower() for k in criteria} <= {"true", "false"}
    ):
        raise ValueError(
            f"{name}: a noul question takes 'criteria' keyed only 'true'/'false', or none"
        )
    if "option_order" in definition:
        # As the upstream's _option_count: noul is the pair [false, true], otherwise one per entry.
        if kind == "noul":
            count = 2
        else:
            count = len(criteria) if isinstance(criteria, dict | list | tuple) else 0
        order = definition["option_order"]
        if (
            not isinstance(order, list | tuple)
            or len(order) != count
            or sorted(i for i in order if isinstance(i, int) and not isinstance(i, bool))
            != list(range(count))
        ):
            raise ValueError(
                f"{name}: 'option_order' must be a permutation of range({count}), got {order!r}"
            )
    if "labels" in definition:
        if kind != "noul":
            raise ValueError(f"{name}: 'labels' is only supported for noul questions")
        _check_noul_labels(name, definition["labels"])


def _check_choice_criteria(name: str, criteria: Any) -> None:
    if not isinstance(criteria, dict | list):
        raise ValueError(
            f"{name}: a choice question takes 'criteria' as a dict of label -> description, "
            "or a list of labels"
        )
    if not criteria:
        raise ValueError(f"{name}: a choice question needs at least one criterion")
    for index, label in enumerate(criteria):
        if isinstance(label, list | dict | set | bytearray):
            raise ValueError(f"{name}: choice label {index} must be a scalar, got {label!r}")
        if label is None:
            raise ValueError(f"{name}: choice label {index} is null")
    if isinstance(criteria, list):
        seen: dict[Any, int] = {}
        for index, label in enumerate(criteria):
            try:
                first = seen.setdefault(label, index)
            except TypeError:
                raise ValueError(
                    f"{name}: choice label {index} ({label!r}) is unhashable"
                ) from None
            if first != index:
                raise ValueError(
                    f"{name}: choice label {index} ({label!r}) repeats label {first} "
                    "(1, 1.0 and True are one key)"
                )


def _check_noul_labels(name: str, labels: Any) -> None:
    problem = (
        f"{name}: noul labels must map exactly 'false' and 'true' to distinct non-empty strings"
    )
    if not isinstance(labels, dict) or set(labels) != {"false", "true"}:
        raise ValueError(problem)
    texts = [labels["false"], labels["true"]]
    if not all(isinstance(text, str) for text in texts):
        raise ValueError(problem)
    false_text, true_text = (text.strip() for text in texts)
    if not false_text or not true_text or false_text == true_text:
        raise ValueError(problem)


class EngineSettings(_Frozen):
    """Engine configuration of this phase: an optional upstream checkpoint.

    ``checkpoint`` is a ``laya.Router`` checkpoint name or alias (``english``, ``multilingual``,
    ``typed-decisions``, ``ml``...), stored under its canonical name. Unset means automatic
    routing. It becomes the ``model`` control of a prediction
    (:meth:`DecisionSpec.predict_controls`).
    """

    checkpoint: StrictStr | None = None

    @model_validator(mode="before")
    @classmethod
    def _not_yet(cls, data: Any) -> Any:
        _refuse_not_yet(data, ENGINE_NOT_YET_SUPPORTED, "engine.")
        return data

    @field_validator("checkpoint")
    @classmethod
    def _known_checkpoint(cls, value: str | None) -> str | None:
        return None if value is None else str(normalise_name(value))


class DecisionSpec(_Frozen):
    id: Annotated[StrictStr, StringConstraints(pattern=ID_PATTERN, max_length=128)]
    version: Annotated[StrictInt, Field(ge=1)]
    json_schema: dict[StrictStr, Any] | None = Field(default=None, alias="schema")
    questions: dict[Any, Any] | None = None
    languages: Annotated[tuple[LanguageCode, ...], Field(min_length=1)]
    engine: EngineSettings = Field(default_factory=EngineSettings)

    @model_validator(mode="before")
    @classmethod
    def _not_yet(cls, data: Any) -> Any:
        _refuse_not_yet(data, NOT_YET_SUPPORTED, "")
        return data

    @field_validator("questions")
    @classmethod
    def _questions_the_upstream_accepts(cls, value: dict[Any, Any] | None) -> dict[Any, Any] | None:
        for question_id, definition in (value or {}).items():
            validate_question(question_id, definition)
        return value

    @field_validator("languages")
    @classmethod
    def _unique_languages(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("languages must not repeat")
        return value

    @model_validator(mode="after")
    def _one_source_that_the_upstream_accepts(self) -> Self:
        if (self.json_schema is None) == (self.questions is None):
            raise ValueError("pass exactly one of 'schema' or 'questions'")
        if self.json_schema is not None:
            try:
                questions_from_json_schema(self.json_schema)
            except SchemaError as exc:
                raise ValueError(f"schema is not supported by laya.structured: {exc}") from None
        return self

    def to_questions(self) -> Questions:
        """The upstream questions this spec asks (a fresh copy on every call).

        ``questions`` come back exactly as written; a ``schema`` goes through
        ``laya.structured.questions_from_json_schema``.
        """
        if self.questions is not None:
            return copy.deepcopy(self.questions)
        questions: Questions = questions_from_json_schema(self.json_schema)
        return questions

    def predict_controls(self) -> PredictControls:
        """Controls implied by the spec's engine settings (``model`` when a checkpoint is set)."""
        controls: PredictControls = {}
        if self.engine.checkpoint is not None:
            controls["model"] = self.engine.checkpoint
        return controls


# ------------------------------------------------------------------------------------------ loading
class _SpecYamlLoader(yaml.SafeLoader):
    """``yaml.SafeLoader`` with YAML 1.2 booleans and duplicate keys refused."""


_SpecYamlLoader.yaml_implicit_resolvers = {
    first: [(tag, regexp) for tag, regexp in resolvers if tag != "tag:yaml.org,2002:bool"]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_SpecYamlLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF")
)


def _construct_mapping(loader: _SpecYamlLoader, node: yaml.MappingNode) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        try:
            duplicate = key in mapping
        except TypeError:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable key",
                key_node.start_mark,
            ) from None
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=True)
    return mapping


_SpecYamlLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    for key, value in pairs:
        if key in mapping:
            raise ValueError(f"duplicate key {key!r}")
        mapping[key] = value
    return mapping


def _refuse_constant(name: str) -> Any:
    raise ValueError(f"{name} is not valid JSON")


def _format_validation_error(error: ValidationError) -> str:
    lines = []
    for item in error.errors(include_url=False):
        location = ".".join(str(part) for part in item["loc"]) or "<spec>"
        lines.append(f"  - {location}: {item['msg']}")
    return "\n".join(lines)


def parse_decision_spec(data: Any, *, source: str = "<data>") -> DecisionSpec:
    """Validate already-parsed data (a mapping) as a DecisionSpec."""
    if not isinstance(data, Mapping):
        raise DecisionSpecError(
            f"{source}: a DecisionSpec must be a mapping, got {type(data).__name__}"
        )
    try:
        return DecisionSpec.model_validate(dict(data))
    except ValidationError as exc:
        details = _format_validation_error(exc)
        raise DecisionSpecError(f"{source}: invalid DecisionSpec\n{details}") from None


def load_decision_spec(path: str | os.PathLike[str]) -> DecisionSpec:
    """Read a DecisionSpec from a ``.yaml``/``.yml`` or ``.json`` file."""
    file = Path(path)
    suffix = file.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise DecisionSpecError(
            f"{file}: unsupported DecisionSpec format {suffix!r}; use one of {SUPPORTED_SUFFIXES}"
        )
    text = file.read_text(encoding="utf-8-sig")  # tolerate the BOM some Windows editors write
    try:
        if suffix == ".json":
            data = json.loads(text, object_pairs_hook=_json_object, parse_constant=_refuse_constant)
        else:
            data = yaml.load(text, Loader=_SpecYamlLoader)  # noqa: S506 -- a SafeLoader subclass
    except (ValueError, yaml.YAMLError) as exc:
        raise DecisionSpecError(f"{file}: cannot parse {suffix[1:].upper()}: {exc}") from None
    return parse_decision_spec(data, source=str(file))
