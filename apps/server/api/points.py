"""
Points API - User-facing points and check-in endpoints.

Provides endpoints for:
- Getting points balance
- Daily check-in
- Points transaction history
- Points redemption for Pro
- Earn opportunities
"""
import logging
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

from core.error_codes import ErrorCode
from core.error_handler import APIException
from database import get_session
from models.entities import User
from services.core.auth_service import get_current_active_user
from services.features.points_service import (
    POINTS_CHECK_IN,
    POINTS_CHECK_IN_STREAK,
    POINTS_INSPIRATION_CONTRIBUTION,
    POINTS_PRO_7DAYS_COST,
    POINTS_PROFILE_COMPLETE,
    POINTS_REFERRAL,
    POINTS_SKILL_CONTRIBUTION,
    STREAK_BONUS_THRESHOLD,
    points_service,
)
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)


router = APIRouter(prefix="/api/v1/points", tags=["points"])


# ============== Schemas ==============

class PointsBalanceResponse(BaseModel):
    """Response for points balance endpoint."""
    available: int
    pending_expiration: int
    nearest_expiration_date: str | None = None


class CheckInResponse(BaseModel):
    """Response for check-in endpoint."""
    success: bool
    points_earned: int
    streak_days: int
    message: str


class CheckInStatusResponse(BaseModel):
    """Response for check-in status endpoint."""
    checked_in: bool
    streak_days: int
    points_earned_today: int


class TransactionItem(BaseModel):
    """Single transaction item for history."""
    id: str
    amount: int
    balance_after: int
    transaction_type: str
    source_id: str | None = None
    description: str | None = None
    expires_at: str | None = None
    is_expired: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TransactionHistoryResponse(BaseModel):
    """Response for paginated transaction history."""
    transactions: list[TransactionItem]
    total: int
    page: int
    page_size: int
    total_pages: int


class EarnOpportunityResponse(BaseModel):
    """Single earn opportunity."""
    type: str
    points: int
    description: str
    is_completed: bool
    is_available: bool


class RedeemProRequest(BaseModel):
    """Request for redeeming points for Pro."""
    days: Literal[7, 14, 30] = Field(default=7, description="Supported Pro redemption days: 7, 14, 30")


class RedeemProResponse(BaseModel):
    """Response for points redemption."""
    success: bool
    points_spent: int
    pro_days: int
    new_period_end: str


class PointsConfigResponse(BaseModel):
    """Response for points configuration (public endpoint)."""
    check_in: int
    check_in_streak: int
    referral: int
    skill_contribution: int
    inspiration_contribution: int
    profile_complete: int
    pro_7days_cost: int
    streak_bonus_threshold: int


# ============== Endpoints ==============

@router.get("/balance", response_model=PointsBalanceResponse)
def get_balance(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user)
):
    """Get current user's points balance."""
    balance = points_service.get_balance(session, current_user.id)

    return PointsBalanceResponse(
        available=balance["available"],
        pending_expiration=balance["pending_expiration"],
        nearest_expiration_date=balance["nearest_expiration_date"],
    )


@router.post("/check-in", response_model=CheckInResponse)
def check_in(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user)
):
    """
    Perform daily check-in.

    Awards base points plus streak bonus if eligible.
    One check-in per day per user.
    """
    result = points_service.check_in(session, current_user.id)

    return CheckInResponse(
        success=result["success"],
        points_earned=result["points_earned"],
        streak_days=result["streak_days"],
        message=result["message"],
    )


@router.get("/check-in/status", response_model=CheckInStatusResponse)
def get_check_in_status(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user)
):
    """Get current user's check-in status for today."""
    status = points_service.get_check_in_status(session, current_user.id)

    return CheckInStatusResponse(
        checked_in=status["checked_in"],
        streak_days=status["streak_days"],
        points_earned_today=status["points_earned_today"],
    )


@router.get("/transactions", response_model=TransactionHistoryResponse)
def get_transactions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user)
):
    """
    Get paginated transaction history.

    Args:
        page: Page number (1-indexed)
        page_size: Items per page (default 20, max 100)
    """
    transactions, total = points_service.get_transaction_history(
        session, current_user.id, page, page_size
    )

    total_pages = (total + page_size - 1) // page_size

    return TransactionHistoryResponse(
        transactions=[
            TransactionItem(
                id=t.id,
                amount=t.amount,
                balance_after=t.balance_after,
                transaction_type=t.transaction_type,
                source_id=t.source_id,
                description=t.description,
                expires_at=t.expires_at.isoformat() if t.expires_at else None,
                is_expired=t.is_expired,
                created_at=t.created_at,
            )
            for t in transactions
        ],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.post("/redeem", response_model=RedeemProResponse)
def redeem_for_pro(
    request: RedeemProRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user)
):
    """
    Redeem points for Pro subscription days.

    Supported redemption durations: 7, 14, and 30 days.
    Points are deducted immediately upon successful redemption.
    """
    result = points_service.redeem_for_pro(session, current_user.id, request.days)

    return RedeemProResponse(
        success=result["success"],
        points_spent=result["points_spent"],
        pro_days=result["pro_days"],
        new_period_end=result["new_period_end"],
    )


@router.get("/earn-opportunities", response_model=list[EarnOpportunityResponse])
def get_earn_opportunities(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user)
):
    """
    Get available ways to earn points.

    Returns list of opportunities with their status (completed, available).
    """
    try:
        opportunities = points_service.get_earn_opportunities(session, current_user.id)

        return [
            EarnOpportunityResponse(
                type=opp["type"],
                points=opp["points"],
                description=opp["description"],
                is_completed=opp["is_completed"],
                is_available=opp["is_available"],
            )
            for opp in opportunities
        ]
    except Exception as e:
        log_with_context(
            logger,
            logging.ERROR,
            "Failed to get earn opportunities",
            error_type=type(e).__name__,
            error_message=str(e),
            user_id=current_user.id,
        )
        raise APIException(
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        ) from e


@router.get("/config", response_model=PointsConfigResponse)
async def get_config():
    """Get points configuration (public endpoint, no auth required)."""
    return PointsConfigResponse(
        check_in=POINTS_CHECK_IN,
        check_in_streak=POINTS_CHECK_IN_STREAK,
        referral=POINTS_REFERRAL,
        skill_contribution=POINTS_SKILL_CONTRIBUTION,
        inspiration_contribution=POINTS_INSPIRATION_CONTRIBUTION,
        profile_complete=POINTS_PROFILE_COMPLETE,
        pro_7days_cost=POINTS_PRO_7DAYS_COST,
        streak_bonus_threshold=STREAK_BONUS_THRESHOLD,
    )
