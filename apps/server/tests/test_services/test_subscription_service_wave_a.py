from __future__ import annotations

from sqlmodel import Session, select

from models import User
from models.subscription import SubscriptionHistory, SubscriptionPlan, UserSubscription
from services.core.auth_service import hash_password
from services.quota_service import quota_service
from services.subscription.subscription_service import subscription_service


def _create_user(db_session: Session, suffix: str = "wave-a") -> User:
    user = User(
        email=f"subscription-{suffix}@example.com",
        username=f"subscription-{suffix}",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _create_plan(db_session: Session, name: str) -> SubscriptionPlan:
    plan = SubscriptionPlan(
        name=name,
        display_name=name.title(),
        display_name_en=name.title(),
        price_monthly_cents=2900 if name != "free" else 0,
        price_yearly_cents=29000 if name != "free" else 0,
        features={"ai_conversations_per_day": -1 if name != "free" else 10},
        is_active=True,
    )
    db_session.add(plan)
    db_session.commit()
    db_session.refresh(plan)
    return plan


def test_get_or_create_free_plan_creates_default_plan_when_missing(db_session: Session):
    plan = subscription_service._get_or_create_free_plan(db_session)

    assert plan.name == "free"
    assert plan.price_monthly_cents == 0
    assert isinstance(plan.features, dict)


def test_ensure_user_subscription_and_quota_creates_both_records(db_session: Session):
    user = _create_user(db_session, "create-both")

    result = subscription_service.ensure_user_subscription_and_quota(
        db_session,
        user.id,
        source="wave-a-test",
    )

    subscription = subscription_service.get_user_subscription(db_session, user.id)
    quota = quota_service.get_user_quota(db_session, user.id)

    assert result == {"created_subscription": True, "created_quota": True}
    assert subscription is not None
    assert quota is not None


def test_ensure_user_subscription_and_quota_is_noop_when_records_exist(db_session: Session):
    user = _create_user(db_session, "noop")
    _create_plan(db_session, "free")
    subscription_service.create_user_subscription(
        db_session,
        user.id,
        "free",
        duration_days=30,
    )
    quota_service.create_default_quota(db_session, user.id)

    result = subscription_service.ensure_user_subscription_and_quota(
        db_session,
        user.id,
        source="wave-a-test",
    )

    assert result == {"created_subscription": False, "created_quota": False}


def test_ensure_user_subscription_returns_existing_subscription(db_session: Session):
    user = _create_user(db_session, "ensure-existing")
    _create_plan(db_session, "free")
    existing = subscription_service.create_user_subscription(
        db_session,
        user.id,
        "free",
        duration_days=30,
    )

    result = subscription_service.ensure_user_subscription(db_session, user.id)

    assert result.id == existing.id


def test_create_user_subscription_commit_false_allows_rollback(db_session: Session):
    user = _create_user(db_session, "rollback")
    plan = _create_plan(db_session, "pro")

    subscription = subscription_service.create_user_subscription(
        db_session,
        user.id,
        plan.name,
        duration_days=7,
        metadata={"source": "wave-a"},
        commit=False,
    )

    assert subscription.id is not None
    assert db_session.get(UserSubscription, subscription.id) is not None
    assert db_session.exec(select(SubscriptionHistory)).first() is not None

    db_session.rollback()

    assert db_session.get(UserSubscription, subscription.id) is None
    assert db_session.exec(select(SubscriptionHistory)).first() is None
