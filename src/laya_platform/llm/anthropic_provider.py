"""Claude through the official ``anthropic`` SDK (Messages API with structured outputs).

The reply is constrained with ``output_config.format`` (a JSON schema); retries and backoff are the
SDK's (``max_retries``). No ``thinking`` parameter is sent, so each model runs its own default;
depth is tuned with the tier's ``effort``. Sampling parameters are never sent (current models
refuse them).
"""

from __future__ import annotations

import os
from typing import Any

from laya_platform.llm.types import Completion, LLMProviderError, Stop

#: Server-side refusal fallback, ``fallbacks: "default"`` form (beta).
FALLBACK_BETA = "server-side-fallback-2026-07-01"
STOPS: dict[str, Stop] = {"end_turn": "end", "max_tokens": "max_tokens", "refusal": "refusal"}


class AnthropicProvider:
    def __init__(
        self,
        *,
        api_key_env: str | None = None,
        base_url: str | None = None,
        max_retries: int = 2,
        fallbacks: str | None = None,
        client: Any = None,
    ) -> None:
        if client is None:
            import anthropic

            # Unset: the SDK resolves ANTHROPIC_API_KEY, a profile or workload identity itself.
            api_key = os.environ.get(api_key_env) if api_key_env else None
            if api_key_env and not api_key:
                raise ValueError(f"set ${api_key_env} for the Anthropic provider")
            client = anthropic.Anthropic(
                api_key=api_key, base_url=base_url, max_retries=max_retries
            )
        self._client = client
        self._fallbacks = fallbacks

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
    ) -> Completion:
        import anthropic

        output_config: dict[str, Any] = {"format": {"type": "json_schema", "schema": schema}}
        if effort:
            output_config["effort"] = effort
        request: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
            "output_config": output_config,
            "timeout": timeout,
        }
        try:
            if self._fallbacks:
                response = self._client.beta.messages.create(
                    **request, betas=[FALLBACK_BETA], fallbacks=self._fallbacks
                )
            else:
                response = self._client.messages.create(**request)
        except anthropic.APIError as exc:
            raise LLMProviderError(f"{type(exc).__name__}: {exc}") from None
        text = "".join(block.text for block in response.content if block.type == "text")
        return Completion(
            text=text,
            model=str(response.model),
            input_tokens=int(response.usage.input_tokens),
            output_tokens=int(response.usage.output_tokens),
            stop=STOPS.get(str(response.stop_reason), "other"),
        )
