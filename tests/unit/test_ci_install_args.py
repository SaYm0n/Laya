from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path
from types import ModuleType
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
TORCH_ONLY_NAMES = frozenset(
    {"torch", "triton", "sympy", "mpmath", "networkx", "jinja2", "markupsafe", "setuptools"}
)


def _load_script() -> ModuleType:
    path = REPO_ROOT / "scripts" / "ci_install_args.py"
    spec = importlib.util.spec_from_file_location("ci_install_args", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ci = _load_script()


def _lock(*packages: dict[str, Any]) -> dict[str, Any]:
    return {"package": list(packages)}


def _dep(*names: str) -> list[dict[str, str]]:
    return [{"name": name} for name in names]


SYNTHETIC = _lock(
    {
        "name": "app",
        "source": {"editable": "."},
        "dependencies": _dep("laya"),
        "dev-dependencies": {"dev": _dep("pytest")},
    },
    {"name": "laya", "dependencies": _dep("torch", "numpy", "filelock")},
    {"name": "torch", "dependencies": _dep("filelock", "nvidia-cublas", "sympy")},
    {"name": "nvidia-cublas"},
    {"name": "sympy", "dependencies": _dep("mpmath")},
    {"name": "mpmath"},
    {"name": "numpy"},
    {"name": "filelock"},
    {"name": "pytest"},
)


def test_only_packages_reachable_solely_through_torch_are_skipped() -> None:
    assert ci.heavy_only_packages(SYNTHETIC) == ["mpmath", "nvidia-cublas", "sympy", "torch"]


def test_shared_dependencies_are_kept() -> None:
    skipped = ci.heavy_only_packages(SYNTHETIC)
    assert "filelock" not in skipped
    assert "numpy" not in skipped
    assert "laya" not in skipped


def test_the_real_lock_skips_the_cuda_stack_but_keeps_laya() -> None:
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    skipped = set(ci.heavy_only_packages(lock))
    assert "torch" in skipped
    assert {"laya", "laya-platform", "numpy", "transformers", "pytest"}.isdisjoint(skipped)
    unexpected = {
        name
        for name in skipped
        if not name.startswith(("nvidia-", "cuda-")) and name not in TORCH_ONLY_NAMES
    }
    assert not unexpected
