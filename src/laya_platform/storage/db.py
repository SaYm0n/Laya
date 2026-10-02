"""Database access: Alembic upgrade on start, audit writes, feature flags."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from laya_platform.storage.models import AuditEvent, FeatureFlag, utc_now

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

    def flags(self) -> dict[str, Any]:
        with self._sessions() as session:
            return {flag.name: flag.value for flag in session.scalars(select(FeatureFlag))}

    def set_flag(self, name: str, value: Any) -> None:
        """Set a flag; ``None`` removes it."""
        with self._sessions.begin() as session:
            flag = session.get(FeatureFlag, name)
            if value is None:
                if flag is not None:
                    session.delete(flag)
            elif flag is None:
                session.add(FeatureFlag(name=name, value=value, updated_at=utc_now()))
            else:
                flag.value = value
                flag.updated_at = utc_now()

    def dispose(self) -> None:
        self.engine.dispose()
