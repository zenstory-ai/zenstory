"""
Subscription API - User-facing subscription endpoints.
"""

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, func, select

from config.datetime_utils import utcnow
from database import get_session
from middleware.rate_limit import check_rate_limit
from models import (
    UPGRADE_FUNNEL_ACTION_CLICK,
    UPGRADE_FUNNEL_ACTION_CONVERSION,
    UPGRADE_FUNNEL_ACTION_EXPOSE,
    UPGRADE_FUNNEL_CTAS,
    UPGRADE_FUNNEL_SURFACES,
)
from models.entities import Project, User
from models.subscription import SubscriptionHistory, SubscriptionPlan
from services.core.auth_service import get_current_active_user
from services.features.upgrade_funnel_event_service import upgrade_funnel_event_service
from services.quota_service import quota_service
from services.subscription.defaults import (
    DEFAULT_FREE_PLAN_DISPLAY_NAME,
    DEFAULT_FREE_PLAN_DISPLAY_NAME_EN,
    DEFAULT_FREE_PLAN_FEATURES,
    clone_default_free_features,
    normalize_export_formats,
    resolve_plan_feature,
)
from services.subscription.redemption_service import redemption_service
from services.subscription.subscription_service import subscription_service

router = APIRouter(prefix="/api/v1/subscription", tags=["subscription"])


# ============== Schemas ==============


class SubscriptionStatusResponse(BaseModel):
    tier: str
    status: str
    display_name: str
    display_name_en: str | None = None
    current_period_end: datetime | None = None
    days_remaining: int | None = None
    features: dict


class SubscriptionPlanResponse(BaseModel):
    id: str
    name: str
    display_name: str
    display_name_en: str | None = None
    price_monthly_cents: int
    price_yearly_cents: int
    features: dict
    is_active: bool


class SubscriptionCatalogEntitlementsResponse(BaseModel):
    ai_conversations_per_day: int = 0
    # Deprecated, never advertised: these historical catalog fields were
    # estimates derived from the real daily conversation limit, not runtime
    # quotas. Keep fixed neutral values for cached web bundles only.
    writing_credits_monthly: int = 0
    agent_runs_monthly: int = 0
    active_projects_limit: int = 0
    materials_library_access: bool = False
    material_uploads_monthly: int = 0
    material_decompositions_monthly: int = 0
    custom_skills_limit: int = 0
    inspiration_copies_monthly: int = 0
    export_formats: list[str] = Field(default_factory=list)
    # Deprecated, never advertised: context size and priority queueing are not
    # implemented. Kept as fixed neutral values only so web bundles cached from
    # before 2026-10 (which format these fields unconditionally) do not crash;
    # remove once those bundles are gone.
    context_tokens_limit: int = 0
    priority_queue_level: Literal["standard"] = "standard"


class SubscriptionCatalogPlanResponse(BaseModel):
    id: str
    name: str
    display_name: str
    display_name_en: str | None = None
    price_monthly_cents: int
    price_yearly_cents: int
    recommended: bool = False
    summary_key: str
    target_user_key: str
    entitlements: SubscriptionCatalogEntitlementsResponse


class SubscriptionCatalogResponse(BaseModel):
    version: str
    comparison_mode: str
    pricing_anchor_monthly_cents: int
    tiers: list[SubscriptionCatalogPlanResponse]


class QuotaMetricResponse(BaseModel):
    used: int
    limit: int
    reset_at: datetime | None = None


class QuotaResponse(BaseModel):
    ai_conversations: QuotaMetricResponse
    projects: QuotaMetricResponse
    material_uploads: QuotaMetricResponse
    material_decompositions: QuotaMetricResponse
    skill_creates: QuotaMetricResponse
    inspiration_copies: QuotaMetricResponse


class RedeemCodeRequest(BaseModel):
    code: str = Field(..., pattern=r"^ERG-[A-Z0-9]{2,8}-[A-Z0-9]{4}-[A-Z0-9]{8}$")
    source: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_:-]+$",
    )


class RedeemCodeResponse(BaseModel):
    success: bool
    message: str
    tier: str | None = None
    duration_days: int | None = None


