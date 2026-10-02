from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
from importlib.metadata import entry_points
from pathlib import Path

import pytest
import yaml

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


# ---------------------------------------------------------------------------------- subcommands
REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = REPO_ROOT / "examples" / "support_triage"


def test_importing_the_cli_loads_no_subcommand_runtime() -> None:
    heavy = ("laya", "fastapi", "uvicorn", "sqlalchemy", "alembic", "prometheus_client")
    code = f"import sys, laya_platform.cli\nprint([m for m in {heavy!r} if m in sys.modules])\n"
    result = subprocess.run(  # noqa: S603 -- fixed argv, no shell
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=60
    )
    assert result.stdout.strip() == "[]"


def test_hash_key_reads_the_key_from_stdin(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("my-key\n"))
    assert cli.main(["hash-key"]) == 0
    assert capsys.readouterr().out.strip() == hashlib.sha256(b"my-key").hexdigest()
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert cli.main(["hash-key"]) == 2


def test_hash_key_can_generate_a_key(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["hash-key", "--generate"]) == 0
    key_line, hash_line = capsys.readouterr().out.splitlines()
    key = key_line.removeprefix("key:").strip()
    assert len(key) >= 32
    assert hash_line.removeprefix("sha256:").strip() == hashlib.sha256(key.encode()).hexdigest()


