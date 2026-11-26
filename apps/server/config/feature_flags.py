"""Runtime feature flags shared across backend entry points."""

from __future__ import annotations

import os


def get_first_work_rollout_percent() -> int:
    """Zero disables new enrollment/initialization; malformed values stay closed."""
    try:
        percent = int(os.getenv("FIRST_WORK_ONBOARDING_ROLLOUT_PERCENT", "0"))
    except (TypeError, ValueError):
        return 0
    return percent if 0 <= percent <= 100 else 0


def is_first_work_onboarding_enabled() -> bool:
    return get_first_work_rollout_percent() > 0
