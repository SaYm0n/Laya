"""Specialist registry, challengers, flag events, shared LLM spend; engine on audit events.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("audit_events") as batch:
        batch.add_column(sa.Column("engine", sa.String(length=160), nullable=True))
    op.create_table(
        "flag_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("old", sa.JSON(), nullable=True),
        sa.Column("new", sa.JSON(), nullable=True),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_flag_events_at", "flag_events", ["at"])
    op.create_index("ix_flag_events_name", "flag_events", ["name"])
    op.create_table(
        "llm_spend",
        sa.Column("day", sa.String(length=10), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("day"),
    )
    op.create_table(
        "specialists",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "version", name="uq_specialists_name_version"),
    )
    op.create_index("ix_specialists_name", "specialists", ["name"])
    op.create_index("ix_specialists_status", "specialists", ["status"])
    op.create_table(
        "specialist_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("from_status", sa.String(length=16), nullable=True),
        sa.Column("to_status", sa.String(length=16), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_specialist_events_at", "specialist_events", ["at"])
    op.create_index("ix_specialist_events_name", "specialist_events", ["name"])
    op.create_table(
        "specialist_pointers",
        sa.Column("spec_id", sa.String(length=128), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("previous_name", sa.String(length=128), nullable=True),
        sa.Column("previous_version", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.PrimaryKeyConstraint("spec_id", "role"),
    )
    op.create_table(
        "challenger_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trace_id", sa.String(length=36), nullable=False),
        sa.Column("spec_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("values", sa.JSON(), nullable=True),
        sa.Column("agreement", sa.JSON(), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_challenger_results_trace_id", "challenger_results", ["trace_id"])
    op.create_index("ix_challenger_results_spec_id", "challenger_results", ["spec_id"])
    op.create_index("ix_challenger_results_name", "challenger_results", ["name"])


def downgrade() -> None:
    for index in ("name", "spec_id", "trace_id"):
        op.drop_index(f"ix_challenger_results_{index}", table_name="challenger_results")
    op.drop_table("challenger_results")
    op.drop_table("specialist_pointers")
    for index in ("name", "at"):
        op.drop_index(f"ix_specialist_events_{index}", table_name="specialist_events")
    op.drop_table("specialist_events")
    for index in ("status", "name"):
        op.drop_index(f"ix_specialists_{index}", table_name="specialists")
    op.drop_table("specialists")
    op.drop_table("llm_spend")
    for index in ("name", "at"):
        op.drop_index(f"ix_flag_events_{index}", table_name="flag_events")
    op.drop_table("flag_events")
    with op.batch_alter_table("audit_events") as batch:
        batch.drop_column("engine")
