"""Add multi-currency payment columns for Creem USD checkout.

Revision ID: 20261124_120000
Revises: 20261124_000000
Create Date: 2026-11-24 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261124_120000"
down_revision: str | Sequence[str] | None = "20261124_000000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    dialect = bind.dialect.name

    payment_columns = {col["name"] for col in inspector.get_columns("payment_order")}
    if "provider" not in payment_columns:
        op.add_column(
            "payment_order",
            sa.Column("provider", sa.String(length=16), nullable=False, server_default=sa.text("'zpay'")),
        )
    if "currency" not in payment_columns:
        op.add_column(
            "payment_order",
            sa.Column("currency", sa.String(length=8), nullable=False, server_default=sa.text("'CNY'")),
        )
    if "provider_checkout_id" not in payment_columns:
        op.add_column(
            "payment_order",
            sa.Column("provider_checkout_id", sa.String(length=64), nullable=True),
        )

    existing_indexes = {idx["name"]: idx for idx in inspector.get_indexes("payment_order")}
    if "ix_payment_order_provider_checkout_id" not in existing_indexes:
        op.create_index(
            "ix_payment_order_provider_checkout_id",
            "payment_order",
            ["provider_checkout_id"],
            unique=False,
        )

    existing_uniques = {uc["name"] for uc in inspector.get_unique_constraints("payment_order")}
    if dialect == "sqlite":
        with op.batch_alter_table("payment_order") as batch_op:
            if "ix_payment_order_trade_no" in existing_indexes and existing_indexes["ix_payment_order_trade_no"].get("unique"):
                batch_op.drop_index("ix_payment_order_trade_no")
                batch_op.create_index("ix_payment_order_trade_no", ["trade_no"], unique=False)
            if "uq_payment_order_provider_trade_no" not in existing_uniques:
                batch_op.create_unique_constraint(
                    "uq_payment_order_provider_trade_no", ["provider", "trade_no"]
                )
    else:
        if "ix_payment_order_trade_no" in existing_indexes and existing_indexes["ix_payment_order_trade_no"].get("unique"):
            op.drop_index("ix_payment_order_trade_no", table_name="payment_order")
            op.create_index("ix_payment_order_trade_no", "payment_order", ["trade_no"], unique=False)
        for uc in inspector.get_unique_constraints("payment_order"):
            if uc.get("column_names") == ["trade_no"] and uc.get("name"):
                op.drop_constraint(uc["name"], "payment_order", type_="unique")
        if "uq_payment_order_provider_trade_no" not in existing_uniques:
            op.create_unique_constraint(
                "uq_payment_order_provider_trade_no",
                "payment_order",
                ["provider", "trade_no"],
            )

    plan_columns = {col["name"] for col in inspector.get_columns("subscription_plan")}
    if "price_monthly_usd_cents" not in plan_columns:
        op.add_column(
            "subscription_plan",
            sa.Column("price_monthly_usd_cents", sa.Integer(), nullable=False, server_default=sa.text("0")),
        )
    if "price_yearly_usd_cents" not in plan_columns:
        op.add_column(
            "subscription_plan",
            sa.Column("price_yearly_usd_cents", sa.Integer(), nullable=False, server_default=sa.text("0")),
        )

    op.execute(
        sa.text(
            "UPDATE subscription_plan "
            "SET price_monthly_usd_cents = 999, price_yearly_usd_cents = 7900 "
            "WHERE name = 'pro' AND price_monthly_usd_cents = 0 AND price_yearly_usd_cents = 0"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    dialect = bind.dialect.name

    plan_columns = {col["name"] for col in inspector.get_columns("subscription_plan")}
    if "price_yearly_usd_cents" in plan_columns:
        op.drop_column("subscription_plan", "price_yearly_usd_cents")
    if "price_monthly_usd_cents" in plan_columns:
        op.drop_column("subscription_plan", "price_monthly_usd_cents")

    existing_uniques = {uc["name"] for uc in inspector.get_unique_constraints("payment_order")}
    existing_indexes = {idx["name"]: idx for idx in inspector.get_indexes("payment_order")}
    if dialect == "sqlite":
        with op.batch_alter_table("payment_order") as batch_op:
            if "uq_payment_order_provider_trade_no" in existing_uniques:
                batch_op.drop_constraint("uq_payment_order_provider_trade_no", type_="unique")
            if "ix_payment_order_trade_no" in existing_indexes:
                batch_op.drop_index("ix_payment_order_trade_no")
            batch_op.create_index("ix_payment_order_trade_no", ["trade_no"], unique=True)
    else:
        if "uq_payment_order_provider_trade_no" in existing_uniques:
            op.drop_constraint("uq_payment_order_provider_trade_no", "payment_order", type_="unique")
        if "ix_payment_order_trade_no" in existing_indexes:
            op.drop_index("ix_payment_order_trade_no", table_name="payment_order")
        op.create_index("ix_payment_order_trade_no", "payment_order", ["trade_no"], unique=True)

    if "ix_payment_order_provider_checkout_id" in existing_indexes:
        op.drop_index("ix_payment_order_provider_checkout_id", table_name="payment_order")
    payment_columns = {col["name"] for col in inspector.get_columns("payment_order")}
    if "provider_checkout_id" in payment_columns:
        op.drop_column("payment_order", "provider_checkout_id")
    if "currency" in payment_columns:
        op.drop_column("payment_order", "currency")
    if "provider" in payment_columns:
        op.drop_column("payment_order", "provider")
