"""AgentEngine: a DecisionEngine over one loaded checkpoint (``laya.Agent`` or a look-alike).

This is how an external checkpoint -- a fine-tuned specialist on the Hub or in a local directory --
is used before the SpecialistRegistry exists (F7): the caller builds or loads it, the engine wraps
it. There is nothing to route with one checkpoint, so ``route`` and the routing controls
(``model``, ``task``, ``lang_guess``) raise instead of being ignored.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol, Self, TypedDict, Unpack, runtime_checkable

from laya_platform.core.adapters._requests import check_requests, group_requests, refuse_routing
from laya_platform.core.errors import EngineError, MissingRuntimeError, UnsupportedOperationError
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
from laya_platform.core.upstream_compat import torch_available

#: Per-request fields a single checkpoint honours (``lang`` selects per-language temperatures).
ITEM_CONTROLS = ("lang", "max_len", "head_max_len")


@runtime_checkable
class AgentLike(Protocol):
    """The part of ``laya.Agent`` (and ``laya.onnx_agent.ONNXAgent``) an engine needs."""

    def system_one(self, state: State, questions: Questions, **kwargs: Any) -> Any: ...

    def predict_batch(self, states: list[State], questions: Questions, **kwargs: Any) -> Any: ...


class AgentOptions(TypedDict, total=False):
    """Keyword arguments of ``laya.load`` (frozen against the upstream signature in tests)."""

    device: str | None
    token: str | None
    subfolder: str | None
    fast: bool
    compile: bool
    revision: str | None
    expected_sha256: dict[str, str] | None
    lang_temperatures: dict[str, dict[str, Any]] | None
    hooks: Any
    on_predict_start: Callable[..., Any] | None
    on_predict_end: Callable[..., Any] | None
    hooks_raise: bool
    hooks_concurrent: bool
    hooks_timeout: float | None
    calibration: str | None


class SingleCheckpointEngine:
    """DecisionEngine over one agent-like object; payloads are returned as the agent made them."""

    def __init__(self, agent: AgentLike) -> None:
        if not isinstance(agent, AgentLike):
            raise TypeError(
                f"{type(self).__name__} needs an object with system_one() and predict_batch(), "
                f"got {type(agent).__name__}"
            )
        self._agent = agent

    @property
    def agent(self) -> AgentLike:
        return self._agent

    def predict(
        self, state: State, questions: Questions, **controls: Unpack[PredictControls]
    ) -> DecisionPayload:
        refuse_routing(controls, engine=type(self).__name__, where="control(s) ")
        payload: DecisionPayload = self._agent.system_one(state, questions, **controls)
        return payload

    def predict_batch(
        self, requests: Sequence[DecisionRequest], **controls: Unpack[BatchControls]
    ) -> list[DecisionPayload]:
        name = type(self).__name__
        check_requests(requests, allowed=ITEM_CONTROLS, engine=name)
        results: dict[int, DecisionPayload] = {}
        for indices, first in group_requests(requests, ITEM_CONTROLS):
            fields: Mapping[str, Any] = first
            overrides = {key: fields[key] for key in ITEM_CONTROLS if fields.get(key) is not None}
            states = [requests[index]["state"] for index in indices]
            payloads = self._agent.predict_batch(
                states, first["questions"], **overrides, **controls
            )
            if len(payloads) != len(indices):
                raise EngineError(
                    f"{name}: the agent returned {len(payloads)} results for {len(indices)} states"
                )
            results.update(zip(indices, payloads, strict=True))
        return [results[index] for index in range(len(requests))]

    def route(
        self, state: State, questions: Questions | None = None, **hints: Unpack[RouteHints]
    ) -> RoutePayload:
        raise UnsupportedOperationError(
            f"{type(self).__name__} wraps one fixed checkpoint, so there is nothing to route; "
            "use UpstreamRouterEngine to route between the upstream checkpoints"
        )


class AgentEngine(SingleCheckpointEngine):
    """DecisionEngine over one PyTorch checkpoint (``laya.Agent``)."""

    @classmethod
    def from_checkpoint(cls, model_id_or_path: str, **options: Unpack[AgentOptions]) -> Self:
        """Load a checkpoint with ``laya.load`` (Hub id or local directory).

        Needs torch (the full install profile). Pin what is loaded with ``revision`` and
        ``expected_sha256``, exactly as with ``laya.load``.
        """
        if not torch_available():
            raise MissingRuntimeError(
                "AgentEngine.from_checkpoint needs torch, which the light install profile leaves "
                "out; install the full profile with `uv sync --locked`"
            )
        import laya

        return cls(laya.load(model_id_or_path, **options))