def test_eval_then_bands_on_the_example(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from laya_platform.core.adapters import FakeAnswer, FakeEngine
    from laya_platform.gateway.settings import EngineConfig

    engine = FakeEngine({"department": FakeAnswer("billing", 0.9)})
    monkeypatch.setattr(EngineConfig, "build", lambda self: engine)
    out = tmp_path / "report"
    assert (
        cli.main(
            [
                "eval",
                "--spec",
                str(EXAMPLE / "specs" / "support_triage.yaml"),
                "--data",
                str(EXAMPLE / "eval_ptbr.jsonl"),
                "--out",
                str(out),
            ]
        )
        == 0
    )
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["identity"]["engine"] == "router {}"
    assert (
        (out / "report.md").read_text(encoding="utf-8").startswith(f"# Evaluation {report['id']}")
    )
    assert report["id"] in capsys.readouterr().out

    report_path = str(out / "report.json")
    args = ["bands", "--report", report_path, "--error-cost", "5", "--review-cost", "1"]
    assert cli.main([*args, "--question", "department"]) == 0
    printed = capsys.readouterr().out
    policy = yaml.safe_load(printed)["policy"]
    assert policy["calibration_ref"] == report["id"]
    assert policy["bands"][-1] == {"outcome": "review"}
    assert printed.startswith("# threshold=")


def test_eval_takes_the_engine_block_of_a_settings_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from laya_platform.core.adapters import FakeEngine
    from laya_platform.gateway.settings import EngineConfig

    built: list[EngineConfig] = []

    def build(self: EngineConfig) -> FakeEngine:
        built.append(self)
        return FakeEngine()

    monkeypatch.setattr(EngineConfig, "build", build)
    settings = tmp_path / "gateway.yaml"
    settings.write_text("engine:\n  kind: router\n  router: {default: multilingual}\n", "utf-8")
    spec, data = EXAMPLE / "specs" / "support_triage.yaml", EXAMPLE / "eval_ptbr.jsonl"
    common = ["eval", "--spec", str(spec), "--data", str(data), "--out", str(tmp_path / "r")]
    assert cli.main([*common, "--engine-config", str(settings)]) == 0
    assert cli.main([*common, "--remote-url", "http://127.0.0.1:9"]) == 0
    assert built[0].router == {"default": "multilingual"}
    assert (built[1].kind, built[1].remote_url) == ("remote", "http://127.0.0.1:9")


def test_errors_are_reported_without_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = str(tmp_path / "missing.yaml")
    args = ["eval", "--spec", missing, "--data", missing, "--out", str(tmp_path)]
    assert cli.main(args) == 1
    assert capsys.readouterr().err.startswith("laya-platform: error: ")


def test_db_upgrade_creates_the_audit_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    url = f"sqlite:///{tmp_path / 'audit.db'}"
    assert cli.main(["db", "upgrade", "--url", url]) == 0
    assert "at head" in capsys.readouterr().out
    assert (tmp_path / "audit.db").is_file()


# ------------------------------------------------------------------------------- Block C commands
def _db(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'ops.db'}"


def test_db_purge_and_forget(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    url = _db(tmp_path)
    assert cli.main(["db", "purge", "--url", url, "--older-than-days", "30"]) == 0
    assert json.loads(capsys.readouterr().out)["deleted"]["audit_events"] == 0
    assert cli.main(["db", "purge", "--url", url, "--older-than-days", "0"]) == 1
    assert cli.main(["db", "forget", "--url", url, "--hmac", "a" * 64]) == 0
    assert json.loads(capsys.readouterr().out)["inputs"] == 1
    assert cli.main(["db", "forget", "--url", url]) == 1


def test_dataset_split_from_the_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = tmp_path / "data.jsonl"
    rows = [
        {"state": f"s{i}", "expected": {"churn_risk": True}, "group": f"g{i}"} for i in range(20)
    ]
    data.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    spec = str(EXAMPLE / "specs" / "support_triage.yaml")
    out = tmp_path / "splits"
    args = ["dataset", "split", "--spec", spec, "--data", str(data), "--out", str(out)]
    assert cli.main(args) == 0
    counts = json.loads(capsys.readouterr().out)
    assert sum(counts.values()) == 20
    assert (out / "manifest.json").is_file()


def test_dataset_build_needs_the_hmac_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("LAYA_PLATFORM_HMAC_KEY", raising=False)
    spec = str(EXAMPLE / "specs" / "support_triage.yaml")
    args = [
        "dataset",
        "build",
        "--db-url",
        _db(tmp_path),
        "--spec",
        spec,
        "--inputs",
        str(tmp_path / "in.jsonl"),
        "--out",
        str(tmp_path / "out.jsonl"),
    ]
    assert cli.main(args) == 1
    assert "LAYA_PLATFORM_HMAC_KEY" in capsys.readouterr().err


def test_specialist_registry_from_the_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = tmp_path / "specialist.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "name": "examples.triage_pt",
                "version": "1",
                "base": "multilingual",
                "source": "/models/x",
                "decision_specs": ["examples.support_triage"],
            }
        ),
        encoding="utf-8",
    )
    url = _db(tmp_path)
    register = [
        "specialist",
        "register",
        "--db-url",
        url,
        "--manifest",
        str(manifest),
        "--actor",
        "ml",
    ]
    assert cli.main(register) == 0
    assert cli.main(register) == 1  # already registered
    capsys.readouterr()
    assert cli.main(["specialist", "list", "--db-url", url]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["versions"][0]["status"] == "experimental"
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"identity": {}, "overall": {}}), encoding="utf-8")
    shadow = [
        "specialist",
        "shadow",
        "--db-url",
        url,
        "--name",
        "examples.triage_pt",
        "--version",
        "1",
        "--spec",
        str(EXAMPLE / "specs" / "support_triage.yaml"),
        "--report",
        str(report),
        "--actor",
        "ml",
    ]
    assert cli.main(shadow) == 1
    assert "cannot enter shadow" in capsys.readouterr().err


def test_train_refuses_a_recipe_that_is_not_the_pinned_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from laya_platform.core import upstream_compat

    fake = tmp_path / "upstream" / upstream_compat.FINETUNE_SCRIPT.path
    fake.parent.mkdir(parents=True)
    fake.write_text("print('not it')\n")
    args = [
        "train",
        "--spec",
        str(EXAMPLE / "specs" / "support_triage.yaml"),
        "--data",
        str(tmp_path / "train.jsonl"),
        "--base-dir",
        str(tmp_path / "base"),
        "--upstream-dir",
        str(tmp_path / "upstream"),
        "--out",
        str(tmp_path / "out"),
        "--name",
        "examples.triage_pt",
        "--version",
        "1",
    ]
    code = cli.main(args)
    err = capsys.readouterr().err
    assert code == 1
    assert "not the pinned" in err  # checked before anything is imported
