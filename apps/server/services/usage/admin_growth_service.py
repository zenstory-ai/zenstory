"""Read-only, customer-level growth metrics for the admin dashboard.

All database timestamps are naive UTC. Reporting windows are half-open and use
Beijing calendar boundaries; the prior window is shifted by exactly ``days`` so
it has the same elapsed portion of its final day as the current window.

Query budget: builds current period metrics, prior period metrics, and all
daily rows in 8 bounded SQL statements (down from 18), reusing additive daily
aggregates for the current period and combining current/prior scans.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import and_, case, literal, or_
from sqlmodel import Session, func, select

from config.datetime_utils import (
    BEIJING_TIMEZONE,
    beijing_day_bounds,
    normalize_datetime_to_utc,
    utcnow,
)
from models.entities import User
from models.llm_usage import LLM_USAGE_SOURCE_SUGGEST, LLMUsageEvent, has_usage_tokens
from models.payment import PaymentOrder
from models.subscription import SubscriptionHistory
from services.infra.admin_analytics_cache import admin_read_statement_timeout
from services.usage.pricing import beijing_day_bucket_case

GROWTH_DAYS = (7, 14, 30)
GRANT_ACTIONS = ("created", "upgraded", "renewed")
PAID_SOURCES = frozenset({"zpay", "creem"})
_PREV_BUCKET = "__previous__"


def _revenue_by_currency_map(cny_cents: int, usd_cents: int) -> dict[str, int]:
    totals: dict[str, int] = {}
    if cny_cents:
        totals["CNY"] = cny_cents
    if usd_cents:
        totals["USD"] = usd_cents
    return totals


@dataclass(frozen=True)
class GrowthPeriod:
    start: datetime
    end: datetime

    @property
    def naive_bounds(self) -> tuple[datetime, datetime]:
        return self.start.replace(tzinfo=None), self.end.replace(tzinfo=None)


def _periods(days: int, now: datetime) -> tuple[GrowthPeriod, GrowthPeriod]:
    current_start = beijing_day_bounds(now - timedelta(days=days - 1))[0]
    current = GrowthPeriod(current_start, normalize_datetime_to_utc(now))
    # Shift both bounds by the requested number of Beijing calendar days. This
    # compares the same weekday/time-of-day slice and keeps durations identical.
    previous = GrowthPeriod(
        current.start - timedelta(days=days),
        current.end - timedelta(days=days),
    )
    return current, previous


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _two_period_condition(
    column: Any,
    curr_start: datetime,
    curr_end: datetime,
    prev_start: datetime,
    prev_end: datetime,
) -> Any:
    return or_(
        and_(column >= prev_start, column < prev_end),
        and_(column >= curr_start, column < curr_end),
    )


def _day_or_prev_bucket(
    column: Any,
    current: GrowthPeriod,
    previous: GrowthPeriod,
    *,
    day_list: list[date],
) -> Any:
    prev_start, prev_end = previous.naive_bounds
    day_expr = beijing_day_bucket_case(column, day_list, end_utc=current.end)
    return case(
        (and_(column >= prev_start, column < prev_end), literal(_PREV_BUCKET)),
        else_=day_expr,
    )


def _empty_daily(day: date) -> dict[str, Any]:
    return {
        "date": day,
        "new_users": 0,
        "ai_active_users": 0,
        "cohort_activated_users": 0,
        "cohort_activation_rate": None,
        "paid_orders": 0,
        "revenue_cents": 0,
        "revenue_usd_cents": 0,
        "revenue_by_currency": {},
        "paid_users": 0,
        "cohort_paid_users": 0,
        "signup_to_paid_rate": None,
        "grant_upgrade_events": 0,
        "grant_upgrade_users": 0,
        "grant_channels": [],
    }


def _sorted_grant_channels(channel_map: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    channels = [
        {"channel": channel, "events": values["events"], "users": len(values["user_ids"])}
        for channel, values in channel_map.items()
    ]
    channels.sort(key=lambda item: (-item["events"], item["channel"]))
    return channels


DEFINITIONS = {
    "new_users": "Non-superuser accounts created in the half-open period.",
    "ai_active_users": "Distinct non-superusers with a non-backfilled LLM usage event in the period.",
    "cohort_activation_rate": "New-user cohort with a non-backfilled LLM usage event after signup, divided by new users; null when there are no signups.",
    "paid": "Payment orders with status=paid and paid_at in the period; pending, refunded, missing paid_at, and plan grants are excluded.",
    "signup_to_paid_rate": "Distinct new-user cohort members with a confirmed payment after signup, divided by new users; null when there are no signups.",
    "grants": "Non-free subscription history events whose source is not a payment provider (zpay/creem); shown separately from revenue.",
}


def get_growth_dashboard(
    session: Session,
    *,
    days: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the growth dashboard payload for a supported window in 8 SQL queries."""
    if days not in GROWTH_DAYS:
        raise ValueError(f"unsupported growth window: {days}")
    report_now = normalize_datetime_to_utc(now or utcnow())
    current, previous = _periods(days, report_now)

    curr_start, curr_end = current.naive_bounds
    prev_start, prev_end = previous.naive_bounds

    first_day = normalize_datetime_to_utc(current.start).astimezone(BEIJING_TIMEZONE).date()
    day_list = [first_day + timedelta(days=offset) for offset in range(days)]
    entries = [
        _empty_daily(day)
        for offset, day in enumerate(day_list)
        if current.start + timedelta(days=offset) < current.end
    ]
    by_day = {entry["date"].isoformat(): entry for entry in entries}

    customer = User.is_superuser.is_(False)
    # 打开空项目时界面自动请求的建议（/agent/suggest）不是作者主动用 AI，不算活跃 / 激活。
    author_initiated = LLMUsageEvent.source != LLM_USAGE_SOURCE_SUGGEST
    signup_bucket = _day_or_prev_bucket(User.created_at, current, previous, day_list=day_list)
    cohort_obs_end = case(
        (and_(User.created_at >= curr_start, User.created_at < curr_end), literal(curr_end)),
        else_=literal(prev_end),
    )

    prev_new_users = 0
    prev_ai_active_users = 0
    curr_ai_active_users = 0
    prev_cohort_activated = 0
    prev_paid_orders = 0
    prev_revenue_cents = 0
    prev_revenue_usd_cents = 0
    prev_paid_users = 0
    curr_paid_users = 0
    prev_cohort_paid = 0

    with admin_read_statement_timeout(session):
        # 1. New users (current daily + previous total)
        new_user_rows = session.exec(
            select(signup_bucket.label("bucket"), func.count(User.id).label("users"))
            .where(
                customer,
                _two_period_condition(User.created_at, curr_start, curr_end, prev_start, prev_end),
            )
            .group_by(signup_bucket)
        ).all()
        for row in new_user_rows:
            if row.bucket == _PREV_BUCKET:
                prev_new_users = int(row.users or 0)
            elif row.bucket in by_day:
                by_day[row.bucket]["new_users"] = int(row.users or 0)

        # 2. Daily AI active users (current)
        usage_day = beijing_day_bucket_case(LLMUsageEvent.occurred_at, day_list, end_utc=current.end)
        active_daily_rows = session.exec(
            select(
                usage_day.label("day"),
                func.count(func.distinct(LLMUsageEvent.user_id)).label("users"),
            )
            .join(User, User.id == LLMUsageEvent.user_id)
            .where(
                customer,
                author_initiated,
                LLMUsageEvent.is_backfilled.is_(False),
                has_usage_tokens(LLMUsageEvent),
                LLMUsageEvent.occurred_at >= curr_start,
                LLMUsageEvent.occurred_at < curr_end,
            )
            .group_by(usage_day)
        ).all()
        for row in active_daily_rows:
            if row.day in by_day:
                by_day[row.day]["ai_active_users"] = int(row.users or 0)

        # 3. Period distinct AI active users (current + previous combined)
        usage_period = case(
            (
                and_(LLMUsageEvent.occurred_at >= curr_start, LLMUsageEvent.occurred_at < curr_end),
                literal("current"),
            ),
            else_=literal("previous"),
        )
        active_period_rows = session.exec(
            select(
                usage_period.label("period"),
                func.count(func.distinct(LLMUsageEvent.user_id)).label("users"),
            )
            .join(User, User.id == LLMUsageEvent.user_id)
            .where(
                customer,
                author_initiated,
                LLMUsageEvent.is_backfilled.is_(False),
                has_usage_tokens(LLMUsageEvent),
                _two_period_condition(
                    LLMUsageEvent.occurred_at, curr_start, curr_end, prev_start, prev_end
                ),
            )
            .group_by(usage_period)
        ).all()
        for row in active_period_rows:
            if row.period == "current":
                curr_ai_active_users = int(row.users or 0)
            else:
                prev_ai_active_users = int(row.users or 0)

        # 4. Cohort activation (current daily + previous total)
        cohort_activation_rows = session.exec(
            select(
                signup_bucket.label("bucket"),
                func.count(func.distinct(User.id)).label("users"),
            )
            .join(LLMUsageEvent, LLMUsageEvent.user_id == User.id)
            .where(
                customer,
                _two_period_condition(User.created_at, curr_start, curr_end, prev_start, prev_end),
                author_initiated,
                LLMUsageEvent.is_backfilled.is_(False),
                has_usage_tokens(LLMUsageEvent),
                LLMUsageEvent.occurred_at >= User.created_at,
                LLMUsageEvent.occurred_at < cohort_obs_end,
            )
            .group_by(signup_bucket)
        ).all()
        for row in cohort_activation_rows:
            if row.bucket == _PREV_BUCKET:
                prev_cohort_activated = int(row.users or 0)
            elif row.bucket in by_day:
                by_day[row.bucket]["cohort_activated_users"] = int(row.users or 0)

        # 5. Paid orders & revenue (current daily + previous total including distinct paid_users)
        paid_bucket = _day_or_prev_bucket(PaymentOrder.paid_at, current, previous, day_list=day_list)
        cny_amount_expr = case((PaymentOrder.currency == "CNY", PaymentOrder.amount_cents), else_=0)
        usd_amount_expr = case((PaymentOrder.currency == "USD", PaymentOrder.amount_cents), else_=0)
        payment_rows = session.exec(
            select(
                paid_bucket.label("bucket"),
                func.count(PaymentOrder.id).label("orders"),
                func.coalesce(func.sum(cny_amount_expr), 0).label("revenue_cny_cents"),
                func.coalesce(func.sum(usd_amount_expr), 0).label("revenue_usd_cents"),
                func.count(func.distinct(PaymentOrder.user_id)).label("users"),
            )
            .join(User, User.id == PaymentOrder.user_id)
            .where(
                customer,
                PaymentOrder.status == "paid",
                PaymentOrder.paid_at.is_not(None),
                _two_period_condition(PaymentOrder.paid_at, curr_start, curr_end, prev_start, prev_end),
            )
            .group_by(paid_bucket)
        ).all()
        for row in payment_rows:
            cny_cents = int(row.revenue_cny_cents or 0)
            usd_cents = int(row.revenue_usd_cents or 0)
            if row.bucket == _PREV_BUCKET:
                prev_paid_orders = int(row.orders or 0)
                prev_revenue_cents = cny_cents
                prev_revenue_usd_cents = usd_cents
                prev_paid_users = int(row.users or 0)
            elif row.bucket in by_day:
                entry = by_day[row.bucket]
                entry["paid_orders"] = int(row.orders or 0)
                entry["revenue_cents"] = cny_cents
                entry["revenue_usd_cents"] = usd_cents
                entry["revenue_by_currency"] = _revenue_by_currency_map(cny_cents, usd_cents)
                entry["paid_users"] = int(row.users or 0)

        # 6. Current period distinct paid users (non-additive across days)
        curr_paid_users = int(
            session.exec(
                select(func.count(func.distinct(PaymentOrder.user_id)))
                .join(User, User.id == PaymentOrder.user_id)
                .where(
                    customer,
                    PaymentOrder.status == "paid",
                    PaymentOrder.paid_at.is_not(None),
                    PaymentOrder.paid_at >= curr_start,
                    PaymentOrder.paid_at < curr_end,
                )
            ).one()
            or 0
        )

        # 7. Cohort paid users (current daily + previous total)
        cohort_payment_rows = session.exec(
            select(
                signup_bucket.label("bucket"),
                func.count(func.distinct(User.id)).label("users"),
            )
            .join(PaymentOrder, PaymentOrder.user_id == User.id)
            .where(
                customer,
                _two_period_condition(User.created_at, curr_start, curr_end, prev_start, prev_end),
                PaymentOrder.status == "paid",
                PaymentOrder.paid_at.is_not(None),
                PaymentOrder.paid_at >= User.created_at,
                PaymentOrder.paid_at < cohort_obs_end,
            )
            .group_by(signup_bucket)
        ).all()
        for row in cohort_payment_rows:
            if row.bucket == _PREV_BUCKET:
                prev_cohort_paid = int(row.users or 0)
            elif row.bucket in by_day:
                by_day[row.bucket]["cohort_paid_users"] = int(row.users or 0)

        # 8. Grant upgrades (current + previous combined in one query)
        grant_rows = session.exec(
            select(
                SubscriptionHistory.created_at,
                SubscriptionHistory.user_id,
                SubscriptionHistory.event_metadata,
            )
            .join(User, User.id == SubscriptionHistory.user_id)
            .where(
                customer,
                _two_period_condition(
                    SubscriptionHistory.created_at, curr_start, curr_end, prev_start, prev_end
                ),
                SubscriptionHistory.action.in_(GRANT_ACTIONS),
                SubscriptionHistory.plan_name != "free",
            )
        ).all()

    curr_grant_events = 0
    curr_grant_users: set[str] = set()
    curr_grant_channels: dict[str, dict[str, Any]] = {}

    prev_grant_events = 0
    prev_grant_users: set[str] = set()
    prev_grant_channels: dict[str, dict[str, Any]] = {}

    daily_grant_users: dict[str, set[str]] = {day: set() for day in by_day}
    daily_grant_channels: dict[str, dict[str, dict[str, Any]]] = {day: {} for day in by_day}

    for created_at, user_id, event_metadata in grant_rows:
        source = event_metadata.get("source") if isinstance(event_metadata, dict) else None
        channel = source.strip() if isinstance(source, str) and source.strip() else "other"
        if channel in PAID_SOURCES:
            continue

        naive_ts = created_at.replace(tzinfo=None) if created_at.tzinfo is not None else created_at
        if curr_start <= naive_ts < curr_end:
            curr_grant_events += 1
            curr_grant_users.add(user_id)
            c_bucket = curr_grant_channels.setdefault(channel, {"events": 0, "user_ids": set()})
            c_bucket["events"] += 1
            c_bucket["user_ids"].add(user_id)

            local_day = normalize_datetime_to_utc(created_at).astimezone(BEIJING_TIMEZONE).date().isoformat()
            entry = by_day.get(local_day)
            if entry is not None:
                entry["grant_upgrade_events"] += 1
                daily_grant_users[local_day].add(user_id)
                d_bucket = daily_grant_channels[local_day].setdefault(
                    channel, {"events": 0, "user_ids": set()}
                )
                d_bucket["events"] += 1
                d_bucket["user_ids"].add(user_id)
        elif prev_start <= naive_ts < prev_end:
            prev_grant_events += 1
            prev_grant_users.add(user_id)
            p_bucket = prev_grant_channels.setdefault(channel, {"events": 0, "user_ids": set()})
            p_bucket["events"] += 1
            p_bucket["user_ids"].add(user_id)

    for day_key, entry in by_day.items():
        entry["grant_upgrade_users"] = len(daily_grant_users[day_key])
        entry["grant_channels"] = _sorted_grant_channels(daily_grant_channels[day_key])
        entry["cohort_activation_rate"] = _rate(entry["cohort_activated_users"], entry["new_users"])
        entry["signup_to_paid_rate"] = _rate(entry["cohort_paid_users"], entry["new_users"])

    curr_new_users = sum(entry["new_users"] for entry in entries)
    curr_cohort_activated = sum(entry["cohort_activated_users"] for entry in entries)
    curr_paid_orders = sum(entry["paid_orders"] for entry in entries)
    curr_revenue_cents = sum(entry["revenue_cents"] for entry in entries)
    curr_revenue_usd_cents = sum(entry["revenue_usd_cents"] for entry in entries)
    curr_cohort_paid = sum(entry["cohort_paid_users"] for entry in entries)

    current_metrics = {
        "new_users": curr_new_users,
        "ai_active_users": curr_ai_active_users,
        "cohort_activated_users": curr_cohort_activated,
        "cohort_activation_rate": _rate(curr_cohort_activated, curr_new_users),
        "paid_orders": curr_paid_orders,
        "revenue_cents": curr_revenue_cents,
        "revenue_usd_cents": curr_revenue_usd_cents,
        "revenue_by_currency": _revenue_by_currency_map(curr_revenue_cents, curr_revenue_usd_cents),
        "paid_users": curr_paid_users,
        "cohort_paid_users": curr_cohort_paid,
        "signup_to_paid_rate": _rate(curr_cohort_paid, curr_new_users),
        "grant_upgrade_events": curr_grant_events,
        "grant_upgrade_users": len(curr_grant_users),
        "grant_channels": _sorted_grant_channels(curr_grant_channels),
    }
    previous_metrics = {
        "new_users": prev_new_users,
        "ai_active_users": prev_ai_active_users,
        "cohort_activated_users": prev_cohort_activated,
        "cohort_activation_rate": _rate(prev_cohort_activated, prev_new_users),
        "paid_orders": prev_paid_orders,
        "revenue_cents": prev_revenue_cents,
        "revenue_usd_cents": prev_revenue_usd_cents,
        "revenue_by_currency": _revenue_by_currency_map(prev_revenue_cents, prev_revenue_usd_cents),
        "paid_users": prev_paid_users,
        "cohort_paid_users": prev_cohort_paid,
        "signup_to_paid_rate": _rate(prev_cohort_paid, prev_new_users),
        "grant_upgrade_events": prev_grant_events,
        "grant_upgrade_users": len(prev_grant_users),
        "grant_channels": _sorted_grant_channels(prev_grant_channels),
    }

    return {
        "days": days,
        "timezone": "Asia/Shanghai",
        "current": {
            "period_start": current.start,
            "period_end": current.end,
            "metrics": current_metrics,
        },
        "previous": {
            "period_start": previous.start,
            "period_end": previous.end,
            "metrics": previous_metrics,
        },
        "daily": entries,
        "definitions": DEFINITIONS,
    }