class SubscriptionHistoryItem(BaseModel):
    id: str
    action: str
    plan_name: str
    start_date: datetime
    end_date: datetime | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UpgradeFunnelEventRequest(BaseModel):
    action: Literal[
        UPGRADE_FUNNEL_ACTION_EXPOSE,
        UPGRADE_FUNNEL_ACTION_CLICK,
        UPGRADE_FUNNEL_ACTION_CONVERSION,
    ]
    source: str = Field(
        ...,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_:-]+$",
    )
    surface: Literal["modal", "toast", "page"] = "modal"
    cta: Literal["primary", "secondary", "direct"] | None = None
    destination: str | None = Field(
        default=None,
        max_length=128,
        pattern=r"^[A-Za-z0-9_:-]+$",
    )
    event_name: str | None = Field(default=None, max_length=64)
    meta: dict[str, str | int | float | bool | None] | None = None
    occurred_at: datetime | None = None


class UpgradeFunnelEventResponse(BaseModel):
    success: bool


# ============== Default Free Tier ==============

# Default free tier values used when subscription_plans table is empty or unavailable
DEFAULT_FREE_TIER = {
    "name": "free",
    "display_name": DEFAULT_FREE_PLAN_DISPLAY_NAME,
    "display_name_en": DEFAULT_FREE_PLAN_DISPLAY_NAME_EN,
    "features": clone_default_free_features(),
}


# Only entitlements the backend enforces belong here. Context window and priority
# queue were advertised without an implementation and are intentionally absent.
CATALOG_VERSION = "2026-03"
CATALOG_COMPARISON_MODE = "task_outcome"
CATALOG_PRICING_ANCHOR_MONTHLY_CENTS = 4900
PUBLIC_PLAN_NAMES = ("free", "pro")

PLAN_CATALOG_PRESETS: dict[str, dict[str, Any]] = {
    "free": {
        "recommended": False,
        "summary_key": "starter",
        "target_user_key": "explorer",
    },
    "pro": {
        "recommended": True,
        "summary_key": "creator",
        "target_user_key": "daily_writer",
    },
}


def _normalize_plan_features_for_response(
    features: dict[str, Any] | None,
    plan_name: str | None = "free",
) -> dict[str, Any]:
    normalized = dict(features or {})
    for key, fallback in DEFAULT_FREE_PLAN_FEATURES.items():
        normalized[key] = resolve_plan_feature(plan_name, features, key, fallback)
    normalized["export_formats"] = normalize_export_formats(normalized["export_formats"])
    return normalized


def _normalize_plan_entitlements(plan_name: str, features: dict | None) -> dict[str, Any]:
    normalized_features = features or {}

    def limit(key: str) -> int:
        value = resolve_plan_feature(plan_name, normalized_features, key, 0)
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    raw_materials_access = resolve_plan_feature(plan_name, normalized_features, "materials_library_access", False)
    if isinstance(raw_materials_access, str):
        materials_library_access = raw_materials_access.strip().lower() in {"true", "1", "yes", "on"}
    else:
        materials_library_access = bool(raw_materials_access)

    return {
        "ai_conversations_per_day": limit("ai_conversations_per_day"),
        "writing_credits_monthly": 0,
        "agent_runs_monthly": 0,
        "active_projects_limit": limit("max_projects"),
        "materials_library_access": materials_library_access,
        "material_uploads_monthly": limit("material_uploads"),
        "material_decompositions_monthly": limit("material_decompositions"),
        "custom_skills_limit": limit("custom_skills"),
        "inspiration_copies_monthly": limit("inspiration_copies_monthly"),
        "export_formats": normalize_export_formats(
            resolve_plan_feature(plan_name, normalized_features, "export_formats", [])
        ),
    }


def _build_catalog_plan(plan: SubscriptionPlan) -> SubscriptionCatalogPlanResponse:
    preset = PLAN_CATALOG_PRESETS.get(plan.name, PLAN_CATALOG_PRESETS["free"])
    entitlements = _normalize_plan_entitlements(plan.name, plan.features)

    return SubscriptionCatalogPlanResponse(
        id=plan.id,
        name=plan.name,
        display_name=plan.display_name,
        display_name_en=plan.display_name_en,
        price_monthly_cents=plan.price_monthly_cents,
        price_yearly_cents=plan.price_yearly_cents,
        recommended=bool(preset["recommended"]),
        summary_key=str(preset["summary_key"]),
        target_user_key=str(preset["target_user_key"]),
        entitlements=SubscriptionCatalogEntitlementsResponse(**entitlements),
    )


def _list_active_plans(session: Session) -> list[SubscriptionPlan]:
    return session.exec(
        select(SubscriptionPlan)
        .where(SubscriptionPlan.is_active.is_(True))
        .where(SubscriptionPlan.name.in_(PUBLIC_PLAN_NAMES))
        .order_by(SubscriptionPlan.price_monthly_cents.asc(), SubscriptionPlan.name.asc())
    ).all()


