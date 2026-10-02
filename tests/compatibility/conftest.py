"""Helpers for the upstream contract tests.

* Signatures are read from the installed upstream **source** with ``ast``, so the contract of the
  torch-backed modules (``laya.agent``, ``laya.common``, ``laya.onnx_agent``) is checked in the
  light profile too, without importing torch. A ``torch``-marked test checks that this reading
  agrees with ``inspect.signature`` wherever torch is installed.
* ``StubAgent`` stands in for a loaded checkpoint so the real ``laya.Router`` (routing, hooks, the
  confidence gate, the ``routing`` block) and the real ``laya.serve`` app run with no weights. It
  answers like the platform's FakeEngine and records what the upstream passed it.
"""

from __future__ import annotations

import ast
import importlib.util
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

import pytest
from laya import Router

from laya_platform.core.adapters import FakeAnswer, FakeEngine
from laya_platform.core.types import DecisionPayload, Questions, State

CHECKPOINTS = ("english", "multilingual", "typed-decisions")


@cache
def _source_tree(module: str) -> ast.Module:
    spec = importlib.util.find_spec(module)
    assert spec is not None
    assert spec.origin is not None
    return ast.parse(Path(spec.origin).read_text(encoding="utf-8"))


def _definition(module: str, qualname: str) -> ast.AST:
    scope: list[ast.stmt] = _source_tree(module).body
    node: ast.AST | None = None
    for part in qualname.split("."):
        node = next(
            (
                item
                for item in scope
                if isinstance(item, ast.FunctionDef | ast.ClassDef | ast.AsyncFunctionDef)
                and item.name == part
            ),
            None,
        )
        if node is None:
            raise LookupError(f"{module}.{qualname} is not defined in the installed source")
        scope = node.body if isinstance(node, ast.ClassDef) else []
    assert node is not None
    return node


def render_arguments(arguments: ast.arguments) -> str:
    """``(a, b=1, *, c=None, **kw)`` without annotations: names, kinds and default expressions."""
    positional = [*arguments.posonlyargs, *arguments.args]
    defaults: list[ast.expr | None] = [None] * (len(positional) - len(arguments.defaults))
    defaults += arguments.defaults
    parts = [
        arg.arg if default is None else f"{arg.arg}={ast.unparse(default)}"
        for arg, default in zip(positional, defaults, strict=True)
    ]
    if arguments.posonlyargs:
        parts.insert(len(arguments.posonlyargs), "/")
    if arguments.vararg:
        parts.append(f"*{arguments.vararg.arg}")
    elif arguments.kwonlyargs:
        parts.append("*")
    for arg, kw_default in zip(arguments.kwonlyargs, arguments.kw_defaults, strict=True):
        parts.append(arg.arg if kw_default is None else f"{arg.arg}={ast.unparse(kw_default)}")
    if arguments.kwarg:
        parts.append(f"**{arguments.kwarg.arg}")
    return "(" + ", ".join(parts) + ")"


def upstream_signature(module: str, qualname: str) -> str:
    """The signature of ``module.qualname`` as written in the installed upstream source."""
    node = _definition(module, qualname)
    if isinstance(node, ast.ClassDef):
        node = _definition(module, f"{qualname}.__init__")
    assert isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    return render_arguments(node.args)


def upstream_class_aliases(module: str, class_name: str) -> dict[str, str]:
    """``{alias: target}`` for every ``alias = target`` assignment in a class body."""
    node = _definition(module, class_name)
    assert isinstance(node, ast.ClassDef)
    return {
        target.id: item.value.id
        for item in node.body
        if isinstance(item, ast.Assign) and isinstance(item.value, ast.Name)
        for target in item.targets
        if isinstance(target, ast.Name)
    }


def upstream_defines(module: str, qualname: str) -> bool:
    try:
        _definition(module, qualname)
    except LookupError:
        return False
    return True


@dataclass
class StubAgent:
    """A loaded-checkpoint stand-in with the ``Agent`` call surface the upstream Router uses."""

    answers: dict[str, FakeAnswer] = field(default_factory=dict)
    error: Exception | None = None
    device: str = "cpu"
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def _one(self, questions: Questions) -> DecisionPayload:
        payload = FakeEngine(self.answers, error=self.error).predict("", questions)
        payload.pop("routing")
        payload["model"] = "laya-rl-agent"
        return payload

    def system_one(self, state: State, questions: Questions, **kwargs: Any) -> DecisionPayload:
        self.calls.append(("system_one", {"state": state, **kwargs}))
        return self._one(questions)

    def predict_batch(
        self, states: list[State], questions: Questions, **kwargs: Any
    ) -> list[DecisionPayload]:
        self.calls.append(("predict_batch", {"states": states, **kwargs}))
        return [self._one(questions) for _ in states]


def stub_router(agent: StubAgent | None = None, **options: Any) -> Router:
    """A real ``laya.Router`` whose three checkpoints are one StubAgent (nothing is downloaded)."""
    router = Router(**options)
    stub = agent or StubAgent()
    for name in CHECKPOINTS:
        router.attach(name, stub)
    return router


# Test modules cannot import this conftest (--import-mode=importlib), so the helpers are fixtures.
@pytest.fixture(scope="session")
def signature() -> Callable[[str, str], str]:
    return upstream_signature


@pytest.fixture(scope="session")
def class_aliases() -> Callable[[str, str], dict[str, str]]:
    return upstream_class_aliases


@pytest.fixture(scope="session")
def defines() -> Callable[[str, str], bool]:
    return upstream_defines


@pytest.fixture(scope="session")
def make_agent() -> type[StubAgent]:
    return StubAgent


@pytest.fixture
def stub_agent() -> StubAgent:
    return StubAgent()


@pytest.fixture
def router_factory(stub_agent: StubAgent) -> Callable[..., Router]:
    """``router_factory(agent=None, **Router options)``: a real Router over a StubAgent."""

    def make(agent: StubAgent | None = None, **options: Any) -> Router:
        return stub_router(agent or stub_agent, **options)

    return make


# ------------------------------------------------------------------------------- weights-gated
#: The checkpoints are loaded at the upstream's reviewed revision of the bundle repository (the
#: same pin `LAYA_REVISION=reviewed` selects), on CPU in fp32: the most reproducible setting.
REVIEWED_BUNDLE_REVISION = "55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851"


@pytest.fixture(scope="session")
def weighted_router() -> Router:
    """A real Router over the real checkpoints. Only ``weights``-marked tests may use it."""
    return Router(revision=REVIEWED_BUNDLE_REVISION, device="cpu")
