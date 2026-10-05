"""backfill advertised pro plan entitlements

Revision ID: 20261005_180100
Revises: 20261005_180000

The production ``pro`` row was seeded before ``custom_skills`` and
``inspiration_copies_monthly`` existed, so paid users fell back to free-tier
limits (3 skills, 10 copies) while the pricing catalog promised 20 and 100.
Only missing keys are added; values an operator already set are kept, which
also makes re-running the upgrade a no-op.

Values are frozen here on purpose instead of importing application defaults:
a migration must keep meaning the same thing after the code moves on.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261005_180100"
down_revision: str | None = "20261005_180000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PRO_MISSING_KEY_DEFAULTS = {
    "custom_skills": 20,
    "inspiration_copies_monthly": 100,
}

subscription_plan = sa.table(
    "subscription_plan",
    sa.column("id", sa.String()),
    sa.column("name", sa.String()),
    sa.column("features", sa.JSON()),
    sa.column("updated_at", sa.DateTime()),
)


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(subscription_plan.c.id, subscription_plan.c.features).where(
            subscription_plan.c.name == "pro"
        )
    ).all()
    for plan_id, features in rows:
        current = dict(features or {})
        missing = {
            key: value
            for key, value in PRO_MISSING_KEY_DEFAULTS.items()
            if current.get(key) is None
        }
        if not missing:
            continue
        bind.execute(
            subscription_plan.update()
            .where(subscription_plan.c.id == plan_id)
            .values(features={**current, **missing}, updated_at=sa.func.current_timestamp())
        )


def downgrade() -> None:
    # Intentionally a no-op. Removing the keys would put paying users back on
    # free-tier limits and could delete values an operator set after upgrading;
    # the previous revision's code reads these keys correctly when present.
    pass
