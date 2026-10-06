"""Admin "Usage & cost": per-user DeepSeek token usage and CNY cost."""

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
from services.usage.admin_usage_service import (
    get_usage_summary,
    get_user_daily_usage,
    list_user_usage,
)

router = APIRouter(prefix="/usage", tags=["admin-usage"])

UsageWindowParam = Literal["today", "yesterday", "7d"]
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


@router.get("/summary", response_model=UsageSummaryResponse)
def usage_summary(
    window: UsageWindowParam = "today",
    _current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    return get_usage_summary(session, window)


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
