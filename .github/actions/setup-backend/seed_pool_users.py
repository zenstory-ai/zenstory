"""Seed the existing Playwright account pool in the disposable CI database."""

import os
import sys
from pathlib import Path
from urllib.parse import urlparse


def main() -> None:
    database = urlparse(os.environ.get("DATABASE_URL", ""))
    if database.hostname not in {"localhost", "127.0.0.1", "::1"} or database.path != "/zenstory_test":
        raise RuntimeError("E2E pool seeding requires the local disposable zenstory_test database")

    server = Path(__file__).resolve().parents[3] / "apps" / "server"
    sys.path.insert(0, str(server))
    from scripts.seed_test_user import (  # noqa: PLC0415
        Session,
        SubscriptionPlan,
        select,
        sync_engine,
        upsert_quota,
        upsert_subscription,
        upsert_user,
    )

    with Session(sync_engine) as session:
        plan = session.exec(select(SubscriptionPlan).where(SubscriptionPlan.name == "free")).one()
        for index in range(5):
            user = upsert_user(
                session,
                email=f"e2e-pool-{index}@example.com",
                password=f"PoolTest{index}!Aa",
                username=f"e2e-pool-user-{index}",
                is_superuser=False,
            )
            upsert_subscription(session, user_id=user.id, plan_id=plan.id)
            upsert_quota(session, user_id=user.id, skill_creates_used=0)


if __name__ == "__main__":
    main()
