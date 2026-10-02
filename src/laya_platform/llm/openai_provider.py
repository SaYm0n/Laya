"""OpenAI and every OpenAI-compatible endpoint through the official ``openai`` SDK.

One adapter for OpenAI itself and, with ``base_url``, Gemini's OpenAI-compatible endpoint,
DeepSeek, OpenRouter, Ollama and vLLM (Chat Completions). Support for a constrained reply varies by
server, hence ``json_mode``: ``json_schema`` (strict schema), ``json_object`` (any JSON; the prompt
carries the schema) or ``none``. The reply is validated locally either way. Retries and backoff are
the SDK's.
"""

from __future__ import annotations

import os
from typing import Any

from laya_platform.llm.types import Completion, LLMProviderError, Stop

STOPS: dict[str, Stop] = {"stop": "end", "length": "max_tokens", "content_filter": "refusal"}
#: Local servers (Ollama, vLLM) take no key, but the SDK requires a non-empty one.
NO_KEY = "unused"


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        api_key_env: str | None = None,
        base_url: str | None = None,
        local: bool = False,
        max_retries: int = 2,
        json_mode: str = "json_schema",
        max_tokens_param: str = "max_tokens",
        client: Any = None,
    ) -> None:
        if client is None:
            import openai

            api_key = os.environ.get(api_key_env) if api_key_env else None
            if api_key_env and not api_key:
                raise ValueError(f"set ${api_key_env} for the OpenAI-compatible provider")
            if api_key is None and not local:
                raise ValueError("an external OpenAI-compatible provider needs api_key_env")
            client = openai.OpenAI(
                api_key=api_key or NO_KEY, base_url=base_url, max_retries=max_retries
            )
        self._client = client
        self._json_mode = json_mode
        self._max_tokens_param = max_tokens_param

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
        import openai

        request: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            self._max_tokens_param: max_tokens,
            "timeout": timeout,
        }
        if self._json_mode == "json_schema":
            request["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "decision", "schema": schema, "strict": True},
            }
        elif self._json_mode == "json_object":
            request["response_format"] = {"type": "json_object"}
        if effort:
            request["reasoning_effort"] = effort
        try:
            response = self._client.chat.completions.create(**request)
        except openai.APIError as exc:
            raise LLMProviderError(f"{type(exc).__name__}: {exc}") from None
        choice = response.choices[0]
        usage = response.usage
        stop = STOPS.get(str(choice.finish_reason), "other")
        if getattr(choice.message, "refusal", None):
            stop = "refusal"
        return Completion(
            text=choice.message.content or "",
            model=str(response.model),
            input_tokens=int(usage.prompt_tokens) if usage else 0,
            output_tokens=int(usage.completion_tokens) if usage else 0,
            stop=stop,
        )
