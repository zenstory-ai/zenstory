"""Add immutable edit anchors and recoverable deletion batches.

Revision ID: 20261119_040000
Revises: 20261106_120000
"""

import sqlalchemy as sa

from alembic import op

revision = "20261119_040000"
down_revision = "20261106_120000"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "file_edit_operation",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("project_id", sa.String(), sa.ForeignKey("project.id"), nullable=False),
        sa.Column("file_id", sa.String(), sa.ForeignKey("file.id"), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("before_version_id", sa.String(), sa.ForeignKey("file_version.id"), nullable=False),
        sa.Column("after_version_id", sa.String(), sa.ForeignKey("file_version.id"), nullable=False),
        sa.Column("before_title", sa.String(), nullable=False),
        sa.Column("after_title", sa.String(), nullable=False),
        sa.Column("before_order", sa.Integer(), nullable=False),
        sa.Column("after_order", sa.Integer(), nullable=False),
        sa.Column("after_updated_at", sa.DateTime(), nullable=False),
        sa.Column("after_content_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("undone_at", sa.DateTime(), nullable=True),
        sa.Column("undo_version_id", sa.String(), sa.ForeignKey("file_version.id"), nullable=True),
    )
    for column in (
        "user_id", "project_id", "file_id", "kind", "before_version_id",
        "after_version_id", "created_at", "undone_at", "undo_version_id",
    ):
        op.create_index(f"ix_file_edit_operation_{column}", "file_edit_operation", [column])
    op.create_index(
        "ix_file_edit_operation_owner_file_created", "file_edit_operation",
        ["user_id", "project_id", "file_id", "created_at"],
    )
    op.create_table(
        "file_deletion_batch",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("project_id", sa.String(), sa.ForeignKey("project.id"), nullable=False),
        sa.Column("user_id", sa.String(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("root_ids_json", sa.String(), nullable=False),
        sa.Column("items_json", sa.String(), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("target_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("restored_at", sa.DateTime(), nullable=True),
    )
    for column in ("project_id", "user_id", "created_at", "restored_at"):
        op.create_index(f"ix_file_deletion_batch_{column}", "file_deletion_batch", [column])


def downgrade():
    op.drop_table("file_deletion_batch")
    op.drop_table("file_edit_operation")
