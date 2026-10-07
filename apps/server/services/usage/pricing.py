"""DeepSeek Flash price table, peak/off-peak bands and Beijing day helpers.

Single source of truth for estimated LLM cost. Prices are CNY per 1M tokens;
money is computed with Decimal. Known Chinese public holidays are encoded from
the State Council calendar because DeepSeek bills them off-peak all day.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from models.llm_usage import PRICE_BAND_OFFPEAK, PRICE_BAND_PEAK

PRICING_VERSION = "deepseek-flash-2026-10-holiday"
BEIJING_TZ = ZoneInfo("Asia/Shanghai")
BEIJING_TZ_NAME = "Asia/Shanghai"

# CNY per 1M tokens.
PRICES_CNY_PER_MILLION: dict[str, dict[str, Decimal]] = {
    PRICE_BAND_PEAK: {
        "cache_hit": Decimal("0.04"),
        "cache_miss": Decimal("2"),
        "output": Decimal("8"),
    },
    PRICE_BAND_OFFPEAK: {
        "cache_hit": Decimal("0.02"),
        "cache_miss": Decimal("1"),
        "output": Decimal("4"),
    },
}

# Peak windows in Beijing time, Monday-Friday, half-open [start, end).
PEAK_WINDOWS: tuple[tuple[time, time], ...] = (
    (time(9, 0), time(12, 0)),
    (time(14, 0), time(18, 0)),
)

# State Council General Office notice for the complete 2026 holiday calendar:
# https://www.gov.cn/zhengce/zhengceku/202511/content_7047091.htm
#
# DeepSeek explicitly keeps weekends off-peak, so make-up workdays that land on
# Saturday/Sunday are deliberately absent: they do not override that rule.
CHINA_PUBLIC_HOLIDAY_RANGES: dict[int, tuple[tuple[date, date], ...]] = {
    # Half-open local-date ranges [start, end).
    2026: (
        (date(2026, 1, 1), date(2026, 1, 4)),
        (date(2026, 2, 15), date(2026, 2, 24)),
        (date(2026, 4, 4), date(2026, 4, 7)),
        (date(2026, 5, 1), date(2026, 5, 6)),
        (date(2026, 6, 19), date(2026, 6, 22)),
        (date(2026, 9, 25), date(2026, 9, 28)),
        (date(2026, 10, 1), date(2026, 10, 8)),
    )
}

# SQL-friendly integer weights: tokens * weight is cost in units of 1e-8 CNY
# (price per 1M tokens * 100). Exact for every price with <= 2 decimals.
COST_UNIT_SCALE = Decimal(10) ** 8
COST_WEIGHTS: dict[str, dict[str, int]] = {}
for _band, _prices in PRICES_CNY_PER_MILLION.items():
    COST_WEIGHTS[_band] = {}
    for _kind, _price in _prices.items():
        _scaled = _price * 100
        if _scaled != _scaled.to_integral_value():  # pragma: no cover - config guard
            raise ValueError(f"price {_price} for {_band}/{_kind} needs more than 2 decimals")
        COST_WEIGHTS[_band][_kind] = int(_scaled)

CNY_QUANT = Decimal("0.0001")


def to_utc(value: datetime) -> datetime:
    """Treat naive datetimes as UTC (codebase convention) and return aware UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def naive_utc(value: datetime) -> datetime:
    """Aware or naive datetime -> naive UTC for storage and filtering."""
    return to_utc(value).replace(tzinfo=None)


def is_china_public_holiday(day: date) -> bool | None:
    """Return holiday status when an official calendar is encoded.

    ``None`` means the year is unknown. This makes missing calendar updates
    observable instead of pretending future holiday dates are authoritative.
    """
    ranges = CHINA_PUBLIC_HOLIDAY_RANGES.get(day.year)
    if ranges is None:
        return None
    return any(start <= day < end for start, end in ranges)


def price_band(occurred_at: datetime) -> str:
    """Return 'peak' or 'offpeak' for a call that happened at ``occurred_at``."""
    local = to_utc(occurred_at).astimezone(BEIJING_TZ)
    if local.weekday() >= 5:
        return PRICE_BAND_OFFPEAK
    if is_china_public_holiday(local.date()) is True:
        return PRICE_BAND_OFFPEAK
    clock = local.time()
    for start, end in PEAK_WINDOWS:
        if start <= clock < end:
            return PRICE_BAND_PEAK
    return PRICE_BAND_OFFPEAK


def cost_units(band: str, cache_hit: int, cache_miss: int, output: int) -> int:
    """Integer cost in 1e-8 CNY."""
    weights = COST_WEIGHTS[band]
    return (
        cache_hit * weights["cache_hit"]
        + cache_miss * weights["cache_miss"]
        + output * weights["output"]
    )


def units_to_cny(units: int | Decimal | None) -> Decimal:
    return Decimal(int(units or 0)) / COST_UNIT_SCALE


def format_cny(amount: Decimal) -> str:
    """Round to 4 decimals CNY for the API."""
    return str(amount.quantize(CNY_QUANT, rounding=ROUND_HALF_UP))


def beijing_today(now: datetime) -> date:
    return to_utc(now).astimezone(BEIJING_TZ).date()


def beijing_day_start_utc(day: date) -> datetime:
    """Naive UTC instant of 00:00 Beijing on ``day``."""
    local_midnight = datetime.combine(day, time(0, 0), tzinfo=BEIJING_TZ)
    return local_midnight.astimezone(UTC).replace(tzinfo=None)


def beijing_range_utc(first_day: date, last_day: date) -> tuple[datetime, datetime]:
    """Half-open naive-UTC bounds covering Beijing days first_day..last_day."""
    return beijing_day_start_utc(first_day), beijing_day_start_utc(last_day + timedelta(days=1))


def price_table() -> dict[str, dict[str, str]]:
    return {
        band: {kind: str(price) for kind, price in prices.items()}
        for band, prices in PRICES_CNY_PER_MILLION.items()
    }
