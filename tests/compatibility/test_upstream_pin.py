"""The upstream pin: one exact laya release, locked by artifact hash, installed as locked."""

from __future__ import annotations

import subprocess
import sys
import tomllib
from importlib.metadata import requires, version
from pathlib import Path
from typing import Any

from laya_platform import _upstream as pin

REPO_ROOT = Path(__file__).resolve().parents[2]


def _locked_laya() -> dict[str, Any]:
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    entries = [p for p in lock["package"] if p["name"] == pin.UPSTREAM_DISTRIBUTION]
    assert len(entries) == 1, "uv.lock must hold exactly one laya release"
    entry: dict[str, Any] = entries[0]
    return entry


def test_pyproject_pins_the_exact_release() -> None:
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert f"{pin.UPSTREAM_DISTRIBUTION}=={pin.UPSTREAM_VERSION}" in project["dependencies"]


def test_lock_resolves_the_pinned_release_from_pypi() -> None:
    laya = _locked_laya()
    assert laya["version"] == pin.UPSTREAM_VERSION
    assert laya["source"] == {"registry": "https://pypi.org/simple"}


def test_lock_holds_the_audited_wheel_hash() -> None:
    wheels = _locked_laya()["wheels"]
    assert len(wheels) == 1
    assert wheels[0]["url"].endswith("/" + pin.UPSTREAM_WHEEL_FILENAME)
    assert wheels[0]["hash"] == f"sha256:{pin.UPSTREAM_WHEEL_SHA256}"


def test_lock_holds_the_audited_sdist_hash() -> None:
    sdist = _locked_laya()["sdist"]
    assert sdist["url"].endswith("/" + pin.UPSTREAM_SDIST_FILENAME)
    assert sdist["hash"] == f"sha256:{pin.UPSTREAM_SDIST_SHA256}"


def test_installed_release_is_the_pinned_one() -> None:
    assert version(pin.UPSTREAM_DISTRIBUTION) == pin.UPSTREAM_VERSION


def test_installed_platform_requires_the_pinned_release() -> None:
    assert f"{pin.UPSTREAM_DISTRIBUTION}=={pin.UPSTREAM_VERSION}" in (
        requires("laya-platform") or []
    )


def test_importing_laya_does_not_import_torch() -> None:
    # The light CI profile relies on this upstream property: `import laya` resolves torch lazily.
    code = "import sys, laya; print(laya.__version__); print('torch' in sys.modules)"
    result = subprocess.run(  # noqa: S603 -- fixed argv, no shell
        [sys.executable, "-c", code], capture_output=True, text=True, check=False, timeout=120
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == [pin.UPSTREAM_VERSION, "False"]
