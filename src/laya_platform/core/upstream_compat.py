"""The only module of laya_platform that may touch non-public parts of the upstream ``laya``.

Everything else uses laya's public API (``laya.__all__`` and the documented public modules such as
``laya.structured``, ``laya.router``, ``laya.serve``, ``laya.confidence``). The rule is enforced by
``tests/unit/test_architecture_imports.py``; the internals listed here are frozen by
``tests/compatibility/test_training_internals_contract.py``, so an upstream upgrade that moves or
re-signs one of them fails CI before anything here breaks at runtime.

Importing this module never imports torch. ``laya.common`` and ``laya.agent`` import torch at
module level, so every accessor resolves lazily and raises :class:`MissingRuntimeError` when the
light install profile (no torch) is active.
"""

from __future__ import annotations

import importlib
import importlib.util
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from laya_platform.core.errors import MissingRuntimeError


@dataclass(frozen=True)
class InternalApi:
    """One non-public upstream object the platform depends on, and why."""

    module: str
    qualname: str
    reason: str

    @property
    def path(self) -> str:
        return f"{self.module}.{self.qualname}"


CHECK_QUESTION = InternalApi(
    "laya.agent",
    "Agent._check_question",
    "the upstream's own per-question validation; DecisionSpec's question rules are kept "
    "aligned with it by tests/compatibility/test_schema_contract.py",
)

#: Audited internals (docs/UPSTREAM_ANALYSIS.md §3.11). The training loop arrives in F8; F2 only
#: records what it will need so an upstream upgrade is checked against it from now on.
INTERNAL_APIS: tuple[InternalApi, ...] = (
    InternalApi(
        "laya.common",
        "build_model",
        "training (F8): builds the encoder and decision head the way the upstream notebook does",
    ),
    InternalApi(
        "laya.common",
        "build_sequence",
        "training (F8): renders a state and one question into token ids exactly as inference does",
    ),
    InternalApi(
        "laya.common",
        "collate_items",
        "training (F8): pads and collates encoded items into a batch",
    ),
    InternalApi(
        "laya.agent",
        "_fix_tokenizer_config",
        "training (F8): the upstream notebook repairs a saved checkpoint's tokenizer config",
    ),
    CHECK_QUESTION,
)

_TORCH_BACKED_MODULES = frozenset({"laya.agent", "laya.common"})


def torch_available() -> bool:
    """Whether torch is importable (full install profile) without importing it."""
    return importlib.util.find_spec("torch") is not None


def resolve(api: InternalApi) -> Any:
    """Import and return the upstream object ``api`` names.

    Raises :class:`MissingRuntimeError` when the object lives in a torch-backed module and torch
    is not installed, and ``AttributeError`` when the pinned upstream no longer has it.
    """
    if api.module in _TORCH_BACKED_MODULES and not torch_available():
        raise MissingRuntimeError(
            f"{api.path} lives in a module that imports torch, which the light install profile "
            "leaves out; install the full profile with `uv sync --locked`"
        )
    target: Any = importlib.import_module(api.module)
    for part in api.qualname.split("."):
        target = getattr(target, part)
    return target


def check_question(question_id: str, definition: Mapping[str, Any]) -> None:
    """Validate one question with the upstream's own rules; raises ``ValueError`` naming it.

    Needs torch (see :func:`resolve`).
    """
    resolve(CHECK_QUESTION)(question_id, dict(definition))
