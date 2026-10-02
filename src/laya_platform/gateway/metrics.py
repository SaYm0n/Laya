"""Prometheus metrics of the gateway (own registry per app, exposed at ``/metrics``)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

from laya_platform.llm import LLMCall

CONFIDENCE_BUCKETS = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99, 1.0)


class GatewayMetrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.decisions = Counter(
            "laya_platform_decisions",
            "Decisions served by /api/v1/decide",
            ["spec", "mode"],
            registry=self.registry,
        )
        self.errors = Counter(
            "laya_platform_decision_errors",
            "Decisions that failed",
            ["spec"],
            registry=self.registry,
        )
        self.latency = Histogram(
            "laya_platform_decision_latency_seconds",
            "Engine time per decision",
            ["spec"],
            registry=self.registry,
        )
        self.confidence = Histogram(
            "laya_platform_answer_confidence",
            "answer_confidence of each answer (never the entropy confidence)",
            ["spec", "question"],
            buckets=CONFIDENCE_BUCKETS,
            registry=self.registry,
        )
        self.bands = Counter(
            "laya_platform_bands",
            "Answers per policy band",
            ["spec", "question", "band"],
            registry=self.registry,
        )
        self.agreement = Counter(
            "laya_platform_incumbent_agreement",
            "Answers compared with the incumbent system's decision",
            ["spec", "question", "agree"],
            registry=self.registry,
        )

        self.outcomes = Counter(
            "laya_platform_policy_outcomes",
            "Policy verdicts (auto, review, escalate)",
            ["spec", "mode", "outcome"],
            registry=self.registry,
        )
        self.acts = Counter(
            "laya_platform_acts",
            "Decisions returned with act=true (gated, auto band, inside the canary)",
            ["spec"],
            registry=self.registry,
        )
        self.reviews = Counter(
            "laya_platform_reviews_opened",
            "Decisions sent to the human review queue",
            ["spec"],
            registry=self.registry,
        )
        self.resolutions = Counter(
            "laya_platform_reviews_resolved",
            "Review items answered by a human",
            ["spec"],
            registry=self.registry,
        )
        self.llm_calls = Counter(
            "laya_platform_llm_calls",
            "System-2 calls by outcome (ok, unavailable, provider, refusal, output)",
            ["tier", "outcome"],
            registry=self.registry,
        )
        self.llm_tokens = Counter(
            "laya_platform_llm_tokens",
            "System-2 tokens",
            ["tier", "direction"],
            registry=self.registry,
        )
        self.llm_cost = Counter(
            "laya_platform_llm_cost",
            "System-2 spend, in the currency of the tier prices",
            ["tier"],
            registry=self.registry,
        )
        self.llm_latency = Histogram(
            "laya_platform_llm_latency_seconds",
            "System-2 time per call",
            ["tier"],
            registry=self.registry,
        )

        self.system2_agreement = Counter(
            "laya_platform_system2_agreement",
            "System-1 answers compared with System-2's on escalated decisions",
            ["spec", "question", "agree"],
            registry=self.registry,
        )
        self.served_by = Counter(
            "laya_platform_served_by",
            "Decisions per System-1 engine (router or specialist name@version)",
            ["spec", "engine"],
            registry=self.registry,
        )
        self.challenger = Counter(
            "laya_platform_challenger_agreement",
            "Shadow specialist answers compared with what was served",
            ["spec", "specialist", "question", "agree"],
            registry=self.registry,
        )
        self.challenger_errors = Counter(
            "laya_platform_challenger_errors",
            "Shadow specialist runs that failed",
            ["spec", "specialist"],
            registry=self.registry,
        )
        self.reviews_open = Gauge(
            "laya_platform_reviews_open",
            "Review items waiting for a human",
            registry=self.registry,
        )
        self.reviews_oldest = Gauge(
            "laya_platform_reviews_oldest_age_seconds",
            "Age of the oldest open review item (0 when none)",
            registry=self.registry,
        )

    def watch_reviews(self, stats: Callable[[], tuple[int, datetime | None]]) -> None:
        """Read the review queue at every scrape."""

        def oldest_age() -> float:
            oldest = stats()[1]
            if oldest is None:
                return 0.0
            if oldest.tzinfo is None:  # SQLite hands timestamps back naive (they are UTC)
                oldest = oldest.replace(tzinfo=UTC)
            return (datetime.now(UTC) - oldest).total_seconds()

        self.reviews_open.set_function(lambda: float(stats()[0]))
        self.reviews_oldest.set_function(oldest_age)

    def observe_llm(self, call: LLMCall) -> None:
        self.llm_calls.labels(call.tier, call.outcome).inc()
        self.llm_tokens.labels(call.tier, "input").inc(call.input_tokens)
        self.llm_tokens.labels(call.tier, "output").inc(call.output_tokens)
        self.llm_cost.labels(call.tier).inc(call.cost)
        if call.latency_ms:
            self.llm_latency.labels(call.tier).observe(call.latency_ms / 1000.0)

    def exposition(self) -> bytes:
        return generate_latest(self.registry)
