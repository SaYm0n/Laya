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
        urgency: {type: integer, minimum: 0, maximum: 4, description: "Quão urgente é?"}
        churn_risk: {type: boolean, description: "O cliente ameaça cancelar?"}
    languages: [pt, en]
    engine:
      checkpoint: multilingual

What a spec does **not** carry yet, on purpose: confidence bands and ``calibration_ref`` (F4/F6),
risk (F6), integration mode (F3) and specialists (F7). Those keys are refused with a message naming
the phase, rather than accepted and ignored.

Rules beyond the upstream's, each because the upstream accepts something that fails silently:

* every schema property needs a ``description`` (without one the upstream asks a generic
  "What is `x`?", which docs/UPSTREAM_ANALYSIS.md §3.9 shows is a poor question);
* a ``questions`` spec must fit the upstream HTTP limits (``laya.serve``), so the same spec works
  in-process and over ``/v1/systemone``;
* YAML booleans are YAML 1.2 (``true``/``false`` only: ``no`` stays the string "no", e.g. the
  Norwegian language code) and duplicate keys are refused in YAML and JSON.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any, Literal, Self

import yaml
from laya.router import normalise_name
from laya.serve import MAX_CHOICE_OPTIONS, MAX_QUESTIONS, MAX_SCORE_LEVELS, MAX_TOTAL_OPTIONS
from laya.structured import SchemaError, questions_from_json_schema
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from laya_platform.core.types import PredictControls, Questions

ID_PATTERN = r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$"
QUESTION_ID_PATTERN = r"^[A-Za-z_][A-Za-z0-9_.-]*$"
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


def _not_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value


Text = Annotated[StrictStr, AfterValidator(_not_blank)]
QuestionId = Annotated[StrictStr, StringConstraints(pattern=QUESTION_ID_PATTERN, max_length=64)]
LanguageCode = Annotated[StrictStr, StringConstraints(pattern=LANGUAGE_PATTERN)]


def _refuse_not_yet(data: Any, reasons: Mapping[str, str], where: str) -> None:
    if isinstance(data, Mapping):
        for key, reason in reasons.items():
            if key in data:
                raise ValueError(f"'{where}{key}' is not supported yet: {reason}")


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Question(_Frozen):
    instructions: Text
    option_order: list[StrictInt] | None = None

    def option_count(self) -> int:
        raise NotImplementedError

    @model_validator(mode="after")
    def _option_order_is_a_permutation(self) -> Self:
        count = self.option_count()
        if self.option_order is not None and sorted(self.option_order) != list(range(count)):
            raise ValueError(
                f"option_order must be a permutation of range({count}), got {self.option_order}"
            )
        return self

    def to_upstream(self) -> dict[str, Any]:
        raise NotImplementedError

    def _with_order(self, question: dict[str, Any]) -> dict[str, Any]:
        if self.option_order is not None:
            question["option_order"] = list(self.option_order)
        return question


class ChoiceQuestion(_Question):
    """``criteria``: ``{label: description or null}`` or a list of labels, in display order."""

    type: Literal["choice"]
    criteria: dict[Text, StrictStr | None] | list[Text]

    @field_validator("criteria")
    @classmethod
    def _labels(cls, value: dict[str, str | None] | list[str]) -> dict[str, str | None] | list[str]:
        if not value:
            raise ValueError("a choice question needs at least one option")
        if isinstance(value, list) and len(set(value)) != len(value):
            raise ValueError("choice labels must be unique: they are the answer keys")
        return value

    def option_count(self) -> int:
        return len(self.criteria)

    def to_upstream(self) -> dict[str, Any]:
        criteria = dict(self.criteria) if isinstance(self.criteria, dict) else list(self.criteria)
        question = {"type": self.type, "instructions": self.instructions, "criteria": criteria}
        return self._with_order(question)


