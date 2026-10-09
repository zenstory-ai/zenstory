"""Project statistics API endpoints"""
import logging
import os
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from services.auth import get_current_active_user
from sqlmodel import Session

from config.datetime_utils import utcnow
from core.error_codes import ErrorCode
from core.error_handler import APIException
from core.project_access import verify_project_ownership
from database import get_session
from models import User
from services.features.activation_event_service import activation_event_service
from services.features.writing_stats_service import writing_stats_service
from services.infra.dashboard_cache import dashboard_cache
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["stats"])


def _read_cache_ttl_seconds(env_name: str, default: int) -> int:
    """Read non-negative cache TTL seconds from env with safe fallback."""
    raw = os.getenv(env_name)
    if raw is None:
        return default
    try:
        return max(0, int(raw.strip()))
    except ValueError:
        return default


def _parse_iso_date_or_none(value: str | None, field_name: str) -> date | None:
    """Parse YYYY-MM-DD string into date or raise validation error."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as err:
        raise APIException(
            error_code=ErrorCode.VALIDATION_ERROR,
            status_code=400,
            detail=f"Invalid {field_name} format. Use YYYY-MM-DD."
        ) from err


# ============== Statistics Schemas ==============

# --- Word Count Trend Schemas ---

class DailyWordCountItem(BaseModel):
    """Single day word count data."""
    date: str
    word_count: int
    words_added: int
    words_deleted: int
    net_words: int
    edit_sessions: int


class WeeklyWordCountItem(BaseModel):
    """Weekly aggregated word count data."""
    date: str
    period_label: str
    word_count: int
    words_added: int
    words_deleted: int
    net_words: int
    edit_sessions: int
    days_with_activity: int
    avg_words_per_day: int


class MonthlyWordCountItem(BaseModel):
    """Monthly aggregated word count data."""
    date: str
    period_label: str
    word_count: int
    words_added: int
    words_deleted: int
    net_words: int
    edit_sessions: int
    days_with_activity: int
    avg_words_per_day: int


class WordCountTrendResponse(BaseModel):
    """Response for word count trend endpoint."""
    period: str
    days: int
    data: list[Any]  # List of DailyWordCountItem, WeeklyWordCountItem, or MonthlyWordCountItem
    total_words_added: int
    total_words_deleted: int
    total_net_words: int


# --- Chapter Completion Schemas ---

class ChapterDetailItem(BaseModel):
    """Single chapter completion status."""
    outline_id: str
    draft_id: str | None = None
    title: str
    word_count: int
    target_word_count: int | None = None
    status: str  # "complete", "in_progress", "not_started"
    completion_percentage: int


class ChapterCompletionResponse(BaseModel):
    """Response for chapter completion statistics."""
    total_chapters: int
    completed_chapters: int
    in_progress_chapters: int
    not_started_chapters: int
    completion_percentage: int
    chapter_details: list[ChapterDetailItem]


# --- Writing Streak Schemas ---

class WritingStreakResponse(BaseModel):
    """Response for writing streak status."""
    current_streak: int
    longest_streak: int
    streak_status: str  # "active", "at_risk", "broken", "none"
    days_until_break: int
    last_writing_date: str | None = None
    streak_start_date: str | None = None
    streak_recovery_count: int
    can_recover: bool
    grace_period_days: int
    min_words_for_day: int


class StreakHistoryItem(BaseModel):
    """Single day in streak history."""
    date: str
    wrote: bool
    word_count: int
    streak_count: int


class StreakHistoryResponse(BaseModel):
    """Response for streak history endpoint."""
    days: int
    history: list[StreakHistoryItem]


# --- AI Usage Schemas ---

class AIUsageStatsResponse(BaseModel):
    """AI usage statistics for a project."""
    total_sessions: int
    active_session_id: str | None = None
    total_messages: int
    user_messages: int
    ai_messages: int
    tool_messages: int
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    total_tokens: int = 0
    estimated_tokens: int
    estimated_cost_usd: float = 0.0
    first_interaction_date: str | None = None
    last_interaction_date: str | None = None


class AIUsageTrendItem(BaseModel):
    """Single period AI usage data."""
    date: str
    period_label: str | None = None
    total_messages: int
    user_messages: int
    ai_messages: int
    tool_messages: int
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    total_tokens: int = 0
    estimated_tokens: int
    estimated_cost_usd: float = 0.0
    days_with_activity: int | None = None


class AIUsageTrendResponse(BaseModel):
    """Response for AI usage trend endpoint."""
    period: str
    days: int
    data: list[AIUsageTrendItem]


class AIUsagePeriodSummary(BaseModel):
    """AI usage summary for a period."""
    total: int
    user: int
    ai: int
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    total_tokens: int = 0
    estimated_tokens: int
    estimated_cost_usd: float = 0.0


class AIUsageSummaryResponse(BaseModel):
    """Comprehensive AI usage summary for dashboard."""
    current: AIUsageStatsResponse
    today: AIUsagePeriodSummary
    this_week: AIUsagePeriodSummary
    this_month: AIUsagePeriodSummary


# --- Record Stats Schemas ---

class RecordStatsRequest(BaseModel):
    """Request body for recording daily writing stats."""
    word_count: int = Field(ge=0, description="Total word count")
    words_added: int = Field(default=0, ge=0, description="Words added in this session")
    words_deleted: int = Field(default=0, ge=0, description="Words deleted in this session")
    edit_time_seconds: int = Field(default=0, ge=0, description="Time spent editing in seconds")
    stats_date: str | None = Field(default=None, description="Date to record for (YYYY-MM-DD, defaults to today)")


class RecordStatsResponse(BaseModel):
    """Response for recording writing stats."""
    id: str
    user_id: str
    project_id: str
    stats_date: str
    word_count: int
    words_added: int
    words_deleted: int
    edit_sessions: int
    total_edit_time_seconds: int
    created_at: datetime
    updated_at: datetime
    streak_updated: bool = False
    new_streak: int | None = None

    model_config = ConfigDict(from_attributes=True)


# --- Combined Dashboard Stats ---

class ProjectDashboardStatsResponse(BaseModel):
    """Combined statistics for project dashboard."""
    project_id: str
    project_name: str
    # novel / short / screenplay: the dashboard names chapters 章 / 篇 / 集.
    project_type: str = "novel"
    # Word count
    total_word_count: int
    words_today: int
    words_this_week: int
    words_this_month: int
    # Chapter completion
    chapter_completion: ChapterCompletionResponse
    # Writing streak
    streak: WritingStreakResponse
    # AI usage
    ai_usage: AIUsageSummaryResponse
    # Timestamps
    generated_at: str


class ActivationGuideStepResponse(BaseModel):
    """Single first-day activation guide step."""

    event_name: str
    label: str
    completed: bool
    completed_at: str | None = None
    action_path: str


class ActivationGuideResponse(BaseModel):
    """Current user's first-day activation guide state."""

    user_id: str
    window_hours: int
    within_first_day: bool
    total_steps: int
    completed_steps: int
    completion_rate: float
    is_activated: bool
    next_event_name: str | None = None
    next_action: str | None = None
    steps: list[ActivationGuideStepResponse]


