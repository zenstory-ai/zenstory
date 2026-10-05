"""
Admin Quota Usage Statistics API endpoints.

This module contains quota usage statistics endpoints for admin operations.
"""
import logging

from fastapi import APIRouter, Depends
from sqlmodel import Session, func, select

from core.error_codes import ErrorCode
from core.error_handler import APIException
from database import get_session
from models import User
from models.subscription import SubscriptionPlan, UsageQuota, UserSubscription
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
    Get quota usage statistics.

    Requires superuser privileges.
    """
    # Total material uploads (current period)
    material_uploads = session.exec(
        select(func.coalesce(func.sum(UsageQuota.material_uploads_used), 0))
    ).one() or 0

    # Total material decompositions
    material_decomposes = session.exec(
        select(func.coalesce(func.sum(UsageQuota.material_decompositions_used), 0))
    ).one() or 0

    # Total skill creates
    skill_creates = session.exec(
        select(func.coalesce(func.sum(UsageQuota.skill_creates_used), 0))
    ).one() or 0

    # Total inspiration copies
    inspiration_copies = session.exec(
        select(func.coalesce(func.sum(UsageQuota.inspiration_copies_used), 0))
    ).one() or 0

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

    # Get user's subscription
    resolved_user_id = user.id

    subscription = session.exec(
        select(UserSubscription).where(UserSubscription.user_id == resolved_user_id)
    ).first()

    # Get user's quota
    quota = session.exec(
        select(UsageQuota).where(UsageQuota.user_id == resolved_user_id)
    ).first()

    # Get plan info with defaults
    plan_name = "free"
    ai_conversations_limit = 20
    material_upload_limit = 5
    skill_create_limit = 3
    inspiration_copy_limit = 10

    if subscription:
        plan = session.get(SubscriptionPlan, subscription.plan_id)
        if plan:
            plan_name = plan.name
            features = plan.features or {}
            ai_conversations_limit = features.get("ai_conversations_per_day", ai_conversations_limit)
            material_upload_limit = features.get("material_uploads", material_upload_limit)
            skill_create_limit = quota_service.get_plan_feature(plan, "custom_skills")
            inspiration_copy_limit = quota_service.get_plan_feature(
                plan, "inspiration_copies_monthly"
            )

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
        ai_conversations_used=quota.ai_conversations_used if quota else 0,
        ai_conversations_limit=ai_conversations_limit,
        material_upload_used=quota.material_uploads_used if quota else 0,
        material_upload_limit=material_upload_limit,
        skill_create_used=quota_service.count_custom_skills(session, resolved_user_id),
        skill_create_limit=skill_create_limit,
        inspiration_copy_used=quota.inspiration_copies_used if quota else 0,
        inspiration_copy_limit=inspiration_copy_limit,
    )
