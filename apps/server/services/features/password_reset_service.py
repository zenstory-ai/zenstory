"""
Self-service password reset with an emailed one-time code.

- Codes live in Redis under ``pwreset:*`` (see ``services.infra.redis_client``),
  apart from registration codes; only ``sha256(code)`` is stored.
- ``request_reset`` behaves the same for every address: the same response, the
  same minimum duration, and the email is sent from a background task. A code
  is only issued for an active account with a verified email.
- ``confirm_reset`` reports an unknown account and a wrong/expired code with the
  same error. The fifth wrong code deletes the stored code. Wrong codes are also
  counted per address for a day (``pwreset:fails``), which a new code does not
  reset; at the cap confirm always fails and request issues no code.
- Success rewrites the password and revokes every refresh token in one
  transaction; the new password fingerprint makes every previously issued
  access token fail as well.
"""
import asyncio
import hashlib
import hmac
import os
import re
import secrets
import time

from fastapi import BackgroundTasks, status
from sqlmodel import Session, select

from config.datetime_utils import utcnow
from core.error_codes import ErrorCode
from core.error_handler import APIException
from models import User
from services.core.auth_service import hash_password, revoke_active_refresh_tokens_for_user
from services.infra import redis_client
from services.infra.email_client import EMAIL_PURPOSE_PASSWORD_RESET, send_verification_email
from utils.email_identity import email_identity_matches, normalize_email_identity
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)


