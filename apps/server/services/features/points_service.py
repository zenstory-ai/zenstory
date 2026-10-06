"""
Points Service - Core service for points and check-in management.

Provides methods for:
- Balance management (get_balance)
- Points earning and spending (FIFO)
- Daily check-in with streak tracking
- Points redemption for Pro subscription
- Expiration handling
- Earn opportunities display
"""
import os
from collections.abc import Iterable
from contextlib import suppress
from datetime import date, datetime, timedelta
from itertools import groupby
from typing import Any, cast

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import QueryableAttribute, load_only
from sqlmodel import Session, col, func, select

from config.datetime_utils import beijing_date, normalize_datetime_to_utc, utcnow
from core.error_codes import ErrorCode
from core.error_handler import APIException
from models.entities import User
from models.points import CheckInRecord, PointsTransaction
from services.subscription.subscription_service import subscription_service
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

# Configuration from environment
POINTS_CHECK_IN = int(os.getenv("POINTS_CHECK_IN", "10"))
POINTS_CHECK_IN_STREAK = int(os.getenv("POINTS_CHECK_IN_STREAK", "50"))
POINTS_REFERRAL = int(os.getenv("POINTS_REFERRAL", "100"))
POINTS_SKILL_CONTRIBUTION = int(os.getenv("POINTS_SKILL_CONTRIBUTION", "50"))
POINTS_INSPIRATION_CONTRIBUTION = int(os.getenv("POINTS_INSPIRATION_CONTRIBUTION", "30"))
POINTS_PROFILE_COMPLETE = int(os.getenv("POINTS_PROFILE_COMPLETE", "20"))
POINTS_PRO_7DAYS_COST = int(os.getenv("POINTS_PRO_7DAYS_COST", "100"))
POINTS_EXPIRATION_MONTHS = int(os.getenv("POINTS_EXPIRATION_MONTHS", "12"))

# Streak bonus threshold (days)
STREAK_BONUS_THRESHOLD = int(os.getenv("STREAK_BONUS_THRESHOLD", "7"))

# Supported redemption durations and their points costs
REDEEM_DURATION_COSTS = {
    7: POINTS_PRO_7DAYS_COST,
    14: POINTS_PRO_7DAYS_COST * 2,
    30: POINTS_PRO_7DAYS_COST * 4,
}


def effective_check_in_date(record: CheckInRecord) -> date:
    """Return a record's Beijing calendar day without migrating legacy rows.

    Legacy rows stored the UTC date of ``created_at``.  Current rows store the
    Beijing date directly.  Only an exact UTC-date match is treated as legacy;
    this preserves explicitly backfilled dates and older test fixtures whose
    creation timestamp belongs to a different day.
    """
    created_at = record.created_at
    if created_at is not None:
        created_at_utc = normalize_datetime_to_utc(created_at)
        if record.check_in_date == created_at_utc.date():
            return beijing_date(created_at_utc)
    return record.check_in_date


