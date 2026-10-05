"""Plan defaults, lapsed-expiry races, renewal base, downgrade codes and skill caps."""

import hashlib
import hmac
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, SQLModel, select

from config.datetime_utils import utcnow
from models import User
from models.skill import UserSkill
from models.subscription import RedemptionCode, SubscriptionPlan, UserSubscription
from services.quota_service import quota_service
from services.subscription.redemption_service import redemption_service
from services.subscription.subscription_service import subscription_service

# Production pro features before the backfill migration.
PRODUCTION_PRO_FEATURES = {
    "max_projects": -1,
    "custom_prompts": True,
    "export_formats": ["txt"],
    "material_uploads": 5,
    "context_window_tokens": 16384,
    "file_versions_per_file": 100,
    "material_decompositions": 5,
    "ai_conversations_per_day": -1,
    "materials_library_access": True,
}
HMAC_SECRET = "entitlement-hardening-secret-at-least-32-chars"


def _user(session: Session, name: str) -> User:
    user = User(
        username=name,
        email=f"{name}@example.com",
        hashed_password="x",
        email_verified=True,
        is_active=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _plan(session: Session, name: str, price: int, features: dict) -> SubscriptionPlan:
    plan = SubscriptionPlan(
        name=name,
        display_name=name,
        price_monthly_cents=price,
        price_yearly_cents=price * 10,
        features=features,
        is_active=True,
    )
    session.add(plan)
    session.commit()
    session.refresh(plan)
    return plan


def _subscribe(session, user, plan, *, status="active", days=30) -> UserSubscription:
    now = utcnow()
    subscription = UserSubscription(
        user_id=user.id,
        plan_id=plan.id,
        status=status,
        current_period_start=now - timedelta(days=1),
        current_period_end=now + timedelta(days=days),
    )
    session.add(subscription)
    session.commit()
    session.refresh(subscription)
    return subscription


def test_pro_plan_missing_keys_use_pro_defaults_not_free(db_session: Session):
    user = _user(db_session, "pro_defaults")
    pro = _plan(db_session, "pro", 4900, dict(PRODUCTION_PRO_FEATURES))
    _subscribe(db_session, user, pro)

    assert quota_service.check_feature_quota(db_session, user.id, "skill_create") == (True, 0, 20)
    assert quota_service.check_feature_quota(db_session, user.id, "inspiration_copy") == (True, 0, 100)
    snapshot = quota_service.get_quota_snapshot(db_session, user.id)
    assert snapshot["skill_creates"]["limit"] == 20
    assert snapshot["inspiration_copies"]["limit"] == 100

    # Unknown plans and the free plan keep free-tier fallbacks.
    other = _user(db_session, "custom_plan_user")
    custom = _plan(db_session, "team-trial", 100, {})
    _subscribe(db_session, other, custom)
    assert quota_service.check_feature_quota(db_session, other.id, "skill_create")[2] == 3


@pytest.mark.integration
def test_lapsed_expiry_does_not_clobber_a_concurrent_renewal(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'expiry-race.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    SQLModel.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    with factory() as session:
        user = _user(session, "expiry_race")
        _plan(session, "free", 0, {})
        pro = _plan(session, "pro", 4900, dict(PRODUCTION_PRO_FEATURES))
        subscription = _subscribe(session, user, pro, days=-1)  # lapsed, still "active"
        user_id, subscription_id = user.id, subscription.id

    original = quota_service._expire_if_still_lapsed

    def renew_between_read_and_write(session, sub_id, now):
        # A payment lands after get_user_plan read the stale row.
        with factory() as other:
            subscription_service.create_user_subscription(other, user_id, "pro", 30)
        return original(session, sub_id, now)

    monkeypatch.setattr(quota_service, "_expire_if_still_lapsed", renew_between_read_and_write)
    with factory() as session:
        quota_service.get_user_plan(session, user_id)

    with factory() as session:
        stored = session.get(UserSubscription, subscription_id)
        assert stored.status == "active"
        assert stored.current_period_end.replace(tzinfo=None) > utcnow().replace(tzinfo=None)
        assert quota_service.get_user_plan(session, user_id).name == "pro"


def test_lapsed_subscription_is_still_expired_without_a_race(db_session: Session):
    user = _user(db_session, "plain_expiry")
    _plan(db_session, "free", 0, {})
    pro = _plan(db_session, "pro", 4900, {})
    subscription = _subscribe(db_session, user, pro, days=-1)

    assert quota_service.get_user_plan(db_session, user.id).name == "free"
    db_session.expire_all()
    assert db_session.get(UserSubscription, subscription.id).status == "expired"


@pytest.mark.parametrize("status", ["cancelled", "expired"])
def test_renewal_after_revocation_starts_now(db_session: Session, status: str):
    user = _user(db_session, f"revoked_{status}")
    pro = _plan(db_session, "pro", 4900, {})
    _subscribe(db_session, user, pro, status=status, days=300)

    renewed = subscription_service.create_user_subscription(db_session, user.id, "pro", 30)

    remaining = renewed.current_period_end.replace(tzinfo=None) - utcnow().replace(tzinfo=None)
    assert timedelta(days=29) < remaining <= timedelta(days=30)
    assert renewed.status == "active"


def test_active_renewal_still_extends_from_period_end(db_session: Session):
    user = _user(db_session, "active_renewal")
    pro = _plan(db_session, "pro", 4900, {})
    _subscribe(db_session, user, pro, days=10)

    renewed = subscription_service.create_user_subscription(db_session, user.id, "pro", 30)

    remaining = renewed.current_period_end.replace(tzinfo=None) - utcnow().replace(tzinfo=None)
    assert timedelta(days=39) < remaining <= timedelta(days=40)


def _code(session: Session, tier: str, creator: User, tier_duration: str) -> str:
    random_part = "ABCDEFGH"
    signature = hmac.new(
        HMAC_SECRET.encode(), f"{tier_duration}-{random_part}".encode(), hashlib.sha256
    ).digest()
    code = f"ERG-{tier_duration}-{signature[:4].hex().upper()[:4]}-{random_part}"
    session.add(
        RedemptionCode(
            code=code,
            code_type="single_use",
            tier=tier,
            duration_days=7,
            max_uses=1,
            current_uses=0,
            created_by=creator.id,
            is_active=True,
        )
    )
    session.commit()
    return code


def test_lower_tier_code_is_refused_while_paid_plan_runs(db_session: Session):
    user = _user(db_session, "downgrade_user")
    _plan(db_session, "free", 0, {})
    pro = _plan(db_session, "pro", 4900, {})
    subscription = _subscribe(db_session, user, pro, days=300)
    code = _code(db_session, "free", user, "FREE7D")

    with patch.object(redemption_service, "get_hmac_secret", return_value=HMAC_SECRET):
        success, message, _info = redemption_service.redeem_code(db_session, code, user.id)
    assert success is False
    assert "lower-tier" in message
    db_session.expire_all()
    stored = db_session.get(UserSubscription, subscription.id)
    assert stored.plan_id == pro.id
    assert db_session.exec(select(RedemptionCode).where(RedemptionCode.code == code)).one().current_uses == 0

    # Once the paid plan has lapsed the same code is fine.
    stored.current_period_end = utcnow() - timedelta(days=1)
    db_session.add(stored)
    db_session.commit()
    with patch.object(redemption_service, "get_hmac_secret", return_value=HMAC_SECRET):
        success, _message, _info = redemption_service.redeem_code(db_session, code, user.id)
    assert success is True


@pytest.mark.integration
def test_concurrent_skill_creates_cannot_exceed_owned_limit(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'skill-race.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    SQLModel.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    with factory() as session:
        user = _user(session, "skill_race")
        _plan(session, "free", 0, {"custom_skills": 3})
        free = session.exec(select(SubscriptionPlan).where(SubscriptionPlan.name == "free")).one()
        _subscribe(session, user, free, days=3650)
        session.add_all([UserSkill(user_id=user.id, name=f"s{i}", instructions="x") for i in range(2)])
        session.commit()
        quota_service.ensure_default_quota(session, user.id)
        user_id = user.id

    first_holds_lock = Event()

    def first():
        with factory() as session:
            allowed, used, limit = quota_service.check_custom_skill_slot(session, user_id)
            first_holds_lock.set()
            time.sleep(0.5)  # The second request runs its check meanwhile.
            if allowed:
                session.add(UserSkill(user_id=user_id, name="first", instructions="x"))
            session.commit()
            return allowed, used, limit

    def second():
        assert first_holds_lock.wait(timeout=10)
        with factory() as session:
            allowed, used, limit = quota_service.check_custom_skill_slot(session, user_id)
            if allowed:
                session.add(UserSkill(user_id=user_id, name="second", instructions="x"))
            session.commit()
            return allowed, used, limit

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(first)
        second_future = pool.submit(second)
        assert first_future.result(timeout=30) == (True, 2, 3)
        assert second_future.result(timeout=30) == (False, 3, 3)

    with factory() as session:
        owned = session.exec(select(UserSkill).where(UserSkill.user_id == user_id)).all()
        assert len(owned) == 3
