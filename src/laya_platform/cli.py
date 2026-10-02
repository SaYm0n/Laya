"""Command-line entry point: ``laya-platform``.

Phase 1 only ships ``--version``. It reads distribution metadata and never imports ``laya`` (and
therefore never imports torch or touches the network).
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as distribution_version

from laya_platform import __version__
from laya_platform._upstream import UPSTREAM_DISTRIBUTION, UPSTREAM_VERSION


def installed_upstream_version() -> str | None:
    """Version of the installed upstream distribution, or None when it is not installed."""
    try:
        return distribution_version(UPSTREAM_DISTRIBUTION)
    except PackageNotFoundError:
        return None


def version_text() -> str:
    """One line naming this package's version and the state of the upstream pin."""
    installed = installed_upstream_version()
    if installed == UPSTREAM_VERSION:
        upstream = f"upstream {UPSTREAM_DISTRIBUTION} {UPSTREAM_VERSION}"
    elif installed is None:
        upstream = f"upstream {UPSTREAM_DISTRIBUTION}: not installed, expected {UPSTREAM_VERSION}"
    else:
        upstream = (
            f"upstream {UPSTREAM_DISTRIBUTION}: installed {installed}, expected {UPSTREAM_VERSION}"
        )
    return f"laya-platform {__version__} ({upstream})"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="laya-platform",
        description=(
            "Laya Decision Platform (provisional name). Independent project built on top of "
            "Laya; not affiliated with Convai Innovations."
        ),
    )
    parser.add_argument("--version", action="version", version=version_text())
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0
