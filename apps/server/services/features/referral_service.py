"""
Referral service for managing invite codes and referral relationships.

Handles generation, validation of invite codes, and referral reward distribution.
"""
import os
import secrets
from datetime import UTC, timedelta

from sqlalchemy import func, or_, update
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from config.datetime_utils import utcnow
from core.error_codes import ErrorCode
from core.error_handler import APIException
from models.entities import User
from models.referral import (
    REFERRAL_STATUS_COMPLETED,
    REFERRAL_STATUS_PENDING,
    REFERRAL_STATUS_REWARDED,
    REWARD_TYPE_POINTS,
    InviteCode,
    Referral,
    UserReward,
    UserStats,
)
from services.features.points_service import points_service
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

# Configuration
MAX_INVITE_CODES_PER_USER = int(os.getenv("MAX_INVITE_CODES_PER_USER", "3"))
INVITE_CODE_LENGTH = int(os.getenv("INVITE_CODE_LENGTH", "8"))  # 8 chars = XXXX-XXXX
INVITE_CODE_MAX_USES = int(os.getenv("INVITE_CODE_MAX_USES", "3"))
INVITER_REWARD_POINTS = int(os.getenv("INVITER_REWARD_POINTS", "100"))
# Invitee reward is points (defaults to same as inviter points).
INVITEE_REWARD_POINTS = int(os.getenv("INVITEE_REWARD_POINTS", str(INVITER_REWARD_POINTS)))
REFERRAL_REPEAT_IP_WINDOW_HOURS = int(os.getenv("REFERRAL_REPEAT_IP_WINDOW_HOURS", "24"))

# Characters allowed in invite codes (exclude confusing: 0O1IL)
INVITE_CODE_CHARS = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


def _is_code_expired(invite_code: InviteCode) -> bool:
    """Check invite code expiration with safe timezone handling."""
    if not invite_code.expires_at:
        return False
    expires_at = invite_code.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return utcnow() > expires_at


def generate_invite_code() -> str:
    """
    Generate a random invite code in format XXXX-XXXX.

    Uses cryptographically secure random generation via secrets module.
    Excludes confusing characters: 0, O, 1, I, L

    Returns:
        str: Generated invite code (e.g., "A3B7-C9D2")
    """
    # Generate 8 random characters using cryptographically secure random
    code_chars = "".join(secrets.choice(INVITE_CODE_CHARS) for _ in range(INVITE_CODE_LENGTH))
    # Format as XXXX-XXXX
    return f"{code_chars[:4]}-{code_chars[4:]}"


