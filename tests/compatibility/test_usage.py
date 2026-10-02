"""The ``usage`` block of real payloads (weights-gated): tokens, truncation, collapsed options."""

from __future__ import annotations

from typing import Any

import pytest
from laya import Router

pytestmark = pytest.mark.weights

SHORT = "My card was charged twice."
ONE: dict[str, Any] = {"refund": {"type": "noul", "instructions": "Is a refund requested?"}}
TWO: dict[str, Any] = {**ONE, "angry": {"type": "noul", "instructions": "Is the customer angry?"}}


def test_a_short_state_fits(weighted_router: Router) -> None:
    usage = weighted_router.predict(SHORT, ONE, model="english")["usage"]
    assert usage["input_tokens"] > 0
    assert usage["output_tokens"] == 0
    assert usage["state_tokens"] > 0
    assert usage["state_tokens_dropped"] == 0
    assert usage["truncated"] is False
    assert usage["truncated_questions"] == []
    assert "options" not in usage


def test_every_question_is_a_row(weighted_router: Router) -> None:
    one = weighted_router.predict(SHORT, ONE, model="english")["usage"]["input_tokens"]
    two = weighted_router.predict(SHORT, TWO, model="english")["usage"]["input_tokens"]
    assert two > one


def test_a_long_state_reports_its_truncation(weighted_router: Router) -> None:
    state = " ".join(["The customer explains the billing problem again in detail."] * 400)
    usage = weighted_router.predict(state, ONE, model="english", max_len=128)["usage"]
    assert usage["truncated"] is True
    assert usage["state_tokens_dropped"] > 0
    assert usage["truncated_questions"] == ["refund"]


def test_options_that_collapse_are_reported(weighted_router: Router) -> None:
    labels = [f"category number {i} about a fairly long description" for i in range(60)]
    questions = {"cat": {"type": "choice", "instructions": "Which category?", "criteria": labels}}
    usage = weighted_router.predict(SHORT, questions, model="english", head_max_len=96)["usage"]
    collapsed = usage["options"]["cat"]
    assert collapsed["total"] == 60
    assert collapsed["distinct"] < collapsed["total"]
    assert "tokens_per_option" in collapsed


def test_batch_items_carry_their_own_usage(weighted_router: Router) -> None:
    payloads = weighted_router.predict_batch(
        [
            {"state": SHORT, "questions": ONE, "model": "english"},
            {"state": SHORT, "questions": TWO, "model": "english"},
        ]
    )
    assert payloads[1]["usage"]["input_tokens"] > payloads[0]["usage"]["input_tokens"]
