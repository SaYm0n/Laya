"""Architecture guard: Laya internals are reached only through one adapter module.

Decided in Phase 0 (docs/ARCHITECTURE_PROPOSAL.md §5): the platform uses laya's public API, and
the few internal functions the training loop needs are isolated in
``laya_platform.core.upstream_compat``. import-linter cannot express rules on submodules of an
external package, so this test does.
"""

from __future__ import annotations

import ast
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


def internal_imports(source: str) -> list[str]:
    """Every import of a laya internal module or private name in ``source``."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
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
        "from laya_platform import cli",
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
