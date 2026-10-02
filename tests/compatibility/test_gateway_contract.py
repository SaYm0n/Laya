"""The gateway over a real ``laya.Router``: the upstream app is mounted unchanged next to it.

The checkpoints are a StubAgent (nothing is downloaded); routing, the upstream HTTP app, its auth
and its wire contract are real. ``/v1/systemone`` must answer through the gateway exactly as it
answers from ``laya.serve.create_app`` alone.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from laya import Router
from laya.serve import create_app as create_upstream_app
from pydantic import SecretStr

from laya_platform.core.adapters import FakeAnswer, RemoteEngine, UpstreamRouterEngine
from laya_platform.gateway import GatewaySettings
from laya_platform.gateway.app import create_app
from laya_platform.storage import Database

SPEC = {
    "id": "examples.triage",
    "version": 1,
    "languages": ["pt", "en"],
    "mode": "advisory",
    "schema": {
        "type": "object",
        "properties": {
            "department": {"type": "string", "enum": ["billing", "technical"]},
            "urgency": {"type": "integer", "minimum": 0, "maximum": 3},
        },
    },
    "engine": {"checkpoint": "multilingual"},
}
STATE = "Fui cobrado duas vezes em março, quero o reembolso"


@pytest.fixture(autouse=True)
def _no_upstream_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LAYA_API_KEY", raising=False)


@pytest.fixture
def router(router_factory: Callable[..., Router], make_agent: Any) -> Router:
    return router_factory(make_agent(answers={"department": FakeAnswer("technical", 0.8)}))


def _settings(tmp_path: Path) -> GatewaySettings:
    specs = tmp_path / "specs"
    specs.mkdir(exist_ok=True)
    (specs / "triage.yaml").write_text(yaml.safe_dump(SPEC), encoding="utf-8")
    return GatewaySettings(
        specs_dir=specs,
        database_url=f"sqlite:///{tmp_path / 'audit.db'}",
        hmac_key=SecretStr("k" * 32),
        auth_disabled=True,
    )


@pytest.fixture
def gateway(tmp_path: Path, router: Router) -> Iterator[TestClient]:
    database = Database(f"sqlite:///{tmp_path / 'audit.db'}")
    app = create_app(_settings(tmp_path), engine=UpstreamRouterEngine(router), database=database)
    with TestClient(app) as client:
        yield client
    database.dispose()


def test_systemone_through_the_gateway_is_the_upstream_answer(
    gateway: TestClient, router: Router, tmp_path: Path
) -> None:
    questions = {"churn": {"type": "noul", "instructions": "Will they cancel?"}}
    body = {"state": STATE, "questions": questions, "model": "english"}
    through_gateway = gateway.post("/v1/systemone", json=body)
    with TestClient(create_upstream_app(router)) as upstream:
        alone = upstream.post("/v1/systemone", json=body)
    assert through_gateway.status_code == alone.status_code == 200
    assert through_gateway.json() == alone.json()
    assert gateway.get("/health").json() == upstream.get("/health").json()


def test_the_upstream_refusals_are_unchanged(gateway: TestClient) -> None:
    response = gateway.post("/v1/systemone", json={"questions": {}})
    assert response.status_code == 400
    assert response.json()["detail"] == "'state' is required"


def test_the_upstream_auth_still_guards_systemone(
    tmp_path: Path, router: Router, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LAYA_API_KEY", "upstream-secret")
    database = Database(f"sqlite:///{tmp_path / 'audit.db'}")
    app = create_app(_settings(tmp_path), engine=UpstreamRouterEngine(router), database=database)
    with TestClient(app) as client:
        body = {"state": STATE, "questions": {"q": {"type": "noul", "instructions": "?"}}}
        assert client.post("/v1/systemone", json=body).status_code == 401
        assert client.get("/ready").status_code == 200  # the platform's own routes win
    database.dispose()


def test_decide_runs_the_spec_on_the_router_with_its_checkpoint(gateway: TestClient) -> None:
    response = gateway.post("/api/v1/decide", json={"spec": SPEC["id"], "state": STATE})
    assert response.status_code == 200
    suggestion = response.json()["suggestion"]
    assert suggestion["values"]["department"] == "technical"
    assert suggestion["values"]["urgency"] in (0, 1, 2, 3)
    assert suggestion["routing"]["model"] == "multilingual"  # engine.checkpoint -> model control


def test_route_reports_the_upstream_routing(gateway: TestClient) -> None:
    response = gateway.post("/api/v1/route", json={"state": STATE, "model": "ml"})
    assert response.status_code == 200
    assert response.json()["model"] == "multilingual"


def test_a_remote_engine_gets_no_upstream_mount(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'audit.db'}")
    engine = RemoteEngine("http://127.0.0.1:9")
    with TestClient(create_app(_settings(tmp_path), engine=engine, database=database)) as client:
        assert client.post("/v1/systemone", json={}).status_code == 404
    database.dispose()
