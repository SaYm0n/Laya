from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from laya_platform import _upstream


@pytest.fixture(scope="module")
def watch(load_script: Callable[[str], ModuleType]) -> ModuleType:
    return load_script("check_upstream")


@pytest.fixture(scope="module")
def pin(watch: ModuleType) -> Any:
    return watch.load_pin()


def _files(*, yanked: bool = False) -> list[dict[str, Any]]:
    return [{"filename": "x.whl", "yanked": yanked}]


RELEASES = {
    "releases": {
        "0.3.22": _files(),
        "0.3.23": _files(),
        "0.3.24": _files(),
        "0.3.25": _files(yanked=True),
        "0.4.0rc1": _files(),
        "0.5.0": [],
        "not-a-version": _files(),
    }
}


def _served(pin: Any, wheel: str | None = None, sdist: str | None = None) -> list[dict[str, Any]]:
    return [
        {"filename": pin.wheel_filename, "digests": {"sha256": wheel or pin.wheel_sha256}},
        {"filename": pin.sdist_filename, "digests": {"sha256": sdist or pin.sdist_sha256}},
    ]


def test_pin_is_read_from_the_package_source(pin: Any) -> None:
    assert pin.version == _upstream.UPSTREAM_VERSION
    assert pin.wheel_sha256 == _upstream.UPSTREAM_WHEEL_SHA256
    assert pin.sdist_sha256 == _upstream.UPSTREAM_SDIST_SHA256


def test_newer_releases_ignore_yanked_empty_invalid_and_prereleases(watch: ModuleType) -> None:
    assert watch.newer_releases(RELEASES, "0.3.23") == ["0.3.24"]


def test_prereleases_can_be_included(watch: ModuleType) -> None:
    assert watch.newer_releases(RELEASES, "0.3.23", include_prereleases=True) == [
        "0.3.24",
        "0.4.0rc1",
    ]


def test_nothing_newer_than_the_latest(watch: ModuleType) -> None:
    assert watch.newer_releases(RELEASES, "0.3.24") == []


def test_matching_hashes_report_no_problem(watch: ModuleType, pin: Any) -> None:
    assert watch.hash_mismatches(pin, _served(pin)) == []


def test_changed_or_missing_artifacts_are_reported(watch: ModuleType, pin: Any) -> None:
    changed = watch.hash_mismatches(pin, _served(pin, wheel="0" * 64))
    missing = watch.hash_mismatches(pin, _served(pin)[1:])
    assert len(changed) == 1
    assert "!= audited" in changed[0]
    assert missing == [f"{pin.wheel_filename} is no longer served by PyPI"]


def test_report_carries_the_upgrade_checklist_only_when_needed(watch: ModuleType, pin: Any) -> None:
    with_newer = watch.render_report(pin, ["0.3.24"], [])
    without = watch.render_report(pin, [], [])
    assert "Upgrade checklist (nothing is updated automatically)" in with_newer
    assert f"{pin.repository}/releases/tag/v0.3.24" in with_newer
    assert "Upgrade checklist" not in without
    assert "No newer release" in without


def test_issue_title_names_latest_and_pin(watch: ModuleType, pin: Any) -> None:
    assert watch.issue_title(pin, ["0.3.24", "0.3.26"]) == (
        f"Upstream laya 0.3.26 available (pinned {pin.version})"
    )


def _fake_pypi(pin: Any, served: list[dict[str, Any]]) -> Callable[[str], dict[str, Any]]:
    def fetch(path: str, timeout: float = 30.0) -> dict[str, Any]:
        if path.endswith(f"/{pin.version}/json"):
            return {"urls": served}
        return RELEASES

    return fetch


def test_main_writes_report_and_github_outputs(
    watch: ModuleType, pin: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outputs = tmp_path / "out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(outputs))
    monkeypatch.setattr(watch, "fetch_json", _fake_pypi(pin, _served(pin)))
    report = tmp_path / "report.md"
    assert watch.main(["--report", str(report)]) == 0
    assert "0.3.24" in report.read_text(encoding="utf-8")
    assert outputs.read_text(encoding="utf-8").splitlines() == [
        "newer=true",
        "latest=0.3.24",
        f"title=Upstream laya 0.3.24 available (pinned {pin.version})",
    ]


def test_main_fails_when_audited_artifacts_change(
    watch: ModuleType, pin: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.setattr(watch, "fetch_json", _fake_pypi(pin, _served(pin, sdist="f" * 64)))
    assert watch.main([]) == 1


def test_main_reports_an_unreachable_index(
    watch: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    def offline(path: str, timeout: float = 30.0) -> dict[str, Any]:
        raise OSError("network unreachable")

    monkeypatch.setattr(watch, "fetch_json", offline)
    assert watch.main([]) == 2


@pytest.mark.network
def test_pypi_still_serves_the_audited_artifacts(watch: ModuleType) -> None:
    assert watch.main([]) == 0
