"""
Code Generator - Generates redemption codes with HMAC checksums.
"""
import hashlib
import hmac
import os
import secrets


def get_hmac_secret() -> str:
    """Get HMAC secret from environment."""
    secret = os.getenv("REDEMPTION_CODE_HMAC_SECRET", "")
    if len(secret) < 32:
        raise ValueError("REDEMPTION_CODE_HMAC_SECRET must be at least 32 characters")
    return secret


def generate_code(
    tier: str,
    duration_days: int,
) -> str:
    """
    Generate a redemption code.

    Format: ERG-{TIER}{DURATION}-{CHECKSUM4}-{RANDOM8}

    Args:
        tier: Plan tier (e.g., "pro", "free")
        duration_days: Duration in days

    Returns:
        Generated code string
    """
    # Build tier+duration part
    tier_code = tier.upper()[:2]  # PR for pro, FR for free
    if duration_days >= 365:
        duration_code = "YR"
    elif duration_days >= 30:
        duration_code = f"{duration_days // 30}M"
    else:
        duration_code = f"{duration_days}D"

    tier_duration = f"{tier_code}{duration_code}"

    # Generate random part (8 chars)
    random_part = secrets.token_hex(4).upper()[:8]

    # Generate checksum
    secret = get_hmac_secret()
    message = f"{tier_duration}-{random_part}"
    signature = hmac.new(
        secret.encode(),
        message.encode(),
        hashlib.sha256
    ).digest()
    checksum = signature[:4].hex().upper()[:4]

    return f"ERG-{tier_duration}-{checksum}-{random_part}"


def generate_batch_codes(
    tier: str,
    duration_days: int,
    count: int,
) -> list[str]:
    """Generate multiple unique codes."""
    codes = set()
    while len(codes) < count:
        codes.add(generate_code(tier, duration_days))
    return list(codes)
