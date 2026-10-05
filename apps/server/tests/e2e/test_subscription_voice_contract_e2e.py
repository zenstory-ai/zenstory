"""
Focused E2E contracts for subscription/quota and voice status.

These tests keep the nightly web suites grounded on stable backend contracts.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlmodel import Session

from models.skill import UserSkill
from models.subscription import RedemptionCode, SubscriptionHistory, UsageQuota
from .test_core_api_e2e import (
    _attach_subscription,
    _build_valid_redemption_code,
    _create_plan,
    _create_user,
    _login,
)

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_subscription_status_and_quota_contract(
    client: AsyncClient,
    db_session: Session,
):
    user = await _create_user(db_session, prefix="subscription_contract")
    free_plan = _create_plan(
        db_session,
        name="free",
        display_name="Free",
        features={
            "ai_conversations_per_day": 10,
            "max_projects": 3,
            "material_uploads": 5,
            "material_decompositions": 5,
            "custom_skills": 3,
            "inspiration_copies_monthly": 10,
            "export_formats": ["txt"],
        },
    )
    pro_plan = _create_plan(
        db_session,
        name="pro",
        display_name="Pro",
        features={
            "ai_conversations_per_day": -1,
            "max_projects": 8,
            "material_uploads": -1,
            "material_decompositions": -1,
            "custom_skills": 20,
            "inspiration_copies_monthly": 100,
            "export_formats": ["txt", "md"],
        },
    )
    _attach_subscription(db_session, user_id=user.id, plan_id=pro_plan.id)

    quota = UsageQuota(
        user_id=user.id,
        period_start=datetime.utcnow() - timedelta(days=1),
        period_end=datetime.utcnow() + timedelta(days=29),
        ai_conversations_used=6,
        material_uploads_used=1,
        material_decompositions_used=2,
        skill_creates_used=9,
        inspiration_copies_used=3,
        monthly_period_start=datetime.utcnow() - timedelta(days=1),
        monthly_period_end=datetime.utcnow() + timedelta(days=29),
        last_reset_at=datetime.utcnow() - timedelta(hours=1),
    )
    db_session.add(quota)
    history = SubscriptionHistory(
        user_id=user.id,
        action="upgraded",
        plan_name="Pro",
        start_date=datetime.utcnow() - timedelta(days=1),
        end_date=datetime.utcnow() + timedelta(days=29),
        metadata={"source": "e2e"},
    )
    db_session.add(history)
    # skill_creates.used reports owned custom skills, not the monthly counter.
    db_session.add_all(
        [UserSkill(user_id=user.id, name=f"skill {i}", instructions="x") for i in range(4)]
    )
    db_session.commit()

    login_payload = await _login(client, identifier=user.email)
    headers = {"Authorization": f"Bearer {login_payload['access_token']}"}

    status_response = await client.get("/api/v1/subscription/me", headers=headers)
    assert status_response.status_code == 200
    status_payload = status_response.json()
    assert status_payload["tier"] == "pro"
    assert status_payload["status"] == "active"
    assert status_payload["display_name"] == "Pro"
    assert status_payload["features"]["max_projects"] == 8

    quota_response = await client.get("/api/v1/subscription/quota", headers=headers)
    assert quota_response.status_code == 200
    quota_payload = quota_response.json()
    assert quota_payload["ai_conversations"]["used"] == 6
    assert quota_payload["projects"]["limit"] == 8
    assert quota_payload["skill_creates"]["used"] == 4
    assert quota_payload["inspiration_copies"]["used"] == 3

    history_response = await client.get("/api/v1/subscription/history", headers=headers)
    assert history_response.status_code == 200
    history_payload = history_response.json()
    assert len(history_payload) >= 1
    assert history_payload[0]["plan_name"] == "Pro"

    # Keep free plan referenced so cleanup ordering still sees both plans.
    assert free_plan.name == "free"


@pytest.mark.asyncio
async def test_redeem_code_contract_upgrades_plan_and_records_history(
    client: AsyncClient,
    db_session: Session,
):
    user = await _create_user(db_session, prefix="redeem_contract")
    _create_plan(
        db_session,
        name="free",
        display_name="Free",
        features={"max_projects": 3, "export_formats": ["txt"]},
    )
    pro_plan = _create_plan(
        db_session,
        name="pro",
        display_name="Pro",
        features={"max_projects": 8, "export_formats": ["txt", "md"]},
    )

    code_value = _build_valid_redemption_code(tier_duration="PRO7M", random_part="ABCDEF12")
    code_record = RedemptionCode(
        code=code_value,
        tier="pro",
        duration_days=7,
        code_type="single_use",
        is_active=True,
        max_uses=1,
        current_uses=0,
        created_by=user.id,
    )
    db_session.add(code_record)
    db_session.commit()

    login_payload = await _login(client, identifier=user.username)
    headers = {"Authorization": f"Bearer {login_payload['access_token']}"}

    redeem_response = await client.post(
        "/api/v1/subscription/redeem",
        json={"code": code_value, "source": "e2e_contract"},
        headers=headers,
    )
    assert redeem_response.status_code == 200
    redeem_payload = redeem_response.json()
    assert redeem_payload["success"] is True
    assert redeem_payload["tier"] == "pro"
    assert redeem_payload["duration_days"] == 7

    status_response = await client.get("/api/v1/subscription/me", headers=headers)
    assert status_response.status_code == 200
    assert status_response.json()["tier"] == pro_plan.name

    db_session.refresh(code_record)
    assert code_record.current_uses == 1
    assert user.id in code_record.redeemed_by


@pytest.mark.asyncio
async def test_voice_status_contract_reports_configured_and_unconfigured_states(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("TENCENT_SECRET_ID", raising=False)
    monkeypatch.delenv("TENCENT_SECRET_KEY", raising=False)

    unconfigured_response = await client.get("/api/v1/voice/status")
    assert unconfigured_response.status_code == 200
    unconfigured_payload = unconfigured_response.json()
    assert unconfigured_payload["configured"] is False
    assert unconfigured_payload["provider"] == "tencent"
    assert "webm" in unconfigured_payload["supported_formats"]

    monkeypatch.setenv("TENCENT_SECRET_ID", "contract-secret-id")
    monkeypatch.setenv("TENCENT_SECRET_KEY", "contract-secret-key")

    configured_response = await client.get("/api/v1/voice/status")
    assert configured_response.status_code == 200
    configured_payload = configured_response.json()
    assert configured_payload["configured"] is True
    assert configured_payload["provider"] == "tencent"
    assert configured_payload["service"] == "一句话识别"
    assert configured_payload["max_duration_seconds"] == 60