# ==================== Project Statistics ====================
@router.get("/activation/guide", response_model=ActivationGuideResponse)
def get_my_activation_guide(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    """
    Get first-day activation guide state for current user.

    Returned payload can drive onboarding wizard/checklist UI:
    signup -> create project -> save first file -> complete first AI action.
    """
    guide = activation_event_service.get_user_guide(
        session,
        user_id=current_user.id,
        user_created_at=current_user.created_at,
    )

    log_with_context(
        logger,
        logging.INFO,
        "Fetched activation guide",
        user_id=current_user.id,
        completed_steps=guide["completed_steps"],
        total_steps=guide["total_steps"],
        next_event_name=guide["next_event_name"],
    )

    return ActivationGuideResponse(**guide)


@router.get("/projects/{project_id}/stats", response_model=ProjectDashboardStatsResponse)
def get_project_stats(
    project_id: str,
    client_date: str | None = Query(
        default=None,
        description="Client local date in YYYY-MM-DD, used for today/week/month boundaries",
    ),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Get combined statistics for a project dashboard.

    Returns aggregated statistics including:
    - Word count trends (today, this week, this month)
    - Chapter completion status
    - Writing streak information
    - AI usage metrics
    """
    # Check project ownership
    project = verify_project_ownership(project_id, current_user, session)

    cache_ttl_seconds = _read_cache_ttl_seconds(
        "DASHBOARD_PROJECT_STATS_CACHE_TTL_SECONDS",
        15,
    )

    # Use client-local date when provided to avoid UTC day-boundary skew.
    today = _parse_iso_date_or_none(client_date, "client_date") or utcnow().date()

    project_cache_version = dashboard_cache.get_project_version(
        current_user.id,
        project_id,
    )
    normalized_client_date = client_date or today.isoformat()
    cache_key = (
        "project_stats:"
        f"v{project_cache_version}:"
        f"{current_user.id}:{project_id}:{normalized_client_date}"
    )

    if cache_ttl_seconds > 0:
        cached_payload = dashboard_cache.get_json(cache_key)
        if isinstance(cached_payload, dict):
            return cached_payload

    log_with_context(
        logger,
        logging.INFO,
        "Fetching project statistics",
        user_id=current_user.id,
        project_id=project_id,
    )

    # Get total word count from draft files
    total_word_count = writing_stats_service.get_total_word_count(
        session, current_user.id, project_id
    )

    # Get words written in different periods
    words_today = writing_stats_service.get_words_written_in_period(
        session, current_user.id, project_id, today, today
    )
    words_this_week = writing_stats_service.get_words_written_in_period(
        session, current_user.id, project_id,
        today - timedelta(days=today.weekday()),  # Monday of current week
        today
    )
    words_this_month = writing_stats_service.get_words_written_in_period(
        session, current_user.id, project_id,
        today.replace(day=1),  # First day of current month
        today
    )

    # Get chapter completion stats
    chapter_completion_data = writing_stats_service.get_chapter_completion_stats(
        session, project_id
    )
    chapter_completion = ChapterCompletionResponse(
        total_chapters=chapter_completion_data["total_chapters"],
        completed_chapters=chapter_completion_data["completed_chapters"],
        in_progress_chapters=chapter_completion_data["in_progress_chapters"],
        not_started_chapters=chapter_completion_data["not_started_chapters"],
        completion_percentage=chapter_completion_data["completion_percentage"],
        chapter_details=[
            ChapterDetailItem(**detail)
            for detail in chapter_completion_data["chapter_details"]
        ],
    )

    # Get writing streak stats
    streak_data = writing_stats_service.get_streak(
        session,
        current_user.id,
        project_id,
        reference_date=today,
    )
    streak = WritingStreakResponse(
        current_streak=streak_data["current_streak"],
        longest_streak=streak_data["longest_streak"],
        streak_status=streak_data["streak_status"],
        days_until_break=streak_data["days_until_break"],
        last_writing_date=streak_data["last_writing_date"],
        streak_start_date=streak_data["streak_start_date"],
        streak_recovery_count=streak_data["streak_recovery_count"],
        can_recover=streak_data["can_recover"],
        grace_period_days=streak_data["grace_period_days"],
        min_words_for_day=streak_data["min_words_for_day"],
    )

    # Get AI usage summary
    ai_usage_data = writing_stats_service.get_ai_usage_summary(
        session, current_user.id, project_id, reference_date=today
    )
    ai_usage = AIUsageSummaryResponse(
        current=AIUsageStatsResponse(
            total_sessions=ai_usage_data["current"]["total_sessions"],
            active_session_id=ai_usage_data["current"]["active_session_id"],
            total_messages=ai_usage_data["current"]["total_messages"],
            user_messages=ai_usage_data["current"]["user_messages"],
            ai_messages=ai_usage_data["current"]["ai_messages"],
            tool_messages=ai_usage_data["current"]["tool_messages"],
            input_tokens=ai_usage_data["current"].get("input_tokens", 0),
            output_tokens=ai_usage_data["current"].get("output_tokens", 0),
            cache_read_tokens=ai_usage_data["current"].get("cache_read_tokens", 0),
            cache_write_tokens=ai_usage_data["current"].get("cache_write_tokens", 0),
            total_tokens=ai_usage_data["current"].get("total_tokens", 0),
            estimated_tokens=ai_usage_data["current"]["estimated_tokens"],
            estimated_cost_usd=ai_usage_data["current"].get("estimated_cost_usd", 0.0),
            first_interaction_date=ai_usage_data["current"]["first_interaction_date"],
            last_interaction_date=ai_usage_data["current"]["last_interaction_date"],
        ),
        today=AIUsagePeriodSummary(
            total=ai_usage_data["today"]["total"],
            user=ai_usage_data["today"]["user"],
            ai=ai_usage_data["today"]["ai"],
            input_tokens=ai_usage_data["today"].get("input_tokens", 0),
            output_tokens=ai_usage_data["today"].get("output_tokens", 0),
            cache_read_tokens=ai_usage_data["today"].get("cache_read_tokens", 0),
            cache_write_tokens=ai_usage_data["today"].get("cache_write_tokens", 0),
            total_tokens=ai_usage_data["today"].get("total_tokens", 0),
            estimated_tokens=ai_usage_data["today"]["estimated_tokens"],
            estimated_cost_usd=ai_usage_data["today"].get("estimated_cost_usd", 0.0),
        ),
        this_week=AIUsagePeriodSummary(
            total=ai_usage_data["this_week"]["total"],
            user=ai_usage_data["this_week"]["user"],
            ai=ai_usage_data["this_week"]["ai"],
            input_tokens=ai_usage_data["this_week"].get("input_tokens", 0),
            output_tokens=ai_usage_data["this_week"].get("output_tokens", 0),
            cache_read_tokens=ai_usage_data["this_week"].get("cache_read_tokens", 0),
            cache_write_tokens=ai_usage_data["this_week"].get("cache_write_tokens", 0),
            total_tokens=ai_usage_data["this_week"].get("total_tokens", 0),
            estimated_tokens=ai_usage_data["this_week"]["estimated_tokens"],
            estimated_cost_usd=ai_usage_data["this_week"].get("estimated_cost_usd", 0.0),
        ),
        this_month=AIUsagePeriodSummary(
            total=ai_usage_data["this_month"]["total"],
            user=ai_usage_data["this_month"]["user"],
            ai=ai_usage_data["this_month"]["ai"],
            input_tokens=ai_usage_data["this_month"].get("input_tokens", 0),
            output_tokens=ai_usage_data["this_month"].get("output_tokens", 0),
            cache_read_tokens=ai_usage_data["this_month"].get("cache_read_tokens", 0),
            cache_write_tokens=ai_usage_data["this_month"].get("cache_write_tokens", 0),
            total_tokens=ai_usage_data["this_month"].get("total_tokens", 0),
            estimated_tokens=ai_usage_data["this_month"]["estimated_tokens"],
            estimated_cost_usd=ai_usage_data["this_month"].get("estimated_cost_usd", 0.0),
        ),
    )

    log_with_context(
        logger,
        logging.INFO,
        "Project statistics fetched successfully",
        user_id=current_user.id,
        project_id=project_id,
        total_word_count=total_word_count,
        completion_percentage=chapter_completion.completion_percentage,
        current_streak=streak.current_streak,
    )

    response = ProjectDashboardStatsResponse(
        project_id=project_id,
        project_name=project.name,
        project_type=project.project_type or "novel",
        total_word_count=total_word_count,
        words_today=words_today["net_words"],
        words_this_week=words_this_week["net_words"],
        words_this_month=words_this_month["net_words"],
        chapter_completion=chapter_completion,
        streak=streak,
        ai_usage=ai_usage,
        generated_at=utcnow().isoformat(),
    )

    if cache_ttl_seconds > 0:
        dashboard_cache.set_json(
            cache_key,
            response.model_dump(),
            ttl_seconds=cache_ttl_seconds,
        )

    return response


@router.get(
    "/projects/{project_id}/stats/word-count-trend",
    response_model=WordCountTrendResponse,
)
def get_project_word_count_trend(
    project_id: str,
    period: str = Query(
        "daily",
        pattern="^(daily|weekly|monthly)$",
        description="Aggregation period: daily, weekly, or monthly",
    ),
    days: int = Query(30, ge=1, le=365, description="Number of days to look back"),
    client_date: str | None = Query(
        default=None,
        description="Client local date in YYYY-MM-DD for trend window end date",
    ),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    """
    Get word count trend data for a project.

    Returns time-series data grouped by day/week/month.
    """
    verify_project_ownership(project_id, current_user, session)

    cache_ttl_seconds = _read_cache_ttl_seconds(
        "DASHBOARD_WORD_COUNT_TREND_CACHE_TTL_SECONDS",
        60,
    )

    end_date = _parse_iso_date_or_none(client_date, "client_date")
    normalized_end_date = (end_date or utcnow().date()).isoformat()

    project_cache_version = dashboard_cache.get_project_version(
        current_user.id,
        project_id,
    )
    cache_key = (
        "word_count_trend:"
        f"v{project_cache_version}:"
        f"{current_user.id}:{project_id}:{period}:{days}:{normalized_end_date}"
    )

    if cache_ttl_seconds > 0:
        cached_payload = dashboard_cache.get_json(cache_key)
        if isinstance(cached_payload, dict):
            return cached_payload

    trend_data = writing_stats_service.get_word_count_trend(
        session=session,
        user_id=current_user.id,
        project_id=project_id,
        period=period,
        days=days,
        end_date=end_date,
    )

    total_words_added = sum(int(item.get("words_added", 0)) for item in trend_data)
    total_words_deleted = sum(int(item.get("words_deleted", 0)) for item in trend_data)

    response = WordCountTrendResponse(
        period=period,
        days=days,
        data=trend_data,
        total_words_added=total_words_added,
        total_words_deleted=total_words_deleted,
        total_net_words=total_words_added - total_words_deleted,
    )

    if cache_ttl_seconds > 0:
        dashboard_cache.set_json(
            cache_key,
            response.model_dump(),
            ttl_seconds=cache_ttl_seconds,
        )

    return response


@router.post("/projects/{project_id}/stats/record", response_model=RecordStatsResponse, status_code=201)
def record_project_stats(
    project_id: str,
    request: RecordStatsRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Record daily writing stats for a project.

    This endpoint allows the frontend to record word count changes
    when the user edits files. It tracks:
    - Total word count
    - Words added/deleted in the session
    - Edit time
    - Number of edit sessions

    Recording stats also updates the writing streak for the project.
    """
    # Check project ownership
    verify_project_ownership(project_id, current_user, session)

    log_with_context(
        logger,
        logging.INFO,
        "Recording project stats",
        user_id=current_user.id,
        project_id=project_id,
        word_count=request.word_count,
        words_added=request.words_added,
        words_deleted=request.words_deleted,
        stats_date=request.stats_date,
    )

    effective_stats_date = _parse_iso_date_or_none(request.stats_date, "stats_date") or utcnow().date()
    user_id = current_user.id

    try:
        # Daily activity and its streak are one transaction, including first-row inserts.
        stats = writing_stats_service.record_word_count(
            session=session,
            user_id=user_id,
            project_id=project_id,
            word_count=request.word_count,
            words_added=request.words_added,
            words_deleted=request.words_deleted,
            edit_time_seconds=request.edit_time_seconds,
            stats_date=effective_stats_date,
            commit=False,
        )

        streak_updated = False
        new_streak: int | None = None
        streak_activity_words = request.words_added + request.words_deleted
        if streak_activity_words > 0:
            existing_streak = writing_stats_service.get_or_create_streak(
                session=session, user_id=user_id, project_id=project_id, commit=False,
            )
            previous_streak_count = existing_streak.current_streak
            previous_last_writing_date = existing_streak.last_writing_date
            updated_streak = writing_stats_service.update_streak(
                session=session,
                user_id=user_id,
                project_id=project_id,
                words_written=streak_activity_words,
                stats_date=effective_stats_date,
                commit=False,
            )
            streak_updated = (
                updated_streak.last_writing_date == effective_stats_date
                and (
                    previous_last_writing_date != updated_streak.last_writing_date
                    or previous_streak_count != updated_streak.current_streak
                )
            )
            if streak_updated:
                new_streak = updated_streak.current_streak

        session.flush()
        # Capture while rows are readable: a postcommit refresh failure must not
        # report failure for durable additive activity and invite a double-add retry.
        response = RecordStatsResponse(
            id=stats.id,
            user_id=stats.user_id,
            project_id=stats.project_id,
            stats_date=str(stats.stats_date),
            word_count=stats.word_count,
            words_added=stats.words_added,
            words_deleted=stats.words_deleted,
            edit_sessions=stats.edit_sessions,
            total_edit_time_seconds=stats.total_edit_time_seconds,
            created_at=stats.created_at,
            updated_at=stats.updated_at,
            streak_updated=streak_updated,
            new_streak=new_streak,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise

    # Cache outages must not turn an already committed record into HTTP failure.
    try:
        dashboard_cache.bump_project_version(user_id, project_id)
    except Exception as error:
        log_with_context(
            logger, logging.WARNING, "Failed to invalidate recorded project stats",
            user_id=user_id, project_id=project_id, error=str(error),
        )

    log_with_context(
        logger,
        logging.INFO,
        "Project stats recorded successfully",
        user_id=user_id,
        project_id=project_id,
        stats_id=response.id,
        stats_date=response.stats_date,
    )
    return response
