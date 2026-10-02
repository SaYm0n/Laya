"""The DecisionEngine adapters, without weights: conformance, pass-through and explicit refusals."""

from __future__ import annotations

import http.server
import json
import threading
from collections.abc import Iterator, Mapping
from typing import Any

import pytest
from laya import Router

from laya_platform.core import (
    DecisionEngine,
    EngineError,
    MissingRuntimeError,
    RemoteEngineError,
    UnsupportedControlError,
    UnsupportedOperationError,
)
from laya_platform.core.adapters import (
    AgentEngine,
    FakeAnswer,
    FakeEngine,
    HttpResponse,
    OnnxEngine,
    RemoteEngine,
    UpstreamRouterEngine,
    UrllibTransport,
)
from laya_platform.core.adapters import onnx as onnx_adapter
from laya_platform.core.adapters.fake import FAKE_ROUTE
from laya_platform.core.types import DecisionPayload, DecisionRequest

QUESTIONS: dict[str, Any] = {
    "team": {
        "type": "choice",
        "instructions": "Team?",
        "criteria": {"a": None, "b": "second", "c": None},
    },
    "level": {"type": "score", "instructions": "Level?", "criteria": ["low", "mid", "high"]},
    "flag": {"type": "noul", "instructions": "Flag?"},
}
OTHER: dict[str, Any] = {"only": {"type": "noul", "instructions": "Only?"}}


