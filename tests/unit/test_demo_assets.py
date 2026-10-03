"""The demo images of the README come from running the platform (scripts/make_demo_assets.py).

The script must keep working, every card must say it came from a simulated engine, and the
images the docs link to must be the ones it produces.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from xml.etree import ElementTree

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "make_demo_assets.py"
COMMITTED = REPO_ROOT / "docs" / "assets" / "demo"


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("make_demo_assets", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their annotations through it
    spec.loader.exec_module(module)
    return module


def test_the_demo_cards_are_generated_from_the_running_platform(tmp_path: Path) -> None:
    assert _script().main(["--out", str(tmp_path)]) == 0
    generated = sorted(p.name for p in tmp_path.glob("*.svg"))
    assert generated == sorted(p.name for p in COMMITTED.glob("*.svg"))
    for name in generated:
        svg = (tmp_path / name).read_text(encoding="utf-8")
        ElementTree.fromstring(svg)  # noqa: S314 -- well-formed; the file was generated just above
        assert "motor simulado" in svg, name


def test_the_shell_command_wraps_between_options() -> None:
    words = ["laya-platform", "specialist", "list", "--db-url", "x" * 60, "--actor", "me"]
    first = "laya-platform specialist list --db-url " + "x" * 60
    assert _script().wrap(words) == first + " \\\n  --actor me"
