"""Google OAuth API endpoints"""
import base64
import json
import logging
import os
import re
import secrets
import urllib.parse

import httpx
from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import RedirectResponse
from services.auth import (
    TOKEN_TYPE_ACCESS,
    access_token_claims,
    access_token_matches_password,
    create_access_token,
    create_refresh_token,
    generate_token_jti,
    get_refresh_token_expires_at,
    verify_token,
)
from sqlmodel import Session, select

from api.auth import (
    _is_invite_code_expired,
    _normalize_invite_code,
    _resolve_registration_invite_policy,
)
from config.datetime_utils import utcnow
from core.error_codes import ErrorCode
from core.error_handler import APIException
from database import get_session
from middleware.rate_limit import get_client_ip
from models import ACTIVATION_EVENT_SIGNUP_SUCCESS, RefreshTokenRecord, User
from models.referral import InviteCode
from services.features.activation_event_service import activation_event_service
from services.features.referral_service import create_referral as create_referral_service
from services.subscription.subscription_service import subscription_service
from utils.email_identity import email_identity_matches, normalize_email_identity
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

# Google OAuth Configuration
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "")
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
OAUTH_STATE_COOKIE_NAME = "oauth_google_state"
OAUTH_STATE_TTL_SECONDS = int(os.getenv("OAUTH_STATE_TTL_SECONDS", "600"))
OAUTH_STATE_INVITE_CODE_KEY = "invite_code"
SSO_ALLOWED_REDIRECT_DOMAINS = [
    domain.strip().lower()
    for domain in os.getenv(
        "SSO_ALLOWED_REDIRECT_DOMAINS",
        "zenstory.ai,www.zenstory.ai",
    ).split(",")
    if domain.strip()
]

router = APIRouter(prefix="/api/auth", tags=["auth"])

_INVITE_CODE_ALLOWED_CHARS_RE = re.compile(r"[^A-Z0-9-]+")


def _sanitize_invite_code(raw: str | None) -> str | None:
    """
    Normalize and sanitize invite code for safe transport in OAuth state.

    Notes:
    - Invite codes are not secrets, but we still sanitize/trim to avoid abuse.
    - Accept both "XXXXXXXX" and "XXXX-XXXX" formats.
    """
    if not raw:
        return None

    cleaned = _normalize_invite_code(raw)
    cleaned = _INVITE_CODE_ALLOWED_CHARS_RE.sub("", cleaned)
    if not cleaned:
        return None

    if len(cleaned) == 8 and "-" not in cleaned:
        cleaned = f"{cleaned[:4]}-{cleaned[4:]}"

    # Defensive cap (real codes are 9 chars).
    if len(cleaned) > 32:
        cleaned = cleaned[:32]

    return cleaned or None


def _redirect_to_frontend_register(
    *,
    error_code: str,
    redirect: str | None = None,
    invite_code: str | None = None,
) -> RedirectResponse:
    """
    Redirect back to frontend register page with a stable error code.

    This avoids showing raw JSON API errors to users when OAuth callback fails.
    """
    frontend_url = os.getenv("FRONTEND_URL", "https://zenstory.ai")
    params: dict[str, str] = {"error_code": error_code}
    if invite_code:
        params["code"] = invite_code
    if redirect and _is_allowed_redirect_url(redirect):
        params["redirect"] = redirect

    url = f"{frontend_url}/register?{urllib.parse.urlencode(params)}"
    response = RedirectResponse(url)
    response.delete_cookie(
        key=OAUTH_STATE_COOKIE_NAME,
        path="/",
        secure=_should_use_secure_cookie(),
        samesite="lax",
    )
    return response


def _redirect_to_frontend_auth_callback(
    *,
    error_code: str,
    redirect: str | None = None,
) -> RedirectResponse:
    """Redirect OAuth callback failures to the frontend error UI."""
    frontend_url = os.getenv("FRONTEND_URL", "https://zenstory.ai")
    params: dict[str, str] = {"error_code": error_code}
    if redirect and _is_allowed_redirect_url(redirect):
        params["redirect"] = redirect

    url = f"{frontend_url}/auth/callback?{urllib.parse.urlencode(params)}"
    response = RedirectResponse(url)
    response.delete_cookie(
        key=OAUTH_STATE_COOKIE_NAME,
        path="/",
        secure=_should_use_secure_cookie(),
        samesite="lax",
    )
    return response


def _ensure_user_free_subscription_and_quota(
    session: Session,
    user_id: str,
    source: str,
) -> None:
    """Ensure OAuth user has baseline free subscription and quota records."""
    subscription_service.ensure_user_subscription_and_quota(
        session=session,
        user_id=user_id,
        source=source,
    )


