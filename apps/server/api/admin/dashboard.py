"""
Admin Dashboard API endpoints.

This module contains dashboard statistics endpoints for admin operations.
"""
import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, case
from sqlmodel import Session, func, select

from config.datetime_utils import beijing_date, beijing_day_bounds, utcnow
from config.feature_flags import is_inspirations_enabled
from database import get_session
from models import Inspiration, Project, UpgradeFunnelEvent, User, WorkspaceOnboarding
from models.points import CheckInRecord
from models.referral import InviteCode, Referral
from models.subscription import SubscriptionHistory, SubscriptionPlan, UserSubscription
from services.core.auth_service import get_current_superuser
from services.features.activation_event_service import activation_event_service
from services.features.points_service import effective_check_in_date, points_service
from services.features.upgrade_funnel_event_service import upgrade_funnel_event_service
from services.infra.admin_analytics_cache import (
    DEFAULT_CACHE_TTL_SECONDS,
    LONG_WINDOW_CACHE_TTL_SECONDS,
    admin_read_statement_timeout,
    cached_admin_analytics,
)
from services.usage.acquisition_service import ACQUISITION_DAYS, get_acquisition_stats
from services.usage.admin_growth_service import get_growth_dashboard
from services.usage.admin_ux_metrics_service import UX_HEALTH_DAYS, get_ux_health_dashboard
from utils.logger import get_logger, log_with_context

from .schemas import (
    AcquisitionStatsResponse,
    ActivationFunnelResponse,
    DashboardStatsResponse,
    GrowthDashboardResponse,
    UpgradeConversionStatsResponse,
    UpgradeFunnelStatsResponse,
    UxHealthDashboardResponse,
)

logger = get_logger(__name__)

router = APIRouter(tags=["admin-dashboard"])
CONVERSION_ACTIONS = {"created", "upgraded", "renewed"}
# SubscriptionHistory.event_metadata.source -> conversion channel. Only payment
# providers ("zpay", "creem") are money received; the rest are grants.
CONVERSION_CHANNELS = {
    "zpay": "zpay",
    "creem": "creem",
    "redemption_code": "redemption_code",
    "points_redemption": "points_redemption",
    "admin_update": "admin_update",
}
PAID_CONVERSION_CHANNELS = frozenset({"zpay", "creem"})
# Upgrade-entry sources shown when the free daily AI message limit is used up
# (the chat card and the quota badge's blocked state).
AI_QUOTA_WALL_SOURCES = ("chat_quota_blocked", "settings_subscription_upgrade:blocked")


# ==================== Dashboard Stats ====================


