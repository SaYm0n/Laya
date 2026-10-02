"""Check every package in ``uv.lock`` against the license policy in ``pyproject.toml``.

License metadata is read from PyPI's JSON API (one request per locked release), so the check covers
the whole universal lock -- including platform-specific packages such as the CUDA stack that the
light CI profile never installs -- without installing anything. Standard library only.

Resolution order per release: ``license_expression`` (PEP 639), then a recognisable ``license``
field, then ``License ::`` classifiers (several classifiers are read as alternatives).

Exit status: 0 when every package is allowed or covered by a documented exception, 1 otherwise,
2 when metadata could not be fetched.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
import tomllib
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
ALLOWED = "allowed"
UNKNOWN = "unknown"
DENIED = "denied"
_RANK = {ALLOWED: 0, UNKNOWN: 1, DENIED: 2}

# Free-text `license` values and classifier names seen in practice, mapped to SPDX-like ids.
_TEXT_ALIASES: dict[str, str] = {
    "apache 2.0": "Apache-2.0",
    "apache 2.0 license": "Apache-2.0",
    "apache license 2.0": "Apache-2.0",
    "apache license, version 2.0": "Apache-2.0",
    "apache software license": "Apache-2.0",
    "apache-2.0": "Apache-2.0",
    "bsd": "BSD",
    "bsd license": "BSD",
    "bsd 2-clause license": "BSD-2-Clause",
    "bsd 3-clause license": "BSD-3-Clause",
    "isc license": "ISC",
    "isc license (iscl)": "ISC",
    "mit": "MIT",
    "mit license": "MIT",
    "mozilla public license 2.0 (mpl 2.0)": "MPL-2.0",
    "psf": "PSF-2.0",
    "psfl": "PSF-2.0",
    "python software foundation license": "PSF-2.0",
    "the unlicense (unlicense)": "Unlicense",
    "other/proprietary license": "LicenseRef-Proprietary",
    "nvidia proprietary software": "LicenseRef-NVIDIA-Proprietary",
    "gnu general public license (gpl)": "GPL",
    "gnu lesser general public license v3 (lgplv3)": "LGPL-3.0",
    "gnu library or lesser general public license (lgpl)": "LGPL",
}
_LICENSE_TEXT_PREFIXES: tuple[tuple[str, str], ...] = (
    ("apache license", "Apache-2.0"),
    ("permission is hereby granted, free of charge", "MIT"),
)
_SPDX_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+\-]*$")


@dataclass(frozen=True)
class Policy:
    allowed: frozenset[str]
    denied: tuple[str, ...]
    exceptions: Mapping[str, str] = field(default_factory=dict)

    def classify(self, license_id: str) -> str:
        upper = license_id.upper()
        if any(token.upper() in upper for token in self.denied):
            return DENIED
        if license_id in self.allowed:
            return ALLOWED
        return UNKNOWN

    def exception_for(self, package: str) -> str | None:
        for pattern, reason in self.exceptions.items():
            if fnmatch.fnmatchcase(package, pattern):
                return reason
        return None


@dataclass(frozen=True)
class Verdict:
    name: str
    version: str
    scope: str
    expression: str
    status: str
    exception: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == ALLOWED or self.exception is not None


# ----------------------------------------------------------------------------- expressions
def _tokens(expression: str) -> list[str]:
    return re.findall(r"\(|\)|[^\s()]+", expression)


def evaluate_expression(expression: str, policy: Policy) -> str:
    """Evaluate an SPDX-style expression: OR takes the best branch, AND the worst."""
    tokens = _tokens(expression)
    position = 0

    def peek() -> str | None:
        return tokens[position] if position < len(tokens) else None

    def take() -> str:
        nonlocal position
        if position >= len(tokens):
            raise ValueError(f"unexpected end of license expression {expression!r}")
        token = tokens[position]
        position += 1
        return token

    def disjunction() -> str:
        best = conjunction()
        while (peek() or "").upper() == "OR":
            take()
            best = min(best, conjunction(), key=_RANK.__getitem__)
        return best

    def conjunction() -> str:
        worst = factor()
        while (peek() or "").upper() == "AND":
            take()
            worst = max(worst, factor(), key=_RANK.__getitem__)
        return worst

    def factor() -> str:
        symbol = take()
        if symbol == "(":
            value = disjunction()
            if take() != ")":
                raise ValueError(f"unbalanced parentheses in {expression!r}")
            return value
        if (peek() or "").upper() == "WITH":  # an exception never widens the base license
            take()
            take()
        return policy.classify(symbol)

    result = disjunction()
    if position != len(tokens):
        raise ValueError(f"trailing tokens in license expression {expression!r}")
    return result


# ----------------------------------------------------------------------------- metadata
def normalise_text(text: str) -> str | None:
    """Map a free-text ``license`` field or classifier to an id, or None when unrecognisable."""
    cleaned = " ".join(text.split())
    if not cleaned:
        return None
    alias = _TEXT_ALIASES.get(cleaned.lower())
    if alias:
        return alias
    for prefix, license_id in _LICENSE_TEXT_PREFIXES:
        if cleaned.lower().startswith(prefix):
            return license_id
    if len(cleaned) <= 64 and (_SPDX_ID.match(cleaned) or " OR " in cleaned or " AND " in cleaned):
        return cleaned
    return None


def license_expression(info: Mapping[str, Any]) -> str:
    """The license expression a PyPI ``info`` block declares, or ``UNKNOWN``."""
    expression = (info.get("license_expression") or "").strip()
    if expression:
        return expression
    from_text = normalise_text(info.get("license") or "")
    if from_text:
        return from_text
    classifiers = [
        c.split(" :: ")[-1] for c in info.get("classifiers") or [] if c.startswith("License ::")
    ]
    ids = sorted({normalise_text(c) or "UNKNOWN" for c in classifiers})
    return " OR ".join(ids) if ids else "UNKNOWN"


def judge(name: str, version: str, scope: str, info: Mapping[str, Any], policy: Policy) -> Verdict:
    expression = license_expression(info)
    try:
        status = evaluate_expression(expression, policy)
    except ValueError:
        status = UNKNOWN
    exception = None if status == ALLOWED else policy.exception_for(name)
    return Verdict(name, version, scope, expression, status, exception)


# ----------------------------------------------------------------------------- lock and policy
def locked_releases(lock: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    """(name, version, scope) for every registry release; scope is runtime or dev."""
    packages = lock["package"]
    graph: dict[str, set[str]] = {}
    root: Mapping[str, Any] | None = None
    for package in packages:
        deps = {d["name"] for d in package.get("dependencies", [])}
        for extra in package.get("optional-dependencies", {}).values():
            deps.update(d["name"] for d in extra)
        graph.setdefault(package["name"], set()).update(deps)
        source = package.get("source", {})
        if "editable" in source or "virtual" in source:
            root = package
    if root is None:
        raise ValueError("uv.lock has no editable/virtual root package")
    runtime: set[str] = set()
    stack = [d["name"] for d in root.get("dependencies", [])]
    while stack:
        name = stack.pop()
        if name not in runtime:
            runtime.add(name)
            stack.extend(graph.get(name, ()))
    return sorted(
        (p["name"], p["version"], "runtime" if p["name"] in runtime else "dev")
        for p in packages
        if "registry" in p.get("source", {})
    )


def load_policy(pyproject: Mapping[str, Any]) -> Policy:
    table = pyproject["tool"]["laya-platform"]["license-policy"]
    return Policy(
        allowed=frozenset(table["allowed"]),
        denied=tuple(table["denied"]),
        exceptions=dict(table.get("exceptions", {})),
    )


def fetch_info(name: str, version: str, timeout: float = 30.0) -> dict[str, Any]:
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    request = urllib.request.Request(url, headers={"User-Agent": "laya-platform-license-check"})
    # The URL is built from a fixed https:// prefix, never from caller-supplied schemes.
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        info: dict[str, Any] = json.load(response)["info"]
    return info


def check(
    releases: Iterable[tuple[str, str, str]],
    policy: Policy,
    fetch: Callable[[str, str], Mapping[str, Any]] | None = None,
    workers: int = 8,
) -> list[Verdict]:
    releases = list(releases)
    fetch_one = fetch or fetch_info
    with ThreadPoolExecutor(max_workers=workers) as pool:
        infos = list(pool.map(lambda r: fetch_one(r[0], r[1]), releases))
    return [
        judge(name, version, scope, info, policy)
        for (name, version, scope), info in zip(releases, infos, strict=True)
    ]


def report(verdicts: list[Verdict]) -> str:
    lines = [f"{'package':32} {'version':14} {'scope':8} {'status':9} license"]
    for v in verdicts:
        status = "exception" if v.status != ALLOWED and v.exception else v.status
        lines.append(f"{v.name:32} {v.version:14} {v.scope:8} {status:9} {v.expression}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lock", type=Path, default=REPO_ROOT / "uv.lock")
    parser.add_argument("--pyproject", type=Path, default=REPO_ROOT / "pyproject.toml")
    args = parser.parse_args(argv)

    lock = tomllib.loads(args.lock.read_text(encoding="utf-8"))
    policy = load_policy(tomllib.loads(args.pyproject.read_text(encoding="utf-8")))
    try:
        verdicts = check(locked_releases(lock), policy)
    except OSError as exc:
        print(f"could not fetch license metadata from PyPI: {exc}", file=sys.stderr)
        return 2

    print(report(verdicts))
    failures = [v for v in verdicts if not v.ok]
    used = {v.name for v in verdicts if v.exception}
    stale = [p for p in policy.exceptions if not any(fnmatch.fnmatchcase(n, p) for n in used)]
    for pattern in stale:
        print(f"warning: license exception {pattern!r} no longer matches a package that needs it")
    if failures:
        print(f"\n{len(failures)} package(s) violate the license policy:", file=sys.stderr)
        for v in failures:
            print(
                f"  {v.name} {v.version} ({v.scope}): {v.expression} -> {v.status}", file=sys.stderr
            )
        return 1
    print(f"\nlicense policy: {len(verdicts)} releases checked, no violations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
