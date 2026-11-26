"""Add chat_message_archive cold table and drop chat_message_ux_summary CASCADE FK.

Revision ID: 20261126_000000
Revises: 20261124_180000
Create Date: 2026-11-26 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261126_000000"
down_revision: str | Sequence[str] | None = "20261124_180000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ARCHIVE_TABLE = "chat_message_archive"
SUMMARY_TABLE = "chat_message_ux_summary"
_ARCHIVE_INDEXES: tuple[tuple[str, list[str]], ...] = (
    ("ix_chat_message_archive_session_created_at_id", ["session_id", "created_at", "id"]),
    ("ix_chat_message_archive_archived_at", ["archived_at"]),
)
_ARCHIVE_FEEDBACK_PARTIAL_INDEX_NAME = "ix_chat_message_archive_assistant_feedback_created_at"
_ARCHIVE_FEEDBACK_PARTIAL_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS ix_chat_message_archive_assistant_feedback_created_at "
    "ON chat_message_archive (created_at) "
    "WHERE role = 'assistant' AND message_metadata IS NOT NULL "
    "AND strpos(message_metadata, '\"feedback\"') > 0"
)
_PG_MIGRATION_LOCK_TIMEOUT = "5s"
_FK_NAMING_CONVENTION = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}
_SUMMARY_CHAT_MESSAGE_FK_NAME = "fk_chat_message_ux_summary_message_id_chat_message"


def _has_index(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    return any(idx.get("name") == index_name for idx in inspector.get_indexes(table_name))


def _summary_chat_message_fks(inspector: sa.Inspector) -> list[dict]:
    if not inspector.has_table(SUMMARY_TABLE):
        return []
    return [
        fk
        for fk in inspector.get_foreign_keys(SUMMARY_TABLE)
        if fk.get("referred_table") == "chat_message"
        or "message_id" in (fk.get("constrained_columns") or [])
    ]


def _drop_summary_chat_message_fk(bind: sa.Connection, inspector: sa.Inspector) -> None:
    fks = _summary_chat_message_fks(inspector)
    if not fks:
        return

    if bind.dialect.name == "sqlite":
        # SQLite cannot ALTER TABLE DROP CONSTRAINT directly; rebuild via batch_alter_table.
        # Unnamed inline FKs from 20261124_000000 have name=None, so supply a naming convention
        # so Alembic assigns a deterministic name we can drop cleanly.
        with op.batch_alter_table(
            SUMMARY_TABLE,
            recreate="always",
            naming_convention=_FK_NAMING_CONVENTION,
        ) as batch_op:
            dropped: set[str] = set()
            for fk in fks:
                fk_name = fk.get("name") or _SUMMARY_CHAT_MESSAGE_FK_NAME
                if fk_name not in dropped:
                    batch_op.drop_constraint(fk_name, type_="foreignkey")
                    dropped.add(fk_name)
    else:
        for fk in fks:
            fk_name = fk.get("name")
            if fk_name:
                op.drop_constraint(fk_name, SUMMARY_TABLE, type_="foreignkey")


def _restore_summary_chat_message_fk(bind: sa.Connection, inspector: sa.Inspector) -> None:
    if not inspector.has_table(SUMMARY_TABLE) or not inspector.has_table("chat_message"):
        return
    if _summary_chat_message_fks(inspector):
        return

    # Only restore the FK when every summary row still exists in chat_message;
    # otherwise downgrade would fail on already-archived rows.
    orphan_count = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM chat_message_ux_summary s "
            "WHERE NOT EXISTS (SELECT 1 FROM chat_message m WHERE m.id = s.message_id)"
        )
    ).scalar_one()
    if int(orphan_count or 0) > 0:
        return

    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(SUMMARY_TABLE, recreate="always") as batch_op:
            batch_op.create_foreign_key(
                _SUMMARY_CHAT_MESSAGE_FK_NAME,
                "chat_message",
                ["message_id"],
                ["id"],
                ondelete="CASCADE",
            )
    else:
        op.create_foreign_key(
            _SUMMARY_CHAT_MESSAGE_FK_NAME,
            SUMMARY_TABLE,
            "chat_message",
            ["message_id"],
            ["id"],
            ondelete="CASCADE",
        )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # Bound DDL lock wait so DROP CONSTRAINT on chat_message_ux_summary
        # (which takes ACCESS EXCLUSIVE on parent chat_message) never queues
        # live chat traffic unboundedly.
        op.execute(sa.text(f"SET LOCAL lock_timeout = '{_PG_MIGRATION_LOCK_TIMEOUT}'"))

    inspector = sa.inspect(bind)

    # 1. Decouple chat_message_ux_summary from hot chat_message deletes so
    #    archiving >7d rows never CASCADE-wipes admin UX summary analytics.
    _drop_summary_chat_message_fk(bind, inspector)

    # 2. Create cold archive table for >7d chat messages.
    inspector = sa.inspect(bind)
    if not inspector.has_table(ARCHIVE_TABLE):
        op.create_table(
            ARCHIVE_TABLE,
            sa.Column("id", sa.String(), primary_key=True, nullable=False),
            sa.Column(
                "session_id",
                sa.String(),
                sa.ForeignKey("chat_session.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("role", sa.String(), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("tool_calls", sa.Text(), nullable=True),
            sa.Column("tool_call_id", sa.String(), nullable=True),
            sa.Column("reasoning_content", sa.Text(), nullable=True),
            sa.Column("message_metadata", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("archived_at", sa.DateTime(), nullable=False),
        )

    inspector = sa.inspect(bind)
    for index_name, columns in _ARCHIVE_INDEXES:
        if not _has_index(inspector, ARCHIVE_TABLE, index_name):
            op.create_index(index_name, ARCHIVE_TABLE, columns)

    if bind.dialect.name == "postgresql" and not _has_index(
        inspector, ARCHIVE_TABLE, _ARCHIVE_FEEDBACK_PARTIAL_INDEX_NAME
    ):
        op.execute(sa.text(_ARCHIVE_FEEDBACK_PARTIAL_INDEX_DDL))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(sa.text(f"SET LOCAL lock_timeout = '{_PG_MIGRATION_LOCK_TIMEOUT}'"))

    inspector = sa.inspect(bind)

    if inspector.has_table(ARCHIVE_TABLE):
        archived_rows = bind.execute(
            sa.text(f"SELECT COUNT(*) FROM {ARCHIVE_TABLE}")
        ).scalar_one()
        if int(archived_rows or 0) > 0:
            raise RuntimeError(
                f"Refusing to drop non-empty {ARCHIVE_TABLE} table "
                f"({int(archived_rows)} archived rows); restore or migrate archived rows first"
            )
        if bind.dialect.name == "postgresql" and _has_index(
            inspector, ARCHIVE_TABLE, _ARCHIVE_FEEDBACK_PARTIAL_INDEX_NAME
        ):
            op.drop_index(_ARCHIVE_FEEDBACK_PARTIAL_INDEX_NAME, table_name=ARCHIVE_TABLE)
        for index_name, _columns in reversed(_ARCHIVE_INDEXES):
            if _has_index(inspector, ARCHIVE_TABLE, index_name):
                op.drop_index(index_name, table_name=ARCHIVE_TABLE)
        op.drop_table(ARCHIVE_TABLE)

    inspector = sa.inspect(bind)
    _restore_summary_chat_message_fk(bind, inspector)
