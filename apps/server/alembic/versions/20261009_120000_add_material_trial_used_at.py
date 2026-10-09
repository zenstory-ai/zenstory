"""add usage_quota.material_trial_used_at

Revision ID: 20261009_120000
Revises: 20261007_170000

Additive only: a nullable column, so code from the previous revision keeps
working against the upgraded table.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261009_120000"
down_revision: str | None = "20261007_170000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "usage_quota",
        sa.Column("material_trial_used_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("usage_quota") as batch_op:
        batch_op.drop_column("material_trial_used_at")
