"""
Tests for Points API endpoints.

Integration tests for the points and check-in system API, covering:
- GET /api/v1/points/balance - Get points balance
- POST /api/v1/points/check-in - Daily check-in
- GET /api/v1/points/check-in/status - Check-in status
- GET /api/v1/points/transactions - Transaction history
- POST /api/v1/points/redeem - Redeem for Pro
- GET /api/v1/points/earn-opportunities - Earn opportunities
- GET /api/v1/points/config - Points configuration
"""
from datetime import UTC, date, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlmodel import Session, select

from config.datetime_utils import beijing_date, utcnow
from models import SubscriptionPlan, User
from models.points import CheckInRecord
from services.core.auth_service import hash_password
from services.features.points_service import (
    POINTS_CHECK_IN,
    POINTS_CHECK_IN_STREAK,
    POINTS_EXPIRATION_MONTHS,
    POINTS_PRO_7DAYS_COST,
    POINTS_REFERRAL,
    STREAK_BONUS_THRESHOLD,
    points_service,
)


def beijing_check_in_timestamp(day: date) -> datetime:
    """Return an early-Beijing-day UTC timestamp for a current-format row."""
    return datetime.combine(day - timedelta(days=1), datetime.min.time(), tzinfo=UTC).replace(
        hour=17
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
async def user_with_points(db_session: Session):
    """Create a user with some points for testing."""
    user = User(
        email="points@example.com",
        username="pointsuser",
        hashed_password=hash_password("testpassword123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    # Add some points to user
    points_service.earn_points(
        session=db_session,
        user_id=user.id,
        amount=200,
        transaction_type="test_setup",
        description="Test setup points",
    )

    return user


@pytest.fixture
def pro_plan(db_session: Session):
    """Ensure Pro subscription plan exists for redemption tests."""
    existing = db_session.exec(
        select(SubscriptionPlan).where(SubscriptionPlan.name == "pro")
    ).first()
    if existing:
        return existing

    plan = SubscriptionPlan(
        name="pro",
        display_name="Pro",
        display_name_en="Pro",
        price_monthly_cents=1999,
        price_yearly_cents=19900,
        features={"ai_conversations_per_day": 9999},
        is_active=True,
    )
    db_session.add(plan)
    db_session.commit()
    db_session.refresh(plan)
    return plan


@pytest.fixture
async def auth_headers_with_points(client: AsyncClient, db_session: Session, user_with_points):
    """Create auth headers for a user with points."""
    response = await client.post(
        "/api/auth/login",
        data={
            "username": "pointsuser",
            "password": "testpassword123",
        }
    )

    assert response.status_code == 200
    data = response.json()
    return {"Authorization": f"Bearer {data['access_token']}"}


@pytest.mark.integration
class TestGetBalance:
    """Tests for GET /api/v1/points/balance endpoint."""

    async def test_get_balance_unauthorized(self, client: AsyncClient):
        """Test that unauthenticated requests are rejected."""
        response = await client.get("/api/v1/points/balance")
        assert response.status_code == 401

    async def test_get_balance_empty(self, client: AsyncClient, auth_headers):
        """Test getting balance when user has no points."""
        response = await client.get(
            "/api/v1/points/balance",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["available"] == 0
        assert data["pending_expiration"] == 0
        assert data["nearest_expiration_date"] is None

    async def test_get_balance_with_points(self, client: AsyncClient, auth_headers_with_points):
        """Test getting balance when user has points."""
        response = await client.get(
            "/api/v1/points/balance",
            headers=auth_headers_with_points,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["available"] == 200
        expected_pending = 200 if POINTS_EXPIRATION_MONTHS <= 1 else 0
        assert data["pending_expiration"] == expected_pending
        assert data["nearest_expiration_date"] is not None


@pytest.mark.integration
class TestCheckIn:
    """Tests for POST /api/v1/points/check-in endpoint."""

    async def test_check_in_unauthorized(self, client: AsyncClient):
        """Test that unauthenticated requests are rejected."""
        response = await client.post("/api/v1/points/check-in")
        assert response.status_code == 401

    async def test_check_in_first_time(self, client: AsyncClient, auth_headers):
        """Test first-time check-in."""
        response = await client.post(
            "/api/v1/points/check-in",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["points_earned"] == POINTS_CHECK_IN
        assert data["streak_days"] == 1
        assert "Check-in successful" in data["message"]

    async def test_check_in_duplicate_same_day(self, client: AsyncClient, auth_headers):
        """Test that duplicate check-in on same day fails."""
        # First check-in
        response1 = await client.post(
            "/api/v1/points/check-in",
            headers=auth_headers,
        )
        assert response1.status_code == 200

        # Second check-in same day
        response2 = await client.post(
            "/api/v1/points/check-in",
            headers=auth_headers,
        )

        assert response2.status_code == 400
        data = response2.json()
        error_detail = data.get("error_detail", data.get("detail", ""))
        if isinstance(error_detail, dict):
            message = str(error_detail.get("message", ""))
        else:
            message = str(error_detail)
        assert "already checked in" in message.lower()

    async def test_check_in_streak_continuation(self, client: AsyncClient, auth_headers, db_session: Session):
        """Test streak continuation when checking in consecutive days."""
        from sqlalchemy import text as sql_text

        # Get user ID
        user_result = db_session.exec(
            sql_text("SELECT id FROM user WHERE email = 'test@example.com'")
        ).first()
        user_id = user_result[0] if user_result else None

        # Create yesterday's check-in record
        today = beijing_date(utcnow())
        yesterday = today - timedelta(days=1)
        yesterday_record = CheckInRecord(
            user_id=user_id,
            check_in_date=yesterday,
            streak_days=3,
            points_earned=POINTS_CHECK_IN,
            created_at=beijing_check_in_timestamp(yesterday),
        )
        db_session.add(yesterday_record)
        db_session.commit()

        # Today's check-in
        response = await client.post(
            "/api/v1/points/check-in",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["streak_days"] == 4  # Continues from yesterday

    async def test_check_in_streak_bonus(self, client: AsyncClient, auth_headers, db_session: Session):
        """Test streak bonus is awarded at threshold."""
        from sqlalchemy import text as sql_text

        # Get user ID
        user_result = db_session.exec(
            sql_text("SELECT id FROM user WHERE email = 'test@example.com'")
        ).first()
        user_id = user_result[0] if user_result else None

        # Create check-in records for streak_days - 1 days
        # So next check-in will trigger bonus
        today = beijing_date(utcnow())
        for i in range(STREAK_BONUS_THRESHOLD - 1):
            check_date = today - timedelta(days=STREAK_BONUS_THRESHOLD - 1 - i)
            record = CheckInRecord(
                user_id=user_id,
                check_in_date=check_date,
                streak_days=i + 1,
                points_earned=POINTS_CHECK_IN,
                created_at=beijing_check_in_timestamp(check_date),
            )
            db_session.add(record)
        db_session.commit()

        # Today's check-in should trigger bonus
        response = await client.post(
            "/api/v1/points/check-in",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["streak_days"] == STREAK_BONUS_THRESHOLD
        assert data["points_earned"] == POINTS_CHECK_IN + POINTS_CHECK_IN_STREAK
        assert "streak bonus" in data["message"].lower()


@pytest.mark.integration
class TestGetCheckInStatus:
    """Tests for GET /api/v1/points/check-in/status endpoint."""

    async def test_check_in_status_unauthorized(self, client: AsyncClient):
        """Test that unauthenticated requests are rejected."""
        response = await client.get("/api/v1/points/check-in/status")
        assert response.status_code == 401

    async def test_check_in_status_not_checked_in(self, client: AsyncClient, auth_headers):
        """Test status when user hasn't checked in today."""
        response = await client.get(
            "/api/v1/points/check-in/status",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["checked_in"] is False
        assert data["streak_days"] == 0
        assert data["points_earned_today"] == 0

    async def test_check_in_status_checked_in(self, client: AsyncClient, auth_headers):
        """Test status after checking in."""
        # Check in first
        await client.post(
            "/api/v1/points/check-in",
            headers=auth_headers,
        )

        # Get status
        response = await client.get(
            "/api/v1/points/check-in/status",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["checked_in"] is True
        assert data["streak_days"] == 1
        assert data["points_earned_today"] == POINTS_CHECK_IN

    async def test_check_in_status_with_streak(self, client: AsyncClient, auth_headers, db_session: Session):
        """Test status shows previous streak when not checked in today."""
        from sqlalchemy import text as sql_text

        # Get user ID
        user_result = db_session.exec(
            sql_text("SELECT id FROM user WHERE email = 'test@example.com'")
        ).first()
        user_id = user_result[0] if user_result else None

        # Create yesterday's check-in
        today = beijing_date(utcnow())
        yesterday = today - timedelta(days=1)
        yesterday_record = CheckInRecord(
            user_id=user_id,
            check_in_date=yesterday,
            streak_days=5,
            points_earned=POINTS_CHECK_IN,
            created_at=beijing_check_in_timestamp(yesterday),
        )
        db_session.add(yesterday_record)
        db_session.commit()

        # Get status - should show streak from yesterday
        response = await client.get(
            "/api/v1/points/check-in/status",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["checked_in"] is False
        assert data["streak_days"] == 5  # Shows previous streak
        assert data["points_earned_today"] == 0


@pytest.mark.integration
class TestGetTransactions:
    """Tests for GET /api/v1/points/transactions endpoint."""

    async def test_transactions_unauthorized(self, client: AsyncClient):
        """Test that unauthenticated requests are rejected."""
        response = await client.get("/api/v1/points/transactions")
        assert response.status_code == 401

    async def test_transactions_empty(self, client: AsyncClient, auth_headers):
        """Test getting transactions when user has none."""
        response = await client.get(
            "/api/v1/points/transactions",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["transactions"] == []
        assert data["total"] == 0
        assert data["page"] == 1
        assert data["page_size"] == 20
        assert data["total_pages"] == 0

    async def test_transactions_with_data(self, client: AsyncClient, auth_headers_with_points):
        """Test getting transactions when user has some."""
        response = await client.get(
            "/api/v1/points/transactions",
            headers=auth_headers_with_points,
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["transactions"]) == 1
        assert data["total"] == 1

        # Check transaction format
        tx = data["transactions"][0]
        assert "id" in tx
        assert tx["amount"] == 200
        assert tx["balance_after"] == 200
        assert tx["transaction_type"] == "test_setup"
        assert tx["is_expired"] is False
        assert "created_at" in tx

    async def test_transactions_pagination(self, client: AsyncClient, auth_headers, db_session: Session):
        """Test transaction history pagination."""
        from sqlalchemy import text as sql_text

        # Get user ID
        user_result = db_session.exec(
            sql_text("SELECT id FROM user WHERE email = 'test@example.com'")
        ).first()
        user_id = user_result[0] if user_result else None

        # Create 25 transactions
        for i in range(25):
            points_service.earn_points(
                session=db_session,
                user_id=user_id,
                amount=10,
                transaction_type=f"test_{i}",
                description=f"Test transaction {i}",
            )

        # Get first page
        response1 = await client.get(
            "/api/v1/points/transactions?page=1&page_size=10",
            headers=auth_headers,
        )

        assert response1.status_code == 200
        data1 = response1.json()
        assert len(data1["transactions"]) == 10
        assert data1["total"] == 25
        assert data1["page"] == 1
        assert data1["total_pages"] == 3

        # Get second page
        response2 = await client.get(
            "/api/v1/points/transactions?page=2&page_size=10",
            headers=auth_headers,
        )

        assert response2.status_code == 200
        data2 = response2.json()
        assert len(data2["transactions"]) == 10
        assert data2["page"] == 2

        # Get last page
        response3 = await client.get(
            "/api/v1/points/transactions?page=3&page_size=10",
            headers=auth_headers,
        )

        assert response3.status_code == 200
        data3 = response3.json()
        assert len(data3["transactions"]) == 5

    @pytest.mark.parametrize(
        "query",
        ["page=0", "page=-1", "page_size=0", "page_size=-1", "page_size=101"],
    )
    async def test_transactions_reject_invalid_pagination(
        self, client: AsyncClient, auth_headers, query: str
    ):
        response = await client.get(
            f"/api/v1/points/transactions?{query}",
            headers=auth_headers,
        )

        assert response.status_code == 422


@pytest.mark.integration
class TestRedeemForPro:
    """Tests for POST /api/v1/points/redeem endpoint."""

    async def test_redeem_unauthorized(self, client: AsyncClient):
        """Test that unauthenticated requests are rejected."""
        response = await client.post(
            "/api/v1/points/redeem",
            json={"days": 7},
        )
        assert response.status_code == 401

    async def test_redeem_insufficient_points(self, client: AsyncClient, auth_headers):
        """Test redemption with insufficient points."""
        response = await client.post(
            "/api/v1/points/redeem",
            json={"days": 7},
            headers=auth_headers,
        )

        assert response.status_code == 402  # Payment Required
        data = response.json()
        error_detail = data.get("error_detail", data.get("detail", ""))
        if isinstance(error_detail, dict):
            message = str(error_detail.get("message", ""))
        else:
            message = str(error_detail)
        assert "insufficient" in message.lower()

    async def test_redeem_minimum_days(self, client: AsyncClient, auth_headers_with_points):
        """Test that minimum redemption is 7 days."""
        response = await client.post(
            "/api/v1/points/redeem",
            json={"days": 5},  # Less than 7
            headers=auth_headers_with_points,
        )

        # Should fail validation (min 7 days)
        assert response.status_code == 422  # Validation error

    async def test_redeem_success(self, client: AsyncClient, auth_headers_with_points, pro_plan):
        """Test successful redemption for Pro."""
        response = await client.post(
            "/api/v1/points/redeem",
            json={"days": 7},
            headers=auth_headers_with_points,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["points_spent"] == POINTS_PRO_7DAYS_COST
        assert data["pro_days"] == 7
        assert "new_period_end" in data

    async def test_redeem_multiple_weeks(self, client: AsyncClient, auth_headers_with_points, pro_plan):
        """Test redemption for multiple weeks."""
        response = await client.post(
            "/api/v1/points/redeem",
            json={"days": 14},  # 2 weeks
            headers=auth_headers_with_points,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["points_spent"] == POINTS_PRO_7DAYS_COST * 2
        assert data["pro_days"] == 14


@pytest.mark.integration
class TestGetEarnOpportunities:
    """Tests for GET /api/v1/points/earn-opportunities endpoint."""

    async def test_earn_opportunities_unauthorized(self, client: AsyncClient):
        """Test that unauthenticated requests are rejected."""
        response = await client.get("/api/v1/points/earn-opportunities")
        assert response.status_code == 401

    async def test_earn_opportunities_success(self, client: AsyncClient, auth_headers):
        """Test getting earn opportunities."""
        response = await client.get(
            "/api/v1/points/earn-opportunities",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0

        # Check opportunity format
        for opp in data:
            assert "type" in opp
            assert "points" in opp
            assert "description" in opp
            assert "is_completed" in opp
            assert "is_available" in opp

    async def test_earn_opportunities_check_in_available(self, client: AsyncClient, auth_headers):
        """Test that check-in opportunity is available before checking in."""
        response = await client.get(
            "/api/v1/points/earn-opportunities",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()

        # Find check-in opportunity
        check_in_opp = next(
            (opp for opp in data if opp["type"] == "check_in"),
            None
        )
        assert check_in_opp is not None
        assert check_in_opp["is_completed"] is False
        assert check_in_opp["is_available"] is True

    async def test_earn_opportunities_check_in_completed(self, client: AsyncClient, auth_headers):
        """Test that check-in opportunity is marked completed after checking in."""
        # Check in first
        await client.post(
            "/api/v1/points/check-in",
            headers=auth_headers,
        )

        # Get opportunities
        response = await client.get(
            "/api/v1/points/earn-opportunities",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()

        # Find check-in opportunity
        check_in_opp = next(
            (opp for opp in data if opp["type"] == "check_in"),
            None
        )
        assert check_in_opp is not None
        assert check_in_opp["is_completed"] is True


@pytest.mark.integration
class TestGetConfig:
    """Tests for GET /api/v1/points/config endpoint."""

    async def test_config_public(self, client: AsyncClient):
        """Test that config endpoint is public (no auth required)."""
        response = await client.get("/api/v1/points/config")

        assert response.status_code == 200
        data = response.json()
        assert "check_in" in data
        assert "check_in_streak" in data
        assert "referral" in data
        assert "pro_7days_cost" in data
        assert "streak_bonus_threshold" in data

    async def test_config_values(self, client: AsyncClient):
        """Test that config returns expected values."""
        response = await client.get("/api/v1/points/config")

        assert response.status_code == 200
        data = response.json()

        assert data["check_in"] == POINTS_CHECK_IN
        assert data["check_in_streak"] == POINTS_CHECK_IN_STREAK
        assert data["referral"] == POINTS_REFERRAL
        assert data["pro_7days_cost"] == POINTS_PRO_7DAYS_COST
        assert data["streak_bonus_threshold"] == STREAK_BONUS_THRESHOLD
