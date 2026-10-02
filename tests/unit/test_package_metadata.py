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
    # Optional capabilities arrive as extras in later phases; the core depends on laya only.
    assert _pyproject()["project"]["dependencies"] == ["laya==0.3.23"]
    assert "optional-dependencies" not in _pyproject()["project"]
