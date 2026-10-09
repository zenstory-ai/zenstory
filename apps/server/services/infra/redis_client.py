"""
Redis client for verification code storage and caching.
"""
import os
from typing import Any

import redis

from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)


def _get_positive_float_env(name: str, default: float) -> float:
    """Parse positive float env var with safe fallback."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _get_positive_int_env(name: str, default: int) -> int:
    """Parse positive integer env var with safe fallback."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


# Redis configuration
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
REDIS_SOCKET_CONNECT_TIMEOUT = _get_positive_float_env(
    "REDIS_SOCKET_CONNECT_TIMEOUT_S",
    2.0,
)
REDIS_SOCKET_TIMEOUT = _get_positive_float_env(
    "REDIS_SOCKET_TIMEOUT_S",
    2.0,
)
REDIS_HEALTH_CHECK_INTERVAL = _get_positive_int_env(
    "REDIS_HEALTH_CHECK_INTERVAL_S",
    30,
)

_REDIS_POOL_KWARGS: dict[str, Any] = {
    "decode_responses": True,
    "socket_connect_timeout": REDIS_SOCKET_CONNECT_TIMEOUT,
    "socket_timeout": REDIS_SOCKET_TIMEOUT,
    "health_check_interval": REDIS_HEALTH_CHECK_INTERVAL,
}

# Create Redis connection pool
redis_pool = redis.ConnectionPool.from_url(
    REDIS_URL,
    **_REDIS_POOL_KWARGS,
)


def get_redis_client() -> redis.Redis:
    """
    Get a Redis client from the connection pool.

    Returns:
        redis.Redis: Redis client instance
    """
    return redis.Redis(connection_pool=redis_pool)


def store_verification_code(email: str, code: str, ttl: int = 300) -> bool:
    """
    Store a verification code for an email address.

    Args:
        email: Email address
        code: 6-digit verification code
        ttl: Time to live in seconds (default: 300 seconds / 5 minutes)

    Returns:
        bool: True if successful, False otherwise
    """
    try:
        client = get_redis_client()
        key = f"verification:{email}"
        # Store the code with TTL
        client.setex(key, ttl, code)
        return True
    except Exception as e:
        log_with_context(
            logger,
            40,  # ERROR
            "Error storing verification code",
            email=email,
            error=str(e),
            error_type=type(e).__name__,
        )
        return False


def get_verification_code(email: str) -> str | None:
    """
    Retrieve a verification code for an email address.

    Args:
        email: Email address

    Returns:
        Optional[str]: Verification code if exists, None otherwise
    """
    try:
        client = get_redis_client()
        key = f"verification:{email}"
        code = client.get(key)  # type: ignore[return-value]
        return code  # type: ignore[return-value]
    except Exception as e:
        log_with_context(
            logger,
            40,  # ERROR
            "Error retrieving verification code",
            email=email,
            error=str(e),
            error_type=type(e).__name__,
        )
        return None


def delete_verification_code(email: str) -> bool:
    """
    Delete a verification code for an email address.

    Args:
        email: Email address

    Returns:
        bool: True if successful, False otherwise
    """
    try:
        client = get_redis_client()
        key = f"verification:{email}"
        client.delete(key)
        return True
    except Exception as e:
        log_with_context(
            logger,
            40,  # ERROR
            "Error deleting verification code",
            email=email,
            error=str(e),
            error_type=type(e).__name__,
        )
        return False


def consume_verification_code(email: str, code: str) -> bool:
    """Atomically consume only the matching code; fail closed on Redis errors."""
    try:
        client = get_redis_client()
        return bool(client.eval(
            "if redis.call('GET', KEYS[1]) == ARGV[1] then "
            "return redis.call('DEL', KEYS[1]) end return 0",
            1, f"verification:{email}", code,
        ))
    except Exception as error:
        log_with_context(
            logger, 40, "Error consuming verification code",
            email=email, error_type=type(error).__name__,
        )
        return False


