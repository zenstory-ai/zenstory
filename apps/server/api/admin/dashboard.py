"""
Admin Dashboard API endpoints.

This module contains dashboard statistics endpoints for admin operations.
"""
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, func, select

from config.datetime_utils import beijing_date, utcnow
from config.feature_flags import is_inspirations_enabled
from database import get_session
from models import Inspiration, Project, User
from models.points import CheckInRecord, PointsTransaction
from models.referral import InviteCode, Referral
from models.subscription import SubscriptionHistory, SubscriptionPlan, UserSubscription
from services.core.auth_service import get_current_superuser
from services.features.activation_event_service import activation_event_service
from services.features.points_service import effective_check_in_date
from services.features.upgrade_funnel_event_service import upgrade_funnel_event_service
from utils.logger import get_logger, log_with_context

from .schemas import (
    ActivationFunnelResponse,
    DashboardStatsResponse,
    UpgradeConversionStatsResponse,
    UpgradeFunnelStatsResponse,
)

logger = get_logger(__name__)

router = APIRouter(tags=["admin-dashboard"])
CONVERSION_ACTIONS = {"created", "upgraded", "renewed"}
# SubscriptionHistory.event_metadata.source -> conversion channel. Only "zpay" is
# money received; the rest are grants and must not read as paid conversions.
CONVERSION_CHANNELS = {
    "zpay": "zpay",
    "redemption_code": "redemption_code",
    "points_redemption": "points_redemption",
    "admin_update": "admin_update",
}
PAID_CONVERSION_CHANNELS = frozenset({"zpay"})


# ==================== Dashboard Stats ====================


