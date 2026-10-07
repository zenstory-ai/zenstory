"""
Request-driven admin support e2e workflows.

These tests cover audit logs, check-in/quota stats, and plan management through
the real admin HTTP surface.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlmodel import Session, select

from config.datetime_utils import beijing_date, beijing_day_bounds, utcnow
from models import User
from models.points import CheckInRecord
from models.subscription import AdminAuditLog, SubscriptionPlan, UserSubscription
from services.core.auth_service import hash_password
from services.quota_service import quota_service

pytestmark = pytest.mark.e2e


def _identity(prefix: str) -> tuple[str, str]:
    suffix = uuid4().hex[:8]
    username = f"{prefix}_{suffix}"
    return username, f"{username}@example.com"


async def _create_user(
    db_session: Session,
    *,
    prefix: str,
    is_superuser: bool = False,
    password: str = "password123",
) -> User:
    username, email = _identity(prefix)
    user = User(
        username=username,
        email=email,
        hashed_password=hash_password(password),
        email_verified=True,
        is_active=True,
        is_superuser=is_superuser,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


async def _login(client: AsyncClient, *, identifier: str, password: str = "password123") -> dict:
    response = await client.post(
        "/api/auth/login",
        data={"username": identifier, "password": password},
    )
    assert response.status_code == 200
    return response.json()


def _auth_headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def _get_or_create_plan(db_session: Session, *, name: str, display_name: str) -> SubscriptionPlan:
    existing = db_session.exec(select(SubscriptionPlan).where(SubscriptionPlan.name == name)).first()
    if existing:
        return existing

    plan = SubscriptionPlan(
        name=name,
        display_name=display_name,
        display_name_en=display_name,
        price_monthly_cents=0 if name == "free" else 2900,
        price_yearly_cents=0 if name == "free" else 29000,
        features={
            "ai_conversations_per_day": 20 if name == "free" else 9999,
            "max_projects": 3 if name == "free" else 10,
            "material_uploads": 5 if name == "free" else 50,
            "custom_skills": 3 if name == "free" else 20,
            "inspiration_copies_monthly": 10 if name == "free" else 100,
        },
        is_active=True,
    )
    db_session.add(plan)
    db_session.commit()
    db_session.refresh(plan)
    return plan


@pytest.mark.asyncio
async def test_admin_audit_logs_roundtrip_lists_filtered_actions(
    client: AsyncClient,
    db_session: Session,
):
    admin = await _create_user(db_session, prefix="admin_audit", is_superuser=True)
    target = await _create_user(db_session, prefix="audit_target")
    free_plan = _get_or_create_plan(db_session, name=f"free_{uuid4().hex[:6]}", display_name="Free")
    now = utcnow()
    subscription = UserSubscription(
        user_id=target.id,
        plan_id=free_plan.id,
        status="active",
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
        cancel_at_period_end=False,
    )
    db_session.add(subscription)
    db_session.commit()

    login_payload = await _login(client, identifier=admin.username)
    headers = _auth_headers(login_payload["access_token"])

    update_response = await client.put(
        f"/api/admin/subscriptions/{target.id}",
        json={"status": "cancelled"},
        headers=headers,
    )
    assert update_response.status_code == 200

    response = await client.get(
        "/api/admin/audit-logs?page=1&page_size=20&resource_type=subscription&action=update_subscription",
        headers=headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["page"] == 1
    assert payload["page_size"] == 20
    assert len(payload["items"]) == 1
    assert payload["items"][0]["action"] == "update_subscription"
    assert payload["items"][0]["resource_type"] == "subscription"
    assert payload["items"][0]["resource_id"] == target.id


@pytest.mark.asyncio
async def test_admin_checkin_roundtrip_reports_exact_stats_and_records(
    client: AsyncClient,
    db_session: Session,
):
    admin = await _create_user(db_session, prefix="admin_checkin", is_superuser=True)
    target = await _create_user(db_session, prefix="checkin_target")

    # Check-ins store their Beijing calendar date; admin stats count Beijing days.
    today_start, _ = beijing_day_bounds(utcnow())
    today_at = today_start + timedelta(hours=2)
    yesterday_at = today_start - timedelta(hours=2)
    db_session.add(
        CheckInRecord(
            user_id=target.id, check_in_date=beijing_date(today_at), streak_days=8, points_earned=10,
            created_at=today_at,
        )
    )
    db_session.add(
        CheckInRecord(
            user_id=target.id, check_in_date=beijing_date(yesterday_at),
            streak_days=7, points_earned=10, created_at=yesterday_at,
        )
    )
    db_session.commit()

    login_payload = await _login(client, identifier=admin.email)
    headers = _auth_headers(login_payload["access_token"])

    stats_response = await client.get("/api/admin/check-in/stats", headers=headers)
    assert stats_response.status_code == 200
    stats_payload = stats_response.json()
    assert stats_payload["today_count"] == 1
    assert stats_payload["yesterday_count"] == 1
    assert stats_payload["week_total"] == 2
    assert int(stats_payload["streak_distribution"]["7"]) == 1

    records_response = await client.get(
        f"/api/admin/check-in/records?page=1&page_size=20&user_id={target.id}",
        headers=headers,
    )
    assert records_response.status_code == 200
    records_payload = records_response.json()
    assert records_payload["total"] == 2
    assert all(item["user_id"] == target.id for item in records_payload["items"])


@pytest.mark.asyncio
async def test_admin_quota_roundtrip_reports_exact_usage_and_user_detail(
    client: AsyncClient,
    db_session: Session,
):
    admin = await _create_user(db_session, prefix="admin_quota", is_superuser=True)
    target = await _create_user(db_session, prefix="quota_target")

    pro_plan = _get_or_create_plan(db_session, name="pro", display_name="Pro")
    subscription = UserSubscription(
        user_id=target.id,
        plan_id=pro_plan.id,
        status="active",
        current_period_start=utcnow() - timedelta(days=1),
        current_period_end=utcnow() + timedelta(days=30),
        cancel_at_period_end=False,
    )
    quota = quota_service.create_default_quota(db_session, target.id, commit=False)
    quota.ai_conversations_used = 11
    quota.material_uploads_used = 2
    quota.material_decompositions_used = 1
    quota.skill_creates_used = 3
    quota.inspiration_copies_used = 4
    db_session.add(subscription)
    db_session.add(quota)
    db_session.commit()

    login_payload = await _login(client, identifier=admin.email)
    headers = _auth_headers(login_payload["access_token"])

    usage_response = await client.get("/api/admin/quota/usage", headers=headers)
    assert usage_response.status_code == 200
    usage_payload = usage_response.json()
    assert "material_uploads" not in usage_payload
    assert usage_payload["material_decompositions"] == 1
    assert usage_payload["skills_created"] == 3
    assert usage_payload["inspiration_copies"] == 4

    detail_response = await client.get(f"/api/admin/quota/{target.username}", headers=headers)
    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert detail_payload["user_id"] == target.id
    assert detail_payload["plan_name"] == "pro"
    assert detail_payload["ai_conversations"]["used"] == 11
    assert detail_payload["material_decompositions"]["used"] == 1
    assert detail_payload["inspiration_copies"]["used"] == 4


@pytest.mark.asyncio
async def test_admin_plans_roundtrip_lists_updates_and_audits_plan_changes(
    client: AsyncClient,
    db_session: Session,
):
    admin = await _create_user(db_session, prefix="admin_plan", is_superuser=True)
    plan = _get_or_create_plan(db_session, name=f"plan_{uuid4().hex[:6]}", display_name="基础版")

    login_payload = await _login(client, identifier=admin.username)
    headers = _auth_headers(login_payload["access_token"])

    list_response = await client.get("/api/admin/plans", headers=headers)
    assert list_response.status_code == 200
    assert any(item["id"] == plan.id for item in list_response.json())

    update_response = await client.put(
        f"/api/admin/plans/{plan.id}",
        json={
            "display_name": "专业版",
            "display_name_en": "Pro",
            "price_monthly_cents": 2999,
            "price_yearly_cents": 29999,
            "features": {"ai_conversations_per_day": 100, "max_projects": 30},
            "is_active": False,
        },
        headers=headers,
    )
    assert update_response.status_code == 200
    update_payload = update_response.json()
    assert update_payload["display_name"] == "专业版"
    assert update_payload["display_name_en"] == "Pro"
    assert update_payload["is_active"] is False

    audit_log = db_session.exec(
        select(AdminAuditLog).where(
            AdminAuditLog.resource_type == "plan",
            AdminAuditLog.resource_id == plan.id,
            AdminAuditLog.action == "update_plan",
        )
    ).first()
    assert audit_log is not None
    assert audit_log.admin_user_id == admin.id
