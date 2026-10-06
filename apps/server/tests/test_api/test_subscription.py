"""
Tests for Subscription API endpoints.

Integration tests for the subscription system API, covering:
- GET /api/v1/subscription/me - Get current user's subscription status
- GET /api/v1/subscription/quota - Get usage quota
- POST /api/v1/subscription/redeem - Redeem subscription code
- GET /api/v1/subscription/history - Get subscription history
- POST /api/v1/subscription/upgrade-funnel-events - Track upgrade funnel events
"""
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlmodel import Session, select

from models import UpgradeFunnelEvent, User
from models.subscription import (
    RedemptionCode,
    SubscriptionHistory,
    SubscriptionPlan,
    UsageQuota,
    UserSubscription,
)
from services.core.auth_service import hash_password


@pytest.fixture(autouse=True)
def redemption_hmac_secret(monkeypatch: pytest.MonkeyPatch):
    """Ensure redemption checksum secret is available during tests."""
    monkeypatch.setenv(
        "REDEMPTION_CODE_HMAC_SECRET",
        "test-secret-key-must-be-at-least-32-characters-long",
    )


@pytest.fixture
async def auth_headers(client: AsyncClient, db_session: Session):
    """Create a verified user and return auth headers."""
    user = User(
        email="test@example.com",
        username="testuser",
        hashed_password=hash_password("testpassword123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    # Login to get token
    response = await client.post(
        "/api/auth/login",
        data={
            "username": "testuser",
            "password": "testpassword123",
        }
    )

    assert response.status_code == 200
    data = response.json()
    return {"Authorization": f"Bearer {data['access_token']}"}


@pytest.fixture
def free_plan(db_session: Session):
    """Create a free subscription plan."""
    plan = SubscriptionPlan(
        name="free",
        display_name="Free",
        display_name_en="Free",
        price_monthly_cents=0,
        price_yearly_cents=0,
        features={
            "ai_conversations_per_day": 20,
            "max_projects": 3,
            "materials_library_access": False,
            "material_uploads": 0,
            "material_decompositions": 0,
            "custom_skills": 3,
            "inspiration_copies_monthly": 10,
        },
        is_active=True,
    )
    db_session.add(plan)
    db_session.commit()
    db_session.refresh(plan)
    return plan


@pytest.fixture
def pro_plan(db_session: Session):
    """Create a pro subscription plan."""
    plan = SubscriptionPlan(
        name="pro",
        display_name="Pro",
        display_name_en="Pro",
        price_monthly_cents=2900,
        price_yearly_cents=29000,
        features={
            "ai_conversations_per_day": -1,  # unlimited
            "max_projects": -1,
            "materials_library_access": True,
            "material_uploads": 5,
            "material_decompositions": 5,
            "custom_skills": -1,
            "inspiration_copies_monthly": -1,
        },
        is_active=True,
    )
    db_session.add(plan)
    db_session.commit()
    db_session.refresh(plan)
    return plan


@pytest.fixture
def test_user(db_session: Session):
    """Create a test user for subscription testing."""
    user = User(
        email="subuser@example.com",
        username="subuser",
        hashed_password=hash_password("password123"),
        name="Subscription User",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def test_user_with_quota(db_session: Session, test_user):
    """Create a test user with usage quota."""
    now = datetime.utcnow()
    quota = UsageQuota(
        user_id=test_user.id,
        period_start=now,
        period_end=now + timedelta(days=30),
        ai_conversations_used=5,
        last_reset_at=now,
    )
    db_session.add(quota)
    db_session.commit()
    db_session.refresh(quota)
    return test_user


@pytest.mark.integration
class TestGetSubscriptionStatus:
    """Tests for GET /api/v1/subscription/me endpoint."""

    async def test_get_subscription_status_unauthorized(self, client: AsyncClient):
        """Test that unauthenticated requests are rejected."""
        response = await client.get("/api/v1/subscription/me")
        assert response.status_code == 401

    async def test_get_subscription_status_free_plan(
        self, client: AsyncClient, auth_headers, free_plan
    ):
        """Test getting subscription status for free plan user."""
        response = await client.get(
            "/api/v1/subscription/me",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()

        assert data["tier"] == "free"
        assert data["status"] == "none"
        assert data["display_name"] == "Free"
        assert data["days_remaining"] is None
        assert "features" in data

    async def test_get_subscription_status_pro_plan(
        self, client: AsyncClient, db_session: Session, free_plan, pro_plan
    ):
        """Test getting subscription status for pro plan user."""
        # Create user with pro subscription
        user = User(
            email="prouser@example.com",
            username="prouser",
            hashed_password=hash_password("password123"),
            email_verified=True,
            is_active=True,
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)

        now = datetime.utcnow()
        subscription = UserSubscription(
            user_id=user.id,
            plan_id=pro_plan.id,
            status="active",
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
        )
        db_session.add(subscription)
        db_session.commit()

        # Login to get token
        response = await client.post(
            "/api/auth/login",
            data={"username": "prouser", "password": "password123"},
        )
        assert response.status_code == 200
        token = response.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Get subscription status
        response = await client.get(
            "/api/v1/subscription/me",
            headers=headers,
        )

        assert response.status_code == 200
        data = response.json()

        assert data["tier"] == "pro"
        assert data["status"] == "active"
        assert data["display_name"] == "Pro"
        assert data["days_remaining"] is not None
        assert data["days_remaining"] > 0
        assert data["current_period_end"] is not None

    async def test_get_subscription_status_expired(
        self, client: AsyncClient, db_session: Session, free_plan, pro_plan
    ):
        """Test getting subscription status for expired subscription."""
        # Create user with expired pro subscription
        user = User(
            email="expireduser@example.com",
            username="expireduser",
            hashed_password=hash_password("password123"),
            email_verified=True,
            is_active=True,
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)

        now = datetime.utcnow()
        subscription = UserSubscription(
            user_id=user.id,
            plan_id=pro_plan.id,
            status="active",
            current_period_start=now - timedelta(days=60),
            current_period_end=now - timedelta(days=30),  # Expired 30 days ago
        )
        db_session.add(subscription)
        db_session.commit()

        # Login to get token
        response = await client.post(
            "/api/auth/login",
            data={"username": "expireduser", "password": "password123"},
        )
        assert response.status_code == 200
        token = response.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Get subscription status
        response = await client.get(
            "/api/v1/subscription/me",
            headers=headers,
        )

        assert response.status_code == 200
        data = response.json()
        # Expired subscriptions should show free plan
        assert data["tier"] == "free"
        # ...as the plan the user is on now, not "Free" paired with "expired"
        assert data["status"] == "active"
        assert data["current_period_end"] is None
        assert data["days_remaining"] is None

        # Same once the lapsed row has been marked expired.
        db_session.refresh(subscription)
        assert subscription.status == "expired"
        response = await client.get("/api/v1/subscription/me", headers=headers)
        data = response.json()
        assert (data["tier"], data["status"]) == ("free", "active")


@pytest.mark.integration
class TestGetQuota:
    """Tests for GET /api/v1/subscription/quota endpoint."""

    async def test_get_quota_unauthorized(self, client: AsyncClient):
        """Test that unauthenticated requests are rejected."""
        response = await client.get("/api/v1/subscription/quota")
        assert response.status_code == 401

    async def test_get_quota_success(
        self, client: AsyncClient, auth_headers, free_plan, db_session: Session
    ):
        """Test getting quota information."""
        response = await client.get(
            "/api/v1/subscription/quota",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()

        assert "ai_conversations" in data
        assert "projects" in data
        assert "used" in data["ai_conversations"]
        assert "limit" in data["ai_conversations"]
        assert "reset_at" in data["ai_conversations"]
        assert "used" in data["projects"]
        assert "limit" in data["projects"]

    @pytest.mark.parametrize(
        ("now", "last_reset", "expected_daily_used", "expected_monthly_used", "daily_end", "monthly_end"),
        [
            (datetime(2026, 10, 5, 16), datetime(2026, 10, 5, 15, 59), 0, 4, datetime(2026, 10, 6, 16), datetime(2026, 10, 31, 16)),
            (datetime(2026, 10, 31, 16), datetime(2026, 10, 31, 16), 7, 0, datetime(2026, 11, 1, 16), datetime(2026, 11, 30, 16)),
        ],
    )
    async def test_quota_api_returns_beijing_reset_boundaries_with_timezone(
        self, client, auth_headers, free_plan, db_session, monkeypatch,
        now, last_reset, expected_daily_used, expected_monthly_used, daily_end, monthly_end
    ):
        monkeypatch.setattr("services.quota_service.utcnow", lambda: now.replace(tzinfo=UTC))
        user = db_session.exec(select(User).where(User.username == "testuser")).one()
        quota = UsageQuota(
            user_id=user.id,
            period_start=last_reset,
            period_end=last_reset + timedelta(hours=24),
            last_reset_at=last_reset,
            ai_conversations_used=7,
            material_decompositions_used=4,
            monthly_period_start=datetime(2026, 10, 1),
            monthly_period_end=datetime(2026, 11, 1),
        )
        db_session.add(quota)
        db_session.commit()

        response = await client.get("/api/v1/subscription/quota", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["ai_conversations"]["used"] == expected_daily_used
        assert data["material_decompositions"]["used"] == expected_monthly_used
        assert datetime.fromisoformat(data["ai_conversations"]["reset_at"]) == daily_end.replace(tzinfo=UTC)
        assert datetime.fromisoformat(data["material_decompositions"]["reset_at"]) == monthly_end.replace(tzinfo=UTC)

    async def test_get_quota_free_plan_limits(
        self, client: AsyncClient, auth_headers, free_plan
    ):
        """Test that free plan quota limits are correct."""
        response = await client.get(
            "/api/v1/subscription/quota",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()

        # Free plan should have 20 AI conversations per day
        assert data["ai_conversations"]["limit"] == 20
        # Free plan should have max 3 projects
        assert data["projects"]["limit"] == 3
        assert data["material_decompositions"]["limit"] == 0

    async def test_get_quota_paid_material_decompose_limit(
        self, client: AsyncClient, db_session: Session, free_plan, pro_plan
    ):
        """Test that paid plan exposes the configured material decompose limit."""
        # Create user with pro subscription
        user = User(
            email="proupser2@example.com",
            username="proupser2",
            hashed_password=hash_password("password123"),
            email_verified=True,
            is_active=True,
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)

        now = datetime.utcnow()
        subscription = UserSubscription(
            user_id=user.id,
            plan_id=pro_plan.id,
            status="active",
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
        )
        db_session.add(subscription)

        quota = UsageQuota(
            user_id=user.id,
            period_start=now,
            period_end=now + timedelta(days=30),
            ai_conversations_used=100,
            last_reset_at=now,
        )
        db_session.add(quota)
        db_session.commit()

        # Login to get token
        response = await client.post(
            "/api/auth/login",
            data={"username": "proupser2", "password": "password123"},
        )
        assert response.status_code == 200
        token = response.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Get quota
        response = await client.get(
            "/api/v1/subscription/quota",
            headers=headers,
        )

        assert response.status_code == 200
        data = response.json()

        # Pro plan should keep unlimited AI/project quotas, but materials use the configured limit.
        assert data["ai_conversations"]["limit"] == -1
        assert data["projects"]["limit"] == -1
        assert data["material_decompositions"]["limit"] == 5


@pytest.mark.integration
class TestRedeemCode:
    """Tests for POST /api/v1/subscription/redeem endpoint."""

    async def test_redeem_code_unauthorized(self, client: AsyncClient):
        """Test that unauthenticated requests are rejected."""
        response = await client.post(
            "/api/v1/subscription/redeem",
            json={"code": "ERG-PRO7M-XXXX-12345678"},
        )
        assert response.status_code == 401

    async def test_redeem_code_invalid_format(
        self, client: AsyncClient, auth_headers, free_plan
    ):
        """Test redeeming code with invalid format."""
        response = await client.post(
            "/api/v1/subscription/redeem",
            headers=auth_headers,
            json={"code": "INVALID-CODE"},
        )

        # Should fail validation (422 for Pydantic validation error)
        assert response.status_code in [400, 422]

    async def test_redeem_code_rejects_invalid_source(
        self, client: AsyncClient, auth_headers, free_plan
    ):
        """source should be alphanumeric-style token for attribution safety."""
        response = await client.post(
            "/api/v1/subscription/redeem",
            headers=auth_headers,
            json={"code": "ERG-PRO7M-ABCD-12345678", "source": "bad source"},
        )
        assert response.status_code == 422

    async def test_redeem_code_passes_source_to_redemption_service(
        self, client: AsyncClient, auth_headers, free_plan, monkeypatch: pytest.MonkeyPatch
    ):
        """Valid source should be forwarded so upgrade attribution can be persisted."""
        captured: dict[str, str | None] = {"source": None}

        def _fake_redeem_code(
            _session: Session,
            _code: str,
            _user_id: str,
            *,
            attribution_source: str | None = None,
        ):
            captured["source"] = attribution_source
            return (
                True,
                "ok",
                {"tier": "pro", "duration_days": 30, "subscription_id": "sub_test"},
            )

        monkeypatch.setattr(
            "api.subscription.redemption_service.redeem_code",
            _fake_redeem_code,
        )

        response = await client.post(
            "/api/v1/subscription/redeem",
            headers=auth_headers,
            json={"code": "ERG-PRO7M-ABCD-12345678", "source": "chat_quota_blocked"},
        )

        assert response.status_code == 200
        assert captured["source"] == "chat_quota_blocked"
        assert response.json()["success"] is True

    async def test_redeem_code_not_found(
        self, client: AsyncClient, auth_headers, free_plan, db_session: Session
    ):
        """Test redeeming a code that doesn't exist in database."""
        # We need to mock or create a code with valid format but not in DB
        # The code format validation happens first, then DB lookup
        # For this test, we'll create a code with valid format

        response = await client.post(
            "/api/v1/subscription/redeem",
            headers=auth_headers,
            json={"code": "ERG-PRO7M-ABCD-12345678"},  # Valid format but not in DB
        )

        # Should fail - code not found or checksum failed
        assert response.status_code == 400

    async def test_redeem_code_disabled(
        self, client: AsyncClient, auth_headers, free_plan, pro_plan, db_session: Session
    ):
        """Test redeeming a disabled code."""
        # Get user
        from sqlalchemy import text as sql_text
        user_result = db_session.exec(
            sql_text("SELECT id FROM user WHERE email = 'test@example.com'")
        ).first()
        user_id = user_result[0] if user_result else None

        # Create a disabled redemption code
        code = RedemptionCode(
            code="ERG-PRO7M-ABCD-12345678",
            code_type="single_use",
            tier="pro",
            duration_days=30,
            max_uses=1,
            current_uses=0,
            created_by=user_id,
            is_active=False,
        )
        db_session.add(code)
        db_session.commit()

        # Try to redeem (will fail at format or checksum validation first)
        response = await client.post(
            "/api/v1/subscription/redeem",
            headers=auth_headers,
            json={"code": "ERG-PRO7M-ABCD-12345678"},
        )

        # Should fail
        assert response.status_code == 400


@pytest.mark.integration
class TestGetHistory:
    """Tests for GET /api/v1/subscription/history endpoint."""

    async def test_get_history_unauthorized(self, client: AsyncClient):
        """Test that unauthenticated requests are rejected."""
        response = await client.get("/api/v1/subscription/history")
        assert response.status_code == 401

    @pytest.mark.parametrize("limit", [0, -1, 101, 1000])
    async def test_get_history_rejects_invalid_limit(
        self, client: AsyncClient, auth_headers, limit: int
    ):
        response = await client.get(
            f"/api/v1/subscription/history?limit={limit}",
            headers=auth_headers,
        )

        assert response.status_code == 422

    async def test_get_history_empty(
        self, client: AsyncClient, auth_headers, free_plan
    ):
        """Test getting history when user has none."""
        response = await client.get(
            "/api/v1/subscription/history",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data == []

    async def test_get_history_with_data(
        self, client: AsyncClient, auth_headers, free_plan, pro_plan, db_session: Session
    ):
        """Test getting subscription history with data."""
        # Get user
        from sqlalchemy import text as sql_text
        user_result = db_session.exec(
            sql_text("SELECT id FROM user WHERE email = 'test@example.com'")
        ).first()
        user_id = user_result[0] if user_result else None

        # Create some history entries
        now = datetime.utcnow()
        history1 = SubscriptionHistory(
            user_id=user_id,
            action="created",
            plan_name="free",
            start_date=now - timedelta(days=60),
            end_date=None,
        )
        history2 = SubscriptionHistory(
            user_id=user_id,
            action="upgraded",
            plan_name="pro",
            start_date=now - timedelta(days=30),
            end_date=now + timedelta(days=30),
        )
        db_session.add(history1)
        db_session.add(history2)
        db_session.commit()

        response = await client.get(
            "/api/v1/subscription/history",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2

        # Check response format
        for item in data:
            assert "id" in item
            assert "action" in item
            assert "plan_name" in item
            assert "start_date" in item
            assert "created_at" in item

        # Should be ordered by created_at desc
        assert data[0]["action"] == "upgraded"
        assert data[1]["action"] == "created"

    async def test_get_history_limit(
        self, client: AsyncClient, auth_headers, free_plan, db_session: Session
    ):
        """Test that history respects limit parameter."""
        # Get user
        from sqlalchemy import text as sql_text
        user_result = db_session.exec(
            sql_text("SELECT id FROM user WHERE email = 'test@example.com'")
        ).first()
        user_id = user_result[0] if user_result else None

        # Create many history entries
        now = datetime.utcnow()
        for i in range(60):
            history = SubscriptionHistory(
                user_id=user_id,
                action="renewed",
                plan_name="pro",
                start_date=now - timedelta(days=i),
                end_date=now + timedelta(days=30-i),
            )
            db_session.add(history)
        db_session.commit()

        # Test default limit (50)
        response = await client.get(
            "/api/v1/subscription/history",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 50

        # Test custom limit
        response = await client.get(
            "/api/v1/subscription/history?limit=10",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 10

    async def test_get_history_only_own_records(
        self, client: AsyncClient, auth_headers, free_plan, db_session: Session
    ):
        """Test that users can only see their own history."""
        # Create another user
        other_user = User(
            email="other@example.com",
            username="otheruser",
            hashed_password=hash_password("password123"),
            email_verified=True,
            is_active=True,
        )
        db_session.add(other_user)
        db_session.commit()
        db_session.refresh(other_user)

        # Create history for other user
        now = datetime.utcnow()
        history = SubscriptionHistory(
            user_id=other_user.id,
            action="created",
            plan_name="pro",
            start_date=now,
            end_date=now + timedelta(days=30),
        )
        db_session.add(history)
        db_session.commit()

        # Get history - should be empty for current user
        response = await client.get(
            "/api/v1/subscription/history",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        # Should not include other user's history
        for item in data:
            assert item["id"] != history.id


@pytest.mark.integration
class TestTrackUpgradeFunnelEvent:
    """Tests for POST /api/v1/subscription/upgrade-funnel-events endpoint."""

    async def test_track_upgrade_funnel_event_unauthorized(self, client: AsyncClient):
        response = await client.post(
            "/api/v1/subscription/upgrade-funnel-events",
            json={
                "action": "expose",
                "source": "chat_quota_blocked",
                "surface": "modal",
            },
        )
        assert response.status_code == 401

    async def test_track_upgrade_funnel_event_success(
        self, client: AsyncClient, auth_headers, free_plan, db_session: Session
    ):
        from uuid import uuid4

        # Use a unique source to avoid cross-test collisions under xdist parallelism.
        source = f"chat_quota_blocked_{uuid4().hex}"

        response = await client.post(
            "/api/v1/subscription/upgrade-funnel-events",
            headers=auth_headers,
            json={
                "action": "click",
                "source": source,
                "surface": "modal",
                "cta": "primary",
                "destination": "billing",
                "meta": {"entry": "chat", "attempt": 1},
                "occurred_at": "2026-03-08T00:00:00Z",
            },
        )

        assert response.status_code == 201
        assert response.json() == {"success": True}

        events = db_session.exec(
            select(UpgradeFunnelEvent).where(UpgradeFunnelEvent.source == source)
        ).all()
        assert len(events) == 1
        event = events[0]
        assert event.action == "click"
        assert event.event_name == "upgrade_entry_click"
        assert event.surface == "modal"
        assert event.cta == "primary"
        assert event.destination == "billing"
        assert event.event_metadata.get("entry") == "chat"

    async def test_track_upgrade_funnel_event_rejects_invalid_source(
        self, client: AsyncClient, auth_headers, free_plan
    ):
        response = await client.post(
            "/api/v1/subscription/upgrade-funnel-events",
            headers=auth_headers,
            json={
                "action": "expose",
                "source": "invalid source with spaces",
                "surface": "modal",
            },
        )

        assert response.status_code == 422

    async def test_track_upgrade_funnel_event_rate_limited(
        self, client: AsyncClient, auth_headers, free_plan, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(
            "api.subscription.check_rate_limit",
            lambda *_args, **_kwargs: (False, {}),
        )

        response = await client.post(
            "/api/v1/subscription/upgrade-funnel-events",
            headers=auth_headers,
            json={
                "action": "expose",
                "source": "chat_quota_blocked",
                "surface": "modal",
            },
        )

        assert response.status_code == 429
        assert response.json()["detail"] == "Rate limit exceeded"
