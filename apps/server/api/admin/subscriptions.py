"""
Admin Subscription Management API endpoints.

This module contains all subscription management endpoints for admin operations.
"""
import logging
from datetime import UTC, timedelta

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import and_, or_
from sqlmodel import Session, col, func, select

from config.datetime_utils import utcnow
from core.error_codes import ErrorCode
from core.error_handler import APIException
from database import get_session
from models import User
from models.subscription import SubscriptionPlan, UsageQuota, UserSubscription
from services.admin_audit_service import admin_audit_service
from services.core.auth_service import get_current_superuser
from services.subscription.defaults import DEFAULT_FREE_PLAN_DISPLAY_NAME, DEFAULT_FREE_PLAN_DISPLAY_NAME_EN
from services.subscription.subscription_service import subscription_service
from utils.logger import get_logger, log_with_context

from .schemas import SubscriptionListResponse, SubscriptionUpdateRequest

logger = get_logger(__name__)

router = APIRouter(tags=["admin-subscriptions"])
STATUS_ALIASES = {
    "active": "active",
    "expired": "expired",
    "past_due": "expired",
    "cancelled": "cancelled",
    "canceled": "cancelled",
}
FREE_SUBSCRIPTION_DAYS = 36500


def _normalize_status(status_value: str) -> str:
    normalized = STATUS_ALIASES.get(status_value.lower())
    if not normalized:
        raise APIException(
            error_code=ErrorCode.VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid subscription status: {status_value}",
        )
    return normalized


def _is_test_account(user: User) -> bool:
    token_source = f"{user.username} {user.email}".lower()
    if user.email.lower().endswith("@example.com"):
        return True
    return any(token in token_source for token in ("test", "smoke", "qa", "demo"))


def _resolve_effective_status(sub: UserSubscription | None) -> str:
    """Resolve semantic status used by UI filtering/display."""
    if not sub:
        return "active"

    normalized = STATUS_ALIASES.get(sub.status.lower(), sub.status.lower())
    if normalized != "active":
        return normalized

    period_end = sub.current_period_end
    if period_end.tzinfo is None:
        period_end = period_end.replace(tzinfo=UTC)

    return "expired" if period_end <= utcnow() else "active"


# ==================== Subscription Management ====================


