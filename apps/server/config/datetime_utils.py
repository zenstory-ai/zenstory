"""DateTime utilities for consistent timezone handling."""
from datetime import UTC, date, datetime, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from pydantic import PlainSerializer

BEIJING_TIMEZONE = ZoneInfo("Asia/Shanghai")


def utcnow() -> datetime:
    """Return current UTC datetime with timezone info.

    This replaces datetime.utcnow() which is deprecated in Python 3.12+.
    The returned datetime includes timezone info (timezone.utc).
    """
    return datetime.now(UTC)


def normalize_datetime_to_utc(value: datetime) -> datetime:
    """Normalize a datetime to UTC-aware.

    The database currently stores some timestamps as naive UTC datetimes.
    When serialized to JSON, those values can be interpreted as local time
    in clients, leading to incorrect "future" timestamps in UTC- timezones.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def advance_timestamp(previous: datetime, *, now: datetime) -> datetime:
    """Advance a freshly locked row's token even with an equal/backward clock."""
    return max(
        normalize_datetime_to_utc(now),
        normalize_datetime_to_utc(previous) + timedelta(microseconds=1),
    )


def _serialize_utc(value: datetime) -> str:
    return normalize_datetime_to_utc(value).isoformat()


# A datetime field that always serializes to JSON with an explicit UTC offset, so
# clients never read naive UTC as local time.
UTCDateTime = Annotated[
    datetime,
    PlainSerializer(_serialize_utc, return_type=str, when_used="json"),
]


def beijing_date(value: datetime) -> date:
    """Return the Beijing calendar date, treating naive database values as UTC."""
    return normalize_datetime_to_utc(value).astimezone(BEIJING_TIMEZONE).date()


def beijing_day_bounds(value: datetime) -> tuple[datetime, datetime]:
    """Return the current Beijing calendar day's bounds as UTC timestamps."""
    local_start = normalize_datetime_to_utc(value).astimezone(BEIJING_TIMEZONE).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return local_start.astimezone(UTC), (local_start + timedelta(days=1)).astimezone(UTC)
