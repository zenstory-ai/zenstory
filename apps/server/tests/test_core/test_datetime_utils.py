from datetime import UTC, datetime, timedelta, timezone

import pytest

from config.datetime_utils import advance_timestamp


@pytest.mark.parametrize("naive", [True, False])
@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_advance_timestamp_is_utc_and_strictly_monotonic(naive, offset):
    previous = datetime(2026, 10, 6, 0, 0, 0, 123456, tzinfo=UTC)
    now = (previous + timedelta(seconds=offset)).astimezone(timezone(timedelta(hours=8)))
    actual = advance_timestamp(previous.replace(tzinfo=None) if naive else previous, now=now)
    assert actual.tzinfo == UTC
    assert actual == previous + (timedelta(seconds=offset) if offset > 0 else timedelta(microseconds=1))