class ScoreQuestion(_Question):
    """``criteria``: level descriptions, index 0 first."""

    type: Literal["score"]
    criteria: list[Text]

    @field_validator("criteria")
    @classmethod
    def _levels(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("a score question needs at least one level")
        return value

    def option_count(self) -> int:
        return len(self.criteria)

    def to_upstream(self) -> dict[str, Any]:
        question = {
            "type": self.type,
            "instructions": self.instructions,
            "criteria": list(self.criteria),
        }
        return self._with_order(question)


class NoulLabels(_Frozen):
    """Model-facing texts of the two noul options; the answer is still P(true)."""

    false: Text
    true: Text

    @model_validator(mode="after")
    def _distinct(self) -> Self:
        if self.false.strip() == self.true.strip():
            raise ValueError("noul labels must be distinct")
        return self


class NoulQuestion(_Question):
    """``criteria`` (optional) describes the ``true``/``false`` options; ``labels`` renames them."""

    type: Literal["noul"]
    criteria: dict[Literal["true", "false"] | StrictBool, StrictStr | None] | None = None
    labels: NoulLabels | None = None

    @field_validator("criteria")
    @classmethod
    def _boolean_keys(
        cls, value: dict[str | bool, str | None] | None
    ) -> dict[str | bool, str | None] | None:
        # YAML reads `true:` as a boolean key; the upstream lower-cases str(key) the same way.
        if value is not None and len({str(key).lower() for key in value}) != len(value):
            raise ValueError("noul criteria name 'true' and 'false' at most once each")
        return value

    def option_count(self) -> int:
        return 2

    def to_upstream(self) -> dict[str, Any]:
        question: dict[str, Any] = {"type": self.type, "instructions": self.instructions}
        if self.criteria is not None:
            question["criteria"] = {str(key).lower(): text for key, text in self.criteria.items()}
        if self.labels is not None:
            question["labels"] = {"false": self.labels.false, "true": self.labels.true}
        return self._with_order(question)


QuestionSpec = Annotated[ChoiceQuestion | ScoreQuestion | NoulQuestion, Field(discriminator="type")]


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
    questions: dict[QuestionId, QuestionSpec] | None = None
    languages: Annotated[tuple[LanguageCode, ...], Field(min_length=1)]
    engine: EngineSettings = Field(default_factory=EngineSettings)

    @model_validator(mode="before")
    @classmethod
    def _not_yet(cls, data: Any) -> Any:
        _refuse_not_yet(data, NOT_YET_SUPPORTED, "")
        return data

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
            _check_schema(self.json_schema)
        if self.questions is not None:
            _check_wire_limits(self.questions)
        return self

    def to_questions(self) -> Questions:
        """The upstream questions this spec asks (a fresh dict on every call)."""
        if self.questions is not None:
            return {qid: question.to_upstream() for qid, question in self.questions.items()}
        questions: Questions = questions_from_json_schema(self.json_schema)
        return questions

    def predict_controls(self) -> PredictControls:
        """Controls implied by the spec's engine settings (``model`` when a checkpoint is set)."""
        controls: PredictControls = {}
        if self.engine.checkpoint is not None:
            controls["model"] = self.engine.checkpoint
        return controls


def _check_schema(schema: dict[str, Any]) -> None:
    try:
        questions_from_json_schema(schema)
    except SchemaError as exc:
        raise ValueError(f"schema is not supported by laya.structured: {exc}") from None
    undescribed = [
        name
        for name, prop in schema["properties"].items()
        if not (isinstance(prop.get("description"), str) and prop["description"].strip())
    ]
    if undescribed:
        raise ValueError(
            f"schema properties without a 'description': {undescribed}; the description is the "
            "question the model reads, and without one the upstream asks a generic one"
        )


def _check_wire_limits(
    questions: Mapping[str, ChoiceQuestion | ScoreQuestion | NoulQuestion],
) -> None:
    # Same rules and the same constants as laya.serve's request gate, so a valid spec is also a
    # valid /v1/systemone request (only choice and score options count towards the total there).
    if len(questions) > MAX_QUESTIONS:
        raise ValueError(f"too many questions ({len(questions)} > {MAX_QUESTIONS})")
    total = 0
    for qid, question in questions.items():
        if isinstance(question, ChoiceQuestion) and question.option_count() > MAX_CHOICE_OPTIONS:
            raise ValueError(
                f"too many choice options for {qid!r} "
                f"({question.option_count()} > {MAX_CHOICE_OPTIONS})"
            )
        if isinstance(question, ScoreQuestion) and question.option_count() > MAX_SCORE_LEVELS:
            raise ValueError(
                f"too many score levels for {qid!r} "
                f"({question.option_count()} > {MAX_SCORE_LEVELS})"
            )
        if not isinstance(question, NoulQuestion):
            total += question.option_count()
    if total > MAX_TOTAL_OPTIONS:
        raise ValueError(
            f"too many answer options across questions ({total} > {MAX_TOTAL_OPTIONS})"
        )


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
