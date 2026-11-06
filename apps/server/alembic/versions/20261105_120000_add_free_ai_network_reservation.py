"""Free AI message reservations shared across accounts on a client network.

Revision ID: 20261105_120000
Revises: 20261024_120000
"""

import sqlalchemy as sa

from alembic import op

revision = "20261105_120000"
down_revision = "20261024_120000"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "free_ai_network_reservation",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("ip_key", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("released_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_free_ai_network_reservation_ip_key_day", "free_ai_network_reservation", ["ip_key", "day"]
    )
    op.create_index("ix_free_ai_network_reservation_day", "free_ai_network_reservation", ["day"])
    op.create_index("ix_free_ai_network_reservation_user_id", "free_ai_network_reservation", ["user_id"])


def downgrade():
    op.drop_table("free_ai_network_reservation")
