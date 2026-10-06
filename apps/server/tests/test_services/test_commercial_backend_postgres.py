from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, func
from sqlmodel import Session, select

from core.error_codes import ErrorCode
from core.error_handler import APIException
from models.entities import User
from models.points import PointsTransaction
from models.referral import InviteCode, Referral, UserReward, UserStats
from models.subscription import (
    RedemptionCode,
    SubscriptionHistory,
    SubscriptionPlan,
    UserSubscription,
)
from services.features.referral_service import (
    complete_referral_and_reward,
    create_invite_code,
)
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
    InviteCode.__table__,
    Referral.__table__,
    UserReward.__table__,
    UserStats.__table__,
    PointsTransaction.__table__,
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


def test_concurrent_referral_completion_rewards_each_participant_once(pg_engine):
    with Session(pg_engine) as setup:
        inviter = _user(setup, "referral-inviter")
        invitee = _user(setup, "referral-invitee")
        inviter_id = inviter.id
        invitee_id = invitee.id
        code = InviteCode(code="PGAA-BBBB", owner_id=inviter_id)
        setup.add(code)
        setup.commit()
        setup.refresh(code)
        referral = Referral(
            inviter_id=inviter_id,
            invitee_id=invitee_id,
            invite_code_id=code.id,
        )
        setup.add(referral)
        setup.commit()
        setup.refresh(referral)
        referral_id = referral.id

    def complete():
        with Session(pg_engine) as session:
            asyncio.run(complete_referral_and_reward(referral_id, session))

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: complete(), range(2)))

    with Session(pg_engine) as verify:
        rewards = verify.exec(
            select(UserReward).where(UserReward.referral_id == referral_id)
        ).all()
        transactions = verify.exec(
            select(PointsTransaction).where(PointsTransaction.source_id == referral_id)
        ).all()
        stats = verify.exec(
            select(UserStats).where(UserStats.user_id == inviter_id)
        ).one()

        assert sorted(reward.user_id for reward in rewards) == sorted([inviter_id, invitee_id])
        assert sorted(transaction.user_id for transaction in transactions) == sorted(
            [inviter_id, invitee_id]
        )
        assert stats.successful_invites == 1
        assert stats.total_points == 100


def test_concurrent_invite_creation_respects_active_cap(pg_engine):
    with Session(pg_engine) as setup:
        owner = _user(setup, "invite-owner")
        owner_id = owner.id
        setup.add(InviteCode(code="PG11-AAAA", owner_id=owner_id))
        setup.add(InviteCode(code="PG22-BBBB", owner_id=owner_id))
        setup.commit()

    def create():
        with Session(pg_engine) as session:
            try:
                create_invite_code(owner_id, session)
                return "created"
            except APIException as exc:
                assert exc.error_code == ErrorCode.REFERRAL_MAX_CODES_REACHED
                return "limited"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: create(), range(2)))

    assert sorted(outcomes) == ["created", "limited"]
    with Session(pg_engine) as verify:
        active_count = verify.exec(
            select(func.count())
            .select_from(InviteCode)
            .where(InviteCode.owner_id == owner_id, InviteCode.is_active.is_(True))
        ).one()
        assert active_count == 3
