"""The gateway with Block C: specialists answer and challenge, rollback without deploy, and the
operational quick wins (flag authorship, review-queue gauges, System-1 x System-2 agreement, an
LLM budget shared through the database)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import SecretStr

from laya_platform.core import DecisionSpec
from laya_platform.core.adapters import FakeAnswer, FakeEngine
from laya_platform.gateway import ApiKey, GatewaySettings, hash_key
from laya_platform.gateway.app import create_app
from laya_platform.llm import FakeProvider, LLMGateway, LLMSettings
from laya_platform.registry import (
    Gates,
    SpecialistManifest,
    SpecialistRegistry,
    SpecialistSelector,
    engine_label,
)
from laya_platform.storage import Database

STATE = "Fui cobrado duas vezes"
SPEC: dict[str, Any] = {
    "id": "t.triage",
    "version": 1,
    "languages": ["pt"],
    "mode": "gated",
    "schema": {
        "type": "object",
        "properties": {
            "department": {"type": "string", "enum": ["billing", "technical"]},
            "churn_risk": {"type": "boolean"},
        },
    },
    "engine": {"checkpoint": "multilingual"},
    "policy": {
        "calibration_ref": "eval-0123456789ab",
        "bands": [
            {"min": 0.9, "outcome": "auto"},
            {"min": 0.6, "outcome": "review"},
            {"outcome": "escalate"},
        ],
        "escalation_tier": "deep",
        "canary": 1.0,
    },
}
ROUTER_ANSWERS = {"department": FakeAnswer("billing", 0.95), "churn_risk": FakeAnswer(True, 0.95)}
SPECIALIST_ANSWERS = {
    "department": FakeAnswer("technical", 0.97),
    "churn_risk": FakeAnswer(True, 0.97),
}


def _manifest(version: str) -> SpecialistManifest:
    return SpecialistManifest(
        name="t.triage_pt",
        version=version,
        base="multilingual",
        source=f"/models/{version}",
        decision_specs=("t.triage",),
    )


class Harness:
    def __init__(self, tmp_path: Path, router: FakeEngine, *, llm_replies: list[Any] = ()):  # type: ignore[assignment]
        (tmp_path / "specs").mkdir(exist_ok=True)
        (tmp_path / "specs" / "t.yaml").write_text(yaml.safe_dump(SPEC), encoding="utf-8")
        self.database = Database(f"sqlite:///{tmp_path / 'g.db'}")
        self.database.upgrade()
        self.registry = SpecialistRegistry(self.database)
        self.engines: dict[str, FakeEngine] = {}
        self.broken: set[str] = set()

        def loader(manifest: SpecialistManifest) -> FakeEngine:
            if manifest.key in self.broken:
                raise OSError("weights missing")
            return self.engines.setdefault(manifest.key, FakeEngine(SPECIALIST_ANSWERS))

        self.selector = SpecialistSelector(self.registry, router, loader=loader, refresh_s=0.0)
        settings = LLMSettings.model_validate(
            {
                "providers": {"local": {"kind": "openai_compatible", "local": True}},
                "tiers": {
                    "deep": {"provider": "local", "model": "model-a", "input_cost_per_mtok": 1000.0}
                },
            }
        )
        self.provider = FakeProvider.answering(*llm_replies)
        llm = LLMGateway(settings, providers={"local": self.provider}, spend=self.database)
        app = create_app(
            GatewaySettings(
                specs_dir=tmp_path / "specs",
                database_url=f"sqlite:///{tmp_path / 'g.db'}",
                hmac_key=SecretStr("k" * 32),
                api_keys=(
                    ApiKey(
                        name="ops",
                        sha256=hash_key("t-ops"),
                        scopes=("decide", "route", "admin", "metrics", "review"),
                    ),
                ),
            ),
            engine=router,
            database=self.database,
            llm=llm,
            specialists=self.selector,
        )
        self.client = TestClient(app)
        self.auth = {"Authorization": "Bearer t-ops"}

    def decide(self) -> Any:
        response = self.client.post(
            "/api/v1/decide", json={"spec": "t.triage", "state": STATE}, headers=self.auth
        )
        assert response.status_code == 200, response.text
        return response.json()

    def promote(self, manifest: SpecialistManifest) -> None:
        spec = DecisionSpec.model_validate(SPEC)
        report = {
            "id": f"eval-{manifest.version}",
            "identity": {
                "spec": spec.id,
                "questions_sha256": spec.questions_sha256(),
                "engine": engine_label(manifest),
            },
            "overall": {"accuracy": 0.9},
        }
        self.registry.register(manifest, "ml")
        self.registry.to_shadow(manifest.name, manifest.version, spec, report, "ml")

    def metrics(self) -> str:
        return str(self.client.get("/metrics", headers=self.auth).text)


@pytest.fixture
def make(tmp_path: Path) -> Iterator[Any]:
    made: list[Harness] = []

    def factory(router: FakeEngine | None = None, **kwargs: Any) -> Harness:
        made.append(Harness(tmp_path, router or FakeEngine(ROUTER_ANSWERS), **kwargs))
        return made[-1]

    yield factory
    for harness in made:
        harness.client.close()
        harness.database.dispose()


def test_the_router_answers_until_a_specialist_is_promoted(make: Any) -> None:
    router = FakeEngine(ROUTER_ANSWERS)
    h = make(router)
    body = h.decide()
    assert body["suggestion"]["values"]["department"] == "billing"
    assert h.database.audit_events()[0].engine == "router"
    assert router.calls[-1].controls == {"model": "multilingual"}  # the spec's checkpoint


def test_a_shadow_specialist_challenges_after_the_answer(make: Any) -> None:
    h = make()
    h.promote(_manifest("1"))
    body = h.decide()
    assert body["suggestion"]["values"]["department"] == "billing"  # still the Router's
    (result,) = h.database.challenger_results("t.triage_pt", "1")
    assert result.trace_id == body["trace_id"]
    assert result.values == {"department": "technical", "churn_risk": True}
    assert result.agreement == {"department": False, "churn_risk": True}
    assert h.engines["t.triage_pt@1"].calls[0].controls == {}  # no Router control for one model
    assert (
        'laya_platform_challenger_agreement_total{agree="false",question="department",'
        'spec="t.triage",specialist="t.triage_pt@1"} 1.0'
    ) in h.metrics()


def test_a_production_specialist_answers_and_rollback_returns_to_the_router(make: Any) -> None:
    h = make()
    manifest = _manifest("1")
    h.promote(manifest)
    h.decide()  # one shadow run is the evidence below
    h.registry.to_candidate(manifest.name, "1", "ml", gates=Gates(min_shadow_samples=1))
    h.registry.to_production(manifest.name, "1", "head of ops", "approved")
    body = h.decide()
    assert body["suggestion"]["values"]["department"] == "technical"
    assert h.database.audit_events()[0].engine == "t.triage_pt@1"
    listed = h.client.get("/api/v1/specialists", headers=h.auth).json()
    assert listed["pointers"] == {"t.triage": {"production": "t.triage_pt@1"}}
    response = h.client.post(
        "/api/v1/specialists/rollback",
        json={"spec": "t.triage", "reason": "regression"},
        headers=h.auth,
    )
    assert response.json() == {"spec": "t.triage", "production": "router"}
    assert h.decide()["suggestion"]["values"]["department"] == "billing"
    again = h.client.post(
        "/api/v1/specialists/rollback", json={"spec": "t.triage", "reason": "x"}, headers=h.auth
    )
    assert again.status_code == 409
    events = h.registry.events("t.triage_pt")
    assert events[-1]["actor"] == "ops"


def test_a_specialist_that_fails_to_load_never_blocks_a_decision(make: Any) -> None:
    h = make()
    manifest = _manifest("1")
    h.broken.add(manifest.key)
    h.promote(manifest)
    h.decide()
    assert h.database.challenger_results("t.triage_pt", "1") == []
    ready = h.client.get("/ready").json()
    assert ready["checks"]["specialist_errors"] == {"t.triage_pt@1": "OSError: weights missing"}


def test_flag_changes_are_attributed(make: Any) -> None:
    h = make()
    h.client.put("/api/v1/flags/kill_switch", json={"value": True}, headers=h.auth)
    events = h.client.get("/api/v1/flags/events", headers=h.auth).json()
    assert [(e["name"], e["old"], e["new"], e["actor"]) for e in events] == [
        ("kill_switch", None, True, "ops")
    ]


def test_review_queue_gauges(make: Any) -> None:
    h = make(FakeEngine({"department": FakeAnswer("billing", 0.7)}))
    h.decide()  # band review in gated mode -> queued
    text = h.metrics()
    assert "laya_platform_reviews_open 1.0" in text
    assert "laya_platform_reviews_oldest_age_seconds" in text


def test_system2_agreement_and_a_budget_kept_in_the_database(make: Any) -> None:
    lost = FakeEngine({"department": FakeAnswer("billing", 0.55), "churn_risk": FakeAnswer(True)})
    h = make(lost, llm_replies=[{"department": "technical", "churn_risk": True}])
    body = h.decide()
    assert body["system2"]["agreement"] == {"department": False, "churn_risk": True}
    assert (
        'laya_platform_system2_agreement_total{agree="false",question="department"' in h.metrics()
    )
    day_spend = h.database.llm_spent(datetime.now(UTC).date().isoformat())
    assert day_spend == pytest.approx(100 * 1000.0 / 1_000_000)
