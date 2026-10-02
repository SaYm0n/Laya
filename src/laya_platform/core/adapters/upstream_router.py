"""UpstreamRouterEngine: a thin DecisionEngine over ``laya.Router``.

Routing (script and language detection, ``task``, typed-decisions workflows), checkpoint loading,
batching and the confidence gate are all the upstream's; this adapter only forwards calls and
returns the upstream results as they are, ``routing`` block included.

The Router's defaults are kept: nothing here prefers a checkpoint. For mostly-Portuguese traffic the
recommended configuration (docs/ARCHITECTURE_PROPOSAL.md §6.1) is passed by the caller::

    UpstreamRouterEngine.create(default="multilingual", lang_guess=my_language_detector)
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Self, TypedDict, Unpack

from laya import Router

from laya_platform.core.types import (
    BatchControls,
    DecisionPayload,
    DecisionRequest,
    LangGuess,
    PredictControls,
    Questions,
    RouteHints,
    RoutePayload,
    State,
)


class RouterOptions(TypedDict, total=False):
    """Keyword arguments of ``laya.Router`` (frozen against the upstream signature in tests)."""

    models: dict[str, Any]
    device: str | None
    token: str | None
    revision: str | None
    revisions: dict[str, str | None]
    max_loaded: int
    default: str
    auto_task_detection: bool
    standalone_repos: bool
    preload: bool
    lang_guess: LangGuess | None
    hooks: Any
    on_predict_start: Callable[..., Any] | None
    on_predict_end: Callable[..., Any] | None
    hooks_raise: bool
    hooks_concurrent: bool
    hooks_timeout: float | None
    agent_kwargs: dict[str, Any]
    sha256_digests: dict[str, dict[str, str] | None]


class UpstreamRouterEngine:
    def __init__(self, router: Router) -> None:
        self._router = router

    @classmethod
    def create(cls, **options: Unpack[RouterOptions]) -> Self:
        """Build a ``laya.Router`` with exactly these options (the upstream defaults otherwise).

        Nothing is downloaded unless ``preload=True``; checkpoints load on first use.
        """
        return cls(Router(**options))

    @property
    def router(self) -> Router:
        """The wrapped ``laya.Router`` (for preload, attach, unload and hooks)."""
        return self._router

    def predict(
        self, state: State, questions: Questions, **controls: Unpack[PredictControls]
    ) -> DecisionPayload:
        payload: DecisionPayload = self._router.predict(state, questions, **controls)
        return payload

    def predict_batch(
        self, requests: Sequence[DecisionRequest], **controls: Unpack[BatchControls]
    ) -> list[DecisionPayload]:
        payloads: list[DecisionPayload] = self._router.predict_batch(requests, **controls)
        return payloads

    def route(
        self, state: State, questions: Questions | None = None, **hints: Unpack[RouteHints]
    ) -> RoutePayload:
        decision: RoutePayload = self._router.route(state, questions, **hints)
        return decision
