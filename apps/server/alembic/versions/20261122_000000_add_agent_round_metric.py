"""Add agent_round_metric fact table and created_at indexes on chat_message/file_version.

Revision ID: 20261122_000000
Revises: 20261120_000000
"""

import sqlalchemy as sa

from alembic import op

revision = "20261122_000000"
down_revision = "20261120_000000"
branch_labels = None
depends_on = None


def _has_index(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    return any(idx.get("name") == index_name for idx in inspector.get_indexes(table_name))


def _has_single_column_index(inspector: sa.Inspector, table_name: str, column_name: str) -> bool:
    return any(idx.get("column_names") == [column_name] for idx in inspector.get_indexes(table_name))


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("agent_round_metric"):
        op.create_table(
            "agent_round_metric",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("occurred_at", sa.DateTime(), nullable=False),
            sa.Column("user_id", sa.String(), sa.ForeignKey("user.id"), nullable=False),
            sa.Column("project_id", sa.String(length=64), nullable=True),
            sa.Column("session_id", sa.String(length=64), nullable=True),
            sa.Column("agent_run_id", sa.String(length=64), nullable=False),
            sa.Column("is_mobile", sa.Boolean(), nullable=True),
            sa.Column("ui_action", sa.String(length=32), nullable=True),
            sa.Column("primary_model", sa.String(length=64), nullable=True),
            sa.Column("prepare_context_ms", sa.Integer(), nullable=True),
            sa.Column("first_output_ms", sa.Integer(), nullable=True),
            sa.Column("first_tool_started_ms", sa.Integer(), nullable=True),
            sa.Column("chapter_first_prose_ms", sa.Integer(), nullable=True),
            sa.Column("first_body_persisted_ms", sa.Integer(), nullable=True),
            sa.Column("total_duration_ms", sa.Integer(), nullable=True),
            sa.Column("stop_reason", sa.String(length=64), nullable=True),
            sa.Column("billing_reason", sa.String(length=64), nullable=True),
            sa.Column("user_stopped", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("client_disconnected", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("deadline_exceeded", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("write_succeeded", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("runaway_stop", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("file_body_pending_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("leaked_tool_call_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("tool_calls_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("tool_failures_count", sa.Integer(), nullable=False, server_default="0"),
        )
        op.create_index("ix_agent_round_metric_occurred_at", "agent_round_metric", ["occurred_at"])
        op.create_index("ix_agent_round_metric_user_id", "agent_round_metric", ["user_id"])
        op.create_index(
            "ix_agent_round_metric_user_id_occurred_at",
            "agent_round_metric",
            ["user_id", "occurred_at"],
        )
        op.create_index(
            "ix_agent_round_metric_agent_run_id",
            "agent_round_metric",
            ["agent_run_id"],
            unique=True,
        )

    if inspector.has_table("chat_message") and not _has_single_column_index(inspector, "chat_message", "created_at"):
        op.create_index("ix_chat_message_created_at", "chat_message", ["created_at"])

    if inspector.has_table("file_version") and not _has_single_column_index(inspector, "file_version", "created_at"):
        op.create_index("ix_file_version_created_at", "file_version", ["created_at"])


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("file_version") and _has_index(inspector, "file_version", "ix_file_version_created_at"):
        op.drop_index("ix_file_version_created_at", table_name="file_version")

    if inspector.has_table("chat_message") and _has_index(inspector, "chat_message", "ix_chat_message_created_at"):
        op.drop_index("ix_chat_message_created_at", table_name="chat_message")

    if inspector.has_table("agent_round_metric"):
        op.drop_table("agent_round_metric")
