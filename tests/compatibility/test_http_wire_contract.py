"""``/v1/systemone`` wire/API contract of the upstream app (``laya.serve.create_app``, laya 0.3.23).

The app under test is the upstream's own, built around a real ``laya.Router`` whose checkpoints are
a StubAgent: everything HTTP (parsing, limits, refusals, auth, status codes, headers, error texts)
and everything routing is real; only the forward pass is stubbed.

Compared semantically, never byte for byte: bodies through their parsed JSON, headers by presence
and format. Key order, whitespace and headers added by middleware may change without breaking it.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
from collections.abc import Callable, Iterator, Mapping
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from laya import Router
from laya.serve import create_app

from laya_platform.core import RemoteEngineError
from laya_platform.core.adapters import FakeAnswer, HttpResponse, RemoteEngine

ENGLISH = "Hi, we were billed twice for March and need a refund for the duplicate charge."
PORTUGUESE = "Fui cobrado duas vezes em março, quero o reembolso"
QUESTIONS: dict[str, Any] = {
    "team": {
        "type": "choice",
        "instructions": "Team?",
        "criteria": {"billing": None, "tech": None},
    },
    "level": {"type": "score", "instructions": "Urgency?", "criteria": ["low", "mid", "high"]},
    "churn": {"type": "noul", "instructions": "Will they cancel?"},
}
SERVER_ENV = (
    "LAYA_API_KEY",
    "LAYA_MAX_CONCURRENT",
    "LAYA_MAX_TOKEN_BUDGET",
    "LAYA_ROOT_PATH",
    "LAYA_DEFAULT_MODEL",
    "LAYA_THREADS",
)


@pytest.fixture(autouse=True)
def _clean_server_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in SERVER_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def router(router_factory: Callable[..., Router], make_agent: Any) -> Router:
    return router_factory(make_agent(answers={"team": FakeAnswer("tech", 0.7)}))


@pytest.fixture
def client(router: Router) -> Iterator[TestClient]:
    with TestClient(create_app(router)) as client:
        yield client


def _json(value: Any) -> Any:
    """What a JSON client sees for an in-process value (string keys, lists for tuples)."""
    return json.loads(json.dumps(value))


# Starlette's TestClient returns its own client library's responses; only .json(), .status_code and
# .headers are used, so they are typed loosely.
def _post(client: TestClient, body: Any, path: str = "/v1/systemone", **headers: str) -> Any:
    return client.post(path, json=body, headers=headers)


def _detail(response: Any) -> Any:
    return response.json()["detail"]


# ---------------------------------------------------------------------------------- success path
def test_a_decision_is_the_router_payload(client: TestClient, router: Router) -> None:
    response = _post(client, {"state": PORTUGUESE, "questions": QUESTIONS})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert body == _json(router.predict(PORTUGUESE, QUESTIONS))
    assert set(body) == {"model", "answers", "usage", "routing"}
    assert body["routing"]["model"] == "multilingual"
    assert {name: answer["type"] for name, answer in body["answers"].items()} == {
        "team": "choice",
        "level": "score",
        "churn": "noul",
    }
    assert isinstance(body["usage"]["input_tokens"], int)
    assert body["usage"]["output_tokens"] == 0


def test_timing_headers(client: TestClient) -> None:
    response = _post(client, {"state": ENGLISH, "questions": QUESTIONS})
    assert re.fullmatch(r"inference;dur=\d+\.\d{2}", response.headers["server-timing"])
    assert re.fullmatch(r"\d+\.\d{2}", response.headers["x-inference-time-ms"])


@pytest.mark.parametrize(
    ("controls", "model"),
    [
        ({}, "english"),
        ({"model": "ml"}, "multilingual"),
        ({"model": "jev-1"}, "english"),  # a Jev model id is not a checkpoint: auto-route
        ({"model": "laya-multilingual"}, "multilingual"),
        ({"task": "typed_decisions"}, "typed-decisions"),
        ({"lang": "pt"}, "multilingual"),
        ({"lang_guess": "pt-BR"}, "multilingual"),
        ({"lang": None, "lang_guess": None, "model": None}, "english"),
    ],
)
def test_routing_controls(client: TestClient, controls: dict[str, Any], model: str) -> None:
    body = _post(client, {"state": ENGLISH, "questions": QUESTIONS, **controls}).json()
    assert body["routing"]["model"] == model


def test_budget_and_gate_controls(client: TestClient, router: Router, make_agent: Any) -> None:
    agent = router._agents["english"]  # the StubAgent attached by the fixture
    body = _post(
        client,
        {
            "state": ENGLISH,
            "questions": QUESTIONS,
            "max_len": 256,
            "head_max_len": 64,
            "min_confidence": 0.8,
        },
    ).json()
    assert agent.calls[-1][1]["max_len"] == 256
    assert agent.calls[-1][1]["head_max_len"] == 64
    assert body["answers"]["team"]["abstention"] == "abstained"
    assert body["answers"]["team"]["abstention_threshold"] == 0.8
    assert body["answers"]["churn"]["abstention"] == "passed"


def test_the_payload_is_not_rewritten(client: TestClient, router: Router) -> None:
    payload = _post(client, {"state": ENGLISH, "questions": QUESTIONS}).json()
    team = payload["answers"]["team"]
    assert (team["answer_confidence"], team["confidence"]) == (0.7, 0.1187)
    assert team["probabilities"] == {"billing": 0.3, "tech": 0.7}


# ------------------------------------------------------------------------------------------ 400
@pytest.mark.parametrize(
    ("raw", "detail"),
    [
        (b"{not json", "request body must be valid JSON"),
        (b"[]", "request body must be an object with a 'questions' field"),
        (b'{"state": "x"}', "request body must be an object with a 'questions' field"),
        (b'{"questions": {}}', "'state' is required"),
        (b'{"state": null, "questions": {}}', "'state' is required"),
        (b'{"state": "x", "questions": []}', "'questions' must be an object"),
        (b'{"state": "\\ud800", "questions": {}}', "request body contains an unpaired surrogate"),
    ],
)
def test_bad_requests(client: TestClient, raw: bytes, detail: str) -> None:
    response = client.post(
        "/v1/systemone", content=raw, headers={"content-type": "application/json"}
    )
    assert response.status_code == 400
    assert _detail(response).startswith(detail)


# ------------------------------------------------------------------------------------------ 413
def _choice(count: int) -> dict[str, Any]:
    return {"type": "choice", "instructions": "x", "criteria": [f"o{i}" for i in range(count)]}


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        (
            {
                "state": "x",
                "questions": {f"q{i}": {"type": "noul", "instructions": "x"} for i in range(65)},
            },
            "too many questions (65 > 64)",
        ),
        (
            {"state": "x", "questions": {"q": _choice(101)}},
            "too many choice options for 'q' (101 > 100)",
        ),
        (
            {
                "state": "x",
                "questions": {"q": {"type": "score", "instructions": "x", "criteria": ["l"] * 33}},
            },
            "too many score levels for 'q' (33 > 32)",
        ),
        (
            {"state": "x", "questions": {f"q{i}": _choice(90) for i in range(6)}},
            "too many answer options across questions (540 > 512)",
        ),
        ({"state": "x" * 50001, "questions": QUESTIONS}, "state too large (50001 > 50000 chars)"),
    ],
    ids=["questions", "choice-options", "score-levels", "total-options", "state"],
)
def test_limits(client: TestClient, body: dict[str, Any], detail: str) -> None:
    response = _post(client, body)
    assert response.status_code == 413
    assert _detail(response) == detail


def test_the_limits_are_inclusive(client: TestClient) -> None:
    body = {"state": "x" * 50000, "questions": {f"q{i}": _choice(8) for i in range(64)}}
    assert _post(client, body).status_code == 200


def test_body_size_limit(client: TestClient) -> None:
    raw = json.dumps({"state": "x", "questions": {}, "pad": "y" * (2 * 1024 * 1024)}).encode()
    response = client.post(
        "/v1/systemone", content=raw, headers={"content-type": "application/json"}
    )
    assert response.status_code == 413
    assert _detail(response) == "request body too large"


# ------------------------------------------------------------------------------------------ 422
@pytest.mark.parametrize(
    "key", ["hooks", "on_predict_start", "on_predict_end", "hooks_raise", "hooks_timeout"]
)
def test_server_side_controls_are_refused(client: TestClient, key: str) -> None:
    response = _post(client, {"state": "x", "questions": QUESTIONS, key: 1})
    assert response.status_code == 422
    assert _detail(response).startswith(f"{key} run inside the server process")


@pytest.mark.parametrize(
    ("controls", "detail"),
    [
        ({"min_confidence": 1.5}, "min_confidence must be a float in [0.0, 1.0], got 1.5"),
        ({"min_confidence": True}, "min_confidence must be a float in [0.0, 1.0], got True"),
        ({"max_len": "512"}, "max_len must be an integer"),
        ({"max_len": 0}, "max_len must be a positive integer"),
        ({"head_max_len": 9000}, "head_max_len exceeds server limit (9000 > 8192)"),
        ({"lang": True}, 'lang must be a language code string such as "de", or null'),
        ({"lang_guess": 3}, 'lang_guess must be a language code string such as "de", or null'),
    ],
)
def test_invalid_controls(client: TestClient, controls: dict[str, Any], detail: str) -> None:
    response = _post(client, {"state": "x", "questions": QUESTIONS, **controls})
    assert response.status_code == 422
    assert _detail(response) == detail


def test_an_unknown_task_is_a_422_naming_it(client: TestClient) -> None:
    response = _post(client, {"state": "x", "questions": QUESTIONS, "task": "summarise"})
    assert response.status_code == 422
    assert "unknown model 'summarise'" in _detail(response)


def test_a_value_error_from_inference_is_a_422_with_its_message(
    router_factory: Callable[..., Router], make_agent: Any
) -> None:
    agent = make_agent(error=ValueError("question 'q': unknown type 'rating'"))
    with TestClient(create_app(router_factory(agent))) as client:
        response = _post(client, {"state": "x", "questions": QUESTIONS})
    assert response.status_code == 422
    assert _detail(response) == "question 'q': unknown type 'rating'"


def test_any_other_failure_is_a_fixed_500(
    router_factory: Callable[..., Router], make_agent: Any
) -> None:
    agent = make_agent(error=RuntimeError("CUDA OOM in /home/secret/weights.safetensors"))
    with TestClient(create_app(router_factory(agent))) as client:
        response = _post(client, {"state": "x", "questions": QUESTIONS})
    assert response.status_code == 500
    assert response.json() == {"detail": "inference failed"}


# ---------------------------------------------------------------------------------- auth, health
def test_bearer_authentication(router: Router, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAYA_API_KEY", "s3cret")
    with TestClient(create_app(router)) as client:
        body = {"state": "x", "questions": QUESTIONS}
        assert _post(client, body).status_code == 401
        assert (
            _detail(_post(client, body, authorization="Bearer wrong"))
            == "invalid or missing bearer token"
        )
        latin1: Any = {
            "authorization": "Bearer s\N{LATIN SMALL LETTER E WITH ACUTE}cret".encode("latin-1")
        }
        assert client.post("/v1/systemone", json=body, headers=latin1).status_code == 401
        assert _post(client, body, authorization="Bearer s3cret").status_code == 200
        assert (
            _post(
                client, {"states": ["x"], "questions": QUESTIONS}, "/v1/systemone/batch"
            ).status_code
            == 401
        )
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/health", headers={"authorization": "Bearer s3cret"}).json()["loaded"]


def test_health(client: TestClient) -> None:
    body = client.get("/health").json()
    assert set(body) == {
        "status",
        "loaded",
        "revisions",
        "device",
        "device_is_preference",
        "checkpoint_devices",
        "cpu_fallbacks",
    }
    assert body["status"] == "ok"
    assert sorted(body["loaded"]) == ["english", "multilingual", "typed-decisions"]
    assert body["device"] == "cpu"
    assert body["device_is_preference"] is False
    assert body["cpu_fallbacks"]["english"] == {"count": 0, "last_reason": None}


def test_only_the_upstream_endpoints_exist(client: TestClient) -> None:
    for path in ("/ready", "/metrics", "/v1/decide", "/v1/route", "/api/v1/decide"):
        assert client.post(path, json={}).status_code == 404
    assert client.get("/ready").status_code == 404


# ----------------------------------------------------------------------------------------- batch
def test_a_batch_is_one_payload_per_state_in_order(client: TestClient, router: Router) -> None:
    response = _post(
        client,
        {"states": [ENGLISH, PORTUGUESE, ENGLISH], "questions": QUESTIONS},
        "/v1/systemone/batch",
    )
    assert response.status_code == 200
    assert re.fullmatch(r"inference;dur=\d+\.\d{2}", response.headers["server-timing"])
    body = response.json()
    assert set(body) == {"results", "total_usage"}
    assert [r["routing"]["model"] for r in body["results"]] == [
        "english",
        "multilingual",
        "english",
    ]
    assert body["results"][1] == _json(router.predict(PORTUGUESE, QUESTIONS))
    assert body["total_usage"] == {
        "input_tokens": sum(r["usage"]["input_tokens"] for r in body["results"]),
        "output_tokens": 0,
    }


@pytest.mark.parametrize(
    ("body", "status", "detail"),
    [
        (
            {"questions": QUESTIONS},
            400,
            "request body must be an object with 'states' and 'questions' fields",
        ),
        ({"states": [], "questions": QUESTIONS}, 400, "'states' must be a non-empty list"),
        ({"states": "x", "questions": QUESTIONS}, 400, "'states' must be a non-empty list"),
        ({"states": ["x"] * 65, "questions": QUESTIONS}, 413, "too many states in batch (65 > 64)"),
        (
            {"states": ["x"], "questions": QUESTIONS, "batch_size": 0},
            422,
            "batch_size must be a positive integer, got 0",
        ),
        (
            {"states": ["x"], "questions": QUESTIONS, "sort_by_length": "yes"},
            422,
            "sort_by_length must be a boolean",
        ),
        (
            {"states": ["x"], "questions": QUESTIONS, "hooks": []},
            422,
            "hooks run inside the server process",
        ),
    ],
)
def test_batch_errors(client: TestClient, body: dict[str, Any], status: int, detail: str) -> None:
    response = _post(client, body, "/v1/systemone/batch")
    assert response.status_code == status
    assert _detail(response).startswith(detail)


# ------------------------------------------------------------------------------------------ 503
def test_admission_is_bounded(
    router_factory: Callable[..., Router], make_agent: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LAYA_MAX_CONCURRENT", "1")
    release = threading.Event()
    started = threading.Event()

    class Blocking(make_agent):  # type: ignore[misc]
        def system_one(self, state: Any, questions: Any, **kwargs: Any) -> Any:
            started.set()
            release.wait(timeout=10)
            return super().system_one(state, questions, **kwargs)

    app = create_app(router_factory(Blocking()))

    async def scenario() -> tuple[int, int, Mapping[str, str]]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://laya") as client:
            body = {"state": "x", "questions": QUESTIONS}
            first = asyncio.create_task(client.post("/v1/systemone", json=body))
            await asyncio.to_thread(started.wait, 10)
            second = await client.post("/v1/systemone", json=body)
            release.set()
            return (await first).status_code, second.status_code, second.headers

    first, second, headers = asyncio.run(scenario())
    assert (first, second) == (200, 503)
    assert headers["retry-after"] == "1"


# ------------------------------------------------- RemoteEngine speaks this contract end to end
class AppTransport:
    """HttpTransport over the upstream app in-process (no socket, no network)."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.seen: list[tuple[str, Mapping[str, str]]] = []

    def post(
        self, url: str, body: bytes, headers: Mapping[str, str], timeout: float
    ) -> HttpResponse:
        self.seen.append((url, dict(headers)))
        response = self.client.post(url, content=body, headers=dict(headers))
        return HttpResponse(response.status_code, response.content, dict(response.headers))


