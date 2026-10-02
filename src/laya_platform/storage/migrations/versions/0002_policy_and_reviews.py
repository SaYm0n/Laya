"""Policy verdict on audit events; human review queue.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("audit_events") as batch:
        batch.add_column(sa.Column("outcome", sa.String(length=16), nullable=True))
        batch.add_column(
            sa.Column("act", sa.Boolean(), nullable=False, server_default=sa.text("0"))
        )
        batch.add_column(sa.Column("reasons", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("system2", sa.JSON(), nullable=True))
    op.create_table(
        "review_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("trace_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("spec_id", sa.String(length=128), nullable=False),
        sa.Column("spec_version", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("suggestion", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolver", sa.String(length=128), nullable=True),
        sa.Column("resolution", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_review_items_trace_id", "review_items", ["trace_id"])
    op.create_index("ix_review_items_spec_id", "review_items", ["spec_id"])
    op.create_index("ix_review_items_status", "review_items", ["status"])


def downgrade() -> None:
    for index in ("status", "spec_id", "trace_id"):
        op.drop_index(f"ix_review_items_{index}", table_name="review_items")
    op.drop_table("review_items")
    with op.batch_alter_table("audit_events") as batch:
        for column in ("system2", "reasons", "act", "outcome"):
            batch.drop_column(column)
