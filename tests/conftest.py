"""Test categories, opt-in gates and the offline guard.

Every test belongs to exactly one *base* category, taken from its directory:

    tests/unit/           -> unit
    tests/compatibility/  -> compatibility

Some tests also need resources the default run must never touch. They carry a *gated* marker and
are skipped unless explicitly enabled:

    network  needs outbound network access                       --run-network
    weights  needs real Laya checkpoints (implies network)        --run-weights
    gpu      needs a CUDA/XPU/MPS device                          --run-gpu
    llm      calls an external LLM provider (implies network)    --run-llm

One more marker states a *runtime* requirement instead of a resource. It is not opt-in: the test
runs whenever the runtime is installed and is skipped (with the reason) when it is not.

    torch    needs the torch runtime but no weights: skipped in the light profile, run by the
             full install (the Full install workflow)

``weights`` and ``gpu`` imply ``torch``: once enabled they still skip, with the reason, when torch
is not installed.

The default run is offline and deterministic: outbound connections from tests without a network
permission raise immediately, and the Hugging Face libraries are put in offline mode.
"""

from __future__ import annotations

import importlib.util
import os
import socket
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

BASE_CATEGORIES: dict[str, str] = {
    "unit": "fast, offline, deterministic tests of this repository's own code",
    "compatibility": "contract tests against the pinned upstream laya release",
}
GATED_CATEGORIES: dict[str, str] = {
    "network": "needs outbound network access",
    "weights": "needs real Laya checkpoints (implies network)",
    "gpu": "needs a CUDA/XPU/MPS device",
    "llm": "calls an external LLM provider (implies network)",
}
RUNTIME_CATEGORIES: dict[str, str] = {
    "torch": "needs the torch runtime but no weights (skipped when torch is not installed)",
}
#: Gates whose tests cannot run without a runtime even when enabled (real checkpoints need torch).
IMPLIED_RUNTIMES = {"weights": "torch", "gpu": "torch"}
NETWORK_PERMITTED = frozenset({"network", "weights", "llm"})
OFFLINE_ENV = {
    "HF_HUB_OFFLINE": "1",
    "HF_DATASETS_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
}
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0:0:0:0:0:0:0:1"})


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("laya-platform test categories")
    for name, help_text in GATED_CATEGORIES.items():
        group.addoption(
            f"--run-{name}",
            action="store_true",
            default=False,
            help=f"run tests marked '{name}' ({help_text})",
        )


def _enabled(config: pytest.Config) -> set[str]:
    return {name for name in GATED_CATEGORIES if config.getoption(f"--run-{name}")}


def pytest_configure(config: pytest.Config) -> None:
    for name, help_text in {**BASE_CATEGORIES, **GATED_CATEGORIES, **RUNTIME_CATEGORIES}.items():
        config.addinivalue_line("markers", f"{name}: {help_text}")
    if not _enabled(config) & NETWORK_PERMITTED:
        os.environ.update(OFFLINE_ENV)


def _base_category(path: Path, rootdir: Path) -> str | None:
    try:
        parts = path.relative_to(rootdir).parts
    except ValueError:
        return None
    return next((part for part in parts if part in BASE_CATEGORIES), None)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    rootdir = Path(str(config.rootpath))
    enabled = _enabled(config)
    for item in items:
        category = _base_category(Path(str(item.path)), rootdir)
        if category is None:
            raise pytest.UsageError(
                f"{item.nodeid}: every test must live under one of "
                f"{sorted(BASE_CATEGORIES)} (tests/<category>/...)"
            )
        item.add_marker(getattr(pytest.mark, category))
        missing = sorted(_marker_names(item) & set(GATED_CATEGORIES) - enabled)
        if missing:
            flags = " ".join(f"--run-{name}" for name in missing)
            item.add_marker(
                pytest.mark.skip(reason=f"gated: {', '.join(missing)} (enable {flags})")
            )
        markers = _marker_names(item)
        runtimes = (markers & set(RUNTIME_CATEGORIES)) | {
            runtime for gate, runtime in IMPLIED_RUNTIMES.items() if gate in markers
        }
        absent = sorted(name for name in runtimes if importlib.util.find_spec(name) is None)
        if absent:
            item.add_marker(
                pytest.mark.skip(
                    reason=f"needs {', '.join(absent)}: not installed (light profile); "
                    "run by the full install (`uv sync --locked`, Full install workflow)"
                )
            )


def _marker_names(item: pytest.Item) -> set[str]:
    # Markers only: `item.keywords` also holds parametrize ids, so a case named "network" would
    # otherwise be gated by its id.
    return {marker.name for marker in item.iter_markers()}


def _host_of(address: Any) -> str | None:
    if isinstance(address, tuple) and address:
        return str(address[0])
    return None  # AF_UNIX path or other non-IP address


def _is_loopback(host: str | None) -> bool:
    return host is None or host in _LOOPBACK_HOSTS or host.startswith("127.")


class OfflineViolationError(RuntimeError):
    """Raised when a test without a network permission tries to reach a remote host."""


@pytest.fixture(autouse=True)
def _offline_guard(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    if NETWORK_PERMITTED & _marker_names(request.node):
        yield
        return

    def refuse(host: str | None) -> None:
        raise OfflineViolationError(
            f"{request.node.nodeid} tried to reach {host!r}; default tests are offline. "
            "Mark the test @pytest.mark.network (or weights/llm) if it really needs the network."
        )

    def guard(original: Callable[..., Any]) -> Callable[..., Any]:
        def guarded(self: socket.socket, address: Any, *args: Any, **kwargs: Any) -> Any:
            host = _host_of(address)
            if not _is_loopback(host):
                refuse(host)
            return original(self, address, *args, **kwargs)

        return guarded

    original_getaddrinfo = socket.getaddrinfo

    def guarded_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        name = None if host is None else str(host)
        if not _is_loopback(name):
            refuse(name)
        return original_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guard(socket.socket.connect))
    monkeypatch.setattr(socket.socket, "connect_ex", guard(socket.socket.connect_ex))
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    yield


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"


@pytest.fixture(scope="session")
def load_script() -> Callable[[str], ModuleType]:
    """Import a CI script from scripts/ by name (they are standalone files, not a package)."""

    def load(name: str) -> ModuleType:
        module_name = f"_laya_platform_script_{name}"
        if module_name in sys.modules:
            return sys.modules[module_name]
        spec = importlib.util.spec_from_file_location(module_name, SCRIPTS_DIR / f"{name}.py")
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module  # dataclasses resolve annotations through sys.modules
        spec.loader.exec_module(module)
        return module

    return load
