"""The audit store: migrations match the models, flags and audit rows round-trip."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from laya_platform.storage import AuditEvent, Base, Database
from laya_platform.storage.db import MIGRATIONS


@pytest.fixture
def database(tmp_path: Path) -> Iterator[Database]:
    database = Database(f"sqlite:///{tmp_path / 'audit.db'}")
    database.upgrade()
    yield database
    database.dispose()


def _event(trace_id: str, **overrides: object) -> AuditEvent:
    values: dict[str, object] = {
        "trace_id": trace_id,
        "spec_id": "examples.triage",
        "spec_version": 1,
        "mode": "shadow",
        "input_hmac": "0" * 64,
        "answers": {"q": {"value": True}},
        "latency_ms": 1.0,
        "data_classification": "synthetic",
    }
    return AuditEvent(**(values | overrides))


def test_the_migrations_build_exactly_the_models(database: Database) -> None:
    with database.engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []


def test_upgrade_is_idempotent(database: Database) -> None:
    database.upgrade()
    tables = set(inspect(database.engine).get_table_names())
    assert {"audit_events", "feature_flags", "alembic_version"} <= tables


def test_the_migration_scripts_ship_with_the_package() -> None:
    assert (MIGRATIONS / "env.py").is_file()
    assert (MIGRATIONS / "script.py.mako").is_file()
    assert sorted(p.name for p in (MIGRATIONS / "versions").glob("*.py")) == [
        "0001_audit_and_flags.py"
    ]


def test_flags_are_set_replaced_and_removed(database: Database) -> None:
    assert database.flags() == {}
    database.set_flag("kill_switch", True)
    database.set_flag("mode:examples.triage", "advisory")
    database.set_flag("mode:examples.triage", "shadow")
    assert database.flags() == {"kill_switch": True, "mode:examples.triage": "shadow"}
    database.set_flag("kill_switch", None)
    database.set_flag("never_set", None)
    assert database.flags() == {"mode:examples.triage": "shadow"}


def test_audit_events_come_back_newest_first(database: Database) -> None:
    for index in range(3):
        database.record(_event(f"trace-{index}", latency_ms=float(index)))
    events = database.audit_events(limit=2)
    assert [e.trace_id for e in events] == ["trace-2", "trace-1"]
    assert events[0].answers == {"q": {"value": True}}
    assert events[0].created_at is not None


def test_trace_ids_are_unique(database: Database) -> None:
    database.record(_event("same"))
    with pytest.raises(IntegrityError, match="UNIQUE"):
        database.record(_event("same"))


def test_ping_reports_an_unreachable_database(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'missing' / 'audit.db'}")
    assert database.ping() is False
    database.dispose()
