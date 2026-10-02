"""The category rules in tests/conftest.py: base categories, opt-in gates and the offline guard.

Each case runs an isolated pytest session in a subprocess with the real conftest, so the gate logic
is exercised exactly as CI uses it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

CONFTEST = (Path(__file__).resolve().parents[1] / "conftest.py").read_text(encoding="utf-8")

GATED_FILE = """
import pytest

def test_plain():
    pass

@pytest.mark.network
def test_network():
    pass

@pytest.mark.weights
def test_weights():
    pass

@pytest.mark.gpu
def test_gpu():
    pass

@pytest.mark.llm
def test_llm():
    pass
"""


@pytest.fixture
def session(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> pytest.Pytester:
    for name in ("HF_HUB_OFFLINE", "HF_DATASETS_OFFLINE", "TRANSFORMERS_OFFLINE"):
        monkeypatch.delenv(name, raising=False)
    pytester.makeconftest(CONFTEST)
    return pytester


def _run(pytester: pytest.Pytester, *args: str) -> pytest.RunResult:
    return pytester.runpytest_subprocess("-p", "no:cacheprovider", "--strict-markers", "-rs", *args)


def test_default_run_skips_every_gated_category(session: pytest.Pytester) -> None:
    session.makepyfile(**{"unit/test_gated": GATED_FILE})
    result = _run(session)
    result.assert_outcomes(passed=1, skipped=4)
    result.stdout.fnmatch_lines(["*gated: weights (enable --run-weights)*"])


@pytest.mark.parametrize("category", ["network", "weights", "gpu", "llm"])
def test_each_gate_is_opened_only_by_its_own_flag(session: pytest.Pytester, category: str) -> None:
    session.makepyfile(**{"unit/test_gated": GATED_FILE})
    result = _run(session, f"--run-{category}")
    result.assert_outcomes(passed=2, skipped=3)


def test_base_category_comes_from_the_directory(session: pytest.Pytester) -> None:
    session.makepyfile(**{"compatibility/test_contract": "def test_contract():\n    pass\n"})
    session.makepyfile(**{"unit/test_unit": "def test_unit():\n    pass\n"})
    result = _run(session, "-m", "compatibility")
    result.assert_outcomes(passed=1, deselected=1)


def test_a_test_outside_the_categories_is_refused(session: pytest.Pytester) -> None:
    session.makepyfile(**{"misc/test_orphan": "def test_orphan():\n    pass\n"})
    result = _run(session)
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*every test must live under one of*"])


def test_default_run_blocks_remote_hosts_and_allows_loopback(session: pytest.Pytester) -> None:
    session.makepyfile(
        **{
            "unit/test_sockets": """
import os
import socket

import pytest

def test_remote_is_refused():
    with pytest.raises(RuntimeError, match="default tests are offline"):
        socket.create_connection(("192.0.2.1", 80), timeout=0.1)

def test_dns_is_refused():
    with pytest.raises(RuntimeError, match="default tests are offline"):
        socket.getaddrinfo("example.com", 443)

def test_loopback_is_allowed():
    with socket.create_server(("127.0.0.1", 0)) as server:
        port = server.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            pass

def test_hugging_face_is_offline():
    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert os.environ["TRANSFORMERS_OFFLINE"] == "1"
"""
        }
    )
    _run(session).assert_outcomes(passed=4)


def test_network_marked_tests_are_not_guarded(session: pytest.Pytester) -> None:
    session.makepyfile(
        **{
            "unit/test_net": """
import socket

import pytest

@pytest.mark.network
def test_guard_is_not_installed():
    # No real connection is attempted: the point is only that the guard is not in place.
    assert "guarded" not in socket.socket.connect.__qualname__
    assert "guarded" not in socket.getaddrinfo.__qualname__
"""
        }
    )
    _run(session, "--run-network").assert_outcomes(passed=1)


def test_parametrize_ids_are_not_mistaken_for_gates(session: pytest.Pytester) -> None:
    session.makepyfile(
        **{
            "unit/test_ids": """
import pytest

@pytest.mark.parametrize("name", ["network", "weights", "gpu", "llm"])
def test_case(name):
    pass
"""
        }
    )
    _run(session).assert_outcomes(passed=4)
