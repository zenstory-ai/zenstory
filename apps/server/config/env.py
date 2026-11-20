"""Environment variable parsing helpers with safe fallbacks."""

from __future__ import annotations

import os


def get_positive_float_env(name: str, default: float) -> float:
    """Read a positive float environment variable with safe fallback."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def get_int_env(
    name: str,
    default: int,
    *,
    minimum: int | None = 1,
    clamp: bool = False,
) -> int:
    """Read an integer environment variable with safe fallback.

    When ``minimum`` is set and the parsed value is below ``minimum``:
    - ``clamp=False`` (default): fall back to ``default``.
    - ``clamp=True``: clamp to ``minimum``.
    """
    raw = os.getenv(name)
    if raw is None:
        value = default
    else:
        try:
            value = int(raw)
        except ValueError:
            value = default
    if minimum is not None and value < minimum:
        return minimum if clamp else default
    return value
