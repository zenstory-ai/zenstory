"""
Data sanitization utilities for logging.

Automatically masks sensitive information in log output.
"""

import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlencode

# Sensitive field names (case-insensitive)
SENSITIVE_FIELDS = {
    "password",
    "passwd",
    "pwd",
    "hashed_password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "authorization",
    "auth_token",
    "bearer",
    "session_key",
    "session_token",
    "code",
    "old_password",
    "new_password",
    "confirm_password",
    "verification_code",
    "reset_token",
    "csrf_token",
    "private_key",
    "public_key",
}

# Patterns for sensitive values in strings
SENSITIVE_PATTERNS = [
    # API Key patterns (sk-xxx, pk-xxx, etc.)
    re.compile(r'\b(sk-[a-zA-Z0-9]{20,})\b'),
    re.compile(r'\b(pk-[a-zA-Z0-9]{20,})\b'),
    # JWT tokens
    re.compile(r'\b(eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+)\b'),
    # Bearer tokens
    re.compile(r'\b(bearer [a-zA-Z0-9_-]+)\b', re.IGNORECASE),
    # API keys with alphanumeric
    re.compile(r'\b([a-zA-Z0-9]{32,})\b'),
]


def mask_value(value: Any) -> str:
    """
    Mask a sensitive value.

    Strategy: Show first 2 chars + *** + last 2 chars.
    For short values (<6 chars), mask all except first/last char.

    Args:
        value: Value to mask (converted to string)

    Returns:
        Masked string
    """
    if value is None:
        return "null"

    str_value = str(value)

    if len(str_value) <= 6:
        return f"{str_value[0]}***{str_value[-1]}" if len(str_value) > 1 else "***"
    return f"{str_value[:2]}***{str_value[-2:]}"


def mask_sensitive_in_string(text: str) -> str:
    """
    Mask sensitive patterns in a string.

    Args:
        text: String to sanitize

    Returns:
        Sanitized string with sensitive patterns masked
    """
    result = text

    for pattern in SENSITIVE_PATTERNS:
        result = pattern.sub(lambda m: mask_value(m.group(1)), result)

    return result


def sanitize_dict(data: dict[str, Any]) -> dict[str, Any]:
    """
    Recursively sanitize dictionary values.

    Masks values for keys matching sensitive field names.
    Handles nested dictionaries and lists.

    Args:
        data: Dictionary to sanitize

    Returns:
        Sanitized dictionary
    """
    if not isinstance(data, dict):
        return data

    sanitized = {}

    for key, value in data.items():
        # Check if key is sensitive (case-insensitive)
        if key.lower() in SENSITIVE_FIELDS:
            # Mask the entire value
            if isinstance(value, str):
                sanitized[key] = mask_sensitive_in_string(mask_value(value))
            else:
                sanitized[key] = mask_value(value)
        elif isinstance(value, dict):
            # Recursively sanitize nested dictionaries
            sanitized[key] = sanitize_dict(value)
        elif isinstance(value, list):
            # Sanitize list items
            sanitized[key] = sanitize_list(value)
        elif isinstance(value, str):
            # Mask sensitive patterns in strings
            sanitized[key] = mask_sensitive_in_string(value)
        else:
            sanitized[key] = value

    return sanitized


def sanitize_list(data: list[Any]) -> list[Any]:
    """
    Recursively sanitize list items.

    Args:
        data: List to sanitize

    Returns:
        Sanitized list
    """
    if not isinstance(data, list):
        return data

    return [sanitize_item(item) for item in data]


def sanitize_item(item: Any) -> Any:
    """
    Sanitize a single item (dict, list, or primitive).

    Args:
        item: Item to sanitize

    Returns:
        Sanitized item
    """
    if isinstance(item, dict):
        return sanitize_dict(item)
    elif isinstance(item, list):
        return sanitize_list(item)
    elif isinstance(item, str):
        return mask_sensitive_in_string(item)
    else:
        return item


def sanitize_for_logging(data: Any) -> Any:
    """
    Main entry point for sanitizing data before logging.

    Handles dicts, lists, strings, and primitives.

    Args:
        data: Data to sanitize

    Returns:
        Sanitized data
    """
    return sanitize_item(data)


# Email addresses are masked wherever they appear in log output: keep the
# first character and the domain so support can still tell accounts apart.
EMAIL_PATTERN = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})")

# Log fields that carry user-written text (prompts, manuscript previews).
# Only their length is kept.
USER_TEXT_LOG_FIELDS = {
    "message_preview",
    "user_message_preview",
    "prompt",
    "user_prompt",
    "prompt_preview",
    "content_preview",
}


def mask_email(value: str) -> str:
    """Mask every email address in ``value`` (``alice@example.com`` -> ``a***@example.com``)."""
    return EMAIL_PATTERN.sub(r"\1***@\2", value)


def redact_log_value(value: Any) -> Any:
    """Mask emails inside a log value, recursing into dicts and lists."""
    if isinstance(value, str):
        return mask_email(value)
    if isinstance(value, dict):
        return {key: redact_log_field(str(key), item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_log_value(item) for item in value]
    return value


def redact_log_field(key: str, value: Any) -> Any:
    """Redact one structured log field: drop user text, mask emails."""
    if key.lower() in USER_TEXT_LOG_FIELDS and isinstance(value, str):
        return f"[redacted {len(value)} chars]"
    return redact_log_value(value)


# Query-string keys whose values must never reach logs (credentials, OAuth
# handshakes, payment signatures, personal identifiers).
SENSITIVE_QUERY_KEYS = {
    "token",
    "access_token",
    "refresh_token",
    "id_token",
    "code",
    "state",
    "password",
    "key",
    "api_key",
    "secret",
    "sign",
    "signature",
}


def sanitize_query_params(items: Iterable[tuple[str, str]]) -> str:
    """Render query parameters for logs with credentials redacted and emails masked."""
    sanitized: list[tuple[str, str]] = []
    for key, value in items:
        if key.lower() in SENSITIVE_QUERY_KEYS:
            sanitized.append((key, "[redacted]"))
        else:
            sanitized.append((key, mask_email(value)))
    return urlencode(sanitized, safe="[]*@")
