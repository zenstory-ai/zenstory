"""Harden payment_order refund/dispute idempotency and partial-refund tracking.

Revision ID: 20261124_180000
Revises: 20261124_120000
Create Date: 2026-11-24 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261124_180000"
down_revision: str | Sequence[str] | None = "20261124_120000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    payment_columns = {col["name"] for col in inspector.get_columns("payment_order")}

    if "paid_amount_cents" not in payment_columns:
        op.add_column("payment_order", sa.Column("paid_amount_cents", sa.Integer(), nullable=True))
    if "refunded_at" not in payment_columns:
        op.add_column("payment_order", sa.Column("refunded_at", sa.DateTime(), nullable=True))
    if "refunded_amount_cents" not in payment_columns:
        op.add_column(
            "payment_order",
            sa.Column("refunded_amount_cents", sa.Integer(), nullable=False, server_default=sa.text("0")),
        )
    if "refund_ids" not in payment_columns:
        op.add_column("payment_order", sa.Column("refund_ids", sa.Text(), nullable=True))
    if "disputed_at" not in payment_columns:
        op.add_column("payment_order", sa.Column("disputed_at", sa.DateTime(), nullable=True))

    op.execute(
        sa.text(
            "UPDATE payment_order "
            "SET paid_amount_cents = amount_cents "
            "WHERE status IN ('paid', 'refunded', 'disputed') AND paid_amount_cents IS NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE payment_order "
            "SET refunded_at = COALESCE(paid_at, created_at), "
            "    refunded_amount_cents = CASE WHEN refunded_amount_cents = 0 THEN COALESCE(paid_amount_cents, amount_cents) ELSE refunded_amount_cents END "
            "WHERE status = 'refunded' AND refunded_at IS NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE payment_order "
            "SET disputed_at = COALESCE(paid_at, created_at) "
            "WHERE status = 'disputed' AND disputed_at IS NULL"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    payment_columns = {col["name"] for col in inspector.get_columns("payment_order")}

    if "disputed_at" in payment_columns:
        op.drop_column("payment_order", "disputed_at")
    if "refund_ids" in payment_columns:
        op.drop_column("payment_order", "refund_ids")
    if "refunded_amount_cents" in payment_columns:
        op.drop_column("payment_order", "refunded_amount_cents")
    if "refunded_at" in payment_columns:
        op.drop_column("payment_order", "refunded_at")
    if "paid_amount_cents" in payment_columns:
        op.drop_column("payment_order", "paid_amount_cents")
