"""Detect new upstream laya releases on PyPI. Report only -- never changes the pin.

Reads the pin from ``src/laya_platform/_upstream.py`` (by path, without installing the project),
compares it with PyPI, and re-checks that PyPI still serves the audited artifacts for the pinned
release. Writes a Markdown report and, under GitHub Actions, step outputs for the workflow that
opens a tracking issue.

Exit status: 0 on success (whether or not a newer release exists), 1 when the pinned release's
artifacts no longer match the audited hashes, 2 when PyPI could not be reached.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from packaging.version import InvalidVersion, Version

REPO_ROOT = Path(__file__).resolve().parents[1]
PIN_MODULE = REPO_ROOT / "src" / "laya_platform" / "_upstream.py"


@dataclass(frozen=True)
class Pin:
    distribution: str
    version: str
    wheel_filename: str
    wheel_sha256: str
    sdist_filename: str
    sdist_sha256: str
    repository: str


def load_pin(path: Path = PIN_MODULE) -> Pin:
    spec = importlib.util.spec_from_file_location("_upstream_pin", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module: ModuleType = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return Pin(
        distribution=module.UPSTREAM_DISTRIBUTION,
        version=module.UPSTREAM_VERSION,
        wheel_filename=module.UPSTREAM_WHEEL_FILENAME,
        wheel_sha256=module.UPSTREAM_WHEEL_SHA256,
        sdist_filename=module.UPSTREAM_SDIST_FILENAME,
        sdist_sha256=module.UPSTREAM_SDIST_SHA256,
        repository=module.UPSTREAM_REPOSITORY,
    )


def _is_live(files: list[Mapping[str, Any]]) -> bool:
    return bool(files) and not all(f.get("yanked", False) for f in files)


def newer_releases(
    project: Mapping[str, Any], pinned: str, include_prereleases: bool = False
) -> list[str]:
    """Versions on PyPI newer than ``pinned``, oldest first; yanked releases are ignored."""
    current = Version(pinned)
    found: list[Version] = []
    for raw, files in project.get("releases", {}).items():
        try:
            candidate = Version(raw)
        except InvalidVersion:
            continue
        if candidate <= current or not _is_live(files):
            continue
        if candidate.is_prerelease and not include_prereleases:
            continue
        found.append(candidate)
    return [str(v) for v in sorted(found)]


def hash_mismatches(pin: Pin, release_files: list[Mapping[str, Any]]) -> list[str]:
    """Problems with the pinned release's artifacts as PyPI serves them now."""
    served = {f["filename"]: f.get("digests", {}).get("sha256") for f in release_files}
    problems = []
    for filename, expected in (
        (pin.wheel_filename, pin.wheel_sha256),
        (pin.sdist_filename, pin.sdist_sha256),
    ):
        actual = served.get(filename)
        if actual is None:
            problems.append(f"{filename} is no longer served by PyPI")
        elif actual != expected:
            problems.append(f"{filename}: sha256 {actual} != audited {expected}")
    return problems


def issue_title(pin: Pin, newer: list[str]) -> str:
    return f"Upstream {pin.distribution} {newer[-1]} available (pinned {pin.version})"


def render_report(pin: Pin, newer: list[str], problems: list[str]) -> str:
    lines = [f"## Upstream `{pin.distribution}` watch", ""]
    lines.append(f"- Pinned: `{pin.distribution}=={pin.version}`")
    if newer:
        lines.append(f"- Newer on PyPI: {', '.join(f'`{v}`' for v in newer)}")
        lines += ["", "### Release notes", ""]
        lines += [f"- {pin.repository}/releases/tag/v{v}" for v in newer]
    else:
        lines.append("- No newer release on PyPI.")
    lines += ["", "### Pinned artifacts", ""]
    if problems:
        lines += [f"- **{p}**" for p in problems]
    else:
        lines.append("- PyPI still serves the audited wheel and sdist hashes.")
    if newer:
        lines += [
            "",
            "### Upgrade checklist (nothing is updated automatically)",
            "",
            "- [ ] Read the release notes and the upstream diff since the pinned commit.",
            "- [ ] Bump the pin in `pyproject.toml` and `src/laya_platform/_upstream.py`,"
            " record the new artifact hashes, run `uv lock`.",
            "- [ ] Default suite green (`uv run pytest`), including `tests/compatibility/`.",
            "- [ ] Weight-dependent compatibility and regression suites green"
            " (`--run-weights`, outside CI).",
            "- [ ] Benchmarks compared with the previous pin.",
            "- [ ] Security review: dependency audit, license check, changelog for security fixes.",
        ]
    return "\n".join(lines) + "\n"


def fetch_json(path: str, timeout: float = 30.0) -> dict[str, Any]:
    """GET ``https://pypi.org/pypi/<path>`` as JSON."""
    url = f"https://pypi.org/pypi/{path}"
    request = urllib.request.Request(url, headers={"User-Agent": "laya-platform-upstream-watch"})
    # The URL is built from a fixed https:// prefix, never from caller-supplied schemes.
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        data: dict[str, Any] = json.load(response)
    return data


def _write_github_outputs(values: Mapping[str, str]) -> None:
    target = os.environ.get("GITHUB_OUTPUT")
    if not target:
        return
    with Path(target).open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report", type=Path, help="write the Markdown report here")
    parser.add_argument("--include-prereleases", action="store_true")
    args = parser.parse_args(argv)

    pin = load_pin()
    try:
        project = fetch_json(f"{pin.distribution}/json")
        release = fetch_json(f"{pin.distribution}/{pin.version}/json")
    except OSError as exc:
        print(f"could not reach PyPI: {exc}", file=sys.stderr)
        return 2

    newer = newer_releases(project, pin.version, args.include_prereleases)
    problems = hash_mismatches(pin, release.get("urls", []))
    report = render_report(pin, newer, problems)
    if args.report:
        args.report.write_text(report, encoding="utf-8")
    print(report)
    _write_github_outputs(
        {
            "newer": "true" if newer else "false",
            "latest": newer[-1] if newer else pin.version,
            "title": issue_title(pin, newer) if newer else "",
        }
    )
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
