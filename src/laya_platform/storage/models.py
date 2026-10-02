"""Tables of the platform's own store (SQLite in development, PostgreSQL later; same models).

Nothing here stores a raw state: an audit row keeps the HMAC of the input, the answers and the
comparison with the incumbent system. Schema changes go through Alembic migrations
(``laya_platform/storage/migrations``); a test checks the models and the migrations agree.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint
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
    # Block C (F7): which System-1 answered -- "router" or a specialist "name@version".
    engine: Mapped[str | None] = mapped_column(String(160))


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


class FlagEvent(Base):
    """Who changed a flag, from what to what (feature flags are operations worth auditing)."""

    __tablename__ = "flag_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    old: Mapped[Any] = mapped_column(JSON, nullable=True)
    new: Mapped[Any] = mapped_column(JSON, nullable=True)
    actor: Mapped[str] = mapped_column(String(128))


class LLMSpend(Base):
    """System-2 spend per UTC day, shared by every gateway process."""

    __tablename__ = "llm_spend"

    day: Mapped[str] = mapped_column(String(10), primary_key=True)  # YYYY-MM-DD
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Specialist(Base):
    """One registered specialist version and where it is in its lifecycle (F7)."""

    __tablename__ = "specialists"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_specialists_name_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), index=True)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SpecialistEvent(Base):
    """Every lifecycle transition, with its evidence and who made it."""

    __tablename__ = "specialist_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    version: Mapped[str] = mapped_column(String(64))
    from_status: Mapped[str | None] = mapped_column(String(16))
    to_status: Mapped[str] = mapped_column(String(16))
    actor: Mapped[str] = mapped_column(String(128))
    reason: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class SpecialistPointer(Base):
    """Which specialist answers a spec (``production``) or runs beside it (``shadow``).

    ``previous_*`` is the version a rollback returns to, without a deploy.
    """

    __tablename__ = "specialist_pointers"

    spec_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    version: Mapped[str] = mapped_column(String(64))
    previous_name: Mapped[str | None] = mapped_column(String(128))
    previous_version: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    actor: Mapped[str] = mapped_column(String(128))


class ChallengerResult(Base):
    """A shadow specialist's answer to a real decision, compared with what was served."""

    __tablename__ = "challenger_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    trace_id: Mapped[str] = mapped_column(String(36), index=True)
    spec_id: Mapped[str] = mapped_column(String(128), index=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    version: Mapped[str] = mapped_column(String(64))
    values: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    agreement: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text)
