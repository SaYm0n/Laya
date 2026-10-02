"""Typed shapes of what flows through a DecisionEngine.

These are ``TypedDict`` descriptions of the upstream ``laya`` payloads, not models that parse or
rebuild them: at runtime every value is the plain ``dict`` the upstream (or a remote
``/v1/systemone`` server) returned, unchanged. ``total=False`` keys are the ones the upstream emits
only in some cases (``low_confidence`` and ``abstention`` only when ``min_confidence`` is sent,
``usage.options`` only when options collapsed, ``routing`` only from a Router).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, Required, TypeAlias, TypedDict

#: A state as the upstream accepts it: text, a JSON object, or a conversation (list of turns).
State: TypeAlias = str | dict[str, Any] | list[Any]

#: Question id -> question definition, in the upstream format
#: (``{"type": "choice"|"score"|"noul", "instructions": ..., "criteria": ...}``).
Questions: TypeAlias = dict[str, dict[str, Any]]

#: A language hint: a code (``"pt"``, ``"en-US"``) or a callable taking the state and returning a
#: code, or None to abstain. Same meaning as ``laya.Router(lang_guess=...)``.
LangGuess: TypeAlias = str | Callable[[State], str | None]

QuestionType: TypeAlias = Literal["choice", "score", "noul"]
GateState: TypeAlias = Literal["passed", "abstained", "unevaluated"]


class PredictControls(TypedDict, total=False):
    """Per-call controls of ``predict``; the names and meanings are the upstream's."""

    model: str
    task: str
    lang: str
    lang_guess: LangGuess
    max_len: int
    head_max_len: int
    min_confidence: float


class BatchControls(TypedDict, total=False):
    """Call-level controls of ``predict_batch``; per-request controls travel in each request."""

    batch_size: int
    min_confidence: float
    sort_by_length: bool


class RouteHints(TypedDict, total=False):
    """Routing hints of ``route``; same precedence as ``laya.Router.route``."""

    model: str
    task: str
    lang: str
    lang_guess: LangGuess


class DecisionRequest(TypedDict, total=False):
    """One item of ``predict_batch`` (the ``laya.Router.predict_batch`` request shape)."""

    state: Required[State]
    questions: Required[Questions]
    model: str
    task: str
    lang: str
    lang_guess: LangGuess
    max_len: int
    head_max_len: int


class AnswerPayload(TypedDict, total=False):
    """One answer of a decision payload.

    ``answer_confidence`` is ``max(p)``: the quantity temperature scaling fits and the only one a
    gate may compare against. ``confidence`` is normalized entropy for ``choice``/``score``; it is
    not calibrated and must never stand in for ``answer_confidence``.
    """

    type: Required[QuestionType]
    choice: str | int | float | bool
    score: float
    legend: dict[str, str]
    noul: float
    probabilities: dict[str, float]
    confidence: float
    answer_confidence: float
    action: dict[str, float]
    low_confidence: bool
    abstention: GateState
    abstention_threshold: float


class UsagePayload(TypedDict, total=False):
    input_tokens: Required[int]
    output_tokens: Required[int]
    state_tokens: int
    state_tokens_dropped: int
    truncated: bool
    truncated_questions: list[str]
    options: dict[str, dict[str, int]]
    windows: int


class RoutePayload(TypedDict):
    """A routing decision (``laya.RouteDecision`` is a ``dict`` with exactly these keys)."""

    model: str
    repo: str | None
    reason: str
    detection: dict[str, Any] | None
    workflow: str | None


class DecisionPayload(TypedDict, total=False):
    """A decision as the upstream returns it from ``predict`` / ``system_one``."""

    model: Required[str]
    answers: Required[dict[str, AnswerPayload]]
    usage: Required[UsagePayload]
    routing: RoutePayload
