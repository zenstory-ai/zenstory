"""
Verification code service for managing email verification flow.

Handles generation, storage, and validation of verification codes.
"""
import asyncio
import os
import secrets

from core.messages import get_message
from utils.email_identity import normalize_email_identity
from utils.logger import get_logger, log_with_context

from ..infra.email_client import send_verification_email
from ..infra.redis_client import (
    check_resend_cooldown,
    consume_verification_code,
    delete_resend_cooldown,
    delete_verification_code,
    get_verification_attempts,
    get_verification_code,
    increment_verification_attempts,
    reset_verification_attempts,
    set_resend_cooldown,
    store_verification_code,
)

logger = get_logger(__name__)

# Configuration
VERIFICATION_CODE_LENGTH = int(os.getenv("VERIFICATION_CODE_LENGTH", "6"))
VERIFICATION_CODE_TTL = int(os.getenv("VERIFICATION_CODE_TTL", "300"))  # 5 minutes
RESEND_COOLDOWN = int(os.getenv("RESEND_COOLDOWN", "60"))  # 60 seconds
MAX_VERIFICATION_ATTEMPTS = int(os.getenv("MAX_VERIFICATION_ATTEMPTS", "5"))


def generate_verification_code(length: int = VERIFICATION_CODE_LENGTH) -> str:
    """
    Generate a random verification code.

    Uses secrets.choice() for cryptographic randomness.

    Args:
        length: Code length (default: 6 digits)

    Returns:
        str: Generated verification code
    """
    # Generate a random number with specified digits
    code = "".join(secrets.choice("0123456789") for _ in range(length))
    return code


async def send_verification_code(email: str, language: str = "zh") -> tuple[bool, str | None]:
    """
    Generate and send a verification code to the user's email.

    Args:
        email: Recipient email address
        language: Email language ('zh' or 'en', default: 'zh')

    Returns:
        Tuple[bool, Optional[str]]: (success, error_message)
    """
    email = normalize_email_identity(email)

    try:
        # Redis calls are synchronous (2s socket timeouts); run them in a
        # worker thread so a slow Redis cannot stall the event loop.
        # Check resend cooldown
        if await asyncio.to_thread(check_resend_cooldown, email, RESEND_COOLDOWN):
            cooldown_seconds = await asyncio.to_thread(get_remaining_cooldown, email)
            message = get_message("verification_resend_cooldown", language)
            return False, message.format(cooldown_seconds=cooldown_seconds)

        # Generate verification code
        code = generate_verification_code(VERIFICATION_CODE_LENGTH)

        # Store code in Redis
        if not await asyncio.to_thread(store_verification_code, email, code, VERIFICATION_CODE_TTL):
            return False, get_message("verification_send_failed", language)

        # Set resend cooldown
        await asyncio.to_thread(set_resend_cooldown, email, RESEND_COOLDOWN)

        # Reset attempts counter
        await asyncio.to_thread(reset_verification_attempts, email)

        # Send email
        expiry_minutes = VERIFICATION_CODE_TTL // 60
        email_sent = await send_verification_email(email, code, expiry_minutes, language=language)

        if not email_sent:
            # Delete retry state if email failed so users who never received
            # a code can immediately request another one.
            await asyncio.to_thread(delete_verification_code, email)
            await asyncio.to_thread(delete_resend_cooldown, email)
            return False, get_message("verification_email_failed", language)

        return True, None

    except Exception as e:
        log_with_context(
            logger,
            40,  # ERROR
            "send_verification_code failed",
            email=email,
            error=str(e),
            error_type=type(e).__name__,
        )
        return False, get_message("verification_error", language)


async def verify_code(email: str, code: str, language: str = "zh") -> tuple[bool, str | None]:
    """
    Verify the submitted verification code.

    Args:
        email: User email address
        code: Submitted verification code
        language: Language for error messages ('zh' or 'en', default: 'zh')

    Returns:
        Tuple[bool, Optional[str]]: (success, error_message)
    """
    email = normalize_email_identity(email)

    try:
        # Check attempts limit
        attempts = await asyncio.to_thread(get_verification_attempts, email)
        if attempts >= MAX_VERIFICATION_ATTEMPTS:
            return False, get_message("verification_too_many_attempts", language)

        # Get stored code
        stored_code = await asyncio.to_thread(get_verification_code, email)

        if not stored_code:
            return False, get_message("verification_not_exist", language)

        # Verify code
        if code != stored_code:
            # Increment attempts
            await asyncio.to_thread(increment_verification_attempts, email, MAX_VERIFICATION_ATTEMPTS)
            remaining_attempts = MAX_VERIFICATION_ATTEMPTS - (attempts + 1)
            message = get_message("verification_incorrect", language)
            return False, message.format(count=remaining_attempts)

        # A concurrent verification/resend may have changed the code since GET.
        if not await asyncio.to_thread(consume_verification_code, email, code):
            return False, get_message("verification_not_exist", language)
        await asyncio.to_thread(reset_verification_attempts, email)

        return True, None

    except Exception as e:
        log_with_context(
            logger,
            40,  # ERROR
            "verify_code failed",
            email=email,
            error=str(e),
            error_type=type(e).__name__,
        )
        return False, get_message("verification_verify_error", language)


def get_remaining_cooldown(email: str) -> int:
    """
    Get remaining cooldown time for resending verification code.

    Args:
        email: User email address

    Returns:
        int: Remaining cooldown seconds (0 if no cooldown)
    """
    email = normalize_email_identity(email)

    try:
        from ..infra.redis_client import get_redis_client

        client = get_redis_client()
        key = f"resend_cooldown:{email}"
        ttl = client.ttl(key)  # type: ignore[return-value]
        ttl_int = int(ttl) if ttl is not None else 0  # type: ignore[arg-type]
        return max(0, ttl_int)
    except Exception as e:
        log_with_context(
            logger,
            40,  # ERROR
            "get_remaining_cooldown failed",
            email=email,
            error=str(e),
            error_type=type(e).__name__,
        )
        return 0


def get_code_ttl(email: str) -> int:
    """
    Get remaining time until verification code expires.

    Args:
        email: User email address

    Returns:
        int: Remaining TTL in seconds (0 if no code exists)
    """
    email = normalize_email_identity(email)

    try:
        from ..infra.redis_client import get_redis_client

        client = get_redis_client()
        key = f"verification:{email}"
        ttl = client.ttl(key)  # type: ignore[return-value]
        ttl_int = int(ttl) if ttl is not None else 0  # type: ignore[arg-type]
        return max(0, ttl_int)
    except Exception as e:
        log_with_context(
            logger,
            40,  # ERROR
            "get_code_ttl failed",
            email=email,
            error=str(e),
            error_type=type(e).__name__,
        )
        return 0
