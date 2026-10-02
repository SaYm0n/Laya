"""Specialist Registry (Block C, F7): versions, lifecycle with objective gates, per-spec pointers
with rollback, and the selector the gateway asks which System-1 answers."""

from laya_platform.registry.lifecycle import ALLOWED, STATES, Gates, engine_label
from laya_platform.registry.manifest import SpecialistManifest, load_manifest, write_manifest
from laya_platform.registry.registry import Pointer, RegistryError, SpecialistRegistry
from laya_platform.registry.selector import ROUTER, Choice, SpecialistSelector, load_specialist

__all__ = [
    "ALLOWED",
    "ROUTER",
    "STATES",
    "Choice",
    "Gates",
    "Pointer",
    "RegistryError",
    "SpecialistManifest",
    "SpecialistRegistry",
    "SpecialistSelector",
    "engine_label",
    "load_manifest",
    "load_specialist",
    "write_manifest",
]
