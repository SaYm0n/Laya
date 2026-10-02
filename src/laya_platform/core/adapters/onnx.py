"""OnnxEngine: a DecisionEngine over the upstream ONNX runtime (``laya.onnx_agent.ONNXAgent``).

The runtime is optional and imported only when an engine is built from an export, so importing
this module (or the rest of laya_platform) never needs onnxruntime. In ``laya==0.3.23`` the ONNX
agent still imports torch through ``laya.common``, so both runtimes are required to load one.
Exporting a model (the upstream ``scripts/export_onnx.py``) and ONNX images are out of scope here.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable
from typing import Any, Self, TypedDict, Unpack

from laya_platform.core.adapters.agent import SingleCheckpointEngine
from laya_platform.core.errors import MissingRuntimeError

#: Modules ``ONNXAgent`` needs at load time in the pinned upstream release.
REQUIRED_RUNTIMES = ("onnxruntime", "torch")


class OnnxOptions(TypedDict, total=False):
    """Keyword arguments of ``ONNXAgent`` (frozen against the upstream signature in tests)."""

    token: str | None
    subfolder: str | None
    revision: str | None
    expected_sha256: dict[str, str] | None
    hooks: Any
    on_predict_start: Callable[..., Any] | None
    on_predict_end: Callable[..., Any] | None
    hooks_raise: bool
    hooks_concurrent: bool
    hooks_timeout: float | None
    lang_temperatures: dict[str, dict[str, Any]] | None
    calibration: str | None


def missing_runtimes() -> list[str]:
    """The required runtimes that are not installed, without importing any of them."""
    return [name for name in REQUIRED_RUNTIMES if importlib.util.find_spec(name) is None]


class OnnxEngine(SingleCheckpointEngine):
    """DecisionEngine over one exported ONNX model; same contract as AgentEngine."""

    @classmethod
    def from_export(
        cls, model_id_or_path: str, onnx_path: str = "laya.onnx", **options: Unpack[OnnxOptions]
    ) -> Self:
        """Load ``onnx_path`` with the tokenizer and config of ``model_id_or_path``."""
        missing = missing_runtimes()
        if missing:
            raise MissingRuntimeError(
                f"OnnxEngine needs {missing}: the pinned laya ONNXAgent loads onnxruntime and "
                "imports torch through laya.common. Neither is required by the rest of the "
                "platform, so they are not installed by default"
            )
        from laya.onnx_agent import ONNXAgent

        return cls(ONNXAgent(model_id_or_path, onnx_path=onnx_path, **options))
