"""
Admin Quota Usage Statistics API endpoints.

This module contains quota usage statistics endpoints for admin operations.
"""
import logging

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from core.error_codes import ErrorCode
from core.error_handler import APIException
from database import get_session
from models import User
from services.core.auth_service import get_current_superuser
from services.quota_service import quota_service
from utils.logger import get_logger, log_with_context

from .schemas import (
    QuotaUsageStatsResponse,
    UserQuotaDetail,
)

logger = get_logger(__name__)

router = APIRouter(tags=["admin-quotas"])


# ==================== Quota Usage Stats ====================


def _resolve_user_identifier(session: Session, identifier: str) -> User | None:
    """Resolve a user by id, username, or email."""
    user = session.get(User, identifier)
    if user:
        return user
    return session.exec(
        select(User).where((User.username == identifier) | (User.email == identifier))
    ).first()


@router.get("/quota/usage", response_model=QuotaUsageStatsResponse)
def get_quota_usage_stats(
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    Monthly quota usage summed over the current period.

    Requires superuser privileges.
    """
    totals = quota_service.get_current_month_totals(session)

    log_with_context(
        logger,
        logging.INFO,
        "Retrieved quota usage stats",
        user_id=current_user.id,
        material_decompositions=totals["material_decompositions"],
    )

    return QuotaUsageStatsResponse(**totals)


@router.get("/quota/{user_id}", response_model=UserQuotaDetail)
def get_user_quota_detail(
    user_id: str,
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    Get one user's quota (accepts user id, username or email).

    Requires superuser privileges.
    """
    user = _resolve_user_identifier(session, user_id)
    if not user:
        raise APIException(
            error_code=ErrorCode.NOT_FOUND,
            status_code=404,
            detail="User not found",
        )

    view = quota_service.get_admin_quota_view(session, user.id)

    log_with_context(
        logger,
        logging.INFO,
        "Retrieved user quota detail",
        user_id=current_user.id,
        target_user_id=user.id,
        plan_name=view["plan_name"],
    )

    return UserQuotaDetail(user_id=user.id, username=user.username, email=user.email, **view)
