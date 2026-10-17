"""Add the singleton lightweight announcement configuration.

Revision ID: 20261017_100000
Revises: 20261014_100000
"""

import sqlalchemy as sa

from alembic import op

revision = "20261017_100000"
down_revision = "20261014_100000"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "announcement_config",
        sa.Column("id", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=80), nullable=False),
        sa.Column("description", sa.String(length=240), nullable=False),
        sa.Column("action_label", sa.String(length=24), nullable=True),
        sa.Column("action_url", sa.String(length=2048), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.CheckConstraint("id = 'current'", name="ck_announcement_config_singleton"),
        sa.ForeignKeyConstraint(["updated_by"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade():
    op.drop_table("announcement_config")
