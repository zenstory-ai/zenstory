"""
Admin Check-in Management API endpoints.

This module contains check-in statistics endpoints for admin operations.
"""
import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, func, select

from config.datetime_utils import beijing_date, utcnow
from database import get_session
from models import User
from models.points import CheckInRecord
from services.core.auth_service import get_current_superuser
from services.features.points_service import effective_check_in_date
from utils.logger import get_logger, log_with_context

from .schemas import (
    CheckInRecordListResponse,
    CheckInRecordResponse,
    CheckInStatsResponse,
)

logger = get_logger(__name__)

router = APIRouter(tags=["admin-checkin"])


# ==================== Check-in Stats ====================


@router.get("/check-in/stats", response_model=CheckInStatsResponse)
def get_check_in_stats(
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    Get check-in statistics.

    Requires superuser privileges.
    """
    today = beijing_date(utcnow())
    yesterday = today - timedelta(days=1)
    week_ago = today - timedelta(days=6)

    # Include the preceding stored date because legacy records used their UTC
    # creation date, which can be one day behind the effective Beijing date.
    recent_records = session.exec(
        select(CheckInRecord)
        .where(CheckInRecord.check_in_date >= week_ago - timedelta(days=1))
        .where(CheckInRecord.check_in_date <= today)
        .order_by(CheckInRecord.created_at.asc(), CheckInRecord.id.asc())
    ).all()
    records_by_day: dict = {}
    for record in recent_records:
        day = effective_check_in_date(record)
        # The old UTC-day policy could award the same user twice inside one
        # Beijing day across UTC midnight. Keep history and points intact, but
        # count the user's latest record only in normalized admin statistics.
        records_by_day.setdefault(day, {})[record.user_id] = record

    today_records = list(records_by_day.get(today, {}).values())
    today_count = len(today_records)
    yesterday_count = len(records_by_day.get(yesterday, {}))
    week_total = sum(
        len(records_by_day.get(week_ago + timedelta(days=offset), {}))
        for offset in range(7)
    )

    # Streak distribution (7, 14, 30 days)
    streak_distribution = {}
    for threshold in [7, 14, 30]:
        count = sum(record.streak_days >= threshold for record in today_records)
        if count > 0:
            streak_distribution[str(threshold)] = count

    log_with_context(
        logger,
        logging.INFO,
        "Retrieved check-in stats",
        user_id=current_user.id,
        today_count=today_count,
        week_total=week_total,
    )

    return CheckInStatsResponse(
        today_count=today_count,
        yesterday_count=yesterday_count,
        week_total=week_total,
        streak_distribution=streak_distribution,
    )


@router.get("/check-in/records", response_model=CheckInRecordListResponse)
def get_check_in_records(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    user_id_filter: str | None = Query(None, alias="user_id", description="Filter by user ID"),
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    Get check-in records with pagination.

    Requires superuser privileges.
    """
    base_query = select(CheckInRecord)

    if user_id_filter:
        base_query = base_query.where(CheckInRecord.user_id == user_id_filter)

    # Get total count
    count_query = select(func.count()).select_from(base_query.subquery())
    total = session.exec(count_query).one()

    # Apply pagination
    query = (
        select(CheckInRecord, User.username)
        .select_from(CheckInRecord)
        .join(User, User.id == CheckInRecord.user_id, isouter=True)
    )
    if user_id_filter:
        query = query.where(CheckInRecord.user_id == user_id_filter)
    query = query.order_by(CheckInRecord.check_in_date.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    records = session.exec(query).all()

    items = []
    for record, username in records:
        items.append(CheckInRecordResponse(
            id=record.id,
            user_id=record.user_id,
            username=username or "Unknown",
            check_in_date=effective_check_in_date(record),
            streak_days=record.streak_days,
            points_earned=record.points_earned,
            created_at=record.created_at,
        ))

    log_with_context(
        logger,
        logging.INFO,
        "Retrieved check-in records",
        user_id=current_user.id,
        count=len(items),
        user_id_filter=user_id_filter,
    )

    return CheckInRecordListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )
