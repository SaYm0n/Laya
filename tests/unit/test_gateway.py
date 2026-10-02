"""The gateway's own API over a FakeEngine: auth, modes, flags, audit, metrics, limits.

The upstream app mounted next to it is tested over a real ``laya.Router`` in
tests/compatibility/test_gateway_contract.py.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from laya.serve import MAX_STATE_CHARS
from pydantic import ValidationError

from laya_platform.core.adapters import FakeAnswer, FakeEngine
from laya_platform.core.errors import UnsupportedOperationError
from laya_platform.gateway import ApiKey, GatewaySettings, hash_key
from laya_platform.gateway.app import create_app, input_hmac, state_length
from laya_platform.gateway.settings import HMAC_KEY_ENV, Scope
from laya_platform.gateway.specs import SpecStore, wire_limit_problems
from laya_platform.storage import Database

HMAC_KEY = "k" * 32
KEYS: dict[str, tuple[Scope, ...]] = {
    "all": ("decide", "route", "metrics", "admin"),
    "decide-only": ("decide",),
}
STATE = "Fui cobrado duas vezes, se não resolverem eu cancelo"
SPEC = {
    "id": "examples.triage",
    "version": 2,
    "languages": ["pt"],
    "schema": {
        "type": "object",
        "properties": {
            "department": {"type": "string", "enum": ["billing", "technical"]},
            "churn_risk": {"type": "boolean"},
        },
    },
    "policy": {
        "calibration_ref": "eval-0123456789ab",
        "bands": [{"min": 0.9, "outcome": "auto"}, {"outcome": "review"}],
    },
}
ANSWERS = {"department": FakeAnswer("billing", 0.95), "churn_risk": FakeAnswer(True, 0.8)}


def _token(name: str) -> str:
    return f"token-{name}"


def _settings(tmp_path: Path, **overrides: Any) -> GatewaySettings:
    values: dict[str, Any] = {
        "specs_dir": tmp_path / "specs",
        "database_url": f"sqlite:///{tmp_path / 'audit.db'}",
        "hmac_key": HMAC_KEY,
        "api_keys": [
            ApiKey(name=name, sha256=hash_key(_token(name)), scopes=scopes)
            for name, scopes in KEYS.items()
        ],
    }
    return GatewaySettings(**(values | overrides))


def _write_specs(tmp_path: Path, *specs: dict[str, Any]) -> None:
    directory = tmp_path / "specs"
    directory.mkdir(exist_ok=True)
    for spec in specs:
        (directory / f"{spec['id']}.yaml").write_text(yaml.safe_dump(spec), encoding="utf-8")


@pytest.fixture
def engine() -> FakeEngine:
    return FakeEngine(ANSWERS)


@pytest.fixture
def database(tmp_path: Path) -> Iterator[Database]:
    database = Database(f"sqlite:///{tmp_path / 'audit.db'}")
    yield database
    database.dispose()


@pytest.fixture
def client(tmp_path: Path, engine: FakeEngine, database: Database) -> Iterator[TestClient]:
    _write_specs(tmp_path, SPEC, {**SPEC, "id": "examples.offline", "mode": "offline"})
    app = create_app(_settings(tmp_path), engine=engine, database=database)
    with TestClient(app) as client:
        yield client


def _auth(name: str = "all") -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(name)}"}


def _decide(client: TestClient, body: dict[str, Any] | None = None, name: str = "all") -> Any:
    return client.post(
        "/api/v1/decide", json=body or {"spec": SPEC["id"], "state": STATE}, headers=_auth(name)
    )


def _set_flag(client: TestClient, name: str, value: Any) -> Any:
    return client.put(f"/api/v1/flags/{name}", json={"value": value}, headers=_auth())


# ------------------------------------------------------------------------------------- decide
def test_shadow_decides_audits_and_returns_nothing_to_act_on(
    client: TestClient, database: Database
) -> None:
    response = _decide(client)
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "shadow"
    assert body["act"] is False
    assert body["suggestion"] is None
    assert body["spec"] == {"id": SPEC["id"], "version": 2}
    (event,) = database.audit_events()
    assert event.trace_id == body["trace_id"]
    assert event.mode == "shadow"
    assert event.model == "fake-engine"
    assert event.error is None
    assert event.data_classification == "synthetic"
    assert event.answers["department"] == {
        "value": "billing",
        "answer_confidence": 0.95,
        "band": "auto",
    }
    assert event.answers["churn_risk"]["band"] == "review"


def test_the_audit_keeps_an_hmac_of_the_input_never_the_input(
    client: TestClient, database: Database
) -> None:
    _decide(client)
    (event,) = database.audit_events()
    assert event.input_hmac == input_hmac(HMAC_KEY.encode(), STATE)
    stored = json.dumps(
        {column: getattr(event, column) for column in event.__table__.columns.keys()},  # noqa: SIM118
        default=str,
        ensure_ascii=False,
    )
    assert STATE not in stored
    assert "cobrado" not in stored


def test_the_input_hmac_is_keyed_and_canonical() -> None:
    key = HMAC_KEY.encode()
    assert input_hmac(key, {"a": 1, "b": 2}) == input_hmac(key, {"b": 2, "a": 1})
    assert input_hmac(key, STATE) != input_hmac(b"x" * 32, STATE)


def test_advisory_returns_the_suggestion_with_its_bands(
    client: TestClient, engine: FakeEngine
) -> None:
    assert _set_flag(client, f"mode:{SPEC['id']}", "advisory").status_code == 200
    body = _decide(client).json()
    assert body["mode"] == "advisory"
    assert body["act"] is False
    suggestion = body["suggestion"]
    assert suggestion["values"] == {"department": "billing", "churn_risk": True}
    assert suggestion["bands"] == {"department": "auto", "churn_risk": "review"}
    assert suggestion["calibration_ref"] == "eval-0123456789ab"
    assert suggestion["answers"]["department"]["answer_confidence"] == 0.95
    assert engine.calls[-1].arguments[0] == STATE  # the state reaches the engine unchanged


def test_the_kill_switch_turns_every_spec_into_shadow(client: TestClient) -> None:
    _set_flag(client, f"mode:{SPEC['id']}", "advisory")
    assert _set_flag(client, "kill_switch", True).json() == {
        "kill_switch": True,
        f"mode:{SPEC['id']}": "advisory",
    }
    body = _decide(client).json()
    assert body["mode"] == "shadow"
    assert body["suggestion"] is None
    _set_flag(client, "kill_switch", None)
    assert _decide(client).json()["mode"] == "advisory"


def test_offline_specs_are_evaluation_only(client: TestClient) -> None:
    response = _decide(client, {"spec": "examples.offline", "state": STATE})
    assert response.status_code == 409


def test_unknown_spec_is_404(client: TestClient) -> None:
    assert _decide(client, {"spec": "nope", "state": STATE}).status_code == 404


def test_agreement_with_the_incumbent_is_measured(client: TestClient, database: Database) -> None:
    body = _decide(
        client,
        {
            "spec": SPEC["id"],
            "state": STATE,
            "incumbent": {"department": "billing", "churn_risk": False},
        },
    ).json()
    assert body["agreement"] == {"department": True, "churn_risk": False}
    (event,) = database.audit_events()
    assert event.incumbent == {"department": "billing", "churn_risk": False}
    metrics = client.get("/metrics", headers=_auth()).text
    assert 'laya_platform_incumbent_agreement_total{agree="true",question="department"' in metrics


@pytest.mark.parametrize(
    ("error", "status", "detail"),
    [
        (ValueError("bad question"), 422, "bad question"),
        (RuntimeError("CUDA out of memory at 0xdeadbeef"), 500, "decision failed"),
    ],
)
def test_engine_failures_are_audited_and_not_leaked(
    tmp_path: Path, database: Database, error: Exception, status: int, detail: str
) -> None:
    _write_specs(tmp_path, SPEC)
    app = create_app(_settings(tmp_path), engine=FakeEngine(error=error), database=database)
    with TestClient(app) as client:
        response = _decide(client)
        assert response.status_code == status
        assert response.json()["detail"] == detail
        (event,) = database.audit_events()
        assert event.error == f"{type(error).__name__}: {error}"
        assert (
            'laya_platform_decision_errors_total{spec="examples.triage"} 1.0'
            in client.get("/metrics", headers=_auth()).text
        )


def test_a_state_over_the_upstream_limit_is_refused(client: TestClient) -> None:
    response = _decide(client, {"spec": SPEC["id"], "state": "x" * (MAX_STATE_CHARS + 1)})
    assert response.status_code == 413
    assert _decide(client, {"spec": SPEC["id"], "state": "x" * MAX_STATE_CHARS}).status_code == 200


def test_state_length_is_measured_as_the_upstream_measures_it() -> None:
    assert state_length('a "quoted" text') == len('a "quoted" text')
    assert state_length({"body": '"'}) == len(json.dumps({"body": '"'}, ensure_ascii=False))


def test_a_body_over_the_upstream_limit_is_refused(client: TestClient) -> None:
    response = client.post(
        "/api/v1/decide",
        content=b"{}",
        headers={**_auth(), "Content-Type": "application/json", "Content-Length": str(3 << 20)},
    )
    assert response.status_code == 413


# --------------------------------------------------------------------------------- auth, flags
@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({}, 401),
        ({"Authorization": "Bearer wrong"}, 401),
        ({"Authorization": f"Bearer {_token('decide-only')}"}, 403),
    ],
)
def test_admin_routes_need_the_admin_scope(
    client: TestClient, headers: dict[str, str], status: int
) -> None:
    assert client.get("/api/v1/flags", headers=headers).status_code == status
    assert client.get("/metrics", headers=headers).status_code == status


def test_a_decide_key_decides(client: TestClient) -> None:
    assert _decide(client, name="decide-only").status_code == 200
    assert (
        client.post("/api/v1/decide", json={"spec": SPEC["id"], "state": STATE}).status_code == 401
    )


@pytest.mark.parametrize(
    ("name", "value", "status"),
    [
        ("kill_switch", "yes", 422),
        ("mode:examples.triage", "production", 422),
        ("mode:nope", "shadow", 404),
        ("anything", True, 404),
    ],
)
def test_flags_are_validated(client: TestClient, name: str, value: Any, status: int) -> None:
    assert _set_flag(client, name, value).status_code == status
    assert client.get("/api/v1/flags", headers=_auth()).json() == {}


def test_specs_list_their_effective_mode(client: TestClient) -> None:
    _set_flag(client, f"mode:{SPEC['id']}", "advisory")
    listed = {s["id"]: s for s in client.get("/api/v1/specs", headers=_auth()).json()}
    assert listed[SPEC["id"]]["mode"] == "shadow"
    assert listed[SPEC["id"]]["effective_mode"] == "advisory"
    assert listed[SPEC["id"]]["calibration_ref"] == "eval-0123456789ab"
    assert listed["examples.offline"]["effective_mode"] == "offline"


def test_auth_can_be_disabled_for_local_use(tmp_path: Path, database: Database) -> None:
    _write_specs(tmp_path, SPEC)
    settings = _settings(tmp_path, api_keys=(), auth_disabled=True)
    with TestClient(create_app(settings, engine=FakeEngine(), database=database)) as client:
        assert client.get("/api/v1/flags").status_code == 200


# ------------------------------------------------------------------------- route, ready, metrics
def test_route_answers_with_the_engine_route(client: TestClient) -> None:
    response = client.post("/api/v1/route", json={"state": STATE, "lang": "pt"}, headers=_auth())
    assert response.status_code == 200
    assert response.json()["reason"] == "FakeEngine: fixed route"


def test_route_is_501_for_an_engine_that_cannot_route(tmp_path: Path, database: Database) -> None:
    _write_specs(tmp_path, SPEC)
    engine = FakeEngine(error=UnsupportedOperationError("no routing here"))
    with TestClient(create_app(_settings(tmp_path), engine=engine, database=database)) as client:
        response = client.post("/api/v1/route", json={"state": STATE}, headers=_auth())
        assert response.status_code == 501


def test_ready_reports_its_checks(client: TestClient) -> None:
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {
        "ready": True,
        "checks": {"database": True, "specs": 2, "engine": "FakeEngine"},
    }


def test_ready_is_503_without_specs(tmp_path: Path, database: Database) -> None:
    (tmp_path / "specs").mkdir()
    app = create_app(_settings(tmp_path), engine=FakeEngine(), database=database)
    with TestClient(app) as client:
        assert client.get("/ready").status_code == 503


def test_metrics_count_decisions_confidence_and_bands(client: TestClient) -> None:
    _decide(client)
    _decide(client)
    text = client.get("/metrics", headers=_auth()).text
    spec = 'spec="examples.triage"'
    assert f'laya_platform_decisions_total{{mode="shadow",{spec}}} 2.0' in text
    assert f'laya_platform_bands_total{{band="auto",question="department",{spec}}} 2.0' in text
    assert f'laya_platform_answer_confidence_count{{question="churn_risk",{spec}}} 2.0' in text


def test_each_app_has_its_own_metrics(tmp_path: Path, database: Database) -> None:
    _write_specs(tmp_path, SPEC)
    first = create_app(_settings(tmp_path), engine=FakeEngine(ANSWERS), database=database)
    second = create_app(_settings(tmp_path), engine=FakeEngine(ANSWERS), database=database)
    with TestClient(first) as a, TestClient(second) as b:
        _decide(a)
        assert "laya_platform_decisions_total{" not in b.get("/metrics", headers=_auth()).text


# ------------------------------------------------------------------------------------ settings
def test_settings_refuse_real_data_without_a_dg1_approval(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="Data Governance Gate"):
        _settings(tmp_path, data_classification="real")
    assert _settings(tmp_path, data_classification="real", dg1_approval_ref="DG-1/2026-11")


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"hmac_key": "short"}, "at least 32 characters"),
        ({"api_keys": ()}, "at least one API key"),
        ({"engine": {"kind": "remote"}}, "needs engine.remote_url"),
    ],
)
def test_settings_refuse_unsafe_configurations(
    tmp_path: Path, overrides: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        _settings(tmp_path, **overrides)


def test_settings_file_takes_the_hmac_key_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    file = tmp_path / "gateway.yaml"
    file.write_text("specs_dir: specs\nauth_disabled: true\n", encoding="utf-8")
    monkeypatch.setenv(HMAC_KEY_ENV, HMAC_KEY)
    settings = GatewaySettings.load(file)
    assert settings.specs_dir == tmp_path / "specs"
    assert settings.hmac_key.get_secret_value() == HMAC_KEY
    assert HMAC_KEY not in repr(settings)
    file.write_text(f"specs_dir: specs\nhmac_key: {HMAC_KEY}\n", encoding="utf-8")
    with pytest.raises(ValueError, match=HMAC_KEY_ENV):
        GatewaySettings.load(file)


# --------------------------------------------------------------------------------------- specs
def test_a_spec_store_refuses_duplicate_ids(tmp_path: Path) -> None:
    _write_specs(tmp_path, SPEC)
    (tmp_path / "specs" / "copy.yml").write_text(yaml.safe_dump(SPEC), encoding="utf-8")
    with pytest.raises(ValueError, match="defined twice"):
        SpecStore.load(tmp_path / "specs")


def test_wire_limits_apply_only_over_http(tmp_path: Path) -> None:
    wide = {
        "id": "examples.wide",
        "version": 1,
        "languages": ["en"],
        "questions": {
            "pick": {
                "type": "choice",
                "instructions": "Which?",
                "criteria": [f"option {i}" for i in range(101)],
            }
        },
    }
    _write_specs(tmp_path, wide)
    assert len(SpecStore.load(tmp_path / "specs")) == 1
    with pytest.raises(ValueError, match="does not fit /v1/systemone"):
        SpecStore.load(tmp_path / "specs", over_http=True)


def test_wire_limit_problems_count_as_the_upstream_counts() -> None:
    assert wire_limit_problems({"q": {"type": "score", "criteria": [str(i) for i in range(33)]}})
    assert not wire_limit_problems({"q": {"type": "score", "criteria": {"a": 1}}})
    many = {f"q{i}": {"type": "noul"} for i in range(65)}
    assert wire_limit_problems(many) == ["65 questions > 64"]


def test_the_example_settings_and_specs_load(monkeypatch: pytest.MonkeyPatch) -> None:
    example = Path(__file__).resolve().parents[2] / "examples" / "support_triage"
    monkeypatch.setenv(HMAC_KEY_ENV, HMAC_KEY)
    settings = GatewaySettings.load(example / "gateway.yaml")
    assert settings.data_classification == "synthetic"
    assert [spec.id for spec in SpecStore.load(settings.specs_dir)] == ["examples.support_triage"]