def _should_use_secure_cookie() -> bool:
    return GOOGLE_REDIRECT_URI.startswith("https://")


def _encode_oauth_state(payload: dict[str, str]) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _decode_oauth_state(state: str) -> dict[str, str] | None:
    try:
        padded = state + "=" * (-len(state) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("utf-8"))
        payload = json.loads(decoded.decode("utf-8"))
        if not isinstance(payload, dict):
            return None
        normalized: dict[str, str] = {}
        for key, value in payload.items():
            if isinstance(key, str) and isinstance(value, str):
                normalized[key] = value
        return normalized
    except Exception:
        return None


def _is_allowed_redirect_url(redirect_url: str) -> bool:
    """Validate post-login redirect URL against allowed domains."""
    try:
        parsed = urllib.parse.urlparse(redirect_url)
        _ = parsed.port
    except ValueError:
        return False

    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    if parsed.username or parsed.password:
        return False

    host = parsed.netloc.lower()
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return False
    if parsed.scheme == "http" and hostname not in {"localhost", "127.0.0.1"}:
        return False

    for allowed in SSO_ALLOWED_REDIRECT_DOMAINS:
        # Allow localhost entries with explicit port.
        if ":" in allowed and host == allowed:
            return True
        # Allow localhost/127.0.0.1 with any port if explicitly configured.
        if allowed in {"localhost", "127.0.0.1"} and hostname == allowed:
            return True
        # Allow exact domain and subdomains for normal domains.
        if hostname == allowed or hostname.endswith(f".{allowed}"):
            return True

    return False


@router.get("/google")
async def google_oauth_login(
    redirect: str | None = Query(None, description="Redirect URL after successful login"),
    invite_code: str | None = Query(None, description="Invite code for new user registration"),
    invite: str | None = Query(None, description="Alias for invite_code"),
    code: str | None = Query(None, description="Alias for invite_code"),
):
    """
    Initiate Google OAuth flow.

    Redirects user to Google's OAuth consent page.
    """
    if not GOOGLE_CLIENT_ID or not GOOGLE_REDIRECT_URI:
        log_with_context(
            logger,
            logging.ERROR,
            "Google OAuth login failed: not configured",
        )
        return _redirect_to_frontend_auth_callback(
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            redirect=redirect,
        )

    # Construct authorization URL
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
    }

    # Always include CSRF nonce in state and persist it in HttpOnly cookie.
    state_payload: dict[str, str] = {"nonce": secrets.token_urlsafe(24)}

    # Include redirect URL in state only when it is allowlisted.
    if redirect:
        if _is_allowed_redirect_url(redirect):
            state_payload["redirect"] = redirect
        else:
            log_with_context(
                logger,
                logging.WARNING,
                "Rejected unsafe OAuth redirect URL",
                redirect=redirect,
            )

    # Include invite code in state for new-user gating (sanitize to keep payload small).
    raw_invite_code = invite_code or invite or code
    sanitized_invite_code = _sanitize_invite_code(raw_invite_code)
    if sanitized_invite_code:
        state_payload[OAUTH_STATE_INVITE_CODE_KEY] = sanitized_invite_code

    params["state"] = _encode_oauth_state(state_payload)
    auth_url = f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"
    response = RedirectResponse(auth_url)
    response.set_cookie(
        key=OAUTH_STATE_COOKIE_NAME,
        value=state_payload["nonce"],
        max_age=OAUTH_STATE_TTL_SECONDS,
        httponly=True,
        secure=_should_use_secure_cookie(),
        samesite="lax",
        path="/",
    )
    return response


