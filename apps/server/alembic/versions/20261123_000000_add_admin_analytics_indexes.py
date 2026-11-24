"""Add indexes for admin dashboard, usage analytics, and list pagination.

Revision ID: 20261123_000000
Revises: 20261122_000000
"""

import sqlalchemy as sa

from alembic import op

revision = "20261123_000000"
down_revision = "20261122_000000"
branch_labels = None
depends_on = None


_STANDARD_INDEXES: tuple[tuple[str, str, list[str]], ...] = (
    ("chat_message", "ix_chat_message_role_created_at", ["role", "created_at"]),
    ("user", "ix_user_created_at_id", ["created_at", "id"]),
    ("payment_order", "ix_payment_order_status_paid_at", ["status", "paid_at"]),
    ("subscription_history", "ix_subscription_history_created_at", ["created_at"]),
    ("file", "ix_file_type_created_at", ["file_type", "created_at"]),
    ("admin_audit_log", "ix_admin_audit_log_created_at_id", ["created_at", "id"]),
    ("points_transaction", "ix_points_transaction_user_id_created_at", ["user_id", "created_at"]),
    (
        "upgrade_funnel_event",
        "ix_upgrade_funnel_event_user_source_created",
        ["user_id", "source", "created_at"],
    ),
    ("redemption_code", "ix_redemption_code_created_at_id", ["created_at", "id"]),
    ("referral", "ix_referral_created_at", ["created_at"]),
)

_POSTGRES_PARTIAL_INDEXES: tuple[tuple[str, str, str], ...] = (
    (
        "chat_message",
        "ix_chat_message_assistant_feedback_created_at",
        (
            "CREATE INDEX IF NOT EXISTS ix_chat_message_assistant_feedback_created_at "
            "ON chat_message (created_at) "
            "WHERE role = 'assistant' AND message_metadata IS NOT NULL "
            "AND strpos(message_metadata, '\"feedback\"') > 0"
        ),
    ),
    (
        "chat_message",
        "ix_chat_message_assistant_tool_calls_created_at",
        (
            "CREATE INDEX IF NOT EXISTS ix_chat_message_assistant_tool_calls_created_at "
            "ON chat_message (created_at) "
            "WHERE role = 'assistant' AND tool_calls IS NOT NULL"
        ),
    ),
)


def _has_index(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    return any(idx.get("name") == index_name for idx in inspector.get_indexes(table_name))


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for table_name, index_name, columns in _STANDARD_INDEXES:
        if inspector.has_table(table_name) and not _has_index(inspector, table_name, index_name):
            op.create_index(index_name, table_name, columns)

    if bind.dialect.name == "postgresql":
        for table_name, index_name, ddl in _POSTGRES_PARTIAL_INDEXES:
            if inspector.has_table(table_name) and not _has_index(inspector, table_name, index_name):
                op.execute(sa.text(ddl))


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if bind.dialect.name == "postgresql":
        for table_name, index_name, _ddl in reversed(_POSTGRES_PARTIAL_INDEXES):
            if inspector.has_table(table_name) and _has_index(inspector, table_name, index_name):
                op.drop_index(index_name, table_name=table_name)

    for table_name, index_name, _columns in reversed(_STANDARD_INDEXES):
        if inspector.has_table(table_name) and _has_index(inspector, table_name, index_name):
            op.drop_index(index_name, table_name=table_name)
