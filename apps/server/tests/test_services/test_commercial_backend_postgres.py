from __future__ import annotations

import hashlib
import hmac
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, select

from models.entities import User
from models.subscription import (
    RedemptionCode,
    SubscriptionHistory,
    SubscriptionPlan,
    UsageQuota,
    UserSubscription,
)
from services.quota_service import quota_service
from services.subscription.redemption_service import redemption_service

pytestmark = pytest.mark.skipif(
    not os.getenv("ZENSTORY_TEST_POSTGRES_URL"),
    reason="isolated PostgreSQL URL not configured",
)

SECRET = "commercial-backend-postgres-secret-32-chars"
TABLES = [
    User.__table__,
    SubscriptionPlan.__table__,
    UserSubscription.__table__,
    SubscriptionHistory.__table__,
    RedemptionCode.__table__,
    UsageQuota.__table__,
]


@pytest.fixture(scope="module")
def pg_engine():
    engine = create_engine(os.environ["ZENSTORY_TEST_POSTGRES_URL"], pool_pre_ping=True)
    for table in reversed(TABLES):
        table.drop(engine, checkfirst=True)
    for table in TABLES:
        table.create(engine, checkfirst=True)
    try:
        yield engine
    finally:
        for table in reversed(TABLES):
            table.drop(engine, checkfirst=True)
        engine.dispose()


def _user(session: Session, suffix: str) -> User:
    user = User(
        email=f"pg-{suffix}@example.com",
        username=f"pg-{suffix}",
        hashed_password="hashed",
        email_verified=True,
        is_active=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _valid_code(random_part: str) -> str:
    tier_duration = "PRO7"
    signature = hmac.new(
        SECRET.encode(), f"{tier_duration}-{random_part}".encode(), hashlib.sha256
    ).digest()
    checksum = signature[:4].hex().upper()[:4]
    return f"ERG-{tier_duration}-{checksum}-{random_part}"


def test_distinct_redemptions_for_one_user_extend_by_fourteen_days(pg_engine, monkeypatch):
    monkeypatch.setenv("REDEMPTION_CODE_HMAC_SECRET", SECRET)
    with Session(pg_engine) as setup:
        admin = _user(setup, "redeem-admin")
        member = _user(setup, "redeem-member")
        admin_id = admin.id
        member_id = member.id
        plan = SubscriptionPlan(
            name="pro",
            display_name="Pro",
            display_name_en="Pro",
            is_active=True,
        )
        setup.add(plan)
        setup.commit()
        codes = [_valid_code("12345678"), _valid_code("87654321")]
        for code in codes:
            setup.add(
                RedemptionCode(
                    code=code,
                    code_type="single_use",
                    tier="pro",
                    duration_days=7,
                    max_uses=1,
                    created_by=admin_id,
                )
            )
        setup.commit()

    def redeem(code: str):
        with Session(pg_engine) as session:
            return redemption_service.redeem_code(session, code, member_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(redeem, codes))

    assert [result[0] for result in results] == [True, True]
    with Session(pg_engine) as verify:
        subscription = verify.exec(
            select(UserSubscription).where(UserSubscription.user_id == member_id)
        ).one()
        histories = verify.exec(
            select(SubscriptionHistory).where(SubscriptionHistory.user_id == member_id)
        ).all()
        redeemed_codes = verify.exec(
            select(RedemptionCode).where(RedemptionCode.code.in_(codes))
        ).all()

        assert (subscription.current_period_end - subscription.current_period_start).days == 14
        assert len(histories) == 2
        assert sorted(code.current_uses for code in redeemed_codes) == [1, 1]


def test_concurrent_first_quota_reservations_share_one_initialized_row(
    pg_engine, monkeypatch
):
    """Two first requests must both reserve instead of racing on the unique user row."""
    with Session(pg_engine) as setup:
        member = _user(setup, "quota-first-reservation")
        member_id = member.id

    barrier = Barrier(2)
    original_get = quota_service.get_user_quota

    def synchronize_missing_read(session: Session, user_id: str):
        quota = original_get(session, user_id)
        if quota is None:
            barrier.wait(timeout=10)
        return quota

    monkeypatch.setattr(quota_service, "get_user_quota", synchronize_missing_read)

    def reserve():
        with Session(pg_engine) as session:
            return quota_service.reserve_ai_conversation(session, member_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        reservations = list(pool.map(lambda _: reserve(), range(2)))

    assert all(reservation is not None for reservation in reservations)
    with Session(pg_engine) as verify:
        rows = verify.exec(
            select(UsageQuota).where(UsageQuota.user_id == member_id)
        ).all()
        assert len(rows) == 1
        assert rows[0].ai_conversations_used == 2


def test_quota_initialization_without_commit_keeps_caller_transaction_owned(pg_engine):
    member_id = "quota-no-commit-target"
    with Session(pg_engine) as caller:
        caller.add(
            User(
                id=member_id,
                email="pg-quota-no-commit-target@example.com",
                username="pg-quota-no-commit-target",
                hashed_password="hashed",
                email_verified=True,
                is_active=True,
            )
        )
        quota = quota_service.create_default_quota(caller, member_id, commit=False)
        assert quota.user_id == member_id
        caller.rollback()

    with Session(pg_engine) as verify:
        assert verify.get(User, member_id) is None
        assert verify.exec(
            select(UsageQuota).where(UsageQuota.user_id == member_id)
        ).first() is None


def test_default_quota_conflict_preserves_existing_usage(pg_engine):
    with Session(pg_engine) as setup:
        member = _user(setup, "quota-existing-usage")
        quota = quota_service.create_default_quota(setup, member.id)
        quota.ai_conversations_used = 2
        setup.add(quota)
        setup.commit()
        member_id = member.id

    with Session(pg_engine) as caller:
        quota_service.create_default_quota(caller, member_id)

    with Session(pg_engine) as verify:
        quota = verify.exec(
            select(UsageQuota).where(UsageQuota.user_id == member_id)
        ).one()
        assert quota.ai_conversations_used == 2
