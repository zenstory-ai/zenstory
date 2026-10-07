"""
Additional tests for subscription plans/catalog and redeem rate-limit behavior.
"""

from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlmodel import Session

from models import User
from models.entities import Project
from models.subscription import SubscriptionPlan, UserSubscription
from services.core.auth_service import hash_password


@pytest.fixture(autouse=True)
def redemption_hmac_secret(monkeypatch: pytest.MonkeyPatch):
    """Ensure redemption checksum secret is available during tests."""
    monkeypatch.setenv(
        "REDEMPTION_CODE_HMAC_SECRET",
        "test-secret-key-must-be-at-least-32-characters-long",
    )


async def create_user(
    db_session: Session,
    username: str,
    email: str,
    password: str = "password123",
) -> User:
    user = User(
        username=username,
        email=email,
        hashed_password=hash_password(password),
        email_verified=True,
        is_active=True,
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
async def test_subscription_plans_returns_default_free_when_no_active_plans(
    client: AsyncClient,
    db_session: Session,
):
    user = await create_user(db_session, "sub_plan_user_1", "sub_plan_user_1@example.com")
    token = await login_user(client, user.username)

    response = await client.get("/api/v1/subscription/plans", headers=auth_headers(token))

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["id"] == "default-free-plan"
    assert payload[0]["name"] == "free"
    assert payload[0]["price_monthly_cents"] == 0
    assert payload[0]["is_active"] is True


@pytest.mark.integration
async def test_subscription_plans_returns_active_sorted_and_excludes_inactive(
    client: AsyncClient,
    db_session: Session,
):
    user = await create_user(db_session, "sub_plan_user_2", "sub_plan_user_2@example.com")

    db_session.add(
        SubscriptionPlan(
            name="pro",
            display_name="Pro",
            display_name_en="Pro",
            price_monthly_cents=2900,
            price_yearly_cents=29000,
            features={"ai_conversations_per_day": 80},
            is_active=True,
        )
    )
    db_session.add(
        SubscriptionPlan(
            name="free",
            display_name="Free",
            display_name_en="Free",
            price_monthly_cents=0,
            price_yearly_cents=0,
            features={"ai_conversations_per_day": 20},  # Legacy stored value.
            is_active=True,
        )
    )
    db_session.commit()

    token = await login_user(client, user.username)
    response = await client.get("/api/v1/subscription/plans", headers=auth_headers(token))

    assert response.status_code == 200
    payload = response.json()
    assert [item["name"] for item in payload] == ["free", "pro"]
    assert payload[0]["features"]["ai_conversations_per_day"] == 10


@pytest.mark.integration
async def test_subscription_catalog_filters_unknown_active_plan_names(
    client: AsyncClient,
    db_session: Session,
):
    user = await create_user(db_session, "sub_plan_user_unknown", "sub_plan_user_unknown@example.com")

    db_session.add(
        SubscriptionPlan(
            name="free",
            display_name="Free",
            display_name_en="Free",
            price_monthly_cents=0,
            price_yearly_cents=0,
            features={"ai_conversations_per_day": 10},
            is_active=True,
        )
    )
    db_session.add(
        SubscriptionPlan(
            name="pro",
            display_name="Pro",
            display_name_en="Pro",
            price_monthly_cents=2900,
            price_yearly_cents=29000,
            features={"ai_conversations_per_day": 80},
            is_active=True,
        )
    )
    db_session.add(
        SubscriptionPlan(
            name="max",
            display_name="Max",
            display_name_en="Max",
            price_monthly_cents=4900,
            price_yearly_cents=49000,
            features={"ai_conversations_per_day": -1},
            is_active=True,
        )
    )
    db_session.commit()

    token = await login_user(client, user.username)
    response = await client.get("/api/v1/subscription/catalog", headers=auth_headers(token))

    assert response.status_code == 200
    payload = response.json()
    assert [tier["name"] for tier in payload["tiers"]] == ["free", "pro"]


@pytest.mark.integration
async def test_subscription_plans_filters_unsupported_export_formats(
    client: AsyncClient,
    db_session: Session,
):
    user = await create_user(db_session, "sub_plan_user_2b", "sub_plan_user_2b@example.com")

    db_session.add(
        SubscriptionPlan(
            name="free",
            display_name="Free",
            display_name_en="Free",
            price_monthly_cents=0,
            price_yearly_cents=0,
            features={"ai_conversations_per_day": 10, "export_formats": ["txt", "md", "pdf"]},
            is_active=True,
        )
    )
    db_session.commit()

    token = await login_user(client, user.username)
    response = await client.get("/api/v1/subscription/plans", headers=auth_headers(token))

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["features"]["export_formats"] == ["txt"]


@pytest.mark.integration
async def test_subscription_catalog_returns_default_free_when_no_active_plans(
    client: AsyncClient,
    db_session: Session,
):
    user = await create_user(db_session, "sub_plan_user_3", "sub_plan_user_3@example.com")
    token = await login_user(client, user.username)

    response = await client.get("/api/v1/subscription/catalog", headers=auth_headers(token))

    assert response.status_code == 200
    payload = response.json()
    assert payload["version"] == "2026-03"
    assert payload["comparison_mode"] == "task_outcome"
    assert payload["pricing_anchor_monthly_cents"] == 4900
    assert len(payload["tiers"]) == 1

    free_tier = payload["tiers"][0]
    assert free_tier["id"] == "default-free-plan"
    assert free_tier["name"] == "free"
    assert free_tier["recommended"] is False
    assert free_tier["summary_key"] == "starter"
    assert free_tier["target_user_key"] == "explorer"


@pytest.mark.integration
async def test_subscription_catalog_normalizes_feature_based_entitlements(
    client: AsyncClient,
    db_session: Session,
):
    user = await create_user(db_session, "sub_plan_user_4", "sub_plan_user_4@example.com")

    db_session.add(
        SubscriptionPlan(
            name="pro",
            display_name="Pro",
            display_name_en="Pro",
            price_monthly_cents=2900,
            price_yearly_cents=29000,
            features={
                "ai_conversations_per_day": 15,
                "max_projects": 6,
                "active_projects_limit": 999,
                "context_window_tokens": "8192",
                "material_uploads": 12,
                "material_uploads_monthly": 999,
                "material_decompositions": 8,
                "material_decompositions_monthly": 999,
                "custom_skills": 9,
                "custom_skills_limit": 999,
                "inspiration_copies_monthly": "33",
                "writing_credits_monthly": 999,
                "agent_runs_monthly": 999,
                "priority_support": True,
                "export_formats": "docx",  # invalid type should be normalized to []
            },
            is_active=True,
        )
    )
    db_session.commit()

    token = await login_user(client, user.username)
    response = await client.get("/api/v1/subscription/catalog", headers=auth_headers(token))

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["tiers"]) == 1

    tier = payload["tiers"][0]
    assert tier["name"] == "pro"
    assert tier["recommended"] is True
    assert tier["summary_key"] == "creator"
    assert tier["target_user_key"] == "daily_writer"

    entitlements = tier["entitlements"]
    assert entitlements["ai_conversations_per_day"] == 15
    # Legacy catalog-only fields are kept neutral for cached clients, but are
    # neither derived from daily usage nor advertised as real entitlements.
    assert entitlements["writing_credits_monthly"] == 0
    assert entitlements["agent_runs_monthly"] == 0
    assert entitlements["active_projects_limit"] == 6
    # Deprecated neutral values: stored context/priority features are not advertised.
    assert entitlements["context_tokens_limit"] == 0
    assert entitlements["material_uploads_monthly"] == 12
    assert entitlements["material_decompositions_monthly"] == 8
    assert entitlements["custom_skills_limit"] == 9
    assert entitlements["inspiration_copies_monthly"] == 33
    assert entitlements["priority_queue_level"] == "standard"
    assert entitlements["export_formats"] == []