def _build_default_free_plan() -> SubscriptionPlan:
    now = utcnow()
    return SubscriptionPlan(
        id="default-free-plan",
        name=DEFAULT_FREE_TIER["name"],
        display_name=DEFAULT_FREE_TIER["display_name"],
        display_name_en=DEFAULT_FREE_TIER["display_name_en"],
        price_monthly_cents=0,
        price_yearly_cents=0,
        features=clone_default_free_features(),
        is_active=True,
        created_at=now,
        updated_at=now,
    )


def _list_active_plans_or_default_free(session: Session) -> list[SubscriptionPlan]:
    plans = _list_active_plans(session)
    if plans:
        return plans
    return [_build_default_free_plan()]


def _build_plan_response(plan: SubscriptionPlan) -> SubscriptionPlanResponse:
    return SubscriptionPlanResponse(
        id=plan.id,
        name=plan.name,
        display_name=plan.display_name,
        display_name_en=plan.display_name_en,
        price_monthly_cents=plan.price_monthly_cents,
        price_yearly_cents=plan.price_yearly_cents,
        features=_normalize_plan_features_for_response(plan.features, plan.name),
        is_active=plan.is_active,
    )


# ============== Endpoints ==============


@router.get("/me", response_model=SubscriptionStatusResponse)
async def get_subscription_status(
    session: Session = Depends(get_session), current_user: User = Depends(get_current_active_user)
):
    """Get current user's subscription status."""
    subscription = subscription_service.get_user_subscription(session, current_user.id)
    plan = quota_service.get_user_plan(session, current_user.id)

    # Graceful degradation: Use default free tier if plan is not found
    # This handles cases where:
    # 1. No "free" plan exists in subscription_plans table
    # 2. Database query fails silently
    if not plan:
        plan_name = DEFAULT_FREE_TIER["name"]
        display_name = DEFAULT_FREE_TIER["display_name"]
        display_name_en = DEFAULT_FREE_TIER["display_name_en"]
        features = _normalize_plan_features_for_response(clone_default_free_features(), "free")
    else:
        plan_name = plan.name
        display_name = plan.display_name
        display_name_en = plan.display_name_en
        features = _normalize_plan_features_for_response(plan.features, plan.name)

    # A lapsed paid subscription falls back to the free plan; report the plan
    # the user is actually on instead of pairing "Free" with "expired".
    if subscription and (plan is None or plan.id != subscription.plan_id):
        subscription = None
        status_value = "active"
    else:
        status_value = subscription.status if subscription else "none"

    days_remaining = None
    if subscription and subscription.current_period_end:
        period_end = subscription.current_period_end
        if period_end.tzinfo is None:
            period_end = period_end.replace(tzinfo=UTC)
        delta = period_end - utcnow()
        days_remaining = max(0, delta.days)

    return SubscriptionStatusResponse(
        tier=plan_name,
        status=status_value,
        display_name=display_name,
        display_name_en=display_name_en,
        current_period_end=subscription.current_period_end if subscription else None,
        days_remaining=days_remaining,
        features=features,
    )


@router.get("/quota", response_model=QuotaResponse)
async def get_quota(session: Session = Depends(get_session), current_user: User = Depends(get_current_active_user)):
    """Get current usage quota."""
    plan = quota_service.get_user_plan(session, current_user.id)
    quota_snapshot = quota_service.get_quota_snapshot(session, current_user.id, plan=plan)

    # Get project count
    project_count = int(
        session.exec(
            select(func.count())
            .select_from(Project)
            .where(
                Project.owner_id == current_user.id,
                Project.is_deleted.is_(False),
            )
        ).one()
    )

    # Graceful degradation: Use default free tier limit if plan is not found
    project_limit = quota_service.get_plan_feature(plan, "max_projects")

    return QuotaResponse(
        ai_conversations=QuotaMetricResponse(
            used=quota_snapshot["ai_conversations"]["used"],
            limit=quota_snapshot["ai_conversations"]["limit"],
            reset_at=quota_snapshot["ai_conversations"]["reset_at"],
        ),
        projects=QuotaMetricResponse(
            used=project_count,
            limit=project_limit,
            reset_at=None,
        ),
        material_uploads=QuotaMetricResponse(
            used=quota_snapshot["material_uploads"]["used"],
            limit=quota_snapshot["material_uploads"]["limit"],
            reset_at=quota_snapshot["material_uploads"]["reset_at"],
        ),
        material_decompositions=QuotaMetricResponse(
            used=quota_snapshot["material_decompositions"]["used"],
            limit=quota_snapshot["material_decompositions"]["limit"],
            reset_at=quota_snapshot["material_decompositions"]["reset_at"],
        ),
        skill_creates=QuotaMetricResponse(
            used=quota_snapshot["skill_creates"]["used"],
            limit=quota_snapshot["skill_creates"]["limit"],
            reset_at=quota_snapshot["skill_creates"]["reset_at"],
        ),
        inspiration_copies=QuotaMetricResponse(
            used=quota_snapshot["inspiration_copies"]["used"],
            limit=quota_snapshot["inspiration_copies"]["limit"],
            reset_at=quota_snapshot["inspiration_copies"]["reset_at"],
        ),
    )


