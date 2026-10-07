"""Price bands, cost math and Beijing day boundaries for the usage ledger."""

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from services.usage import pricing

BEIJING = timezone(timedelta(hours=8))


def _beijing(year, month, day, hour, minute=0, second=0):
    return datetime(year, month, day, hour, minute, second, tzinfo=BEIJING)


# 2026-10-08 is a Thursday after the National Day holiday;
# 2026-10-10 is a Saturday and 2026-10-11 a Sunday.
@pytest.mark.unit
@pytest.mark.parametrize(
    ("hour", "minute", "expected"),
    [
        (8, 59, "offpeak"),
        (9, 0, "peak"),
        (11, 59, "peak"),
        (12, 0, "offpeak"),
        (13, 59, "offpeak"),
        (14, 0, "peak"),
        (17, 59, "peak"),
        (18, 0, "offpeak"),
        (0, 0, "offpeak"),
        (23, 59, "offpeak"),
    ],
)
def test_weekday_band_edges_are_half_open(hour, minute, expected):
    assert pricing.price_band(_beijing(2026, 10, 8, hour, minute)) == expected


@pytest.mark.unit
def test_last_second_before_noon_is_peak_and_noon_is_not():
    assert pricing.price_band(_beijing(2026, 10, 8, 11, 59, 59)) == "peak"
    assert pricing.price_band(_beijing(2026, 10, 8, 17, 59, 59)) == "peak"


@pytest.mark.unit
@pytest.mark.parametrize("day", [10, 11])
def test_weekend_is_always_offpeak(day):
    for hour in (9, 10, 15, 17):
        assert pricing.price_band(_beijing(2026, 10, day, hour)) == "offpeak"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("month", "day"),
    [
        (1, 1),
        (2, 16),
        (4, 6),
        (5, 4),
        (6, 19),
        (9, 25),
        (10, 7),
    ],
)
def test_2026_public_holiday_weekdays_are_always_offpeak(month, day):
    for hour in (9, 10, 15, 17):
        assert pricing.price_band(_beijing(2026, month, day, hour)) == "offpeak"


@pytest.mark.unit
def test_weekend_make_up_workday_remains_offpeak():
    # The supplier says weekends are always off-peak. Government make-up
    # workdays therefore do not turn Saturday/Sunday into peak billing days.
    assert pricing.price_band(_beijing(2026, 10, 10, 10)) == "offpeak"


@pytest.mark.unit
def test_unknown_calendar_year_does_not_claim_holiday_knowledge():
    # 2027-10-01 is a Friday, but its official holiday calendar is not encoded.
    assert pricing.is_china_public_holiday(date(2027, 10, 1)) is None
    assert pricing.price_band(_beijing(2027, 10, 1, 10)) == "peak"


@pytest.mark.unit
def test_naive_datetimes_are_treated_as_utc():
    # 01:30 UTC Thursday = 09:30 Beijing (peak); 04:30 UTC = 12:30 Beijing (lunch).
    assert pricing.price_band(datetime(2026, 10, 8, 1, 30)) == "peak"
    assert pricing.price_band(datetime(2026, 10, 8, 4, 30)) == "offpeak"
    # Friday 23:30 UTC is Saturday 07:30 Beijing.
    assert pricing.price_band(datetime(2026, 10, 9, 23, 30)) == "offpeak"
    # Sunday 22:00 UTC is Monday 06:00 Beijing, still before 09:00.
    assert pricing.price_band(datetime(2026, 10, 11, 22, 0)) == "offpeak"


@pytest.mark.unit
def test_cost_units_match_price_table():
    # 1M of each kind: peak = 0.04 + 2 + 8 CNY; off-peak = 0.02 + 1 + 4 CNY.
    million = 1_000_000
    assert pricing.units_to_cny(pricing.cost_units("peak", million, million, million)) == Decimal("10.04")
    assert pricing.units_to_cny(pricing.cost_units("offpeak", million, million, million)) == Decimal("5.02")
    # One cache-hit token off-peak is 0.00000002 CNY: exact, no float drift.
    assert pricing.cost_units("offpeak", 1, 0, 0) == 2
    assert pricing.units_to_cny(2) == Decimal("0.00000002")


@pytest.mark.unit
def test_format_cny_rounds_half_up_to_four_places():
    assert pricing.format_cny(Decimal("0.00005")) == "0.0001"
    assert pricing.format_cny(Decimal("0.00004999")) == "0.0000"
    assert pricing.format_cny(Decimal("12.3")) == "12.3000"
    assert pricing.format_cny(pricing.units_to_cny(None)) == "0.0000"


@pytest.mark.unit
def test_beijing_day_bounds_are_utc_16_00():
    assert pricing.beijing_day_start_utc(date(2026, 10, 5)) == datetime(2026, 10, 4, 16, 0)
    assert pricing.beijing_range_utc(date(2026, 10, 1), date(2026, 10, 7)) == (
        datetime(2026, 9, 30, 16, 0),
        datetime(2026, 10, 7, 16, 0),
    )
    # 15:59 UTC is still "today" in Beijing; 16:00 UTC is tomorrow.
    assert pricing.beijing_today(datetime(2026, 10, 5, 15, 59, tzinfo=UTC)) == date(2026, 10, 5)
    assert pricing.beijing_today(datetime(2026, 10, 5, 16, 0)) == date(2026, 10, 6)


@pytest.mark.unit
def test_naive_utc_and_price_table():
    assert pricing.naive_utc(_beijing(2026, 10, 5, 9)) == datetime(2026, 10, 5, 1, 0)
    table = pricing.price_table()
    assert table["peak"] == {"cache_hit": "0.04", "cache_miss": "2", "output": "8"}
    assert table["offpeak"] == {"cache_hit": "0.02", "cache_miss": "1", "output": "4"}
    assert pricing.PRICING_VERSION
    assert len(pricing.PRICING_VERSION) <= 32