def _non_negative_env(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    try:
        value = float(raw) if raw else default
    except ValueError:
        return default
    return value if value >= 0 else default


PASSWORD_RESET_CODE_TTL = int(_non_negative_env("PASSWORD_RESET_CODE_TTL", 600)) or 600
PASSWORD_RESET_COOLDOWN_SECONDS = 60
PASSWORD_RESET_MAX_ATTEMPTS = 5
# Per-address wrong-code budget across all codes; requesting a new code does not
# restore it, so guessing cannot continue indefinitely by re-requesting.
PASSWORD_RESET_MAX_DAILY_FAILURES = 10
PASSWORD_RESET_FAILURE_WINDOW_SECONDS = 24 * 60 * 60
PASSWORD_RESET_CODE_LENGTH = 6
# Floor for the request endpoint so an issued code (two Redis writes) and an
# ignored address take the same time from the client's point of view.
PASSWORD_RESET_REQUEST_MIN_SECONDS = _non_negative_env("PASSWORD_RESET_REQUEST_MIN_SECONDS", 0.4)

_CODE_PATTERN = re.compile(rf"[0-9]{{{PASSWORD_RESET_CODE_LENGTH}}}")


def hash_reset_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def generate_reset_code() -> str:
    return "".join(secrets.choice("0123456789") for _ in range(PASSWORD_RESET_CODE_LENGTH))


def code_ttl_minutes() -> int:
    return max(1, PASSWORD_RESET_CODE_TTL // 60)


def _invalid_code() -> APIException:
    return APIException(
        error_code=ErrorCode.AUTH_PASSWORD_RESET_CODE_INVALID,
        status_code=status.HTTP_400_BAD_REQUEST,
    )


def _find_user(session: Session, email: str, *, for_update: bool = False) -> User | None:
    statement = select(User).where(email_identity_matches(User.email, email))
    if for_update:
        statement = statement.with_for_update()
    return session.exec(statement).first()


def _can_reset(user: User | None) -> bool:
    return bool(user and user.is_active and user.email_verified)


async def _send_reset_email(email_key: str, recipient: str, code: str, language: str) -> None:
    """Background task: deliver the code; on failure drop it so a retry works at once."""
    sent = await send_verification_email(
        recipient,
        code,
        code_ttl_minutes(),
        language=language,
        purpose=EMAIL_PURPOSE_PASSWORD_RESET,
    )
    if not sent:
        await asyncio.to_thread(redis_client.clear_password_reset_state, email_key)
        log_with_context(logger, 30, "Password reset email not sent; code discarded")


async def _issue_code_if_eligible(
    session: Session,
    email_key: str,
    language: str,
    background_tasks: BackgroundTasks,
) -> None:
    user = _find_user(session, email_key)
    if not _can_reset(user):
        return
    assert user is not None
    if await asyncio.to_thread(
        redis_client.password_reset_failures_capped, email_key, PASSWORD_RESET_MAX_DAILY_FAILURES,
    ):
        return
    if not await asyncio.to_thread(
        redis_client.claim_password_reset_cooldown, email_key, PASSWORD_RESET_COOLDOWN_SECONDS,
    ):
        return

    code = generate_reset_code()
    stored = await asyncio.to_thread(
        redis_client.store_password_reset_code, email_key, hash_reset_code(code), PASSWORD_RESET_CODE_TTL,
    )
    if not stored:
        await asyncio.to_thread(redis_client.clear_password_reset_state, email_key)
        return

    background_tasks.add_task(_send_reset_email, email_key, user.email, code, language)
    log_with_context(logger, 20, "Password reset code issued", user_id=user.id)


async def request_reset(
    session: Session,
    email: str,
    language: str,
    background_tasks: BackgroundTasks,
) -> None:
    """Issue a reset code when the account qualifies; never reveals whether it does."""
    started = time.monotonic()
    try:
        await _issue_code_if_eligible(
            session, normalize_email_identity(email), language, background_tasks,
        )
    except Exception as error:  # A failure must look exactly like an ignored address.
        session.rollback()
        log_with_context(
            logger, 40, "Password reset request failed", error_type=type(error).__name__,
        )
    remaining = PASSWORD_RESET_REQUEST_MIN_SECONDS - (time.monotonic() - started)
    if remaining > 0:
        await asyncio.sleep(remaining)


async def confirm_reset(session: Session, email: str, code: str, new_password: str) -> None:
    """Consume the code and set the new password; raises the same error for every failure."""
    email_key = normalize_email_identity(email)
    submitted = (code or "").strip()
    if not _CODE_PATTERN.fullmatch(submitted):
        raise _invalid_code()

    if await asyncio.to_thread(
        redis_client.password_reset_failures_capped, email_key, PASSWORD_RESET_MAX_DAILY_FAILURES,
    ):
        raise _invalid_code()

    submitted_hash = hash_reset_code(submitted)
    stored_hash = await asyncio.to_thread(redis_client.get_password_reset_code_hash, email_key)
    if not stored_hash:
        raise _invalid_code()
    if not hmac.compare_digest(stored_hash, submitted_hash):
        await asyncio.to_thread(
            redis_client.record_password_reset_failure,
            email_key,
            PASSWORD_RESET_MAX_ATTEMPTS,
            PASSWORD_RESET_CODE_TTL,
            PASSWORD_RESET_MAX_DAILY_FAILURES,
            PASSWORD_RESET_FAILURE_WINDOW_SECONDS,
        )
        raise _invalid_code()
    # A concurrent confirm, a newer code, or the attempt budget may have removed
    # the code since the read above; only one caller can consume it.
    if not await asyncio.to_thread(redis_client.consume_password_reset_code, email_key, submitted_hash):
        raise _invalid_code()

    # bcrypt (~0.3s) runs before the row lock is taken.
    new_hash = await asyncio.to_thread(hash_password, new_password)
    user = _find_user(session, email_key, for_update=True)
    if not _can_reset(user):
        session.rollback()
        raise _invalid_code()
    assert user is not None

    user.hashed_password = new_hash
    user.updated_at = utcnow()
    session.add(user)
    revoked_count = revoke_active_refresh_tokens_for_user(
        session, user_id=user.id, reason="password_reset",
    )
    session.commit()
    log_with_context(
        logger, 20, "Password reset completed", user_id=user.id, revoked_count=revoked_count,
    )
