"""
Authentication API endpoints
"""
import asyncio
import hashlib
import logging
import os
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, ConfigDict, EmailStr, field_serializer, field_validator
from services.auth import (
    ALLOW_LEGACY_REFRESH_WITHOUT_JTI,
    TOKEN_TYPE_REFRESH,
    create_access_token,
    create_refresh_token,
    generate_token_jti,
    get_current_active_user,
    get_refresh_token_expires_at,
    hash_password,
    verify_password,
    verify_token,
)
from services.verification_service import send_verification_code
from sqlalchemy import delete, or_
from sqlmodel import Session, select

from config.datetime_utils import normalize_datetime_to_utc, utcnow
from core.error_codes import ErrorCode
from core.error_handler import APIException
from core.i18n import get_accept_language
from core.messages import get_message
from database import get_session
from middleware.rate_limit import check_rate_limit, get_client_ip
from models import (
    ACTIVATION_EVENT_SIGNUP_SUCCESS,
    RefreshTokenRecord,
    User,
)
from models.referral import InviteCode
from services.features.activation_event_service import activation_event_service
from services.features.referral_service import create_referral as create_referral_service
from services.subscription.subscription_service import subscription_service
from utils.email_identity import email_identity_matches, normalize_email_identity
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

_REFRESH_TOKEN_CLEANUP_LAST_RUN_AT: datetime | None = None


# Request/Response schemas
class RegisterRequest(BaseModel):
    username: str
    email: EmailStr
    password: str
    language: str | None = "zh"  # Default to Chinese
    invite_code: str | None = None  # Invitation code for referral
    device_fingerprint: str | None = None  # Device fingerprint for fraud detection

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 3:
            raise ValueError("username must be at least 3 characters")
        return value

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        return _validate_new_password(value)


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: dict


class UserResponse(BaseModel):
    id: str
    username: str
    email: str
    email_verified: bool
    avatar_url: str | None
    is_active: bool
    is_superuser: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("created_at", "updated_at")
    def _serialize_timestamp(self, value: datetime, _info):  # type: ignore[no-untyped-def]
        return normalize_datetime_to_utc(value).isoformat()


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class RegisterPolicyResponse(BaseModel):
    invite_code_optional: bool
    variant: str
    rollout_percent: int


class UpdateUserRequest(BaseModel):
    username: str | None = None
    email: EmailStr | None = None
    avatar_url: str | None = None

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if len(value) < 3:
            raise ValueError("username must be at least 3 characters")
        return value


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value: str) -> str:
        return _validate_new_password(value)


def _validate_new_password(value: str) -> str:
    if len(value) < 6:
        raise ValueError("password must be at least 6 characters")
    if len(value.encode("utf-8")) > 72:
        raise ValueError("password must be at most 72 bytes")
    return value


def _normalize_invite_code(code: str) -> str:
    """Normalize invite code input for case-insensitive matching."""
    return code.strip().upper()


