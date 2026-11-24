"""Add narrow chat_message_ux_summary table and backfill assistant UX metrics.

Revision ID: 20261124_000000
Revises: 20261123_000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from services.usage.chat_message_ux_summary_backfill import (
    DEFAULT_BACKFILL_BATCH_SIZE,
    backfill_chat_message_ux_summaries,
)

revision = "20261124_000000"
down_revision = "20261123_000000"
branch_labels = None
depends_on = None

TABLE_NAME = "chat_message_ux_summary"
_INDEXES: tuple[tuple[str, list[str]], ...] = (
    ("ix_chat_message_ux_summary_created_at", ["created_at"]),
    ("ix_chat_message_ux_summary_session_id", ["session_id"]),
)


def _has_index(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    return any(idx.get("name") == index_name for idx in inspector.get_indexes(table_name))


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table(TABLE_NAME):
        op.create_table(
            TABLE_NAME,
            sa.Column(
                "message_id",
                sa.String(),
                sa.ForeignKey("chat_message.id", ondelete="CASCADE"),
                primary_key=True,
                nullable=False,
            ),
            sa.Column("session_id", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("round_category", sa.String(length=24), nullable=False),
            sa.Column("abnormal_reason", sa.String(length=128), nullable=True),
            sa.Column("feedback_vote", sa.String(length=8), nullable=True),
            sa.Column("feedback_preset", sa.String(length=128), nullable=True),
            sa.Column("ai_edit_cards", sa.Integer(), server_default="0", nullable=False),
            sa.Column("ai_edit_undone", sa.Integer(), server_default="0", nullable=False),
            sa.Column("ai_delete_cards", sa.Integer(), server_default="0", nullable=False),
            sa.Column("ai_delete_restored", sa.Integer(), server_default="0", nullable=False),
        )

    inspector = sa.inspect(bind)
    for index_name, columns in _INDEXES:
        if not _has_index(inspector, TABLE_NAME, index_name):
            op.create_index(index_name, TABLE_NAME, columns)

    if inspector.has_table("chat_message"):
        if bind.dialect.name == "postgresql":
            # Commit DDL and run server-side backfill in short independent batches
            # so live chat_message traffic is never blocked behind a long transaction.
            with op.get_context().autocommit_block():
                backfill_chat_message_ux_summaries(
                    bind,
                    batch_size=DEFAULT_BACKFILL_BATCH_SIZE,
                    commit_each_batch=False,
                )
        else:
            backfill_chat_message_ux_summaries(
                bind,
                batch_size=DEFAULT_BACKFILL_BATCH_SIZE,
                commit_each_batch=False,
            )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table(TABLE_NAME):
        for index_name, _columns in reversed(_INDEXES):
            if _has_index(inspector, TABLE_NAME, index_name):
                op.drop_index(index_name, table_name=TABLE_NAME)
        op.drop_table(TABLE_NAME)
