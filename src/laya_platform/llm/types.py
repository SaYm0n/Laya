"""What an LLM provider returns, and the ways a System-2 call can fail."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

Stop = Literal["end", "max_tokens", "refusal", "other"]


@dataclass(frozen=True)
class Completion:
    """One provider response, reduced to what the gateway needs."""

    text: str
    model: str
    input_tokens: int
    output_tokens: int
    stop: Stop


class LLMProvider(Protocol):
    """One API family. ``schema`` is the JSON schema the reply must follow."""

    def complete(
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        schema: dict[str, Any],
        max_tokens: int,
        timeout: float,
        effort: str | None,
    ) -> Completion: ...


@dataclass(frozen=True)
class LLMCall:
    """What one System-2 call cost and how it ended (audited and exported as metrics)."""

    tier: str
    provider: str
    model: str
    outcome: str  # "ok" or the kind of the LLMError
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    latency_ms: float = 0.0
    error: str | None = None
    #: PII placeholders put in the state before it was sent (``laya_platform.privacy``).
    redacted: int = 0


class LLMError(Exception):
    """A System-2 call that produced no usable answer; ``call`` says what it cost."""

    kind = "error"

    def __init__(self, message: str, call: LLMCall | None = None) -> None:
        super().__init__(message)
        self.call = call


class LLMUnavailableError(LLMError):
    """Not attempted: unknown tier, external provider not allowed, budget spent, circuit open."""

    kind = "unavailable"


class LLMProviderError(LLMError):
    """The provider failed (after the SDK's own retries)."""

    kind = "provider"


class LLMRefusalError(LLMError):
    """The model declined to answer."""

    kind = "refusal"


class LLMOutputError(LLMError):
    """The reply was cut off or does not match the schema."""

    kind = "output"
