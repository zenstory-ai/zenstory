"""
Tests for admin plans and dashboard stats endpoints.
"""

from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlmodel import Session

from config.datetime_utils import utcnow
from models import Project, User
from models.subscription import SubscriptionPlan, UserSubscription
from services.core.auth_service import hash_password


async def create_user(
    db_session: Session,
    username: str,
    email: str,
    password: str = "password123",
    *,
    is_superuser: bool = False,
    is_active: bool = True,
    created_at=None,
) -> User:
    """Create and persist a user for tests."""
    user = User(
        username=username,
        email=email,
        hashed_password=hash_password(password),
        email_verified=True,
        is_active=is_active,
        is_superuser=is_superuser,
        created_at=created_at or utcnow().replace(tzinfo=None),
        updated_at=utcnow().replace(tzinfo=None),
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


@pytest.mark.integration
async def test_admin_list_plans_and_update_plan_success(client: AsyncClient, db_session: Session):
    admin = await create_user(db_session, "admin_plan_1", "admin_plan_1@example.com", is_superuser=True)

    pro_plan = SubscriptionPlan(
        name="pro",
        display_name="Pro",
        display_name_en="Pro",
        price_monthly_cents=1999,
        price_yearly_cents=19999,
        features={"ai_conversations_per_day": 80},
        is_active=True,
    )
    free_plan = SubscriptionPlan(
        name="free",
        display_name="Free",
        display_name_en="Free",
        price_monthly_cents=0,
        price_yearly_cents=0,
        features={"ai_conversations_per_day": 5},
        is_active=True,
    )
    db_session.add(pro_plan)
    db_session.add(free_plan)
    db_session.commit()
    db_session.refresh(pro_plan)

    token = await login_user(client, admin.username)

    list_response = await client.get("/api/admin/plans", headers=auth_headers(token))
    assert list_response.status_code == 200
    plan_names = {item["name"] for item in list_response.json()}
    assert {"pro", "free"}.issubset(plan_names)

    update_response = await client.put(
        f"/api/admin/plans/{pro_plan.id}",
        headers=auth_headers(token),
        json={
            "display_name": "Pro Plus",
            "price_monthly_cents": 2999,
            "is_active": False,
            "features": {"ai_conversations_per_day": 120},
        },
    )

    assert update_response.status_code == 200
    payload = update_response.json()
    assert payload["display_name"] == "Pro Plus"
    assert payload["price_monthly_cents"] == 2999
    assert payload["is_active"] is False
    assert payload["features"]["ai_conversations_per_day"] == 120

    db_session.refresh(pro_plan)
    assert pro_plan.display_name == "Pro Plus"
    assert pro_plan.price_monthly_cents == 2999
    assert pro_plan.is_active is False


@pytest.mark.integration
async def test_admin_dashboard_stats_aggregates_core_metrics(
    client: AsyncClient,
    db_session: Session,
):
    now = utcnow().replace(tzinfo=None)
    yesterday = now - timedelta(days=1)

    admin = await create_user(
        db_session,
        "admin_dashboard_1",
        "admin_dashboard_1@example.com",
        is_superuser=True,
        created_at=yesterday,
    )
    active_user = await create_user(
        db_session,
        "dashboard_user_active",
        "dashboard_user_active@example.com",
        created_at=now,
    )
    inactive_user = await create_user(
        db_session,
        "dashboard_user_inactive",
        "dashboard_user_inactive@example.com",
        is_active=False,
        created_at=yesterday,
    )

    db_session.add(
        Project(
            name="Dashboard Project",
            owner_id=active_user.id,
            created_at=now,
            updated_at=now,
        )
    )

    pro_plan = SubscriptionPlan(
        name="pro",
        display_name="Pro",
        display_name_en="Pro",
        price_monthly_cents=1999,
        price_yearly_cents=19999,
        features={"ai_conversations_per_day": 80},
        is_active=True,
    )
    free_plan = SubscriptionPlan(
        name="free",
        display_name="Free",
        display_name_en="Free",
        price_monthly_cents=0,
        price_yearly_cents=0,
        features={"ai_conversations_per_day": 5},
        is_active=True,
    )
    db_session.add(pro_plan)
    db_session.add(free_plan)
    db_session.commit()
    db_session.refresh(pro_plan)
    db_session.refresh(free_plan)

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
        UserSubscription(
            user_id=inactive_user.id,
            plan_id=free_plan.id,
            status="cancelled",
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
            cancel_at_period_end=True,
        )
    )

    db_session.commit()

    token = await login_user(client, admin.username)
    response = await client.get("/api/admin/dashboard/stats", headers=auth_headers(token))

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_users"] == 3
    assert payload["active_users"] == 2
    assert payload["new_users_today"] == 1
    assert payload["total_projects"] == 1
    assert payload["active_subscriptions"] == 1
    assert payload["pro_users"] == 1


@pytest.mark.integration
async def test_admin_update_plan_not_found_returns_404(client: AsyncClient, db_session: Session):
    admin = await create_user(db_session, "admin_plan_404", "admin_plan_404@example.com", is_superuser=True)
    token = await login_user(client, admin.username)

    response = await client.put(
        "/api/admin/plans/does-not-exist",
        headers=auth_headers(token),
        json={"display_name": "Pro X"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "ERR_NOT_FOUND"


@pytest.mark.integration
@pytest.mark.parametrize(
    "method,path_template,payload",
    [
        ("GET", "/api/admin/plans", None),
        ("GET", "/api/admin/dashboard/stats", None),
        ("PUT", "/api/admin/plans/{plan_id}", {"display_name": "forbidden"}),
    ],
)
async def test_admin_plans_and_dashboard_endpoints_forbidden_for_non_superuser(
    client: AsyncClient,
    db_session: Session,
    method: str,
    path_template: str,
    payload: dict | None,
):
    normal_user = await create_user(db_session, "normal_plan_forbidden", "normal_plan_forbidden@example.com")

    plan = SubscriptionPlan(
        name="starter",
        display_name="Starter",
        display_name_en="Starter",
        price_monthly_cents=999,
        price_yearly_cents=9999,
        features={"ai_conversations_per_day": 10},
        is_active=True,
    )
    db_session.add(plan)
    db_session.commit()
    db_session.refresh(plan)

    token = await login_user(client, normal_user.username)
    path = path_template.format(plan_id=plan.id)

    response = await client.request(method, path, headers=auth_headers(token), json=payload)

    assert response.status_code == 403
    assert response.json()["detail"] == "ERR_NOT_AUTHORIZED"
