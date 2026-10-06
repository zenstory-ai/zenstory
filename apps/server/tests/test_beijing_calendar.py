"""All daily entitlements share the same explicit Beijing calendar clock."""

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from config.datetime_utils import beijing_date, beijing_day_bounds


@pytest.mark.unit
@pytest.mark.parametrize(
    ("value", "expected_date", "expected_start"),
    [
        (datetime(2026, 10, 5, 15, 59, 59), date(2026, 10, 5), datetime(2026, 10, 4, 16, tzinfo=UTC)),
        (datetime(2026, 10, 5, 16, tzinfo=UTC), date(2026, 10, 6), datetime(2026, 10, 5, 16, tzinfo=UTC)),
        (datetime(2026, 10, 6, 0, tzinfo=UTC), date(2026, 10, 6), datetime(2026, 10, 5, 16, tzinfo=UTC)),
        (datetime(2026, 10, 5, 9, tzinfo=timezone(timedelta(hours=-7))), date(2026, 10, 6), datetime(2026, 10, 5, 16, tzinfo=UTC)),
        (datetime(2026, 12, 31, 16, tzinfo=UTC), date(2027, 1, 1), datetime(2026, 12, 31, 16, tzinfo=UTC)),
    ],
)
def test_beijing_date_and_day_bounds(value, expected_date, expected_start):
    assert beijing_date(value) == expected_date
    assert beijing_day_bounds(value) == (expected_start, expected_start + timedelta(days=1))