def _is_registration_invite_code_optional() -> bool:
    """
    Whether registration invite code is optional.

    Controlled by env var AUTH_REGISTER_INVITE_CODE_OPTIONAL.
    Default is False (invite code required).
    """
    return os.getenv("AUTH_REGISTER_INVITE_CODE_OPTIONAL", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _get_registration_invite_gray_rollout_percent() -> int:
    """Rollout percent for invite-code optional registration experiment."""
    raw = _safe_int_from_env("AUTH_REGISTER_INVITE_GRAY_PERCENT", 0, minimum=0)
    return max(0, min(raw, 100))


def _get_registration_invite_gray_salt() -> str:
    return (os.getenv("AUTH_REGISTER_INVITE_GRAY_SALT", "auth-register-invite-v1") or "").strip() or "auth-register-invite-v1"


def _invite_gray_bucket(identity: str, *, salt: str) -> int:
    digest = hashlib.sha256(f"{salt}:{identity}".encode()).hexdigest()
    return int(digest[:8], 16) % 100


def _resolve_registration_invite_policy(
    *,
    email: str | None,
    username: str | None,
) -> tuple[bool, str, int]:
    """
    Resolve invite-code requirement policy for current registration request.

    Priority:
    1) AUTH_REGISTER_INVITE_CODE_OPTIONAL=true  => always optional
    2) AUTH_REGISTER_INVITE_GRAY_PERCENT rollout => optional by stable bucket
    3) fallback => required
    """
    if _is_registration_invite_code_optional():
        return True, "global_optional", 100

    rollout_percent = _get_registration_invite_gray_rollout_percent()
    if rollout_percent <= 0:
        return False, "control_required", 0

    identity = (email or username or "").strip().lower()
    if not identity:
        return False, "control_required", rollout_percent

    bucket = _invite_gray_bucket(identity, salt=_get_registration_invite_gray_salt())
    if bucket < rollout_percent:
        return True, "treatment_optional", rollout_percent
    return False, "control_required", rollout_percent


def _is_invite_code_expired(expires_at: datetime | None) -> bool:
    """Check invite code expiration with robust timezone handling."""
    if not expires_at:
        return False
    return normalize_datetime_to_utc(expires_at) <= utcnow()


def _is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _safe_int_from_env(name: str, default: int, *, minimum: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        return max(minimum, int(raw))
    except (TypeError, ValueError):
        return default


def _is_auth_rate_limit_enabled() -> bool:
    """Whether auth rate limiting is enabled."""
    return _is_truthy(os.getenv("AUTH_RATE_LIMIT_ENABLED", "true"))


def _enforce_auth_rate_limit(
    *,
    http_request: Request,
    key: str,
    max_requests: int,
    window_seconds: int,
    endpoint: str,
    identifier: str | None = None,
    include_client_ip: bool = True,
) -> None:
    """Apply per-IP auth rate limit with security logging."""
    allowed, _remaining = check_rate_limit(
        http_request,
        key=key,
        max_requests=max_requests,
        window_seconds=window_seconds,
        include_client_ip=include_client_ip,
    )
    if allowed:
        return

    log_with_context(
        logger,
        logging.WARNING,
        "Auth rate limit exceeded",
        endpoint=endpoint,
        client_ip=get_client_ip(http_request),
        identifier=identifier,
        max_requests=max_requests,
        window_seconds=window_seconds,
    )
    raise APIException(
        error_code=ErrorCode.AUTH_RATE_LIMIT_EXCEEDED,
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
    )


def _get_refresh_token_retention_days() -> int:
    return _safe_int_from_env("AUTH_REFRESH_TOKEN_RETENTION_DAYS", 30, minimum=1)


def _get_refresh_token_cleanup_interval_seconds() -> int:
    return _safe_int_from_env("AUTH_REFRESH_TOKEN_CLEANUP_INTERVAL_SECONDS", 3600, minimum=60)


def _maybe_cleanup_refresh_token_records(session: Session) -> int:
    """
    Best-effort cleanup for stale refresh token records.

    Deletes old revoked/expired records to keep table size bounded.
    """
    global _REFRESH_TOKEN_CLEANUP_LAST_RUN_AT

    now = utcnow()
    if _REFRESH_TOKEN_CLEANUP_LAST_RUN_AT is not None:
        elapsed_seconds = (now - _REFRESH_TOKEN_CLEANUP_LAST_RUN_AT).total_seconds()
        if elapsed_seconds < _get_refresh_token_cleanup_interval_seconds():
            return 0

    retention_cutoff = now - timedelta(days=_get_refresh_token_retention_days())

    delete_stmt = delete(RefreshTokenRecord).where(
        or_(
            RefreshTokenRecord.expires_at < retention_cutoff,
            (
                RefreshTokenRecord.revoked_at.is_not(None)
                & (RefreshTokenRecord.revoked_at < retention_cutoff)
            ),
        )
    )
    result = session.exec(delete_stmt)
    session.commit()
    deleted_count = max(int(result.rowcount or 0), 0)
    if deleted_count > 0:
        log_with_context(
            logger,
            logging.INFO,
            "Refresh token cleanup completed",
            deleted_count=deleted_count,
            retention_days=_get_refresh_token_retention_days(),
        )

    _REFRESH_TOKEN_CLEANUP_LAST_RUN_AT = now
    return deleted_count


def _run_refresh_token_housekeeping(session: Session) -> None:
    """Run cleanup without impacting user-facing auth flows."""
    try:
        _maybe_cleanup_refresh_token_records(session)
    except Exception as exc:  # pragma: no cover - defensive
        session.rollback()
        log_with_context(
            logger,
            logging.WARNING,
            "Refresh token cleanup failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )


def _ensure_user_free_subscription_and_quota(
    session: Session,
    user_id: str,
    source: str,
) -> None:
    """Ensure user baseline records (free subscription + quota) exist."""
    subscription_service.ensure_user_subscription_and_quota(
        session=session,
        user_id=user_id,
        source=source,
    )


def _create_refresh_record(
    session: Session,
    *,
    user_id: str,
    token_jti: str,
    family_id: str,
) -> RefreshTokenRecord:
    """Persist refresh token metadata for rotation/revocation."""
    record = RefreshTokenRecord(
        user_id=user_id,
        token_jti=token_jti,
        family_id=family_id,
        issued_at=utcnow(),
        expires_at=get_refresh_token_expires_at(),
    )
    session.add(record)
    return record


def _revoke_refresh_family(session: Session, *, family_id: str, reason: str) -> int:
    """Revoke all active refresh tokens in the same family."""
    now = utcnow()
    records = session.exec(
        select(RefreshTokenRecord).where(
            RefreshTokenRecord.family_id == family_id,
            RefreshTokenRecord.revoked_at.is_(None),
        )
    ).all()
    for record in records:
        record.revoked_at = now
        record.revoke_reason = reason
        record.updated_at = now
        session.add(record)
    return len(records)


def _revoke_active_refresh_tokens_for_user(
    session: Session,
    *,
    user_id: str,
    reason: str,
) -> int:
    """Revoke all active refresh tokens for a user."""
    # Share refresh's User -> token lock order so rotation cannot insert a
    # descendant after our active-token snapshot. Do not reload pending changes.
    session.exec(select(User.id).where(User.id == user_id).with_for_update()).first()
    now = utcnow()
    active_records = session.exec(
        select(RefreshTokenRecord).where(
            RefreshTokenRecord.user_id == user_id,
            RefreshTokenRecord.revoked_at.is_(None),
        )
    ).all()

    for record in active_records:
        record.revoked_at = now
        record.revoke_reason = reason
        record.updated_at = now
        session.add(record)

    return len(active_records)


# Endpoints
@router.get("/register-policy", response_model=RegisterPolicyResponse)
async def get_register_policy(
    email: str | None = None,
    username: str | None = None,
):
    invite_code_optional, variant, rollout_percent = _resolve_registration_invite_policy(
        email=email,
        username=username,
    )
    return RegisterPolicyResponse(
        invite_code_optional=invite_code_optional,
        variant=variant,
        rollout_percent=rollout_percent,
    )


@router.post("/register")
async def register(
    request: RegisterRequest,
    http_request: Request,  # For capturing client IP
    session: Session = Depends(get_session),
    _accept_language: str = Depends(get_accept_language)
):
    """
    Register a new user.

    - Creates a new user account with email_verified=False
    - Sends verification code to user's email
    - User must verify email before logging in
    - Supports invitation codes for referral rewards
    """
    normalized_email = normalize_email_identity(str(request.email))

    # Check if username already exists
    existing_user = session.exec(
        select(User).where(User.username == request.username)
    ).first()
    if existing_user:
        raise APIException(
            error_code=ErrorCode.AUTH_USERNAME_EXISTS,
            status_code=status.HTTP_400_BAD_REQUEST
        )

    # Check if email already exists
    existing_email = session.exec(
        select(User).where(email_identity_matches(User.email, normalized_email))
    ).first()
    if existing_email:
        raise APIException(
            error_code=ErrorCode.AUTH_EMAIL_EXISTS,
            status_code=status.HTTP_400_BAD_REQUEST
        )

    invite_code_optional, invite_policy_variant, invite_policy_rollout_percent = (
        _resolve_registration_invite_policy(
            email=normalized_email,
            username=request.username,
        )
    )

    # Validate invite code
    referral_invite_code_id: str | None = None
    referral_inviter_id: str | None = None
    normalized_invite_code: str | None = None
    if request.invite_code:
        normalized_invite_code = _normalize_invite_code(request.invite_code)
        if not normalized_invite_code:
            normalized_invite_code = None

    # Invite code is required by default (unless env explicitly allows optional).
    if not normalized_invite_code and not invite_code_optional:
        raise APIException(
            error_code=ErrorCode.AUTH_INVITE_CODE_REQUIRED,
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if normalized_invite_code:
        invite_code_obj = session.exec(
            select(InviteCode).where(InviteCode.code == normalized_invite_code)
        ).first()

        if not invite_code_obj:
            raise APIException(
                error_code=ErrorCode.REFERRAL_CODE_INVALID,
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        if not invite_code_obj.is_active:
            raise APIException(
                error_code=ErrorCode.REFERRAL_CODE_INVALID,
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        if invite_code_obj.current_uses >= invite_code_obj.max_uses:
            raise APIException(
                error_code=ErrorCode.REFERRAL_CODE_USED_UP,
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        if _is_invite_code_expired(invite_code_obj.expires_at):
            raise APIException(
                error_code=ErrorCode.REFERRAL_CODE_EXPIRED,
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        # SECURITY: Prevent self-referral (check if owner has same email)
        owner = session.get(User, invite_code_obj.owner_id)
        if owner and normalize_email_identity(owner.email) == normalized_email:
            log_with_context(
                logger,
                logging.WARNING,
                "Self-referral attempt blocked",
                email=normalized_email,
                invite_code=normalized_invite_code,
            )
            raise APIException(
                error_code=ErrorCode.REFERRAL_CODE_INVALID,
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        referral_invite_code_id = invite_code_obj.id
        referral_inviter_id = invite_code_obj.owner_id

    # Create new user with email_verified=False
    # bcrypt is CPU-bound (~0.3s); keep it off the event loop.
    hashed_password = await asyncio.to_thread(hash_password, request.password)
    new_user = User(
        username=request.username,
        email=normalized_email,
        hashed_password=hashed_password,
        email_verified=False
    )

    session.add(new_user)
    session.commit()
    session.refresh(new_user)

    # Create referral relationship if valid invite code was used
    if referral_invite_code_id and referral_inviter_id:
        # Capture client IP for fraud detection
        client_ip = get_client_ip(http_request)
        if client_ip == "unknown":
            client_ip = None

        try:
            invite_code_obj = session.get(InviteCode, referral_invite_code_id)
            if invite_code_obj:
                await create_referral_service(
                    invite_code=invite_code_obj,
                    invitee_id=new_user.id,
                    session=session,
                    device_fingerprint=request.device_fingerprint,
                    ip_address=client_ip,
                )
                log_with_context(
                    logger,
                    logging.INFO,
                    "Referral relationship created",
                    inviter_id=referral_inviter_id,
                    invitee_id=new_user.id,
                    invite_code=normalized_invite_code,
                )
            else:
                log_with_context(
                    logger,
                    logging.INFO,
                    "Referral skipped: invite code missing during registration",
                    invite_code_id=referral_invite_code_id,
                    invitee_id=new_user.id,
                )
        except Exception as e:
            session.rollback()
            log_with_context(
                logger,
                logging.WARNING,
                "Referral creation failed after user registration",
                invite_code_id=referral_invite_code_id,
                invitee_id=new_user.id,
                error=str(e),
                error_type=type(e).__name__,
            )

    try:
        _ensure_user_free_subscription_and_quota(
            session=session,
            user_id=new_user.id,
            source="registration",
        )
    except Exception as e:
        # Recover failed SQL before logging expired attributes or continuing signup.
        session.rollback()
        # Log but don't fail registration
        log_with_context(
            logger,
            logging.WARNING,
            "Failed to bootstrap subscription/quota for new user",
            user_id=new_user.id,
            error=str(e),
            error_type=type(e).__name__,
        )

    # Send verification code
    language = request.language or "zh"
    success, error = await send_verification_code(normalized_email, language=language)

    if not success:
        # Keep registration successful to avoid "account created but API failed" inconsistency.
        # User can request a new code via /api/auth/resend-verification.
        log_with_context(
            logger,
            logging.WARNING,
            "Verification code send failed after successful registration",
            email=normalized_email,
            user_id=new_user.id,
            error=error,
        )

    # Activation milestone: signup completed.
    try:
        activation_event_service.record_once(
            session,
            user_id=new_user.id,
            event_name=ACTIVATION_EVENT_SIGNUP_SUCCESS,
            event_metadata={
                "source": "register_api",
                "invite_policy_variant": invite_policy_variant,
                "invite_policy_rollout_percent": invite_policy_rollout_percent,
                "invite_code_optional": invite_code_optional,
            },
        )
    except Exception as e:
        log_with_context(
            logger,
            logging.WARNING,
            "Failed to record signup activation event",
            user_id=new_user.id,
            error=str(e),
            error_type=type(e).__name__,
        )

    return {
        "message": get_message("auth_register_success", _accept_language),
        "email": normalized_email,
        "email_verified": False,
        "verification_sent": success,
    }


@router.post("/login", response_model=LoginResponse)
async def login(
    http_request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    session: Session = Depends(get_session),
    _accept_language: str = Depends(get_accept_language)
):
    """
    Login with username/email and password.

    - Accepts username OR email in the username field
    - Returns access token and refresh token
    - Uses OAuth2 password flow
    - Requires email verification for regular login
    """
    log_with_context(
        logger,
        logging.INFO,
        "Login attempt",
        identifier=form_data.username,
        client_ip=get_client_ip(http_request),
    )

    # Find user by username or email
    identifier = form_data.username.strip()
    normalized_identifier = identifier.lower()

    if _is_auth_rate_limit_enabled():
        _enforce_auth_rate_limit(
            http_request=http_request,
            key="auth_login_ip",
            max_requests=_safe_int_from_env(
                "AUTH_LOGIN_RATE_LIMIT_PER_MINUTE",
                30,
                minimum=1,
            ),
            window_seconds=60,
            endpoint="login",
            identifier=normalized_identifier,
        )

        if normalized_identifier:
            _enforce_auth_rate_limit(
                http_request=http_request,
                key=f"auth_login_identifier:{normalized_identifier}",
                max_requests=_safe_int_from_env(
                    "AUTH_LOGIN_IDENTIFIER_RATE_LIMIT_PER_10_MIN",
                    20,
                    minimum=1,
                ),
                window_seconds=600,
                endpoint="login",
                identifier=normalized_identifier,
                include_client_ip=False,
            )

    user = session.exec(
        select(User).where(
            (User.username == identifier) | email_identity_matches(User.email, identifier)
        )
    ).first()

    password_ok = bool(user) and await asyncio.to_thread(
        verify_password, form_data.password, user.hashed_password
    )
    if not user or not password_ok:
        log_with_context(
            logger,
            logging.WARNING,
            "Login failed: invalid credentials",
            identifier=identifier,
            client_ip=get_client_ip(http_request),
        )
        raise APIException(
            error_code=ErrorCode.AUTH_INVALID_CREDENTIALS,
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        log_with_context(
            logger,
            logging.WARNING,
            "Login failed: inactive user",
            user_id=user.id,
            username=user.username,
            client_ip=get_client_ip(http_request),
        )
        raise APIException(
            error_code=ErrorCode.AUTH_INACTIVE_USER,
            status_code=status.HTTP_400_BAD_REQUEST
        )

    # Check email verification status
    # Skip verification check if user registered via OAuth (has empty hashed_password)
    if not user.email_verified and user.hashed_password:
        log_with_context(
            logger,
            logging.WARNING,
            "Login failed: email not verified",
            user_id=user.id,
            email=user.email,
            client_ip=get_client_ip(http_request),
        )
        raise APIException(
            error_code=ErrorCode.AUTH_EMAIL_NOT_VERIFIED,
            status_code=status.HTTP_403_FORBIDDEN
        )

    # Generate tokens (refresh token is one-time-use with rotation metadata)
    access_token = create_access_token(data={"sub": user.id})
    refresh_token_jti = generate_token_jti()
    refresh_family_id = generate_token_jti()
    refresh_token = create_refresh_token(
        data={"sub": user.id, "jti": refresh_token_jti, "family_id": refresh_family_id}
    )
    _create_refresh_record(
        session,
        user_id=user.id,
        token_jti=refresh_token_jti,
        family_id=refresh_family_id,
    )
    session.commit()
    _run_refresh_token_housekeeping(session)

    log_with_context(
        logger,
        logging.INFO,
        "User logged in successfully",
        user_id=user.id,
        username=user.username,
        email_verified=user.email_verified,
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


@router.post("/refresh", response_model=LoginResponse)
def refresh_token(
    request: RefreshTokenRequest,
    http_request: Request,
    session: Session = Depends(get_session),
    _accept_language: str = Depends(get_accept_language)
):
    """
    Refresh access token using refresh token.

    - Accepts refresh token
    - Returns new access token and refresh token
    """
    if _is_auth_rate_limit_enabled():
        _enforce_auth_rate_limit(
            http_request=http_request,
            key="auth_refresh_ip",
            max_requests=_safe_int_from_env(
                "AUTH_REFRESH_RATE_LIMIT_PER_MINUTE",
                60,
                minimum=1,
            ),
            window_seconds=60,
            endpoint="refresh",
        )

    payload = verify_token(request.refresh_token, expected_type=TOKEN_TYPE_REFRESH)
    if payload is None:
        log_with_context(
            logger,
            logging.WARNING,
            "Refresh token rejected: invalid payload",
            client_ip=get_client_ip(http_request),
        )
        raise APIException(
            error_code=ErrorCode.AUTH_TOKEN_INVALID,
            status_code=status.HTTP_401_UNAUTHORIZED
        )

    user_id = payload.get("sub")
    if user_id is None:
        log_with_context(
            logger,
            logging.WARNING,
            "Refresh token rejected: missing subject",
            client_ip=get_client_ip(http_request),
        )
        raise APIException(
            error_code=ErrorCode.AUTH_TOKEN_INVALID,
            status_code=status.HTTP_401_UNAUTHORIZED
        )

    token_jti = payload.get("jti")
    family_id = payload.get("family_id")

    if (not token_jti or not family_id) and not ALLOW_LEGACY_REFRESH_WITHOUT_JTI:
        log_with_context(
            logger,
            logging.WARNING,
            "Refresh token rejected: missing jti/family_id",
            client_ip=get_client_ip(http_request),
            user_id=user_id,
        )
        raise APIException(
            error_code=ErrorCode.AUTH_TOKEN_INVALID,
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    # Serialize rotation with logout/password revocation before locking a JTI.
    user = session.exec(
        select(User).where(User.id == user_id).with_for_update()
        .execution_options(populate_existing=True)
    ).first()
    if not user or not user.is_active:
        log_with_context(
            logger,
            logging.WARNING,
            "Refresh token rejected: user missing or inactive",
            client_ip=get_client_ip(http_request),
            user_id=user_id,
        )
        raise APIException(
            error_code=ErrorCode.AUTH_UNAUTHORIZED,
            status_code=status.HTTP_401_UNAUTHORIZED
        )

    now = utcnow()
    refresh_record: RefreshTokenRecord | None = None

    if token_jti:
        refresh_record = session.exec(
            select(RefreshTokenRecord)
            .where(
                RefreshTokenRecord.token_jti == token_jti,
                RefreshTokenRecord.user_id == user.id,
            )
            .with_for_update()
        ).first()

        if not refresh_record:
            log_with_context(
                logger,
                logging.WARNING,
                "Refresh token rejected: jti not found",
                client_ip=get_client_ip(http_request),
                user_id=user.id,
                token_jti=token_jti,
            )
            raise APIException(
                error_code=ErrorCode.AUTH_TOKEN_INVALID,
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

        if normalize_datetime_to_utc(refresh_record.expires_at) <= now:
            refresh_record.revoked_at = now
            refresh_record.revoke_reason = "expired"
            refresh_record.updated_at = now
            session.add(refresh_record)
            session.commit()
            log_with_context(
                logger,
                logging.WARNING,
                "Refresh token rejected: token expired",
                client_ip=get_client_ip(http_request),
                user_id=user.id,
                token_jti=token_jti,
            )
            raise APIException(
                error_code=ErrorCode.AUTH_TOKEN_EXPIRED,
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

        if refresh_record.revoked_at is not None:
            revoked_count = _revoke_refresh_family(
                session,
                family_id=refresh_record.family_id,
                reason="replay_detected",
            )
            session.commit()
            log_with_context(
                logger,
                logging.WARNING,
                "Refresh token replay detected; family revoked",
                user_id=user.id,
                family_id=refresh_record.family_id,
                revoked_count=revoked_count,
            )
            raise APIException(
                error_code=ErrorCode.AUTH_TOKEN_INVALID,
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

        family_id = refresh_record.family_id

    # Rotate refresh token
    access_token = create_access_token(data={"sub": user.id})
    new_refresh_jti = generate_token_jti()
    resolved_family_id = family_id or generate_token_jti()
    new_refresh_token = create_refresh_token(
        data={"sub": user.id, "jti": new_refresh_jti, "family_id": resolved_family_id}
    )

    if refresh_record is not None:
        refresh_record.revoked_at = now
        refresh_record.revoke_reason = "rotated"
        refresh_record.replaced_by_jti = new_refresh_jti
        refresh_record.updated_at = now
        session.add(refresh_record)

    _create_refresh_record(
        session,
        user_id=user.id,
        token_jti=new_refresh_jti,
        family_id=resolved_family_id,
    )
    session.commit()
    _run_refresh_token_housekeeping(session)

    return {
        "access_token": access_token,
        "refresh_token": new_refresh_token,
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


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    current_user: User = Depends(get_current_active_user)
):
    """
    Get current user information.

    - Requires authentication
    - Returns current user profile
    """
    return current_user


@router.put("/me", response_model=UserResponse)
def update_current_user(
    request: UpdateUserRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Update current user information.

    - Requires authentication
    - Updates user profile
    - Username and email must be unique
    """
    # Check if username is being changed and is unique
    if request.username and request.username != current_user.username:
        existing_user = session.exec(
            select(User).where(User.username == request.username)
        ).first()
        if existing_user:
            raise APIException(
                error_code=ErrorCode.AUTH_USERNAME_EXISTS,
                status_code=status.HTTP_400_BAD_REQUEST
            )
        current_user.username = request.username

    # Check if email is being changed and is unique
    if request.email:
        normalized_email = normalize_email_identity(str(request.email))
        current_normalized_email = normalize_email_identity(current_user.email)
        if normalized_email != current_normalized_email:
            existing_email = session.exec(
                select(User).where(email_identity_matches(User.email, normalized_email))
            ).first()
            if existing_email:
                raise APIException(
                    error_code=ErrorCode.AUTH_EMAIL_EXISTS,
                    status_code=status.HTTP_400_BAD_REQUEST
                )
            current_user.email = normalized_email
            current_user.email_verified = False
        elif current_user.email != normalized_email:
            current_user.email = normalized_email

    # Update avatar URL
    if request.avatar_url is not None:
        current_user.avatar_url = request.avatar_url

    # Update timestamp
    current_user.updated_at = utcnow()

    session.add(current_user)
    session.commit()
    session.refresh(current_user)

    return current_user


@router.post("/change-password")
async def change_password(
    request: ChangePasswordRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
    accept_language: str = Depends(get_accept_language)
):
    """
    Change user password.

    - Requires authentication
    - Verifies old password before changing
    """
    # Verify old password
    if not await asyncio.to_thread(verify_password, request.old_password, current_user.hashed_password):
        raise APIException(
            error_code=ErrorCode.AUTH_INVALID_CREDENTIALS,
            status_code=status.HTTP_400_BAD_REQUEST
        )

    # Update password
    current_user.hashed_password = await asyncio.to_thread(hash_password, request.new_password)
    current_user.updated_at = utcnow()

    session.add(current_user)
    revoked_count = _revoke_active_refresh_tokens_for_user(
        session,
        user_id=current_user.id,
        reason="password_changed",
    )
    session.commit()
    _run_refresh_token_housekeeping(session)
    if revoked_count > 0:
        log_with_context(
            logger,
            logging.INFO,
            "Revoked active refresh tokens after password change",
            user_id=current_user.id,
            revoked_count=revoked_count,
        )

    return {"message": get_message("auth_password_changed", accept_language)}


@router.post("/logout")
def logout(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
    _accept_language: str = Depends(get_accept_language)
):
    """
    Logout current user.

    - Requires authentication
    - Note: JWT tokens are stateless, so this is mainly for client-side cleanup
    - In production, consider using a token blacklist
    """
    revoked_count = _revoke_active_refresh_tokens_for_user(
        session,
        user_id=current_user.id,
        reason="logout",
    )
    if revoked_count > 0:
        session.commit()
        _run_refresh_token_housekeeping(session)

    return {"message": get_message("auth_logout_success", _accept_language)}
