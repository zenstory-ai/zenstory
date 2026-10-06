"""
Admin Redemption Code Management API endpoints.

This module contains all redemption code management endpoints for admin operations.
"""
import logging

from fastapi import APIRouter, Depends, Query, Request, status
from sqlmodel import Session, func, select

from core.error_codes import ErrorCode
from core.error_handler import APIException
from database import get_session
from middleware.rate_limit import check_rate_limit
from models import User
from models.subscription import RedemptionCode, SubscriptionPlan
from services.admin_audit_service import admin_audit_service
from services.core.auth_service import get_current_superuser
from services.subscription.redemption_service import redemption_service
from utils.code_generator import generate_batch_codes, generate_code
from utils.logger import get_logger, log_with_context

from .schemas import CodeBatchCreateRequest, CodeCreateRequest, CodeListResponse, CodeUpdateRequest

logger = get_logger(__name__)

router = APIRouter(tags=["admin-codes"])
CODE_TYPE_ALIASES = {
    "single": "single_use",
    "multi": "multi_use",
}
VALID_CODE_TYPES = {"single_use", "multi_use"}


def _normalize_code_type(code_type: str) -> str:
    normalized = CODE_TYPE_ALIASES.get(code_type, code_type)
    if normalized not in VALID_CODE_TYPES:
        raise APIException(
            error_code=ErrorCode.VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid code_type. Use single_use or multi_use.",
        )
    return normalized


def _raise_code_generator_config_error(exc: ValueError) -> None:
    """Convert redemption code generator configuration errors to APIException."""
    message = str(exc)
    if "REDEMPTION_CODE_HMAC_SECRET" not in message:
        raise exc

    log_with_context(
        logger,
        logging.ERROR,
        "Redemption code generator misconfigured",
        error=message,
    )
    raise APIException(
        error_code=ErrorCode.SERVICE_UNAVAILABLE,
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=message,
    ) from exc


def _validate_tier_exists(session: Session, tier: str) -> None:
    if tier == "free":
        # A free-tier code would replace a paid plan and discard its remaining days.
        raise APIException(
            error_code=ErrorCode.VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Redemption codes cannot grant the free tier",
        )
    plan = session.exec(
        select(SubscriptionPlan).where(SubscriptionPlan.name == tier)
    ).first()
    if not plan:
        raise APIException(
            error_code=ErrorCode.VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid tier: {tier}",
        )


# ==================== Code Management ====================


