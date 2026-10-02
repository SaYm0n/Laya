"""Database access: Alembic upgrade on start, audit writes, feature flags."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, delete, func, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from laya_platform.storage.models import (
    AuditEvent,
    ChallengerResult,
    FeatureFlag,
    FlagEvent,
    LLMSpend,
    ReviewItem,
    utc_now,
)

MIGRATIONS = Path(__file__).resolve().parent / "migrations"


def alembic_config(url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    config.set_main_option("sqlalchemy.url", url)
    return config


class Database:
    def __init__(self, url: str) -> None:
        self.url = url
        self.engine = create_engine(url)
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    def upgrade(self) -> None:
        """Apply every pending migration (``alembic upgrade head``)."""
        config = alembic_config(self.url)
        with self.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")

    def ping(self) -> bool:
        try:
            with self.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001 -- readiness only reports it
            return False
        return True

    def record(self, event: AuditEvent) -> None:
        with self._sessions.begin() as session:
            session.add(event)

    def audit_events(self, limit: int = 100) -> list[AuditEvent]:
        with self._sessions() as session:
            query = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit)
            return list(session.scalars(query))

    def open_review(self, item: ReviewItem) -> int:
        with self._sessions.begin() as session:
            session.add(item)
            session.flush()
            return item.id

    def reviews(self, status: str | None = "open", limit: int = 100) -> list[ReviewItem]:
        with self._sessions() as session:
            query = select(ReviewItem).order_by(ReviewItem.id).limit(limit)
            if status is not None:
                query = query.where(ReviewItem.status == status)
            return list(session.scalars(query))

    def review(self, review_id: int) -> ReviewItem | None:
        with self._sessions() as session:
            return session.get(ReviewItem, review_id)

    def resolve_review(
        self, review_id: int, resolution: dict[str, Any], resolver: str
    ) -> ReviewItem | None:
        """Close an open item; None when it does not exist or is already resolved."""
        with self._sessions.begin() as session:
            item = session.get(ReviewItem, review_id)
            if item is None or item.status != "open":
                return None
            item.status, item.resolution, item.resolver = "resolved", resolution, resolver
            item.resolved_at = utc_now()
            return item

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        """A session committed on success (for modules that keep their own queries)."""
        with self._sessions.begin() as session:
            yield session

    @contextmanager
    def reader(self) -> Iterator[Session]:
        with self._sessions() as session:
            yield session

    def review_stats(self) -> tuple[int, datetime | None]:
        """Open review items and when the oldest of them was opened."""
        with self._sessions() as session:
            query = select(func.count(ReviewItem.id), func.min(ReviewItem.created_at))
            count, oldest = session.execute(query.where(ReviewItem.status == "open")).one()
            return int(count), oldest

    def flags(self) -> dict[str, Any]:
        with self._sessions() as session:
            return {flag.name: flag.value for flag in session.scalars(select(FeatureFlag))}

    def set_flag(self, name: str, value: Any, actor: str = "system") -> None:
        """Set a flag (``None`` removes it) and record who changed it from what."""
        with self._sessions.begin() as session:
            flag = session.get(FeatureFlag, name)
            old = None if flag is None else flag.value
            if value is None:
                if flag is not None:
                    session.delete(flag)
            elif flag is None:
                session.add(FeatureFlag(name=name, value=value, updated_at=utc_now()))
            else:
                flag.value = value
                flag.updated_at = utc_now()
            session.add(FlagEvent(name=name, old=old, new=value, actor=actor))

    def flag_events(self, limit: int = 100) -> list[FlagEvent]:
        with self._sessions() as session:
            query = select(FlagEvent).order_by(FlagEvent.id.desc()).limit(limit)
            return list(session.scalars(query))

    def llm_spent(self, day: str) -> float:
        with self._sessions() as session:
            row = session.get(LLMSpend, day)
            return 0.0 if row is None else float(row.amount)

    def add_llm_spend(self, day: str, amount: float) -> None:
        """Add to a day's spend atomically, so several gateway processes share one budget."""
        with self._sessions.begin() as session:
            changed = session.execute(
                update(LLMSpend)
                .where(LLMSpend.day == day)
                .values(amount=LLMSpend.amount + amount, updated_at=utc_now())
            )
            if changed.rowcount == 0:  # type: ignore[attr-defined]
                session.add(LLMSpend(day=day, amount=amount, updated_at=utc_now()))

    def record_challenger(self, result: ChallengerResult) -> None:
        with self._sessions.begin() as session:
            session.add(result)

    def challenger_results(
        self, name: str, version: str, spec_id: str | None = None
    ) -> list[ChallengerResult]:
        with self._sessions() as session:
            query = select(ChallengerResult).where(
                ChallengerResult.name == name, ChallengerResult.version == version
            )
            if spec_id is not None:
                query = query.where(ChallengerResult.spec_id == spec_id)
            return list(session.scalars(query.order_by(ChallengerResult.id)))

    def forget(self, input_hmacs: Sequence[str]) -> dict[str, int]:
        """Erase every trace of the decisions on these inputs (a data subject's deletion
        request: the source system computes the HMAC of that person's inputs)."""
        with self._sessions.begin() as session:
            trace_ids = list(
                session.scalars(
                    select(AuditEvent.trace_id).where(AuditEvent.input_hmac.in_(input_hmacs))
                )
            )
            counts = {
                "review_items": session.execute(
                    delete(ReviewItem).where(ReviewItem.trace_id.in_(trace_ids))
                ).rowcount,  # type: ignore[attr-defined]
                "challenger_results": session.execute(
                    delete(ChallengerResult).where(ChallengerResult.trace_id.in_(trace_ids))
                ).rowcount,  # type: ignore[attr-defined]
                "audit_events": session.execute(
                    delete(AuditEvent).where(AuditEvent.trace_id.in_(trace_ids))
                ).rowcount,  # type: ignore[attr-defined]
            }
        return {table: int(count) for table, count in counts.items()}

    def purge(self, before: datetime) -> dict[str, int]:
        """Delete what the retention policy expires: audit events, challenger results and
        *resolved* review items older than ``before``. Open reviews, flag events and the
        specialist lifecycle are kept (they are the operations' own audit trail)."""
        with self._sessions.begin() as session:
            counts = {
                "audit_events": session.execute(
                    delete(AuditEvent).where(AuditEvent.created_at < before)
                ).rowcount,  # type: ignore[attr-defined]
                "challenger_results": session.execute(
                    delete(ChallengerResult).where(ChallengerResult.created_at < before)
                ).rowcount,  # type: ignore[attr-defined]
                "review_items": session.execute(
                    delete(ReviewItem).where(
                        ReviewItem.status == "resolved", ReviewItem.resolved_at < before
                    )
                ).rowcount,  # type: ignore[attr-defined]
            }
        return {table: int(count) for table, count in counts.items()}

    def dispose(self) -> None:
        self.engine.dispose()
