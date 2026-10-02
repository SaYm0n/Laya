"""LLM configuration: providers (API families) and tiers (a provider + a model + its price).

Code depends on tiers, never on model IDs: concrete IDs live only in configuration files, dated.
A provider that is not ``local`` sends data outside your infrastructure, so it is refused unless
``allow_external`` is set (forbidden by default; for real data only under DG-1).
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Name = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["anthropic", "openai_compatible"]
    #: Environment variable holding the key. Unset: the SDK's own resolution (``anthropic``) or no
    #: key at all (a ``local`` OpenAI-compatible server such as Ollama or vLLM).
    api_key_env: str | None = None
    base_url: str | None = None
    #: Runs on your own infrastructure: data never leaves it.
    local: bool = False
    max_retries: Annotated[int, Field(ge=0, le=10)] = 2
    #: OpenAI-compatible only: how the reply is constrained (support varies by server).
    json_mode: Literal["json_schema", "json_object", "none"] = "json_schema"
    max_tokens_param: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    #: Anthropic only: server-side refusal fallback (beta), routed by refusal category.
    fallbacks: Literal["default"] | None = None

    @model_validator(mode="after")
    def _options_of_the_kind(self) -> Self:
        if self.fallbacks is not None and self.kind != "anthropic":
            raise ValueError("'fallbacks' is an Anthropic option")
        return self


class TierConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Name
    model: Name
    input_cost_per_mtok: Annotated[float, Field(ge=0.0)] = 0.0
    output_cost_per_mtok: Annotated[float, Field(ge=0.0)] = 0.0
    max_tokens: Annotated[int, Field(ge=1)] = 16000
    timeout_s: Annotated[float, Field(gt=0.0)] = 60.0
    #: Passed as the provider's effort control when set (Anthropic ``output_config.effort``,
    #: OpenAI ``reasoning_effort``).
    effort: str | None = None

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens * self.input_cost_per_mtok + output_tokens * self.output_cost_per_mtok
        ) / 1_000_000


class CircuitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    failures: Annotated[int, Field(ge=1)] = 5
    cooldown_s: Annotated[float, Field(gt=0.0)] = 60.0


class LLMSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    allow_external: bool = False
    providers: dict[Name, ProviderConfig] = Field(default_factory=dict)
    tiers: dict[Name, TierConfig] = Field(default_factory=dict)
    #: Spend cap per UTC day, in the currency of the tier prices; unset means no cap.
    daily_budget: Annotated[float, Field(ge=0.0)] | None = None
    circuit: CircuitConfig = Field(default_factory=CircuitConfig)
    #: Redact PII (``laya_platform.privacy``) before a state goes to an external provider; and,
    #: with ``redact_local``, to local ones too.
    redact_pii: bool = True
    redact_local: bool = False

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        for name, tier in self.tiers.items():
            if tier.provider not in self.providers:
                raise ValueError(f"tier {name!r} names unknown provider {tier.provider!r}")
        external = sorted(name for name in self.tiers if not self.is_local(name))
        if external and not self.allow_external:
            raise ValueError(
                f"tier(s) {external} send data to an external provider: set llm.allow_external "
                "(forbidden by default; real data only under an approved DG-1)"
            )
        return self

    def is_local(self, tier: str) -> bool:
        return self.providers[self.tiers[tier].provider].local
