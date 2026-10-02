"""LLM Gateway (Block B, F5): System-2 through the official SDKs, by tier, never by model ID.

The ``llm`` extra brings the SDKs; only the provider modules import them, and only when a provider
of that kind is configured.
"""

from laya_platform.llm.config import CircuitConfig, LLMSettings, ProviderConfig, TierConfig
from laya_platform.llm.decisions import DecisionPrompt, decision_prompt, parse_reply
from laya_platform.llm.fake import FakeProvider
from laya_platform.llm.gateway import LLMGateway, MemorySpend, SpendStore, build_provider
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

__all__ = [
    "CircuitConfig",
    "Completion",
    "DecisionPrompt",
    "FakeProvider",
    "LLMCall",
    "LLMError",
    "LLMGateway",
    "LLMOutputError",
    "LLMProvider",
    "LLMProviderError",
    "LLMRefusalError",
    "LLMSettings",
    "LLMUnavailableError",
    "MemorySpend",
    "ProviderConfig",
    "SpendStore",
    "TierConfig",
    "build_provider",
    "decision_prompt",
    "parse_reply",
]
