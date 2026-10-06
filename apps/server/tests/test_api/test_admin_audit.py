"""Tests for admin audit log endpoints."""

import pytest
from httpx import AsyncClient
from sqlmodel import Session

from models import User
from services.admin_audit_service import admin_audit_service
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


@pytest.mark.integration
async def test_admin_audit_logs_list_supports_filters_and_shorthand_action(
    client: AsyncClient,
    db_session: Session,
):
    admin = await create_user(
        db_session,
        "admin_audit_logs",
        "admin_audit_logs@example.com",
        is_superuser=True,
    )

    admin_audit_service.log_action(
        db_session,
        admin_user_id=admin.id,
        action="update_subscription",
        resource_type="subscription",
        resource_id="sub-1",
        old_value={"status": "active"},
        new_value={"status": "cancelled"},
    )
    admin_audit_service.log_action(
        db_session,
        admin_user_id=admin.id,
        action="update_plan",
        resource_type="plan",
        resource_id="plan-1",
        old_value={"price_monthly_cents": 1000},
        new_value={"price_monthly_cents": 1200},
    )
    admin_audit_service.log_action(
        db_session,
        admin_user_id=admin.id,
        action="create_code",
        resource_type="code",
        resource_id="code-1",
        new_value={"tier": "pro"},
    )

    token = await login_user(client, admin.username)

    response = await client.get(
        "/api/admin/audit-logs",
        headers=auth_headers(token),
        params={
            "page": 1,
            "page_size": 20,
            "resource_type": "subscription",
            "action": "update",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["page"] == 1
    assert payload["page_size"] == 20
    assert len(payload["items"]) == 1
    assert payload["items"][0]["action"] == "update_subscription"
    assert payload["items"][0]["resource_type"] == "subscription"

    exact_action_response = await client.get(
        "/api/admin/audit-logs",
        headers=auth_headers(token),
        params={"action": "create_code", "page": 1, "page_size": 20},
    )
    assert exact_action_response.status_code == 200
    exact_items = exact_action_response.json()["items"]
    assert len(exact_items) == 1
    assert exact_items[0]["action"] == "create_code"
    assert exact_items[0]["resource_type"] == "code"


@pytest.mark.integration
async def test_admin_audit_logs_forbidden_for_non_superuser(
    client: AsyncClient,
    db_session: Session,
):
    user = await create_user(db_session, "audit_normal_user", "audit_normal_user@example.com")
    token = await login_user(client, user.username)

    response = await client.get(
        "/api/admin/audit-logs",
        headers=auth_headers(token),
    )

    assert response.status_code == 403


@pytest.mark.integration
async def test_admin_audit_pagination_orders_timestamp_ties(client, db_session):
    from datetime import datetime

    from models.subscription import AdminAuditLog
    from services.core.auth_service import create_access_token

    admin = await create_user(db_session, "audit_ties", "audit_ties@example.com", is_superuser=True)
    timestamp = datetime(2026, 4, 8, 9)
    ids = ["audit-a", "audit-f", "audit-b", "audit-e", "audit-c", "audit-d"]
    for log_id in ids:
        db_session.add(AdminAuditLog(id=log_id, admin_user_id=admin.id,
                                    action="update_user", resource_type="user", created_at=timestamp))
    db_session.commit()
    headers = auth_headers(create_access_token({"sub": admin.id}))
    actual = []
    for page in (1, 2, 3):
        response = await client.get("/api/admin/audit-logs", headers=headers,
                                    params={"page": page, "page_size": 2})
        assert response.status_code == 200
        assert response.json()["total"] == 6
        actual.extend(item["id"] for item in response.json()["items"])
    assert actual == sorted(ids, reverse=True)
    assert len(set(actual)) == 6
