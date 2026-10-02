"""Prometheus metrics of the gateway (own registry per app, exposed at ``/metrics``)."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest

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

    def exposition(self) -> bytes:
        return generate_latest(self.registry)
