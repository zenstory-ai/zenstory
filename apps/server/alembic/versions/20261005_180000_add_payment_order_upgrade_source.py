"""add payment order upgrade source

Revision ID: 20261005_180000
Revises: 20261005_120000

Additive only: a nullable column, so code from the previous revision keeps
working against the upgraded table.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261005_180000"
down_revision: str | None = "20261005_120000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "payment_order",
        sa.Column("upgrade_source", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("payment_order") as batch_op:
        batch_op.drop_column("upgrade_source")
