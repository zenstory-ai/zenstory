"""
Tests for PointsService.

Unit tests for the points and check-in management service, covering:
- Balance management (get_balance)
- Points earning and spending (FIFO)
- Daily check-in with streak tracking
- Points redemption for Pro subscription
- Expiration handling
- Earn opportunities
"""
from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlmodel import Session, select

from config.datetime_utils import beijing_date, utcnow
from core.error_codes import ErrorCode
from core.error_handler import APIException
from models import User
from models.points import CheckInRecord, PointsTransaction
from services.features.points_service import (
    POINTS_CHECK_IN,
    POINTS_CHECK_IN_STREAK,
    POINTS_EXPIRATION_MONTHS,
    POINTS_PRO_7DAYS_COST,
    STREAK_BONUS_THRESHOLD,
    points_service,
)


def beijing_check_in_timestamp(day: date) -> datetime:
    """Return an early-Beijing-day UTC timestamp for a current-format row."""
    return datetime.combine(day - timedelta(days=1), datetime.min.time(), tzinfo=UTC).replace(
        hour=17
    )


@pytest.fixture
def test_user(db_session: Session):
    """Create a test user for points testing."""
    user = User(
        email="test@example.com",
        username="testuser",
        hashed_password="hashed_password",
        name="Test User",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.mark.unit
class TestGetBalance:
    """Tests for get_balance function."""

    def test_get_balance_empty(self, db_session: Session, test_user):
        """Test getting balance when user has no points."""
        balance = points_service.get_balance(db_session, test_user.id)

        assert balance["available"] == 0
        assert balance["pending_expiration"] == 0
        assert balance["nearest_expiration_date"] is None

    def test_get_balance_with_earnings(self, db_session: Session, test_user):
        """Test getting balance with positive earnings."""
        # Earn some points
        points_service.earn_points(
            session=db_session,
            user_id=test_user.id,
            amount=100,
            transaction_type="test",
        )

        balance = points_service.get_balance(db_session, test_user.id)

        assert balance["available"] == 100
        expected_pending = 100 if POINTS_EXPIRATION_MONTHS <= 1 else 0
        assert balance["pending_expiration"] == expected_pending
        assert balance["nearest_expiration_date"] is not None

    def test_get_balance_after_spending(self, db_session: Session, test_user):
        """Test getting balance after spending some points."""
        # Earn points
        points_service.earn_points(
            session=db_session,
            user_id=test_user.id,
            amount=100,
            transaction_type="test",
        )

        # Spend points
        points_service.spend_points(
            session=db_session,
            user_id=test_user.id,
            amount=30,
            transaction_type="spend",
        )

        balance = points_service.get_balance(db_session, test_user.id)

        assert balance["available"] == 70

    def test_get_balance_multiple_transactions(self, db_session: Session, test_user):
        """Test balance calculation with multiple transactions."""
        # Multiple earnings
        points_service.earn_points(
            session=db_session,
            user_id=test_user.id,
            amount=50,
            transaction_type="test1",
        )
        points_service.earn_points(
            session=db_session,
            user_id=test_user.id,
            amount=30,
            transaction_type="test2",
        )
        points_service.spend_points(
            session=db_session,
            user_id=test_user.id,
            amount=20,
            transaction_type="spend1",
        )

        balance = points_service.get_balance(db_session, test_user.id)

        assert balance["available"] == 60

    def test_get_balance_ignores_expired(self, db_session: Session, test_user):
        """Test that balance excludes expired points."""
        # Create an expired transaction
        expired_tx = PointsTransaction(
            user_id=test_user.id,
            amount=100,
            balance_after=100,
            transaction_type="expired_test",
            expires_at=datetime.utcnow() - timedelta(days=1),
            is_expired=True,
        )
        db_session.add(expired_tx)
        db_session.commit()

        balance = points_service.get_balance(db_session, test_user.id)

        assert balance["available"] == 0  # Expired points not counted


@pytest.mark.unit
class TestEarnPoints:
    """Tests for earn_points function."""

    def test_earn_points_success(self, db_session: Session, test_user):
        """Test successful points earning."""
        tx = points_service.earn_points(
            session=db_session,
            user_id=test_user.id,
            amount=50,
            transaction_type="test_earn",
            description="Test earning",
        )

        assert tx is not None
        assert tx.amount == 50
        assert tx.balance_after == 50
        assert tx.transaction_type == "test_earn"
        assert tx.description == "Test earning"
        assert tx.expires_at is not None
        assert tx.is_expired is False

    def test_earn_points_expiration_date(self, db_session: Session, test_user):
        """Test that earned points have correct expiration date."""
        tx = points_service.earn_points(
            session=db_session,
            user_id=test_user.id,
            amount=50,
            transaction_type="test",
        )

        # Should expire in approximately POINTS_EXPIRATION_MONTHS months
        expected_expiry = datetime.utcnow() + timedelta(days=POINTS_EXPIRATION_MONTHS * 30)
        delta = abs((tx.expires_at - expected_expiry).total_seconds())
        assert delta < 60  # Within 1 minute

    def test_earn_points_accumulates(self, db_session: Session, test_user):
        """Test that multiple earnings accumulate correctly."""
        tx1 = points_service.earn_points(
            session=db_session,
            user_id=test_user.id,
            amount=30,
            transaction_type="test1",
        )
        assert tx1.balance_after == 30

        tx2 = points_service.earn_points(
            session=db_session,
            user_id=test_user.id,
            amount=20,
            transaction_type="test2",
        )
        assert tx2.balance_after == 50

    def test_earn_points_zero_raises_error(self, db_session: Session, test_user):
        """Test that earning zero points raises error."""
        with pytest.raises(APIException) as exc_info:
            points_service.earn_points(
                session=db_session,
                user_id=test_user.id,
                amount=0,
                transaction_type="test",
            )

        assert exc_info.value.error_code == ErrorCode.VALIDATION_ERROR

    def test_earn_points_negative_raises_error(self, db_session: Session, test_user):
        """Test that earning negative points raises error."""
        with pytest.raises(APIException) as exc_info:
            points_service.earn_points(
                session=db_session,
                user_id=test_user.id,
                amount=-10,
                transaction_type="test",
            )

        assert exc_info.value.error_code == ErrorCode.VALIDATION_ERROR

    def test_earn_points_with_source_id(self, db_session: Session, test_user):
        """Test earning points with a source ID."""
        tx = points_service.earn_points(
            session=db_session,
            user_id=test_user.id,
            amount=50,
            transaction_type="referral",
            source_id="referral-123",
        )

        assert tx.source_id == "referral-123"


@pytest.mark.unit
class TestSpendPoints:
    """Tests for spend_points function."""

    def test_spend_points_success(self, db_session: Session, test_user):
        """Test successful points spending."""
        # First earn some points
        points_service.earn_points(
            session=db_session,
            user_id=test_user.id,
            amount=100,
            transaction_type="test",
        )

        # Then spend
        tx = points_service.spend_points(
            session=db_session,
            user_id=test_user.id,
            amount=30,
            transaction_type="spend",
            description="Test spending",
        )

        assert tx is not None
        assert tx.amount == -30  # Negative for spending
        assert tx.balance_after == 70
        assert tx.transaction_type == "spend"
        assert tx.description == "Test spending"
        assert tx.expires_at is None  # Spending transactions don't expire

    def test_spend_points_insufficient_balance(self, db_session: Session, test_user):
        """Test spending with insufficient balance raises error."""
        # Earn some points
        points_service.earn_points(
            session=db_session,
            user_id=test_user.id,
            amount=50,
            transaction_type="test",
        )

        # Try to spend more than available
        with pytest.raises(APIException) as exc_info:
            points_service.spend_points(
                session=db_session,
                user_id=test_user.id,
                amount=100,
                transaction_type="spend",
            )

        assert exc_info.value.error_code == ErrorCode.QUOTA_EXCEEDED
        assert exc_info.value.status_code == 402

    def test_spend_points_zero_raises_error(self, db_session: Session, test_user):
        """Test that spending zero points raises error."""
        # Earn points first
        points_service.earn_points(
            session=db_session,
            user_id=test_user.id,
            amount=100,
            transaction_type="test",
        )

        with pytest.raises(APIException) as exc_info:
            points_service.spend_points(
                session=db_session,
                user_id=test_user.id,
                amount=0,
                transaction_type="spend",
            )

        assert exc_info.value.error_code == ErrorCode.VALIDATION_ERROR

    def test_spend_points_negative_raises_error(self, db_session: Session, test_user):
        """Test that spending negative points raises error."""
        # Earn points first
        points_service.earn_points(
            session=db_session,
            user_id=test_user.id,
            amount=100,
            transaction_type="test",
        )

        with pytest.raises(APIException) as exc_info:
            points_service.spend_points(
                session=db_session,
                user_id=test_user.id,
                amount=-10,
                transaction_type="spend",
            )

        assert exc_info.value.error_code == ErrorCode.VALIDATION_ERROR

    def test_spend_points_exact_balance(self, db_session: Session, test_user):
        """Test spending exactly the available balance."""
        # Earn points
        points_service.earn_points(
            session=db_session,
            user_id=test_user.id,
            amount=100,
            transaction_type="test",
        )

        # Spend exact amount
        tx = points_service.spend_points(
            session=db_session,
            user_id=test_user.id,
            amount=100,
            transaction_type="spend",
        )

        assert tx.balance_after == 0


@pytest.mark.unit
class TestCheckIn:
    """Tests for check_in function."""

    def test_check_in_first_time(self, db_session: Session, test_user):
        """Test first-time check-in."""
        result = points_service.check_in(db_session, test_user.id)

        assert result["success"] is True
        assert result["points_earned"] == POINTS_CHECK_IN
        assert result["streak_days"] == 1
        assert "Check-in successful" in result["message"]

    def test_check_in_creates_record(self, db_session: Session, test_user):
        """Test that check-in creates a CheckInRecord."""
        today = beijing_date(utcnow())

        points_service.check_in(db_session, test_user.id)

        # Verify record was created
        record = db_session.exec(
            select(CheckInRecord).where(
                CheckInRecord.user_id == test_user.id,
                CheckInRecord.check_in_date == today,
            )
        ).first()

        assert record is not None
        assert record.streak_days == 1
        assert record.points_earned == POINTS_CHECK_IN

    def test_check_in_creates_transaction(self, db_session: Session, test_user):
        """Test that check-in creates a PointsTransaction."""
        points_service.check_in(db_session, test_user.id)

        # Verify transaction was created
        tx = db_session.exec(
            select(PointsTransaction).where(
                PointsTransaction.user_id == test_user.id,
                PointsTransaction.transaction_type == "check_in",
            )
        ).first()

        assert tx is not None
        assert tx.amount == POINTS_CHECK_IN

    def test_check_in_duplicate_same_day(self, db_session: Session, test_user):
        """Test that duplicate check-in on same day fails."""
        # First check-in
        points_service.check_in(db_session, test_user.id)

        # Second check-in same day
        with pytest.raises(APIException) as exc_info:
            points_service.check_in(db_session, test_user.id)

        assert exc_info.value.error_code == ErrorCode.VALIDATION_ERROR
        assert "already checked in" in str(exc_info.value.detail).lower()

    def test_check_in_uses_beijing_calendar_day(self, db_session: Session, test_user):
        before_midnight = datetime(2026, 10, 5, 15, 59, tzinfo=UTC)
        after_midnight = datetime(2026, 10, 5, 16, 1, tzinfo=UTC)

        with patch("services.features.points_service.utcnow", return_value=before_midnight):
            first = points_service.check_in(db_session, test_user.id)
        with patch("services.features.points_service.utcnow", return_value=after_midnight):
            second = points_service.check_in(db_session, test_user.id)

        assert first["streak_days"] == 1
        assert second["streak_days"] == 2
        records = db_session.exec(
            select(CheckInRecord)
            .where(CheckInRecord.user_id == test_user.id)
            .order_by(CheckInRecord.check_in_date)
        ).all()
        assert [record.check_in_date for record in records] == [
            date(2026, 10, 5),
            date(2026, 10, 6),
        ]

    def test_check_in_does_not_reset_at_utc_midnight(self, db_session: Session, test_user):
        with patch(
            "services.features.points_service.utcnow",
            return_value=datetime(2026, 10, 5, 23, 59, tzinfo=UTC),
        ):
            points_service.check_in(db_session, test_user.id)

        with (
            patch(
                "services.features.points_service.utcnow",
                return_value=datetime(2026, 10, 6, 0, 1, tzinfo=UTC),
            ),
            pytest.raises(APIException) as exc_info,
        ):
            points_service.check_in(db_session, test_user.id)

        assert exc_info.value.error_code == ErrorCode.VALIDATION_ERROR

    def test_check_in_rejects_legacy_utc_record_from_same_beijing_day(
        self,
        db_session: Session,
        test_user,
    ):
        legacy_time = datetime(2026, 10, 5, 16, 30, tzinfo=UTC)
        db_session.add(
            CheckInRecord(
                user_id=test_user.id,
                check_in_date=date(2026, 10, 5),
                streak_days=4,
                points_earned=POINTS_CHECK_IN,
                created_at=legacy_time,
            )
        )
        db_session.commit()

        with (
            patch(
                "services.features.points_service.utcnow",
                return_value=datetime(2026, 10, 5, 17, 0, tzinfo=UTC),
            ),
            pytest.raises(APIException) as exc_info,
        ):
            points_service.check_in(db_session, test_user.id)

        assert exc_info.value.error_code == ErrorCode.VALIDATION_ERROR
        assert exc_info.value.detail["streak_days"] == 4

    def test_check_in_continues_streak_from_legacy_utc_record(
        self,
        db_session: Session,
        test_user,
    ):
        db_session.add(
            CheckInRecord(
                user_id=test_user.id,
                check_in_date=date(2026, 10, 4),
                streak_days=6,
                points_earned=POINTS_CHECK_IN,
                created_at=datetime(2026, 10, 4, 16, 30, tzinfo=UTC),
            )
        )
        db_session.commit()

        with patch(
            "services.features.points_service.utcnow",
            return_value=datetime(2026, 10, 5, 16, 30, tzinfo=UTC),
        ):
            result = points_service.check_in(db_session, test_user.id)

        assert result["streak_days"] == 7

    def test_check_in_streak_continuation(self, db_session: Session, test_user):
        """Test streak continuation when checking in consecutive days."""
        today = beijing_date(utcnow())
        yesterday = today - timedelta(days=1)

        # Create yesterday's record
        yesterday_record = CheckInRecord(
            user_id=test_user.id,
            check_in_date=yesterday,
            streak_days=3,
            points_earned=POINTS_CHECK_IN,
            created_at=beijing_check_in_timestamp(yesterday),
        )
        db_session.add(yesterday_record)
        db_session.commit()

        # Today's check-in
        result = points_service.check_in(db_session, test_user.id)

        assert result["streak_days"] == 4

    def test_check_in_streak_bonus(self, db_session: Session, test_user):
        """Test streak bonus is awarded at threshold."""
        today = beijing_date(utcnow())

        # Create check-in records for STREAK_BONUS_THRESHOLD - 1 days
        for i in range(STREAK_BONUS_THRESHOLD - 1):
            check_date = today - timedelta(days=STREAK_BONUS_THRESHOLD - 1 - i)
            record = CheckInRecord(
                user_id=test_user.id,
                check_in_date=check_date,
                streak_days=i + 1,
                points_earned=POINTS_CHECK_IN,
                created_at=beijing_check_in_timestamp(check_date),
            )
            db_session.add(record)
        db_session.commit()

        # Today's check-in should trigger bonus
        result = points_service.check_in(db_session, test_user.id)

        assert result["streak_days"] == STREAK_BONUS_THRESHOLD
        assert result["points_earned"] == POINTS_CHECK_IN + POINTS_CHECK_IN_STREAK
        assert "streak bonus" in result["message"].lower()

    def test_check_in_streak_multiple_bonuses(self, db_session: Session, test_user):
        """Test multiple streak bonuses at intervals."""
        today = beijing_date(utcnow())

        # Create records for 13 days so today's 14th check-in hits the 2nd bonus interval (7, 14, ...)
        for i in range(13):
            check_date = today - timedelta(days=13 - i)
            record = CheckInRecord(
                user_id=test_user.id,
                check_in_date=check_date,
                streak_days=i + 1,
                points_earned=POINTS_CHECK_IN,
                created_at=beijing_check_in_timestamp(check_date),
            )
            db_session.add(record)
        db_session.commit()

        # 14th check-in should trigger second bonus
        result = points_service.check_in(db_session, test_user.id)

        assert result["streak_days"] == 14
        assert result["points_earned"] == POINTS_CHECK_IN + POINTS_CHECK_IN_STREAK

    def test_check_in_no_streak_after_break(self, db_session: Session, test_user):
        """Test that streak resets after a break."""
        today = beijing_date(utcnow())
        two_days_ago = today - timedelta(days=2)

        # Create record from 2 days ago (gap of 1 day)
        old_record = CheckInRecord(
            user_id=test_user.id,
            check_in_date=two_days_ago,
            streak_days=5,
            points_earned=POINTS_CHECK_IN,
            created_at=beijing_check_in_timestamp(two_days_ago),
        )
        db_session.add(old_record)
        db_session.commit()

        # Today's check-in should restart streak
        result = points_service.check_in(db_session, test_user.id)

        assert result["streak_days"] == 1  # Reset to 1


@pytest.mark.unit
class TestGetCheckInStatus:
    """Tests for get_check_in_status function."""

    def test_get_check_in_status_not_checked_in(self, db_session: Session, test_user):
        """Test status when user hasn't checked in today."""
        status = points_service.get_check_in_status(db_session, test_user.id)

        assert status["checked_in"] is False
        assert status["streak_days"] == 0
        assert status["points_earned_today"] == 0

    def test_get_check_in_status_checked_in(self, db_session: Session, test_user):
        """Test status after checking in."""
        # Check in
        points_service.check_in(db_session, test_user.id)

        # Get status
        status = points_service.get_check_in_status(db_session, test_user.id)

        assert status["checked_in"] is True
        assert status["streak_days"] == 1
        assert status["points_earned_today"] == POINTS_CHECK_IN

    def test_get_check_in_status_shows_previous_streak(self, db_session: Session, test_user):
        """Test status shows previous streak when not checked in today."""
        today = beijing_date(utcnow())
        yesterday = today - timedelta(days=1)

        # Create yesterday's check-in
        yesterday_record = CheckInRecord(
            user_id=test_user.id,
            check_in_date=yesterday,
            streak_days=5,
            points_earned=POINTS_CHECK_IN,
            created_at=beijing_check_in_timestamp(yesterday),
        )
        db_session.add(yesterday_record)
        db_session.commit()

        # Get status
        status = points_service.get_check_in_status(db_session, test_user.id)

        assert status["checked_in"] is False
        assert status["streak_days"] == 5  # Shows previous streak
        assert status["points_earned_today"] == 0

    def test_get_check_in_status_recognizes_legacy_record_after_beijing_midnight(
        self,
        db_session: Session,
        test_user,
    ):
        db_session.add(
            CheckInRecord(
                user_id=test_user.id,
                check_in_date=date(2026, 10, 5),
                streak_days=3,
                points_earned=POINTS_CHECK_IN,
                created_at=datetime(2026, 10, 5, 16, 5, tzinfo=UTC),
            )
        )
        db_session.commit()

        with patch(
            "services.features.points_service.utcnow",
            return_value=datetime(2026, 10, 5, 18, 0, tzinfo=UTC),
        ):
            status = points_service.get_check_in_status(db_session, test_user.id)

        assert status == {
            "checked_in": True,
            "streak_days": 3,
            "points_earned_today": POINTS_CHECK_IN,
        }


@pytest.mark.unit
class TestRedeemForPro:
    """Tests for redeem_for_pro function."""

    @patch('services.features.points_service.subscription_service')
    def test_redeem_for_pro_success(self, mock_subscription_service, db_session: Session, test_user):
        """Test successful redemption for Pro."""
        # Mock subscription service
        mock_subscription = MagicMock()
        mock_subscription.current_period_end = datetime.utcnow() + timedelta(days=7)
        mock_subscription_service.create_user_subscription.return_value = mock_subscription

        # Earn enough points
        points_service.earn_points(
            session=db_session,
            user_id=test_user.id,
            amount=POINTS_PRO_7DAYS_COST,
            transaction_type="test",
        )

        # Redeem
        result = points_service.redeem_for_pro(db_session, test_user.id, days=7)

        assert result["success"] is True
        assert result["points_spent"] == POINTS_PRO_7DAYS_COST
        assert result["pro_days"] == 7
        assert "new_period_end" in result

    @patch('services.features.points_service.subscription_service')
    def test_redeem_for_pro_multiple_weeks(self, mock_subscription_service, db_session: Session, test_user):
        """Test redemption for multiple weeks."""
        mock_subscription = MagicMock()
        mock_subscription.current_period_end = datetime.utcnow() + timedelta(days=14)
        mock_subscription_service.create_user_subscription.return_value = mock_subscription

        # Earn enough points for 2 weeks
        points_service.earn_points(
            session=db_session,
            user_id=test_user.id,
            amount=POINTS_PRO_7DAYS_COST * 2,
            transaction_type="test",
        )

        # Redeem
        result = points_service.redeem_for_pro(db_session, test_user.id, days=14)

        assert result["success"] is True
        assert result["points_spent"] == POINTS_PRO_7DAYS_COST * 2
        assert result["pro_days"] == 14

    def test_redeem_for_pro_insufficient_points(self, db_session: Session, test_user):
        """Test redemption with insufficient points."""
        # Earn some points but not enough
        points_service.earn_points(
            session=db_session,
            user_id=test_user.id,
            amount=50,
            transaction_type="test",
        )

        with pytest.raises(APIException) as exc_info:
            points_service.redeem_for_pro(db_session, test_user.id, days=7)

        assert exc_info.value.error_code == ErrorCode.QUOTA_EXCEEDED
        assert exc_info.value.status_code == 402

    def test_redeem_for_pro_minimum_days(self, db_session: Session, test_user):
        """Test that minimum redemption is 7 days."""
        with pytest.raises(APIException) as exc_info:
            points_service.redeem_for_pro(db_session, test_user.id, days=5)

        assert exc_info.value.error_code == ErrorCode.VALIDATION_ERROR

    @patch('services.features.points_service.subscription_service')
    def test_redeem_for_pro_deducts_points(self, mock_subscription_service, db_session: Session, test_user):
        """Test that redemption deducts points correctly."""
        mock_subscription = MagicMock()
        mock_subscription.current_period_end = datetime.utcnow() + timedelta(days=7)
        mock_subscription_service.create_user_subscription.return_value = mock_subscription

        # Earn points
        points_service.earn_points(
            session=db_session,
            user_id=test_user.id,
            amount=POINTS_PRO_7DAYS_COST + 50,
            transaction_type="test",
        )

        # Redeem
        points_service.redeem_for_pro(db_session, test_user.id, days=7)

        # Check balance
        balance = points_service.get_balance(db_session, test_user.id)
        assert balance["available"] == 50  # Remaining points


@pytest.mark.unit
class TestExpireStalePoints:
    """Tests for expire_stale_points function."""

    def test_expire_stale_points_marks_expired(self, db_session: Session, test_user):
        """Test that expired points are marked as expired."""
        # Create an expired transaction
        expired_tx = PointsTransaction(
            user_id=test_user.id,
            amount=100,
            balance_after=100,
            transaction_type="test",
            expires_at=datetime.utcnow() - timedelta(days=1),
            is_expired=False,
        )
        db_session.add(expired_tx)
        db_session.commit()

        # Run expiration
        count = points_service.expire_stale_points(db_session)

        assert count == 1

        # Verify it's marked as expired
        db_session.refresh(expired_tx)
        assert expired_tx.is_expired is True
        assert expired_tx.expired_at is not None

    def test_expire_stale_points_skips_non_expired(self, db_session: Session, test_user):
        """Test that non-expired points are not marked."""
        # Create a non-expired transaction
        valid_tx = PointsTransaction(
            user_id=test_user.id,
            amount=100,
            balance_after=100,
            transaction_type="test",
            expires_at=datetime.utcnow() + timedelta(days=30),
            is_expired=False,
        )
        db_session.add(valid_tx)
        db_session.commit()

        # Run expiration
        count = points_service.expire_stale_points(db_session)

        assert count == 0

        # Verify it's not marked
        db_session.refresh(valid_tx)
        assert valid_tx.is_expired is False

    def test_expire_stale_points_skips_spending(self, db_session: Session, test_user):
        """Test that spending transactions are not affected."""
        # Create a spending transaction (negative amount)
        spend_tx = PointsTransaction(
            user_id=test_user.id,
            amount=-50,
            balance_after=50,
            transaction_type="spend",
            expires_at=datetime.utcnow() - timedelta(days=1),
            is_expired=False,
        )
        db_session.add(spend_tx)
        db_session.commit()

        # Run expiration
        count = points_service.expire_stale_points(db_session)

        assert count == 0

        # Verify it's not marked
        db_session.refresh(spend_tx)
        assert spend_tx.is_expired is False


@pytest.mark.unit
class TestGetEarnOpportunities:
    """Tests for get_earn_opportunities function."""

    def test_get_earn_opportunities_includes_check_in(self, db_session: Session, test_user):
        """Test that check-in opportunity is included."""
        opportunities = points_service.get_earn_opportunities(db_session, test_user.id)

        check_in_opp = next(
            (opp for opp in opportunities if opp["type"] == "check_in"),
            None
        )
        assert check_in_opp is not None
        assert check_in_opp["points"] == POINTS_CHECK_IN
        assert check_in_opp["is_available"] is True

    def test_get_earn_opportunities_check_in_completed(self, db_session: Session, test_user):
        """Test that check-in is marked completed after checking in."""
        # Check in
        points_service.check_in(db_session, test_user.id)

        # Get opportunities
        opportunities = points_service.get_earn_opportunities(db_session, test_user.id)

        check_in_opp = next(
            (opp for opp in opportunities if opp["type"] == "check_in"),
            None
        )
        assert check_in_opp["is_completed"] is True

    def test_get_earn_opportunities_streak_bonus_eligible(self, db_session: Session, test_user):
        """Test that streak bonus appears when eligible."""
        today = beijing_date(utcnow())

        # Create check-in records for STREAK_BONUS_THRESHOLD - 1 days
        for i in range(STREAK_BONUS_THRESHOLD - 1):
            check_date = today - timedelta(days=STREAK_BONUS_THRESHOLD - 1 - i)
            record = CheckInRecord(
                user_id=test_user.id,
                check_in_date=check_date,
                streak_days=i + 1,
                points_earned=POINTS_CHECK_IN,
                created_at=beijing_check_in_timestamp(check_date),
            )
            db_session.add(record)
        db_session.commit()

        # Get opportunities
        opportunities = points_service.get_earn_opportunities(db_session, test_user.id)

        # Should include streak bonus opportunity
        streak_opp = next(
            (opp for opp in opportunities if opp["type"] == "check_in_streak"),
            None
        )
        assert streak_opp is not None
        assert streak_opp["points"] == POINTS_CHECK_IN_STREAK
        assert streak_opp["is_available"] is True


@pytest.mark.unit
class TestGetTransactionHistory:
    """Tests for get_transaction_history function."""

    def test_get_transaction_history_empty(self, db_session: Session, test_user):
        """Test getting history when user has no transactions."""
        transactions, total = points_service.get_transaction_history(
            db_session, test_user.id, page=1, page_size=20
        )

        assert transactions == []
        assert total == 0

    def test_get_transaction_history_with_data(self, db_session: Session, test_user):
        """Test getting history with transactions."""
        # Create some transactions
        for i in range(5):
            points_service.earn_points(
                session=db_session,
                user_id=test_user.id,
                amount=10,
                transaction_type=f"test_{i}",
            )

        transactions, total = points_service.get_transaction_history(
            db_session, test_user.id, page=1, page_size=20
        )

        assert len(transactions) == 5
        assert total == 5

    def test_get_transaction_history_pagination(self, db_session: Session, test_user):
        """Test transaction history pagination."""
        # Create 25 transactions
        for i in range(25):
            points_service.earn_points(
                session=db_session,
                user_id=test_user.id,
                amount=1,
                transaction_type=f"test_{i}",
            )

        # Get first page
        page1, total1 = points_service.get_transaction_history(
            db_session, test_user.id, page=1, page_size=10
        )
        assert len(page1) == 10
        assert total1 == 25

        # Get second page
        page2, total2 = points_service.get_transaction_history(
            db_session, test_user.id, page=2, page_size=10
        )
        assert len(page2) == 10
        assert total2 == 25

        # Get last page
        page3, total3 = points_service.get_transaction_history(
            db_session, test_user.id, page=3, page_size=10
        )
        assert len(page3) == 5
        assert total3 == 25

    def test_get_transaction_history_order(self, db_session: Session, test_user):
        """Test that transactions are ordered by created_at desc."""
        # Create multiple transactions with slight time differences
        import time
        for i in range(3):
            points_service.earn_points(
                session=db_session,
                user_id=test_user.id,
                amount=10,
                transaction_type=f"test_{i}",
            )
            time.sleep(0.01)  # Small delay to ensure different timestamps

        transactions, _ = points_service.get_transaction_history(
            db_session, test_user.id, page=1, page_size=10
        )

        # Should be ordered by created_at descending (newest first)
        assert transactions[0].created_at >= transactions[1].created_at
        assert transactions[1].created_at >= transactions[2].created_at
