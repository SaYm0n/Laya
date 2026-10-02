from __future__ import annotations

import sys
import tomllib
from importlib.metadata import metadata
from importlib.resources import files
from pathlib import Path
from typing import Any

from packaging.specifiers import SpecifierSet

import laya_platform

REPO_ROOT = Path(__file__).resolve().parents[2]


def _pyproject() -> dict[str, Any]:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_runtime_version_matches_pyproject() -> None:
    assert laya_platform.__version__ == _pyproject()["project"]["version"]


def test_package_cannot_be_uploaded_to_a_public_index() -> None:
    assert "Private :: Do Not Upload" in metadata("laya-platform").get_all("Classifier", [])


def test_supported_pythons_are_311_and_312_and_include_this_interpreter() -> None:
    spec = SpecifierSet(_pyproject()["project"]["requires-python"])
    assert "3.11" in spec
    assert "3.12" in spec
    assert "3.10" not in spec
    assert "3.13" not in spec
    assert f"{sys.version_info.major}.{sys.version_info.minor}" in spec


def test_package_ships_a_typing_marker() -> None:
    assert files("laya_platform").joinpath("py.typed").is_file()


def test_core_dependencies_stay_minimal() -> None:
    # F2 adds what DecisionSpec imports directly (pydantic, PyYAML). Optional capabilities arrive
    # as extras in later phases; none exists yet.
    assert _pyproject()["project"]["dependencies"] == [
        "laya==0.3.23",
        "pydantic>=2.13.5,<3",
        "pyyaml>=6.0.3,<7",
    ]
    assert "optional-dependencies" not in _pyproject()["project"]


def test_contract_test_tools_are_not_runtime_dependencies() -> None:
    dev = _pyproject()["dependency-groups"]["dev"]
    runtime = " ".join(_pyproject()["project"]["dependencies"])
    for tool in ("fastapi", "httpx", "mcp"):
        assert any(requirement.startswith(tool) for requirement in dev)
        assert tool not in runtime