def _calculate_balance(transactions: Iterable[PointsTransaction], *, now: datetime) -> dict:
    """Replay the existing FIFO ledger without querying or mutating rows."""
    lots: list[dict] = []
    overspent = 0

    def to_naive(dt):
        if dt is None:
            return None
        return dt.replace(tzinfo=None) if getattr(dt, "tzinfo", None) else dt

    for tx in transactions:
        tx_created_at = to_naive(tx.created_at)

        if tx.amount > 0:
            lots.append(
                {
                    "remaining": tx.amount,
                    "expires_at": tx.expires_at,
                    "is_expired": tx.is_expired,
                }
            )
            continue

        if tx.amount >= 0:
            continue

        amount_to_spend = abs(tx.amount)

        # Primary pass: consume from lots that were valid at spend time.
        for lot in lots:
            if amount_to_spend <= 0:
                break
            if lot["remaining"] <= 0:
                continue

            lot_expires_at = to_naive(lot["expires_at"])
            if lot_expires_at and tx_created_at and lot_expires_at <= tx_created_at:
                continue

            consumed = min(lot["remaining"], amount_to_spend)
            lot["remaining"] -= consumed
            amount_to_spend -= consumed

        # Fallback for legacy inconsistent data: consume any remaining lots.
        if amount_to_spend > 0:
            for lot in lots:
                if amount_to_spend <= 0:
                    break
                if lot["remaining"] <= 0:
                    continue
                consumed = min(lot["remaining"], amount_to_spend)
                lot["remaining"] -= consumed
                amount_to_spend -= consumed

        if amount_to_spend > 0:
            overspent += amount_to_spend

    now_naive = to_naive(now)
    thirty_days_later_naive = to_naive(now + timedelta(days=30))

    available = 0
    pending_expiration = 0
    nearest_expiration = None

    for lot in lots:
        if lot["remaining"] <= 0:
            continue

        lot_expires_at = to_naive(lot["expires_at"])
        lot_is_active = not lot["is_expired"] and (
            lot_expires_at is None or (now_naive and lot_expires_at > now_naive)
        )

        if not lot_is_active:
            continue

        available += lot["remaining"]

        if (
            lot_expires_at
            and now_naive
            and thirty_days_later_naive
            and lot_expires_at <= thirty_days_later_naive
        ):
            pending_expiration += lot["remaining"]

        if lot_expires_at and (nearest_expiration is None or lot_expires_at < to_naive(nearest_expiration)):
            nearest_expiration = lot["expires_at"]

    available = max(0, available - overspent)

    return {
        "available": available,
        "pending_expiration": pending_expiration,
        "nearest_expiration_date": nearest_expiration.isoformat() if nearest_expiration else None,
    }


