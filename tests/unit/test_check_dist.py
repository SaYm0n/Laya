from __future__ import annotations

import io
import tarfile
import tomllib
import zipfile
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

DIST_INFO = "laya_platform-0.1.0.dev0.dist-info"
ENTRY_POINTS = "[console_scripts]\nlaya-platform = laya_platform.cli:main\n"
REPO_ROOT = Path(__file__).resolve().parents[2]
DEPENDENCIES = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
    "dependencies"
]
GOOD_METADATA = """Metadata-Version: 2.4
Name: laya-platform
Version: 0.1.0.dev0
License-Expression: Apache-2.0
Classifier: Private :: Do Not Upload
""" + "".join(f"Requires-Dist: {requirement}\n" for requirement in DEPENDENCIES)


@pytest.fixture(scope="module")
def dist(load_script: Callable[[str], ModuleType]) -> ModuleType:
    return load_script("check_dist")


def _wheel(path: Path, metadata: str = GOOD_METADATA, extra: tuple[str, ...] = ()) -> Path:
    members = {
        "laya_platform/__init__.py": "",
        "laya_platform/py.typed": "",
        f"{DIST_INFO}/METADATA": metadata,
        f"{DIST_INFO}/entry_points.txt": ENTRY_POINTS,
        f"{DIST_INFO}/licenses/LICENSE": "Apache",
        f"{DIST_INFO}/licenses/NOTICE": "notice",
        **dict.fromkeys(extra, ""),
    }
    with zipfile.ZipFile(path, "w") as wheel:
        for name, content in members.items():
            wheel.writestr(name, content)
    return path


def _sdist(path: Path, extra: tuple[str, ...] = ()) -> Path:
    root = "laya_platform-0.1.0.dev0"
    names = ("pyproject.toml", "LICENSE", "NOTICE", "src/laya_platform/__init__.py", *extra)
    with tarfile.open(path, "w:gz") as sdist:
        for name in names:
            info = tarfile.TarInfo(f"{root}/{name}")
            sdist.addfile(info, io.BytesIO(b""))
    return path


def test_a_correct_wheel_and_sdist_pass(dist: ModuleType, tmp_path: Path) -> None:
    _wheel(tmp_path / "laya_platform-0.1.0.dev0-py3-none-any.whl")
    _sdist(tmp_path / "laya_platform-0.1.0.dev0.tar.gz")
    assert dist.main([str(tmp_path)]) == 0


def test_an_unpinned_or_extra_dependency_fails(dist: ModuleType, tmp_path: Path) -> None:
    metadata = GOOD_METADATA + "Requires-Dist: torch\n"
    problems = dist.check_wheel(_wheel(tmp_path / "w.whl", metadata=metadata))
    assert any("Requires-Dist" in p for p in problems)


def test_the_wheel_must_declare_the_pyproject_dependencies(
    dist: ModuleType, tmp_path: Path
) -> None:
    assert dist.declared_dependencies() == DEPENDENCIES
    wheel = _wheel(tmp_path / "w.whl")
    assert dist.check_wheel(wheel) == []
    assert dist.check_wheel(wheel, expected=[*DEPENDENCIES, "rich"]) != []


def test_the_upstream_pin_is_required_even_if_pyproject_drops_it(
    dist: ModuleType, tmp_path: Path
) -> None:
    metadata = GOOD_METADATA.replace("Requires-Dist: laya==0.3.23\n", "Requires-Dist: laya>=0.3\n")
    unpinned = [d if d != "laya==0.3.23" else "laya>=0.3" for d in DEPENDENCIES]
    problems = dist.check_wheel(_wheel(tmp_path / "w.whl", metadata=metadata), expected=unpinned)
    assert problems == ["w.whl: Requires-Dist must pin laya==0.3.23"]


def test_a_wheel_without_the_no_upload_classifier_fails(dist: ModuleType, tmp_path: Path) -> None:
    metadata = GOOD_METADATA.replace("Classifier: Private :: Do Not Upload\n", "")
    problems = dist.check_wheel(_wheel(tmp_path / "w.whl", metadata=metadata))
    assert any("Do Not Upload" in p for p in problems)


@pytest.mark.parametrize(
    "member", ["tests/test_x.py", "laya_platform/model.safetensors", "laya_platform/.env"]
)
def test_forbidden_members_fail(dist: ModuleType, tmp_path: Path, member: str) -> None:
    problems = dist.check_wheel(_wheel(tmp_path / "w.whl", extra=(member,)))
    assert any(member in p for p in problems)


def test_an_sdist_with_secrets_or_missing_notice_fails(dist: ModuleType, tmp_path: Path) -> None:
    problems = dist.check_sdist(_sdist(tmp_path / "s.tar.gz", extra=(".env",)))
    assert any(".env" in p for p in problems)


def test_a_missing_artifact_fails(dist: ModuleType, tmp_path: Path) -> None:
    _wheel(tmp_path / "laya_platform-0.1.0.dev0-py3-none-any.whl")
    assert dist.main([str(tmp_path)]) == 1