@pytest.mark.integration
async def test_subscription_catalog_filters_unsupported_export_formats(
    client: AsyncClient,
    db_session: Session,
):
    user = await create_user(db_session, "sub_plan_user_4b", "sub_plan_user_4b@example.com")

    db_session.add(
        SubscriptionPlan(
            name="pro",
            display_name="Pro",
            display_name_en="Pro",
            price_monthly_cents=2900,
            price_yearly_cents=29000,
            features={
                "export_formats": ["txt", "md", "pdf"],
            },
            is_active=True,
        )
    )
    db_session.commit()

    token = await login_user(client, user.username)
    response = await client.get("/api/v1/subscription/catalog", headers=auth_headers(token))

    assert response.status_code == 200
    entitlements = response.json()["tiers"][0]["entitlements"]
    assert entitlements["export_formats"] == ["txt"]


@pytest.mark.integration
async def test_subscription_catalog_ignores_unenforced_monthly_output_aliases(
    client: AsyncClient,
    db_session: Session,
):
    user = await create_user(db_session, "sub_plan_user_5", "sub_plan_user_5@example.com")

    db_session.add(
        SubscriptionPlan(
            name="pro",
            display_name="Pro",
            display_name_en="Pro",
            price_monthly_cents=2900,
            price_yearly_cents=29000,
            features={
                "ai_conversations_per_day": 10,
                "writing_credits_monthly": 999,
            },
            is_active=True,
        )
    )
    db_session.commit()

    token = await login_user(client, user.username)
    response = await client.get("/api/v1/subscription/catalog", headers=auth_headers(token))

    assert response.status_code == 200
    tier = response.json()["tiers"][0]
    entitlements = tier["entitlements"]

    assert entitlements["ai_conversations_per_day"] == 10
    assert entitlements["writing_credits_monthly"] == 0
    assert entitlements["agent_runs_monthly"] == 0


