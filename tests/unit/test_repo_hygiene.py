"""Secrets, weights and real data stay out of git; license and independence notices exist."""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GIT = shutil.which("git")
APACHE_2_0_SHA256 = "a6cba85bc92e0cff7a450b1d873c0eaa2e9fc96bf472df0247a26bec77bf3ff9"

MUST_BE_IGNORED = [
    ".env",
    ".env.local",
    ".env.production",
    "secrets/api.txt",
    "credentials.json",
    "service-account-prod.json",
    "server.pem",
    ".huggingface/token",
    "model.safetensors",
    "checkpoints/epoch1/pytorch_model.bin",
    "export/laya.onnx",
    "weights/specialist.pt",
    "models/laya-support/config.json",
    "data/tickets.csv",
    "datasets/v1/train.jsonl",
    "exports/audit.parquet",
    "calibration/support.json",
    "evals/run.records.jsonl",
    "audit.sqlite",
]
MUST_BE_TRACKABLE = [
    ".env.example",
    "pyproject.toml",
    "uv.lock",
    "src/laya_platform/cli.py",
    "tests/unit/test_cli.py",
    "docs/DEVELOPMENT.md",
]
FORBIDDEN_TRACKED = re.compile(
    r"(^|/)\.env$|(^|/)\.env\.(?!example$)[^/]+$"
    r"|\.(safetensors|onnx|pt|pth|ckpt|gguf|bin|parquet|sqlite3?|db|pem|key|p12|pfx)$"
)
SECRET_NAME = re.compile(r"TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|PRIVATE_KEY|CREDENTIAL", re.I)


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    assert GIT is not None
    return subprocess.run(  # noqa: S603 -- fixed argv, no shell
        [GIT, *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False, timeout=30
    )


requires_git = pytest.mark.skipif(
    GIT is None or not (REPO_ROOT / ".git").exists(), reason="needs a git checkout"
)


@requires_git
@pytest.mark.parametrize("path", MUST_BE_IGNORED)
def test_sensitive_paths_are_ignored(path: str) -> None:
    assert _git("check-ignore", "--no-index", "-q", path).returncode == 0, path


@requires_git
@pytest.mark.parametrize("path", MUST_BE_TRACKABLE)
def test_project_files_are_not_ignored(path: str) -> None:
    assert _git("check-ignore", "--no-index", "-q", path).returncode == 1, path


@requires_git
def test_no_secrets_weights_or_data_are_tracked() -> None:
    tracked = _git("ls-files").stdout.splitlines()
    assert tracked, "git ls-files returned nothing"
    offenders = [path for path in tracked if FORBIDDEN_TRACKED.search(path)]
    assert not offenders


def test_env_example_holds_no_secret_values() -> None:
    for number, raw in enumerate(
        (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, value = line.partition("=")
        assert sep, f".env.example:{number}: not a NAME=value line"
        if SECRET_NAME.search(name):
            assert value == "", f".env.example:{number}: {name} must be empty"


def test_license_is_the_unmodified_apache_2_0_text() -> None:
    digest = hashlib.sha256((REPO_ROOT / "LICENSE").read_bytes()).hexdigest()
    assert digest == APACHE_2_0_SHA256


def test_notice_credits_laya_and_states_independence() -> None:
    notice = (REPO_ROOT / "NOTICE").read_text(encoding="utf-8")
    assert "https://github.com/NandhaKishorM/laya" in notice
    assert "Apache License" in notice
    assert "not affiliated" in notice


def test_readme_states_independence() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "não é afiliado" in readme.lower()
