"""Admin "Usage & cost": per-user token usage and CNY cost, and per-model call metrics."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlmodel import Session

from core.error_codes import ErrorCode
from core.error_handler import APIException
from database import get_session
from models.entities import User
from services.core.auth_service import get_current_superuser
from services.infra.admin_analytics_cache import (
    DEFAULT_CACHE_TTL_SECONDS,
    LONG_WINDOW_CACHE_TTL_SECONDS,
    cached_admin_analytics,
)
from services.usage.admin_stage_latency_service import (
    STAGE_LATENCY_DAYS,
    get_stage_latency_metrics,
)
from services.usage.admin_usage_service import (
    get_model_metrics,
    get_usage_summary,
    get_user_daily_usage,
    list_user_usage,
)

router = APIRouter(prefix="/usage", tags=["admin-usage"])

UsageWindowParam = Literal["today", "yesterday", "7d"]
# models.llm_usage.LLM_USAGE_SOURCES
UsageSourceParam = Literal["agent", "router", "suggest", "polish", "material"]
USER_DAILY_DAYS = (7, 14, 30)


class UsageMetrics(BaseModel):
    calls: int
    cache_hit_tokens: int
    cache_miss_tokens: int
    output_tokens: int
    # Decimal strings, CNY rounded to 4 places.
    cost_cny: str
    peak_cost_cny: str
    offpeak_cost_cny: str


class UsageTotals(UsageMetrics):
    users: int


class UsageSourceRow(UsageMetrics):
    source: str


class UsageDailyRow(UsageMetrics):
    date: str


class UsageDailyRowWithUsers(UsageDailyRow):
    users: int


class UsagePeriod(BaseModel):
    timezone: str
    period_start: str
    period_end: str
    pricing_version: str
    # {"peak"|"offpeak": {"cache_hit"|"cache_miss"|"output": "<CNY per 1M tokens>"}}
    prices: dict[str, dict[str, str]]


class UsageSummaryResponse(UsagePeriod):
    window: UsageWindowParam
    totals: UsageTotals
    by_source: list[UsageSourceRow]
    daily: list[UsageDailyRowWithUsers]


class UserUsageRow(UsageMetrics):
    user_id: str
    username: str
    email: str
    last_used_at: datetime | None


class UserUsageListResponse(UsagePeriod):
    window: UsageWindowParam
    items: list[UserUsageRow]
    total: int
    page: int
    page_size: int


class UserDailyUsageResponse(UsagePeriod):
    user_id: str
    username: str
    email: str
    days: int
    totals: UsageMetrics
    by_source: list[UsageSourceRow]
    daily: list[UsageDailyRow]


class Distribution(BaseModel):
    # Nearest-rank percentiles in milliseconds; None when there are no samples.
    p50: int | None
    p95: int | None
    samples: int


class DecodeSpeed(BaseModel):
    # Output tokens / generation time (first output delta -> stream end), tokens per second.
    value: float | None
    samples: int


class ModelMetrics(BaseModel):
    # Provider attempts in the window (a failover counts both attempts).
    attempts: int
    success: int
    error: int
    timeout: int
    cancelled: int
    not_recorded: int
    # success / (success + error + timeout), 0..1; None without finished attempts.
    success_rate: float | None
    latency_ms: Distribution
    ttft_ms: Distribution
    decode_tps: DecodeSpeed
    cache_hit_tokens: int
    cache_miss_tokens: int
    output_tokens: int
    cost_cny: str


class ModelMetricsRow(ModelMetrics):
    model: str


class ModelMetricsResponse(UsagePeriod):
    window: UsageWindowParam
    source: UsageSourceParam | None
    items: list[ModelMetricsRow]
    totals: ModelMetrics


class SlowTailDistribution(Distribution):
    slow_count: int
    slow_rate: float | None


class ChapterFirstProseDistribution(SlowTailDistribution):
    p90: int | None


class DeviceStageLatencySlice(BaseModel):
    rounds: int
    first_output_ms: SlowTailDistribution
    chapter_first_prose_ms: ChapterFirstProseDistribution
    client_disconnected_rounds: int
    client_disconnected_rate: float | None


class DeviceStageLatencySplit(BaseModel):
    desktop: DeviceStageLatencySlice
    mobile: DeviceStageLatencySlice
    unknown: DeviceStageLatencySlice


class AiDraftSurvivalMetrics(BaseModel):
    eligible_drafts: int
    survived_drafts: int
    co_edited_drafts: int
    deleted_or_reverted_drafts: int
    survival_rate: float | None
    co_edit_rate: float | None
    observation_hours: int


class StageLatencyDailyRow(BaseModel):
    date: str
    rounds: int
    prepare_context_p50_ms: int | None
    first_output_p50_ms: int | None
    first_output_p95_ms: int | None
    first_output_slow_rate: float | None
    chapter_first_prose_p50_ms: int | None
    chapter_first_prose_p95_ms: int | None
    chapter_first_prose_slow_rate: float | None
    file_body_pending_rate: float | None
    leaked_tool_call_rate: float | None
    user_stopped_rate: float | None
    client_disconnected_rate: float | None


class StageLatencyResponse(BaseModel):
    days: int
    timezone: str
    period_start: str
    period_end: str
    slow_threshold_ms: int
    total_rounds: int
    prepare_context_ms: Distribution
    first_output_ms: SlowTailDistribution
    chapter_first_prose_ms: ChapterFirstProseDistribution
    first_body_persisted_ms: Distribution
    file_body_pending_rounds: int
    file_body_pending_rate: float | None
    leaked_tool_call_rounds: int
    leaked_tool_call_rate: float | None
    user_stopped_rounds: int
    user_stopped_rate: float | None
    client_disconnected_rounds: int
    client_disconnected_rate: float | None
    desktop_vs_mobile: DeviceStageLatencySplit
    ai_draft_survival: AiDraftSurvivalMetrics
    daily: list[StageLatencyDailyRow]


@router.get("/summary", response_model=UsageSummaryResponse)
def usage_summary(
    window: UsageWindowParam = "today",
    refresh: bool = Query(default=False),
    _current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    ttl = 300 if window == "yesterday" else (60 if window == "today" else DEFAULT_CACHE_TTL_SECONDS)
    return cached_admin_analytics(
        "usage:summary",
        lambda: get_usage_summary(session, window),
        ttl_seconds=ttl,
        refresh=refresh,
        window=window,
    )


@router.get("/models", response_model=ModelMetricsResponse)
def usage_by_model(
    window: UsageWindowParam = "today",
    source: UsageSourceParam | None = None,
    refresh: bool = Query(default=False),
    _current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    ttl = 300 if window == "yesterday" else (60 if window == "today" else DEFAULT_CACHE_TTL_SECONDS)
    return cached_admin_analytics(
        "usage:models",
        lambda: get_model_metrics(session, window, source=source),
        ttl_seconds=ttl,
        refresh=refresh,
        window=window,
        source=source,
    )


@router.get("/stage-latency", response_model=StageLatencyResponse)
def usage_stage_latency(
    days: int = Query(default=7),
    refresh: bool = Query(default=False),
    _current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    if days not in STAGE_LATENCY_DAYS:
        raise APIException(
            error_code=ErrorCode.VALIDATION_ERROR,
            status_code=422,
            detail="days must be 7, 14 or 30",
        )
    ttl = DEFAULT_CACHE_TTL_SECONDS if days == 7 else LONG_WINDOW_CACHE_TTL_SECONDS
    return cached_admin_analytics(
        "usage:stage_latency",
        lambda: get_stage_latency_metrics(session, days),
        ttl_seconds=ttl,
        refresh=refresh,
        days=days,
    )


@router.get("/users", response_model=UserUsageListResponse)
def usage_by_user(
    window: UsageWindowParam = "today",
    search: str | None = Query(default=None, max_length=100),
    sort: Literal["cost", "calls", "tokens"] = "cost",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    return list_user_usage(
        session, window, search=search, sort=sort, page=page, page_size=page_size
    )


@router.get("/users/{user_id}/daily", response_model=UserDailyUsageResponse)
def user_daily_usage(
    user_id: str,
    days: int = Query(default=7),
    _current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    if days not in USER_DAILY_DAYS:
        raise APIException(
            error_code=ErrorCode.VALIDATION_ERROR,
            status_code=422,
            detail="days must be 7, 14 or 30",
        )
    user = session.get(User, user_id)
    if user is None:
        raise APIException(
            error_code=ErrorCode.NOT_FOUND,
            status_code=404,
            detail="User not found",
        )
    return get_user_daily_usage(session, user, days)
