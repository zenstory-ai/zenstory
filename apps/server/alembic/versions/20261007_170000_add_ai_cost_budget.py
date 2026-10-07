"""Durable daily free-user model cost reservations.

Revision ID: 20261007_170000
Revises: 20261006_090000
"""

import sqlalchemy as sa

from alembic import op

revision = "20261007_170000"
down_revision = "20261006_090000"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ai_cost_daily_budget",
        sa.Column("user_id", sa.String(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("charged_units", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "day"),
    )
    op.create_table(
        "ai_cost_reservation",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("reserved_units", sa.BigInteger(), nullable=False),
        sa.Column("settled_units", sa.BigInteger(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_ai_cost_reservation_user_id", "ai_cost_reservation", ["user_id"])


def downgrade():
    op.drop_table("ai_cost_reservation")
    op.drop_table("ai_cost_daily_budget")
