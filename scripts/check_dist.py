"""Verify the built wheel and sdist before anyone installs them.

Checks what ``uv build`` cannot know about this project's rules: the metadata declares exactly
the pyproject dependencies (and so the exact upstream pin), the no-upload classifier, the license
files, the typing marker and the entry point, and that no tests, secrets, weights or data ended up
inside an artifact. Standard library only.

Usage::

    uv build && python scripts/check_dist.py dist/
"""

from __future__ import annotations

import argparse
import re
import tarfile
import tomllib
import zipfile
from collections.abc import Sequence
from email.parser import Parser
from pathlib import Path

PACKAGE = "laya_platform"
UPSTREAM_PIN = "laya==0.3.23"
PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"
FORBIDDEN_MEMBER = re.compile(
    r"(^|/)\.env($|\.)|(^|/)tests?/|\.(safetensors|onnx|pt|pth|ckpt|gguf|parquet|sqlite3?|db|pem|key)$"
)


def declared_dependencies(pyproject: Path = PYPROJECT) -> list[str]:
    project = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
    return list(project["dependencies"])


def _normalized(requirements: Sequence[str]) -> list[str]:
    return sorted(re.sub(r"\s+", "", requirement).lower() for requirement in requirements)


def check_wheel(path: Path, expected: Sequence[str] | None = None) -> list[str]:
    """Problems with one wheel; ``expected`` defaults to the pyproject dependencies."""
    expected = declared_dependencies() if expected is None else expected
    problems: list[str] = []
    with zipfile.ZipFile(path) as wheel:
        names = wheel.namelist()
        dist_info = next(
            (n.split("/")[0] for n in names if n.endswith(".dist-info/METADATA")), None
        )
        if dist_info is None:
            return [f"{path.name}: no METADATA"]
        metadata = Parser().parsestr(wheel.read(f"{dist_info}/METADATA").decode("utf-8"))
        entry_points = wheel.read(f"{dist_info}/entry_points.txt").decode("utf-8")
    requires = metadata.get_all("Requires-Dist") or []
    if _normalized(requires) != _normalized(expected):
        problems.append(f"Requires-Dist must be exactly {list(expected)}, got {requires}")
    if _normalized([UPSTREAM_PIN])[0] not in _normalized(requires):
        problems.append(f"Requires-Dist must pin {UPSTREAM_PIN}")
    if "Private :: Do Not Upload" not in (metadata.get_all("Classifier") or []):
        problems.append("missing the 'Private :: Do Not Upload' classifier")
    if metadata.get("License-Expression") != "Apache-2.0":
        problems.append(f"License-Expression is {metadata.get('License-Expression')!r}")
    for license_file in ("LICENSE", "NOTICE"):
        if f"{dist_info}/licenses/{license_file}" not in names:
            problems.append(f"{license_file} is not shipped in {dist_info}/licenses/")
    if f"{PACKAGE}/py.typed" not in names:
        problems.append("py.typed marker is missing")
    if "laya-platform = laya_platform.cli:main" not in entry_points:
        problems.append("console script laya-platform is not declared")
    outside = [n for n in names if not n.startswith((f"{PACKAGE}/", f"{dist_info}/"))]
    if outside:
        problems.append(f"unexpected top-level members: {outside}")
    problems += [f"forbidden member {n}" for n in names if FORBIDDEN_MEMBER.search(n)]
    return [f"{path.name}: {p}" for p in problems]


def check_sdist(path: Path) -> list[str]:
    with tarfile.open(path) as sdist:
        names = [m.name.split("/", 1)[1] for m in sdist.getmembers() if "/" in m.name]
    problems = [f"forbidden member {n}" for n in names if FORBIDDEN_MEMBER.search(n)]
    for required in ("pyproject.toml", "LICENSE", "NOTICE", f"src/{PACKAGE}/__init__.py"):
        if required not in names:
            problems.append(f"{required} is missing")
    return [f"{path.name}: {p}" for p in problems]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dist", type=Path, nargs="?", default=Path("dist"))
    args = parser.parse_args(argv)

    wheels = sorted(args.dist.glob("*.whl"))
    sdists = sorted(args.dist.glob("*.tar.gz"))
    problems: list[str] = []
    if len(wheels) != 1 or len(sdists) != 1:
        problems.append(f"expected one wheel and one sdist in {args.dist}, got {wheels + sdists}")
    for wheel in wheels:
        problems += check_wheel(wheel)
    for sdist in sdists:
        problems += check_sdist(sdist)
    for problem in problems:
        print(f"error: {problem}")
    if not problems:
        print(f"dist check: {', '.join(p.name for p in wheels + sdists)} OK")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
