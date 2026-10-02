"""DecisionPolicy: one System-1 decision -> auto, review (a human) or escalate (System-2).

Each answer falls in a band of the spec's policy by its ``answer_confidence`` (no usable value ->
the catch-all band, never the entropy ``confidence``); the decision takes the most cautious band
of its answers. Automation is then blocked -- the outcome drops from ``auto`` to ``review`` -- when
anything says the model could not see what it decided on, as the upstream reports it:

* ``abstained``: the upstream confidence gate abstained, or the answer is ``low_confidence``;
* ``truncated``: the state did not fit (``usage.truncated``);
* ``options_collapsed``: a question lost options to the head budget (``usage.options``);
* ``language``: the detected language is not one the spec was written for;
* ``risk``: the spec is ``high`` risk;
* ``never_auto_if``: a decided value the spec never automates.

Pure: no I/O, nothing acts. Whether an ``auto`` outcome is acted on is the mode's business
(``gated`` and its canary, in the gateway).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from laya.confidence import answer_confidence_value

from laya_platform.core.spec import DecisionSpec, Outcome

CAUTION: dict[str, int] = {"auto": 0, "review": 1, "escalate": 2}


@dataclass(frozen=True)
class PolicyDecision:
    outcome: Outcome
    bands: dict[str, Outcome]
    #: Why the decision is not automated (empty for ``auto``).
    reasons: list[str] = field(default_factory=list)


def _same(value: Any, expected: Any) -> bool:
    """Equal, without Python's ``True == 1``."""
    return bool(value == expected) and isinstance(value, bool) == isinstance(expected, bool)


def _primary(language: str) -> str:
    return language.split("-")[0].lower()


def evaluate(
    spec: DecisionSpec, payload: Mapping[str, Any], values: Mapping[str, Any]
) -> PolicyDecision | None:
    """The policy's verdict on one decision; None when the spec has no policy."""
    policy = spec.policy
    if policy is None:
        return None
    answers: Mapping[str, Any] = payload.get("answers") or {}
    usage: Mapping[str, Any] = payload.get("usage") or {}
    routing: Mapping[str, Any] = payload.get("routing") or {}
    bands = {qid: policy.outcome(answer_confidence_value(a)) for qid, a in answers.items()}
    worst = max(bands.values(), key=CAUTION.__getitem__, default=policy.bands[-1].outcome)
    blocks = [
        f"abstained:{qid}"
        for qid, answer in answers.items()
        if answer.get("abstention") == "abstained" or answer.get("low_confidence")
    ]
    if usage.get("truncated"):
        blocks.append("truncated")
    blocks += [f"options_collapsed:{qid}" for qid in usage.get("options") or {}]
    detected = (routing.get("detection") or {}).get("language")
    if detected and _primary(str(detected)) not in {_primary(lang) for lang in spec.languages}:
        blocks.append(f"language:{detected}")
    if spec.risk == "high":
        blocks.append("risk:high")
    blocks += [
        f"never_auto_if:{rule.question}"
        for rule in policy.never_auto_if
        if rule.question in values and _same(values[rule.question], rule.equals)
    ]
    outcome = worst
    if outcome == "auto" and blocks:
        outcome = "review"
    reasons = [f"band:{qid}={band}" for qid, band in bands.items() if band != "auto"]
    return PolicyDecision(outcome, bands, [] if outcome == "auto" else reasons + blocks)


def canary_selected(input_hmac: str, share: float) -> bool:
    """Whether a decision falls in a canary of ``share`` of the traffic.

    Deterministic per input (the first 32 bits of its HMAC), so the same input always gets the
    same treatment and the audit trail shows which decisions were in the canary.
    """
    return int(input_hmac[:8], 16) / 0x1_0000_0000 < share
