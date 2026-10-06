"""
Focused E2E contracts for points redemption and earn opportunities.

These tests pin the backend responses that the web points/settings flows rely on:
- check-in status / check-in / transaction history
- redeeming points for Pro membership
- insufficient balance redemption failure
- earn-opportunities state derivation
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlmodel import Session, select

from config.datetime_utils import beijing_date, utcnow
from models.inspiration import Inspiration
from models.points import CheckInRecord, PointsTransaction
from models.public_skill import PublicSkill
from models.referral import InviteCode
from models.skill import UserSkill
from models.subscription import SubscriptionPlan
from services.features.points_service import (
    POINTS_CHECK_IN_STREAK,
    POINTS_PRO_7DAYS_COST,
    POINTS_SKILL_CONTRIBUTION,
    STREAK_BONUS_THRESHOLD,
    points_service,
)

from .test_core_api_e2e import _auth_headers, _create_user, _login

pytestmark = pytest.mark.e2e


def _ensure_plan(
    db_session: Session,
    *,
    name: str,
    display_name: str,
    features: dict,
) -> SubscriptionPlan:
    existing = db_session.exec(select(SubscriptionPlan).where(SubscriptionPlan.name == name)).first()
    if existing:
        return existing

    plan = SubscriptionPlan(
        name=name,
        display_name=display_name,
        display_name_en=display_name,
        price_monthly_cents=2900 if name != "free" else 0,
        price_yearly_cents=29000 if name != "free" else 0,
        features=features,
        is_active=True,
    )
    db_session.add(plan)
    db_session.commit()
    db_session.refresh(plan)
    return plan


@pytest.mark.asyncio
async def test_points_check_in_status_and_transactions_contract_roundtrip(
    client: AsyncClient,
    db_session: Session,
):
    user = await _create_user(db_session, prefix="points_checkin_contract")

    login_payload = await _login(client, identifier=user.email)
    headers = _auth_headers(login_payload["access_token"])

    status_before = await client.get("/api/v1/points/check-in/status", headers=headers)
    assert status_before.status_code == 200
    assert status_before.json() == {
        "checked_in": False,
        "streak_days": 0,
        "points_earned_today": 0,
    }

    check_in_response = await client.post("/api/v1/points/check-in", headers=headers)
    assert check_in_response.status_code == 200
    check_in_payload = check_in_response.json()
    assert check_in_payload["success"] is True
    assert check_in_payload["points_earned"] > 0
    assert check_in_payload["streak_days"] == 1

    status_after = await client.get("/api/v1/points/check-in/status", headers=headers)
    assert status_after.status_code == 200
    status_after_payload = status_after.json()
    assert status_after_payload["checked_in"] is True
    assert status_after_payload["streak_days"] == 1
    assert status_after_payload["points_earned_today"] == check_in_payload["points_earned"]

    balance_response = await client.get("/api/v1/points/balance", headers=headers)
    assert balance_response.status_code == 200
    assert balance_response.json()["available"] == check_in_payload["points_earned"]

    transactions_response = await client.get("/api/v1/points/transactions?page=1&page_size=5", headers=headers)
    assert transactions_response.status_code == 200
    transactions_payload = transactions_response.json()
    assert transactions_payload["total"] == 1
    assert transactions_payload["page"] == 1
    assert transactions_payload["page_size"] == 5
    assert transactions_payload["total_pages"] == 1
    assert transactions_payload["transactions"][0]["transaction_type"] == "check_in"
    assert transactions_payload["transactions"][0]["amount"] == check_in_payload["points_earned"]
    assert transactions_payload["transactions"][0]["balance_after"] == check_in_payload["points_earned"]


@pytest.mark.asyncio
async def test_points_redeem_contract_upgrades_to_pro_and_records_spend(
    client: AsyncClient,
    db_session: Session,
):
    user = await _create_user(db_session, prefix="points_redeem_contract")
    _ensure_plan(db_session, name="free", display_name="Free", features={"max_projects": 1})
    _ensure_plan(db_session, name="pro", display_name="Pro", features={"max_projects": 8})

    points_service.earn_points(
        session=db_session,
        user_id=user.id,
        amount=POINTS_PRO_7DAYS_COST * 3,
        transaction_type="test_setup",
        description="Seed points for e2e redeem contract",
    )

    login_payload = await _login(client, identifier=user.email)
    headers = _auth_headers(login_payload["access_token"])

    redeem_response = await client.post(
        "/api/v1/points/redeem",
        json={"days": 14},
        headers=headers,
    )
    assert redeem_response.status_code == 200
    redeem_payload = redeem_response.json()
    assert redeem_payload["success"] is True
    assert redeem_payload["points_spent"] == POINTS_PRO_7DAYS_COST * 2
    assert redeem_payload["pro_days"] == 14
    assert redeem_payload["new_period_end"]

    balance_response = await client.get("/api/v1/points/balance", headers=headers)
    assert balance_response.status_code == 200
    assert balance_response.json()["available"] == POINTS_PRO_7DAYS_COST

    status_response = await client.get("/api/v1/subscription/me", headers=headers)
    assert status_response.status_code == 200
    status_payload = status_response.json()
    assert status_payload["tier"] == "pro"
    assert status_payload["status"] == "active"

    transactions_response = await client.get("/api/v1/points/transactions?page=1&page_size=20", headers=headers)
    assert transactions_response.status_code == 200
    transactions_payload = transactions_response.json()
    assert any(
        item["transaction_type"] == "redeem_pro" and item["amount"] == -(POINTS_PRO_7DAYS_COST * 2)
        for item in transactions_payload["transactions"]
    )


@pytest.mark.asyncio
async def test_points_redeem_contract_rejects_insufficient_balance_with_required_amount(
    client: AsyncClient,
    db_session: Session,
):
    user = await _create_user(db_session, prefix="points_redeem_insufficient")
    _ensure_plan(db_session, name="pro", display_name="Pro", features={"max_projects": 8})

    points_service.earn_points(
        session=db_session,
        user_id=user.id,
        amount=POINTS_PRO_7DAYS_COST - 10,
        transaction_type="test_setup",
        description="Seed insufficient points for e2e redeem contract",
    )

    login_payload = await _login(client, identifier=user.username)
    headers = _auth_headers(login_payload["access_token"])

    redeem_response = await client.post(
        "/api/v1/points/redeem",
        json={"days": 7},
        headers=headers,
    )
    assert redeem_response.status_code == 402
    payload = redeem_response.json()
    detail = payload.get("error_detail", payload.get("detail"))
    assert detail["message"] == "Insufficient points for redemption"
    assert detail["required"] == POINTS_PRO_7DAYS_COST
    assert detail["available"] == POINTS_PRO_7DAYS_COST - 10

    balance_response = await client.get("/api/v1/points/balance", headers=headers)
    assert balance_response.status_code == 200
    assert balance_response.json()["available"] == POINTS_PRO_7DAYS_COST - 10

    transactions = db_session.exec(
        select(PointsTransaction).where(PointsTransaction.user_id == user.id)
    ).all()
    assert all(tx.transaction_type != "redeem_pro" for tx in transactions)


@pytest.mark.asyncio
async def test_points_earn_opportunities_contract_reflects_completed_and_available_states(
    client: AsyncClient,
    db_session: Session,
    monkeypatch,
):
    monkeypatch.setenv("INSPIRATIONS_ENABLED", "true")
    user = await _create_user(db_session, prefix="points_opportunity_contract")
    user.avatar_url = "https://example.com/avatar.png"
    db_session.add(user)

    previous = utcnow() - timedelta(days=1)
    yesterday = beijing_date(previous)
    db_session.add(
        CheckInRecord(
            user_id=user.id,
            check_in_date=yesterday,
            created_at=previous,
            streak_days=STREAK_BONUS_THRESHOLD - 1,
            points_earned=10,
        )
    )
    db_session.add(
        InviteCode(
            code=f"INV-{user.username[:4].upper()}-A1B2",
            owner_id=user.id,
            max_uses=3,
            current_uses=0,
            is_active=True,
        )
    )
    db_session.add(
        UserSkill(
            user_id=user.id,
            name="共享技能",
            description="已分享技能",
            triggers='["共享"]',
            instructions="Shared skill instructions",
            is_active=True,
            is_shared=True,
        )
    )
    # 贡献积分卡以「已核准的社区投稿」为准（送审中的 is_shared 不算）
    db_session.add(
        PublicSkill(
            name="共享技能",
            instructions="Shared skill instructions",
            category="writing",
            source="community",
            author_id=user.id,
            status="approved",
        )
    )
    db_session.add(
        Inspiration(
            name="共享灵感",
            description="已审核通过的灵感",
            project_type="novel",
            tags='["idea"]',
            snapshot_data="{}",
            source="community",
            author_id=user.id,
            status="approved",
        )
    )
    db_session.commit()

    login_payload = await _login(client, identifier=user.email)
    headers = _auth_headers(login_payload["access_token"])

    response = await client.get("/api/v1/points/earn-opportunities", headers=headers)
    assert response.status_code == 200
    payload = response.json()
    assert [item["type"] for item in payload] == [
        "check_in",
        "check_in_streak",
        "referral",
        "skill_contribution",
    ]

    by_type = {item["type"]: item for item in payload}
    assert by_type["check_in"]["is_completed"] is False
    assert by_type["check_in"]["is_available"] is True

    assert by_type["check_in_streak"]["points"] == POINTS_CHECK_IN_STREAK
    assert by_type["check_in_streak"]["is_completed"] is False
    assert by_type["check_in_streak"]["is_available"] is True

    assert by_type["referral"]["is_completed"] is False
    assert by_type["referral"]["is_available"] is True

    assert by_type["skill_contribution"]["points"] == POINTS_SKILL_CONTRIBUTION
    assert by_type["skill_contribution"]["is_completed"] is True
    assert by_type["skill_contribution"]["is_available"] is True

    # 灵感投稿、完善资料没有发放积分的代码路径，不再作为积分机会展示
    assert "inspiration_contribution" not in by_type
    assert "profile_complete" not in by_type
