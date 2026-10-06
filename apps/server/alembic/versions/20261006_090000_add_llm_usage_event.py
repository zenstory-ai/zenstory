"""add llm usage event ledger

Revision ID: 20261006_090000
Revises: 20261005_180100

One row per DeepSeek chat-model call (agent, router, suggest, polish and
material flows), used by the admin "Usage & cost" page.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261006_090000"
down_revision: str | None = "20261005_180100"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_usage_event",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("cache_hit_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cache_miss_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("price_band", sa.String(length=8), nullable=False),
        sa.Column("pricing_version", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("correlation_id", sa.String(length=128), nullable=True),
        sa.Column("is_backfilled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_llm_usage_event_user_id", "llm_usage_event", ["user_id"])
    op.create_index("ix_llm_usage_event_occurred_at", "llm_usage_event", ["occurred_at"])
    op.create_index(
        "ix_llm_usage_event_user_id_occurred_at",
        "llm_usage_event",
        ["user_id", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_table("llm_usage_event")
