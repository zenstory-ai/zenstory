"""
Shared Pydantic models for Admin API endpoints.

This module contains all request and response schemas used across
the admin API modules.
"""
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StrictInt, StringConstraints, model_validator

from config.datetime_utils import UTCDateTime

# ==================== User Management Schemas ====================


class AdminUserResponse(BaseModel):
    """Administrative account metadata; never serialize credential hashes."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    username: str
    email: str
    email_verified: bool
    avatar_url: str | None = None
    is_active: bool
    is_superuser: bool
    created_at: UTCDateTime
    updated_at: UTCDateTime


class AdminUserListResponse(BaseModel):
    """One page of users plus the total matching the active search filter."""

    items: list[AdminUserResponse]
    total: int


class UserUpdateRequest(BaseModel):
    """Request body for updating a user"""
    username: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=100)] | None = None
    email: EmailStr | None = None
    is_active: bool | None = None
    is_superuser: bool | None = None


# ==================== System Prompt Management Schemas ====================


class SystemPromptConfigRequest(BaseModel):
    """Request body for creating or updating system prompt configuration"""
    role_definition: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    capabilities: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    directory_structure: str | None = None
    content_structure: str | None = None
    file_types: str | None = None
    writing_guidelines: str | None = None
    include_dialogue_guidelines: bool | None = False
    is_active: bool | None = True
    expected_version: int | None = Field(default=None, ge=1)


# ==================== Skill Review Schemas ====================


SkillReviewStatus = Literal["pending", "approved", "rejected", "unpublished"]


class SkillReviewRequest(BaseModel):
    """Request body for rejecting or unpublishing a skill"""
    # 与 PublicSkill.rejection_reason 的 VARCHAR(500) 一致，超长回 422 而不是写库时 500
    rejection_reason: Annotated[
        str, StringConstraints(strip_whitespace=True, max_length=500)
    ] | None = None


class PendingSkillResponse(BaseModel):
    """Response model for a skill review item.

    审核者必须看到会进入其他用户 agent 的全部内容：正文原文、触发词（tags）、
    skill_metadata，以及资源文件清单（原文用 GET /skills/{id}/resources 读取）。
    """
    id: str
    name: str
    description: str | None
    instructions: str
    category: str
    tags: list[str] = Field(default_factory=list)
    skill_metadata: dict = Field(default_factory=dict)
    resource_count: int = 0
    source: str = "community"
    author_id: str | None
    author_name: str | None = None
    status: SkillReviewStatus
    reviewed_by: str | None = None
    reviewer_name: str | None = None
    reviewed_at: UTCDateTime | None = None
    rejection_reason: str | None = None
    created_at: UTCDateTime


class SkillReviewResourceResponse(BaseModel):
    """One resource file of a public skill under review, with its raw content."""
    path: str
    size: int
    content: str


class SkillReviewResourcesResponse(BaseModel):
    """All resource files of a public skill under review."""
    resources: list[SkillReviewResourceResponse]


# ==================== Inspiration Management Schemas ====================


class CreateInspirationRequest(BaseModel):
    """Request body for creating an inspiration from a project"""
    project_id: str
    name: str | None = None
    description: str | None = None
    cover_image: str | None = None
    tags: list[str] | None = None
    source: str = "official"
    is_featured: bool = False


class InspirationReviewRequest(BaseModel):
    """Request body for reviewing an inspiration"""
    approve: bool
    # Required when approve=False
    rejection_reason: str | None = None


class UpdateInspirationRequest(BaseModel):
    """Request body for updating an inspiration"""
    name: str | None = None
    description: str | None = None
    cover_image: str | None = None
    tags: list[str] | None = None
    is_featured: bool | None = None
    sort_order: int | None = None
    status: Literal["pending", "approved", "rejected"] | None = None


# ==================== Feedback Management Schemas ====================


class FeedbackStatusUpdateRequest(BaseModel):
    """Request body for updating feedback status."""

    status: Literal["open", "processing", "resolved"]


# ==================== Subscription Plan Schemas ====================


QuotaLimit = Annotated[StrictInt, Field(ge=-1)]


class SubscriptionFeatures(BaseModel):
    """Supported legacy and catalog feature names, validated before persistence."""

    model_config = ConfigDict(extra="forbid", strict=True)
    ai_conversations_per_day: QuotaLimit | None = None
    context_window_tokens: QuotaLimit | None = None
    file_versions_per_file: QuotaLimit | None = None
    max_projects: QuotaLimit | None = None
    material_uploads: QuotaLimit | None = None
    material_decompositions: QuotaLimit | None = None
    writing_credits_monthly: QuotaLimit | None = None
    agent_runs_monthly: QuotaLimit | None = None
    active_projects_limit: QuotaLimit | None = None
    context_tokens_limit: QuotaLimit | None = None
    material_uploads_monthly: QuotaLimit | None = None
    material_decompositions_monthly: QuotaLimit | None = None
    custom_skills: QuotaLimit | None = None
    custom_skills_limit: QuotaLimit | None = None
    skill_creates_monthly: QuotaLimit | None = None
    inspiration_copies_monthly: QuotaLimit | None = None
    export_formats: list[Literal["txt"]] | None = None
    custom_prompts: bool | None = None
    materials_library_access: bool | None = None
    priority_support: bool | None = None
    priority_queue_level: Literal["standard", "priority"] | None = None


class PlanUpdateRequest(BaseModel):
    """Request body for updating a subscription plan"""
    display_name: str | None = None
    display_name_en: str | None = None
    price_monthly_cents: Annotated[StrictInt, Field(ge=0)] | None = None
    price_yearly_cents: Annotated[StrictInt, Field(ge=0)] | None = None
    features: SubscriptionFeatures | None = None
    is_active: bool | None = None


# ==================== Redemption Code Schemas ====================


CodeType = Literal["single_use", "multi_use", "single", "multi"]
MULTI_USE_CODE_TYPES = frozenset({"multi_use", "multi"})


class _MultiUseNeedsMaxUses(BaseModel):
    """A multi-use code without max_uses would grant a paid tier to unlimited users."""

    code_type: CodeType = "single_use"
    max_uses: int | None = Field(default=None, ge=1, le=100000)

    @model_validator(mode="after")
    def _require_max_uses_for_multi_use(self):
        if self.code_type in MULTI_USE_CODE_TYPES and self.max_uses is None:
            raise ValueError("max_uses is required for multi_use codes")
        return self


class CodeCreateRequest(_MultiUseNeedsMaxUses):
    """Request body for creating a redemption code"""
    tier: str = Field(..., min_length=2, max_length=32)
    duration_days: int = Field(..., ge=1, le=36500)
    notes: str | None = Field(default=None, max_length=500)


class CodeBatchCreateRequest(_MultiUseNeedsMaxUses):
    """Request body for batch creating redemption codes"""
    tier: str = Field(..., min_length=2, max_length=32)
    duration_days: int = Field(..., ge=1, le=36500)
    count: int = Field(..., ge=1, le=100)
    notes: str | None = Field(default=None, max_length=500)


class CodeUpdateRequest(BaseModel):
    """Request body for updating a redemption code"""
    is_active: bool | None = None
    notes: str | None = None


class CodeListResponse(BaseModel):
    """Response model for code list"""
    items: list
    total: int
    page: int
    page_size: int


# ==================== Subscription Management Schemas ====================


class SubscriptionUpdateRequest(BaseModel):
    """Request body for updating a subscription"""
    plan_name: str | None = None
    duration_days: int | None = Field(default=None, ge=1, le=36500)
    status: Literal["active", "expired", "past_due", "cancelled", "canceled"] | None = None


class SubscriptionListResponse(BaseModel):
    """Response model for subscription list"""
    items: list
    total: int
    page: int
    page_size: int


# ==================== Dashboard Schemas ====================


class DashboardStatsResponse(BaseModel):
    """Response model for dashboard statistics"""
    total_users: int
    active_users: int
    new_users_today: int
    total_projects: int
    total_inspirations: int
    pending_inspirations: int
    # Paid (non-free) subscriptions whose period has not ended.
    active_subscriptions: int
    pro_users: int
    # Commercialization stats
    total_points_in_circulation: int
    today_check_ins: int
    active_invite_codes: int
    week_referrals: int


class ActivationFunnelStepResponse(BaseModel):
    """Single activation funnel step."""

    event_name: str
    label: str
    users: int
    conversion_from_previous: float | None = None
    drop_off_from_previous: int | None = None


class ActivationFunnelResponse(BaseModel):
    """Activation funnel response for admin dashboard."""

    window_days: int
    period_start: str
    period_end: str
    steps: list[ActivationFunnelStepResponse]
    activation_rate: float


class UpgradeConversionSourceResponse(BaseModel):
    """Per-source upgrade conversion statistics."""

    source: str
    conversions: int
    share: float


class UpgradeConversionChannelResponse(BaseModel):
    """Conversions grouped by how the plan was granted (payment vs. grants)."""

    channel: str
    conversions: int
    paid: bool


class UpgradeConversionStatsResponse(BaseModel):
    """Upgrade conversion attribution stats for admin dashboard."""

    window_days: int
    period_start: str
    period_end: str
    total_conversions: int
    # Conversions backed by an actual payment (Zpay); excludes admin/points/code grants.
    paid_conversions: int = 0
    unattributed_conversions: int
    sources: list[UpgradeConversionSourceResponse]
    channels: list[UpgradeConversionChannelResponse] = Field(default_factory=list)


class UpgradeFunnelTotalsResponse(BaseModel):
    """Upgrade funnel totals across all sources."""

    expose: int
    click: int
    conversion: int


class UpgradeFunnelSourceResponse(BaseModel):
    """Per-source upgrade funnel stats."""

    source: str
    exposes: int
    clicks: int
    conversions: int
    click_through_rate: float
    conversion_rate_from_click: float
    conversion_rate_from_expose: float


class UpgradeFunnelStatsResponse(BaseModel):
    """Upgrade funnel overview for admin dashboard."""

    window_days: int
    period_start: str
    period_end: str
    totals: UpgradeFunnelTotalsResponse
    sources: list[UpgradeFunnelSourceResponse]


class GrowthGrantChannelResponse(BaseModel):
    """Non-payment plan grants grouped by their recorded source."""

    channel: str
    events: int
    users: int


class GrowthMetricsResponse(BaseModel):
    """Auditable growth metrics for one half-open reporting window."""

    new_users: int
    ai_active_users: int
    cohort_activated_users: int
    cohort_activation_rate: float | None
    paid_orders: int
    revenue_cents: int
    paid_users: int
    cohort_paid_users: int
    signup_to_paid_rate: float | None
    grant_upgrade_events: int
    grant_upgrade_users: int
    grant_channels: list[GrowthGrantChannelResponse] = Field(default_factory=list)


class GrowthDailyResponse(GrowthMetricsResponse):
    """Growth metrics attributed to one Beijing calendar day."""

    date: date


class GrowthPeriodResponse(BaseModel):
    """Metrics and exact UTC bounds for one reporting period."""

    period_start: UTCDateTime
    period_end: UTCDateTime
    metrics: GrowthMetricsResponse


class GrowthDashboardResponse(BaseModel):
    """Current growth window, matched prior window, and current daily trend."""

    days: Literal[7, 14, 30]
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    current: GrowthPeriodResponse
    previous: GrowthPeriodResponse
    daily: list[GrowthDailyResponse]
    definitions: dict[str, str]


# ==================== Audit Log Schemas ====================


class AuditLogResponse(BaseModel):
    id: str
    admin_id: str
    admin_name: str | None
    action: str
    resource_type: str
    resource_id: str | None
    old_value: dict | None
    new_value: dict | None
    ip_address: str | None
    user_agent: str | None
    created_at: UTCDateTime


class AuditLogListResponse(BaseModel):
    """Response model for audit log list"""
    items: list[AuditLogResponse]
    page: int
    page_size: int
    total: int


# ==================== Points Management Schemas ====================


class AdminPointsBalance(BaseModel):
    """User points details for admin"""
    user_id: str
    username: str
    email: str
    available: int
    pending_expiration: int
    total_earned: int
    total_spent: int


class AdminPointsAdjustRequest(BaseModel):
    """Points adjustment request"""
    amount: int  # Positive to add, negative to deduct
    reason: str  # Adjustment reason


class PointsStatsResponse(BaseModel):
    """Points system statistics"""
    total_points_issued: int
    total_points_spent: int
    total_points_expired: int
    active_users_with_points: int


class PointsTransactionListResponse(BaseModel):
    """Response model for points transactions list"""
    items: list
    total: int
    page: int
    page_size: int


# ==================== Check-in Stats Schemas ====================


class CheckInStatsResponse(BaseModel):
    """Check-in statistics"""
    today_count: int
    yesterday_count: int
    week_total: int
    streak_distribution: dict  # {7: 10, 14: 5, 30: 2}


class CheckInRecordResponse(BaseModel):
    """Check-in record response"""
    id: str
    user_id: str
    username: str
    check_in_date: date
    streak_days: int
    points_earned: int
    created_at: UTCDateTime


class CheckInRecordListResponse(BaseModel):
    """Response model for check-in records list"""
    items: list[CheckInRecordResponse]
    total: int
    page: int
    page_size: int


# ==================== Referral Management Schemas ====================


class AdminReferralStatsResponse(BaseModel):
    """Referral system statistics"""
    total_codes: int
    active_codes: int
    total_referrals: int
    successful_referrals: int
    pending_rewards: int
    total_points_awarded: int


class AdminInviteCodeResponse(BaseModel):
    """Invite code details"""
    id: str
    code: str
    owner_id: str
    owner_name: str
    max_uses: int
    current_uses: int
    is_active: bool
    expires_at: UTCDateTime | None
    created_at: UTCDateTime


class InviteCodeListResponse(BaseModel):
    """Response model for invite codes list"""
    items: list[AdminInviteCodeResponse]
    total: int
    page: int
    page_size: int


class ReferralRewardListResponse(BaseModel):
    """Response model for referral rewards list"""
    items: list
    total: int
    page: int
    page_size: int


# ==================== Quota Usage Schemas ====================


class QuotaUsageStatsResponse(BaseModel):
    """Monthly quota counters summed over the current Beijing month (the quota period)."""
    period_start: UTCDateTime
    period_end: UTCDateTime
    material_decompositions: int
    inspiration_copies: int
    # Custom skills created this month (refused creates roll back their bump).
    skills_created: int


class QuotaCounter(BaseModel):
    """Used vs. limit for one quota; limit -1 means unlimited."""
    used: int
    limit: int
    reset_at: UTCDateTime | None = None


class UserQuotaDetail(BaseModel):
    """One user's quota as enforcement sees it."""
    user_id: str
    username: str
    email: str
    plan_name: str
    plan_display_name: str | None = None
    plan_display_name_en: str | None = None
    ai_conversations: QuotaCounter
    material_decompositions: QuotaCounter
    inspiration_copies: QuotaCounter
    custom_skills: QuotaCounter