def test_remote_engine_returns_what_the_server_returns(client: TestClient, router: Router) -> None:
    engine = RemoteEngine("http://laya/", transport=AppTransport(client))
    assert engine.predict(PORTUGUESE, QUESTIONS, min_confidence=0.5) == _json(
        router.predict(PORTUGUESE, QUESTIONS, min_confidence=0.5)
    )
    other = {"flag": {"type": "noul", "instructions": "Flag?"}}
    payloads = engine.predict_batch(
        [
            {"state": ENGLISH, "questions": QUESTIONS},
            {"state": ENGLISH, "questions": other},
            {"state": PORTUGUESE, "questions": QUESTIONS, "model": "typed"},
            {"state": PORTUGUESE, "questions": QUESTIONS},
        ]
    )
    assert [set(p["answers"]) for p in payloads] == [
        set(QUESTIONS),
        {"flag"},
        set(QUESTIONS),
        set(QUESTIONS),
    ]
    assert [p["routing"]["model"] for p in payloads] == [
        "english",
        "english",
        "typed-decisions",
        "multilingual",
    ]


def test_remote_engine_reports_server_errors(client: TestClient) -> None:
    engine = RemoteEngine("http://laya", transport=AppTransport(client))
    with pytest.raises(RemoteEngineError, match=r"HTTP 413: too many choice options") as error:
        engine.predict("x", {"q": _choice(101)})
    assert (error.value.status, error.value.detail) == (
        413,
        "too many choice options for 'q' (101 > 100)",
    )
    with pytest.raises(RemoteEngineError) as error:
        engine.predict("x", QUESTIONS, task="summarise")
    assert error.value.status == 422


def test_remote_engine_sends_the_bearer(router: Router, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAYA_API_KEY", "s3cret")
    with TestClient(create_app(router)) as client:
        transport = AppTransport(client)
        assert RemoteEngine("http://laya", api_key="s3cret", transport=transport).predict(
            "x", QUESTIONS
        )
        assert transport.seen[-1][1]["Authorization"] == "Bearer s3cret"
        with pytest.raises(RemoteEngineError) as error:
            RemoteEngine("http://laya", transport=transport).predict("x", QUESTIONS)
        assert error.value.status == 401
