"""One real System-2 call per adapter, against a provider you choose (``--run-llm``; never in CI).

The model comes from the environment, never from this file (model IDs live in configuration):

    LAYA_PLATFORM_TEST_ANTHROPIC_MODEL      a Claude model ID; credentials as the SDK resolves them
    LAYA_PLATFORM_TEST_OPENAI_MODEL         a model of an OpenAI-compatible endpoint
    LAYA_PLATFORM_TEST_OPENAI_BASE_URL      optional (unset: OpenAI)
    LAYA_PLATFORM_TEST_OPENAI_KEY_ENV       the variable holding its key (default OPENAI_API_KEY)
    LAYA_PLATFORM_TEST_OPENAI_JSON_MODE     json_schema (default), json_object or none

Only synthetic text is sent.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from laya_platform.core import load_decision_spec
from laya_platform.llm import LLMGateway, LLMSettings

SPEC = load_decision_spec(
    Path(__file__).resolve().parents[2]
    / "examples"
    / "support_triage"
    / "specs"
    / "support_triage.yaml"
)
STATE = "Fui cobrado duas vezes na fatura deste mês e, se não resolverem hoje, vou cancelar."


def _decide(provider: dict[str, Any], model: str) -> dict[str, Any]:
    settings = LLMSettings.model_validate(
        {
            "allow_external": True,
            "providers": {"p": provider},
            "tiers": {"t": {"provider": "p", "model": model, "max_tokens": 4096, "timeout_s": 120}},
        }
    )
    values, call = LLMGateway(settings).decide("t", SPEC, STATE)
    assert call.outcome == "ok"
    assert call.input_tokens > 0
    return values


def _check(values: dict[str, Any]) -> None:
    assert set(values) == {"department", "urgency", "churn_risk"}
    assert values["department"] in ("billing", "technical", "account")
    assert values["urgency"] in (0, 1, 2, 3)
    assert values["churn_risk"] is True  # the state says so plainly


@pytest.mark.llm
def test_a_real_anthropic_call() -> None:
    model = os.environ.get("LAYA_PLATFORM_TEST_ANTHROPIC_MODEL")
    if not model:
        pytest.skip("set LAYA_PLATFORM_TEST_ANTHROPIC_MODEL")
    _check(_decide({"kind": "anthropic"}, model))


@pytest.mark.llm
def test_a_real_openai_compatible_call() -> None:
    model = os.environ.get("LAYA_PLATFORM_TEST_OPENAI_MODEL")
    if not model:
        pytest.skip("set LAYA_PLATFORM_TEST_OPENAI_MODEL")
    provider = {
        "kind": "openai_compatible",
        "base_url": os.environ.get("LAYA_PLATFORM_TEST_OPENAI_BASE_URL"),
        "api_key_env": os.environ.get("LAYA_PLATFORM_TEST_OPENAI_KEY_ENV", "OPENAI_API_KEY"),
        "json_mode": os.environ.get("LAYA_PLATFORM_TEST_OPENAI_JSON_MODE", "json_schema"),
    }
    _check(_decide(provider, model))
