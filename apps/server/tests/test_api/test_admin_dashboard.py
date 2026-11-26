"""Tests for admin dashboard statistics endpoint."""

from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlmodel import Session

from config.datetime_utils import utcnow
from models import (
    ACTIVATION_EVENT_FIRST_AI_ACTION_ACCEPTED,
    ACTIVATION_EVENT_FIRST_FILE_SAVED,
    ACTIVATION_EVENT_PROJECT_CREATED,
    ACTIVATION_EVENT_SIGNUP_SUCCESS,
    Project,
    User,
)
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
async def test_admin_dashboard_stats_returns_core_metrics(
    client: AsyncClient,
    db_session: Session,
):
    admin = await create_user(
        db_session,
        "admin_dashboard_stats",
        "admin_dashboard_stats@example.com",
        is_superuser=True,
    )
    active_user = await create_user(db_session, "dashboard_user_active", "dashboard_user_active@example.com")
    await create_user(
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
    assert data["active_subscriptions"] >= 1
    assert data["pro_users"] >= 1


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
    steps = {step["event_name"]: step["users"] for step in payload["steps"]}
    assert list(steps) == [
        ACTIVATION_EVENT_SIGNUP_SUCCESS,
        ACTIVATION_EVENT_PROJECT_CREATED,
        "first_ai_message_sent",
        "first_ai_reply_succeeded",
        ACTIVATION_EVENT_FIRST_AI_ACTION_ACCEPTED,
    ]
    assert steps[ACTIVATION_EVENT_SIGNUP_SUCCESS] >= 2
    assert steps[ACTIVATION_EVENT_PROJECT_CREATED] >= 1
    assert steps[ACTIVATION_EVENT_FIRST_AI_ACTION_ACCEPTED] >= 1


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