def create_invite_code(
    user_id: str,
    session: Session,
    *,
    ignore_max_limit: bool = False,
) -> InviteCode:
    """
    Create a new invite code for a user.

    Args:
        user_id: User ID who will own this invite code
        session: Database session

    Returns:
        InviteCode: The newly created invite code

    Raises:
        APIException: If user has reached the maximum number of active invite codes
            (unless ignore_max_limit is True).
    """
    try:
        if not ignore_max_limit:
            owner = session.exec(
                select(User)
                .where(User.id == user_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).first()
            if not owner:
                raise APIException(
                    error_code=ErrorCode.NOT_FOUND,
                    status_code=404,
                    detail="User not found",
                )

            # Check how many active invite codes the user already has
            existing_codes = session.exec(
                select(InviteCode)
                .where(InviteCode.owner_id == user_id)
                .where(InviteCode.is_active == True)
            ).all()

            if len(existing_codes) >= MAX_INVITE_CODES_PER_USER:
                raise APIException(
                    error_code=ErrorCode.REFERRAL_MAX_CODES_REACHED,
                    status_code=400,
                    detail=f"Maximum number of invite codes ({MAX_INVITE_CODES_PER_USER}) reached",
                )

        # Generate a unique code
        max_attempts = 10
        for _ in range(max_attempts):
            code = generate_invite_code()
            # Check if code already exists
            existing = session.exec(
                select(InviteCode).where(InviteCode.code == code)
            ).first()
            if not existing:
                break
        else:
            # If we couldn't generate a unique code after max_attempts
            raise APIException(
                error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                status_code=500,
                detail="Failed to generate unique invite code",
            )

        # Create the invite code
        invite_code = InviteCode(
            code=code,
            owner_id=user_id,
            max_uses=INVITE_CODE_MAX_USES,
            current_uses=0,
            is_active=True,
        )
        session.add(invite_code)
        session.commit()
        session.refresh(invite_code)

        log_with_context(
            logger,
            20,  # INFO
            "Invite code created",
            user_id=user_id,
            code=code,
        )

        return invite_code

    except APIException:
        raise
    except Exception as e:
        log_with_context(
            logger,
            40,  # ERROR
            "create_invite_code failed",
            user_id=user_id,
            error=str(e),
            error_type=type(e).__name__,
        )
        session.rollback()
        raise APIException(
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            status_code=500,
            detail="Failed to create invite code",
        ) from e


def validate_invite_code(code: str, session: Session) -> tuple[bool, InviteCode | None, str]:
    """
    Validate an invite code.

    Args:
        code: The invite code to validate
        session: Database session

    Returns:
        tuple[bool, InviteCode | None, str]: (is_valid, invite_code, error_message)
    """
    try:
        normalized_code = code.strip().upper()

        # Find the invite code
        invite_code = session.exec(
            select(InviteCode).where(InviteCode.code == normalized_code)
        ).first()

        if not invite_code:
            return False, None, "Invalid invite code"

        # Check if code is active
        if not invite_code.is_active:
            return False, None, "This invite code has been deactivated"

        # Check expiration
        # Handle both timezone-aware and naive datetimes for comparison
        if _is_code_expired(invite_code):
            return False, None, "This invite code has expired"

        # Check usage limit
        if invite_code.current_uses >= invite_code.max_uses:
            return False, None, "This invite code has reached its usage limit"

        return True, invite_code, ""

    except Exception as e:
        log_with_context(
            logger,
            40,  # ERROR
            "validate_invite_code failed",
            code=code,
            error=str(e),
            error_type=type(e).__name__,
        )
        return False, None, "Failed to validate invite code"


async def create_referral(
    invite_code: InviteCode,
    invitee_id: str,
    session: Session,
    device_fingerprint: str | None = None,
    ip_address: str | None = None,
) -> Referral:
    """
    Create a referral relationship when a new user registers with an invite code.

    Args:
        invite_code: The InviteCode used
        invitee_id: The new user's ID
        session: Database session
        device_fingerprint: Optional device fingerprint for fraud detection
        ip_address: Optional IP address for fraud detection

    Returns:
        Referral: The created referral record

    Raises:
        APIException: If referral creation fails
    """
    try:
        # Check if invitee already has a referral (shouldn't happen due to unique constraint)
        existing_referral = session.exec(
            select(Referral).where(Referral.invitee_id == invitee_id)
        ).first()

        if existing_referral:
            raise APIException(
                error_code=ErrorCode.REFERRAL_ALREADY_EXISTS,
                status_code=400,
                detail="User already has a referral record",
            )

        # Atomically reserve one usage slot to avoid race conditions.
        usage_update = session.execute(
            update(InviteCode)
            .where(InviteCode.id == invite_code.id)
            .where(InviteCode.is_active == True)
            .where(
                or_(
                    InviteCode.expires_at.is_(None),
                    InviteCode.expires_at > utcnow(),
                )
            )
            .where(InviteCode.current_uses < InviteCode.max_uses)
            .values(current_uses=InviteCode.current_uses + 1)
        )

        if (usage_update.rowcount or 0) == 0:
            raise APIException(
                error_code=ErrorCode.REFERRAL_CODE_USED_UP,
                status_code=400,
                detail="Invite code is no longer available",
            )

        # Create the referral after usage slot is successfully reserved.
        referral = Referral(
            inviter_id=invite_code.owner_id,
            invitee_id=invitee_id,
            invite_code_id=invite_code.id,
            device_fingerprint=device_fingerprint,
            ip_address=ip_address,
            status=REFERRAL_STATUS_PENDING,
        )
        session.add(referral)

        # Update user stats (total_invites)
        inviter_stats = session.exec(
            select(UserStats).where(UserStats.user_id == invite_code.owner_id)
        ).first()

        if not inviter_stats:
            inviter_stats = UserStats(
                user_id=invite_code.owner_id,
                total_invites=1,
            )
            session.add(inviter_stats)
        else:
            inviter_stats.total_invites += 1
            inviter_stats.updated_at = utcnow()

        session.commit()
        session.refresh(referral)

        log_with_context(
            logger,
            20,  # INFO
            "Referral created",
            inviter_id=invite_code.owner_id,
            invitee_id=invitee_id,
            code=invite_code.code,
        )

        return referral

    except APIException:
        raise
    except Exception as e:
        log_with_context(
            logger,
            40,  # ERROR
            "create_referral failed",
            invite_code_id=invite_code.id,
            invitee_id=invitee_id,
            error=str(e),
            error_type=type(e).__name__,
        )
        session.rollback()
        raise APIException(
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            status_code=500,
            detail="Failed to create referral",
        ) from e


async def complete_referral_and_reward(
    referral_id: str, session: Session, commit: bool = True
) -> None:
    """
    Complete a referral and distribute rewards to both parties.

    This function:
    1. Updates referral status to COMPLETED
    2. Gives inviter points (100 points)
    3. Gives invitee points (100 points)
    4. Updates referral status to REWARDED
    5. Updates UserStats for both parties

    Args:
        referral_id: The referral ID to complete
        session: Database session

    Raises:
        APIException: If referral not found or reward distribution fails
    """
    try:
        now = utcnow()
        # Lock before the idempotency/status check and refresh stale ORM state.
        referral = session.exec(
            select(Referral)
            .where(Referral.id == referral_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        if not referral:
            raise APIException(
                error_code=ErrorCode.REFERRAL_NOT_FOUND,
                status_code=404,
                detail="Referral not found",
            )

        if referral.status == REFERRAL_STATUS_REWARDED:
            # Already rewarded, idempotent
            log_with_context(
                logger,
                20,  # INFO
                "Referral already rewarded, skipping",
                referral_id=referral_id,
            )
            return

        # Already assessed and explicitly marked as completed without rewards.
        if referral.status == REFERRAL_STATUS_COMPLETED and (
            not referral.inviter_rewarded and not referral.invitee_rewarded
        ):
            log_with_context(
                logger,
                20,  # INFO
                "Referral already completed without rewards, skipping",
                referral_id=referral_id,
            )
            return

        # Baseline completion stamp before reward decisions
        referral.status = REFERRAL_STATUS_COMPLETED
        referral.completed_at = now

        # Basic risk checks before reward issuance
        if referral.fraud_score >= 0.7:
            referral.inviter_rewarded = False
            referral.invitee_rewarded = False
            session.add(referral)
            if commit:
                session.commit()
            else:
                session.flush()
            log_with_context(
                logger,
                30,  # WARNING
                "Referral rewards blocked due to fraud score",
                referral_id=referral_id,
                fraud_score=referral.fraud_score,
            )
            return

        has_repeat_signal, repeat_reason = _has_repeat_rewarded_signals(referral, session)
        if has_repeat_signal:
            referral.fraud_score = max(referral.fraud_score, 0.95)
            referral.inviter_rewarded = False
            referral.invitee_rewarded = False
            session.add(referral)
            if commit:
                session.commit()
            else:
                session.flush()
            log_with_context(
                logger,
                30,  # WARNING
                "Referral rewards blocked due to anti-abuse signal",
                referral_id=referral_id,
                inviter_id=referral.inviter_id,
                invitee_id=referral.invitee_id,
                reason=repeat_reason,
            )
            return

        # --- Reward the inviter with points ---
        inviter_reward = UserReward(
            user_id=referral.inviter_id,
            reward_type=REWARD_TYPE_POINTS,
            amount=INVITER_REWARD_POINTS,
            source="referral",
            referral_id=referral.id,
            is_used=True,
            used_at=now,
        )
        session.add(inviter_reward)
        points_service.earn_points(
            session=session,
            user_id=referral.inviter_id,
            amount=INVITER_REWARD_POINTS,
            transaction_type="referral",
            source_id=referral.id,
            description="Referral reward",
            commit=False,
        )

        # Update inviter stats
        inviter_stats = session.exec(
            select(UserStats).where(UserStats.user_id == referral.inviter_id)
        ).first()

        if not inviter_stats:
            total_invites = (
                session.exec(
                    select(func.count())
                    .select_from(Referral)
                    .where(Referral.inviter_id == referral.inviter_id)
                ).one()
                or 0
            )
            inviter_stats = UserStats(
                user_id=referral.inviter_id,
                total_invites=max(total_invites, 1),
            )
            session.add(inviter_stats)

        inviter_stats.successful_invites += 1
        inviter_stats.total_points += INVITER_REWARD_POINTS
        inviter_stats.updated_at = now

        # --- Reward the invitee with points ---
        invitee_reward = UserReward(
            user_id=referral.invitee_id,
            reward_type=REWARD_TYPE_POINTS,
            amount=INVITEE_REWARD_POINTS,
            source="referral",
            referral_id=referral.id,
            is_used=True,
            used_at=now,
        )
        points_service.earn_points(
            session=session,
            user_id=referral.invitee_id,
            amount=INVITEE_REWARD_POINTS,
            transaction_type="referral",
            source_id=referral.id,
            description="Referral invitee reward",
            commit=False,
        )
        session.add(invitee_reward)

        inviter_balance = points_service.get_balance(session, referral.inviter_id)
        inviter_stats.available_points = inviter_balance["available"]
        if inviter_stats.total_invites < inviter_stats.successful_invites:
            inviter_stats.total_invites = inviter_stats.successful_invites

        # Update referral status to REWARDED
        referral.status = REFERRAL_STATUS_REWARDED
        referral.rewarded_at = now
        referral.inviter_rewarded = True
        referral.invitee_rewarded = True

        if commit:
            session.commit()
        else:
            session.flush()

        log_with_context(
            logger,
            20,  # INFO
            "Referral completed and rewards distributed",
            referral_id=referral_id,
            inviter_id=referral.inviter_id,
            invitee_id=referral.invitee_id,
            inviter_points=INVITER_REWARD_POINTS,
            invitee_points=INVITEE_REWARD_POINTS,
        )

    except APIException:
        raise
    except Exception as e:
        log_with_context(
            logger,
            40,  # ERROR
            "complete_referral_and_reward failed",
            referral_id=referral_id,
            error=str(e),
            error_type=type(e).__name__,
        )
        session.rollback()
        raise APIException(
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            status_code=500,
            detail="Failed to complete referral and distribute rewards",
        ) from e


async def complete_pending_referral_for_invitee(
    invitee_id: str, session: Session, commit: bool = True
) -> bool:
    """
    Complete and reward a pending referral for an invitee if one exists.

    Returns:
        bool: True if a pending referral was found and processed.
    """
    pending_referral = session.exec(
        select(Referral).where(
            Referral.invitee_id == invitee_id,
            Referral.status == REFERRAL_STATUS_PENDING,
        )
    ).first()
    if not pending_referral:
        return False

    await complete_referral_and_reward(
        pending_referral.id,
        session,
        commit=commit,
    )
    return True


def _has_repeat_rewarded_signals(referral: Referral, session: Session) -> tuple[bool, str]:
    """
    Check lightweight anti-abuse signals before issuing referral rewards.

    Current policy blocks rewards when the same inviter has already received a
    rewarded/completed referral from the same IP within the recent window, or
    from the same device fingerprint.
    """
    if referral.ip_address:
        window_start = utcnow() - timedelta(hours=REFERRAL_REPEAT_IP_WINDOW_HOURS)
        repeat_ip_count = (
            session.exec(
                select(func.count())
                .select_from(Referral)
                .where(Referral.inviter_id == referral.inviter_id)
                .where(Referral.id != referral.id)
                .where(Referral.ip_address == referral.ip_address)
                .where(Referral.created_at >= window_start)
                .where(
                    Referral.status.in_(
                        [REFERRAL_STATUS_COMPLETED, REFERRAL_STATUS_REWARDED]
                    )
                )
            ).one()
            or 0
        )
        if repeat_ip_count > 0:
            return True, "repeat_ip"

    if referral.device_fingerprint:
        repeat_device_count = (
            session.exec(
                select(func.count())
                .select_from(Referral)
                .where(Referral.inviter_id == referral.inviter_id)
                .where(Referral.id != referral.id)
                .where(Referral.device_fingerprint == referral.device_fingerprint)
                .where(
                    Referral.status.in_(
                        [REFERRAL_STATUS_COMPLETED, REFERRAL_STATUS_REWARDED]
                    )
                )
            ).one()
            or 0
        )
        if repeat_device_count > 0:
            return True, "repeat_device"

    return False, ""


def get_user_referral_stats(user_id: str, session: Session) -> dict:
    """
    Get user's referral statistics.

    Args:
        user_id: User ID
        session: Database session

    Returns:
        dict: Statistics including total invites, successful invites, points, etc.
    """
    try:
        stats = session.exec(
            select(UserStats).where(UserStats.user_id == user_id)
        ).first()

        wallet_balance = points_service.get_balance(session, user_id)
        wallet_available = wallet_balance.get("available", 0)

        if not stats:
            return {
                "total_invites": 0,
                "successful_invites": 0,
                "total_points": 0,
                "available_points": wallet_available,
            }

        return {
            "total_invites": stats.total_invites,
            "successful_invites": stats.successful_invites,
            "total_points": stats.total_points,
            # Product contract: expose unified, spendable wallet points.
            "available_points": wallet_available,
        }

    except SQLAlchemyError as e:
        log_with_context(
            logger,
            40,  # ERROR
            "get_user_referral_stats failed",
            user_id=user_id,
            error=str(e),
            error_type=type(e).__name__,
        )
        raise APIException(
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            status_code=500,
            detail="Failed to get referral statistics",
        ) from e


def get_user_invite_codes(user_id: str, session: Session) -> list[InviteCode]:
    """
    Get all invite codes for a user.

    Args:
        user_id: User ID
        session: Database session

    Returns:
        list[InviteCode]: List of user's invite codes
    """
    try:
        codes = session.exec(
            select(InviteCode)
            .where(InviteCode.owner_id == user_id)
            .order_by(InviteCode.created_at.desc())
        ).all()

        return list(codes)

    except SQLAlchemyError as e:
        log_with_context(
            logger,
            40,  # ERROR
            "get_user_invite_codes failed",
            user_id=user_id,
            error=str(e),
            error_type=type(e).__name__,
        )
        raise APIException(
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            status_code=500,
            detail="Failed to get invite codes",
        ) from e
