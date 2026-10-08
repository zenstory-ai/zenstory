from __future__ import annotations

import hashlib
import hmac
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlmodel import Session

from core.error_codes import ErrorCode
from core.error_handler import APIException
from models import User
from models.subscription import RedemptionCode, SubscriptionPlan
from services.core.auth_service import hash_password
from services.subscription.redemption_service import redemption_service

SECRET = "test-secret-key-must-be-at-least-32-characters-long"


def _create_user(db_session: Session, suffix: str) -> User:
    user = User(
        email=f"redeem-{suffix}@example.com",
        username=f"redeem-{suffix}",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _create_plan(db_session: Session, name: str = "pro") -> SubscriptionPlan:
    plan = SubscriptionPlan(
        name=name,
        display_name=name.title(),
        display_name_en=name.title(),
        price_monthly_cents=2900 if name != "free" else 0,
        price_yearly_cents=29000 if name != "free" else 0,
        features={"ai_conversations_per_day": -1},
        is_active=True,
    )
    db_session.add(plan)
    db_session.commit()
    db_session.refresh(plan)
    return plan


def _valid_code(tier_duration: str = "PRO7M", random_part: str = "12345678") -> str:
    message = f"{tier_duration}-{random_part}"
    signature = hmac.new(SECRET.encode(), message.encode(), hashlib.sha256).digest()
    checksum = signature[:4].hex().upper()[:4]
    return f"ERG-{tier_duration}-{checksum}-{random_part}"


def _create_redemption_code(db_session: Session, admin_user_id: str, code: str) -> RedemptionCode:
    redemption_code = RedemptionCode(
        code=code,
        code_type="single_use",
        tier="pro",
        duration_days=30,
        max_uses=1,
        current_uses=0,
        created_by=admin_user_id,
        is_active=True,
    )
    db_session.add(redemption_code)
    db_session.commit()
    db_session.refresh(redemption_code)
    return redemption_code


def test_redeem_code_passes_upgrade_source_into_subscription_metadata(db_session: Session):
    admin = _create_user(db_session, "admin")
    user = _create_user(db_session, "member")
    _create_plan(db_session)
    code = _valid_code()
    _create_redemption_code(db_session, admin.id, code)

    captured: dict[str, object] = {}

    def fake_create_user_subscription(*args, **kwargs):
        captured["metadata"] = kwargs["metadata"]
        return SimpleNamespace(id="sub-wave-a")

    with (
        patch.object(redemption_service, "get_hmac_secret", return_value=SECRET),
        patch(
            "services.subscription.redemption_service.subscription_service.create_user_subscription",
            side_effect=fake_create_user_subscription,
        ),
    ):
        success, message, info = redemption_service.redeem_code(
            db_session,
            code,
            user.id,
            attribution_source="campaign-oauth",
        )

    assert success is True
    assert "Successfully redeemed" in message
    assert info == {
        "tier": "pro",
        "duration_days": 30,
        "subscription_id": "sub-wave-a",
    }
    assert captured["metadata"] == {
        "source": "redemption_code",
        "code_id": captured["metadata"]["code_id"],
        "upgrade_source": "campaign-oauth",
    }


def test_redeem_code_rolls_back_usage_when_subscription_creation_fails(db_session: Session):
    admin = _create_user(db_session, "rollback-admin")
    user = _create_user(db_session, "rollback-member")
    _create_plan(db_session)
    code = _valid_code(random_part="87654321")
    redemption_code = _create_redemption_code(db_session, admin.id, code)

    with (
        patch.object(redemption_service, "get_hmac_secret", return_value=SECRET),
        patch(
            "services.subscription.redemption_service.subscription_service.create_user_subscription",
            side_effect=RuntimeError("subscription boom"),
        ),
    ):
        with pytest.raises(APIException) as exc_info:
            redemption_service.redeem_code(db_session, code, user.id)

    db_session.refresh(redemption_code)

    assert exc_info.value.status_code == 500
    assert exc_info.value.error_code == ErrorCode.INTERNAL_SERVER_ERROR
    assert "subscription boom" not in str(exc_info.value.detail)
    assert redemption_code.current_uses == 0
    assert redemption_code.redeemed_by == []


def test_redeem_code_masks_missing_hmac_configuration(db_session: Session, monkeypatch):
    monkeypatch.delenv("REDEMPTION_CODE_HMAC_SECRET", raising=False)

    with pytest.raises(APIException) as exc_info:
        redemption_service.redeem_code(
            db_session,
            "ERG-PRO7-ABCD-12345678",
            "missing-hmac-user",
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.error_code == ErrorCode.INTERNAL_SERVER_ERROR
    assert "REDEMPTION_CODE_HMAC_SECRET" not in str(exc_info.value.detail)
