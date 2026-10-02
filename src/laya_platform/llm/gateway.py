"""LLM Gateway: one entry point to every configured tier, with the safety the platform needs.

Before a call: the tier exists, its provider may receive the data (``allow_external``), today's
budget is not spent and the provider's circuit is closed. After it: tokens and cost are counted,
the reply is validated against the spec, and every outcome -- answered or not -- is reported to
``on_call`` (the HTTP gateway turns it into metrics and audit). Retries are the SDKs' own.

The budget is kept by a ``SpendStore`` -- in memory by default, in the platform's database when
the HTTP gateway builds it, so every process shares one daily budget. The circuit stays per
process (each process sees its own failures). A call that starts under the budget may end over it.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Any, Protocol

from laya_platform.core.spec import DecisionSpec
from laya_platform.llm.config import LLMSettings, ProviderConfig
from laya_platform.llm.decisions import decision_prompt, parse_reply
from laya_platform.llm.types import (
    Completion,
    LLMCall,
    LLMError,
    LLMOutputError,
    LLMProvider,
    LLMProviderError,
    LLMRefusalError,
    LLMUnavailableError,
)
from laya_platform.privacy import redact


def build_provider(config: ProviderConfig) -> LLMProvider:
    try:
        return _build_provider(config)
    except ImportError as exc:
        raise ValueError(f"provider kind {config.kind!r} needs the 'llm' extra: {exc}") from None


def _build_provider(config: ProviderConfig) -> LLMProvider:
    if config.kind == "anthropic":
        from laya_platform.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(
            api_key_env=config.api_key_env,
            base_url=config.base_url,
            max_retries=config.max_retries,
            fallbacks=config.fallbacks,
        )
    from laya_platform.llm.openai_provider import OpenAICompatibleProvider

    return OpenAICompatibleProvider(
        api_key_env=config.api_key_env,
        base_url=config.base_url,
        local=config.local,
        max_retries=config.max_retries,
        json_mode=config.json_mode,
        max_tokens_param=config.max_tokens_param,
    )


class SpendStore(Protocol):
    """Where the daily System-2 spend is kept. Days are UTC ``YYYY-MM-DD``."""

    def llm_spent(self, day: str) -> float: ...

    def add_llm_spend(self, day: str, amount: float) -> None: ...


class MemorySpend:
    """Per-process spend (the default; the gateway uses its database to share it)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_day: dict[str, float] = {}

    def llm_spent(self, day: str) -> float:
        with self._lock:
            return self._by_day.get(day, 0.0)

    def add_llm_spend(self, day: str, amount: float) -> None:
        with self._lock:
            self._by_day[day] = self._by_day.get(day, 0.0) + amount


@dataclass
class _Circuit:
    failures: int = 0
    opened_at: float | None = None