@router.get("/dashboard/stats", response_model=DashboardStatsResponse)
def get_dashboard_stats(
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    Get unified dashboard statistics.

    Requires superuser privileges.
    """
    now = utcnow()
    now_naive = now.replace(tzinfo=None)
    today_utc = now.date()
    today_beijing = beijing_date(now)
    today_start = datetime.combine(today_utc, datetime.min.time())
    week_ago = now_naive - timedelta(days=7)

    # Total users
    total_users = session.exec(select(func.count()).select_from(User)).one()

    # Active users (is_active = True)
    active_users = session.exec(
        select(func.count()).select_from(User).where(User.is_active == True)
    ).one()

    # New users today
    new_users_today = session.exec(
        select(func.count()).select_from(User).where(User.created_at >= today_start)
    ).one()

    # Total projects
    total_projects = session.exec(select(func.count()).select_from(Project)).one()

    total_inspirations = 0
    pending_inspirations = 0
    if is_inspirations_enabled():
        # Inspiration data stays intact while the disabled module is hidden.
        total_inspirations = session.exec(select(func.count()).select_from(Inspiration)).one()
        pending_inspirations = session.exec(
            select(func.count()).select_from(Inspiration).where(Inspiration.status == "pending")
        ).one()

    # Paid subscriptions that are entitled right now. Every user owns a long-lived
    # "free" subscription row and status only flips to expired lazily, so both the
    # free plan and lapsed periods must be excluded explicitly.
    paid_now = (
        select(func.count())
        .select_from(UserSubscription)
        .join(SubscriptionPlan, UserSubscription.plan_id == SubscriptionPlan.id)
        .where(UserSubscription.status == "active")
        .where(UserSubscription.current_period_end > now_naive)
        .where(SubscriptionPlan.name != "free")
    )
    active_subscriptions = session.exec(paid_now).one()
    pro_users = session.exec(paid_now.where(SubscriptionPlan.name == "pro")).one()

    # Commercialization stats
    # Total points in circulation (sum of all non-expired positive transactions minus spent)
    total_earned = session.exec(
        select(func.coalesce(func.sum(PointsTransaction.amount), 0))
        .where(PointsTransaction.is_expired == False)
        .where(PointsTransaction.amount > 0)
    ).one() or 0

    total_spent = session.exec(
        select(func.coalesce(func.sum(PointsTransaction.amount), 0))
        .where(PointsTransaction.amount < 0)
    ).one() or 0

    total_points_in_circulation = max(0, total_earned + total_spent)  # spent is negative

    # Today's check-ins
    check_in_candidates = session.exec(
        select(CheckInRecord).where(
            CheckInRecord.check_in_date.in_(
                (today_beijing, today_beijing - timedelta(days=1))
            )
        )
    ).all()
    today_check_ins = sum(
        effective_check_in_date(record) == today_beijing
        for record in check_in_candidates
    )

    # Active invite codes
    active_invite_codes = session.exec(
        select(func.count()).select_from(InviteCode).where(InviteCode.is_active == True)
    ).one()

    # Week referrals (referrals created in the last 7 days)
    week_referrals = session.exec(
        select(func.count()).select_from(Referral).where(Referral.created_at >= week_ago)
    ).one()

    log_with_context(
        logger,
        logging.INFO,
        "Retrieved dashboard stats",
        user_id=current_user.id,
        total_users=total_users,
        total_projects=total_projects,
    )

    return DashboardStatsResponse(
        total_users=total_users,
        active_users=active_users,
        new_users_today=new_users_today,
        total_projects=total_projects,
        total_inspirations=total_inspirations,
        pending_inspirations=pending_inspirations,
        active_subscriptions=active_subscriptions,
        pro_users=pro_users,
        total_points_in_circulation=total_points_in_circulation,
        today_check_ins=today_check_ins,
        active_invite_codes=active_invite_codes,
        week_referrals=week_referrals,
    )


@router.get("/dashboard/activation-funnel", response_model=ActivationFunnelResponse)
def get_activation_funnel(
    days: int = Query(7, ge=1, le=90),
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """Get activation funnel stats for recent N days."""
    stats = activation_event_service.get_funnel_stats(session, days=days)

    log_with_context(
        logger,
        logging.INFO,
        "Retrieved activation funnel stats",
        user_id=current_user.id,
        window_days=stats["window_days"],
        activation_rate=stats["activation_rate"],
    )

    return ActivationFunnelResponse(**stats)


@router.get("/dashboard/upgrade-conversion", response_model=UpgradeConversionStatsResponse)
def get_upgrade_conversion_stats(
    days: int = Query(7, ge=1, le=90),
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """Get upgrade conversion attribution stats grouped by source."""
    window_days = max(1, min(days, 90))
    period_end = utcnow()
    period_start = (period_end - timedelta(days=window_days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    conversion_rows = session.exec(
        select(SubscriptionHistory).where(
            SubscriptionHistory.created_at >= period_start,
            SubscriptionHistory.created_at <= period_end,
            SubscriptionHistory.action.in_(CONVERSION_ACTIONS),
            SubscriptionHistory.plan_name != "free",
        )
    ).all()

    source_counts: dict[str, int] = {}
    channel_counts: dict[str, int] = {}
    total_conversions = len(conversion_rows)

    for record in conversion_rows:
        metadata = record.event_metadata if isinstance(record.event_metadata, dict) else {}
        channel = CONVERSION_CHANNELS.get(str(metadata.get("source") or ""), "other")
        channel_counts[channel] = channel_counts.get(channel, 0) + 1
        raw_source = metadata.get("upgrade_source")
        if not isinstance(raw_source, str):
            continue

        source = raw_source.strip()
        if not source:
            continue
        source_counts[source] = source_counts.get(source, 0) + 1

    paid_conversions = sum(
        count for channel, count in channel_counts.items() if channel in PAID_CONVERSION_CHANNELS
    )
    channel_stats = sorted(
        [
            {
                "channel": channel,
                "conversions": count,
                "paid": channel in PAID_CONVERSION_CHANNELS,
            }
            for channel, count in channel_counts.items()
        ],
        key=lambda item: (-item["conversions"], item["channel"]),
    )
    attributed_conversions = sum(source_counts.values())
    unattributed_conversions = max(total_conversions - attributed_conversions, 0)
    source_stats = sorted(
        [
            {
                "source": source,
                "conversions": count,
                "share": round(count / total_conversions, 4) if total_conversions > 0 else 0.0,
            }
            for source, count in source_counts.items()
        ],
        key=lambda item: (-item["conversions"], item["source"]),
    )

    log_with_context(
        logger,
        logging.INFO,
        "Retrieved upgrade conversion attribution stats",
        user_id=current_user.id,
        window_days=window_days,
        total_conversions=total_conversions,
        paid_conversions=paid_conversions,
        unattributed_conversions=unattributed_conversions,
    )

    return UpgradeConversionStatsResponse(
        window_days=window_days,
        period_start=period_start.isoformat(),
        period_end=period_end.isoformat(),
        total_conversions=total_conversions,
        paid_conversions=paid_conversions,
        unattributed_conversions=unattributed_conversions,
        sources=source_stats,
        channels=channel_stats,
    )


@router.get("/dashboard/upgrade-funnel", response_model=UpgradeFunnelStatsResponse)
def get_upgrade_funnel_stats(
    days: int = Query(7, ge=1, le=90),
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """Get upgrade funnel expose/click/conversion stats grouped by source."""
    stats = upgrade_funnel_event_service.get_funnel_stats(session, days=days)

    log_with_context(
        logger,
        logging.INFO,
        "Retrieved upgrade funnel stats",
        user_id=current_user.id,
        window_days=stats["window_days"],
        expose_total=stats["totals"]["expose"],
        click_total=stats["totals"]["click"],
        conversion_total=stats["totals"]["conversion"],
    )

    return UpgradeFunnelStatsResponse(**stats)
