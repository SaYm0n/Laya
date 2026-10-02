"""Audit trail and feature flags.

Revision ID: 0001
Revises:
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("trace_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("spec_id", sa.String(length=128), nullable=False),
        sa.Column("spec_version", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=True),
        sa.Column("input_hmac", sa.String(length=64), nullable=False),
        sa.Column("answers", sa.JSON(), nullable=False),
        sa.Column("incumbent", sa.JSON(), nullable=True),
        sa.Column("agreement", sa.JSON(), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("data_classification", sa.String(length=16), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_events_trace_id", "audit_events", ["trace_id"], unique=True)
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])
    op.create_index("ix_audit_events_spec_id", "audit_events", ["spec_id"])
    op.create_index("ix_audit_events_input_hmac", "audit_events", ["input_hmac"])
    op.create_table(
        "feature_flags",
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("name"),
    )


def downgrade() -> None:
    op.drop_table("feature_flags")
    for index in ("input_hmac", "spec_id", "created_at", "trace_id"):
        op.drop_index(f"ix_audit_events_{index}", table_name="audit_events")
    op.drop_table("audit_events")
