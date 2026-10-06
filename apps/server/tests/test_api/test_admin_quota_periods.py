"""Admin quota details must match the active plan and current usage periods."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import select

from models import User
from models.subscription import SubscriptionPlan, UsageQuota, UserSubscription
from services.core.auth_service import hash_password


@pytest.mark.integration
@pytest.mark.parametrize("expired", [False, True])
async def test_admin_detail_uses_runtime_plan_defaults_and_resets_old_usage(
    client, db_session, monkeypatch, expired
):
    now = datetime(2026, 10, 5, 16, tzinfo=UTC)
    monkeypatch.setattr("services.quota_service.utcnow", lambda: now)
    admin = User(
        username="quota_period_admin", email="quota_period_admin@example.com",
        hashed_password=hash_password("password123"), email_verified=True,
        is_active=True, is_superuser=True,
    )
    target = User(
        username="quota_period_target", email="quota_period_target@example.com",
        hashed_password="unused", email_verified=True, is_active=True,
    )
    plan = SubscriptionPlan(name="pro", display_name="Pro", features={}, is_active=True)
    db_session.add_all([admin, target, plan])
    db_session.commit()
    db_session.add(UserSubscription(
        user_id=target.id, plan_id=plan.id, status="active",
        current_period_start=now - timedelta(days=1),
        current_period_end=now + timedelta(days=-1 if expired else 30),
    ))
    db_session.add(UsageQuota(
        user_id=target.id,
        period_start=now - timedelta(days=1), period_end=now,
        last_reset_at=now - timedelta(minutes=1), ai_conversations_used=20,
        material_uploads_used=2,
        material_decompositions_used=4,
        monthly_period_start=datetime(2026, 10, 1), monthly_period_end=datetime(2026, 11, 1),
    ))
    db_session.commit()
    login = await client.post("/api/auth/login", data={"username": admin.username, "password": "password123"})
    assert login.status_code == 200

    response = await client.get(
        f"/api/admin/quota/{target.id}",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["plan_name"] == ("free" if expired else "pro")
    assert data["ai_conversations_limit"] == (20 if expired else -1)
    assert data["ai_conversations_used"] == 0
    assert data["material_upload_limit"] == (0 if expired else 5)
    assert data["material_upload_used"] == 2
    assert data["material_decompose_limit"] == (0 if expired else 5)
    assert data["material_decompose_used"] == 4
    assert data["skill_create_limit"] == (3 if expired else 20)
    quota = db_session.exec(select(UsageQuota).where(UsageQuota.user_id == target.id)).one()
    db_session.refresh(quota)
    assert quota.period_end == datetime(2026, 10, 6, 16)


@pytest.mark.integration
async def test_admin_monthly_aggregate_excludes_non_current_beijing_periods(
    client, db_session, monkeypatch
):
    now = datetime(2026, 10, 5, 7, tzinfo=UTC)
    monkeypatch.setattr("api.admin.quotas.utcnow", lambda: now)
    admin = User(
        username="quota_aggregate_admin",
        email="quota_aggregate_admin@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
        is_superuser=True,
    )
    db_session.add(admin)
    db_session.add_all([
        UsageQuota(
            user_id="current-month-user",
            period_start=now,
            period_end=now + timedelta(days=1),
            last_reset_at=now,
            material_uploads_used=2,
            material_decompositions_used=3,
            skill_creates_used=4,
            inspiration_copies_used=5,
            monthly_period_start=datetime(2026, 9, 30, 16),
            monthly_period_end=datetime(2026, 10, 31, 16),
        ),
        UsageQuota(
            user_id="expired-month-user",
            period_start=now,
            period_end=now + timedelta(days=1),
            last_reset_at=now,
            material_uploads_used=20,
            material_decompositions_used=30,
            skill_creates_used=40,
            inspiration_copies_used=50,
            monthly_period_start=datetime(2026, 8, 31, 16),
            monthly_period_end=datetime(2026, 9, 30, 16),
        ),
        UsageQuota(
            user_id="legacy-current-month-user",
            period_start=now,
            period_end=now + timedelta(days=1),
            last_reset_at=now,
            material_uploads_used=7,
            material_decompositions_used=11,
            skill_creates_used=13,
            inspiration_copies_used=17,
            monthly_period_start=datetime(2026, 10, 1),
            monthly_period_end=datetime(2026, 11, 1),
        ),
        UsageQuota(
            user_id="unknown-month-user",
            period_start=now,
            period_end=now + timedelta(days=1),
            last_reset_at=now,
            material_uploads_used=200,
            material_decompositions_used=300,
            skill_creates_used=400,
            inspiration_copies_used=500,
        ),
    ])
    db_session.commit()
    login = await client.post(
        "/api/auth/login",
        data={"username": admin.username, "password": "password123"},
    )
    assert login.status_code == 200

    response = await client.get(
        "/api/admin/quota/usage",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "material_uploads": 9,
        "material_decomposes": 14,
        "skill_creates": 17,
        "inspiration_copies": 22,
    }
