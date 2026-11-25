"""Cross-process ¥15 free-user budget checked before every real model call.

Reserve an upper bound before sending; reconcile only when usage is known.
Unknown/cancelled calls retain their reservation. The Beijing day and price band
are fixed at call start, including for calls that finish across midnight. Live
usage records use that same timestamp. No process-local or Redis fallback can
bypass this durable budget.

A free material trial also reserves against the account's lifetime total
(``usage_quota.material_trial_charged_units``), which nothing ever resets. That
call is identified by its job, not by the account's plan, so upgrading during a
trial does not lift it.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, text
from sqlmodel import select

from config.datetime_utils import utcnow
from core.error_codes import ErrorCode
from core.error_handler import APIException
from models.ai_cost_budget import AICostDailyBudget, AICostReservation
from models.llm_usage import LLMUsageEvent, has_usage_tokens
from models.subscription import SubscriptionPlan, UsageQuota, UserSubscription
from services.usage.pricing import (
    COST_WEIGHTS_BY_VERSION,
    beijing_range_utc,
    beijing_today,
    cost_units,
    naive_utc,
    price_band,
    pricing_version_for_model,
    units_to_cny,
)

FREE_DAILY_BUDGET_UNITS = 1_500_000_000
MATERIAL_TRIAL_BUDGET_UNITS = 100_000_000  # 1 CNY, per account, for life.
COST_LIMIT_CODE = "ERR_QUOTA_AI_DAILY_COST_EXCEEDED"
UNAVAILABLE_CODE = "ERR_AI_COST_BUDGET_UNAVAILABLE"
_attribution: ContextVar[Any] = ContextVar("ai_cost_attribution", default=None)
_context_user_id: ContextVar[str | None] = ContextVar("ai_cost_context_user_id", default=None)
_call_started_at: ContextVar[datetime | None] = ContextVar("ai_cost_call_started_at", default=None)
_call_user_id: ContextVar[str | None] = ContextVar("ai_cost_call_user_id", default=None)
_trial_output_limit: ContextVar[int | None] = ContextVar("ai_cost_trial_output_limit", default=None)
_transport_start_hook: ContextVar[Callable[[], None] | None] = ContextVar(
    "ai_cost_transport_start_hook", default=None
)


def call_started_at(user_id: str | None = None) -> datetime | None:
    return _call_started_at.get() if user_id == _call_user_id.get() else None


def bind_call_start(reservation: Reservation | None) -> None:
    _call_started_at.set(reservation.started_at if reservation else None)
    _call_user_id.set(current_user_id() if reservation else None)


def bind_model_transport_start(callback: Callable[[], None] | None) -> Token[Callable[[], None] | None]:
    """Bind a per-request hook invoked right before a real provider transport call starts."""
    return _transport_start_hook.set(callback)


def reset_model_transport_start(token: Token[Callable[[], None] | None]) -> None:
    try:
        _transport_start_hook.reset(token)
    except ValueError:
        _transport_start_hook.set(None)


@contextmanager
def on_model_transport_start(callback: Callable[[], None] | None):
    """Scoped hook invoked right after cost reservation succeeds and before provider transport starts."""
    token = bind_model_transport_start(callback)
    try:
        yield
    finally:
        reset_model_transport_start(token)


def notify_model_transport_start() -> None:
    """Invoke the active transport-start hook, if any."""
    callback = _transport_start_hook.get()
    if callback is not None:
        callback()


@contextmanager
def budget_attribution(attribution: Any):
    token = _attribution.set(attribution)
    try:
        yield
    finally:
        _attribution.reset(token)


@contextmanager
def material_trial_call(*, output_limit: int | None):
    """Model calls inside belong to a free trial job; None means they do not."""
    token = _trial_output_limit.set(output_limit)
    try:
        yield
    finally:
        _trial_output_limit.reset(token)


def set_context_user_id(user_id: str | None) -> None:
    """Bind the ambient user id for the current execution context (e.g. AgentRequestContext)."""
    _context_user_id.set(user_id if isinstance(user_id, str) and user_id else None)


def current_user_id() -> str | None:
    explicit = _attribution.get()
    if explicit is not None and explicit.user_id:
        return explicit.user_id
    return _context_user_id.get()


def _default_session_factory():
    from database import create_session

    return create_session()


_session_factory = _default_session_factory


def _factory():
    return _session_factory()


def _error(code: str, status: int) -> APIException:
    return APIException(error_code=code, status_code=status)


def _log_budget(message: str, *, user_id: str, day: Any, charged_units: int, **extra: Any) -> None:
    from utils.logger import get_logger, log_with_context

    attribution = _attribution.get()
    log_with_context(
        get_logger(__name__),
        20,
        message,
        user_id=user_id,
        usage_source=getattr(attribution, "source", "agent"),
        budget_day=str(day),
        charged_units=charged_units,
        charged_cny=str(units_to_cny(charged_units)),
        daily_limit_units=FREE_DAILY_BUDGET_UNITS,
        daily_limit_cny="15",
        **extra,
    )


def _begin_locked(session: Any) -> None:
    if session.get_bind().dialect.name == "sqlite":
        session.execute(text("BEGIN IMMEDIATE"))


def _has_non_free_entitlement(session: Any, user_id: str, now: datetime) -> bool:
    """Active non-free entitlement, including granted/trial Pro subscriptions."""
    subscription = session.exec(
        select(UserSubscription, SubscriptionPlan)
        .join(SubscriptionPlan, SubscriptionPlan.id == UserSubscription.plan_id)
        .where(UserSubscription.user_id == user_id)
    ).first()
    if subscription is None:
        return False
    sub, plan = subscription
    if sub.status != "active" or plan.name == "free":
        return False
    if sub.current_period_end is None:
        # The existing entitlement contract requires a period end (grants also
        # have one). An invalid entitlement must not silently become free.
        raise _error(UNAVAILABLE_CODE, 503)
    return naive_utc(sub.current_period_end) >= naive_utc(now)


_TEXT_CONTENT_TYPES = frozenset({
    "text",
    "input_text",
    "output_text",
    "reasoning_text",
    "summary_text",
    "refusal",
})
_MULTIMODAL_RESPONSES_ITEM_TYPES = frozenset({"input_image", "input_audio", "input_file", "image_url"})


def _is_omitted(value: Any) -> bool:
    if value is None:
        return True
    cls_name = type(value).__name__
    return cls_name in {"Omit", "NotGiven"} and type(value).__module__.startswith("openai")


def _is_responses_payload(payload: dict[str, Any]) -> bool:
    return "messages" not in payload and (
        ("input" in payload and not _is_omitted(payload.get("input")))
        or ("max_output_tokens" in payload and not _is_omitted(payload.get("max_output_tokens")))
        or ("instructions" in payload and not _is_omitted(payload.get("instructions")))
    )


def _check_text_only_content(content: Any) -> None:
    if isinstance(content, list) and any(
        isinstance(item, dict) and item.get("type") not in _TEXT_CONTENT_TYPES for item in content
    ):
        raise _error(UNAVAILABLE_CODE, 503)


def _input_upper_bound(payload: dict[str, Any]) -> int:
    # A text token cannot contain less than one UTF-8 byte. Count the entire
    # serialized request (including tools, instructions, and framing), plus per-item framing.
    # Multimodal inputs have provider-specific accounting; fail closed for free.
    messages = payload.get("messages")
    if isinstance(messages, list):
        for message in messages:
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, list) and any(
                item.get("type") not in {"text", "input_text"} for item in content if isinstance(item, dict)
            ):
                raise _error(UNAVAILABLE_CODE, 503)
        item_count = len(messages)
    else:
        raw_input = payload.get("input")
        if isinstance(raw_input, list):
            for entry in raw_input:
                if not isinstance(entry, dict):
                    continue
                if entry.get("type") in _MULTIMODAL_RESPONSES_ITEM_TYPES:
                    raise _error(UNAVAILABLE_CODE, 503)
                _check_text_only_content(entry.get("content"))
            item_count = len(raw_input)
        elif not _is_omitted(raw_input):
            item_count = 1
        else:
            item_count = 0
        if not _is_omitted(payload.get("instructions")):
            item_count += 1
    return len(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")) + 128 * (item_count + 1)


@dataclass(frozen=True)
class Reservation:
    id: str
    started_at: datetime
    # Price table of the model the reservation was made for (settlement uses it).
    pricing_version: str | None = None


def _commit_reservation(
    session: Any,
    user_id: str,
    day: Any,
    upper: int,
    started: datetime,
    pricing_version: str,
    quota: UsageQuota | None,
    *,
    charges_daily: bool = True,
) -> Reservation:
    record = AICostReservation(
        id=uuid.uuid4().hex,
        user_id=user_id,
        day=day,
        reserved_units=upper,
        started_at=started,
        charges_daily=charges_daily,
        charges_trial=quota is not None,
    )
    if quota is not None:
        quota.material_trial_charged_units += upper
        session.add(quota)
    session.add(record)
    session.commit()
    return Reservation(record.id, started, pricing_version)


def reserve_model_call(
    payload: dict[str, Any], *, user_id: str | None = None, now: datetime | None = None
) -> Reservation | None:
    """Atomically reserve worst-case call cost; paid users bypass the free cap."""
    started = naive_utc(now or utcnow())
    trial_limit = _trial_output_limit.get()
    user_id = user_id or current_user_id()
    _call_started_at.set(started if user_id else None)
    _call_user_id.set(user_id)
    if not user_id:
        if trial_limit is not None:
            raise _error(UNAVAILABLE_CODE, 503)
        return None  # Infrastructure calls without a user are not free-user calls.
    try:
        with _factory() as session:
            _begin_locked(session)
            paid = _has_non_free_entitlement(session, user_id, started)
            if paid and trial_limit is None:
                session.rollback()
                return None
            pricing_version = pricing_version_for_model(payload.get("model"))
            if pricing_version is None:
                raise _error(UNAVAILABLE_CODE, 503)
            n_value = payload.get("n", 1)
            if not _is_omitted(n_value) and n_value != 1:
                raise _error(UNAVAILABLE_CODE, 503)
            is_responses = _is_responses_payload(payload)
            output_key = "max_output_tokens" if is_responses else "max_tokens"
            if trial_limit is not None:
                # The last step before transport, so failover headroom is covered too.
                if payload.get("stream") is True:
                    raise _error(UNAVAILABLE_CODE, 503)
                requested = (
                    payload.get("max_output_tokens")
                    if is_responses
                    else payload.pop("max_completion_tokens", payload.get("max_tokens"))
                )
                valid = isinstance(requested, int) and not isinstance(requested, bool) and requested > 0
                payload[output_key] = min(requested, trial_limit) if valid else trial_limit
            maximum = (
                payload.get("max_output_tokens")
                if is_responses
                else payload.get("max_completion_tokens", payload.get("max_tokens"))
            )
            if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum <= 0:
                # Set explicit output bound; DeepSeek thinking default can be 64K.
                maximum = 65_536
                payload[output_key] = maximum
            upper = cost_units("peak", 0, _input_upper_bound(payload), maximum, pricing_version=pricing_version)
            day = beijing_today(started)
            quota = None
            if trial_limit is not None:
                quota = session.exec(
                    select(UsageQuota).where(UsageQuota.user_id == user_id).with_for_update()
                ).first()
                if quota is None or quota.material_trial_used_at is None:
                    # A trial job always has its committed mark; without it nothing is provable.
                    raise _error(UNAVAILABLE_CODE, 503)
                if quota.material_trial_charged_units + upper > MATERIAL_TRIAL_BUDGET_UNITS:
                    raise _error(ErrorCode.MATERIAL_TRIAL_COST_LIMIT, 402)
            if paid:
                # Upgraded during the trial: only the lifetime total applies.
                return _commit_reservation(
                    session, user_id, day, upper, started, pricing_version, quota, charges_daily=False
                )
            # Portable upsert, then lock this user's day. SQLite has its writer
            # lock already; PostgreSQL locks the inserted/conflicting row.
            session.execute(
                text(
                    "INSERT INTO ai_cost_daily_budget (user_id, day, charged_units) "
                    "VALUES (:user_id, :day, -1) ON CONFLICT (user_id, day) DO NOTHING"
                ),
                {"user_id": user_id, "day": day},
            )
            bucket = session.exec(
                select(AICostDailyBudget)
                .where(AICostDailyBudget.user_id == user_id, AICostDailyBudget.day == day)
                .with_for_update()
            ).one()
            initialized = bucket.charged_units < 0
            if initialized:
                start, end = beijing_range_utc(day, day)
                historical = session.exec(
                    select(LLMUsageEvent).where(
                        LLMUsageEvent.user_id == user_id,
                        LLMUsageEvent.occurred_at >= start,
                        LLMUsageEvent.occurred_at < end,
                        has_usage_tokens(LLMUsageEvent),
                    )
                ).all()
                if any(
                    pricing_version_for_model(event.model) is None
                    or event.pricing_version not in COST_WEIGHTS_BY_VERSION
                    for event in historical
                ):
                    # We cannot prove a remaining budget from unpriced history.
                    raise _error(UNAVAILABLE_CODE, 503)
                bucket.charged_units = sum(
                    cost_units(
                        price_band(event.occurred_at),
                        event.cache_hit_tokens,
                        event.cache_miss_tokens,
                        event.output_tokens,
                        pricing_version=event.pricing_version,
                    )
                    for event in historical
                )
                seed_units = bucket.charged_units
            if bucket.charged_units + upper > FREE_DAILY_BUDGET_UNITS:
                pending = session.exec(
                    select(func.coalesce(func.sum(AICostReservation.reserved_units), 0)).where(
                        AICostReservation.user_id == user_id,
                        AICostReservation.day == day,
                        AICostReservation.settled_units.is_(None),
                    )
                ).one()
                # Persist historical initialization even when already over cap.
                session.add(bucket)
                session.commit()
                if initialized:
                    _log_budget(
                        "AI free daily cost budget initialized",
                        user_id=user_id,
                        day=day,
                        charged_units=seed_units,
                        historical_rows=len(historical),
                    )
                _log_budget(
                    "AI free daily cost limit reached",
                    user_id=user_id,
                    day=day,
                    charged_units=bucket.charged_units,
                    requested_reservation_units=upper,
                    pending_reserved_units=int(pending),
                    error_code=COST_LIMIT_CODE,
                )
                raise _error(COST_LIMIT_CODE, 402)
            bucket.charged_units += upper
            session.add(bucket)
            reservation = _commit_reservation(session, user_id, day, upper, started, pricing_version, quota)
            if initialized:
                _log_budget(
                    "AI free daily cost budget initialized",
                    user_id=user_id,
                    day=day,
                    charged_units=seed_units,
                    historical_rows=len(historical),
                )
            return reservation
    except APIException:
        raise
    except Exception as exc:
        raise _error(UNAVAILABLE_CODE, 503) from exc


def settle_model_call(reservation: Reservation | None, usage: Any) -> None:
    """Reconcile exactly once; missing usage/failure keeps the upper-bound charge."""
    if reservation is None or usage is None:
        return
    from services.usage.llm_usage_service import _int_field

    prompt = _int_field(usage, "prompt_tokens")
    if prompt is None:
        prompt = _int_field(usage, "input_tokens")
    output = _int_field(usage, "completion_tokens")
    if output is None:
        output = _int_field(usage, "output_tokens")
    if prompt is None or output is None:
        return  # An incomplete usage object is not a proven zero-cost call.
    from services.usage.llm_usage_service import extract_usage_tokens

    tokens = extract_usage_tokens(usage)
    hit = min(prompt, tokens.cache_hit)
    # An absent/inconsistent cache split must not discount unaccounted input.
    miss = max(tokens.cache_miss, prompt - hit)
    actual = cost_units(
        price_band(reservation.started_at),
        hit,
        miss,
        tokens.output,
        **({"pricing_version": reservation.pricing_version} if reservation.pricing_version else {}),
    )
    try:
        with _factory() as session:
            _begin_locked(session)
            record = session.exec(
                select(AICostReservation).where(AICostReservation.id == reservation.id).with_for_update()
            ).one()
            if record.settled_units is not None:
                return
            # A provider violating the upper bound must never refund overspend.
            difference = actual - record.reserved_units
            if record.charges_trial:
                quota = session.exec(
                    select(UsageQuota).where(UsageQuota.user_id == record.user_id).with_for_update()
                ).one()
                quota.material_trial_charged_units += difference
                session.add(quota)
            if record.charges_daily:
                bucket = session.exec(
                    select(AICostDailyBudget)
                    .where(AICostDailyBudget.user_id == record.user_id, AICostDailyBudget.day == record.day)
                    .with_for_update()
                ).one()
                bucket.charged_units += difference
                session.add(bucket)
            record.settled_units = actual
            session.add(record)
            session.commit()
    except Exception:
        # Reservation remains fully charged if reconciliation cannot persist.
        from utils.logger import get_logger, log_with_context

        log_with_context(
            get_logger(__name__), 40, "AI cost reservation settlement failed", reservation_id=reservation.id
        )