@router.get("/dashboard/growth", response_model=GrowthDashboardResponse)
def get_dashboard_growth(
    days: int = Query(7),
    refresh: bool = Query(False),
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """Return customer growth, real payments, and grants as separate metrics."""
    if days not in (7, 14, 30):
        raise HTTPException(status_code=422, detail="days must be one of: 7, 14, 30")
    ttl = DEFAULT_CACHE_TTL_SECONDS if days == 7 else LONG_WINDOW_CACHE_TTL_SECONDS
    payload = cached_admin_analytics(
        "dashboard:growth",
        lambda: get_growth_dashboard(session, days=days),
        ttl_seconds=ttl,
        refresh=refresh,
        days=days,
    )
    log_with_context(
        logger,
        logging.INFO,
        "Retrieved growth dashboard",
        user_id=current_user.id,
        window_days=days,
        new_users=payload["current"]["metrics"]["new_users"],
        paid_orders=payload["current"]["metrics"]["paid_orders"],
    )
    return GrowthDashboardResponse(**payload)


@router.get("/dashboard/ux-health", response_model=UxHealthDashboardResponse)
def get_dashboard_ux_health(
    days: int = Query(7),
    refresh: bool = Query(False),
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """Return customer-scoped UX health metrics, prior-period comparison, and daily trend."""
    if days not in UX_HEALTH_DAYS:
        raise HTTPException(status_code=422, detail="days must be one of: 7, 14, 30")
    ttl = DEFAULT_CACHE_TTL_SECONDS if days == 7 else LONG_WINDOW_CACHE_TTL_SECONDS
    payload = cached_admin_analytics(
        "dashboard:ux_health",
        lambda: get_ux_health_dashboard(session, days=days),
        ttl_seconds=ttl,
        refresh=refresh,
        days=days,
    )
    log_with_context(
        logger,
        logging.INFO,
        "Retrieved UX health dashboard",
        user_id=current_user.id,
        window_days=days,
        total_rounds=payload["current"]["metrics"]["total_rounds"],
        feedback_total=payload["current"]["metrics"]["feedback_total"],
    )
    return UxHealthDashboardResponse(**payload)


@router.get("/dashboard/acquisition", response_model=AcquisitionStatsResponse)
def get_dashboard_acquisition(
    days: int = Query(30),
    refresh: bool = Query(False),
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """New accounts of the window by acquisition channel and source, with activation and paid rates."""
    if days not in ACQUISITION_DAYS:
        raise HTTPException(status_code=422, detail="days must be one of: 7, 14, 30, 90")
    ttl = DEFAULT_CACHE_TTL_SECONDS if days <= 14 else LONG_WINDOW_CACHE_TTL_SECONDS
    payload = cached_admin_analytics(
        "dashboard:acquisition",
        lambda: get_acquisition_stats(session, days=days),
        ttl_seconds=ttl,
        refresh=refresh,
        days=days,
    )
    log_with_context(
        logger,
        logging.INFO,
        "Retrieved acquisition stats",
        user_id=current_user.id,
        window_days=days,
        total_signups=payload["total_signups"],
        attributed_signups=payload["attributed_signups"],
    )
    return AcquisitionStatsResponse(**payload)


def _compute_dashboard_stats(
    session: Session,
    *,
    inspirations_enabled: bool,
    include_commercialization: bool,
) -> dict[str, int]:
    now = utcnow()
    now_naive = now.replace(tzinfo=None)
    today_beijing = beijing_date(now)
    # "Today" and "this week" are Beijing calendar days. Bounds are naive UTC,
    # matching how these created_at columns are stored.
    today_start, today_end = (bound.replace(tzinfo=None) for bound in beijing_day_bounds(now))
    week_start = today_start - timedelta(days=6)

    with admin_read_statement_timeout(session):
        # Single scan for total, active, and today's new users.
        user_row = session.exec(
            select(
                func.count(User.id).label("total"),
                func.coalesce(func.sum(case((User.is_active.is_(True), 1), else_=0)), 0).label("active"),
                func.coalesce(
                    func.sum(
                        case(
                            (and_(User.created_at >= today_start, User.created_at < today_end), 1),
                            else_=0,
                        )
                    ),
                    0,
                ).label("new_today"),
            )
        ).one()
        total_users = int(user_row.total or 0)
        active_users = int(user_row.active or 0)
        new_users_today = int(user_row.new_today or 0)

        # Total projects & auto-initialized onboarding projects.
        total_projects, auto_initialized_projects = session.exec(
            select(
                func.count(func.distinct(Project.id)),
                func.count(func.distinct(WorkspaceOnboarding.project_id)),
            )
            .select_from(Project)
            .outerjoin(WorkspaceOnboarding, WorkspaceOnboarding.project_id == Project.id)
        ).one()
        total_projects = int(total_projects or 0)
        auto_initialized_projects = int(auto_initialized_projects or 0)
        author_created_projects = total_projects - auto_initialized_projects

        total_inspirations = 0
        pending_inspirations = 0
        if inspirations_enabled:
            insp_row = session.exec(
                select(
                    func.count(Inspiration.id).label("total"),
                    func.coalesce(
                        func.sum(case((Inspiration.status == "pending", 1), else_=0)),
                        0,
                    ).label("pending"),
                )
            ).one()
            total_inspirations = int(insp_row.total or 0)
            pending_inspirations = int(insp_row.pending or 0)

        # Paid subscriptions that are entitled right now (single query for active + pro).
        sub_row = session.exec(
            select(
                func.count(UserSubscription.id).label("active_subs"),
                func.coalesce(
                    func.sum(case((SubscriptionPlan.name == "pro", 1), else_=0)),
                    0,
                ).label("pro_users"),
            )
            .select_from(UserSubscription)
            .join(SubscriptionPlan, UserSubscription.plan_id == SubscriptionPlan.id)
            .where(UserSubscription.status == "active")
            .where(UserSubscription.current_period_end > now_naive)
            .where(SubscriptionPlan.name != "free")
        ).one()
        active_subscriptions = int(sub_row.active_subs or 0)
        pro_users = int(sub_row.pro_users or 0)

        total_points_in_circulation = 0
        today_check_ins = 0
        active_invite_codes = 0
        week_referrals = 0
        if include_commercialization:
            # Use the same FIFO/expiry rules as each user's spendable balance.
            total_points_in_circulation = points_service.get_total_available_points(session)

            # Today's check-ins
            check_in_candidates = session.exec(
                select(CheckInRecord)
                .where(
                    CheckInRecord.check_in_date.in_(
                        (today_beijing, today_beijing - timedelta(days=1))
                    )
                )
                .order_by(CheckInRecord.created_at.asc(), CheckInRecord.id.asc())
            ).all()
            today_check_ins_by_user = {
                record.user_id: record
                for record in check_in_candidates
                if effective_check_in_date(record) == today_beijing
            }
            today_check_ins = len(today_check_ins_by_user)

            # Active invite codes
            active_invite_codes = int(
                session.exec(
                    select(func.count(InviteCode.id)).where(InviteCode.is_active.is_(True))
                ).one()
                or 0
            )

            # Referrals created in the last 7 Beijing days, today included
            week_referrals = int(
                session.exec(
                    select(func.count(Referral.id)).where(Referral.created_at >= week_start)
                ).one()
                or 0
            )

    return {
        "total_users": total_users,
        "active_users": active_users,
        "new_users_today": new_users_today,
        "total_projects": total_projects,
        "author_created_projects": author_created_projects,
        "auto_initialized_projects": auto_initialized_projects,
        "total_inspirations": total_inspirations,
        "pending_inspirations": pending_inspirations,
        "active_subscriptions": active_subscriptions,
        "pro_users": pro_users,
        "total_points_in_circulation": total_points_in_circulation,
        "today_check_ins": today_check_ins,
        "active_invite_codes": active_invite_codes,
        "week_referrals": week_referrals,
    }


@router.get("/dashboard/stats", response_model=DashboardStatsResponse)
def get_dashboard_stats(
    include_commercialization: bool = Query(True),
    refresh: bool = Query(False),
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    Get unified dashboard statistics.

    Requires superuser privileges.
    """
    inspirations_enabled = is_inspirations_enabled()
    payload = cached_admin_analytics(
        "dashboard:stats",
        lambda: _compute_dashboard_stats(
            session,
            inspirations_enabled=inspirations_enabled,
            include_commercialization=include_commercialization,
        ),
        ttl_seconds=60,
        refresh=refresh,
        inspirations_enabled=inspirations_enabled,
        include_commercialization=include_commercialization,
    )

    log_with_context(
        logger,
        logging.INFO,
        "Retrieved dashboard stats",
        user_id=current_user.id,
        total_users=payload["total_users"],
        total_projects=payload["total_projects"],
    )

    return DashboardStatsResponse(**payload)


@router.get("/dashboard/activation-funnel", response_model=ActivationFunnelResponse)
def get_activation_funnel(
    days: int = Query(7, ge=1, le=90),
    refresh: bool = Query(False),
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """Get activation funnel stats for recent N days."""
    def _compute() -> dict:
        with admin_read_statement_timeout(session):
            return dict(activation_event_service.get_funnel_stats(session, days=days))

    ttl = DEFAULT_CACHE_TTL_SECONDS if days <= 14 else LONG_WINDOW_CACHE_TTL_SECONDS
    stats = cached_admin_analytics(
        "dashboard:activation_funnel",
        _compute,
        ttl_seconds=ttl,
        refresh=refresh,
        days=days,
    )

    log_with_context(
        logger,
        logging.INFO,
        "Retrieved activation funnel stats",
        user_id=current_user.id,
        window_days=stats["window_days"],
        activation_rate=stats["activation_rate"],
    )

    return ActivationFunnelResponse(**stats)


def _compute_upgrade_conversion_stats(session: Session, *, days: int) -> dict:
    period_end = utcnow()
    # Window starts at 00:00 Beijing time, days - 1 days ago.
    period_start = beijing_day_bounds(period_end - timedelta(days=days - 1))[0]

    with admin_read_statement_timeout(session):
        conversion_rows = session.exec(
            select(
                SubscriptionHistory.user_id,
                SubscriptionHistory.action,
                SubscriptionHistory.event_metadata,
                SubscriptionHistory.created_at,
            ).where(
                SubscriptionHistory.created_at >= period_start,
                SubscriptionHistory.created_at <= period_end,
                SubscriptionHistory.action.in_(CONVERSION_ACTIONS),
                SubscriptionHistory.plan_name != "free",
            )
        ).all()

        source_counts: dict[str, int] = {}
        channel_counts: dict[str, int] = {}
        total_conversions = len(conversion_rows)

        for _user_id, _action, raw_metadata, _created_at in conversion_rows:
            metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
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

        # A renewal is not a conversion caused by the wall. Order by the server's
        # created_at: occurred_at can be supplied by the client.
        wall_candidate_rows = [row for row in conversion_rows if row.action != "renewed"]
        converting_user_ids = {row.user_id for row in wall_candidate_rows}
        first_wall_at: dict[str, object] = {}
        if converting_user_ids:
            first_wall_at = dict(
                session.exec(
                    select(UpgradeFunnelEvent.user_id, func.min(UpgradeFunnelEvent.created_at))
                    .where(
                        UpgradeFunnelEvent.user_id.in_(converting_user_ids),
                        UpgradeFunnelEvent.source.in_(AI_QUOTA_WALL_SOURCES),
                    )
                    .group_by(UpgradeFunnelEvent.user_id)
                ).all()
            )

    after_wall_conversions = 0
    paid_after_wall_conversions = 0
    for row in wall_candidate_rows:
        wall_at = first_wall_at.get(row.user_id)
        if wall_at is None or wall_at > row.created_at:
            continue
        after_wall_conversions += 1
        metadata = row.event_metadata if isinstance(row.event_metadata, dict) else {}
        if CONVERSION_CHANNELS.get(str(metadata.get("source") or ""), "other") in PAID_CONVERSION_CHANNELS:
            paid_after_wall_conversions += 1
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
    return {
        "window_days": days,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "total_conversions": total_conversions,
        "paid_conversions": paid_conversions,
        "unattributed_conversions": unattributed_conversions,
        "sources": source_stats,
        "channels": channel_stats,
        "after_ai_quota_wall_conversions": after_wall_conversions,
        "paid_after_ai_quota_wall_conversions": paid_after_wall_conversions,
    }


@router.get("/dashboard/upgrade-conversion", response_model=UpgradeConversionStatsResponse)
def get_upgrade_conversion_stats(
    days: int = Query(7, ge=1, le=90),
    refresh: bool = Query(False),
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """Get upgrade conversion attribution stats grouped by source."""
    ttl = DEFAULT_CACHE_TTL_SECONDS if days <= 14 else LONG_WINDOW_CACHE_TTL_SECONDS
    payload = cached_admin_analytics(
        "dashboard:upgrade_conversion",
        lambda: _compute_upgrade_conversion_stats(session, days=days),
        ttl_seconds=ttl,
        refresh=refresh,
        days=days,
    )

    log_with_context(
        logger,
        logging.INFO,
        "Retrieved upgrade conversion attribution stats",
        user_id=current_user.id,
        window_days=days,
        total_conversions=payload["total_conversions"],
        paid_conversions=payload["paid_conversions"],
        unattributed_conversions=payload["unattributed_conversions"],
    )

    return UpgradeConversionStatsResponse(**payload)


@router.get("/dashboard/upgrade-funnel", response_model=UpgradeFunnelStatsResponse)
def get_upgrade_funnel_stats(
    days: int = Query(7, ge=1, le=90),
    refresh: bool = Query(False),
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """Get upgrade funnel expose/click/conversion stats grouped by source."""
    def _compute() -> dict:
        with admin_read_statement_timeout(session):
            return dict(upgrade_funnel_event_service.get_funnel_stats(session, days=days))

    ttl = DEFAULT_CACHE_TTL_SECONDS if days <= 14 else LONG_WINDOW_CACHE_TTL_SECONDS
    stats = cached_admin_analytics(
        "dashboard:upgrade_funnel",
        _compute,
        ttl_seconds=ttl,
        refresh=refresh,
        days=days,
    )

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
