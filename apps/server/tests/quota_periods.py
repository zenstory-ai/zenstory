"""Fixture dates for the Beijing monthly quota period."""

from datetime import datetime

from services.quota_service import quota_service


def current_month_start(now: datetime) -> datetime:
    """Naive UTC start of the Beijing month containing ``now``.

    ``now - 1 day`` falls into the previous month during the first Beijing day,
    so fixtures that mean "the current period" anchor to the real month start.
    """
    return quota_service._get_month_start(now).replace(tzinfo=None)
