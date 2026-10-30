"""The scheduled renewal-reminder endpoint and the author-facing history."""

from datetime import datetime

import pytest
from httpx import AsyncClient
from sqlmodel import Session

from models import User
from models.subscription import SubscriptionHistory
from services.core.auth_service import hash_password
from services.subscription.renewal_reminder_service import RENEWAL_REMINDER_ACTION

URL = "/api/v1/subscription/internal/renewal-reminders"


@pytest.fixture(autouse=True)
def reminder_env(monkeypatch):
    monkeypatch.setenv("RENEWAL_REMINDER_TOKEN", "scheduled-job-token")
    monkeypatch.setenv("FRONTEND_URL", "https://app.example.com")


@pytest.mark.integration
@pytest.mark.parametrize("headers", [{}, {"X-Internal-Token": "wrong"}])
async def test_endpoint_rejects_missing_or_wrong_token(client: AsyncClient, headers):
    response = await client.post(URL, params={"dry_run": "true"}, headers=headers)

    assert response.status_code == 401


@pytest.mark.integration
async def test_endpoint_is_closed_when_no_token_is_configured(client: AsyncClient, monkeypatch):
    monkeypatch.delenv("RENEWAL_REMINDER_TOKEN")

    response = await client.post(URL, params={"dry_run": "true"}, headers={"X-Internal-Token": ""})

    assert response.status_code == 401


@pytest.mark.integration
async def test_dry_run_returns_counts_only(client: AsyncClient):
    response = await client.post(
        URL, params={"dry_run": "true"}, headers={"X-Internal-Token": "scheduled-job-token"}
    )

    assert response.status_code == 200
    assert response.json() == {
        "dry_run": True,
        "stages": {
            stage: {"due": 0, "sent": 0, "failed": 0, "already_sent": 0}
            for stage in ("before_expiry", "after_expiry")
        },
    }


@pytest.mark.integration
async def test_history_leaves_out_reminder_records(client: AsyncClient, db_session: Session):
    user = User(
        username="history_author",
        email="history_author@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
    )
    db_session.add(user)
    db_session.commit()
    for action in ("created", RENEWAL_REMINDER_ACTION):
        db_session.add(
            SubscriptionHistory(
                user_id=user.id,
                action=action,
                plan_name="pro",
                start_date=datetime(2026, 10, 8),
                end_date=datetime(2026, 11, 7),
            )
        )
    db_session.commit()
    login = await client.post(
        "/api/auth/login", data={"username": user.username, "password": "password123"}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    response = await client.get("/api/v1/subscription/history", headers=headers)

    assert [item["action"] for item in response.json()] == ["created"]