@pytest.mark.integration
async def test_subscription_redeem_rate_limited_returns_429(
    client: AsyncClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    user = await create_user(db_session, "sub_plan_user_6", "sub_plan_user_6@example.com")
    token = await login_user(client, user.username)

    monkeypatch.setattr("api.subscription.check_rate_limit", lambda *_args, **_kwargs: (False, 0))

    response = await client.post(
        "/api/v1/subscription/redeem",
        headers=auth_headers(token),
        json={"code": "ERG-PRO7M-ABCD-12345678"},
    )

    assert response.status_code == 429
    assert response.json()["detail"] == "Rate limit exceeded"


@pytest.mark.integration
async def test_subscription_me_returns_default_free_when_no_plan_available(
    client: AsyncClient,
    db_session: Session,
):
    user = await create_user(db_session, "sub_me_user_1", "sub_me_user_1@example.com")
    token = await login_user(client, user.username)

    response = await client.get("/api/v1/subscription/me", headers=auth_headers(token))

    assert response.status_code == 200
    payload = response.json()
    assert payload["tier"] == "free"
    assert payload["display_name"] == "免费试用"
    assert payload["display_name_en"] == "Free Trial"
    assert payload["status"] == "none"
    assert payload["features"]["max_projects"] == 3
    assert payload["features"]["ai_conversations_per_day"] == 10
    assert payload["features"]["export_formats"] == ["txt"]


@pytest.mark.integration
async def test_subscription_me_filters_unsupported_export_formats(
    client: AsyncClient,
    db_session: Session,
):
    user = await create_user(db_session, "sub_me_user_2", "sub_me_user_2@example.com")

    plan = SubscriptionPlan(
        name="pro",
        display_name="Pro",
        display_name_en="Pro",
        price_monthly_cents=4900,
        price_yearly_cents=39900,
        features={"export_formats": ["txt", "md", "pdf", 1]},
        is_active=True,
    )
    db_session.add(plan)
    db_session.commit()
    db_session.refresh(plan)

    now = datetime.utcnow()
    subscription = UserSubscription(
        user_id=user.id,
        plan_id=plan.id,
        status="active",
        current_period_start=now - timedelta(days=1),
        current_period_end=now + timedelta(days=30),
        cancel_at_period_end=False,
    )
    db_session.add(subscription)
    db_session.commit()

    token = await login_user(client, user.username)
    response = await client.get("/api/v1/subscription/me", headers=auth_headers(token))

    assert response.status_code == 200
    payload = response.json()
    assert payload["features"]["export_formats"] == ["txt"]


@pytest.mark.integration
async def test_subscription_quota_fallback_uses_default_project_limit_and_non_deleted_count(
    client: AsyncClient,
    db_session: Session,
):
    user = await create_user(db_session, "sub_quota_user_1", "sub_quota_user_1@example.com")

    db_session.add(
        Project(
            name="Active Project",
            owner_id=user.id,
            is_deleted=False,
        )
    )
    db_session.add(
        Project(
            name="Deleted Project",
            owner_id=user.id,
            is_deleted=True,
        )
    )
    db_session.commit()

    token = await login_user(client, user.username)
    response = await client.get("/api/v1/subscription/quota", headers=auth_headers(token))

    assert response.status_code == 200
    payload = response.json()
    assert payload["projects"]["used"] == 1
    assert payload["projects"]["limit"] == 3
    assert payload["ai_conversations"]["limit"] == 10


@pytest.mark.integration
async def test_subscription_redeem_success_maps_service_result(
    client: AsyncClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    user = await create_user(db_session, "sub_redeem_user_1", "sub_redeem_user_1@example.com")
    token = await login_user(client, user.username)

    monkeypatch.setattr("api.subscription.check_rate_limit", lambda *_args, **_kwargs: (True, 9))
    monkeypatch.setattr(
        "api.subscription.redemption_service.redeem_code",
        lambda *_args, **_kwargs: (True, "Redeemed successfully", {"tier": "pro", "duration_days": 30}),
    )

    response = await client.post(
        "/api/v1/subscription/redeem",
        headers=auth_headers(token),
        json={"code": "ERG-PRO7M-ABCD-12345678"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "success": True,
        "message": "Redeemed successfully",
        "tier": "pro",
        "duration_days": 30,
    }
