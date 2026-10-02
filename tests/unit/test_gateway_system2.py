"""The gateway's System-1/System-2 flow (Block B): policy outcomes, gated + canary, escalation to an
LLM tier, the human review queue and the completed ``/api/v1/route``."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import SecretStr

from laya_platform.core.adapters import FakeAnswer, FakeEngine
from laya_platform.gateway import ApiKey, GatewaySettings, hash_key
from laya_platform.gateway.app import create_app
from laya_platform.llm import FakeProvider, LLMGateway, LLMProviderError, LLMSettings
from laya_platform.storage import Database

KEYS = {"ops": ("decide", "route", "metrics", "admin", "review"), "bot": ("decide",)}
STATE = "Se cobrarem de novo eu cancelo"
POLICY = {
    "calibration_ref": "eval-0123456789ab",
    "bands": [
        {"min": 0.9, "outcome": "auto"},
        {"min": 0.6, "outcome": "review"},
        {"outcome": "escalate"},
    ],
    "escalation_tier": "deep",
    "canary": 1.0,
}
SPEC_ID = "t.triage"
SPEC = {
    "id": SPEC_ID,
    "version": 3,
    "languages": ["pt"],
    "mode": "gated",
    "schema": {
        "type": "object",
        "properties": {
            "department": {"type": "string", "enum": ["billing", "technical"]},
            "churn_risk": {"type": "boolean"},
        },
    },
    "policy": POLICY,
}
CONFIDENT = {"department": FakeAnswer("billing", 0.95), "churn_risk": FakeAnswer(True, 0.95)}
UNSURE = {"department": FakeAnswer("billing", 0.7), "churn_risk": FakeAnswer(True, 0.95)}
LOST = {"department": FakeAnswer("billing", 0.55), "churn_risk": FakeAnswer(True, 0.95)}
LLM_REPLY = {"department": "technical", "churn_risk": True}


def _settings(tmp_path: Path) -> GatewaySettings:
    return GatewaySettings(
        specs_dir=tmp_path / "specs",
        database_url=f"sqlite:///{tmp_path / 'audit.db'}",
        hmac_key=SecretStr("k" * 32),
        api_keys=tuple(
            ApiKey(name=name, sha256=hash_key(f"token-{name}"), scopes=scopes)  # type: ignore[arg-type]
            for name, scopes in KEYS.items()
        ),
    )


def _llm(provider: FakeProvider) -> LLMGateway:
    settings = LLMSettings.model_validate(
        {
            "providers": {"local": {"kind": "openai_compatible", "local": True}},
            "tiers": {
                "deep": {"provider": "local", "model": "model-a", "input_cost_per_mtok": 1.0}
            },
        }
    )
    return LLMGateway(settings, providers={"local": provider})


class Harness:
    def __init__(self, tmp_path: Path, answers: dict[str, FakeAnswer], *specs: dict[str, Any]):
        directory = tmp_path / "specs"
        directory.mkdir(exist_ok=True)
        for spec in specs or (SPEC,):
            (directory / f"{spec['id']}.yaml").write_text(yaml.safe_dump(spec), encoding="utf-8")
        self.provider = FakeProvider()
        self.database = Database(f"sqlite:///{tmp_path / 'audit.db'}")
        self.engine = FakeEngine(answers)
        app = create_app(
            _settings(tmp_path), engine=self.engine, database=self.database, llm=_llm(self.provider)
        )
        self.client = TestClient(app)

    def decide(self, spec: str = SPEC_ID, name: str = "ops") -> Any:
        response = self.client.post(
            "/api/v1/decide", json={"spec": spec, "state": STATE}, headers=self.auth(name)
        )
        assert response.status_code == 200, response.text
        return response.json()

    @staticmethod
    def auth(name: str = "ops") -> dict[str, str]:
        return {"Authorization": f"Bearer token-{name}"}


@pytest.fixture
def make(tmp_path: Path) -> Iterator[Any]:
    made: list[Harness] = []

    def factory(answers: dict[str, FakeAnswer], *specs: dict[str, Any]) -> Harness:
        made.append(Harness(tmp_path, answers, *specs))
        return made[-1]

    yield factory
    for harness in made:
        harness.client.close()
        harness.database.dispose()


def test_gated_acts_on_the_auto_band_inside_the_canary(make: Any) -> None:
    h = make(CONFIDENT)
    body = h.decide()
    assert (body["mode"], body["outcome"], body["act"], body["reasons"]) == (
        "gated",
        "auto",
        True,
        [],
    )
    assert body["suggestion"]["values"] == {"department": "billing", "churn_risk": True}
    assert body["suggestion"]["source"] == "system1"
    assert body["review_id"] is None
    assert body["system2"] is None
    (event,) = h.database.audit_events()
    assert (event.outcome, event.act, event.reasons) == ("auto", True, [])
    metrics = h.client.get("/metrics", headers=h.auth()).text
    assert 'laya_platform_acts_total{spec="t.triage"} 1.0' in metrics


def test_outside_the_canary_nothing_acts(make: Any) -> None:
    h = make(CONFIDENT, SPEC | {"policy": POLICY | {"canary": 1e-9}})
    body = h.decide()
    assert (body["outcome"], body["act"]) == ("auto", False)


def test_a_review_outcome_goes_to_the_queue_without_the_input(make: Any) -> None:
    h = make(UNSURE)
    body = h.decide()
    assert (body["outcome"], body["act"]) == ("review", False)
    assert body["reasons"] == ["band:department=review"]
    assert h.provider.calls == []  # review is for a human, not for the LLM
    (item,) = h.client.get("/api/v1/reviews", headers=h.auth()).json()
    assert item["id"] == body["review_id"]
    assert item["trace_id"] == body["trace_id"]
    assert item["reason"] == "band:department=review"
    assert item["suggestion"]["values"] == {"department": "billing", "churn_risk": True}
    assert STATE not in str(item)
    assert "cancelo" not in str(item)


def test_an_escalation_is_answered_by_the_llm_tier(make: Any) -> None:
    h = make(LOST)
    h.provider.extend([LLM_REPLY])
    body = h.decide()
    assert (body["outcome"], body["act"], body["review_id"]) == ("escalate", False, None)
    assert body["suggestion"]["source"] == "system2"
    assert body["suggestion"]["values"] == LLM_REPLY
    assert body["system2"]["tier"] == "deep"
    assert body["system2"]["outcome"] == "ok"
    assert body["system2"]["values"] == LLM_REPLY
    assert STATE in h.provider.calls[0]["prompt"]
    (event,) = h.database.audit_events()
    assert event.system2 is not None
    assert event.system2["cost"] == pytest.approx(100 / 1_000_000)
    metrics = h.client.get("/metrics", headers=h.auth()).text
    assert 'laya_platform_llm_calls_total{outcome="ok",tier="deep"} 1.0' in metrics
    assert 'laya_platform_llm_tokens_total{direction="input",tier="deep"} 100.0' in metrics


def test_a_failed_escalation_falls_back_to_a_human(make: Any) -> None:
    h = make(LOST)
    h.provider.extend([LLMProviderError("APIConnectionError: down")])
    body = h.decide()
    assert body["suggestion"]["source"] == "system1"
    assert body["system2"]["outcome"] == "provider"
    item = h.database.review(body["review_id"])
    assert item is not None
    assert "system2 provider: APIConnectionError: down" in item.reason


def test_an_escalation_without_a_tier_goes_to_a_human(make: Any) -> None:
    no_tier = POLICY | {"escalation_tier": None}
    h = make(LOST, SPEC | {"policy": no_tier})
    body = h.decide()
    assert (body["outcome"], body["system2"]) == ("escalate", None)
    item = h.database.review(body["review_id"])
    assert item is not None
    assert item.reason == "band:department=escalate"
    assert h.provider.calls == []


def test_advisory_escalates_but_queues_nothing(make: Any) -> None:
    h = make(LOST, SPEC | {"mode": "advisory"})
    h.provider.extend([LLM_REPLY])
    body = h.decide()
    assert (body["mode"], body["act"], body["review_id"]) == ("advisory", False, None)
    assert body["suggestion"]["source"] == "system2"
    unsure = make(UNSURE, SPEC | {"id": "t.other", "mode": "advisory"})
    assert unsure.decide("t.other")["review_id"] is None


def test_shadow_records_the_verdict_and_calls_nothing(make: Any) -> None:
    h = make(LOST, SPEC | {"mode": "shadow"})
    body = h.decide()
    assert (body["outcome"], body["act"], body["suggestion"], body["system2"]) == (
        "escalate",
        False,
        None,
        None,
    )
    assert h.provider.calls == []
    (event,) = h.database.audit_events()
    assert event.outcome == "escalate"


def test_the_kill_switch_stops_acting_at_once(make: Any) -> None:
    h = make(CONFIDENT)
    h.client.put("/api/v1/flags/kill_switch", json={"value": True}, headers=h.auth())
    body = h.decide()
    assert (body["mode"], body["act"]) == ("shadow", False)


def test_reviews_are_resolved_once_by_a_reviewer(make: Any) -> None:
    h = make(UNSURE)
    review_id = h.decide()["review_id"]
    path = f"/api/v1/reviews/{review_id}/resolve"
    answer = {"values": {"department": "technical", "churn_risk": True}, "note": "billing bug"}
    assert h.client.post(path, json=answer, headers=h.auth("bot")).status_code == 403
    bad = {"values": {"nope": 1}}
    assert h.client.post(path, json=bad, headers=h.auth()).status_code == 422
    resolved = h.client.post(path, json=answer, headers=h.auth()).json()
    assert (resolved["status"], resolved["resolver"]) == ("resolved", "ops")
    assert resolved["resolution"] == answer
    assert h.client.post(path, json=answer, headers=h.auth()).status_code == 409
    assert (
        h.client.post("/api/v1/reviews/999/resolve", json=answer, headers=h.auth()).status_code
        == 404
    )
    assert h.client.get("/api/v1/reviews", headers=h.auth()).json() == []
    listed = h.client.get("/api/v1/reviews?status=all", headers=h.auth()).json()
    assert [item["status"] for item in listed] == ["resolved"]
    assert h.client.get("/api/v1/reviews?status=x", headers=h.auth()).status_code == 422


@pytest.mark.parametrize(
    ("answers", "system", "tier"),
    [(CONFIDENT, "laya", None), (UNSURE, "human", None), (LOST, "llm", "deep")],
)
def test_route_says_who_would_answer_without_side_effects(
    make: Any, answers: dict[str, FakeAnswer], system: str, tier: str | None
) -> None:
    h = make(answers)
    response = h.client.post(
        "/api/v1/route", json={"state": STATE, "spec": SPEC["id"]}, headers=h.auth()
    )
    assert response.status_code == 200
    assert (response.json()["system"], response.json()["tier"]) == (system, tier)
    assert h.database.audit_events() == []
    assert h.database.reviews() == []
    assert h.provider.calls == []


def test_a_gated_flag_needs_a_spec_that_can_act(make: Any) -> None:
    h = make(CONFIDENT, SPEC, SPEC | {"id": "t.plain", "mode": "advisory", "policy": None})
    response = h.client.put("/api/v1/flags/mode:t.plain", json={"value": "gated"}, headers=h.auth())
    assert response.status_code == 422
    assert "needs a policy" in response.json()["detail"]
    ok = h.client.put("/api/v1/flags/mode:t.triage", json={"value": "gated"}, headers=h.auth())
    assert ok.status_code == 200


def test_start_up_refuses_an_unknown_escalation_tier(tmp_path: Path) -> None:
    (tmp_path / "specs").mkdir()
    bad = SPEC | {"policy": POLICY | {"escalation_tier": "missing"}}
    (tmp_path / "specs" / "s.yaml").write_text(yaml.safe_dump(bad), encoding="utf-8")
    database = Database(f"sqlite:///{tmp_path / 'audit.db'}")
    with pytest.raises(ValueError, match="escalation_tier 'missing' is not a configured LLM tier"):
        create_app(
            _settings(tmp_path), engine=FakeEngine(), database=database, llm=_llm(FakeProvider())
        )
    with pytest.raises(ValueError, match="escalation_tier"):
        create_app(_settings(tmp_path), engine=FakeEngine(), database=database)
    database.dispose()


def test_settings_carry_the_llm_block_and_refuse_external_tiers(tmp_path: Path) -> None:
    llm = {
        "providers": {"cloud": {"kind": "anthropic"}},
        "tiers": {"deep": {"provider": "cloud", "model": "model-b"}},
    }
    base = {"specs_dir": tmp_path, "hmac_key": "k" * 32, "auth_disabled": True}
    with pytest.raises(ValueError, match="external provider"):
        GatewaySettings.model_validate(base | {"llm": llm})
    settings = GatewaySettings.model_validate(base | {"llm": llm | {"allow_external": True}})
    assert settings.llm is not None
    assert settings.llm.tiers["deep"].model == "model-b"
