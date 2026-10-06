"""
Admin Points Management API endpoints.

This module contains points management endpoints for admin operations.
"""
import logging

from fastapi import APIRouter, Depends, Query, Request, status
from sqlmodel import Session, func, select

from core.error_codes import ErrorCode
from core.error_handler import APIException
from database import get_session
from models import User
from models.points import PointsTransaction
from services.admin_audit_service import admin_audit_service
from services.core.auth_service import get_current_superuser
from services.features.points_service import points_service
from utils.logger import get_logger, log_with_context

from .schemas import (
    AdminPointsAdjustRequest,
    AdminPointsBalance,
    PointsStatsResponse,
    PointsTransactionListResponse,
)

logger = get_logger(__name__)

router = APIRouter(tags=["admin-points"])


# ==================== Points Management ====================


def _resolve_user_identifier(session: Session, identifier: str) -> User | None:
    """Resolve a user by id, username, or email."""
    user = session.get(User, identifier)
    if user:
        return user
    return session.exec(
        select(User).where((User.username == identifier) | (User.email == identifier))
    ).first()


@router.get("/points/stats", response_model=PointsStatsResponse)
def get_points_stats(
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    Get points system statistics.

    Requires superuser privileges.
    """
    # Total points issued (sum of all positive transactions)
    total_issued = session.exec(
        select(func.coalesce(func.sum(PointsTransaction.amount), 0))
        .where(PointsTransaction.amount > 0)
    ).one() or 0

    # Total points spent (absolute value of negative transactions)
    total_spent = abs(session.exec(
        select(func.coalesce(func.sum(PointsTransaction.amount), 0))
        .where(PointsTransaction.amount < 0)
    ).one() or 0)

    # Total points expired
    total_expired = session.exec(
        select(func.coalesce(func.sum(PointsTransaction.amount), 0))
        .where(PointsTransaction.is_expired == True)
        .where(PointsTransaction.amount > 0)
    ).one() or 0

    # Active users with points (users with non-expired positive balance)
    _, users_with_balance = points_service.get_available_points_stats(session)

    log_with_context(
        logger,
        logging.INFO,
        "Retrieved points stats",
        user_id=current_user.id,
        total_issued=total_issued,
        total_spent=total_spent,
    )

    return PointsStatsResponse(
        total_points_issued=total_issued,
        total_points_spent=total_spent,
        total_points_expired=total_expired,
        active_users_with_points=users_with_balance,
    )


@router.get("/points/{user_id}", response_model=AdminPointsBalance)
def get_user_points_balance(
    user_id: str,
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    Get user's points balance details.

    Requires superuser privileges.
    """
    user = _resolve_user_identifier(session, user_id)
    if not user:
        raise APIException(
            error_code=ErrorCode.NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    resolved_user_id = user.id
    balance = points_service.get_balance(session, resolved_user_id)

    # Calculate total earned and spent
    total_earned = session.exec(
        select(func.coalesce(func.sum(PointsTransaction.amount), 0))
        .where(PointsTransaction.user_id == resolved_user_id)
        .where(PointsTransaction.amount > 0)
    ).one() or 0

    total_spent = abs(session.exec(
        select(func.coalesce(func.sum(PointsTransaction.amount), 0))
        .where(PointsTransaction.user_id == resolved_user_id)
        .where(PointsTransaction.amount < 0)
    ).one() or 0)

    log_with_context(
        logger,
        logging.INFO,
        "Retrieved user points balance",
        user_id=current_user.id,
        target_user_id=resolved_user_id,
        available=balance["available"],
    )

    return AdminPointsBalance(
        user_id=resolved_user_id,
        username=user.username,
        email=user.email,
        available=balance["available"],
        pending_expiration=balance["pending_expiration"],
        total_earned=total_earned,
        total_spent=total_spent,
    )


@router.get("/points/{user_id}/transactions", response_model=PointsTransactionListResponse)
def get_user_points_transactions(
    user_id: str,
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    Get user's points transaction history.

    Requires superuser privileges.
    """
    user = _resolve_user_identifier(session, user_id)
    if not user:
        raise APIException(
            error_code=ErrorCode.NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    resolved_user_id = user.id
    transactions, total = points_service.get_transaction_history(
        session, resolved_user_id, page, page_size
    )

    log_with_context(
        logger,
        logging.INFO,
        "Retrieved user points transactions",
        user_id=current_user.id,
        target_user_id=resolved_user_id,
        count=len(transactions),
    )

    return PointsTransactionListResponse(
        items=[t.model_dump() for t in transactions],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/points/{user_id}/adjust")
def adjust_user_points(
    user_id: str,
    request: AdminPointsAdjustRequest,
    http_request: Request,
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    Adjust user's points balance (admin operation).

    Positive amount adds points, negative deducts.
    Requires superuser privileges.
    """
    user = _resolve_user_identifier(session, user_id)
    if not user:
        raise APIException(
            error_code=ErrorCode.NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    resolved_user_id = user.id

    if request.amount == 0:
        raise APIException(
            error_code=ErrorCode.VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Amount cannot be zero",
        )

    old_balance = points_service.get_balance(session, resolved_user_id)

    if request.amount > 0:
        transaction = points_service.earn_points(
            session=session,
            user_id=resolved_user_id,
            amount=request.amount,
            transaction_type="admin_adjust",
            description=request.reason,
            commit=False,
        )
    else:
        transaction = points_service.spend_points(
            session=session,
            user_id=resolved_user_id,
            amount=abs(request.amount),
            transaction_type="admin_adjust",
            description=request.reason,
            commit=False,
        )

    new_balance = points_service.get_balance(session, resolved_user_id)

    try:
        admin_audit_service.log_action(
            session, current_user.id, "adjust_points", "points", resolved_user_id,
            old_value={"balance": old_balance["available"]},
            new_value={"balance": new_balance["available"], "amount": request.amount, "reason": request.reason},
            request=http_request, commit=False,
        )
        session.commit()
        session.refresh(transaction)
    except Exception:
        session.rollback()
        raise

    log_with_context(
        logger,
        logging.INFO,
        "Adjusted user points",
        user_id=current_user.id,
        target_user_id=resolved_user_id,
        amount=request.amount,
        reason=request.reason,
    )

    return {
        "success": True,
        "transaction": transaction.model_dump(),
        "old_balance": old_balance["available"],
        "new_balance": new_balance["available"],
    }
