"""Architecture guard: Laya internals are reached only through one adapter module.

Decided in Phase 0 (docs/ARCHITECTURE_PROPOSAL.md §5): the platform uses laya's public API, and
the few internal functions the training loop needs are isolated in
``laya_platform.core.upstream_compat``. import-linter cannot express rules on submodules of an
external package, so this test does.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_DIR = REPO_ROOT / "src" / "laya_platform"
ADAPTER = PACKAGE_DIR / "core" / "upstream_compat.py"
INTERNAL_MODULES = frozenset(
    {
        "laya.agent",
        "laya.calibrate",
        "laya.common",
        "laya.fast",
        "laya.tl_kernels",
    }
)


def _is_internal_module(name: str) -> bool:
    parts = name.split(".")
    if parts[0] != "laya" or len(parts) == 1:
        return False
    return any(part.startswith("_") for part in parts[1:]) or any(
        name == internal or name.startswith(internal + ".") for internal in INTERNAL_MODULES
    )


_DYNAMIC_IMPORTERS = frozenset({"import_module", "__import__"})


def internal_imports(source: str) -> list[str]:
    """Every import of a laya internal module or private name in ``source``.

    Dynamic imports with a literal name (``importlib.import_module("laya.common")``,
    ``__import__("laya.agent")``) count too, so the rule cannot be side-stepped by a string.
    """
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and node.args:
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            target = node.args[0]
            if (
                name in _DYNAMIC_IMPORTERS
                and isinstance(target, ast.Constant)
                and isinstance(target.value, str)
                and _is_internal_module(target.value)
            ):
                found.append(target.value)
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names if _is_internal_module(alias.name))
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if _is_internal_module(node.module):
                found.append(node.module)
            elif node.module.split(".")[0] == "laya":
                found.extend(
                    f"{node.module}.{alias.name}"
                    for alias in node.names
                    if alias.name.startswith("_")
                )
    return found


@pytest.mark.parametrize(
    "source",
    [
        "import laya.common",
        "from laya.common import build_sequence",
        "from laya.agent import _fix_tokenizer_config",
        "from laya import _upstream_private",
        "import laya._compile",
        "from laya.mcp import _internal",
        "importlib.import_module('laya.common')",
        "import_module('laya.agent')",
        "__import__('laya.calibrate')",
    ],
)
def test_checker_flags_internal_imports(source: str) -> None:
    assert internal_imports(source)


@pytest.mark.parametrize(
    "source",
    [
        "import laya",
        "from laya import Router, decide",
        "from laya.structured import SchemaError",
        "from laya.serve import create_app",
        "from laya.onnx_agent import ONNXAgent",
        "from laya_platform import cli",
        "importlib.import_module(api.module)",
        "importlib.import_module('laya.structured')",
    ],
)
def test_checker_accepts_public_imports(source: str) -> None:
    assert not internal_imports(source)


def test_only_the_adapter_imports_laya_internals() -> None:
    offenders = {
        str(path.relative_to(REPO_ROOT)): found
        for path in sorted(PACKAGE_DIR.rglob("*.py"))
        if path != ADAPTER and (found := internal_imports(path.read_text(encoding="utf-8")))
    }
    assert not offenders


def test_the_adapter_exists_and_is_the_only_exception() -> None:
    assert ADAPTER.is_file()
    assert "laya.agent" in ADAPTER.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "module",
    [
        "laya_platform",
        "laya_platform.core",
        "laya_platform.core.adapters",
        "laya_platform.core.upstream_compat",
    ],
)
def test_importing_the_core_pulls_no_heavy_runtime(module: str) -> None:
    # A fresh interpreter, so modules imported by other tests cannot hide an import.
    heavy = ("torch", "onnxruntime", "transformers", "fastapi", "starlette", "uvicorn", "mcp")
    code = (
        f"import sys, {module}\n"
        f"loaded = [m for m in {heavy!r} if m in sys.modules]\n"
        "print(','.join(loaded))"
    )
    result = subprocess.run(  # noqa: S603 -- fixed interpreter and code
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=120
    )
    assert result.stdout.strip() == ""
