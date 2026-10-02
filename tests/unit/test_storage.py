"""The audit store: migrations match the models, flags and audit rows round-trip."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from laya_platform.storage import (
    AuditEvent,
    Base,
    ChallengerResult,
    Database,
    ReviewItem,
    alembic_config,
)
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
        "0001_audit_and_flags.py",
        "0002_policy_and_reviews.py",
        "0003_specialists_and_operations.py",
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


def test_an_existing_store_upgrades_with_its_rows(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'old.db'}"
    database = Database(url)
    with database.engine.begin() as connection:
        config = alembic_config(url)
        config.attributes["connection"] = connection
        command.upgrade(config, "0001")
        connection.execute(
            text(
                "INSERT INTO audit_events (trace_id, created_at, spec_id, spec_version, mode, "
                "input_hmac, answers, latency_ms, data_classification) VALUES ('old', "
                "'2026-10-01 00:00:00', 's', 1, 'shadow', :h, '{}', 1.0, 'synthetic')"
            ),
            {"h": "0" * 64},
        )
    database.upgrade()
    (event,) = database.audit_events()
    assert (event.trace_id, event.act, event.outcome, event.system2) == ("old", False, None, None)
    database.dispose()


def test_the_review_queue_round_trip(database: Database) -> None:
    item = ReviewItem(
        trace_id="t-1", spec_id="s", spec_version=1, reason="band:q=review", status="open"
    )
    review_id = database.open_review(item)
    assert [r.id for r in database.reviews()] == [review_id]
    resolved = database.resolve_review(review_id, {"values": {"q": True}}, "ops")
    assert resolved is not None
    assert (resolved.status, resolved.resolver) == ("resolved", "ops")
    assert resolved.resolved_at is not None
    assert database.resolve_review(review_id, {"values": {}}, "ops") is None
    assert database.resolve_review(999, {"values": {}}, "ops") is None
    assert database.reviews() == []
    assert [r.status for r in database.reviews(status=None)] == ["resolved"]


def test_flag_changes_record_who_and_what(database: Database) -> None:
    database.set_flag("kill_switch", True, actor="ops")
    database.set_flag("kill_switch", None, actor="oncall")
    events = database.flag_events()
    assert [(e.name, e.old, e.new, e.actor) for e in events] == [
        ("kill_switch", True, None, "oncall"),
        ("kill_switch", None, True, "ops"),
    ]


def test_llm_spend_is_shared_through_the_database(database: Database, tmp_path: Path) -> None:
    database.add_llm_spend("2026-10-02", 0.25)
    other = Database(database.url)  # another gateway process on the same store
    other.add_llm_spend("2026-10-02", 0.5)
    assert database.llm_spent("2026-10-02") == pytest.approx(0.75)
    assert database.llm_spent("2026-10-03") == 0.0
    other.dispose()


def test_review_stats_report_open_items_and_the_oldest(database: Database) -> None:
    assert database.review_stats() == (0, None)
    first = database.open_review(
        ReviewItem(trace_id="a", spec_id="s", spec_version=1, reason="r", status="open")
    )
    database.open_review(
        ReviewItem(trace_id="b", spec_id="s", spec_version=1, reason="r", status="open")
    )
    count, oldest = database.review_stats()
    assert count == 2
    assert oldest is not None
    database.resolve_review(first, {"values": {}}, "ops")
    assert database.review_stats()[0] == 1


def test_purge_expires_old_rows_but_keeps_open_reviews(database: Database) -> None:
    old = datetime(2025, 1, 1, tzinfo=UTC)
    database.record(_event("old", created_at=old))
    database.record(_event("new"))
    database.record_challenger(
        ChallengerResult(trace_id="old", spec_id="s", name="n", version="1", created_at=old)
    )
    open_id = database.open_review(
        ReviewItem(
            trace_id="old", spec_id="s", spec_version=1, reason="r", status="open", created_at=old
        )
    )
    done_id = database.open_review(
        ReviewItem(
            trace_id="old", spec_id="s", spec_version=1, reason="r", status="open", created_at=old
        )
    )
    database.resolve_review(done_id, {"values": {}}, "ops")
    with database.transaction() as session:
        session.get(ReviewItem, done_id).resolved_at = old  # type: ignore[union-attr]
    deleted = database.purge(datetime(2026, 1, 1, tzinfo=UTC))
    assert deleted == {"audit_events": 1, "challenger_results": 1, "review_items": 1}
    assert [e.trace_id for e in database.audit_events()] == ["new"]
    assert [r.id for r in database.reviews()] == [open_id]


def test_forget_erases_every_trace_of_an_input(database: Database) -> None:
    database.record(_event("t1", input_hmac="a" * 64))
    database.record(_event("t2", input_hmac="b" * 64))
    database.open_review(
        ReviewItem(trace_id="t1", spec_id="s", spec_version=1, reason="r", status="open")
    )
    database.record_challenger(ChallengerResult(trace_id="t1", spec_id="s", name="n", version="1"))
    assert database.forget(["a" * 64]) == {
        "review_items": 1,
        "challenger_results": 1,
        "audit_events": 1,
    }
    assert [e.trace_id for e in database.audit_events()] == ["t2"]
    assert database.forget(["c" * 64]) == {
        "review_items": 0,
        "challenger_results": 0,
        "audit_events": 0,
    }