class PointsService:
    """Service for managing user points and check-ins."""

    POINTS_CONFIG = {
        "check_in": POINTS_CHECK_IN,
        "check_in_streak": POINTS_CHECK_IN_STREAK,
        "referral": POINTS_REFERRAL,
        "skill_contribution": POINTS_SKILL_CONTRIBUTION,
        "inspiration_contribution": POINTS_INSPIRATION_CONTRIBUTION,
        "profile_complete": POINTS_PROFILE_COMPLETE,
        "pro_7days_cost": POINTS_PRO_7DAYS_COST,
    }

    def _get_check_in_record_for_day(
        self,
        session: Session,
        user_id: str,
        target_date: date,
    ) -> CheckInRecord | None:
        """Find a check-in by its effective Beijing day.

        A legacy UTC-date row for a Beijing day can only be stored under the
        target date or the preceding date, so this remains an indexed lookup.
        """
        stored_dates = (target_date, target_date - timedelta(days=1))
        records = session.exec(
            select(CheckInRecord)
            .where(CheckInRecord.user_id == user_id)
            .where(CheckInRecord.check_in_date.in_(stored_dates))
            .order_by(CheckInRecord.created_at.desc())
        ).all()
        return next(
            (record for record in records if effective_check_in_date(record) == target_date),
            None,
        )

    def _lock_user_row(self, session: Session, user_id: str) -> None:
        """Acquire a row-level lock for user-scoped balance mutations."""
        session.exec(
            select(User).where(User.id == user_id).with_for_update()
        ).first()

    def get_balance(self, session: Session, user_id: str) -> dict:
        """
        Get user's current points balance.

        Args:
            session: Database session
            user_id: User ID

        Returns:
            dict with available, pending_expiration, nearest_expiration_date
        """
        now = utcnow()

        # Replay full ledger in chronological order to keep FIFO spending accurate
        transactions = session.exec(
            select(PointsTransaction)
            .where(PointsTransaction.user_id == user_id)
            .order_by(PointsTransaction.created_at.asc())
        ).all()

        return _calculate_balance(transactions, now=now)

    def get_available_points_stats(self, session: Session) -> tuple[int, int]:
        """Return current available points and positive-wallet count in one scan."""
        now = utcnow()
        transactions = session.exec(
            select(PointsTransaction)
            .options(load_only(
                *(cast(QueryableAttribute[Any], field) for field in (
                    PointsTransaction.id, PointsTransaction.user_id,
                    PointsTransaction.amount, PointsTransaction.created_at,
                    PointsTransaction.expires_at, PointsTransaction.is_expired,
                ))
            ))
            .order_by(col(PointsTransaction.user_id).asc(), col(PointsTransaction.created_at).asc()),
            execution_options={"yield_per": 256},
        )
        try:
            total_available = 0
            positive_wallet_count = 0
            for _, rows in groupby(transactions, key=lambda tx: tx.user_id):
                available = _calculate_balance(rows, now=now)["available"]
                total_available += available
                positive_wallet_count += int(available > 0)
            return total_available, positive_wallet_count
        finally:
            transactions.close()

    def get_total_available_points(self, session: Session) -> int:
        """Keep the dashboard total on the same canonical balance calculation."""
        return self.get_available_points_stats(session)[0]

    def earn_points(
        self,
        session: Session,
        user_id: str,
        amount: int,
        transaction_type: str,
        source_id: str | None = None,
        description: str | None = None,
        commit: bool = True,
    ) -> PointsTransaction:
        """
        Earn points for a user.

        Creates a new positive transaction with expiration date.

        Args:
            session: Database session
            user_id: User ID
            amount: Points to earn (positive)
            transaction_type: Type of earning (check_in, referral, etc.)
            source_id: Optional related entity ID
            description: Optional description

        Returns:
            Created PointsTransaction

        Raises:
            APIException: If amount is not positive
        """
        if amount <= 0:
            raise APIException(
                error_code=ErrorCode.VALIDATION_ERROR,
                status_code=400,
                detail="Amount must be positive for earning",
            )

        # Calculate balance after transaction
        current_balance = self.get_balance(session, user_id)
        balance_after = current_balance["available"] + amount

        # Calculate expiration date (12 months from now)
        expires_at = utcnow() + timedelta(days=POINTS_EXPIRATION_MONTHS * 30)

        transaction = PointsTransaction(
            user_id=user_id,
            amount=amount,
            balance_after=balance_after,
            transaction_type=transaction_type,
            source_id=source_id,
            description=description,
            expires_at=expires_at,
            is_expired=False,
        )

        session.add(transaction)
        if commit:
            session.commit()
            session.refresh(transaction)
        else:
            session.flush()

        log_with_context(
            logger,
            20,  # INFO
            "Points earned",
            user_id=user_id,
            amount=amount,
            transaction_type=transaction_type,
            balance_after=balance_after,
            expires_at=expires_at.isoformat(),
        )

        return transaction

    def spend_points(
        self,
        session: Session,
        user_id: str,
        amount: int,
        transaction_type: str,
        source_id: str | None = None,
        description: str | None = None,
        commit: bool = True,
    ) -> PointsTransaction:
        """
        Spend points using FIFO (First In, First Out) order.

        Spends from oldest non-expired transactions first.

        Args:
            session: Database session
            user_id: User ID
            amount: Points to spend (positive, will be negated internally)
            transaction_type: Type of spending
            source_id: Optional related entity ID
            description: Optional description

        Returns:
            Created PointsTransaction

        Raises:
            APIException: If insufficient balance
        """
        if amount <= 0:
            raise APIException(
                error_code=ErrorCode.VALIDATION_ERROR,
                status_code=400,
                detail="Amount must be positive for spending",
            )

        # Serialize balance mutations per user to avoid concurrent overspend.
        self._lock_user_row(session, user_id)

        # Check balance
        current_balance = self.get_balance(session, user_id)
        if current_balance["available"] < amount:
            raise APIException(
                error_code=ErrorCode.QUOTA_EXCEEDED,
                status_code=402,
                detail={
                    "message": "Insufficient points balance",
                    "required": amount,
                    "available": current_balance["available"],
                },
            )

        balance_after = current_balance["available"] - amount

        # Create negative transaction
        transaction = PointsTransaction(
            user_id=user_id,
            amount=-amount,  # Negative for spending
            balance_after=balance_after,
            transaction_type=transaction_type,
            source_id=source_id,
            description=description,
            expires_at=None,  # Spending transactions don't expire
            is_expired=False,
        )

        session.add(transaction)
        if commit:
            session.commit()
            session.refresh(transaction)
        else:
            session.flush()

        log_with_context(
            logger,
            20,  # INFO
            "Points spent",
            user_id=user_id,
            amount=amount,
            transaction_type=transaction_type,
            balance_after=balance_after,
        )

        return transaction

    def check_in(self, session: Session, user_id: str) -> dict:
        """
        Perform daily check-in.

        Awards base points plus streak bonus if eligible.
        Creates a CheckInRecord and PointsTransaction.

        Args:
            session: Database session
            user_id: User ID

        Returns:
            dict with success, points_earned, streak_days, message

        Raises:
            APIException: If already checked in today
        """
        now = utcnow()
        today = beijing_date(now)

        # Check if already checked in today
        existing = self._get_check_in_record_for_day(session, user_id, today)

        if existing:
            raise APIException(
                error_code=ErrorCode.VALIDATION_ERROR,
                status_code=400,
                detail={
                    "message": "Already checked in today",
                    "streak_days": existing.streak_days,
                    "points_earned": existing.points_earned,
                },
            )

        # Calculate streak
        yesterday = today - timedelta(days=1)
        yesterday_record = self._get_check_in_record_for_day(session, user_id, yesterday)

        streak_days = 1
        if yesterday_record:
            streak_days = yesterday_record.streak_days + 1

        # Calculate points
        points_earned = POINTS_CHECK_IN
        transaction_type = "check_in"

        # Streak bonus
        if streak_days >= STREAK_BONUS_THRESHOLD and streak_days % STREAK_BONUS_THRESHOLD == 0:
            points_earned += POINTS_CHECK_IN_STREAK
            transaction_type = "check_in_streak"

        # Create check-in record
        check_in_record = CheckInRecord(
            user_id=user_id,
            check_in_date=today,
            streak_days=streak_days,
            points_earned=points_earned,
            created_at=now,
        )
        session.add(check_in_record)

        # Create points transaction and commit once to keep check-in atomic.
        try:
            self.earn_points(
                session=session,
                user_id=user_id,
                amount=points_earned,
                transaction_type=transaction_type,
                source_id=check_in_record.id,
                description=None,
                commit=False,
            )
            session.commit()
        except IntegrityError:
            session.rollback()
            existing = self._get_check_in_record_for_day(session, user_id, today)
            if not existing:
                raise
            raise APIException(
                error_code=ErrorCode.VALIDATION_ERROR,
                status_code=400,
                detail={
                    "message": "Already checked in today",
                    "streak_days": existing.streak_days if existing else 0,
                    "points_earned": existing.points_earned if existing else 0,
                },
            ) from None

        log_with_context(
            logger,
            20,  # INFO
            "Check-in successful",
            user_id=user_id,
            streak_days=streak_days,
            points_earned=points_earned,
        )

        return {
            "success": True,
            "points_earned": points_earned,
            "streak_days": streak_days,
            "message": f"Check-in successful! +{points_earned} points" + (
                " (Streak bonus!)" if transaction_type == "check_in_streak" else ""
            ),
        }

    def get_check_in_status(self, session: Session, user_id: str) -> dict:
        """
        Get user's check-in status for today.

        Args:
            session: Database session
            user_id: User ID

        Returns:
            dict with checked_in, streak_days, points_earned_today
        """
        today = beijing_date(utcnow())

        today_record = self._get_check_in_record_for_day(session, user_id, today)

        if today_record:
            return {
                "checked_in": True,
                "streak_days": today_record.streak_days,
                "points_earned_today": today_record.points_earned,
            }

        # Not checked in today, get last streak
        yesterday = today - timedelta(days=1)
        yesterday_record = self._get_check_in_record_for_day(session, user_id, yesterday)

        streak = 0
        if yesterday_record:
            streak = yesterday_record.streak_days

        return {
            "checked_in": False,
            "streak_days": streak,
            "points_earned_today": 0,
        }

    def redeem_for_pro(self, session: Session, user_id: str, days: int = 7) -> dict:
        """
        Redeem points for Pro subscription days.

        Args:
            session: Database session
            user_id: User ID
            days: Number of Pro days (default 7)

        Returns:
            dict with success, points_spent, pro_days, new_period_end

        Raises:
            APIException: If insufficient points
        """
        # Validate supported durations and calculate cost
        cost = REDEEM_DURATION_COSTS.get(days)
        if cost is None:
            raise APIException(
                error_code=ErrorCode.VALIDATION_ERROR,
                status_code=400,
                detail={
                    "message": "Unsupported redemption duration",
                    "allowed_days": sorted(REDEEM_DURATION_COSTS.keys()),
                },
            )

        # Serialize redemption by user and commit points+subscription atomically.
        self._lock_user_row(session, user_id)
        current_balance = self.get_balance(session, user_id)
        if current_balance["available"] < cost:
            raise APIException(
                error_code=ErrorCode.QUOTA_EXCEEDED,
                status_code=402,
                detail={
                    "message": "Insufficient points for redemption",
                    "required": cost,
                    "available": current_balance["available"],
                },
            )

        try:
            self.spend_points(
                session=session,
                user_id=user_id,
                amount=cost,
                transaction_type="redeem_pro",
                description=None,
                commit=False,
            )

            subscription = subscription_service.create_user_subscription(
                session=session,
                user_id=user_id,
                plan_name="pro",
                duration_days=days,
                metadata={"source": "points_redemption", "points_cost": cost},
                commit=False,
            )
            session.commit()
            with suppress(Exception):
                session.refresh(subscription)
        except Exception:
            session.rollback()
            raise

        log_with_context(
            logger,
            20,  # INFO
            "Points redeemed for Pro",
            user_id=user_id,
            points_spent=cost,
            pro_days=days,
            new_period_end=subscription.current_period_end.isoformat(),
        )

        return {
            "success": True,
            "points_spent": cost,
            "pro_days": days,
            "new_period_end": subscription.current_period_end.isoformat(),
        }

    def expire_stale_points(self, session: Session) -> int:
        """
        Batch job to mark expired points.

        Finds all non-expired transactions past their expiration date
        and marks them as expired.

        Args:
            session: Database session

        Returns:
            Number of transactions marked as expired
        """
        now = utcnow()

        # Find expired but not yet marked transactions
        expired_transactions = session.exec(
            select(PointsTransaction)
            .where(PointsTransaction.is_expired == False)
            .where(PointsTransaction.expires_at.is_not(None))
            .where(PointsTransaction.expires_at <= now)
            .where(PointsTransaction.amount > 0)
        ).all()

        count = 0
        for tx in expired_transactions:
            tx.is_expired = True
            tx.expired_at = now
            session.add(tx)
            count += 1

        if count > 0:
            session.commit()

        log_with_context(
            logger,
            20,  # INFO
            "Expired stale points",
            count=count,
        )

        return count

    def award_skill_contribution(
        self,
        session: Session,
        author_id: str,
        public_skill_id: str,
    ) -> PointsTransaction | None:
        """
        核准社区技能时给作者发贡献积分（不提交，由调用方与核准放在同一事务里提交）。

        幂等：以 PublicSkill.id 作为 source_id，同一个公共技能只发一次；
        先锁作者的 user 行，避免并发核准时重复发放。

        Returns:
            新建的积分流水；已发过或积分配置为 0 时返回 None
        """
        if POINTS_SKILL_CONTRIBUTION <= 0:
            return None

        self._lock_user_row(session, author_id)
        already_awarded = session.exec(
            select(PointsTransaction.id)
            .where(PointsTransaction.user_id == author_id)
            .where(PointsTransaction.transaction_type == "skill_contribution")
            .where(PointsTransaction.source_id == public_skill_id)
            .limit(1)
        ).first()
        if already_awarded is not None:
            return None

        return self.earn_points(
            session,
            author_id,
            POINTS_SKILL_CONTRIBUTION,
            "skill_contribution",
            source_id=public_skill_id,
            description="Community skill approved",
            commit=False,
        )

    def get_earn_opportunities(self, session: Session, user_id: str) -> list[dict]:
        """
        Get available ways to earn points.

        Checks which opportunities are available and which are already completed.

        Args:
            session: Database session
            user_id: User ID

        Returns:
            List of opportunity dicts with type, points, description, is_completed, is_available
        """
        opportunities = []

        # Check-in opportunity
        check_in_status = self.get_check_in_status(session, user_id)
        opportunities.append({
            "type": "check_in",
            "points": POINTS_CHECK_IN,
            "description": "opportunity.check_in",
            "is_completed": check_in_status["checked_in"],
            "is_available": True,
        })

        # Streak bonus (if eligible today)
        if not check_in_status["checked_in"] and check_in_status["streak_days"] >= STREAK_BONUS_THRESHOLD - 1:
                opportunities.append({
                    "type": "check_in_streak",
                    "points": POINTS_CHECK_IN_STREAK,
                    "description": "opportunity.check_in_streak",
                    "is_completed": False,
                    "is_available": True,
                })

        # Referral opportunity
        # Check if user has any unused invite codes
        from models.referral import InviteCode
        invite_codes = session.exec(
            select(InviteCode)
            .where(InviteCode.owner_id == user_id)
            .where(InviteCode.is_active == True)
        ).all()

        opportunities.append({
            "type": "referral",
            "points": POINTS_REFERRAL,
            "description": "opportunity.referral",
            "is_completed": False,
            "is_available": len(invite_codes) > 0,
        })

        # Skill contribution: 积分在管理员核准社区技能时发放（award_skill_contribution），
        # 所以「已完成」以「有已核准的投稿或已领过贡献积分」为准，而不是送审（pending）。
        from models.public_skill import PublicSkill
        has_approved_contribution = session.exec(
            select(PublicSkill.id)
            .where(PublicSkill.author_id == user_id)
            .where(PublicSkill.source == "community")
            .where(PublicSkill.status == "approved")
            .limit(1)
        ).first() is not None
        has_contribution_reward = session.exec(
            select(PointsTransaction.id)
            .where(PointsTransaction.user_id == user_id)
            .where(PointsTransaction.transaction_type == "skill_contribution")
            .limit(1)
        ).first() is not None

        opportunities.append({
            "type": "skill_contribution",
            "points": POINTS_SKILL_CONTRIBUTION,
            "description": "opportunity.skill_contribution",
            "is_completed": has_approved_contribution or has_contribution_reward,
            "is_available": True,
        })

        # 灵感投稿、完善资料两张卡没有任何发放积分的代码路径，展示出来就是空头承诺，
        # 因此不再列出；对应的 POINTS_CONFIG 数值保留，等真正接入发放时再加回卡片。

        return opportunities

    def get_transaction_history(
        self,
        session: Session,
        user_id: str,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[PointsTransaction], int]:
        """
        Get paginated transaction history for a user.

        Args:
            session: Database session
            user_id: User ID
            page: Page number (1-indexed)
            page_size: Items per page

        Returns:
            Tuple of (transactions list, total count)
        """
        # Count total
        total = session.exec(
            select(func.count())
            .select_from(PointsTransaction)
            .where(PointsTransaction.user_id == user_id)
        ).one() or 0

        # Get paginated
        offset = (page - 1) * page_size
        transactions = session.exec(
            select(PointsTransaction)
            .where(PointsTransaction.user_id == user_id)
            .order_by(col(PointsTransaction.created_at).desc(), col(PointsTransaction.id).desc())
            .offset(offset)
            .limit(page_size)
        ).all()

        return list(transactions), total


# Singleton instance
points_service = PointsService()
