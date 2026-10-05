"""add payment orders

Revision ID: 20261005_120000
Revises: c3d5e7f9a1b2
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261005_120000"
down_revision: str | None = "c3d5e7f9a1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "payment_order",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("out_trade_no", sa.String(length=32), nullable=False),
        sa.Column("trade_no", sa.String(length=64), nullable=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("plan_name", sa.String(length=32), nullable=False),
        sa.Column("plan_display_name", sa.String(), nullable=False),
        sa.Column("product_name", sa.String(length=100), nullable=False),
        sa.Column("cycle", sa.String(length=16), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("duration_days", sa.Integer(), nullable=False),
        sa.Column("payment_method", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("fulfillment_status", sa.String(length=16), nullable=False),
        sa.Column("failure_reason", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("paid_at", sa.DateTime(), nullable=True),
        sa.Column("fulfilled_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_payment_order_created_at", "payment_order", ["created_at"])
    op.create_index("ix_payment_order_fulfillment_status", "payment_order", ["fulfillment_status"])
    op.create_index("ix_payment_order_out_trade_no", "payment_order", ["out_trade_no"], unique=True)
    op.create_index("ix_payment_order_status", "payment_order", ["status"])
    op.create_index("ix_payment_order_status_created_at", "payment_order", ["status", "created_at"])
    op.create_index("ix_payment_order_trade_no", "payment_order", ["trade_no"], unique=True)
    op.create_index("ix_payment_order_user_id", "payment_order", ["user_id"])


def downgrade() -> None:
    op.drop_table("payment_order")