@router.get("/plans", response_model=list[SubscriptionPlanResponse])
async def list_plans(
    session: Session = Depends(get_session),
):
    """List active subscription plans for pricing and user-side comparison."""
    plans = _list_active_plans_or_default_free(session)
    return [_build_plan_response(plan) for plan in plans]


@router.get("/catalog", response_model=SubscriptionCatalogResponse)
async def get_subscription_catalog(
    session: Session = Depends(get_session),
):
    """
    Get user-facing plan catalog with normalized entitlement metrics.

    This endpoint is designed for pricing/billing UIs to show plan value
    in user-comprehensible units (writing credits, agent runs, active projects).
    """
    plans = _list_active_plans_or_default_free(session)

    tiers = [_build_catalog_plan(plan) for plan in plans]
    tiers.sort(key=lambda tier: (tier.price_monthly_cents, tier.name))

    return SubscriptionCatalogResponse(
        version=CATALOG_VERSION,
        comparison_mode=CATALOG_COMPARISON_MODE,
        pricing_anchor_monthly_cents=CATALOG_PRICING_ANCHOR_MONTHLY_CENTS,
        tiers=tiers,
    )


@router.post("/redeem", response_model=RedeemCodeResponse)
async def redeem_code(
    request: RedeemCodeRequest,
    http_request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    """Redeem a subscription code."""
    allowed, _ = check_rate_limit(http_request, "subscription_redeem_code", 10, 60)
    if not allowed:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Rate limit exceeded")

    attribution_source = request.source.strip() if request.source else None

    success, message, info = redemption_service.redeem_code(
        session, request.code, current_user.id, attribution_source=attribution_source
    )

    if not success:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)

    return RedeemCodeResponse(
        success=True,
        message=message,
        tier=info.get("tier") if info else None,
        duration_days=info.get("duration_days") if info else None,
    )


@router.get("/history", response_model=list[SubscriptionHistoryItem])
async def get_history(
    limit: int = Query(50, ge=1, le=100),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    """Get subscription history."""
    history = session.exec(
        select(SubscriptionHistory)
        .where(SubscriptionHistory.user_id == current_user.id)
        .order_by(SubscriptionHistory.created_at.desc())
        .limit(limit)
    ).all()

    return [
        SubscriptionHistoryItem(
            id=h.id,
            action=h.action,
            plan_name=h.plan_name,
            start_date=h.start_date,
            end_date=h.end_date,
            created_at=h.created_at,
        )
        for h in history
    ]


@router.post("/upgrade-funnel-events", response_model=UpgradeFunnelEventResponse, status_code=201)
async def track_upgrade_funnel_event(
    request: UpgradeFunnelEventRequest,
    http_request: Request,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    """Track one upgrade funnel event for attribution analytics."""
    allowed, _ = check_rate_limit(http_request, "subscription_upgrade_funnel_events", 120, 60)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
        )

    if request.surface not in UPGRADE_FUNNEL_SURFACES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported upgrade funnel surface",
        )

    if request.cta and request.cta not in UPGRADE_FUNNEL_CTAS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported upgrade funnel cta",
        )

    try:
        upgrade_funnel_event_service.record_event(
            session,
            user_id=current_user.id,
            action=request.action,
            source=request.source,
            surface=request.surface,
            cta=request.cta,
            destination=request.destination,
            event_name=request.event_name,
            event_metadata=request.meta,
            occurred_at=request.occurred_at,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return UpgradeFunnelEventResponse(success=True)
