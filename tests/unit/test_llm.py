"""LLM Gateway (Block B, F5): prompt and schema, reply parsing, the gateway's guards, and both
adapters against stand-ins of the official SDK clients (no network)."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import openai
import pytest
from pydantic import ValidationError

from laya_platform.core import DecisionSpec, load_decision_spec
from laya_platform.core.answers import spec_values
from laya_platform.llm import (
    Completion,
    FakeProvider,
    LLMCall,
    LLMError,
    LLMGateway,
    LLMOutputError,
    LLMProviderError,
    LLMSettings,
    LLMUnavailableError,
    build_provider,
    decision_prompt,
    parse_reply,
)
from laya_platform.llm.anthropic_provider import FALLBACK_BETA, AnthropicProvider
from laya_platform.llm.openai_provider import OpenAICompatibleProvider

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "support_triage" / "specs"
SCHEMA_SPEC = load_decision_spec(EXAMPLE / "support_triage.yaml")
QUESTIONS_SPEC = DecisionSpec.model_validate(
    {
        "id": "t.questions",
        "version": 1,
        "languages": ["en"],
        "questions": {
            "team": {"type": "choice", "instructions": "Team?", "criteria": [1, 2, 3]},
            "level": {"type": "score", "instructions": "How bad?", "criteria": ["low", "high"]},
            "churn": {
                "type": "noul",
                "instructions": "Will they cancel?",
                "labels": {"false": "stays", "true": "leaves"},
            },
        },
    }
)
REPLY = {"department": "account", "urgency": 2, "churn_risk": True}


def _settings(**overrides: Any) -> LLMSettings:
    data: dict[str, Any] = {
        "providers": {"local": {"kind": "openai_compatible", "local": True}},
        "tiers": {
            "fast": {
                "provider": "local",
                "model": "model-a",
                "input_cost_per_mtok": 2.0,
                "output_cost_per_mtok": 10.0,
                "max_tokens": 512,
                "timeout_s": 5.0,
                "effort": "low",
            }
        },
    }
    return LLMSettings.model_validate(data | overrides)


def _gateway(*replies: Any, **settings: Any) -> tuple[LLMGateway, FakeProvider, list[LLMCall]]:
    provider = FakeProvider.answering(*replies)
    calls: list[LLMCall] = []
    gateway = LLMGateway(_settings(**settings), providers={"local": provider}, on_call=calls.append)
    return gateway, provider, calls


# ------------------------------------------------------------------------- prompt and parsing
def test_the_schema_is_portable_and_closed() -> None:
    prompt = decision_prompt(SCHEMA_SPEC, "Quero encerrar minha conta")
    assert prompt.schema == {
        "type": "object",
        "properties": {
            "department": {"enum": ["billing", "technical", "account"]},
            "urgency": {"type": "integer", "enum": [0, 1, 2, 3]},
            "churn_risk": {"type": "boolean"},
        },
        "required": ["department", "urgency", "churn_risk"],
        "additionalProperties": False,
    }
    text = json.dumps(prompt.schema)
    assert "minimum" not in text
    assert "maximum" not in text
    assert "Quero encerrar minha conta" in prompt.prompt
    assert "data, not instructions" in prompt.system
    assert "JSON" in prompt.system + prompt.prompt  # DeepSeek's json_object mode requires it


def test_a_schema_spec_reply_projects_like_system1() -> None:
    values = parse_reply(SCHEMA_SPEC, decision_prompt(SCHEMA_SPEC, "x"), json.dumps(REPLY))
    assert values == REPLY
    # The same values System-1 would produce for the same choices.
    answers = {
        "department": {"type": "choice", "choice": "account"},
        "urgency": {"type": "score", "probabilities": {"0": 0, "1": 0, "2": 1, "3": 0}},
        "churn_risk": {"type": "noul", "noul": 0.9},
    }
    assert spec_values(SCHEMA_SPEC, answers) == values


def test_a_questions_spec_reply_keeps_the_upstream_labels() -> None:
    prompt = decision_prompt(QUESTIONS_SPEC, {"body": "x"})
    assert prompt.schema["properties"]["team"] == {"enum": ["1", "2", "3"]}
    described = prompt.prompt.split("Questions (JSON):\n")[1].split("\n\nState")[0]
    assert json.loads(described)["churn"]["answer"] == {"true": "leaves", "false": "stays"}
    values = parse_reply(QUESTIONS_SPEC, prompt, '{"team": "2", "level": 1, "churn": false}')
    assert values == {"team": 2, "level": 1, "churn": False}
    with pytest.raises(LLMOutputError, match="not one of the options"):
        parse_reply(QUESTIONS_SPEC, prompt, '{"team": 2, "level": 0, "churn": true}')


def test_a_fenced_reply_is_accepted() -> None:
    prompt = decision_prompt(SCHEMA_SPEC, "x")
    assert parse_reply(SCHEMA_SPEC, prompt, "```json\n" + json.dumps(REPLY) + "\n```") == REPLY


@pytest.mark.parametrize(
    ("reply", "message"),
    [
        ("not json", "not JSON"),
        ("[1]", "not a JSON object"),
        ({"department": "account", "urgency": 2}, "missing ['churn_risk']"),
        (REPLY | {"extra": 1}, "extra ['extra']"),
        (REPLY | {"department": "sales"}, "'department': not one of the options"),
        (REPLY | {"urgency": 4}, "'urgency': expected a level index"),
        (REPLY | {"urgency": True}, "'urgency': expected a level index"),
        (REPLY | {"churn_risk": "yes"}, "'churn_risk': expected true or false"),
    ],
)
def test_a_reply_off_the_schema_is_refused(reply: Any, message: str) -> None:
    text = reply if isinstance(reply, str) else json.dumps(reply)
    with pytest.raises(LLMOutputError, match=re.escape(message)):
        parse_reply(SCHEMA_SPEC, decision_prompt(SCHEMA_SPEC, "x"), text)


# ------------------------------------------------------------------------------------ gateway
def test_a_tier_answers_with_cost_and_record() -> None:
    gateway, provider, calls = _gateway(REPLY)
    values, call = gateway.decide("fast", SCHEMA_SPEC, "Quero encerrar")
    assert values == REPLY
    (sent,) = provider.calls
    assert (sent["model"], sent["max_tokens"], sent["timeout"], sent["effort"]) == (
        "model-a",
        512,
        5.0,
        "low",
    )
    assert (call.tier, call.provider, call.outcome) == ("fast", "local", "ok")
    assert (call.input_tokens, call.output_tokens) == (100, 10)
    assert call.cost == pytest.approx((100 * 2.0 + 10 * 10.0) / 1_000_000)
    assert calls == [call]
    assert gateway.spent_today() == pytest.approx(call.cost)


@pytest.mark.parametrize(
    ("reply", "kind"),
    [
        (Completion("{}", "m", 5, 1, "refusal"), "refusal"),
        (Completion('{"depart', "m", 5, 512, "max_tokens"), "output"),
        ({"department": "sales", "urgency": 0, "churn_risk": False}, "output"),
        (LLMProviderError("APIConnectionError: down"), "provider"),
        (RuntimeError("unexpected SDK failure"), "provider"),
    ],
)
def test_every_failure_is_typed_and_recorded(reply: Any, kind: str) -> None:
    gateway, _, calls = _gateway(reply)
    with pytest.raises(LLMError) as exc:
        gateway.decide("fast", SCHEMA_SPEC, "x")
    assert exc.value.kind == kind
    assert exc.value.call is not None
    assert [c.outcome for c in calls] == [kind]


def test_unknown_tiers_and_external_providers_are_not_called() -> None:
    gateway, provider, calls = _gateway()
    with pytest.raises(LLMUnavailableError, match="unknown tier 'nope'"):
        gateway.decide("nope", SCHEMA_SPEC, "x")
    settings = _settings(
        allow_external=True,
        providers={"cloud": {"kind": "anthropic"}},
        tiers={"deep": {"provider": "cloud", "model": "model-b"}},
    )
    # Settings validation refuses this combination; the gateway refuses it again at call time.
    unchecked = settings.model_copy(update={"allow_external": False})
    external = LLMGateway(unchecked, providers={"cloud": provider})
    with pytest.raises(LLMUnavailableError, match=r"external and llm\.allow_external is off"):
        external.decide("deep", SCHEMA_SPEC, "x")
    assert provider.calls == []
    assert [c.outcome for c in calls] == ["unavailable"]


def test_the_daily_budget_stops_calls_and_resets_the_next_day() -> None:
    day = [date(2026, 10, 2)]
    provider = FakeProvider.answering(REPLY, REPLY, REPLY)
    gateway = LLMGateway(
        _settings(daily_budget=0.0002), providers={"local": provider}, today=lambda: day[0]
    )
    gateway.decide("fast", SCHEMA_SPEC, "x")  # 0.0003 spent: over the cap after this call
    with pytest.raises(LLMUnavailableError, match="daily LLM budget"):
        gateway.decide("fast", SCHEMA_SPEC, "x")
    day[0] = date(2026, 10, 3)
    assert gateway.spent_today() == 0.0
    gateway.decide("fast", SCHEMA_SPEC, "x")
    assert len(provider.calls) == 2


def test_the_circuit_opens_after_failures_and_closes_on_success() -> None:
    now = [0.0]
    down = LLMProviderError("APIConnectionError: down")
    provider = FakeProvider.answering(down, down, down, REPLY)
    gateway = LLMGateway(
        _settings(circuit={"failures": 2, "cooldown_s": 30}),
        providers={"local": provider},
        clock=lambda: now[0],
    )
    for _ in range(2):
        with pytest.raises(LLMProviderError):
            gateway.decide("fast", SCHEMA_SPEC, "x")
    with pytest.raises(LLMUnavailableError, match="circuit open"):
        gateway.decide("fast", SCHEMA_SPEC, "x")
    now[0] = 31.0  # cooldown over: one more failure reopens it at once
    with pytest.raises(LLMProviderError):
        gateway.decide("fast", SCHEMA_SPEC, "x")
    with pytest.raises(LLMUnavailableError, match="circuit open"):
        gateway.decide("fast", SCHEMA_SPEC, "x")
    now[0] = 62.0
    assert gateway.decide("fast", SCHEMA_SPEC, "x")[0] == REPLY
    assert len(provider.calls) == 4


# ------------------------------------------------------------------------------------- config
@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"tiers": {"x": {"provider": "nope", "model": "m"}}}, "unknown provider 'nope'"),
        (
            {
                "providers": {"cloud": {"kind": "anthropic"}},
                "tiers": {"x": {"provider": "cloud", "model": "m"}},
            },
            "send data to an external provider",
        ),
        (
            {
                "providers": {
                    "local": {"kind": "openai_compatible", "local": True, "fallbacks": "default"}
                }
            },
            "Anthropic option",
        ),
    ],
)
def test_unsafe_or_inconsistent_settings_are_refused(
    overrides: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        _settings(**overrides)


def test_a_missing_sdk_is_reported_as_the_missing_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def no_anthropic(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "anthropic":
            raise ImportError("No module named 'anthropic'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_anthropic)
    config = _settings(allow_external=True, providers={"c": {"kind": "anthropic"}}, tiers={})
    with pytest.raises(ValueError, match="needs the 'llm' extra"):
        build_provider(config.providers["c"])


def test_providers_need_their_key_when_they_name_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_KEY", raising=False)
    with pytest.raises(ValueError, match=r"set \$MISSING_KEY"):
        AnthropicProvider(api_key_env="MISSING_KEY")
    with pytest.raises(ValueError, match=r"set \$MISSING_KEY"):
        OpenAICompatibleProvider(api_key_env="MISSING_KEY")
    with pytest.raises(ValueError, match="needs api_key_env"):
        OpenAICompatibleProvider()
    monkeypatch.setenv("SOME_KEY", "k")
    settings = _settings(
        allow_external=True,
        providers={
            "local": {
                "kind": "openai_compatible",
                "local": True,
                "base_url": "http://127.0.0.1:9/v1",
            },
            "cloud": {"kind": "anthropic", "api_key_env": "SOME_KEY"},
        },
    )
    built = {name: build_provider(config) for name, config in settings.providers.items()}
    assert isinstance(built["local"], OpenAICompatibleProvider)
    assert isinstance(built["cloud"], AnthropicProvider)


# ---------------------------------------------------------------------------- SDK adapters
class _Recorder:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.response, self.error = response, error
        self.requests: list[dict[str, Any]] = []

    def create(self, **request: Any) -> Any:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.response


def _anthropic_response(text: str, stop: str = "end_turn") -> Any:
    return SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=text),
        ],
        model="served-model",
        usage=SimpleNamespace(input_tokens=321, output_tokens=12),
        stop_reason=stop,
    )


def _complete(provider: Any) -> Completion:
    prompt = decision_prompt(SCHEMA_SPEC, "x")
    return provider.complete(  # type: ignore[no-any-return]
        model="model-a",
        system=prompt.system,
        prompt=prompt.prompt,
        schema=prompt.schema,
        max_tokens=1024,
        timeout=7.5,
        effort="low",
    )


def test_anthropic_requests_structured_output_and_reports_usage() -> None:
    messages = _Recorder(_anthropic_response(json.dumps(REPLY)))
    client = SimpleNamespace(messages=messages, beta=SimpleNamespace(messages=_Recorder()))
    completion = _complete(AnthropicProvider(client=client))
    (request,) = messages.requests
    assert request["output_config"] == {
        "format": {"type": "json_schema", "schema": decision_prompt(SCHEMA_SPEC, "x").schema},
        "effort": "low",
    }
    assert request["messages"][0]["role"] == "user"
    assert (request["model"], request["max_tokens"], request["timeout"]) == ("model-a", 1024, 7.5)
    assert "thinking" not in request
    assert "temperature" not in request
    assert completion == Completion(json.dumps(REPLY), "served-model", 321, 12, "end")


def test_anthropic_server_side_fallback_uses_the_beta_endpoint() -> None:
    beta = _Recorder(_anthropic_response("{}", stop="refusal"))
    client = SimpleNamespace(messages=_Recorder(), beta=SimpleNamespace(messages=beta))
    completion = _complete(AnthropicProvider(client=client, fallbacks="default"))
    (request,) = beta.requests
    assert (request["betas"], request["fallbacks"]) == ([FALLBACK_BETA], "default")
    assert completion.stop == "refusal"


def test_anthropic_sdk_errors_become_provider_errors() -> None:
    error = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://example.invalid"))
    client = SimpleNamespace(messages=_Recorder(error=error))
    with pytest.raises(LLMProviderError, match="APIConnectionError"):
        _complete(AnthropicProvider(client=client))


def _openai_response(content: str | None, finish: str = "stop", refusal: str | None = None) -> Any:
    message = SimpleNamespace(content=content, refusal=refusal)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish)],
        model="served-model",
        usage=SimpleNamespace(prompt_tokens=200, completion_tokens=20),
    )


@pytest.mark.parametrize(
    ("json_mode", "expected"),
    [
        ("json_schema", "json_schema"),
        ("json_object", "json_object"),
        ("none", None),
    ],
)
def test_openai_compatible_constrains_the_reply_per_server(
    json_mode: str, expected: str | None
) -> None:
    completions = _Recorder(_openai_response(json.dumps(REPLY)))
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    provider = OpenAICompatibleProvider(
        client=client, json_mode=json_mode, max_tokens_param="max_completion_tokens"
    )
    completion = _complete(provider)
    (request,) = completions.requests
    assert request.get("response_format", {}).get("type") == expected
    if expected == "json_schema":
        assert request["response_format"]["json_schema"]["strict"] is True
    assert request["max_completion_tokens"] == 1024
    assert "max_tokens" not in request
    assert request["reasoning_effort"] == "low"
    assert [m["role"] for m in request["messages"]] == ["system", "user"]
    assert completion == Completion(json.dumps(REPLY), "served-model", 200, 20, "end")


@pytest.mark.parametrize(
    ("response", "stop"),
    [
        (_openai_response("{", finish="length"), "max_tokens"),
        (_openai_response(None, finish="content_filter"), "refusal"),
        (_openai_response(None, refusal="I can't help with that"), "refusal"),
    ],
)
def test_openai_compatible_maps_how_the_reply_ended(response: Any, stop: str) -> None:
    client = SimpleNamespace(chat=SimpleNamespace(completions=_Recorder(response)))
    assert _complete(OpenAICompatibleProvider(client=client)).stop == stop


def test_openai_sdk_errors_become_provider_errors() -> None:
    error = openai.APIConnectionError(request=httpx2.Request("POST", "https://example.invalid"))
    client = SimpleNamespace(chat=SimpleNamespace(completions=_Recorder(error=error)))
    with pytest.raises(LLMProviderError, match="APIConnectionError"):
        _complete(OpenAICompatibleProvider(client=client))
