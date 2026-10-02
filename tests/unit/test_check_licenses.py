from __future__ import annotations

import tomllib
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def lic(load_script: Callable[[str], ModuleType]) -> ModuleType:
    return load_script("check_licenses")


@pytest.fixture(scope="module")
def policy(lic: ModuleType) -> Any:
    return lic.Policy(
        allowed=frozenset({"MIT", "Apache-2.0", "BSD-3-Clause"}),
        denied=("GPL", "AGPL"),
        exceptions={"nvidia-*": "CUDA runtime, reviewed", "odd-pkg": "dev-only, verified MIT"},
    )


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("MIT", "allowed"),
        ("GPL-3.0-only", "denied"),
        ("LGPL-2.1-or-later", "denied"),
        ("AGPL-3.0", "denied"),
        ("MIT OR GPL-3.0", "allowed"),
        ("MIT AND GPL-3.0", "denied"),
        ("Apache-2.0 WITH LLVM-exception", "allowed"),
        ("(MIT OR GPL-3.0) AND BSD-3-Clause", "allowed"),
        ("MIT AND LicenseRef-Proprietary", "unknown"),
        ("UNKNOWN", "unknown"),
    ],
)
def test_expression_evaluation(
    lic: ModuleType, policy: Any, expression: str, expected: str
) -> None:
    assert lic.evaluate_expression(expression, policy) == expected


@pytest.mark.parametrize("expression", ["MIT AND", "(MIT", "MIT)"])
def test_malformed_expressions_are_rejected(lic: ModuleType, policy: Any, expression: str) -> None:
    with pytest.raises(ValueError, match=r"license expression|parentheses"):
        lic.evaluate_expression(expression, policy)


@pytest.mark.parametrize(
    ("info", "expected"),
    [
        ({"license_expression": "MIT", "license": "GPL"}, "MIT"),
        ({"license": "Apache 2.0", "classifiers": []}, "Apache-2.0"),
        ({"license": "BSD 2-Clause License"}, "BSD-2-Clause"),
        ({"license": "Permission is hereby granted, free of charge, to any person"}, "MIT"),
        ({"license": "Apache License\n   Version 2.0, January 2004"}, "Apache-2.0"),
        ({"license": "", "classifiers": ["License :: OSI Approved :: MIT License"]}, "MIT"),
        (
            {
                "classifiers": [
                    "License :: OSI Approved :: MIT License",
                    "License :: OSI Approved :: Apache Software License",
                ]
            },
            "Apache-2.0 OR MIT",
        ),
        ({"license": "", "classifiers": []}, "UNKNOWN"),
        ({"license": "a very long custom license text " * 5}, "UNKNOWN"),
    ],
)
def test_metadata_resolution_order(lic: ModuleType, info: dict[str, Any], expected: str) -> None:
    assert lic.license_expression(info) == expected


def test_exceptions_cover_matching_packages_only(lic: ModuleType, policy: Any) -> None:
    proprietary = {"license_expression": "LicenseRef-NVIDIA-Proprietary"}
    covered = lic.judge("nvidia-cublas", "1.0", "runtime", proprietary, policy)
    uncovered = lic.judge("other-blob", "1.0", "runtime", proprietary, policy)
    assert covered.ok
    assert covered.exception == "CUDA runtime, reviewed"
    assert not uncovered.ok


def test_an_allowed_package_never_reports_an_exception(lic: ModuleType, policy: Any) -> None:
    verdict = lic.judge("odd-pkg", "1.0", "dev", {"license_expression": "MIT"}, policy)
    assert verdict.ok
    assert verdict.exception is None


def test_scope_separates_runtime_from_dev(lic: ModuleType) -> None:
    lock = {
        "package": [
            {
                "name": "app",
                "version": "0",
                "source": {"editable": "."},
                "dependencies": [{"name": "laya"}],
                "dev-dependencies": {"dev": [{"name": "pytest"}]},
            },
            {
                "name": "laya",
                "version": "1",
                "source": {"registry": "x"},
                "dependencies": [{"name": "numpy"}],
            },
            {"name": "numpy", "version": "2", "source": {"registry": "x"}},
            {"name": "pytest", "version": "9", "source": {"registry": "x"}},
        ]
    }
    assert lic.locked_releases(lock) == [
        ("laya", "1", "runtime"),
        ("numpy", "2", "runtime"),
        ("pytest", "9", "dev"),
    ]


def test_check_uses_the_injected_fetcher(lic: ModuleType, policy: Any) -> None:
    infos = {"a": {"license_expression": "MIT"}, "b": {"license": "GPL-3.0"}}
    verdicts = lic.check(
        [("a", "1", "runtime"), ("b", "1", "dev")], policy, fetch=lambda n, v: infos[n]
    )
    assert [(v.name, v.ok) for v in verdicts] == [("a", True), ("b", False)]


def test_project_policy_is_complete_and_every_exception_is_justified(lic: ModuleType) -> None:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project_policy = lic.load_policy(pyproject)
    assert {"MIT", "Apache-2.0", "BSD-3-Clause"} <= project_policy.allowed
    assert {"GPL", "AGPL", "SSPL"} <= set(project_policy.denied)
    assert all(len(reason) > 20 for reason in project_policy.exceptions.values())


def test_every_locked_release_has_a_scope(lic: ModuleType) -> None:
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    releases = lic.locked_releases(lock)
    scopes = {name: scope for name, _, scope in releases}
    assert scopes["laya"] == "runtime"
    assert scopes["torch"] == "runtime"
    assert scopes["pytest"] == "dev"


@pytest.mark.network
def test_the_real_lock_passes_the_policy_against_pypi(lic: ModuleType) -> None:
    assert lic.main([]) == 0