@router.get("/codes", response_model=CodeListResponse)
def list_codes(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    tier: str | None = Query(None, description="Filter by tier"),
    is_active: bool | None = Query(None, description="Filter by active status"),
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    List redemption codes with pagination.

    Requires superuser privileges.
    """
    query = select(RedemptionCode)

    if tier:
        query = query.where(RedemptionCode.tier == tier)
    if is_active is not None:
        query = query.where(RedemptionCode.is_active == is_active)

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total = session.exec(count_query).one()

    # Apply pagination
    query = query.order_by(RedemptionCode.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    codes = session.exec(query).all()

    log_with_context(
        logger,
        logging.INFO,
        "Retrieved redemption codes list",
        user_id=current_user.id,
        count=len(codes),
        page=page,
        tier=tier,
        is_active=is_active,
    )

    return CodeListResponse(
        items=[code.model_dump() for code in codes],
        total=total,
        page=page,
        page_size=page_size
    )


@router.post("/codes")
def create_code(
    request: CodeCreateRequest,
    http_request: Request,
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    Create a single redemption code.

    Requires superuser privileges.
    """
    # Check rate limit
    allowed, _ = check_rate_limit(http_request, "admin_create_code", 10, 60)
    if not allowed:
        raise APIException(
            error_code=ErrorCode.AUTH_RATE_LIMIT_EXCEEDED,
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
        )

    normalized_code_type = _normalize_code_type(request.code_type)
    max_uses = redemption_service.resolve_max_uses(normalized_code_type, request.max_uses)
    _validate_tier_exists(session, request.tier)
    try:
        code = generate_code(request.tier, request.duration_days, normalized_code_type)
    except ValueError as exc:
        _raise_code_generator_config_error(exc)

    redemption = RedemptionCode(
        code=code,
        code_type=normalized_code_type,
        tier=request.tier,
        duration_days=request.duration_days,
        max_uses=max_uses,
        created_by=current_user.id,
        notes=request.notes
    )
    session.add(redemption)
    try:
        session.flush()
        admin_audit_service.log_action(
            session, current_user.id, "create_code", "code", redemption.id,
            new_value={
                "tier": request.tier,
                "duration_days": request.duration_days,
                "code_type": normalized_code_type,
                "max_uses": max_uses,
            },
            request=http_request, commit=False,
        )
        session.commit()
        session.refresh(redemption)
    except Exception:
        session.rollback()
        raise

    log_with_context(
        logger,
        logging.INFO,
        "Created redemption code",
        user_id=current_user.id,
        code_id=redemption.id,
        tier=request.tier,
    )

    return redemption.model_dump()


@router.post("/codes/batch")
def create_codes_batch(
    request: CodeBatchCreateRequest,
    http_request: Request,
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    Create multiple redemption codes.

    Requires superuser privileges.
    """
    # Check rate limit
    allowed, _ = check_rate_limit(http_request, "admin_create_codes_batch", 5, 60)
    if not allowed:
        raise APIException(
            error_code=ErrorCode.AUTH_RATE_LIMIT_EXCEEDED,
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
        )

    normalized_code_type = _normalize_code_type(request.code_type)
    max_uses = redemption_service.resolve_max_uses(normalized_code_type, request.max_uses)
    _validate_tier_exists(session, request.tier)
    try:
        codes = generate_batch_codes(
            request.tier, request.duration_days, request.count, normalized_code_type
        )
    except ValueError as exc:
        _raise_code_generator_config_error(exc)

    redemptions = []
    for code in codes:
        redemption = RedemptionCode(
            code=code,
            code_type=normalized_code_type,
            tier=request.tier,
            duration_days=request.duration_days,
            max_uses=max_uses,
            created_by=current_user.id,
            notes=request.notes
        )
        session.add(redemption)
        redemptions.append(redemption)

    try:
        session.flush()
        admin_audit_service.log_action(
            session, current_user.id, "create_codes_batch", "code", None,
            new_value={
                "tier": request.tier,
                "duration_days": request.duration_days,
                "count": len(redemptions),
                "code_type": normalized_code_type,
                "max_uses": max_uses,
                "code_ids": [redemption.id for redemption in redemptions],
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
        "Created redemption codes batch",
        user_id=current_user.id,
        count=request.count,
        tier=request.tier,
    )

    return {
        "created": len(redemptions),
        "count": len(redemptions),
        "codes": codes,
        "code_type": normalized_code_type,
        "max_uses": max_uses,
    }


@router.get("/codes/{code_id}")
def get_code(
    code_id: str,
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    Get code details.

    Requires superuser privileges.
    """
    code = session.exec(
        select(RedemptionCode).where(RedemptionCode.id == code_id)
    ).first()
    if not code:
        raise APIException(
            error_code=ErrorCode.NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Code not found"
        )

    log_with_context(
        logger,
        logging.INFO,
        "Retrieved code details",
        user_id=current_user.id,
        code_id=code_id,
    )

    return code.model_dump()


@router.put("/codes/{code_id}")
def update_code(
    code_id: str,
    request: CodeUpdateRequest,
    http_request: Request,
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    Update a code (disable/enable, update notes).

    Requires superuser privileges.
    """
    code = session.exec(
        select(RedemptionCode).where(RedemptionCode.id == code_id).with_for_update()
    ).first()
    if not code:
        raise APIException(
            error_code=ErrorCode.NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Code not found"
        )

    old_value = {"is_active": code.is_active, "notes": code.notes}

    if request.is_active is not None:
        code.is_active = request.is_active
    if request.notes is not None:
        code.notes = request.notes

    session.add(code)
    try:
        admin_audit_service.log_action(
            session, current_user.id, "update_code", "code", code_id,
            old_value=old_value,
            new_value={"is_active": code.is_active, "notes": code.notes},
            request=http_request, commit=False,
        )
        session.commit()
        session.refresh(code)
    except Exception:
        session.rollback()
        raise

    log_with_context(
        logger,
        logging.INFO,
        "Updated code",
        user_id=current_user.id,
        code_id=code_id,
    )

    return code.model_dump()
