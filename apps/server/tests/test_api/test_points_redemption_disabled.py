"""Disabling point redemption must preserve earned points and paid time."""

from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlmodel import Session, select

from models import User
from models.points import PointsTransaction
from models.subscription import (
    SubscriptionHistory,
    SubscriptionPlan,
    UsageQuota,
    UserSubscription,
)
from services.core.auth_service import create_access_token
from services.features.points_service import POINTS_PRO_7DAYS_COST, points_service
from services.subscription.subscription_service import subscription_service


def _account_state(session: Session, user_id: str) -> dict:
    """Read persisted ledger, subscription, quota and history without ORM caching."""
    session.expire_all()
    return {
        model.__tablename__: [
            record.model_dump()
            for record in session.exec(
                select(model).where(model.user_id == user_id).order_by(model.id)
            ).all()
        ]
        for model in (PointsTransaction, UserSubscription, UsageQuota, SubscriptionHistory)
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("flag_value", [None, "false"], ids=["unset", "false"])
@pytest.mark.parametrize("days", [7, 14, 30])
@pytest.mark.parametrize("plan_name", ["free", "pro"])
async def test_closed_redemption_preserves_funded_account(
    client: AsyncClient,
    db_session: Session,
    monkeypatch,
    flag_value: str | None,
    days: int,
    plan_name: str,
):
    if flag_value is None:
        monkeypatch.delenv("POINTS_PRO_REDEMPTION_ENABLED", raising=False)
    else:
        monkeypatch.setenv("POINTS_PRO_REDEMPTION_ENABLED", flag_value)

    user_id = uuid4().hex
    user = User(
        id=user_id,
        username=f"closed_points_{user_id}",
        email=f"closed_points_{user_id}@example.test",
        hashed_password="unused",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    for name in ("free", "pro"):
        db_session.add(SubscriptionPlan(name=name, display_name=name, features={}))
    db_session.commit()
    subscription_service.create_user_subscription(
        db_session, user_id, plan_name=plan_name, duration_days=14
    )
    points_service.earn_points(
        db_session,
        user_id,
        amount=POINTS_PRO_7DAYS_COST * 10,
        transaction_type="test_setup",
    )
    before = _account_state(db_session, user_id)
    balance_before = points_service.get_balance(db_session, user_id)

    response = await client.post(
        "/api/v1/points/redeem",
        headers={"Authorization": "Bearer " + create_access_token({"sub": user_id})},
        json={"days": days},
    )

    assert response.status_code == 403, response.text
    assert response.json()["error_code"] == "ERR_POINTS_REDEMPTION_DISABLED"
    assert _account_state(db_session, user_id) == before
    assert points_service.get_balance(db_session, user_id) == balance_before


@pytest.mark.asyncio
async def test_closed_redemption_still_requires_authentication(client: AsyncClient, monkeypatch):
    monkeypatch.delenv("POINTS_PRO_REDEMPTION_ENABLED", raising=False)
    response = await client.post("/api/v1/points/redeem", json={"days": 7})
    assert response.status_code == 401
