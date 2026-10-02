"""FakeProvider: scripted replies for tests and local development (no network, no key)."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from laya_platform.llm.types import Completion


@dataclass
class FakeProvider:
    """Replies in order: a dict (sent as JSON), a raw string, a Completion, or an exception."""

    replies: list[Any] = field(default_factory=list)
    model: str = "fake-llm"
    calls: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def answering(cls, *replies: Any) -> FakeProvider:
        return cls(list(replies))

    def extend(self, replies: Iterable[Any]) -> None:
        self.replies.extend(replies)

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
        self.calls.append(
            {
                "model": model,
                "system": system,
                "prompt": prompt,
                "schema": schema,
                "max_tokens": max_tokens,
                "timeout": timeout,
                "effort": effort,
            }
        )
        if not self.replies:
            raise AssertionError("FakeProvider has no reply left")
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        if isinstance(reply, Completion):
            return reply
        text = reply if isinstance(reply, str) else json.dumps(reply)
        return Completion(text=text, model=model, input_tokens=100, output_tokens=10, stop="end")
