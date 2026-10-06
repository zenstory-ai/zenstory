"""Tests for admin dashboard statistics endpoint."""

from datetime import UTC, date, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlmodel import Session

from config.datetime_utils import utcnow
from models import (
    ACTIVATION_EVENT_FIRST_AI_ACTION_ACCEPTED,
    ACTIVATION_EVENT_FIRST_FILE_SAVED,
    ACTIVATION_EVENT_PROJECT_CREATED,
    ACTIVATION_EVENT_SIGNUP_SUCCESS,
    Inspiration,
    Project,
    User,
)
from models.points import CheckInRecord, PointsTransaction
from models.referral import InviteCode, Referral
from models.subscription import SubscriptionPlan, UserSubscription
from services.core.auth_service import hash_password
from services.features.activation_event_service import activation_event_service


async def create_user(
    db_session: Session,
    username: str,
    email: str,
    password: str = "password123",
    *,
    is_superuser: bool = False,
    is_active: bool = True,
) -> User:
    user = User(
        username=username,
        email=email,
        hashed_password=hash_password(password),
        email_verified=True,
        is_active=is_active,
        is_superuser=is_superuser,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


async def login_user(client: AsyncClient, username: str, password: str = "password123") -> str:
    response = await client.post(
        "/api/auth/login",
        data={"username": username, "password": password},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def get_or_create_plan(db_session: Session, name: str) -> SubscriptionPlan:
    from sqlmodel import select

    plan = db_session.exec(select(SubscriptionPlan).where(SubscriptionPlan.name == name)).first()
    if plan:
        return plan

    plan = SubscriptionPlan(
        name=name,
        display_name=name.title(),
        display_name_en=name.title(),
        price_monthly_cents=1999,
        price_yearly_cents=19999,
        features={"ai_conversations_per_day": 80},
        is_active=True,
    )
    db_session.add(plan)
    db_session.commit()
    db_session.refresh(plan)
    return plan


@pytest.mark.integration
async def test_admin_dashboard_stats_returns_commercialization_metrics(
    client: AsyncClient,
    db_session: Session,
    monkeypatch,
):
    monkeypatch.setenv("INSPIRATIONS_ENABLED", "true")
    admin = await create_user(
        db_session,
        "admin_dashboard_stats",
        "admin_dashboard_stats@example.com",
        is_superuser=True,
    )
    active_user = await create_user(db_session, "dashboard_user_active", "dashboard_user_active@example.com")
    inactive_user = await create_user(
        db_session,
        "dashboard_user_inactive",
        "dashboard_user_inactive@example.com",
        is_active=False,
    )

    now = utcnow()

    project = Project(
        name="Dashboard Project",
        description="project for dashboard stats",
        owner_id=active_user.id,
        project_type="novel",
    )
    db_session.add(project)

    db_session.add(
        Inspiration(
            name="Dashboard Pending Inspiration",
            description="pending",
            project_type="novel",
            tags="[]",
            snapshot_data='{"project_type":"novel","files":[]}',
            source="community",
            status="pending",
            author_id=active_user.id,
        )
    )
    db_session.add(
        Inspiration(
            name="Dashboard Approved Inspiration",
            description="approved",
            project_type="novel",
            tags="[]",
            snapshot_data='{"project_type":"novel","files":[]}',
            source="official",
            status="approved",
            author_id=admin.id,
        )
    )

    pro_plan = get_or_create_plan(db_session, "pro")
    db_session.add(
        UserSubscription(
            user_id=active_user.id,
            plan_id=pro_plan.id,
            status="active",
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
            cancel_at_period_end=False,
        )
    )

    db_session.add(
        PointsTransaction(
            user_id=active_user.id,
            amount=100,
            balance_after=100,
            transaction_type="check_in",
            is_expired=False,
        )
    )
    db_session.add(
        PointsTransaction(
            user_id=active_user.id,
            amount=-30,
            balance_after=70,
            transaction_type="unlock_material_slot",
        )
    )
    db_session.add(
        PointsTransaction(
            user_id=inactive_user.id,
            amount=50,
            balance_after=50,
            transaction_type="check_in",
            is_expired=True,
        )
    )

    db_session.add(
        CheckInRecord(
            user_id=active_user.id,
            check_in_date=now.date(),
            streak_days=3,
            points_earned=10,
        )
    )

    active_code = InviteCode(code="DASH-1001", owner_id=admin.id, max_uses=5, current_uses=0, is_active=True)
    inactive_code = InviteCode(code="DASH-1002", owner_id=admin.id, max_uses=5, current_uses=0, is_active=False)
    db_session.add(active_code)
    db_session.add(inactive_code)
    db_session.commit()
    db_session.refresh(active_code)

    db_session.add(
        Referral(
            inviter_id=admin.id,
            invitee_id=active_user.id,
            invite_code_id=active_code.id,
            status="COMPLETED",
            inviter_rewarded=False,
            created_at=now - timedelta(days=2),
        )
    )
    db_session.add(
        Referral(
            inviter_id=admin.id,
            invitee_id=inactive_user.id,
            invite_code_id=active_code.id,
            status="PENDING",
            inviter_rewarded=False,
            created_at=now - timedelta(days=12),
        )
    )
    db_session.commit()

    token = await login_user(client, admin.username)

    response = await client.get(
        "/api/admin/dashboard/stats",
        headers=auth_headers(token),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total_users"] >= 3
    assert data["active_users"] >= 2
    assert data["new_users_today"] >= 3
    assert data["total_projects"] >= 1
    assert data["total_inspirations"] >= 2
    assert data["pending_inspirations"] >= 1
    assert data["active_subscriptions"] >= 1
    assert data["pro_users"] >= 1
    assert data["total_points_in_circulation"] >= 70
    assert data["today_check_ins"] >= 1
    assert data["active_invite_codes"] >= 1
    assert data["week_referrals"] >= 1


@pytest.mark.integration
async def test_admin_dashboard_stats_forbidden_for_non_superuser(
    client: AsyncClient,
    db_session: Session,
):
    user = await create_user(db_session, "dashboard_normal_user", "dashboard_normal_user@example.com")
    token = await login_user(client, user.username)

    response = await client.get(
        "/api/admin/dashboard/stats",
        headers=auth_headers(token),
    )

    assert response.status_code == 403


@pytest.mark.integration
async def test_admin_dashboard_counts_legacy_check_in_in_current_beijing_day(
    client: AsyncClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    now = datetime(2026, 10, 5, 17, 0, tzinfo=UTC)  # 2026-10-06 01:00 in Beijing
    monkeypatch.setattr("api.admin.dashboard.utcnow", lambda: now)
    admin = await create_user(
        db_session,
        "dashboard_beijing_admin",
        "dashboard_beijing_admin@example.com",
        is_superuser=True,
    )
    target = await create_user(
        db_session,
        "dashboard_beijing_target",
        "dashboard_beijing_target@example.com",
    )
    current_format_target = await create_user(
        db_session,
        "dashboard_beijing_current",
        "dashboard_beijing_current@example.com",
    )
    db_session.add_all(
        [
            CheckInRecord(
                user_id=target.id,
                check_in_date=date(2026, 10, 5),
                streak_days=3,
                points_earned=10,
                created_at=now,
            ),
            CheckInRecord(
                user_id=current_format_target.id,
                check_in_date=date(2026, 10, 6),
                streak_days=1,
                points_earned=10,
                created_at=now + timedelta(minutes=5),
            ),
        ]
    )
    db_session.commit()

    token = await login_user(client, admin.username)
    response = await client.get(
        "/api/admin/dashboard/stats",
        headers=auth_headers(token),
    )

    assert response.status_code == 200
    assert response.json()["today_check_ins"] == 2


@pytest.mark.integration
async def test_admin_dashboard_activation_funnel_returns_step_counts(
    client: AsyncClient,
    db_session: Session,
):
    admin = await create_user(
        db_session,
        "admin_dashboard_funnel",
        "admin_dashboard_funnel@example.com",
        is_superuser=True,
    )
    user_a = await create_user(
        db_session,
        "dashboard_funnel_user_a",
        "dashboard_funnel_user_a@example.com",
    )
    user_b = await create_user(
        db_session,
        "dashboard_funnel_user_b",
        "dashboard_funnel_user_b@example.com",
    )

    activation_event_service.record_once(
        db_session, user_id=user_a.id, event_name=ACTIVATION_EVENT_SIGNUP_SUCCESS
    )
    activation_event_service.record_once(
        db_session, user_id=user_a.id, event_name=ACTIVATION_EVENT_PROJECT_CREATED
    )
    activation_event_service.record_once(
        db_session, user_id=user_a.id, event_name=ACTIVATION_EVENT_FIRST_FILE_SAVED
    )
    activation_event_service.record_once(
        db_session, user_id=user_a.id, event_name=ACTIVATION_EVENT_FIRST_AI_ACTION_ACCEPTED
    )
    activation_event_service.record_once(
        db_session, user_id=user_b.id, event_name=ACTIVATION_EVENT_SIGNUP_SUCCESS
    )

    token = await login_user(client, admin.username)
    response = await client.get(
        "/api/admin/dashboard/activation-funnel",
        headers=auth_headers(token),
        params={"days": 30},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["window_days"] == 30
    assert 0 <= payload["activation_rate"] <= 1
    assert [step["event_name"] for step in payload["steps"]] == [
        ACTIVATION_EVENT_SIGNUP_SUCCESS,
        ACTIVATION_EVENT_PROJECT_CREATED,
        ACTIVATION_EVENT_FIRST_FILE_SAVED,
        ACTIVATION_EVENT_FIRST_AI_ACTION_ACCEPTED,
    ]
    assert payload["steps"][0]["users"] >= 2
    assert payload["steps"][1]["users"] >= 1
    assert payload["steps"][2]["users"] >= 1
    assert payload["steps"][3]["users"] >= 1


@pytest.mark.integration
async def test_admin_dashboard_activation_funnel_forbidden_for_non_superuser(
    client: AsyncClient,
    db_session: Session,
):
    user = await create_user(
        db_session,
        "dashboard_funnel_normal_user",
        "dashboard_funnel_normal_user@example.com",
    )
    token = await login_user(client, user.username)

    response = await client.get(
        "/api/admin/dashboard/activation-funnel",
        headers=auth_headers(token),
        params={"days": 7},
    )

    assert response.status_code == 403
