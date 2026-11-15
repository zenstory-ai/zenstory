"""
Admin Subscription Plan Management API endpoints.

This module contains all subscription plan management endpoints for admin operations.
"""
import logging

from fastapi import APIRouter, Depends, Request, status
from sqlmodel import Session, select

from api.subscription import _normalize_plan_features_for_response
from config.datetime_utils import utcnow
from core.error_codes import ErrorCode
from core.error_handler import APIException
from database import get_session
from models import User
from models.subscription import SubscriptionPlan
from services.admin_audit_service import admin_audit_service
from services.core.auth_service import get_current_superuser
from services.subscription.defaults import FIXED_PLAN_FEATURES
from utils.logger import get_logger, log_with_context

from .schemas import AdminPlanResponse, PlanUpdateRequest

logger = get_logger(__name__)

router = APIRouter(tags=["admin-plans"])


# ==================== Plan Management ====================


@router.get("/plans", response_model=list[AdminPlanResponse])
def list_plans(
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    List all subscription plans.

    Requires superuser privileges.
    """
    plans = session.exec(select(SubscriptionPlan)).all()

    log_with_context(
        logger,
        logging.INFO,
        "Retrieved subscription plans list",
        user_id=current_user.id,
        count=len(plans),
    )

    # 存储的 features 可能缺键、带旧格式或被代码覆盖（免费版每日次数）：卡片显示
    # 与 /plans 同一套解析后的有效权益，编辑框仍用原始 features。
    return [
        AdminPlanResponse(
            **plan.model_dump(),
            effective_features=_normalize_plan_features_for_response(plan.features, plan.name),
        )
        for plan in plans
    ]


@router.put("/plans/{plan_id}", response_model=SubscriptionPlan)
def update_plan(
    plan_id: str,
    request: PlanUpdateRequest,
    http_request: Request,
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    Update a subscription plan.

    Requires superuser privileges.
    """
    plan = session.get(SubscriptionPlan, plan_id)
    if not plan:
        raise APIException(
            error_code=ErrorCode.NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plan not found"
        )

    features = None
    if request.features is not None:
        if any(getattr(request.features, key) is None for key in request.features.model_fields_set):
            raise APIException(error_code=ErrorCode.VALIDATION_ERROR, status_code=422)
        features = request.features.model_dump(exclude_unset=True)
        for key, fixed_limit in FIXED_PLAN_FEATURES.get(plan.name, {}).items():
            if features.get(key, fixed_limit) != fixed_limit:
                raise APIException(
                    error_code=ErrorCode.VALIDATION_ERROR, status_code=422,
                    detail=f"{key} 套餐 {plan.name} 固定为 {fixed_limit}；该策略须通过代码发布调整。",
                )
            features[key] = fixed_limit

    # Store old values for audit log
    old_value = {
        "display_name": plan.display_name,
        "display_name_en": plan.display_name_en,
        "price_monthly_cents": plan.price_monthly_cents,
        "price_yearly_cents": plan.price_yearly_cents,
        "features": plan.features,
        "is_active": plan.is_active,
    }

    # Update fields if provided
    if request.display_name is not None:
        plan.display_name = request.display_name
    if request.display_name_en is not None:
        plan.display_name_en = request.display_name_en
    if request.price_monthly_cents is not None:
        plan.price_monthly_cents = request.price_monthly_cents
    if request.price_yearly_cents is not None:
        plan.price_yearly_cents = request.price_yearly_cents
    if features is not None:
        plan.features = features
    if request.is_active is not None:
        plan.is_active = request.is_active

    plan.updated_at = utcnow()

    session.add(plan)

    # Audit log
    new_value = {
        "display_name": plan.display_name,
        "display_name_en": plan.display_name_en,
        "price_monthly_cents": plan.price_monthly_cents,
        "price_yearly_cents": plan.price_yearly_cents,
        "features": plan.features,
        "is_active": plan.is_active,
    }
    try:
        admin_audit_service.log_action(
            session, current_user.id, "update_plan", "plan", plan_id,
            old_value=old_value, new_value=new_value, request=http_request, commit=False,
        )
        session.commit()
        session.refresh(plan)
    except Exception:
        session.rollback()
        raise

    log_with_context(
        logger,
        logging.INFO,
        "Updated subscription plan",
        user_id=current_user.id,
        plan_id=plan_id,
        plan_name=plan.name,
    )

    return plan
