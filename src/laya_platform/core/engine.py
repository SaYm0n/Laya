"""The DecisionEngine protocol: the one interface every System-1 backend is used through.

Semantics are the upstream's (``laya.Router``):

* ``predict`` answers every question about one state and returns the upstream payload unchanged.
* ``predict_batch`` takes heterogeneous requests (each with its own state, questions and
  per-request controls) and returns one payload per request, in input order.
* ``route`` says which checkpoint would answer, without running a forward pass.

Adapters never rewrite a payload: no confidence bands, no business rules, no change to
``confidence`` or ``answer_confidence``. An adapter that cannot offer an operation or honour a
control raises (:class:`~laya_platform.core.errors.UnsupportedOperationError`,
:class:`~laya_platform.core.errors.UnsupportedControlError`) instead of degrading silently.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, Unpack, runtime_checkable

from laya_platform.core.types import (
    BatchControls,
    DecisionPayload,
    DecisionRequest,
    PredictControls,
    Questions,
    RouteHints,
    RoutePayload,
    State,
)


@runtime_checkable
class DecisionEngine(Protocol):
    def predict(
        self, state: State, questions: Questions, **controls: Unpack[PredictControls]
    ) -> DecisionPayload:
        """Answer ``questions`` about ``state``; the payload is the upstream's, unchanged."""
        ...

    def predict_batch(
        self, requests: Sequence[DecisionRequest], **controls: Unpack[BatchControls]
    ) -> list[DecisionPayload]:
        """One payload per request, in input order."""
        ...

    def route(
        self, state: State, questions: Questions | None = None, **hints: Unpack[RouteHints]
    ) -> RoutePayload:
        """The routing decision for ``state``, without running a forward pass."""
        ...
