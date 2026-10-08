"""Tests for admin plan management endpoints."""

from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlmodel import Session, select

from config.datetime_utils import utcnow
from models import User
from models.subscription import AdminAuditLog, SubscriptionPlan
from services.core.auth_service import hash_password


async def create_user(
    db_session: Session,
    username: str,
    email: str,
    password: str = "password123",
    *,
    is_superuser: bool = False,
) -> User:
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


async def login_user(client: AsyncClient, username: str, password: str = "password123") -> str:
    response = await client.post(
        "/api/auth/login",
        data={"username": username, "password": password},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def create_plan(db_session: Session, name: str) -> SubscriptionPlan:
    plan = SubscriptionPlan(
        name=name,
        display_name="基础版",
        display_name_en="Basic",
        price_monthly_cents=999,
        price_yearly_cents=9999,
        features={"ai_conversations_per_day": 10, "max_projects": 3},
        is_active=True,
        updated_at=utcnow() - timedelta(days=1),
    )
    db_session.add(plan)
    db_session.commit()
    db_session.refresh(plan)
    return plan


@pytest.mark.integration
async def test_admin_list_and_update_plan_creates_audit_log(
    client: AsyncClient,
    db_session: Session,
):
    admin = await create_user(
        db_session,
        "admin_plan_update",
        "admin_plan_update@example.com",
        is_superuser=True,
    )
    plan = create_plan(db_session, "plan_test_admin")

    token = await login_user(client, admin.username)

    list_response = await client.get(
        "/api/admin/plans",
        headers=auth_headers(token),
    )
    assert list_response.status_code == 200
    listed_ids = {item["id"] for item in list_response.json()}
    assert plan.id in listed_ids

    update_response = await client.put(
        f"/api/admin/plans/{plan.id}",
        headers=auth_headers(token),
        json={
            "display_name": "专业版",
            "display_name_en": "Pro",
            "price_monthly_cents": 2999,
            "price_yearly_cents": 29999,
            "features": {"ai_conversations_per_day": 100, "max_projects": 30},
            "is_active": False,
        },
    )

    assert update_response.status_code == 200
    payload = update_response.json()
    assert payload["display_name"] == "专业版"
    assert payload["display_name_en"] == "Pro"
    assert payload["price_monthly_cents"] == 2999
    assert payload["price_yearly_cents"] == 29999
    assert payload["features"]["ai_conversations_per_day"] == 100
    assert payload["is_active"] is False

    refreshed_plan = db_session.exec(
        select(SubscriptionPlan).where(SubscriptionPlan.id == plan.id)
    ).first()
    assert refreshed_plan is not None
    assert refreshed_plan.display_name == "专业版"
    assert refreshed_plan.is_active is False

    audit_log = db_session.exec(
        select(AdminAuditLog).where(
            AdminAuditLog.resource_type == "plan",
            AdminAuditLog.resource_id == plan.id,
            AdminAuditLog.action == "update_plan",
        )
    ).first()
    assert audit_log is not None
    assert audit_log.admin_user_id == admin.id
    assert audit_log.old_value["display_name"] == "基础版"
    assert audit_log.new_value["display_name"] == "专业版"


@pytest.mark.integration
@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("GET", "/api/admin/plans", None),
        ("PUT", "/api/admin/plans/{plan_id}", {"display_name": "forbidden"}),
    ],
)
async def test_admin_plans_endpoints_forbidden_for_non_superuser(
    method: str,
    path: str,
    payload: dict | None,
    client: AsyncClient,
    db_session: Session,
):
    user = await create_user(db_session, "plans_normal_user", "plans_normal_user@example.com")
    plan = create_plan(db_session, "plan_test_forbidden")

    token = await login_user(client, user.username)
    final_path = path.format(plan_id=plan.id)

    if method == "GET":
        response = await client.get(final_path, headers=auth_headers(token))
    else:
        response = await client.put(final_path, headers=auth_headers(token), json=payload)

    assert response.status_code == 403
