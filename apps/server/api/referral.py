import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict
from services.auth import get_current_active_user
from sqlmodel import Session, select

from core.error_codes import ErrorCode
from core.error_handler import APIException
from core.i18n import get_accept_language
from database import get_session
from middleware.rate_limit import check_rate_limit
from models import User
from models.referral import (
    InviteCode,
    UserReward,
)
from services.features.referral_service import (
    create_invite_code as create_invite_code_service,
)
from services.features.referral_service import (
    get_user_invite_codes as get_user_invite_codes_service,
)
from services.features.referral_service import (
    get_user_referral_stats as get_user_referral_stats_service,
)
from services.features.referral_service import (
    validate_invite_code as validate_invite_code_service,
)
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/referral", tags=["referral"])


# ==================== Request/Response Schemas ====================


class InviteCodeResponse(BaseModel):
    """Response schema for invite code."""

    id: str
    code: str
    max_uses: int
    current_uses: int
    is_active: bool
    expires_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CreateInviteCodeResponse(BaseModel):
    """Response schema for creating a new invite code."""

    id: str
    code: str
    max_uses: int
    current_uses: int
    is_active: bool
    expires_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ValidateCodeResponse(BaseModel):
    """Response schema for validating an invite code."""

    valid: bool
    message: str


class ReferralStatsResponse(BaseModel):
    """Response schema for referral statistics."""

    total_invites: int
    successful_invites: int
    total_points: int
    available_points: int


class RewardResponse(BaseModel):
    """Response schema for user rewards."""

    id: str
    reward_type: str
    amount: int
    source: str
    is_used: bool
    expires_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ==================== API Endpoints ====================


@router.get("/codes", response_model=list[InviteCodeResponse])
def get_invite_codes(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    """
    Get all invite codes for the current user.

    Returns a list of all invite codes owned by the current user,
    including active and expired codes.
    """
    log_with_context(
        logger,
        logging.INFO,
        "Fetching invite codes",
        user_id=current_user.id,
    )

    return get_user_invite_codes_service(current_user.id, session)


@router.post("/codes", response_model=CreateInviteCodeResponse, status_code=status.HTTP_201_CREATED)
def create_invite_code(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
    _accept_language: str = Depends(get_accept_language),
):
    """
    Create a new invite code.

    Each user can create up to 3 active invite codes by default.
    Superusers are not subject to this limit.
    Generated code format: XXXX-XXXX
    """
    log_with_context(
        logger,
        logging.INFO,
        "Creating new invite code",
        user_id=current_user.id,
    )

    new_code = create_invite_code_service(
        current_user.id,
        session,
        ignore_max_limit=current_user.is_superuser,
    )

    log_with_context(
        logger,
        logging.INFO,
        "Invite code created successfully",
        user_id=current_user.id,
        code=new_code.code,
    )

    return new_code


@router.post("/codes/{code}/validate", response_model=ValidateCodeResponse)
def validate_invite_code(
    code: str,
    request: Request,
    session: Session = Depends(get_session),
    _accept_language: str = Depends(get_accept_language),
):
    """
    Validate an invite code.

    This endpoint is public (no authentication required) and is used
    during registration to verify the invite code is valid.

    Validation checks:
    - Code exists
    - Code is active
    - Code has not reached max uses
    - Code has not expired
    """
    log_with_context(
        logger,
        logging.INFO,
        "Validating invite code",
        code=code,
    )

    # Public endpoint with IP-based throttling to reduce brute-force enumeration.
    allowed, _ = check_rate_limit(
        request=request,
        key="public_invite_code_validate",
        max_requests=30,
        window_seconds=60,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Please try again later.",
        )

    is_valid, _, _ = validate_invite_code_service(code, session)
    if not is_valid:
        return ValidateCodeResponse(
            valid=False,
            message="Invite code is invalid or unavailable",
        )

    return ValidateCodeResponse(
        valid=True,
        message="Invite code is valid",
    )


@router.get("/stats", response_model=ReferralStatsResponse)
def get_referral_stats(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    """
    Get referral statistics for the current user.

    Returns:
    - total_invites: Total number of people invited
    - successful_invites: Number of completed referrals
    - total_points: Total points earned from referrals
    - available_points: Current spendable points balance in unified wallet
    """
    log_with_context(
        logger,
        logging.INFO,
        "Fetching referral stats",
        user_id=current_user.id,
    )

    stats = get_user_referral_stats_service(current_user.id, session)
    return ReferralStatsResponse(**stats)


@router.get("/rewards", response_model=list[RewardResponse])
def get_user_rewards(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    """
    Get all rewards for the current user.

    Returns a list of all rewards earned by the user,
    including used and unused rewards.
    """
    log_with_context(
        logger,
        logging.INFO,
        "Fetching user rewards",
        user_id=current_user.id,
    )

    rewards = session.exec(
        select(UserReward)
        .where(UserReward.user_id == current_user.id)
        .order_by(UserReward.created_at.desc())
    ).all()

    return rewards


@router.delete("/codes/{code_id}", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_invite_code(
    code_id: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    """
    Deactivate an invite code.

    Only the owner of the code can deactivate it.
    Soft delete by setting is_active to False.
    """
    log_with_context(
        logger,
        logging.INFO,
        "Deactivating invite code",
        user_id=current_user.id,
        code_id=code_id,
    )

    # Find the invite code
    invite_code = session.get(InviteCode, code_id)

    if not invite_code:
        raise APIException(
            error_code=ErrorCode.NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invite code not found",
        )

    # Check ownership
    if invite_code.owner_id != current_user.id:
        raise APIException(
            error_code=ErrorCode.NOT_AUTHORIZED,
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to deactivate this code",
        )

    # Deactivate the code
    invite_code.is_active = False
    session.add(invite_code)
    session.commit()

    log_with_context(
        logger,
        logging.INFO,
        "Invite code deactivated successfully",
        user_id=current_user.id,
        code_id=code_id,
    )

    return None
