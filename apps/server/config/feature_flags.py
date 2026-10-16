"""Runtime feature flags shared across backend entry points."""

from __future__ import annotations

import os

from fastapi import HTTPException, status

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"", "0", "false", "no", "off"})


def _get_bool_env(name: str, *, default: bool = False) -> bool:
    """Read an explicit boolean environment variable with a safe fallback."""
    raw_value = os.getenv(name)
    if raw_value is None:
        return default

    normalized = raw_value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    return default


def is_points_pro_redemption_enabled() -> bool:
    """Allow point-funded Pro only after an explicit operational opt-in."""
    return _get_bool_env("POINTS_PRO_REDEMPTION_ENABLED", default=False)


def is_inspirations_enabled() -> bool:
    """Return whether the inspiration-library module is enabled right now."""
    return _get_bool_env("INSPIRATIONS_ENABLED", default=False)


def require_inspirations_enabled() -> None:
    """Hide inspiration routes when the module is disabled."""
    if not is_inspirations_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


def get_first_work_rollout_percent() -> int:
    """Zero disables new enrollment/initialization; malformed values stay closed."""
    try:
        percent = int(os.getenv("FIRST_WORK_ONBOARDING_ROLLOUT_PERCENT", "0"))
    except (TypeError, ValueError):
        return 0
    return percent if 0 <= percent <= 100 else 0


def is_first_work_onboarding_enabled() -> bool:
    return get_first_work_rollout_percent() > 0
