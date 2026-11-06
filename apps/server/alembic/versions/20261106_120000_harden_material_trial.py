"""Free material trial: lifetime cost total, reservation ledgers, one seat per network per day.

Revision ID: 20261106_120000
Revises: 20261105_120000
"""

import sqlalchemy as sa

from alembic import op

revision = "20261106_120000"
down_revision = "20261105_120000"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "usage_quota",
        sa.Column("material_trial_charged_units", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "ai_cost_reservation",
        sa.Column("charges_daily", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column(
        "ai_cost_reservation",
        sa.Column("charges_trial", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_table(
        "material_trial_network_seat",
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("ip_key", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade():
    op.drop_table("material_trial_network_seat")
    op.drop_column("ai_cost_reservation", "charges_trial")
    op.drop_column("ai_cost_reservation", "charges_daily")
    op.drop_column("usage_quota", "material_trial_charged_units")