class LLMGateway:
    def __init__(
        self,
        settings: LLMSettings,
        *,
        providers: Mapping[str, LLMProvider] | None = None,
        on_call: Callable[[LLMCall], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        today: Callable[[], date] = lambda: datetime.now(UTC).date(),
        spend: SpendStore | None = None,
    ) -> None:
        self.settings = settings
        self._providers = dict(providers) if providers is not None else {}
        for name, config in settings.providers.items():
            if name not in self._providers:
                self._providers[name] = build_provider(config)
        self._on_call = on_call
        self._clock = clock
        self._today = today
        self._lock = threading.Lock()
        self._circuits = {name: _Circuit() for name in settings.providers}
        self._spend: SpendStore = spend if spend is not None else MemorySpend()

    @property
    def tiers(self) -> list[str]:
        return list(self.settings.tiers)

    def spent_today(self) -> float:
        return self._spend.llm_spent(self._today().isoformat())

    def decide(self, tier: str, spec: DecisionSpec, state: Any) -> tuple[dict[str, Any], LLMCall]:
        """Ask ``tier`` the spec's questions about ``state``: spec values and the call's record.

        Raises an :class:`LLMError` (with ``call`` set when the provider was reached).
        """
        redacted = 0
        if tier in self.settings.tiers and self._redacts(tier):
            state, counts = redact(state)
            redacted = sum(counts.values())
        prompt = decision_prompt(spec, state)
        try:
            completion, call = self._complete(tier, prompt.system, prompt.prompt, prompt.schema)
        except LLMError as exc:
            if exc.call is not None and redacted:
                exc.call = replace(exc.call, redacted=redacted)
            raise
        call = replace(call, redacted=redacted)
        try:
            values = parse_reply(spec, prompt, completion.text)
        except LLMOutputError as exc:
            raise self._failed(call, exc) from None
        self._report(call)
        return values, call

    def _redacts(self, tier: str) -> bool:
        settings = self.settings
        local = settings.is_local(tier)
        return settings.redact_pii and (not local or settings.redact_local)

    # ------------------------------------------------------------------------------- internals
    def _complete(
        self, tier: str, system: str, prompt: str, schema: dict[str, Any]
    ) -> tuple[Completion, LLMCall]:
        config = self.settings.tiers.get(tier)
        if config is None:
            raise self._failed(
                LLMCall(tier, "", "", "ok"), LLMUnavailableError(f"unknown tier {tier!r}")
            )
        refused = self._refusal_before_calling(tier, config.provider)
        if refused is not None:
            raise self._failed(LLMCall(tier, config.provider, config.model, "ok"), refused)
        provider = self._providers[config.provider]
        started = time.perf_counter()
        try:
            completion = provider.complete(
                model=config.model,
                system=system,
                prompt=prompt,
                schema=schema,
                max_tokens=config.max_tokens,
                timeout=config.timeout_s,
                effort=config.effort,
            )
        except Exception as exc:  # noqa: BLE001 -- System-2 must never break a System-1 decision
            error = (
                exc
                if isinstance(exc, LLMProviderError)
                else LLMProviderError(f"{type(exc).__name__}: {exc}")
            )
            self._record_failure(config.provider)
            elapsed = (time.perf_counter() - started) * 1000.0
            failed = LLMCall(tier, config.provider, config.model, error.kind, latency_ms=elapsed)
            raise self._failed(failed, error) from None
        self._record_success(config.provider)
        cost = config.cost(completion.input_tokens, completion.output_tokens)
        if cost:
            self._spend.add_llm_spend(self._today().isoformat(), cost)
        call = LLMCall(
            tier=tier,
            provider=config.provider,
            model=completion.model or config.model,
            outcome="ok",
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
            cost=cost,
            latency_ms=(time.perf_counter() - started) * 1000.0,
        )
        if completion.stop == "refusal":
            raise self._failed(call, LLMRefusalError(f"tier {tier!r} declined to answer"))
        if completion.stop == "max_tokens":
            raise self._failed(call, LLMOutputError(f"tier {tier!r} hit max_tokens"))
        return completion, call

    def _refusal_before_calling(self, tier: str, provider: str) -> LLMUnavailableError | None:
        if not self.settings.allow_external and not self.settings.is_local(tier):
            return LLMUnavailableError(f"tier {tier!r} is external and llm.allow_external is off")
        budget = self.settings.daily_budget
        if budget is not None and self.spent_today() >= budget:
            return LLMUnavailableError(f"the daily LLM budget ({budget}) is spent")
        with self._lock:
            circuit = self._circuits[provider]
            open_for = self._clock() - (circuit.opened_at or 0.0)
            if circuit.opened_at is not None and open_for < self.settings.circuit.cooldown_s:
                return LLMUnavailableError(f"provider {provider!r} is failing; circuit open")
        return None

    def _record_failure(self, provider: str) -> None:
        with self._lock:
            circuit = self._circuits[provider]
            circuit.failures += 1
            if circuit.failures >= self.settings.circuit.failures:
                # Open; calls resume after the cooldown and the next failure reopens it.
                circuit.opened_at = self._clock()

    def _record_success(self, provider: str) -> None:
        with self._lock:
            self._circuits[provider] = _Circuit()

    def _failed(self, call: LLMCall, error: LLMError) -> LLMError:
        error.call = LLMCall(
            tier=call.tier,
            provider=call.provider,
            model=call.model,
            outcome=error.kind,
            input_tokens=call.input_tokens,
            output_tokens=call.output_tokens,
            cost=call.cost,
            latency_ms=call.latency_ms,
            error=str(error),
        )
        self._report(error.call)
        return error

    def _report(self, call: LLMCall) -> None:
        if self._on_call is not None:
            self._on_call(call)
