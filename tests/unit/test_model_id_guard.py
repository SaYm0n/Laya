"""Architecture guard: code depends on provider + tier + capabilities, never on model IDs.

Concrete model IDs are dated references. They may appear in configuration files and in dated
documentation, never in ``src/``, ``scripts/`` or ``tests/`` (this file excepted, because it holds
the pattern itself).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCANNED_DIRS = ("src", "scripts", "tests")
MODEL_ID = re.compile(
    r"\b(?:claude|gpt|gemini|deepseek|llama|mistral|qwen|grok)[-_]?v?\d[\w.\-]*"
    r"|\b(?:claude|gpt|gemini|deepseek)-(?:opus|sonnet|haiku|fable|mythos|pro|flash|chat|reasoner)\b",
    re.IGNORECASE,
)


def _scanned_files() -> list[Path]:
    this_file = Path(__file__).resolve()
    found: list[Path] = []
    for directory in SCANNED_DIRS:
        for path in sorted((REPO_ROOT / directory).rglob("*")):
            if (
                path.is_file()
                and path.suffix in {".py", ".toml", ".yaml", ".yml", ".json"}
                and path.resolve() != this_file
            ):
                found.append(path)
    return found


@pytest.mark.parametrize(
    "text",
    [
        "claude-opus-5-5",
        "claude-sonnet",
        "gpt-6",
        "GPT4o",
        "gemini-3.8-flash",
        "deepseek-v4",
        "deepseek-chat",
        "llama-3.3",
        "qwen2.5",
    ],
)
def test_pattern_recognises_model_ids(text: str) -> None:
    assert MODEL_ID.search(text)


@pytest.mark.parametrize("text", ["provider", "tier", "frontier", "capabilities", "anthropic"])
def test_pattern_ignores_architecture_vocabulary(text: str) -> None:
    assert not MODEL_ID.search(text)


def test_no_model_ids_in_code_scripts_or_tests() -> None:
    offenders = [
        f"{path.relative_to(REPO_ROOT)}:{number}: {match.group(0)}"
        for path in _scanned_files()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        for match in MODEL_ID.finditer(line)
    ]
    message = "concrete model IDs belong in configuration only:\n" + "\n".join(offenders)
    assert not offenders, message
