"""
Request-driven admin management e2e workflows.

These tests cover admin user management, subscription management, and
inspiration moderation through the real HTTP API.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlmodel import Session, select

from models import Inspiration, User
from models.subscription import SubscriptionPlan, UserSubscription
from services.core.auth_service import hash_password

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


def _create_plan(
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
        price_monthly_cents=0 if name == "free" else 2900,
        price_yearly_cents=0 if name == "free" else 29000,
        features=features,
        is_active=True,
    )
    db_session.add(plan)
    db_session.commit()
    db_session.refresh(plan)
    return plan


@pytest.mark.asyncio
async def test_admin_users_roundtrip_lists_updates_and_soft_deletes_target_user(
    client: AsyncClient,
    db_session: Session,
):
    admin = await _create_user(db_session, prefix="admin_user_flow", is_superuser=True)
    target = await _create_user(db_session, prefix="managed_user")

    admin_login = await _login(client, identifier=admin.username)
    headers = _auth_headers(admin_login["access_token"])

    list_response = await client.get(
        f"/api/admin/users?search={target.username}",
        headers=headers,
    )
    assert list_response.status_code == 200
    list_payload = list_response.json()
    assert any(item["id"] == target.id for item in list_payload["items"])
    assert list_payload["total"] == len(list_payload["items"])

    detail_response = await client.get(f"/api/admin/users/{target.id}", headers=headers)
    assert detail_response.status_code == 200
    assert detail_response.json()["email"] == target.email

    updated_username = f"{target.username}_updated"
    update_response = await client.put(
        f"/api/admin/users/{target.id}",
        json={"username": updated_username},
        headers=headers,
    )
    assert update_response.status_code == 200
    assert update_response.json()["username"] == updated_username

    delete_response = await client.delete(f"/api/admin/users/{target.id}", headers=headers)
    assert delete_response.status_code == 200
    assert delete_response.json()["is_active"] is False

    refreshed_target = db_session.get(User, target.id)
    assert refreshed_target is not None
    assert refreshed_target.username == updated_username
    assert refreshed_target.is_active is False


@pytest.mark.asyncio
async def test_admin_subscriptions_roundtrip_lists_fetches_and_updates_user_subscription(
    client: AsyncClient,
    db_session: Session,
):
    admin = await _create_user(db_session, prefix="admin_subscription_flow", is_superuser=True)
    target = await _create_user(db_session, prefix="subscription_target")

    free_plan = _create_plan(
        db_session,
        name="free",
        display_name="Free",
        features={"ai_conversations_per_day": 10, "max_projects": 3},
    )
    pro_plan = _create_plan(
        db_session,
        name="pro",
        display_name="Pro",
        features={"ai_conversations_per_day": 9999, "max_projects": 10},
    )

    now = datetime.utcnow()
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

    admin_login = await _login(client, identifier=admin.email)
    headers = _auth_headers(admin_login["access_token"])

    list_response = await client.get("/api/admin/subscriptions?page=1&page_size=100", headers=headers)
    assert list_response.status_code == 200
    list_payload = list_response.json()
    target_row = next(item for item in list_payload["items"] if item["user_id"] == target.id)
    assert target_row["plan_name"] == "free"
    assert target_row["status"] == "active"

    detail_response = await client.get(f"/api/admin/subscriptions/{target.id}", headers=headers)
    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert detail_payload["subscription"]["user_id"] == target.id
    assert detail_payload["plan"]["name"] == "free"

    update_response = await client.put(
        f"/api/admin/subscriptions/{target.id}",
        json={"plan_name": "pro", "duration_days": 30, "status": "cancelled"},
        headers=headers,
    )
    assert update_response.status_code == 200
    assert update_response.json()["success"] is True

    refreshed_subscription = db_session.exec(
        select(UserSubscription).where(UserSubscription.user_id == target.id)
    ).first()
    assert refreshed_subscription is not None
    assert refreshed_subscription.plan_id == pro_plan.id
    assert refreshed_subscription.status == "cancelled"


@pytest.mark.asyncio
async def test_admin_inspiration_moderation_roundtrip_lists_details_and_approves_submission(
    client: AsyncClient,
    db_session: Session,
    monkeypatch,
):
    monkeypatch.setenv("INSPIRATIONS_ENABLED", "true")
    admin = await _create_user(db_session, prefix="admin_inspiration_flow", is_superuser=True)
    author = await _create_user(db_session, prefix="inspiration_author")

    admin_login = await _login(client, identifier=admin.username)
    author_login = await _login(client, identifier=author.email)

    project_response = await client.post(
        "/api/v1/projects",
        json={"name": "Community Inspiration Source", "project_type": "novel"},
        headers=_auth_headers(author_login["access_token"]),
    )
    assert project_response.status_code == 200
    project_id = project_response.json()["id"]

    file_response = await client.post(
        f"/api/v1/projects/{project_id}/files",
        json={"title": "Source Chapter", "content": "Source inspiration content", "file_type": "draft"},
        headers=_auth_headers(author_login["access_token"]),
    )
    assert file_response.status_code == 200

    submit_response = await client.post(
        "/api/v1/inspirations",
        json={
            "project_id": project_id,
            "name": "Pending Community Inspiration",
            "description": "needs admin review",
            "tags": ["community", "pending"],
        },
        headers=_auth_headers(author_login["access_token"]),
    )
    assert submit_response.status_code == 201
    inspiration_id = submit_response.json()["inspiration_id"]
    assert submit_response.json()["status"] == "pending"

    admin_headers = _auth_headers(admin_login["access_token"])

    list_response = await client.get("/api/admin/inspirations?status=pending", headers=admin_headers)
    assert list_response.status_code == 200
    list_payload = list_response.json()
    pending_item = next(item for item in list_payload["items"] if item["id"] == inspiration_id)
    assert pending_item["creator_id"] == author.id
    assert pending_item["status"] == "pending"

    detail_response = await client.get(f"/api/admin/inspirations/{inspiration_id}", headers=admin_headers)
    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert detail_payload["name"] == "Pending Community Inspiration"
    assert detail_payload["creator_name"] == author.username

    review_response = await client.post(
        f"/api/admin/inspirations/{inspiration_id}/review",
        json={"approve": True},
        headers=admin_headers,
    )
    assert review_response.status_code == 200
    assert review_response.json()["inspiration_id"] == inspiration_id

    refreshed_inspiration = db_session.get(Inspiration, inspiration_id)
    assert refreshed_inspiration is not None
    assert refreshed_inspiration.status == "approved"
    assert refreshed_inspiration.reviewed_by == admin.id