def check_resend_cooldown(email: str, _cooldown_seconds: int = 60) -> bool:
    """
    Check if resend verification code is still in cooldown.

    Args:
        email: Email address
        _cooldown_seconds: Cooldown period in seconds (unused, default from set_resend_cooldown)

    Returns:
        bool: True if in cooldown, False otherwise
    """
    try:
        client = get_redis_client()
        key = f"resend_cooldown:{email}"
        ttl = client.ttl(key)  # type: ignore[return-value]
        return ttl > 0  # type: ignore[operator]
    except Exception as e:
        log_with_context(
            logger,
            40,  # ERROR
            "Error checking resend cooldown",
            email=email,
            error=str(e),
            error_type=type(e).__name__,
        )
        return False


def set_resend_cooldown(email: str, cooldown_seconds: int = 60) -> bool:
    """
    Set resend cooldown for an email address.

    Args:
        email: Email address
        cooldown_seconds: Cooldown period in seconds (default: 60 seconds)

    Returns:
        bool: True if successful, False otherwise
    """
    try:
        client = get_redis_client()
        key = f"resend_cooldown:{email}"
        client.setex(key, cooldown_seconds, "1")
        return True
    except Exception as e:
        log_with_context(
            logger,
            40,  # ERROR
            "Error setting resend cooldown",
            email=email,
            error=str(e),
            error_type=type(e).__name__,
        )
        return False


def delete_resend_cooldown(email: str) -> bool:
    """
    Delete resend cooldown for an email address.

    Args:
        email: Email address

    Returns:
        bool: True if successful, False otherwise
    """
    try:
        client = get_redis_client()
        key = f"resend_cooldown:{email}"
        client.delete(key)
        return True
    except Exception as e:
        log_with_context(
            logger,
            40,  # ERROR
            "Error deleting resend cooldown",
            email=email,
            error=str(e),
            error_type=type(e).__name__,
        )
        return False


def get_verification_attempts(email: str) -> int:
    """
    Get the number of failed verification attempts for an email.

    Args:
        email: Email address

    Returns:
        int: Number of attempts
    """
    try:
        client = get_redis_client()
        key = f"attempts:{email}"
        attempts = client.get(key)  # type: ignore[return-value]
        return int(attempts) if attempts else 0  # type: ignore[arg-type]
    except Exception as e:
        log_with_context(
            logger,
            40,  # ERROR
            "Error getting verification attempts",
            email=email,
            error=str(e),
            error_type=type(e).__name__,
        )
        return 0


def increment_verification_attempts(email: str, max_attempts: int = 5) -> bool:
    """
    Increment verification attempts counter for an email.

    Args:
        email: Email address
        max_attempts: Maximum allowed attempts before reset

    Returns:
        bool: True if still under limit, False if limit reached
    """
    try:
        client = get_redis_client()
        key = f"attempts:{email}"
        current_attempts = client.incr(key)  # type: ignore[return-value]

        # Set expiry on first attempt
        if current_attempts == 1:
            client.expire(key, 300)  # 5 minutes expiry

        return current_attempts <= max_attempts  # type: ignore[operator]
    except Exception as e:
        log_with_context(
            logger,
            40,  # ERROR
            "Error incrementing verification attempts",
            email=email,
            error=str(e),
            error_type=type(e).__name__,
        )
        return False


def reset_verification_attempts(email: str) -> bool:
    """
    Reset verification attempts counter for an email.

    Args:
        email: Email address

    Returns:
        bool: True if successful, False otherwise
    """
    try:
        client = get_redis_client()
        key = f"attempts:{email}"
        client.delete(key)
        return True
    except Exception as e:
        log_with_context(
            logger,
            40,  # ERROR
            "Error resetting verification attempts",
            email=email,
            error=str(e),
            error_type=type(e).__name__,
        )
        return False


# ==================== Password reset codes ====================
# Kept in their own ``pwreset:`` namespace so a registration code
# (``verification:{email}``) can never be redeemed as a reset code, and the two
# flows never share attempt counters or cooldowns. Only sha256(code) is stored.

