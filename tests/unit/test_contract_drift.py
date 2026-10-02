"""An incompatible upstream release fails the compatibility suite (F2 exit criterion).

A copy of the installed ``laya`` package is edited the way an incompatible release could change
it, put first on ``PYTHONPATH``, and the contract tests that guard each change run against it in a
fresh interpreter. Each edit must turn its guard red; a guard that stays green would mean an
upgrade could change that contract without CI noticing.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPAT = "tests/compatibility"

#: (file, original text, incompatible text, contract tests that must fail)
DRIFTS = [
    (
        "serve.py",
        "MAX_QUESTIONS = 64",
        "MAX_QUESTIONS = 65",
        [
            f"{COMPAT}/test_python_api_contract.py::test_http_limits",
            f"{COMPAT}/test_http_wire_contract.py::test_limits[questions]",
        ],
    ),
    (
        "router.py",
        '"en": "english", ',
        '"en": "english", "pt": "multilingual", ',
        [f"{COMPAT}/test_routing_contract.py::test_only_three_checkpoints_exist[pt]"],
    ),
    (
        "confidence.py",
        "GATE_STATES = (GATE_PASSED, GATE_ABSTAINED, GATE_UNEVALUATED)",
        'GATE_STATES = (GATE_PASSED, GATE_ABSTAINED, GATE_UNEVALUATED, "skipped")',
        [f"{COMPAT}/test_abstention.py::test_gate_states"],
    ),
    (
        "structured.py",
        "MAX_OPTIONS = 32",
        "MAX_OPTIONS = 64",
        [f"{COMPAT}/test_schema_contract.py::test_limits"],
    ),
    (
        "agent.py",
        "lang: Optional[str] = None,\n                   hooks=None, on_predict_start=None",
        "lang: Optional[str] = None, temperature: float = 1.0,\n"
        "                   hooks=None, on_predict_start=None",
        [f"{COMPAT}/test_python_api_contract.py::test_signature[laya.agent-Agent.system_one]"],
    ),
    (
        "common.py",
        "def collate_items(batch, pad_id: int):",
        "def collate_items(batch, pad_token_id: int):",
        [
            f"{COMPAT}/test_training_internals_contract.py::"
            "test_each_internal_keeps_its_signature[laya.common.collate_items]"
        ],
    ),
]


def _drifted_copy(destination: Path) -> Path:
    spec = importlib.util.find_spec("laya")
    assert spec is not None
    assert spec.origin is not None
    package = destination / "laya"
    shutil.copytree(Path(spec.origin).parent, package, ignore=shutil.ignore_patterns("__pycache__"))
    for file, original, drifted, _ in DRIFTS:
        source = (package / file).read_text(encoding="utf-8")
        assert source.count(original) == 1, f"{file}: the audited text {original!r} moved"
        (package / file).write_text(source.replace(original, drifted), encoding="utf-8")
    return destination


def test_each_contract_change_fails_its_guard(tmp_path: Path) -> None:
    guards = [node for *_, nodes in DRIFTS for node in nodes]
    env = {**os.environ, "PYTHONPATH": str(_drifted_copy(tmp_path / "site"))}
    result = subprocess.run(  # noqa: S603 -- fixed interpreter and arguments
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", "-rf", *guards],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    failed = set(re.findall(r"^FAILED (\S+)", result.stdout, flags=re.MULTILINE))
    assert failed == set(guards), result.stdout[-4000:]