class RecordingAgent:
    """Agent-shaped object that returns one marker payload per state and records every call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any, dict[str, Any]]] = []

    def system_one(self, state: Any, questions: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("system_one", state, kwargs))
        return {"state": state, "questions": list(questions)}

    def predict_batch(
        self, states: list[Any], questions: Any, **kwargs: Any
    ) -> list[dict[str, Any]]:
        self.calls.append(("predict_batch", list(states), {"questions": list(questions), **kwargs}))
        return [{"state": state, "questions": list(questions)} for state in states]


# --------------------------------------------------------------------------------- conformance
def _engines() -> list[DecisionEngine]:
    return [
        UpstreamRouterEngine(Router()),
        AgentEngine(RecordingAgent()),
        OnnxEngine(RecordingAgent()),
        RemoteEngine("http://localhost:8000"),
        FakeEngine(),
    ]


@pytest.mark.parametrize("engine", _engines(), ids=lambda engine: type(engine).__name__)
def test_every_adapter_is_a_decision_engine(engine: DecisionEngine) -> None:
    assert isinstance(engine, DecisionEngine)


def test_a_non_agent_is_refused() -> None:
    with pytest.raises(TypeError, match="needs an object with system_one"):
        AgentEngine(object())  # type: ignore[arg-type]


# -------------------------------------------------------------------------- single checkpoint
@pytest.fixture
def agent() -> RecordingAgent:
    return RecordingAgent()


@pytest.mark.parametrize("engine_type", [AgentEngine, OnnxEngine])
def test_predict_forwards_and_returns_the_agent_payload(
    engine_type: type[AgentEngine], agent: RecordingAgent
) -> None:
    engine = engine_type(agent)
    payload = engine.predict(
        "hi", QUESTIONS, lang="pt", max_len=128, head_max_len=32, min_confidence=0.5
    )
    assert payload == {"state": "hi", "questions": ["team", "level", "flag"]}  # type: ignore[comparison-overlap]
    assert agent.calls == [
        (
            "system_one",
            "hi",
            {"lang": "pt", "max_len": 128, "head_max_len": 32, "min_confidence": 0.5},
        )
    ]
    assert engine.agent is agent


@pytest.mark.parametrize("control", ["model", "task", "lang_guess"])
def test_routing_controls_are_refused_not_ignored(agent: RecordingAgent, control: str) -> None:
    controls: Any = {control: "pt"}
    with pytest.raises(UnsupportedControlError, match=rf"one fixed checkpoint.*\['{control}'\]"):
        AgentEngine(agent).predict("hi", QUESTIONS, **controls)
    assert agent.calls == []


def test_route_is_unsupported(agent: RecordingAgent) -> None:
    with pytest.raises(UnsupportedOperationError, match="nothing to route"):
        AgentEngine(agent).route("hi")
    with pytest.raises(NotImplementedError):
        OnnxEngine(agent).route("hi", QUESTIONS, lang="pt")


def test_batch_groups_by_questions_and_controls_and_keeps_order(agent: RecordingAgent) -> None:
    requests: list[DecisionRequest] = [
        {"state": "s0", "questions": QUESTIONS},
        {"state": "s1", "questions": OTHER},
        {"state": "s2", "questions": QUESTIONS},
        {"state": "s3", "questions": QUESTIONS, "max_len": 64},
        {"state": "s4", "questions": QUESTIONS, "lang": "pt"},
        {"state": "s5", "questions": QUESTIONS},
    ]
    payloads = AgentEngine(agent).predict_batch(requests, batch_size=8, sort_by_length=True)
    assert [p["state"] for p in payloads] == ["s0", "s1", "s2", "s3", "s4", "s5"]  # type: ignore[typeddict-item]
    assert [(states, kwargs) for _, states, kwargs in agent.calls] == [
        (
            ["s0", "s2", "s5"],
            {"questions": ["team", "level", "flag"], "batch_size": 8, "sort_by_length": True},
        ),
        (["s1"], {"questions": ["only"], "batch_size": 8, "sort_by_length": True}),
        (
            ["s3"],
            {
                "questions": ["team", "level", "flag"],
                "max_len": 64,
                "batch_size": 8,
                "sort_by_length": True,
            },
        ),
        (
            ["s4"],
            {
                "questions": ["team", "level", "flag"],
                "lang": "pt",
                "batch_size": 8,
                "sort_by_length": True,
            },
        ),
    ]


def test_option_order_is_positional_so_reordered_questions_are_not_grouped(
    agent: RecordingAgent,
) -> None:
    reordered = {"team": {**QUESTIONS["team"], "criteria": {"c": None, "b": "second", "a": None}}}
    same = {"team": QUESTIONS["team"]}
    AgentEngine(agent).predict_batch(
        [{"state": "x", "questions": same}, {"state": "y", "questions": reordered}]
    )
    assert len(agent.calls) == 2


def test_an_empty_batch(agent: RecordingAgent) -> None:
    assert AgentEngine(agent).predict_batch([]) == []
    assert agent.calls == []


@pytest.mark.parametrize(
    ("requests", "error", "message"),
    [
        ([{"questions": QUESTIONS}], ValueError, "request 0 is missing required key 'state'"),
        ([{"state": "x"}], ValueError, "request 0 is missing required key 'questions'"),
        (["x"], TypeError, "request 0 must be a dict"),
        ("xy", TypeError, "requests must be a sequence"),
        (
            [{"state": "x", "questions": QUESTIONS, "model": "ml"}],
            UnsupportedControlError,
            "one fixed checkpoint",
        ),
        (
            [{"state": "x", "questions": QUESTIONS, "max_lenght": 9}],
            UnsupportedControlError,
            r"unknown field\(s\) \['max_lenght'\]",
        ),
    ],
)
def test_bad_batches(
    agent: RecordingAgent, requests: Any, error: type[Exception], message: str
) -> None:
    with pytest.raises(error, match=message):
        AgentEngine(agent).predict_batch(requests)
    assert agent.calls == []


def test_a_short_batch_result_is_an_error() -> None:
    class Short(RecordingAgent):
        def predict_batch(
            self, states: list[Any], questions: Any, **kwargs: Any
        ) -> list[dict[str, Any]]:
            return []

    with pytest.raises(EngineError, match="returned 0 results for 1 states"):
        AgentEngine(Short()).predict_batch([{"state": "x", "questions": QUESTIONS}])


def test_loading_a_checkpoint_needs_torch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("laya_platform.core.adapters.agent.torch_available", lambda: False)
    with pytest.raises(MissingRuntimeError, match="needs torch"):
        AgentEngine.from_checkpoint("convaiinnovations/laya")


def test_loading_an_onnx_export_needs_its_runtimes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(onnx_adapter, "missing_runtimes", lambda: ["onnxruntime"])
    with pytest.raises(MissingRuntimeError, match=r"needs \['onnxruntime'\]"):
        OnnxEngine.from_export("convaiinnovations/laya", "laya.onnx")
    assert issubclass(MissingRuntimeError, ImportError)


def test_the_onnx_runtime_is_not_a_default_dependency() -> None:
    assert "onnxruntime" in onnx_adapter.missing_runtimes()


# ------------------------------------------------------------------------------------------- fake
def test_fake_defaults_to_the_first_option() -> None:
    answers = FakeEngine().predict("s", QUESTIONS)["answers"]
    assert answers["team"]["choice"] == "a"
    assert answers["team"]["probabilities"] == {"a": 1.0, "b": 0.0, "c": 0.0}
    assert (answers["level"]["score"], answers["level"]["legend"]) == (
        0.0,
        {"0": "low", "1": "mid", "2": "high"},
    )
    assert answers["flag"]["noul"] == 0.0


def test_fake_scripted_answers() -> None:
    engine = FakeEngine(
        {"team": FakeAnswer("b", 0.8), "level": FakeAnswer(2, 0.6), "flag": FakeAnswer(True, 0.9)},
        default_probability=0.5,
    )
    payload = engine.predict("s", QUESTIONS)
    team, level, flag = (payload["answers"][q] for q in ("team", "level", "flag"))
    assert (team["choice"], team["answer_confidence"]) == ("b", 0.8)
    assert team["probabilities"] == {"a": 0.1, "b": 0.8, "c": 0.1}
    assert level["probabilities"] == {"0": 0.2, "1": 0.2, "2": 0.6}
    assert level["score"] == 1.4
    assert (flag["noul"], flag["answer_confidence"], flag["confidence"]) == (0.9, 0.9, 0.9)
    assert payload["model"] == "fake-engine"
    assert payload["routing"] == FAKE_ROUTE
    assert payload["usage"]["input_tokens"] == 0
    assert payload == engine.predict("other state", QUESTIONS)  # never reads the state


def test_fake_batch_route_calls_and_errors() -> None:
    engine = FakeEngine(
        route={
            "model": "multilingual",
            "repo": None,
            "reason": "r",
            "detection": None,
            "workflow": None,
        }
    )
    payloads = engine.predict_batch(
        [{"state": "a", "questions": QUESTIONS}, {"state": "b", "questions": OTHER}],
        min_confidence=0.5,
    )
    assert [set(p["answers"]) for p in payloads] == [set(QUESTIONS), {"only"}]
    assert engine.route("a", lang="pt")["model"] == "multilingual"
    assert [(call.method, dict(call.controls)) for call in engine.calls] == [
        ("predict_batch", {"min_confidence": 0.5}),
        ("route", {"lang": "pt"}),
    ]
    failing = FakeEngine(error=TimeoutError("engine down"))
    with pytest.raises(TimeoutError, match="engine down"):
        failing.predict("s", QUESTIONS)
    with pytest.raises(TimeoutError):
        failing.route("s")


@pytest.mark.parametrize(
    ("answers", "questions", "message"),
    [
        ({"team": FakeAnswer("z")}, QUESTIONS, "'z' is not one of"),
        ({"team": FakeAnswer("a", 0.3)}, QUESTIONS, r"must be in \(1/3, 1\]"),
        ({"level": FakeAnswer(3)}, QUESTIONS, "level index in range"),
        ({"level": FakeAnswer(True)}, QUESTIONS, "level index in range"),
        ({"flag": FakeAnswer("yes")}, QUESTIONS, "True or False"),
        (
            {"one": FakeAnswer("a", 0.9)},
            {"one": {"type": "choice", "instructions": "x", "criteria": ["a"]}},
            "single option",
        ),
        ({}, {"q": {"type": "rating", "instructions": "x"}}, "got type 'rating'"),
        ({}, {"q": {"type": "choice", "instructions": "x", "criteria": []}}, "non-empty criteria"),
    ],
)
def test_fake_refuses_inconsistent_scripts(
    answers: dict[str, FakeAnswer], questions: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        FakeEngine(answers).predict("s", questions)


@pytest.mark.parametrize("probability", [0.0, -0.1, 1.5])
def test_fake_default_probability_range(probability: float) -> None:
    with pytest.raises(ValueError, match="default_probability"):
        FakeEngine(default_probability=probability)


# ----------------------------------------------------------------------------------------- remote
class StubTransport:
    def __init__(self, *responses: HttpResponse) -> None:
        self.responses = list(responses)
        self.sent: list[tuple[str, Any, Mapping[str, str], float]] = []

    def post(
        self, url: str, body: bytes, headers: Mapping[str, str], timeout: float
    ) -> HttpResponse:
        self.sent.append((url, json.loads(body), dict(headers), timeout))
        return self.responses.pop(0)


def _ok(body: Any) -> HttpResponse:
    return HttpResponse(200, json.dumps(body).encode())


PAYLOAD: DecisionPayload = {
    "model": "laya-rl-agent",
    "answers": {},
    "usage": {"input_tokens": 1, "output_tokens": 0},
}


def test_remote_predict_sends_the_wire_body() -> None:
    transport = StubTransport(_ok(PAYLOAD))
    engine = RemoteEngine("https://laya.example/base/", api_key="k", timeout=5, transport=transport)
    assert engine.predict({"body": "olá"}, QUESTIONS, model="ml", min_confidence=0.7) == PAYLOAD
    url, body, headers, timeout = transport.sent[0]
    assert url == "https://laya.example/base/v1/systemone"
    assert body == {
        "state": {"body": "olá"},
        "questions": QUESTIONS,
        "model": "ml",
        "min_confidence": 0.7,
    }
    assert headers == {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": "Bearer k",
    }
    assert timeout == 5.0
    assert engine.base_url == "https://laya.example/base"
    assert "k" not in repr(engine).replace("laya.example", "")


def test_remote_without_a_key_sends_no_authorization() -> None:
    transport = StubTransport(_ok(PAYLOAD))
    RemoteEngine("http://laya", transport=transport).predict("x", QUESTIONS)
    assert "Authorization" not in transport.sent[0][2]


def test_remote_batch_groups_and_chunks_into_the_server_limit() -> None:
    def results(count: int, tag: str) -> HttpResponse:
        return _ok({"results": [{"tag": tag, "i": i} for i in range(count)], "total_usage": {}})

    transport = StubTransport(results(64, "a"), results(6, "a"), results(1, "b"), results(1, "c"))
    requests: list[DecisionRequest] = [
        {"state": f"s{i}", "questions": QUESTIONS} for i in range(70)
    ]
    requests.insert(3, {"state": "other", "questions": OTHER})
    requests.append({"state": "pt", "questions": QUESTIONS, "lang": "pt"})
    payloads = RemoteEngine("http://laya", transport=transport).predict_batch(
        requests, batch_size=16
    )
    assert len(payloads) == 72
    assert payloads[3] == {"tag": "b", "i": 0}
    assert payloads[-1] == {"tag": "c", "i": 0}
    bodies = [body for _, body, _, _ in transport.sent]
    assert [len(body["states"]) for body in bodies] == [64, 6, 1, 1]
    assert bodies[0]["batch_size"] == 16
    assert bodies[3]["lang"] == "pt"
    assert {url for url, *_ in transport.sent} == {"http://laya/v1/systemone/batch"}


@pytest.mark.parametrize(
    ("response", "status", "detail"),
    [
        (
            HttpResponse(413, b'{"detail": "too many questions (65 > 64)"}'),
            413,
            "too many questions (65 > 64)",
        ),
        (HttpResponse(502, b"<html>bad gateway</html>"), 502, None),
        (HttpResponse(200, b"<html>not json</html>"), None, None),
        (HttpResponse(200, b"[1, 2]"), None, None),
        (HttpResponse(200, b'{"no": "answers"}'), None, None),
    ],
)
def test_remote_errors(response: HttpResponse, status: int | None, detail: str | None) -> None:
    engine = RemoteEngine("http://laya", transport=StubTransport(response))
    with pytest.raises(RemoteEngineError) as error:
        engine.predict("x", QUESTIONS)
    assert (error.value.status if response.status != 200 else None, error.value.detail) == (
        status,
        detail,
    )


def test_remote_batch_with_a_wrong_result_count() -> None:
    engine = RemoteEngine("http://laya", transport=StubTransport(_ok({"results": []})))
    with pytest.raises(RemoteEngineError, match="did not return one result per state"):
        engine.predict_batch([{"state": "x", "questions": QUESTIONS}])


def test_remote_refusals() -> None:
    engine = RemoteEngine("http://laya", transport=StubTransport())
    with pytest.raises(UnsupportedControlError, match="lang_guess must be a language code"):
        engine.predict("x", QUESTIONS, lang_guess=lambda state: "pt")
    with pytest.raises(UnsupportedControlError, match="lang_guess"):
        engine.predict_batch(
            [{"state": "x", "questions": QUESTIONS, "lang_guess": lambda state: "pt"}]
        )
    with pytest.raises(UnsupportedControlError, match="unknown field"):
        engine.predict_batch([{"state": "x", "questions": QUESTIONS, "hooks": []}])  # type: ignore[typeddict-unknown-key]
    with pytest.raises(UnsupportedOperationError, match="no routing endpoint"):
        engine.route("x")
    with pytest.raises(TypeError, match="cannot be sent as JSON"):
        engine.predict({"when": object()}, QUESTIONS)
    with pytest.raises(TypeError, match="cannot be sent as JSON"):
        engine.predict("x", QUESTIONS, min_confidence=float("nan"))


@pytest.mark.parametrize(
    "url",
    [
        "laya:8000",
        "ftp://laya",
        "file:///etc/passwd",
        "http://",
        "http://laya?x=1",
        "http://laya#f",
    ],
)
def test_remote_base_url_must_be_plain_http(url: str) -> None:
    with pytest.raises(ValueError, match="base_url"):
        RemoteEngine(url)


@pytest.mark.parametrize("timeout", [0, -1.0, float("nan")])
def test_remote_timeout_must_be_positive(timeout: float) -> None:
    with pytest.raises(ValueError, match="timeout"):
        RemoteEngine("http://laya", timeout=timeout)


# ---------------------------------------------------------------- the stdlib transport, on loopback
@pytest.fixture
def loopback_server() -> Iterator[str]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # http.server's method name
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            payload: dict[str, Any]
            if body.get("state") == "fail":
                payload, status = {"detail": "state too large (9 > 8 chars)"}, 413
            else:
                payload, status = (
                    {**PAYLOAD, "echo": body, "auth": self.headers.get("Authorization")},
                    200,
                )
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args: Any) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def test_urllib_transport_round_trip(loopback_server: str, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "HTTP_PROXY",
        "http_proxy",
        "HTTPS_PROXY",
        "https_proxy",
        "ALL_PROXY",
        "all_proxy",
    ):
        monkeypatch.delenv(name, raising=False)
    engine = RemoteEngine(loopback_server, api_key="k", transport=UrllibTransport())
    payload: Any = engine.predict("olá", QUESTIONS, lang="pt")
    assert payload["echo"] == {"state": "olá", "questions": QUESTIONS, "lang": "pt"}
    assert payload["auth"] == "Bearer k"
    with pytest.raises(RemoteEngineError) as error:
        engine.predict("fail", QUESTIONS)
    assert (error.value.status, error.value.detail) == (413, "state too large (9 > 8 chars)")


def test_urllib_transport_without_a_server(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RemoteEngineError, match="failed") as error:
        RemoteEngine("http://127.0.0.1:9", timeout=2).predict("x", QUESTIONS)
    assert error.value.status is None