@router.get("/subscriptions", response_model=SubscriptionListResponse)
def list_subscriptions(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    status_filter: str | None = Query(None, alias="status", description="Filter by status"),
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    List all subscriptions.

    Requires superuser privileges.
    """
    free_plan = session.exec(
        select(SubscriptionPlan).where(SubscriptionPlan.name == "free")
    ).first()

    normalized_status = _normalize_status(status_filter) if status_filter else None
    now = utcnow()

    query = (
        select(User, UserSubscription, SubscriptionPlan)
        .select_from(User)
        .outerjoin(UserSubscription, UserSubscription.user_id == User.id)
        .outerjoin(SubscriptionPlan, SubscriptionPlan.id == UserSubscription.plan_id)
    )

    if normalized_status == "active":
        query = query.where(
            or_(
                UserSubscription.id.is_(None),
                and_(
                    UserSubscription.status == "active",
                    UserSubscription.current_period_end > now,
                ),
            )
        )
    elif normalized_status == "expired":
        query = query.where(
            and_(
                UserSubscription.id.is_not(None),
                or_(
                    UserSubscription.status.in_(["expired", "past_due"]),
                    and_(
                        UserSubscription.status == "active",
                        UserSubscription.current_period_end <= now,
                    ),
                ),
            )
        )
    elif normalized_status == "cancelled":
        query = query.where(
            and_(
                UserSubscription.id.is_not(None),
                UserSubscription.status.in_(["cancelled", "canceled"]),
            )
        )

    total = session.exec(
        select(func.count()).select_from(query.subquery())
    ).one()

    rows = session.exec(
        query.order_by(col(User.created_at).desc(), col(User.id).desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    # Enrich with user and plan info
    result = []
    for user, sub, plan in rows:
        is_test_account = _is_test_account(user)
        effective_status = _resolve_effective_status(sub)

        if sub:
            status_value = STATUS_ALIASES.get(sub.status.lower(), sub.status)
            result.append({
                **sub.model_dump(),
                "status": status_value,
                "username": user.username,
                "email": user.email,
                "plan_name": plan.name if plan else "free",
                "plan_display_name": plan.display_name if plan else DEFAULT_FREE_PLAN_DISPLAY_NAME,
                "plan_display_name_en": (
                    plan.display_name_en
                    if plan and plan.display_name_en
                    else DEFAULT_FREE_PLAN_DISPLAY_NAME_EN
                ),
                "effective_status": effective_status,
                "effective_plan_name": plan.name if plan else "free",
                "effective_plan_display_name": plan.display_name if plan else DEFAULT_FREE_PLAN_DISPLAY_NAME,
                "effective_plan_display_name_en": (
                    plan.display_name_en
                    if plan and plan.display_name_en
                    else DEFAULT_FREE_PLAN_DISPLAY_NAME_EN
                ),
                "has_subscription_record": True,
                "is_test_account": is_test_account,
            })
            continue

        inferred_period_end = user.created_at + timedelta(days=FREE_SUBSCRIPTION_DAYS)
        result.append({
            "id": f"virtual-{user.id}",
            "user_id": user.id,
            "status": "active",
            "current_period_start": user.created_at,
            "current_period_end": inferred_period_end,
            "cancel_at_period_end": False,
            "stripe_subscription_id": None,
            "stripe_customer_id": None,
            "created_at": user.created_at,
            "updated_at": user.updated_at,
            "username": user.username,
            "email": user.email,
            "plan_name": free_plan.name if free_plan else "free",
            "plan_display_name": free_plan.display_name if free_plan else DEFAULT_FREE_PLAN_DISPLAY_NAME,
            "plan_display_name_en": (
                free_plan.display_name_en
                if free_plan and free_plan.display_name_en
                else DEFAULT_FREE_PLAN_DISPLAY_NAME_EN
            ),
            "effective_status": effective_status,
            "effective_plan_name": free_plan.name if free_plan else "free",
            "effective_plan_display_name": free_plan.display_name if free_plan else DEFAULT_FREE_PLAN_DISPLAY_NAME,
            "effective_plan_display_name_en": (
                free_plan.display_name_en
                if free_plan and free_plan.display_name_en
                else DEFAULT_FREE_PLAN_DISPLAY_NAME_EN
            ),
            "has_subscription_record": False,
            "is_test_account": is_test_account,
        })

    log_with_context(
        logger,
        logging.INFO,
        "Retrieved subscriptions list",
        user_id=current_user.id,
        count=len(result),
        page=page,
    )

    return SubscriptionListResponse(
        items=result,
        total=total,
        page=page,
        page_size=page_size
    )


@router.get("/subscriptions/{user_id}")
def get_user_subscription(
    user_id: str,
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    Get a user's subscription details.

    Requires superuser privileges.
    """
    sub = session.exec(
        select(UserSubscription).where(UserSubscription.user_id == user_id)
    ).first()
    if not sub:
        raise APIException(
            error_code=ErrorCode.NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription not found"
        )

    plan = session.get(SubscriptionPlan, sub.plan_id)
    quota = session.exec(
        select(UsageQuota).where(UsageQuota.user_id == user_id)
    ).first()

    log_with_context(
        logger,
        logging.INFO,
        "Retrieved user subscription",
        user_id=current_user.id,
        target_user_id=user_id,
    )

    return {
        "subscription": sub.model_dump(),
        "plan": plan.model_dump() if plan else None,
        "quota": quota.model_dump() if quota else None
    }


@router.put("/subscriptions/{user_id}")
def update_user_subscription(
    user_id: str,
    request: SubscriptionUpdateRequest,
    http_request: Request,
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    Modify a user's subscription.

    Requires superuser privileges.
    """
    user = session.get(User, user_id)
    if not user:
        raise APIException(
            error_code=ErrorCode.NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    sub = session.exec(
        select(UserSubscription).where(UserSubscription.user_id == user_id)
    ).first()
    if not sub:
        subscription_service.ensure_user_subscription_and_quota(
            session=session,
            user_id=user_id,
            source="admin_update_bootstrap",
            commit=False,
        )
        sub = session.exec(
            select(UserSubscription).where(UserSubscription.user_id == user_id)
        ).first()
        if not sub:
            raise APIException(
                error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to bootstrap missing subscription record",
            )

    old_plan = session.get(SubscriptionPlan, sub.plan_id)
    old_value = {
        "plan": old_plan.name if old_plan else None,
        "status": sub.status,
        "period_end": str(sub.current_period_end)
    }

    has_plan = request.plan_name is not None
    has_duration = request.duration_days is not None
    normalized_status = _normalize_status(request.status) if request.status else None

    if has_plan != has_duration:
        raise APIException(
            error_code=ErrorCode.VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="plan_name and duration_days must be provided together",
        )

    has_changes = False

    if has_plan and request.plan_name and request.duration_days:
        sub = subscription_service.upgrade_subscription(
            session, user_id, request.plan_name, request.duration_days,
            metadata={"source": "admin_update", "admin_id": current_user.id},
            commit=False,
        )
        has_changes = True

    if normalized_status:
        if sub.status != normalized_status:
            sub.status = normalized_status
            sub.updated_at = utcnow()
            session.add(sub)
        has_changes = True

    if not has_changes:
        raise APIException(
            error_code=ErrorCode.VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No valid update fields provided",
        )

    try:
        admin_audit_service.log_action(
            session, current_user.id, "update_subscription", "subscription", user_id,
            old_value=old_value,
            new_value={
                "plan_name": request.plan_name,
                "duration_days": request.duration_days,
                "status": normalized_status,
            },
            request=http_request, commit=False,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise

    log_with_context(
        logger,
        logging.INFO,
        "Updated user subscription",
        user_id=current_user.id,
        target_user_id=user_id,
        plan_name=request.plan_name,
    )

    return {"success": True}
