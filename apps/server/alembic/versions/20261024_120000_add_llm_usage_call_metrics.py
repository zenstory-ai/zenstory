"""Add per-attempt model metrics to llm_usage_event (outcome, duration, time to first token).

Additive and nullable: existing rows keep NULL, which reads as "not recorded".
Staging applies the same columns by hand (ADD COLUMN IF NOT EXISTS), so the
upgrade skips columns that already exist.

Revision ID: 20261024_120000
Revises: 20261021_100000
"""

import sqlalchemy as sa

from alembic import op

revision = "20261024_120000"
down_revision = "20261021_100000"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("outcome", sa.String(length=16)),
    ("duration_ms", sa.Integer()),
    ("ttft_ms", sa.Integer()),
)


def upgrade():
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("llm_usage_event")}
    for name, column_type in _COLUMNS:
        if name not in existing:
            op.add_column("llm_usage_event", sa.Column(name, column_type, nullable=True))


def downgrade():
    with op.batch_alter_table("llm_usage_event") as batch:
        for name, _column_type in reversed(_COLUMNS):
            batch.drop_column(name)
