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

import hashlib
import importlib
import importlib.util
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from laya_platform._upstream import UPSTREAM_RELEASE_COMMIT
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
    InternalApi(
        "laya.calibrate",
        "records_from_labeled",
        "calibration (F4): labelled forwards -> the records Agent.fit_temperatures fits on",
    ),
)

_TORCH_BACKED_MODULES = frozenset({"laya.agent", "laya.calibrate", "laya.common"})


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


def check_question(question_id: Any, definition: Any) -> None:
    """Validate one question with the upstream's own rules; raises ``ValueError`` naming it.

    Both arguments reach the upstream exactly as given. Needs torch (see :func:`resolve`).
    """
    resolve(CHECK_QUESTION)(question_id, definition)


# --------------------------------------------------------------------- upstream repository files
@dataclass(frozen=True)
class UpstreamScript:
    """A file of the upstream *repository* (not of the wheel) the platform runs unchanged.

    Pinned by commit and by the digest of its exact content: a different file is refused, so an
    upstream change is a deliberate update of this record, never a silent drift.
    """

    path: str
    commit: str
    sha256: str
    functions: tuple[str, ...]
    reason: str

    @property
    def url(self) -> str:
        return f"https://raw.githubusercontent.com/{UPSTREAM_REPOSITORY}/{self.commit}/{self.path}"


UPSTREAM_REPOSITORY = "NandhaKishorM/laya"

#: The upstream's own fine-tuning recipe (RLCD on soft targets, temperature fit, export), used as
#: is by laya_platform.training (F8). Unchanged between the v0.3.23 tag and the audited commit.
FINETUNE_SCRIPT = UpstreamScript(
    path="notebooks/laya_finetune_typed_decisions_mps.py",
    commit=UPSTREAM_RELEASE_COMMIT,
    sha256="22ffb2bea0bb24a48650783336a77ead0acd0c111730b6005277c96ee6897dc5",
    functions=("prepare_model", "build_training_item", "train"),
    reason="training (F8): the upstream's fine-tune recipe, driven with the platform's datasets",
)


class UpstreamScriptMismatchError(ValueError):
    """The file found is not the pinned upstream file."""


def script_file(script: UpstreamScript, repository_dir: str | os.PathLike[str]) -> Path:
    """The pinned file inside ``repository_dir`` (a clone, or what :func:`fetch_script` wrote).

    Raises :class:`UpstreamScriptMismatchError` when its content is not the pinned one.
    """
    file = Path(repository_dir) / script.path
    digest = hashlib.sha256(file.read_bytes()).hexdigest()
    if digest != script.sha256:
        raise UpstreamScriptMismatchError(
            f"{file} has sha256 {digest}, not the pinned {script.sha256} "
            f"({UPSTREAM_REPOSITORY}@{script.commit[:12]})"
        )
    return file


def fetch_script(script: UpstreamScript, repository_dir: str | os.PathLike[str]) -> Path:
    """Download the pinned file from the upstream repository at its commit, verified."""
    file = Path(repository_dir) / script.path
    file.parent.mkdir(parents=True, exist_ok=True)
    # S310: a fixed https URL built from constants.
    with urllib.request.urlopen(script.url, timeout=60) as response:  # noqa: S310
        file.write_bytes(response.read())
    return script_file(script, repository_dir)


def load_script(script: UpstreamScript, repository_dir: str | os.PathLike[str]) -> ModuleType:
    """Import the pinned file as a module (it imports torch, transformers and laya internals)."""
    file = script_file(script, repository_dir)  # a wrong file is refused before anything else
    if not torch_available():
        raise MissingRuntimeError(
            f"{script.path} imports torch, which the light install profile leaves out; install "
            "the full profile with `uv sync --locked`"
        )
    spec = importlib.util.spec_from_file_location("laya_upstream_" + file.stem, file)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import {file}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    missing = [name for name in script.functions if not callable(getattr(module, name, None))]
    if missing:
        raise AttributeError(f"{script.path} no longer defines {missing}")
    return module
