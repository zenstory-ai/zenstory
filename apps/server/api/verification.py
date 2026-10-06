"""Email verification API endpoints"""
import asyncio
import logging

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, EmailStr
from services.auth import (
    create_access_token,
    create_refresh_token,
    generate_token_jti,
    get_refresh_token_expires_at,
)
from services.verification_service import (
    get_code_ttl,
    get_remaining_cooldown,
    send_verification_code,
    verify_code,
)
from sqlmodel import Session, select

from config.datetime_utils import normalize_datetime_to_utc, utcnow
from core.error_codes import ErrorCode
from core.error_handler import APIException
from core.i18n import get_accept_language
from core.messages import get_message
from database import get_session
from models import RefreshTokenRecord, User
from services.features.referral_service import complete_pending_referral_for_invitee
from utils.email_identity import email_identity_matches, normalize_email_identity
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


# Request/Response schemas
class VerifyEmailRequest(BaseModel):
    email: EmailStr
    code: str
    language: str | None = "zh"  # Default to Chinese


class ResendVerificationRequest(BaseModel):
    email: EmailStr
    language: str | None = "zh"  # Default to Chinese


@router.post("/verify-email")
async def verify_email(
    request: VerifyEmailRequest,
    session: Session = Depends(get_session)
):
    """
    Verify email with verification code.

    - Accepts email and verification code
    - Updates user.email_verified to True if code is correct
    - Returns access and refresh tokens on success
    - Distributes referral rewards if applicable
    """
    normalized_email = normalize_email_identity(str(request.email))

    log_with_context(
        logger,
        logging.INFO,
        "Email verification attempt",
        email=normalized_email,
    )

    # Find user by email
    user = session.exec(
        select(User).where(email_identity_matches(User.email, normalized_email))
    ).first()

    if not user:
        log_with_context(
            logger,
            logging.WARNING,
            "Email verification failed: user not found (masked as invalid code)",
            email=normalized_email,
        )
        raise APIException(
            error_code=ErrorCode.AUTH_INVALID_VERIFICATION_CODE,
            status_code=status.HTTP_400_BAD_REQUEST
        )

    if not user.is_active:
        raise APIException(
            error_code=ErrorCode.AUTH_INACTIVE_USER,
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if user.email_verified:
        log_with_context(
            logger,
            logging.INFO,
            "Email verification attempt: already verified",
            user_id=user.id,
            email=normalized_email,
        )
        raise APIException(
            error_code=ErrorCode.AUTH_EMAIL_ALREADY_VERIFIED,
            status_code=status.HTTP_400_BAD_REQUEST
        )

    # Verify code
    language = request.language or "zh"
    success, error = await verify_code(normalized_email, request.code, language=language)

    if not success:
        log_with_context(
            logger,
            logging.WARNING,
            "Email verification failed: invalid code",
            user_id=user.id,
            email=normalized_email,
            error=error,
        )
        raise APIException(
            error_code=ErrorCode.AUTH_INVALID_VERIFICATION_CODE,
            status_code=status.HTTP_400_BAD_REQUEST
        )

    # Update user verification status.
    # The lookup above already matched this account case-insensitively, so do
    # NOT rewrite the stored email casing here: the email column has a
    # case-sensitive UNIQUE constraint, and lowercasing a legacy mixed-case
    # address can collide with another row and raise IntegrityError on an
    # otherwise-correct verification code. New accounts are already stored
    # normalized at registration time.
    user.email_verified = True
    user.updated_at = utcnow()
    session.add(user)

    # Process pending referral rewards in unified service flow.
    await complete_pending_referral_for_invitee(
        user.id,
        session,
        commit=False,
    )

    # Generate tokens
    access_token = create_access_token(data={"sub": user.id})
    refresh_token_jti = generate_token_jti()
    refresh_family_id = generate_token_jti()
    refresh_token = create_refresh_token(
        data={"sub": user.id, "jti": refresh_token_jti, "family_id": refresh_family_id}
    )
    session.add(
        RefreshTokenRecord(
            user_id=user.id,
            token_jti=refresh_token_jti,
            family_id=refresh_family_id,
            expires_at=get_refresh_token_expires_at(),
        )
    )
    # Persist verification, referral rewards and the refresh credential together.
    session.commit()
    session.refresh(user)

    log_with_context(
        logger,
        logging.INFO,
        "Email verified successfully",
        user_id=user.id,
        email=normalized_email,
    )

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "avatar_url": user.avatar_url,
            "is_active": user.is_active,
            "is_superuser": user.is_superuser,
            "email_verified": user.email_verified,
            "created_at": normalize_datetime_to_utc(user.created_at),
            "updated_at": normalize_datetime_to_utc(user.updated_at),
        }
    }


@router.post("/resend-verification")
async def resend_verification(
    request: ResendVerificationRequest,
    session: Session = Depends(get_session),
    accept_language: str = Depends(get_accept_language)
):
    """
    Resend verification code to email.

    - Checks cooldown (60 seconds between resends)
    - Sends new verification code
    """
    normalized_email = normalize_email_identity(str(request.email))

    # Find user by email
    user = session.exec(
        select(User).where(email_identity_matches(User.email, normalized_email))
    ).first()

    if not user:
        # Return generic success to avoid user/email enumeration.
        return {
            "message": get_message("auth_verification_resent", accept_language),
            "email": normalized_email
        }

    if user.email_verified:
        # Return generic success to avoid user/email enumeration.
        return {
            "message": get_message("auth_verification_resent", accept_language),
            "email": normalized_email
        }

    # Send verification code
    language = request.language or "zh"
    success, error = await send_verification_code(normalized_email, language=language)

    if not success:
        # Log the actual error for debugging
        log_with_context(
            logger,
            40,  # ERROR
            "Resend verification failed",
            email=normalized_email,
            error=error,
        )
        # Return generic user-friendly error message
        raise APIException(
            error_code=ErrorCode.AUTH_RESEND_FAILED,
            status_code=status.HTTP_400_BAD_REQUEST
        )

    return {
        "message": get_message("auth_verification_resent", accept_language),
        "email": normalized_email
    }


@router.get("/check-verification")
async def check_verification(
    email: EmailStr = Query(..., description="Email address to check"),
    session: Session = Depends(get_session)
):
    """
    Check email verification status and cooldown.

    - Returns verification status
    - Returns remaining cooldown time if code was recently sent
    - Returns verification code TTL if code exists
    """
    normalized_email = normalize_email_identity(str(email))

    # Find user by email
    user = session.exec(
        select(User).where(email_identity_matches(User.email, normalized_email))
    ).first()

    if not user:
        # Return generic state to avoid user/email enumeration.
        return {
            "email": normalized_email,
            "email_verified": False,
            "resend_cooldown_seconds": 0,
            "verification_code_ttl_seconds": 0
        }

    # Verified and unknown accounts intentionally share the same public state.
    if user.email_verified:
        return {
            "email": normalized_email,
            "email_verified": False,
            "resend_cooldown_seconds": 0,
            "verification_code_ttl_seconds": 0,
        }

    # Synchronous Redis lookups; keep them off the event loop.
    cooldown = await asyncio.to_thread(get_remaining_cooldown, normalized_email)
    code_ttl = await asyncio.to_thread(get_code_ttl, normalized_email)

    return {
        "email": normalized_email,
        "email_verified": user.email_verified,
        "resend_cooldown_seconds": cooldown,
        "verification_code_ttl_seconds": code_ttl
    }
