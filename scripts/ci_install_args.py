"""Print the ``uv sync`` arguments for the light CI install profile.

The default CI jobs (lint, type check, offline tests) never import torch: ``import laya``
resolves torch lazily and Phase 1 code only reads distribution metadata. Installing torch and its
CUDA stack would cost several GB per job for nothing, so those jobs skip exactly the packages that
are reachable in ``uv.lock`` **only** through the heavy roots below. Everything else in the lock is
installed with its locked hash. A separate scheduled job installs the full lock.

Usage::

    uv sync --locked $(python scripts/ci_install_args.py)
"""

from __future__ import annotations

import argparse
import tomllib
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

HEAVY_ROOTS: tuple[str, ...] = ("torch",)


def _dependency_names(package: Mapping[str, Any]) -> set[str]:
    names = {dep["name"] for dep in package.get("dependencies", [])}
    for deps in package.get("optional-dependencies", {}).values():
        names.update(dep["name"] for dep in deps)
    for deps in package.get("dev-dependencies", {}).values():
        names.update(dep["name"] for dep in deps)
    return names


def build_graph(lock: Mapping[str, Any]) -> dict[str, set[str]]:
    """Map each locked package name to the names it depends on (all markers, extras and groups)."""
    graph: dict[str, set[str]] = {}
    for package in lock["package"]:
        graph.setdefault(package["name"], set()).update(_dependency_names(package))
    return graph


def project_name(lock: Mapping[str, Any]) -> str:
    for package in lock["package"]:
        source = package.get("source", {})
        if "editable" in source or "virtual" in source:
            return str(package["name"])
    raise ValueError("uv.lock has no editable/virtual root package")


def reachable(graph: Mapping[str, set[str]], start: str, blocked: Iterable[str] = ()) -> set[str]:
    stop = set(blocked)
    seen: set[str] = set()
    stack = [start]
    while stack:
        name = stack.pop()
        if name in seen or name in stop:
            continue
        seen.add(name)
        stack.extend(graph.get(name, ()))
    return seen


def heavy_only_packages(lock: Mapping[str, Any], roots: Iterable[str] = HEAVY_ROOTS) -> list[str]:
    """Packages reachable from the project only through ``roots`` (the roots included)."""
    graph = build_graph(lock)
    root = project_name(lock)
    everything = reachable(graph, root)
    without_heavy = reachable(graph, root, blocked=roots)
    return sorted(everything - without_heavy)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--lock", type=Path, default=Path(__file__).resolve().parents[1] / "uv.lock"
    )
    args = parser.parse_args(argv)
    lock = tomllib.loads(args.lock.read_text(encoding="utf-8"))
    print(" ".join(f"--no-install-package {name}" for name in heavy_only_packages(lock)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
