from __future__ import annotations

import subprocess
import sys
from importlib.metadata import entry_points

import pytest

from laya_platform import __version__, cli
from laya_platform._upstream import UPSTREAM_VERSION


def test_version_flag_prints_package_and_upstream_pin(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out.strip()
    assert out == f"laya-platform {__version__} (upstream laya {UPSTREAM_VERSION})"


def test_version_text_reports_a_missing_upstream(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "installed_upstream_version", lambda: None)
    assert cli.version_text().endswith(
        f"(upstream laya: not installed, expected {UPSTREAM_VERSION})"
    )


def test_version_text_reports_a_mismatched_upstream(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "installed_upstream_version", lambda: "0.0.1")
    assert cli.version_text().endswith(
        f"(upstream laya: installed 0.0.1, expected {UPSTREAM_VERSION})"
    )


def test_no_arguments_prints_help_and_succeeds(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([]) == 0
    out = capsys.readouterr().out
    assert "usage: laya-platform" in out
    assert "not affiliated with Convai Innovations" in out


def test_unknown_argument_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--no-such-flag"])
    assert exc.value.code == 2


def test_module_entry_point_runs_in_a_fresh_interpreter() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "laya_platform", "--version"],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith(f"laya-platform {__version__} ")


def test_console_script_is_declared() -> None:
    scripts = {ep.name: ep.value for ep in entry_points(group="console_scripts")}
    assert scripts.get("laya-platform") == "laya_platform.cli:main"