# KEYS[1]=code, KEYS[2]=attempts; ARGV[1]=submitted hash. Deletes the code and
# its attempt counter only while it still matches, so two concurrent confirms
# with the right code succeed exactly once.
_CONSUME_PASSWORD_RESET_CODE = (
    "if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end "
    "redis.call('DEL', KEYS[1], KEYS[2]) return 1"
)

# KEYS[1]=code, KEYS[2]=attempts; ARGV[1]=max attempts, ARGV[2]=attempts TTL.
# The failure that reaches the budget deletes the code in the same step.
_RECORD_PASSWORD_RESET_FAILURE = (
    "local attempts = redis.call('INCR', KEYS[2]) "
    "if attempts == 1 then redis.call('EXPIRE', KEYS[2], ARGV[2]) end "
    "if attempts >= tonumber(ARGV[1]) then redis.call('DEL', KEYS[1]) end "
    "return attempts"
)


def password_reset_key(kind: str, email: str) -> str:
    """``pwreset:{code|attempts|cooldown}:{email}``."""
    return f"pwreset:{kind}:{email}"


def claim_password_reset_cooldown(email: str, cooldown_seconds: int = 60) -> bool:
    """Start the send cooldown; False when one is already running or Redis fails."""
    try:
        client = get_redis_client()
        return bool(client.set(
            password_reset_key("cooldown", email), "1", nx=True, ex=cooldown_seconds,
        ))
    except Exception as error:
        log_with_context(
            logger, 40, "Error claiming password reset cooldown",
            error_type=type(error).__name__,
        )
        return False


def store_password_reset_code(email: str, code_hash: str, ttl: int) -> bool:
    """Store a fresh reset code hash and restart its attempt budget."""
    try:
        client = get_redis_client()
        pipeline = client.pipeline(transaction=True)
        pipeline.setex(password_reset_key("code", email), ttl, code_hash)
        pipeline.delete(password_reset_key("attempts", email))
        pipeline.execute()
        return True
    except Exception as error:
        log_with_context(
            logger, 40, "Error storing password reset code",
            error_type=type(error).__name__,
        )
        return False


def get_password_reset_code_hash(email: str) -> str | None:
    """Return the stored reset code hash, or None when absent or unreadable."""
    try:
        client = get_redis_client()
        return client.get(password_reset_key("code", email))  # type: ignore[return-value]
    except Exception as error:
        log_with_context(
            logger, 40, "Error reading password reset code",
            error_type=type(error).__name__,
        )
        return None


def consume_password_reset_code(email: str, code_hash: str) -> bool:
    """Atomically compare and delete the code and its attempts; fail closed."""
    try:
        client = get_redis_client()
        return bool(client.eval(
            _CONSUME_PASSWORD_RESET_CODE, 2,
            password_reset_key("code", email),
            password_reset_key("attempts", email),
            code_hash,
        ))
    except Exception as error:
        log_with_context(
            logger, 40, "Error consuming password reset code",
            error_type=type(error).__name__,
        )
        return False


def record_password_reset_failure(email: str, max_attempts: int, ttl: int) -> int:
    """Count a wrong code; the failure reaching ``max_attempts`` deletes the code."""
    try:
        client = get_redis_client()
        return int(client.eval(  # type: ignore[arg-type]
            _RECORD_PASSWORD_RESET_FAILURE, 2,
            password_reset_key("code", email),
            password_reset_key("attempts", email),
            max_attempts, ttl,
        ))
    except Exception as error:
        log_with_context(
            logger, 40, "Error recording password reset failure",
            error_type=type(error).__name__,
        )
        return max_attempts


def clear_password_reset_state(email: str) -> bool:
    """Drop code, attempts and cooldown, e.g. when the email could not be sent."""
    try:
        client = get_redis_client()
        client.delete(*(password_reset_key(kind, email) for kind in ("code", "attempts", "cooldown")))
        return True
    except Exception as error:
        log_with_context(
            logger, 40, "Error clearing password reset state",
            error_type=type(error).__name__,
        )
        return False
