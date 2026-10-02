"""Specialist Registry (Block C, F7): manifest, objective gates, lifecycle, pointers, rollback,
and the selector the gateway asks."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from laya_platform.core import DecisionSpec
from laya_platform.core.adapters import FakeEngine
from laya_platform.registry import (
    ROUTER,
    Gates,
    RegistryError,
    SpecialistManifest,
    SpecialistRegistry,
    SpecialistSelector,
    engine_label,
    load_manifest,
    write_manifest,
)
from laya_platform.storage import ChallengerResult, Database

SPEC = DecisionSpec.model_validate(
    {
        "id": "t.triage",
        "version": 1,
        "languages": ["pt"],
        "questions": {"churn": {"type": "noul", "instructions": "Will they cancel?"}},
    }
)


def _manifest(version: str = "1", **overrides: Any) -> SpecialistManifest:
    data = {
        "name": "t.triage_pt",
        "version": version,
        "base": "multilingual",
        "source": "/models/triage_pt",
        "sha256": {"model.safetensors": "a" * 64},
        "decision_specs": ["t.triage"],
        "languages": ["pt"],
    }
    return SpecialistManifest.model_validate(data | overrides)


def _report(manifest: SpecialistManifest, accuracy: float = 0.9, **extra: Any) -> dict[str, Any]:
    report = {
        "id": f"eval-{manifest.version}",
        "identity": {
            "spec": SPEC.id,
            "questions_sha256": SPEC.questions_sha256(),
            "engine": engine_label(manifest),
            "dataset_sha256": "d" * 64,
        },
        "overall": {"accuracy": accuracy, "ece": 0.05},
        "slices": {"language": {"pt": {"accuracy": accuracy}}},
    }
    return report | extra


@pytest.fixture
def database(tmp_path: Path) -> Iterator[Database]:
    database = Database(f"sqlite:///{tmp_path / 'r.db'}")
    database.upgrade()
    yield database
    database.dispose()


@pytest.fixture
def registry(database: Database) -> SpecialistRegistry:
    return SpecialistRegistry(database)


def _shadow_runs(database: Database, manifest: SpecialistManifest, n: int, failed: int = 0) -> None:
    for i in range(n):
        database.record_challenger(
            ChallengerResult(
                trace_id=f"t{i}",
                spec_id=SPEC.id,
                name=manifest.name,
                version=manifest.version,
                agreement=None if i < failed else {"churn": i % 4 != 0},
                error="boom" if i < failed else None,
            )
        )


def _to_production(
    registry: SpecialistRegistry, database: Database, manifest: SpecialistManifest
) -> None:
    registry.register(manifest, "ml")
    registry.to_shadow(manifest.name, manifest.version, SPEC, _report(manifest), "ml")
    _shadow_runs(database, manifest, 10)
    registry.to_candidate(manifest.name, manifest.version, "ml", gates=Gates(min_shadow_samples=10))
    registry.to_production(manifest.name, manifest.version, "head of ops", "approved in review")


# ------------------------------------------------------------------------------------ manifest
def test_a_manifest_round_trips_and_pins_its_files(tmp_path: Path) -> None:
    manifest = _manifest(revision="abc123", calibration="/cal/pt.json")
    write_manifest(manifest, tmp_path / "specialist.yaml")
    assert load_manifest(tmp_path / "specialist.yaml") == manifest
    assert manifest.key == "t.triage_pt@1"
    assert manifest.load_options() == {
        "revision": "abc123",
        "expected_sha256": {"model.safetensors": "a" * 64},
        "calibration": "/cal/pt.json",
    }


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"sha256": {"../escape": "a" * 64}}, "paths inside the checkpoint"),
        ({"sha256": {"/etc/model.safetensors": "a" * 64}}, "paths inside the checkpoint"),
        ({"sha256": {"C:\\model.safetensors": "a" * 64}}, "paths inside the checkpoint"),
        ({"sha256": {"model.safetensors": "short"}}, "pattern"),
        ({"decision_specs": []}, "at least 1"),
        ({"name": "Bad Name"}, "pattern"),
    ],
)
def test_invalid_manifests_are_refused(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        _manifest(**overrides)


# --------------------------------------------------------------------------------- lifecycle
def test_the_full_lifecycle_records_every_step(
    registry: SpecialistRegistry, database: Database
) -> None:
    manifest = _manifest()
    _to_production(registry, database, manifest)
    assert registry.get(manifest.name, manifest.version)[1] == "production"  # type: ignore[index]
    pointers = registry.pointers(SPEC.id)[SPEC.id]
    assert set(pointers) == {"production"}  # the shadow pointer ends at promotion
    assert pointers["production"].key == "t.triage_pt@1"
    steps = [(e["from"], e["to"], e["actor"]) for e in registry.events(manifest.name)]
    assert steps == [
        (None, "experimental", "ml"),
        ("experimental", "shadow", "ml"),
        ("shadow", "candidate", "ml"),
        ("candidate", "production", "head of ops"),
    ]
    candidate_event = registry.events(manifest.name)[2]
    assert candidate_event["evidence"]["t.triage"]["samples"] == 10


def test_shadow_needs_a_report_on_this_spec_and_this_specialist(
    registry: SpecialistRegistry,
) -> None:
    manifest = _manifest()
    registry.register(manifest, "ml")
    other = _report(manifest)
    other["identity"] = other["identity"] | {"engine": "router {}"}
    with pytest.raises(RegistryError, match=r"not t\.triage_pt@1"):
        registry.to_shadow(manifest.name, "1", SPEC, other, "ml")
    changed = _report(manifest)
    changed["identity"] = changed["identity"] | {"questions_sha256": "0" * 64}
    with pytest.raises(RegistryError, match="other questions"):
        registry.to_shadow(manifest.name, "1", SPEC, changed, "ml")
    with pytest.raises(RegistryError, match=r"accuracy 0\.5 < minimum 0\.8"):
        registry.to_shadow(
            manifest.name, "1", SPEC, _report(manifest, 0.5), "ml", gates=Gates(min_accuracy=0.8)
        )
    with pytest.raises(RegistryError, match="ECE"):
        registry.to_shadow(
            manifest.name, "1", SPEC, _report(manifest), "ml", gates=Gates(max_ece=0.01)
        )


def test_shadow_refuses_a_regression_against_the_baseline(registry: SpecialistRegistry) -> None:
    manifest = _manifest()
    registry.register(manifest, "ml")
    baseline = _report(_manifest("0"), 0.92)
    with pytest.raises(RegistryError, match=r"overall: accuracy 0\.9000 below baseline 0\.9200"):
        registry.to_shadow(
            manifest.name, "1", SPEC, _report(manifest, 0.9), "ml", baseline=baseline
        )
    elsewhere = _report(_manifest("0"), 0.5)
    elsewhere["identity"] = elsewhere["identity"] | {"dataset_sha256": "e" * 64}
    with pytest.raises(RegistryError, match="another dataset"):
        registry.to_shadow(manifest.name, "1", SPEC, _report(manifest), "ml", baseline=elsewhere)
    registry.to_shadow(
        manifest.name,
        "1",
        SPEC,
        _report(manifest, 0.9),
        "ml",
        baseline=baseline,
        gates=Gates(max_regression=0.05),
    )


def test_candidate_needs_enough_healthy_shadow_runs(
    registry: SpecialistRegistry, database: Database
) -> None:
    manifest = _manifest()
    registry.register(manifest, "ml")
    registry.to_shadow(manifest.name, "1", SPEC, _report(manifest), "ml")
    _shadow_runs(database, manifest, 5, failed=1)
    with pytest.raises(RegistryError, match="5 shadow decisions < 10") as exc:
        registry.to_candidate(manifest.name, "1", "ml", gates=Gates(min_shadow_samples=10))
    assert "1 of 5 shadow runs failed" in str(exc.value)


def test_production_needs_a_named_approver(
    registry: SpecialistRegistry, database: Database
) -> None:
    manifest = _manifest()
    registry.register(manifest, "ml")
    registry.to_shadow(manifest.name, "1", SPEC, _report(manifest), "ml")
    _shadow_runs(database, manifest, 3)
    registry.to_candidate(manifest.name, "1", "ml", gates=Gates(min_shadow_samples=3))
    with pytest.raises(RegistryError, match="named approver"):
        registry.to_production(manifest.name, "1", " ", "")


def test_moves_outside_the_lifecycle_are_refused(registry: SpecialistRegistry) -> None:
    manifest = _manifest()
    registry.register(manifest, "ml")
    with pytest.raises(RegistryError, match="cannot go from experimental to production"):
        registry.to_production(manifest.name, "1", "boss", "skip the gates")
    with pytest.raises(RegistryError, match="already registered"):
        registry.register(manifest, "ml")
    with pytest.raises(RegistryError, match="not registered"):
        registry.to_candidate("t.nope", "1", "ml")


def test_a_new_version_replaces_and_rollback_restores_it(
    registry: SpecialistRegistry, database: Database
) -> None:
    first, second = _manifest("1"), _manifest("2")
    _to_production(registry, database, first)
    _to_production(registry, database, second)
    assert registry.pointers(SPEC.id)[SPEC.id]["production"].key == "t.triage_pt@2"
    assert registry.get(first.name, "1")[1] == "deprecated"  # type: ignore[index]
    restored = registry.rollback(SPEC.id, "oncall", "regression in production")
    assert restored is not None
    assert restored.key == "t.triage_pt@1"
    assert registry.get(first.name, "1")[1] == "production"  # type: ignore[index]
    assert registry.get(second.name, "2")[1] == "deprecated"  # type: ignore[index]
    assert registry.rollback(SPEC.id, "oncall", "again") is None  # back to the Router
    assert registry.pointers(SPEC.id) == {}
    with pytest.raises(RegistryError, match="no production specialist"):
        registry.rollback(SPEC.id, "oncall", "nothing left")


def test_deprecating_a_shadow_stops_its_challenger(registry: SpecialistRegistry) -> None:
    manifest = _manifest()
    registry.register(manifest, "ml")
    registry.to_shadow(manifest.name, "1", SPEC, _report(manifest), "ml")
    registry.deprecate(manifest.name, "1", "ml", "worse than expected")
    assert registry.pointers(SPEC.id) == {}


# ---------------------------------------------------------------------------------- selector
def test_the_selector_serves_production_and_challenges_with_shadow(
    registry: SpecialistRegistry, database: Database
) -> None:
    router, loaded = FakeEngine(), {}

    def loader(manifest: SpecialistManifest) -> FakeEngine:
        loaded[manifest.key] = FakeEngine()
        return loaded[manifest.key]

    now = [0.0]
    selector = SpecialistSelector(registry, router, loader=loader, clock=lambda: now[0])
    assert selector.served(SPEC.id).label == ROUTER
    assert selector.challenger(SPEC.id) is None
    first = _manifest("1")
    _to_production(registry, database, first)
    assert selector.served(SPEC.id).label == ROUTER  # pointers are cached for refresh_s
    now[0] = 6.0
    served = selector.served(SPEC.id)
    assert (served.label, served.is_specialist) == ("t.triage_pt@1", True)
    assert served.engine is loaded["t.triage_pt@1"]
    second = _manifest("2")
    registry.register(second, "ml")
    registry.to_shadow(second.name, "2", SPEC, _report(second), "ml")
    selector.refresh()
    challenger = selector.challenger(SPEC.id)
    assert challenger is not None
    assert challenger.label == "t.triage_pt@2"
    assert selector.served(SPEC.id).engine is served.engine  # loaded once


def test_a_specialist_that_fails_to_load_leaves_the_router_answering(
    registry: SpecialistRegistry, database: Database
) -> None:
    def broken(manifest: SpecialistManifest) -> FakeEngine:
        raise OSError("weights missing")

    _to_production(registry, database, _manifest())
    selector = SpecialistSelector(registry, FakeEngine(), loader=broken)
    assert selector.served(SPEC.id).label == ROUTER
    assert selector.load_errors == {"t.triage_pt@1": "OSError: weights missing"}
