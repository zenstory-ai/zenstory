"""Add per-account interface preferences (interface language).

Revision ID: 20261021_100000
Revises: 20261017_100000
"""

import sqlalchemy as sa

from alembic import op

revision = "20261021_100000"
down_revision = "20261017_100000"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_preference",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("ui_language", sa.String(length=8), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade():
    op.drop_table("user_preference")
