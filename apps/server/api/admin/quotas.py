"""
Admin Quota Usage Statistics API endpoints.

This module contains quota usage statistics endpoints for admin operations.
"""
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import and_, or_
from sqlmodel import Session, func, select

from config.datetime_utils import BEIJING_TIMEZONE, normalize_datetime_to_utc, utcnow
from core.error_codes import ErrorCode
from core.error_handler import APIException
from database import get_session
from models import User
from models.subscription import UsageQuota
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


def _beijing_month_windows(
    value: datetime,
) -> tuple[tuple[datetime, datetime], tuple[datetime, datetime]]:
    """Return canonical and legacy-UTC windows for the Beijing calendar month."""
    local_start = normalize_datetime_to_utc(value).astimezone(BEIJING_TIMEZONE).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    if local_start.month == 12:
        local_end = local_start.replace(year=local_start.year + 1, month=1)
    else:
        local_end = local_start.replace(month=local_start.month + 1)
    return (
        (local_start.astimezone(UTC), local_end.astimezone(UTC)),
        (local_start.replace(tzinfo=UTC), local_end.replace(tzinfo=UTC)),
    )


@router.get("/quota/usage", response_model=QuotaUsageStatsResponse)
def get_quota_usage_stats(
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    Get quota usage statistics.

    Requires superuser privileges.
    """
    (period_start, period_end), (legacy_start, legacy_end) = _beijing_month_windows(
        utcnow()
    )
    # Monthly quota rows reset lazily. Count the current Beijing month in both
    # canonical and retained legacy-UTC form without mutating inactive users.
    (
        material_uploads,
        material_decomposes,
        skill_creates,
        inspiration_copies,
    ) = session.exec(
        select(
            func.coalesce(func.sum(UsageQuota.material_uploads_used), 0),
            func.coalesce(func.sum(UsageQuota.material_decompositions_used), 0),
            func.coalesce(func.sum(UsageQuota.skill_creates_used), 0),
            func.coalesce(func.sum(UsageQuota.inspiration_copies_used), 0),
        ).where(
            or_(
                and_(
                    UsageQuota.monthly_period_start == period_start,
                    UsageQuota.monthly_period_end == period_end,
                ),
                and_(
                    UsageQuota.monthly_period_start == legacy_start,
                    UsageQuota.monthly_period_end == legacy_end,
                ),
            )
        )
    ).one()

    log_with_context(
        logger,
        logging.INFO,
        "Retrieved quota usage stats",
        user_id=current_user.id,
        material_uploads=material_uploads,
        skill_creates=skill_creates,
    )

    return QuotaUsageStatsResponse(
        material_uploads=material_uploads,
        material_decomposes=material_decomposes,
        skill_creates=skill_creates,
        inspiration_copies=inspiration_copies,
    )


@router.get("/quota/{user_id}", response_model=UserQuotaDetail)
def get_user_quota_detail(
    user_id: str,
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    Get user's quota usage details.

    Requires superuser privileges.
    """
    user = _resolve_user_identifier(session, user_id)
    if not user:
        raise APIException(
            error_code=ErrorCode.NOT_FOUND,
            status_code=404,
            detail="User not found",
        )

    resolved_user_id = user.id
    plan = quota_service.get_user_plan(session, resolved_user_id)
    snapshot = quota_service.get_quota_snapshot(session, resolved_user_id, plan=plan)
    plan_name = plan.name

    log_with_context(
        logger,
        logging.INFO,
        "Retrieved user quota detail",
        user_id=current_user.id,
        target_user_id=resolved_user_id,
        plan_name=plan_name,
    )

    return UserQuotaDetail(
        user_id=resolved_user_id,
        username=user.username,
        plan_name=plan_name,
        ai_conversations_used=snapshot["ai_conversations"]["used"],
        ai_conversations_limit=snapshot["ai_conversations"]["limit"],
        material_upload_used=snapshot["material_uploads"]["used"],
        material_upload_limit=snapshot["material_uploads"]["limit"],
        material_decompose_used=snapshot["material_decompositions"]["used"],
        material_decompose_limit=snapshot["material_decompositions"]["limit"],
        skill_create_used=snapshot["skill_creates"]["used"],
        skill_create_limit=snapshot["skill_creates"]["limit"],
        inspiration_copy_used=snapshot["inspiration_copies"]["used"],
        inspiration_copy_limit=snapshot["inspiration_copies"]["limit"],
    )
