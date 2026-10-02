"""SpecialistSelector: which System-1 answers a spec, and which runs beside it.

The upstream ``laya.Router`` knows three checkpoint names only, so specialists are chosen above
it: the spec's ``production`` pointer answers (loaded with ``laya.load`` through
``AgentEngine``), otherwise the Router does. A ``shadow`` pointer is a challenger the gateway
runs after answering. Pointers are re-read every ``refresh_s`` seconds, so a promotion or a
rollback takes effect without a deploy; loaded checkpoints are kept in a small LRU.

A specialist that fails to load never blocks a decision: the Router answers and the failure is
reported (``load_errors``, ``/ready``).
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

from laya_platform.core.engine import DecisionEngine
from laya_platform.registry.manifest import SpecialistManifest
from laya_platform.registry.registry import Pointer, SpecialistRegistry

ROUTER = "router"
log = logging.getLogger(__name__)
EngineLoader = Callable[[SpecialistManifest], DecisionEngine]


def load_specialist(manifest: SpecialistManifest) -> DecisionEngine:
    """``laya.load`` with the manifest's revision, digests and calibration (needs torch)."""
    from laya_platform.core.adapters import AgentEngine

    return AgentEngine.from_checkpoint(manifest.source, **manifest.load_options())


@dataclass(frozen=True)
class Choice:
    engine: DecisionEngine
    label: str  # "router" or "name@version"

    @property
    def is_specialist(self) -> bool:
        return self.label != ROUTER


class SpecialistSelector:
    def __init__(
        self,
        registry: SpecialistRegistry,
        default: DecisionEngine,
        *,
        loader: EngineLoader = load_specialist,
        refresh_s: float = 5.0,
        max_loaded: int = 4,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.registry = registry
        self.default = default
        self._loader = loader
        self._refresh_s = refresh_s
        self._max_loaded = max_loaded
        self._clock = clock
        self._lock = threading.Lock()
        self._pointers: dict[str, dict[str, Pointer]] = {}
        self._read_at: float | None = None
        self._engines: OrderedDict[str, DecisionEngine] = OrderedDict()
        self.load_errors: dict[str, str] = {}

    def served(self, spec_id: str) -> Choice:
        pointer = self._pointer(spec_id, "production")
        if pointer is not None and (engine := self._engine(pointer)) is not None:
            return Choice(engine, pointer.key)
        return Choice(self.default, ROUTER)

    def challenger(self, spec_id: str) -> Choice | None:
        pointer = self._pointer(spec_id, "shadow")
        if pointer is None or (engine := self._engine(pointer)) is None:
            return None
        return Choice(engine, pointer.key)

    def refresh(self) -> None:
        """Re-read the pointers now (after a promotion or a rollback through the gateway)."""
        with self._lock:
            self._read_at = None

    # ------------------------------------------------------------------------------ internals
    def _pointer(self, spec_id: str, role: str) -> Pointer | None:
        with self._lock:
            now = self._clock()
            if self._read_at is None or now - self._read_at >= self._refresh_s:
                self._pointers = self.registry.pointers()
                self._read_at = now
            return self._pointers.get(spec_id, {}).get(role)

    def _engine(self, pointer: Pointer) -> DecisionEngine | None:
        with self._lock:
            if pointer.key in self._engines:
                self._engines.move_to_end(pointer.key)
                return self._engines[pointer.key]
            if pointer.key in self.load_errors:
                return None
            found = self.registry.get(pointer.name, pointer.version)
            try:
                if found is None:
                    raise LookupError("not registered")
                engine = self._loader(found[0])
            except Exception as exc:  # noqa: BLE001 -- the Router answers instead; reported
                self.load_errors[pointer.key] = f"{type(exc).__name__}: {exc}"
                log.error("specialist %s did not load: %s", pointer.key, exc)
                return None
            self._engines[pointer.key] = engine
            while len(self._engines) > self._max_loaded:
                self._engines.popitem(last=False)
            return engine
