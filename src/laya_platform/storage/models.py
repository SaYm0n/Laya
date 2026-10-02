"""Tables of the platform's own store (SQLite in development, PostgreSQL later; same models).

Nothing here stores a raw state: an audit row keeps the HMAC of the input, the answers and the
comparison with the incumbent system. Schema changes go through Alembic migrations
(``laya_platform/storage/migrations``); a test checks the models and the migrations agree.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class AuditEvent(Base):
    """One decision made through the gateway (``/api/v1/decide``), in any mode."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )
    spec_id: Mapped[str] = mapped_column(String(128), index=True)
    spec_version: Mapped[int] = mapped_column(Integer)
    mode: Mapped[str] = mapped_column(String(16))
    model: Mapped[str | None] = mapped_column(String(64))
    input_hmac: Mapped[str] = mapped_column(String(64), index=True)
    answers: Mapped[dict[str, Any]] = mapped_column(JSON)
    incumbent: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    agreement: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    latency_ms: Mapped[float] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(Text)
    data_classification: Mapped[str] = mapped_column(String(16))
    # Block B (F6): the policy's verdict, whether it was acted on, and System-2 if it ran.
    outcome: Mapped[str | None] = mapped_column(String(16))
    act: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    reasons: Mapped[list[str] | None] = mapped_column(JSON)
    system2: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class ReviewItem(Base):
    """A decision waiting for a human (outcome ``review``, or an escalation System-2 could not
    answer). It holds no input: the caller, which has the input, shows it to its reviewers and
    posts the resolution back; the resolution becomes a label for training (F8, after DG-1)."""

    __tablename__ = "review_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(36), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    spec_id: Mapped[str] = mapped_column(String(128), index=True)
    spec_version: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text)
    suggestion: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolver: Mapped[str | None] = mapped_column(String(128))
    resolution: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class FeatureFlag(Base):
    """A runtime switch changed without a deploy (kill switch, per-spec mode)."""

    __tablename__ = "feature_flags"

    name: Mapped[str] = mapped_column(String(160), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