@router.get("/validate-token")
def validate_token(
    token: str = Query(..., description="JWT access token to validate"),
    session: Session = Depends(get_session)
):
    """
    Validate JWT access token and return user information.

    - Accepts token as query parameter
    - Returns user info if token is valid
    - Returns 401 if token is invalid or expired
    """
    payload = verify_token(token, expected_type=TOKEN_TYPE_ACCESS)
    if payload is None:
        raise APIException(
            error_code=ErrorCode.AUTH_TOKEN_INVALID,
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    if user_id is None:
        raise APIException(
            error_code=ErrorCode.AUTH_TOKEN_INVALID,
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = session.get(User, user_id)
    if user is None or not access_token_matches_password(payload, user):
        raise APIException(
            error_code=ErrorCode.AUTH_TOKEN_INVALID,
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise APIException(
            error_code=ErrorCode.AUTH_INACTIVE_USER,
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    return {
        "id": user.id,
        "username": user.username,
        "email": user.email
    }


@router.get("/google/callback")
async def google_oauth_callback(
    request: Request,
    code: str = Query(..., description="OAuth authorization code"),
    state: str | None = Query(None, description="OAuth state parameter containing redirect URL"),
    session: Session = Depends(get_session)
):
    """
    Handle Google OAuth callback.

    - Exchanges authorization code for access token
    - Fetches user info from Google
    - Creates or logs in user
    - Returns JWT tokens and redirects to frontend
    """
    log_with_context(
        logger,
        logging.INFO,
        "Google OAuth callback received",
    )

    if not state:
        return _redirect_to_frontend_auth_callback(
            error_code=ErrorCode.AUTH_TOKEN_INVALID,
        )

    state_payload = _decode_oauth_state(state)
    if state_payload is None:
        return _redirect_to_frontend_auth_callback(
            error_code=ErrorCode.AUTH_TOKEN_INVALID,
        )

    redirect_from_state = state_payload.get("redirect")

    state_nonce = state_payload.get("nonce")
    cookie_nonce = request.cookies.get(OAUTH_STATE_COOKIE_NAME) if request else None
    if not state_nonce or not cookie_nonce or state_nonce != cookie_nonce:
        return _redirect_to_frontend_auth_callback(
            error_code=ErrorCode.AUTH_TOKEN_INVALID,
            redirect=redirect_from_state,
        )

    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        log_with_context(
            logger,
            logging.ERROR,
            "Google OAuth callback failed: not configured",
        )
        return _redirect_to_frontend_auth_callback(
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            redirect=redirect_from_state,
        )

    # Exchange code for access token
    async with httpx.AsyncClient() as client:
        try:
            token_response = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "redirect_uri": GOOGLE_REDIRECT_URI,
                    "grant_type": "authorization_code",
                }
            )
        except httpx.HTTPError:
            return _redirect_to_frontend_auth_callback(
                error_code=ErrorCode.AUTH_TOKEN_INVALID,
                redirect=redirect_from_state,
            )

        if token_response.status_code != 200:
            log_with_context(
                logger,
                logging.ERROR,
                "Google OAuth token exchange failed",
                status_code=token_response.status_code,
            )
            return _redirect_to_frontend_auth_callback(
                error_code=ErrorCode.AUTH_TOKEN_INVALID,
                redirect=redirect_from_state,
            )

        try:
            token_data = token_response.json()
        except ValueError:
            return _redirect_to_frontend_auth_callback(
                error_code=ErrorCode.AUTH_TOKEN_INVALID,
                redirect=redirect_from_state,
            )
        if not isinstance(token_data, dict):
            return _redirect_to_frontend_auth_callback(
                error_code=ErrorCode.AUTH_TOKEN_INVALID,
                redirect=redirect_from_state,
            )
        access_token = token_data.get("access_token")

        if not access_token:
            log_with_context(
                logger,
                logging.ERROR,
                "Google OAuth token response missing access token",
            )
            return _redirect_to_frontend_auth_callback(
                error_code=ErrorCode.AUTH_TOKEN_INVALID,
                redirect=redirect_from_state,
            )

        # Fetch user info from Google
        try:
            user_response = await client.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"}
            )
        except httpx.HTTPError:
            return _redirect_to_frontend_auth_callback(
                error_code=ErrorCode.AUTH_TOKEN_INVALID,
                redirect=redirect_from_state,
            )

        if user_response.status_code != 200:
            log_with_context(
                logger,
                logging.ERROR,
                "Google OAuth user info fetch failed",
                status_code=user_response.status_code,
            )
            return _redirect_to_frontend_auth_callback(
                error_code=ErrorCode.AUTH_TOKEN_INVALID,
                redirect=redirect_from_state,
            )

        try:
            google_user = user_response.json()
        except ValueError:
            return _redirect_to_frontend_auth_callback(
                error_code=ErrorCode.AUTH_TOKEN_INVALID,
                redirect=redirect_from_state,
            )
        if not isinstance(google_user, dict):
            return _redirect_to_frontend_auth_callback(
                error_code=ErrorCode.AUTH_TOKEN_INVALID,
                redirect=redirect_from_state,
            )
        google_email = google_user.get("email")
        google_name = google_user.get("name")
        google_picture = google_user.get("picture")

        if not google_email:
            log_with_context(
                logger,
                logging.ERROR,
                "Google OAuth user info missing email",
            )
            return _redirect_to_frontend_auth_callback(
                error_code=ErrorCode.AUTH_TOKEN_INVALID,
                redirect=redirect_from_state,
            )
        google_email = normalize_email_identity(str(google_email))

    # Check if user exists by email
    existing_user = session.exec(
        select(User).where(email_identity_matches(User.email, google_email))
    ).first()

    raw_state_invite = state_payload.get(OAUTH_STATE_INVITE_CODE_KEY)
    normalized_invite_code = _sanitize_invite_code(raw_state_invite)

    is_new_user = False
    if existing_user:
        if not existing_user.is_active:
            log_with_context(
                logger,
                logging.WARNING,
                "Google OAuth rejected inactive user",
                user_id=existing_user.id,
                email=google_email,
            )
            return _redirect_to_frontend_auth_callback(
                error_code=ErrorCode.AUTH_INACTIVE_USER,
                redirect=redirect_from_state,
            )
        log_with_context(
            logger,
            logging.INFO,
            "Google OAuth: existing user login",
            user_id=existing_user.id,
            email=google_email,
        )
        # Update user info.
        # The lookup above already matched this account case-insensitively, so
        # do NOT rewrite the stored email casing here: the email column has a
        # case-sensitive UNIQUE constraint, and lowercasing a legacy mixed-case
        # address can collide with another row and raise IntegrityError on an
        # otherwise-valid OAuth login.
        if google_name and existing_user.username == google_email.split("@")[0]:
            # Only update username if it's still the default email prefix
            existing_user.username = google_name
        if google_picture:
            existing_user.avatar_url = google_picture
        existing_user.updated_at = utcnow()
        session.add(existing_user)
        session.commit()
        session.refresh(existing_user)

        user = existing_user
    else:
        log_with_context(
            logger,
            logging.INFO,
            "Google OAuth: creating new user",
            email=google_email,
            username=google_name,
        )

        invite_code_optional, invite_policy_variant, invite_policy_rollout_percent = (
            _resolve_registration_invite_policy(
                email=str(google_email),
                username=str(google_name) if google_name else None,
            )
        )

        # Invite code is required by default (unless env explicitly allows optional).
        if not normalized_invite_code and not invite_code_optional:
            return _redirect_to_frontend_register(
                error_code=ErrorCode.AUTH_INVITE_CODE_REQUIRED,
                redirect=redirect_from_state,
            )

        invite_code_obj: InviteCode | None = None
        if normalized_invite_code:
            invite_code_obj = session.exec(
                select(InviteCode).where(InviteCode.code == normalized_invite_code)
            ).first()

            if not invite_code_obj:
                return _redirect_to_frontend_register(
                    error_code=ErrorCode.REFERRAL_CODE_INVALID,
                    redirect=redirect_from_state,
                    invite_code=normalized_invite_code,
                )

            if not invite_code_obj.is_active:
                return _redirect_to_frontend_register(
                    error_code=ErrorCode.REFERRAL_CODE_INVALID,
                    redirect=redirect_from_state,
                    invite_code=normalized_invite_code,
                )

            if invite_code_obj.current_uses >= invite_code_obj.max_uses:
                return _redirect_to_frontend_register(
                    error_code=ErrorCode.REFERRAL_CODE_USED_UP,
                    redirect=redirect_from_state,
                    invite_code=normalized_invite_code,
                )

            if _is_invite_code_expired(invite_code_obj.expires_at):
                return _redirect_to_frontend_register(
                    error_code=ErrorCode.REFERRAL_CODE_EXPIRED,
                    redirect=redirect_from_state,
                    invite_code=normalized_invite_code,
                )

            # SECURITY: Prevent self-referral (check if owner has same email)
            owner = session.get(User, invite_code_obj.owner_id)
            if owner and normalize_email_identity(owner.email) == google_email:
                log_with_context(
                    logger,
                    logging.WARNING,
                    "Self-referral attempt blocked (oauth)",
                    email=google_email,
                    invite_code=normalized_invite_code,
                )
                return _redirect_to_frontend_register(
                    error_code=ErrorCode.REFERRAL_CODE_INVALID,
                    redirect=redirect_from_state,
                    invite_code=normalized_invite_code,
                )

        # Create new user
        # Use name from Google, or fallback to email prefix
        original_username = google_name or google_email.split("@")[0]

        # Check if username already exists
        # Optimize: Use single query instead of loop
        username = original_username
        if session.exec(
            select(User).where(User.username == original_username)
        ).first():
            # If base username exists, query all conflicting usernames at once
            conflicting_usernames = session.exec(
                select(User.username).where(User.username.like(f"{original_username}%"))
            ).all()

            # Find the maximum numeric suffix
            max_suffix = 0
            for name in conflicting_usernames:
                if name == original_username:
                    continue
                if name.startswith(original_username):
                    suffix = name[len(original_username):]
                    if suffix.isdigit():
                        max_suffix = max(max_suffix, int(suffix))

            # Generate unique username with the next available suffix
            username = f"{original_username}{max_suffix + 1}"

        new_user = User(
            username=username,
            email=google_email,
            hashed_password="",  # No password for OAuth users
            avatar_url=google_picture,
            is_active=True,
            email_verified=True  # OAuth users are automatically verified
        )

        session.add(new_user)
        session.commit()
        session.refresh(new_user)

        # Create referral relationship if valid invite code was used
        if invite_code_obj:
            try:
                await create_referral_service(
                    invite_code=invite_code_obj,
                    invitee_id=new_user.id,
                    session=session,
                    device_fingerprint=None,
                    ip_address=get_client_ip(request),
                )
                log_with_context(
                    logger,
                    logging.INFO,
                    "Referral relationship created (oauth)",
                    inviter_id=invite_code_obj.owner_id,
                    invitee_id=new_user.id,
                    invite_code=normalized_invite_code,
                    invite_policy_variant=invite_policy_variant,
                    invite_policy_rollout_percent=invite_policy_rollout_percent,
                    invite_code_optional=invite_code_optional,
                )
            except Exception as e:
                session.rollback()
                log_with_context(
                    logger,
                    logging.WARNING,
                    "Referral creation failed after OAuth user creation",
                    invite_code_id=invite_code_obj.id,
                    invitee_id=new_user.id,
                    error=str(e),
                    error_type=type(e).__name__,
                )

        # Activation milestone: signup completed. Never blocks the login.
        try:
            activation_event_service.record_once(
                session,
                user_id=new_user.id,
                event_name=ACTIVATION_EVENT_SIGNUP_SUCCESS,
                event_metadata={
                    "source": "google_oauth",
                    "invite_code_provided": bool(normalized_invite_code),
                    "invite_policy_variant": invite_policy_variant,
                    "invite_policy_rollout_percent": invite_policy_rollout_percent,
                    "invite_code_optional": invite_code_optional,
                },
            )
        except Exception as e:
            session.rollback()
            log_with_context(
                logger,
                logging.WARNING,
                "Failed to record signup activation event (oauth)",
                user_id=new_user.id,
                error=str(e),
                error_type=type(e).__name__,
            )

        is_new_user = True
        user = new_user

    try:
        _ensure_user_free_subscription_and_quota(
            session=session,
            user_id=user.id,
            source="google_oauth",
        )
    except Exception as e:
        # Bootstrap is best effort; a failed flush must not poison token issuance.
        session.rollback()
        log_with_context(
            logger,
            logging.WARNING,
            "Failed to bootstrap OAuth user subscription/quota",
            user_id=user.id,
            error=str(e),
            error_type=type(e).__name__,
        )

    # Generate JWT tokens
    jwt_access_token = create_access_token(data=access_token_claims(user))
    refresh_token_jti = generate_token_jti()
    refresh_family_id = generate_token_jti()
    jwt_refresh_token = create_refresh_token(
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
    session.commit()

    log_with_context(
        logger,
        logging.INFO,
        "Google OAuth login successful",
        user_id=user.id,
        email=user.email,
    )

    # Get frontend URL from environment
    frontend_url = os.getenv("FRONTEND_URL", "https://zenstory.ai")

    # Redirect to frontend with tokens in URL hash (safer than query params).
    callback_params = {
        "access_token": jwt_access_token,
        "refresh_token": jwt_refresh_token,
    }
    if is_new_user:
        # Lets the frontend count the signup; the hash is scrubbed before any
        # analytics sees the URL.
        callback_params["new_user"] = "1"

    # Append redirect parameter if present in state and passes whitelist validation.
    if redirect_from_state:
        if _is_allowed_redirect_url(redirect_from_state):
            callback_params["redirect"] = redirect_from_state
        else:
            log_with_context(
                logger,
                logging.WARNING,
                "Dropped unsafe OAuth callback redirect URL",
                redirect=redirect_from_state,
            )

    redirect_url = f"{frontend_url}/auth/callback#{urllib.parse.urlencode(callback_params)}"

    response = RedirectResponse(redirect_url)
    response.delete_cookie(
        key=OAUTH_STATE_COOKIE_NAME,
        path="/",
        secure=_should_use_secure_cookie(),
        samesite="lax",
    )
    return response
