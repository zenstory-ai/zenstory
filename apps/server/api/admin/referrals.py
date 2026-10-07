"""
Admin Referral Management API endpoints.

This module contains referral management endpoints for admin operations.
"""
import logging

from fastapi import APIRouter, Depends, Query, Request, status
from sqlmodel import Session, col, func, select

from database import get_session
from models import User
from models.referral import (
    REFERRAL_STATUS_COMPLETED,
    REFERRAL_STATUS_REWARDED,
    InviteCode,
    Referral,
    UserReward,
)
from services.admin_audit_service import admin_audit_service
from services.core.auth_service import get_current_superuser
from services.features.referral_service import create_invite_code as create_invite_code_service
from utils.logger import get_logger, log_with_context

from .schemas import (
    AdminInviteCodeResponse,
    AdminReferralStatsResponse,
    InviteCodeListResponse,
    ReferralRewardListResponse,
)

logger = get_logger(__name__)

router = APIRouter(tags=["admin-referrals"])


# ==================== Referral Management ====================


@router.get("/referrals/stats", response_model=AdminReferralStatsResponse)
def get_referral_stats(
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    Get referral system statistics.

    Requires superuser privileges.
    """
    # Total codes
    total_codes = session.exec(select(func.count()).select_from(InviteCode)).one()

    # Active codes
    active_codes = session.exec(
        select(func.count()).select_from(InviteCode).where(InviteCode.is_active == True)
    ).one()

    # Total referrals
    total_referrals = session.exec(select(func.count()).select_from(Referral)).one()

    # Successful referrals (COMPLETED or REWARDED status)
    successful_referrals = session.exec(
        select(func.count()).select_from(Referral)
        .where(Referral.status.in_([REFERRAL_STATUS_COMPLETED, REFERRAL_STATUS_REWARDED]))
    ).one()

    # Pending rewards (referrals completed but not yet rewarded)
    pending_rewards = session.exec(
        select(func.count()).select_from(Referral)
        .where(Referral.status == REFERRAL_STATUS_COMPLETED)
        .where(Referral.inviter_rewarded == False)
    ).one()

    # Total points awarded from referrals
    total_points = session.exec(
        select(func.coalesce(func.sum(UserReward.amount), 0))
        .where(UserReward.source == "referral")
        .where(UserReward.reward_type == "points")
    ).one() or 0

    log_with_context(
        logger,
        logging.INFO,
        "Retrieved referral stats",
        user_id=current_user.id,
        total_codes=total_codes,
        total_referrals=total_referrals,
    )

    return AdminReferralStatsResponse(
        total_codes=total_codes,
        active_codes=active_codes,
        total_referrals=total_referrals,
        successful_referrals=successful_referrals,
        pending_rewards=pending_rewards,
        total_points_awarded=total_points,
    )


@router.post("/invites", response_model=AdminInviteCodeResponse, status_code=status.HTTP_201_CREATED)
def create_admin_invite_code(
    http_request: Request,
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    Create a new invite code from admin referral page.

    Requires superuser privileges.
    """
    new_code = create_invite_code_service(
        current_user.id,
        session,
        ignore_max_limit=True,
    )
    # The code is already committed by the service; audit in its own commit.
    admin_audit_service.log_action(
        session, current_user.id, "create_invite_code", "invite_code", new_code.id,
        new_value={
            "code": new_code.code,
            "max_uses": new_code.max_uses,
            "expires_at": new_code.expires_at.isoformat() if new_code.expires_at else None,
            "ignore_max_limit": True,
        },
        request=http_request,
    )

    log_with_context(
        logger,
        logging.INFO,
        "Admin created invite code",
        user_id=current_user.id,
        code=new_code.code,
    )

    return AdminInviteCodeResponse(
        id=new_code.id,
        code=new_code.code,
        owner_id=new_code.owner_id,
        owner_name=current_user.username,
        max_uses=new_code.max_uses,
        current_uses=new_code.current_uses,
        is_active=new_code.is_active,
        expires_at=new_code.expires_at,
        created_at=new_code.created_at,
    )


@router.get("/invites", response_model=InviteCodeListResponse)
def get_invite_codes(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    is_active_filter: bool | None = Query(None, alias="is_active", description="Filter by active status"),
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    Get invite codes with pagination.

    Requires superuser privileges.
    """
    query = select(InviteCode)

    if is_active_filter is not None:
        query = query.where(InviteCode.is_active == is_active_filter)

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total = session.exec(count_query).one()

    # Apply pagination
    query = query.order_by(col(InviteCode.created_at).desc(), col(InviteCode.id).desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    page_query = query.add_columns(col(User.username)).outerjoin(
        User, col(User.id) == col(InviteCode.owner_id)
    )
    # Session.execute keeps the added scalar paired with the entity projection.
    codes = session.execute(page_query).all()

    items = []
    for code, username in codes:
        items.append(AdminInviteCodeResponse(
            id=code.id,
            code=code.code,
            owner_id=code.owner_id,
            owner_name=username if username is not None else "Unknown",
            max_uses=code.max_uses,
            current_uses=code.current_uses,
            is_active=code.is_active,
            expires_at=code.expires_at,
            created_at=code.created_at,
        ))

    log_with_context(
        logger,
        logging.INFO,
        "Retrieved invite codes",
        user_id=current_user.id,
        count=len(items),
        is_active_filter=is_active_filter,
    )

    return InviteCodeListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/referrals/rewards", response_model=ReferralRewardListResponse)
def get_referral_rewards(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    Get referral rewards history.

    Requires superuser privileges.
    """
    query = select(UserReward).where(UserReward.source == "referral")

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total = session.exec(count_query).one()

    # Apply pagination
    query = query.order_by(col(UserReward.created_at).desc(), col(UserReward.id).desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    page_query = (
        query.add_columns(col(User.username), col(Referral.id))
        .outerjoin(User, col(User.id) == col(UserReward.user_id))
        .outerjoin(Referral, col(Referral.id) == col(UserReward.referral_id))
    )
    rewards = session.execute(page_query).all()

    items = []
    for reward, username, referral_id in rewards:
        items.append({
            **reward.model_dump(),
            "username": username if username is not None else "Unknown",
            "referral_id": referral_id,
        })

    log_with_context(
        logger,
        logging.INFO,
        "Retrieved referral rewards",
        user_id=current_user.id,
        count=len(items),
    )

    return ReferralRewardListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )
