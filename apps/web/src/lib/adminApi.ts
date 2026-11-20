/**
 * Admin API client for user and prompt management operations.
 *
 * Provides typed API functions for administrative operations including:
 * - User management (list, get, update, delete)
 * - System prompt configuration management
 * - Skill review and approval
 * - Redemption code management
 * - Subscription management
 * - Audit log access
 * - Dashboard statistics
 * - Points and check-in systems
 * - Referral and invite code management
 * - Quota usage monitoring
 */

import {
  api,
  authenticatedFetch,
  readApiError,
} from "./apiClient";
import type {
  AcquisitionStats,
  AuditLog,
  User,
  UserUpdateRequest,
  UsersListResponse,
  SystemPromptConfig,
  PromptConfigRequest,
  PromptReloadResult,
  PromptsListResponse,
  PendingSkill,
  RedemptionCode,
  SkillReviewResource,
  SkillReviewStatus,
  DashboardStats,
  GrowthDashboard,
  ActivationFunnelStats,
  UpgradeConversionStats,
  UpgradeFunnelStats,
  PlanUpdateRequest,
  AdminInspiration,
  AdminFeedbackItem,
  AdminFeedbackListResponse,
  AdminFeedbackStatus,
  PointsStats,
  AdminPointsBalance,
  PointsAdjustRequest,
  PointsTransactionsResponse,
  CheckInStats,
  CheckInRecordsResponse,
  ReferralStats,
  InviteCodesResponse,
  RewardsResponse,
  QuotaUsageStats,
  UserQuotaDetail,
  QuotaCounter,
} from "../types/admin";
import type { SubscriptionFeatures, SubscriptionPlan } from "../types/subscription";
import type { PaymentOrder } from "../types/payment";

export type {
  AuditLog,
  PendingSkill,
  RedemptionCode,
  SkillReviewResource,
  SkillReviewStatus,
} from "../types/admin";

const ADMIN_BASE = "/api/admin";
const FALLBACK_TIMESTAMP = "1970-01-01T00:00:00.000Z";

type UnknownRecord = Record<string, unknown>;

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asRecord(value: unknown): UnknownRecord | null {
  return isRecord(value) ? value : null;
}

function requireRecord(value: unknown, errorMessage: string): UnknownRecord {
  if (!isRecord(value)) {
    throw new Error(errorMessage);
  }
  return value;
}

function requireArray(value: unknown, errorMessage: string): unknown[] {
  if (!Array.isArray(value)) {
    throw new Error(errorMessage);
  }
  return value;
}

function asText(value: unknown, fallback: string = ""): string {
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed.length > 0 ? trimmed : fallback;
  }
  return fallback;
}

function asNullableText(value: unknown): string | null {
  if (typeof value !== "string") {
    return null;
  }
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

function asNumber(value: unknown, fallback: number = 0): number {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim().length > 0) {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) {
      return parsed;
    }
  }
  return fallback;
}

function requireFiniteNumber(value: unknown, errorMessage: string): number {
  const parsed = asNumber(value, Number.NaN);
  if (!Number.isFinite(parsed)) {
    throw new Error(errorMessage);
  }
  return parsed;
}

function requireString(value: unknown, errorMessage: string): string {
  if (typeof value !== "string") {
    throw new Error(errorMessage);
  }
  return value;
}

function requireNonEmptyString(value: unknown, errorMessage: string): string {
  const text = asText(value);
  if (!text) {
    throw new Error(errorMessage);
  }
  return text;
}

function asBoolean(value: unknown, fallback: boolean = false): boolean {
  if (typeof value === "boolean") {
    return value;
  }
  return fallback;
}

function isReservedTestAccount(email: string): boolean {
  return email.toLowerCase().endsWith("@example.com");
}

function asIsoDate(value: unknown, fallback: string = FALLBACK_TIMESTAMP): string {
  const raw = asText(value, "");
  if (!raw) {
    return fallback;
  }

  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) {
    return fallback;
  }

  return raw;
}

function asNullableIsoDate(value: unknown): string | null {
  const raw = asText(value, "");
  if (!raw) {
    return null;
  }

  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) {
    return null;
  }

  return raw;
}

function normalizeInspirationSource(value: string): AdminInspiration["source"] {
  return value === "official" ? "official" : "community";
}

function normalizeInspirationStatus(value: string): AdminInspiration["status"] {
  if (value === "approved" || value === "rejected") {
    return value;
  }
  return "pending";
}

function normalizeSubscriptionPlan(plan: unknown): SubscriptionPlan {
  const raw = requireRecord(plan, "Invalid subscription plan response");
  const rawName = asText(raw.name, "free");
  const featuresRecord = asRecord(raw.features) ?? {};
  const effectiveFeatures = asRecord(raw.effective_features);

  return {
    id: asText(raw.id, rawName),
    name: rawName as SubscriptionPlan["name"],
    display_name: asText(raw.display_name, rawName),
    display_name_en: asNullableText(raw.display_name_en) ?? undefined,
    price_monthly_cents: asNumber(raw.price_monthly_cents, 0),
    price_yearly_cents: asNumber(raw.price_yearly_cents, 0),
    features: featuresRecord as SubscriptionFeatures,
    ...(effectiveFeatures ? { effective_features: effectiveFeatures as SubscriptionFeatures } : {}),
    is_active: asBoolean(raw.is_active, true),
    created_at: asNullableIsoDate(raw.created_at) ?? undefined,
    updated_at: asNullableIsoDate(raw.updated_at) ?? undefined,
  };
}

function normalizeUser(user: unknown): User {
  const raw = requireRecord(user, "Invalid user response");
  const normalized: User = {
    id: asText(raw.id, ""),
    username: asText(raw.username, "-"),
    email: asText(raw.email, "-"),
    email_verified: asBoolean(raw.email_verified, false),
    is_active: asBoolean(raw.is_active, true),
    is_superuser: asBoolean(raw.is_superuser, false),
    created_at: asIsoDate(raw.created_at),
    updated_at: asIsoDate(raw.updated_at),
  };

  const avatarUrl = asNullableText(raw.avatar_url);
  if (avatarUrl) {
    normalized.avatar_url = avatarUrl;
  }

  const nickname = asNullableText(raw.nickname);
  if (nickname) {
    normalized.nickname = nickname;
  }

  return normalized;
}

function normalizePendingSkill(skill: unknown): PendingSkill {
  const raw = requireRecord(skill, "Invalid pending skill response");
  return {
    id: asText(raw.id, ""),
    name: asText(raw.name, "Untitled Skill"),
    description: asNullableText(raw.description),
    instructions: asText(raw.instructions, ""),
    category: asText(raw.category, "general"),
    tags: Array.isArray(raw.tags) ? raw.tags.map((tag) => String(tag)) : [],
    skill_metadata: isRecord(raw.skill_metadata) ? raw.skill_metadata : {},
    resource_count: asNumber(raw.resource_count, 0),
    source: asText(raw.source, "community"),
    author_id: asNullableText(raw.author_id),
    author_name: asNullableText(raw.author_name),
    status: asText(raw.status, "pending") as SkillReviewStatus,
    reviewed_by: asNullableText(raw.reviewed_by),
    reviewer_name: asNullableText(raw.reviewer_name),
    reviewed_at: asNullableText(raw.reviewed_at),
    rejection_reason: asNullableText(raw.rejection_reason),
    created_at: asIsoDate(raw.created_at),
  };
}

function normalizeSubscription(subscription: unknown): Subscription {
  const raw = requireRecord(subscription, "Invalid subscription response");
  const id = asText(raw.id, "");
  const username = asText(raw.username, "-");
  const email = asText(raw.email, "-");
  const planName = asText(raw.plan_name, "free");
  const status = asText(raw.status, "expired");
  const hasRecord =
    typeof raw.has_subscription_record === "boolean"
      ? raw.has_subscription_record
      : !id.startsWith("virtual-");
  const effectivePlanName = asText(raw.effective_plan_name, planName);
  const effectiveStatus = asText(raw.effective_status, status);

  return {
    id,
    user_id: asText(raw.user_id, ""),
    username,
    email,
    plan_name: planName,
    plan_display_name: asNullableText(raw.plan_display_name),
    plan_display_name_en: asNullableText(raw.plan_display_name_en),
    effective_plan_name: effectivePlanName,
    effective_plan_display_name:
      asNullableText(raw.effective_plan_display_name) ?? asNullableText(raw.plan_display_name),
    effective_plan_display_name_en:
      asNullableText(raw.effective_plan_display_name_en) ??
      asNullableText(raw.plan_display_name_en),
    status,
    effective_status: effectiveStatus,
    current_period_start: asNullableIsoDate(raw.current_period_start),
    current_period_end: asNullableIsoDate(raw.current_period_end),
    created_at: asIsoDate(raw.created_at),
    updated_at: asIsoDate(raw.updated_at),
    has_subscription_record: hasRecord,
    is_test_account:
      typeof raw.is_test_account === "boolean"
        ? raw.is_test_account
        : isReservedTestAccount(email),
  };
}

function normalizeInspiration(inspiration: unknown): AdminInspiration {
  const raw = requireRecord(inspiration, "Invalid inspiration response");
  const sourceValue = asText(raw.source, "community");
  const statusValue = asText(raw.status, "pending");
  const tags = Array.isArray(raw.tags)
    ? raw.tags.filter((tag): tag is string => typeof tag === "string")
    : [];

  return {
    id: asText(raw.id, ""),
    name: asText(raw.name, "Untitled Inspiration"),
    description: asNullableText(raw.description),
    cover_image: asNullableText(raw.cover_image),
    tags,
    source: normalizeInspirationSource(sourceValue),
    status: normalizeInspirationStatus(statusValue),
    is_featured: asBoolean(raw.is_featured, false),
    sort_order: asNumber(raw.sort_order, 0),
    copy_count: asNumber(raw.copy_count, 0),
    creator_id: asNullableText(raw.creator_id),
    creator_name: asNullableText(raw.creator_name) ?? undefined,
    reviewer_id: asNullableText(raw.reviewer_id),
    reviewer_name: asNullableText(raw.reviewer_name) ?? undefined,
    reviewed_at: asNullableText(raw.reviewed_at) ?? undefined,
    rejection_reason: asNullableText(raw.rejection_reason) ?? undefined,
    original_project_id: asNullableText(raw.original_project_id),
    created_at: asIsoDate(raw.created_at),
    updated_at: asIsoDate(raw.updated_at),
  };
}

function normalizeFeedbackSourcePage(value: string): AdminFeedbackItem["source_page"] {
  return value === "editor" ? "editor" : "dashboard";
}

function normalizeFeedbackStatus(value: string): AdminFeedbackItem["status"] {
  if (value === "processing" || value === "resolved") {
    return value;
  }
  return "open";
}

function normalizeAdminFeedback(feedback: unknown): AdminFeedbackItem {
  const raw = requireRecord(feedback, "Invalid feedback response");
  return {
    id: asText(raw.id, ""),
    user_id: asText(raw.user_id, ""),
    username: asText(raw.username, "-"),
    email: asText(raw.email, "-"),
    source_page: normalizeFeedbackSourcePage(asText(raw.source_page, "dashboard")),
    source_route: asNullableText(raw.source_route),
    issue_text: asText(raw.issue_text, ""),
    trace_id: asNullableText(raw.trace_id),
    request_id: asNullableText(raw.request_id),
    agent_run_id: asNullableText(raw.agent_run_id),
    project_id: asNullableText(raw.project_id),
    agent_session_id: asNullableText(raw.agent_session_id),
    has_screenshot: asBoolean(raw.has_screenshot, false),
    screenshot_original_name: asNullableText(raw.screenshot_original_name),
    screenshot_content_type: asNullableText(raw.screenshot_content_type),
    screenshot_size_bytes:
      raw.screenshot_size_bytes === null ? null : asNumber(raw.screenshot_size_bytes, 0),
    screenshot_download_url: asNullableText(raw.screenshot_download_url),
    status: normalizeFeedbackStatus(asText(raw.status, "open")),
    created_at: asIsoDate(raw.created_at),
    updated_at: asIsoDate(raw.updated_at),
  };
}

function normalizeSystemPrompt(config: unknown): SystemPromptConfig {
  const raw = requireRecord(config, "Invalid system prompt response");
  return {
    id: asText(raw.id, ""),
    project_type: asText(raw.project_type, "unknown"),
    role_definition: asText(raw.role_definition, ""),
    capabilities: asText(raw.capabilities, ""),
    directory_structure: asText(raw.directory_structure, ""),
    content_structure: asText(raw.content_structure, ""),
    file_types: asText(raw.file_types, ""),
    writing_guidelines: asText(raw.writing_guidelines, ""),
    include_dialogue_guidelines: asBoolean(raw.include_dialogue_guidelines, false),
    primary_content_type: asText(raw.primary_content_type, ""),
    is_active: asBoolean(raw.is_active, true),
    version: asNumber(raw.version, 1),
    created_by: asNullableText(raw.created_by) ?? undefined,
    updated_by: asNullableText(raw.updated_by) ?? undefined,
    created_at: asNullableText(raw.created_at) ?? undefined,
    updated_at: asNullableText(raw.updated_at) ?? undefined,
  };
}

function normalizeRedemptionCode(code: unknown): RedemptionCode {
  const raw = requireRecord(code, "Invalid redemption code response");
  return {
    id: asText(raw.id, ""),
    code: asText(raw.code, ""),
    tier: asText(raw.tier, "free"),
    duration_days: asNumber(raw.duration_days, 0),
    code_type: asText(raw.code_type, "single_use"),
    max_uses: raw.max_uses === null ? null : asNumber(raw.max_uses, 1),
    current_uses: asNumber(raw.current_uses, 0),
    is_active: asBoolean(raw.is_active, false),
    notes: asNullableText(raw.notes),
    created_at: asIsoDate(raw.created_at),
    updated_at: asIsoDate(raw.updated_at),
  };
}

function normalizeAuditLog(log: unknown): AuditLog {
  const raw = requireRecord(log, "Invalid audit log response");
  if (
    !asText(raw.id) ||
    !asText(raw.admin_id) ||
    (typeof raw.admin_name !== "string" && raw.admin_name !== null) ||
    !asText(raw.action) ||
    !asText(raw.resource_type) ||
    !Number.isFinite(Date.parse(asText(raw.created_at)))
  ) {
    throw new Error("Invalid audit log response");
  }
  const oldValue = asRecord(raw.old_value);
  const newValue = asRecord(raw.new_value);

  return {
    id: asText(raw.id, ""),
    admin_id: asText(raw.admin_id, ""),
    admin_name: asText(raw.admin_name, "-"),
    action: asText(raw.action, ""),
    resource_type: asText(raw.resource_type, ""),
    resource_id: asText(raw.resource_id, ""),
    old_value: oldValue,
    new_value: newValue,
    ip_address: asNullableText(raw.ip_address),
    user_agent: asNullableText(raw.user_agent),
    created_at: asIsoDate(raw.created_at),
  };
}

function normalizePointsStats(stats: unknown): PointsStats {
  const errorMessage = "Invalid points stats response";
  const raw = requireRecord(stats, errorMessage);
  return {
    total_points_issued: requireFiniteNumber(raw.total_points_issued, errorMessage),
    total_points_spent: requireFiniteNumber(raw.total_points_spent, errorMessage),
    total_points_expired: requireFiniteNumber(raw.total_points_expired, errorMessage),
    active_users_with_points: requireFiniteNumber(raw.active_users_with_points, errorMessage),
  };
}

function normalizeUserPoints(balance: unknown): AdminPointsBalance {
  const errorMessage = "Invalid user points response";
  const raw = requireRecord(balance, errorMessage);
  return {
    user_id: requireNonEmptyString(raw.user_id, errorMessage),
    username: asText(raw.username, "-"),
    email: asText(raw.email, "-"),
    available: requireFiniteNumber(raw.available, errorMessage),
    pending_expiration: requireFiniteNumber(raw.pending_expiration, errorMessage),
    total_earned: requireFiniteNumber(raw.total_earned, errorMessage),
    total_spent: requireFiniteNumber(raw.total_spent, errorMessage),
  };
}

function normalizePointsTransaction(
  transaction: unknown
): PointsTransactionsResponse["items"][number] {
  const raw = requireRecord(transaction, "Invalid points transaction response");
  return {
    id: asText(raw.id, ""),
    user_id: asText(raw.user_id, ""),
    username: asText(raw.username, "-"),
    amount: asNumber(raw.amount, 0),
    balance_after: asNumber(raw.balance_after, 0),
    transaction_type: asText(raw.transaction_type, ""),
    source_id: asNullableText(raw.source_id),
    description: asNullableText(raw.description),
    expires_at: asNullableText(raw.expires_at),
    is_expired: asBoolean(raw.is_expired, false),
    created_at: asIsoDate(raw.created_at),
  };
}

function normalizeCheckInStats(stats: unknown): CheckInStats {
  const errorMessage = "Invalid check-in stats response";
  const raw = requireRecord(stats, errorMessage);
  const rawDistribution = requireRecord(raw.streak_distribution, errorMessage);
  const distribution = Object.entries(rawDistribution).reduce<Record<number, number>>(
    (acc, [key, value]) => {
      const numericKey = Number(key);
      if (!Number.isFinite(numericKey)) {
        return acc;
      }
      acc[numericKey] = requireFiniteNumber(value, errorMessage);
      return acc;
    },
    {}
  );

  return {
    today_count: requireFiniteNumber(raw.today_count, errorMessage),
    yesterday_count: requireFiniteNumber(raw.yesterday_count, errorMessage),
    week_total: requireFiniteNumber(raw.week_total, errorMessage),
    streak_distribution: distribution,
  };
}

function normalizeCheckInRecord(record: unknown): CheckInRecordsResponse["items"][number] {
  const raw = requireRecord(record, "Invalid check-in record response");
  return {
    id: asText(raw.id, ""),
    user_id: asText(raw.user_id, ""),
    username: asText(raw.username, "-"),
    check_in_date: asText(raw.check_in_date, ""),
    streak_days: asNumber(raw.streak_days, 0),
    points_earned: asNumber(raw.points_earned, 0),
    created_at: asIsoDate(raw.created_at, ""),
  };
}

function normalizeReferralStats(stats: unknown): ReferralStats {
  const errorMessage = "Invalid referral stats response";
  const raw = requireRecord(stats, errorMessage);
  return {
    total_codes: requireFiniteNumber(raw.total_codes, errorMessage),
    active_codes: requireFiniteNumber(raw.active_codes, errorMessage),
    total_referrals: requireFiniteNumber(raw.total_referrals, errorMessage),
    successful_referrals: requireFiniteNumber(raw.successful_referrals, errorMessage),
    pending_rewards: requireFiniteNumber(raw.pending_rewards, errorMessage),
    total_points_awarded: requireFiniteNumber(raw.total_points_awarded, errorMessage),
  };
}

function normalizeInviteCode(code: unknown): InviteCodesResponse["items"][number] {
  const raw = requireRecord(code, "Invalid invite code response");
  return {
    id: asText(raw.id, ""),
    code: asText(raw.code, ""),
    owner_id: asText(raw.owner_id, ""),
    owner_name: asText(raw.owner_name, "-"),
    max_uses: asNumber(raw.max_uses, 0),
    current_uses: asNumber(raw.current_uses, 0),
    is_active: asBoolean(raw.is_active, false),
    expires_at: asNullableText(raw.expires_at),
    created_at: asIsoDate(raw.created_at),
  };
}

function normalizeRewardRecord(reward: unknown): RewardsResponse["items"][number] {
  const raw = requireRecord(reward, "Invalid reward record response");
  return {
    id: asText(raw.id, ""),
    user_id: asText(raw.user_id, ""),
    username: asText(raw.username, "-"),
    reward_type: asText(raw.reward_type, ""),
    amount: asNumber(raw.amount, 0),
    source: asText(raw.source, ""),
    is_used: asBoolean(raw.is_used, false),
    expires_at: asNullableText(raw.expires_at),
    created_at: asIsoDate(raw.created_at),
  };
}

function normalizeQuotaUsageStats(stats: unknown): QuotaUsageStats {
  const errorMessage = "Invalid quota usage stats response";
  const raw = requireRecord(stats, errorMessage);
  return {
    period_start: requireNonEmptyString(raw.period_start, errorMessage),
    period_end: requireNonEmptyString(raw.period_end, errorMessage),
    material_decompositions: requireFiniteNumber(raw.material_decompositions, errorMessage),
    inspiration_copies: requireFiniteNumber(raw.inspiration_copies, errorMessage),
    skills_created: requireFiniteNumber(raw.skills_created, errorMessage),
  };
}

function normalizeQuotaCounter(counter: unknown, errorMessage: string): QuotaCounter {
  const raw = requireRecord(counter, errorMessage);
  return {
    used: requireFiniteNumber(raw.used, errorMessage),
    limit: requireFiniteNumber(raw.limit, errorMessage),
    reset_at: asNullableText(raw.reset_at),
  };
}

function normalizeUserQuota(detail: unknown): UserQuotaDetail {
  const errorMessage = "Invalid user quota response";
  const raw = requireRecord(detail, errorMessage);
  return {
    user_id: requireNonEmptyString(raw.user_id, errorMessage),
    username: asText(raw.username, "-"),
    email: requireString(raw.email, errorMessage),
    plan_name: requireNonEmptyString(raw.plan_name, errorMessage),
    plan_display_name: asNullableText(raw.plan_display_name),
    plan_display_name_en: asNullableText(raw.plan_display_name_en),
    ai_conversations: normalizeQuotaCounter(raw.ai_conversations, errorMessage),
    material_decompositions: normalizeQuotaCounter(raw.material_decompositions, errorMessage),
    inspiration_copies: normalizeQuotaCounter(raw.inspiration_copies, errorMessage),
    custom_skills: normalizeQuotaCounter(raw.custom_skills, errorMessage),
  };
}

// ==================== 用户管理 API ====================

async function getUsers(
  skip: number = 0,
  limit: number = 20,
  search?: string
): Promise<UsersListResponse> {
  const params = new URLSearchParams({
    skip: skip.toString(),
    limit: limit.toString(),
  });

  if (search) {
    params.append("search", search);
  }

  const payload = await api.get<unknown>(`${ADMIN_BASE}/users?${params.toString()}`);
  const payloadRecord = requireRecord(payload, "Invalid users list response");
  const items = requireArray(payloadRecord.items, "Invalid users list response");
  const total = requireFiniteNumber(payloadRecord.total, "Invalid users list response");
  return { users: items.map(normalizeUser), total };
}

async function getUser(id: string): Promise<User> {
  const payload = await api.get<unknown>(`${ADMIN_BASE}/users/${encodeURIComponent(id)}`);
  return normalizeUser(payload);
}

async function updateUser(id: string, data: UserUpdateRequest): Promise<User> {
  return api.put<User>(`${ADMIN_BASE}/users/${id}`, data);
}

async function deleteUser(id: string): Promise<User | { message: string }> {
  return api.delete<User | { message: string }>(`${ADMIN_BASE}/users/${id}`);
}

// ==================== Prompt 管理 API ====================

async function getPrompts(): Promise<PromptsListResponse> {
  const payload = await api.get<unknown>(`${ADMIN_BASE}/prompts`);
  return requireArray(payload, "Invalid prompts response").map(normalizeSystemPrompt);
}

async function getPrompt(projectType: string): Promise<SystemPromptConfig> {
  return api.get<SystemPromptConfig>(
    `${ADMIN_BASE}/prompts/${encodeURIComponent(projectType)}`
  );
}

async function upsertPrompt(
  projectType: string,
  data: PromptConfigRequest
): Promise<SystemPromptConfig> {
  return api.put<SystemPromptConfig>(
    `${ADMIN_BASE}/prompts/${encodeURIComponent(projectType)}`,
    data
  );
}

async function deletePrompt(projectType: string): Promise<{ message: string }> {
  return api.delete<{ message: string }>(
    `${ADMIN_BASE}/prompts/${encodeURIComponent(projectType)}`
  );
}

async function reloadPrompts(): Promise<PromptReloadResult> {
  return api.post<PromptReloadResult>(`${ADMIN_BASE}/prompts/reload`);
}

// ==================== 技能审核 API ====================

async function getPendingSkills(status: SkillReviewStatus = "pending"): Promise<PendingSkill[]> {
  const payload = await api.get<unknown>(
    `${ADMIN_BASE}/skills/pending?status=${encodeURIComponent(status)}`
  );
  return requireArray(payload, "Invalid pending skills response").map(normalizePendingSkill);
}

async function approveSkill(id: string): Promise<{ message: string; skill_id: string }> {
  return api.post<{ message: string; skill_id: string }>(`${ADMIN_BASE}/skills/${id}/approve`);
}

async function rejectSkill(
  id: string,
  reason?: string
): Promise<{ message: string; skill_id: string }> {
  return api.post<{ message: string; skill_id: string }>(
    `${ADMIN_BASE}/skills/${id}/reject`,
    { rejection_reason: reason }
  );
}

async function unpublishSkill(
  id: string,
  reason?: string
): Promise<{ message: string; skill_id: string }> {
  return api.post<{ message: string; skill_id: string }>(
    `${ADMIN_BASE}/skills/${id}/unpublish`,
    { rejection_reason: reason }
  );
}

async function getSkillReviewResources(id: string): Promise<SkillReviewResource[]> {
  const payload = await api.get<{ resources?: SkillReviewResource[] }>(
    `${ADMIN_BASE}/skills/${id}/resources`
  );
  return Array.isArray(payload?.resources) ? payload.resources : [];
}

// ==================== 兑换码管理 API ====================

/** Paginated response for redemption code listings. */
export interface CodesListResponse {
  items: RedemptionCode[];
  total: number;
  page: number;
  page_size: number;
}

async function getCodes(params?: {
  page?: number;
  page_size?: number;
  tier?: string;
  is_active?: boolean;
}): Promise<CodesListResponse> {
  const searchParams = new URLSearchParams();
  if (params?.page) searchParams.set("page", String(params.page));
  if (params?.page_size) searchParams.set("page_size", String(params.page_size));
  if (params?.tier) searchParams.set("tier", params.tier);
  if (params?.is_active !== undefined) searchParams.set("is_active", String(params.is_active));

  const payload = await api.get<unknown>(`${ADMIN_BASE}/codes?${searchParams.toString()}`);
  const payloadRecord = requireRecord(payload, "Invalid codes response");
  const items = requireArray(payloadRecord.items, "Invalid codes response").map(
    normalizeRedemptionCode
  );

  return {
    items,
    total: requireFiniteNumber(payloadRecord.total, "Invalid codes response"),
    page: requireFiniteNumber(payloadRecord.page, "Invalid codes response"),
    page_size: requireFiniteNumber(payloadRecord.page_size, "Invalid codes response"),
  };
}

async function createCode(data: {
  tier: string;
  duration_days: number;
  code_type?: "single_use" | "multi_use" | "single" | "multi";
  max_uses?: number;
  notes?: string;
}): Promise<RedemptionCode> {
  return api.post<RedemptionCode>(`${ADMIN_BASE}/codes`, data);
}

export interface CodeBatchCreateResponse {
  codes: string[];
  count?: number;
  created?: number;
  code_type?: "single_use" | "multi_use";
  max_uses?: number;
}

async function createCodesBatch(data: {
  tier: string;
  duration_days: number;
  count: number;
  code_type?: "single_use" | "multi_use" | "single" | "multi";
  max_uses?: number;
  notes?: string;
}): Promise<CodeBatchCreateResponse> {
  return api.post<CodeBatchCreateResponse>(`${ADMIN_BASE}/codes/batch`, data);
}

async function updateCode(
  id: string,
  data: { is_active?: boolean; notes?: string }
): Promise<RedemptionCode> {
  return api.put<RedemptionCode>(`${ADMIN_BASE}/codes/${id}`, data);
}

// ==================== 订阅管理 API ====================

/** Represents a user subscription with plan and billing details. */
export interface Subscription {
  id: string;
  user_id: string;
  username: string;
  email: string;
  plan_name: string;
  plan_display_name?: string | null;
  plan_display_name_en?: string | null;
  effective_plan_name?: string;
  effective_plan_display_name?: string | null;
  effective_plan_display_name_en?: string | null;
  status: string;
  effective_status?: string;
  current_period_start: string | null;
  current_period_end: string | null;
  created_at: string;
  updated_at: string;
  has_subscription_record?: boolean;
  is_test_account?: boolean;
}

export interface UserSubscriptionDetailResponse {
  subscription: Subscription;
  plan: SubscriptionPlan | null;
  quota: Record<string, unknown> | null;
}

export interface UpdateSubscriptionResponse {
  success: boolean;
}

/** Paginated response for subscription listings. */
export interface SubscriptionsListResponse {
  items: Subscription[];
  total: number;
  page: number;
  page_size: number;
}

async function getSubscriptions(params?: {
  page?: number;
  page_size?: number;
  status?: string;
  search?: string;
}): Promise<SubscriptionsListResponse> {
  const searchParams = new URLSearchParams();
  if (params?.page) searchParams.set("page", String(params.page));
  if (params?.page_size) searchParams.set("page_size", String(params.page_size));
  if (params?.status) searchParams.set("status", params.status);
  if (params?.search) searchParams.set("search", params.search);

  const payload = await api.get<unknown>(
    `${ADMIN_BASE}/subscriptions?${searchParams.toString()}`
  );
  const payloadRecord = requireRecord(payload, "Invalid subscriptions response");
  const items = requireArray(payloadRecord.items, "Invalid subscriptions response").map(
    normalizeSubscription
  );

  return {
    items,
    total: requireFiniteNumber(payloadRecord.total, "Invalid subscriptions response"),
    page: asNumber(payloadRecord.page, params?.page ?? 1),
    page_size: asNumber(payloadRecord.page_size, (params?.page_size ?? items.length) || 20),
  };
}

export interface AdminPaymentOrder extends PaymentOrder {
  username: string;
  email: string;
}

export interface PaymentOrdersListResponse {
  items: AdminPaymentOrder[];
  total: number;
  page: number;
  page_size: number;
  needs_attention_total?: number;
}

export type PaymentOrderSyncOutcome = "already_fulfilled" | "fulfilled" | "unpaid" | "not_found";

export interface PaymentOrderSyncResponse {
  outcome: PaymentOrderSyncOutcome;
  order: AdminPaymentOrder;
}

async function getPaymentOrders(params: {
  page?: number;
  page_size?: number;
  status?: "pending" | "paid";
  fulfillment_status?: "pending" | "succeeded" | "failed";
  needs_attention?: boolean;
  search?: string;
  user_id?: string;
} = {}): Promise<PaymentOrdersListResponse> {
  const query = new URLSearchParams();
  if (params.page) query.set("page", String(params.page));
  if (params.page_size) query.set("page_size", String(params.page_size));
  if (params.status) query.set("status", params.status);
  if (params.fulfillment_status) query.set("fulfillment_status", params.fulfillment_status);
  if (params.needs_attention) query.set("needs_attention", "true");
  if (params.search) query.set("search", params.search);
  if (params.user_id) query.set("user_id", params.user_id);
  return api.get<PaymentOrdersListResponse>(`${ADMIN_BASE}/payment-orders?${query.toString()}`);
}

async function syncPaymentOrder(orderId: string): Promise<PaymentOrderSyncResponse> {
  return api.post<PaymentOrderSyncResponse>(
    `${ADMIN_BASE}/payment-orders/${encodeURIComponent(orderId)}/sync`
  );
}

async function getUserSubscription(userId: string): Promise<UserSubscriptionDetailResponse> {
  return api.get<UserSubscriptionDetailResponse>(`${ADMIN_BASE}/subscriptions/${userId}`);
}

async function updateUserSubscription(
  userId: string,
  data: { plan_name?: string; duration_days?: number; status?: string }
): Promise<UpdateSubscriptionResponse> {
  return api.put<UpdateSubscriptionResponse>(`${ADMIN_BASE}/subscriptions/${userId}`, data);
}

// ==================== 用量与成本 API ====================

export type UsageWindow = "today" | "yesterday" | "7d";
export type UsageUserSort = "cost" | "calls" | "tokens";
export type UsageDetailDays = 7 | 14 | 30;
export type UsagePriceKind = "cache_hit" | "cache_miss" | "output";

export interface UsageMetrics {
  calls: number;
  cache_hit_tokens: number;
  cache_miss_tokens: number;
  output_tokens: number;
  cost_cny: string;
  peak_cost_cny: string;
  offpeak_cost_cny: string;
}

export interface UsageSourceRow extends UsageMetrics {
  source: string;
}

export interface UsageDailyRow extends UsageMetrics {
  date: string;
  users?: number;
}

export interface UsagePeriod {
  timezone: string;
  period_start: string;
  period_end: string;
  pricing_version: string;
  prices: Record<"peak" | "offpeak", Record<UsagePriceKind, string>>;
}

export interface UsageSummaryResponse extends UsagePeriod {
  window: UsageWindow;
  totals: UsageMetrics & { users: number };
  by_source: UsageSourceRow[];
  daily: UsageDailyRow[];
}

export interface UserUsageRow extends UsageMetrics {
  user_id: string;
  username: string;
  email: string;
  last_used_at: string | null;
}

export interface UserUsageListResponse extends UsagePeriod {
  window: UsageWindow;
  items: UserUsageRow[];
  total: number;
  page: number;
  page_size: number;
}

export interface UserDailyUsageResponse extends UsagePeriod {
  user_id: string;
  username: string;
  email: string;
  days: UsageDetailDays;
  totals: UsageMetrics;
  by_source: UsageSourceRow[];
  daily: UsageDailyRow[];
}

export type UsageSource = "agent" | "router" | "suggest" | "polish" | "material";

export interface UsageDistribution {
  p50: number | null;
  p95: number | null;
  samples: number;
}

export interface ModelMetrics {
  attempts: number;
  success: number;
  error: number;
  timeout: number;
  cancelled: number;
  not_recorded: number;
  success_rate: number | null;
  latency_ms: UsageDistribution;
  ttft_ms: UsageDistribution;
  decode_tps: { value: number | null; samples: number };
  cache_hit_tokens: number;
  cache_miss_tokens: number;
  output_tokens: number;
  cost_cny: string;
}

export interface ModelMetricsRow extends ModelMetrics {
  model: string;
}

export interface ModelMetricsResponse extends UsagePeriod {
  window: UsageWindow;
  source: UsageSource | null;
  items: ModelMetricsRow[];
  totals: ModelMetrics;
}

async function getUsageSummary(window: UsageWindow): Promise<UsageSummaryResponse> {
  return api.get<UsageSummaryResponse>(`${ADMIN_BASE}/usage/summary?window=${window}`);
}

async function getUsageByUser(params: {
  window: UsageWindow;
  sort?: UsageUserSort;
  search?: string;
  page?: number;
  page_size?: number;
}): Promise<UserUsageListResponse> {
  const query = new URLSearchParams({ window: params.window });
  if (params.sort) query.set("sort", params.sort);
  if (params.search) query.set("search", params.search);
  if (params.page) query.set("page", String(params.page));
  if (params.page_size) query.set("page_size", String(params.page_size));
  return api.get<UserUsageListResponse>(`${ADMIN_BASE}/usage/users?${query.toString()}`);
}

async function getUsageByModel(
  window: UsageWindow,
  source?: UsageSource
): Promise<ModelMetricsResponse> {
  const query = new URLSearchParams({ window });
  if (source) query.set("source", source);
  return api.get<ModelMetricsResponse>(`${ADMIN_BASE}/usage/models?${query.toString()}`);
}

async function getUserDailyUsage(
  userId: string,
  days: UsageDetailDays = 7
): Promise<UserDailyUsageResponse> {
  return api.get<UserDailyUsageResponse>(
    `${ADMIN_BASE}/usage/users/${encodeURIComponent(userId)}/daily?days=${days}`
  );
}

// ==================== 审计日志 API ====================

/** Paginated response for audit log listings. */
export interface AuditLogsResponse {
  items: AuditLog[];
  total: number;
  page: number;
  page_size: number;
}

async function getAuditLogs(params?: {
  page?: number;
  page_size?: number;
  resource_type?: string;
  action?: string;
}): Promise<AuditLogsResponse> {
  const searchParams = new URLSearchParams();
  if (params?.page) searchParams.set("page", String(params.page));
  if (params?.page_size) searchParams.set("page_size", String(params.page_size));
  if (params?.resource_type) searchParams.set("resource_type", params.resource_type);
  if (params?.action) searchParams.set("action", params.action);

  const payload = await api.get<unknown>(`${ADMIN_BASE}/audit-logs?${searchParams.toString()}`);
  const payloadRecord = requireRecord(payload, "Invalid audit log response");
  const rawItems = payloadRecord.items;
  const total = asNumber(payloadRecord.total, Number.NaN);
  const page = asNumber(payloadRecord.page, Number.NaN);
  const pageSize = asNumber(payloadRecord.page_size, Number.NaN);
  if (
    !Array.isArray(rawItems) ||
    !Number.isInteger(total) ||
    total < 0 ||
    !Number.isInteger(page) ||
    page < 1 ||
    !Number.isInteger(pageSize) ||
    pageSize < 1
  ) {
    throw new Error("Invalid audit log response");
  }
  const items = rawItems.map(normalizeAuditLog);

  return {
    items,
    total,
    page,
    page_size: pageSize,
  };
}

// ==================== Dashboard Stats API ====================

async function getGrowthDashboard(days: 7 | 14 | 30 = 7): Promise<GrowthDashboard> {
  return api.get<GrowthDashboard>(`${ADMIN_BASE}/dashboard/growth?days=${days}`);
}

async function getAcquisitionStats(days: 7 | 14 | 30 | 90 = 30): Promise<AcquisitionStats> {
  return api.get<AcquisitionStats>(`${ADMIN_BASE}/dashboard/acquisition?days=${days}`);
}

async function getDashboardStats(): Promise<DashboardStats> {
  const errorMessage = "Invalid dashboard stats response";
  const payload = await api.get<unknown>(`${ADMIN_BASE}/dashboard/stats`);
  const raw = requireRecord(payload, errorMessage);

  return {
    total_users: requireFiniteNumber(raw.total_users, errorMessage),
    active_users: requireFiniteNumber(raw.active_users, errorMessage),
    new_users_today: requireFiniteNumber(raw.new_users_today, errorMessage),
    total_projects: requireFiniteNumber(raw.total_projects, errorMessage),
    author_created_projects: requireFiniteNumber(raw.author_created_projects, errorMessage),
    auto_initialized_projects: requireFiniteNumber(raw.auto_initialized_projects, errorMessage),
    total_inspirations: requireFiniteNumber(raw.total_inspirations, errorMessage),
    pending_inspirations: requireFiniteNumber(raw.pending_inspirations, errorMessage),
    active_subscriptions: requireFiniteNumber(raw.active_subscriptions, errorMessage),
    pro_users: requireFiniteNumber(raw.pro_users, errorMessage),
    total_points_in_circulation: requireFiniteNumber(
      raw.total_points_in_circulation,
      errorMessage
    ),
    today_check_ins: requireFiniteNumber(raw.today_check_ins, errorMessage),
    active_invite_codes: requireFiniteNumber(raw.active_invite_codes, errorMessage),
    week_referrals: requireFiniteNumber(raw.week_referrals, errorMessage),
  };
}

async function getActivationFunnel(days = 7): Promise<ActivationFunnelStats> {
  const errorMessage = "Invalid activation funnel response";
  const safeDays = Number.isFinite(days) ? Math.min(Math.max(Math.floor(days), 1), 90) : 7;
  const payload = await api.get<unknown>(
    `${ADMIN_BASE}/dashboard/activation-funnel?days=${safeDays}`
  );
  const raw = requireRecord(payload, errorMessage);
  const rawSteps = requireArray(raw.steps, errorMessage);

  return {
    window_days: requireFiniteNumber(raw.window_days, errorMessage),
    period_start: requireString(raw.period_start, errorMessage),
    period_end: requireString(raw.period_end, errorMessage),
    activation_rate: requireFiniteNumber(raw.activation_rate, errorMessage),
    steps: rawSteps.map((step) => {
      const rawStep = requireRecord(step, errorMessage);
      return {
        event_name: requireNonEmptyString(rawStep.event_name, errorMessage),
        label: requireString(rawStep.label, errorMessage),
        users: requireFiniteNumber(rawStep.users, errorMessage),
        conversion_from_previous:
          rawStep.conversion_from_previous === null ||
          rawStep.conversion_from_previous === undefined
            ? null
            : requireFiniteNumber(rawStep.conversion_from_previous, errorMessage),
        drop_off_from_previous:
          rawStep.drop_off_from_previous === null || rawStep.drop_off_from_previous === undefined
            ? null
            : requireFiniteNumber(rawStep.drop_off_from_previous, errorMessage),
      };
    }),
  };
}

async function getUpgradeConversionStats(days = 7): Promise<UpgradeConversionStats> {
  const errorMessage = "Invalid upgrade conversion stats response";
  const safeDays = Number.isFinite(days) ? Math.min(Math.max(Math.floor(days), 1), 90) : 7;
  const payload = await api.get<unknown>(
    `${ADMIN_BASE}/dashboard/upgrade-conversion?days=${safeDays}`
  );
  const raw = requireRecord(payload, errorMessage);
  const rawSources = requireArray(raw.sources, errorMessage);
  const rawChannels = requireArray(raw.channels, errorMessage);

  return {
    window_days: requireFiniteNumber(raw.window_days, errorMessage),
    period_start: requireString(raw.period_start, errorMessage),
    period_end: requireString(raw.period_end, errorMessage),
    total_conversions: requireFiniteNumber(raw.total_conversions, errorMessage),
    paid_conversions: requireFiniteNumber(raw.paid_conversions, errorMessage),
    channels: rawChannels.map((item) => {
      const channelItem = requireRecord(item, errorMessage);
      return {
        channel: asText(channelItem.channel, "other"),
        conversions: requireFiniteNumber(channelItem.conversions, errorMessage),
        paid: channelItem.paid === true,
      };
    }),
    unattributed_conversions: requireFiniteNumber(raw.unattributed_conversions, errorMessage),
    after_ai_quota_wall_conversions: requireFiniteNumber(
      raw.after_ai_quota_wall_conversions,
      errorMessage
    ),
    paid_after_ai_quota_wall_conversions: requireFiniteNumber(
      raw.paid_after_ai_quota_wall_conversions,
      errorMessage
    ),
    sources: rawSources.map((item) => {
      const sourceItem = requireRecord(item, errorMessage);
      return {
        source: requireNonEmptyString(sourceItem.source, errorMessage),
        conversions: requireFiniteNumber(sourceItem.conversions, errorMessage),
        share: requireFiniteNumber(sourceItem.share, errorMessage),
      };
    }),
  };
}

async function getUpgradeFunnelStats(days = 7): Promise<UpgradeFunnelStats> {
  const errorMessage = "Invalid upgrade funnel stats response";
  const safeDays = Number.isFinite(days) ? Math.min(Math.max(Math.floor(days), 1), 90) : 7;
  const payload = await api.get<unknown>(
    `${ADMIN_BASE}/dashboard/upgrade-funnel?days=${safeDays}`
  );
  const raw = requireRecord(payload, errorMessage);
  const rawTotals = requireRecord(raw.totals, errorMessage);
  const rawSources = requireArray(raw.sources, errorMessage);
  const rawSurfaces = raw.surfaces === undefined ? [] : requireArray(raw.surfaces, errorMessage);

  return {
    window_days: requireFiniteNumber(raw.window_days, errorMessage),
    period_start: requireString(raw.period_start, errorMessage),
    period_end: requireString(raw.period_end, errorMessage),
    totals: {
      expose: requireFiniteNumber(rawTotals.expose, errorMessage),
      click: requireFiniteNumber(rawTotals.click, errorMessage),
      conversion: requireFiniteNumber(rawTotals.conversion, errorMessage),
    },
    surfaces: rawSurfaces.map((item) => {
      const surfaceItem = requireRecord(item, errorMessage);
      return {
        source: requireString(surfaceItem.source, errorMessage),
        surface: requireString(surfaceItem.surface, errorMessage),
        exposes: requireFiniteNumber(surfaceItem.exposes, errorMessage),
        exposed_users: requireFiniteNumber(surfaceItem.exposed_users, errorMessage),
        clicks: requireFiniteNumber(surfaceItem.clicks, errorMessage),
        clicking_users: requireFiniteNumber(surfaceItem.clicking_users, errorMessage),
        page_arrivals: requireFiniteNumber(surfaceItem.page_arrivals, errorMessage),
      };
    }),
    sources: rawSources.map((item) => {
      const sourceItem = requireRecord(item, errorMessage);
      return {
        source: requireNonEmptyString(sourceItem.source, errorMessage),
        exposes: requireFiniteNumber(sourceItem.exposes, errorMessage),
        clicks: requireFiniteNumber(sourceItem.clicks, errorMessage),
        conversions: requireFiniteNumber(sourceItem.conversions, errorMessage),
        click_through_rate: requireFiniteNumber(sourceItem.click_through_rate, errorMessage),
        conversion_rate_from_click: requireFiniteNumber(
          sourceItem.conversion_rate_from_click,
          errorMessage
        ),
        conversion_rate_from_expose: requireFiniteNumber(
          sourceItem.conversion_rate_from_expose,
          errorMessage
        ),
      };
    }),
  };
}

// ==================== Subscription Plans API ====================

async function getPlans(): Promise<SubscriptionPlan[]> {
  const payload = await api.get<unknown>(`${ADMIN_BASE}/plans`);
  return requireArray(payload, "Invalid subscription plans response").map(
    normalizeSubscriptionPlan
  );
}

async function updatePlan(
  planId: string,
  data: PlanUpdateRequest
): Promise<SubscriptionPlan> {
  return api.put<SubscriptionPlan>(`${ADMIN_BASE}/plans/${planId}`, data);
}

// ==================== Inspirations (Admin) API ====================

export interface AdminInspirationsListResponse {
  items: AdminInspiration[];
  total: number;
}

async function getInspirations(params?: {
  status?: string;
  source?: string;
  skip?: number;
  limit?: number;
}): Promise<AdminInspirationsListResponse> {
  const searchParams = new URLSearchParams();
  if (params?.status) searchParams.set("status", params.status);
  if (params?.source) searchParams.set("source", params.source);
  if (params?.skip) searchParams.set("skip", String(params.skip));
  if (params?.limit) searchParams.set("limit", String(params.limit));

  const queryString = searchParams.toString();
  const url = queryString
    ? `${ADMIN_BASE}/inspirations?${queryString}`
    : `${ADMIN_BASE}/inspirations`;

  const payload = await api.get<unknown>(url);
  const payloadRecord = requireRecord(payload, "Invalid inspirations response");
  const items = requireArray(payloadRecord.items, "Invalid inspirations response").map(
    normalizeInspiration
  );

  return {
    items,
    total: requireFiniteNumber(payloadRecord.total, "Invalid inspirations response"),
  };
}

async function reviewInspiration(
  id: string,
  approve: boolean,
  reason?: string
): Promise<{ message: string }> {
  return api.post<{ message: string }>(`${ADMIN_BASE}/inspirations/${id}/review`, {
    approve,
    rejection_reason: reason,
  });
}

async function updateInspiration(
  id: string,
  data: {
    name?: string;
    description?: string;
    tags?: string[];
    is_featured?: boolean;
  }
): Promise<AdminInspiration> {
  return api.patch<AdminInspiration>(`${ADMIN_BASE}/inspirations/${id}`, data);
}

async function deleteInspiration(id: string): Promise<{ message: string }> {
  return api.delete<{ message: string }>(`${ADMIN_BASE}/inspirations/${id}`);
}

// ==================== Feedback (Admin) API ====================

async function getFeedbackList(params?: {
  status?: AdminFeedbackStatus;
  source_page?: "dashboard" | "editor";
  has_screenshot?: boolean;
  search?: string;
  skip?: number;
  limit?: number;
}): Promise<AdminFeedbackListResponse> {
  const searchParams = new URLSearchParams();
  if (params?.status) searchParams.set("status", params.status);
  if (params?.source_page) searchParams.set("source_page", params.source_page);
  if (params?.has_screenshot !== undefined) {
    searchParams.set("has_screenshot", String(params.has_screenshot));
  }
  if (params?.search) searchParams.set("search", params.search);
  if (params?.skip !== undefined) searchParams.set("skip", String(params.skip));
  if (params?.limit !== undefined) searchParams.set("limit", String(params.limit));

  const queryString = searchParams.toString();
  const url = queryString ? `${ADMIN_BASE}/feedback?${queryString}` : `${ADMIN_BASE}/feedback`;

  const payload = await api.get<unknown>(url);
  const payloadRecord = requireRecord(payload, "Invalid feedback list response");
  const items = requireArray(payloadRecord.items, "Invalid feedback list response").map(
    normalizeAdminFeedback
  );

  return {
    items,
    total: requireFiniteNumber(payloadRecord.total, "Invalid feedback list response"),
  };
}

async function updateFeedbackStatus(
  feedbackId: string,
  status: AdminFeedbackStatus
): Promise<AdminFeedbackItem> {
  const payload = await api.patch<unknown>(`${ADMIN_BASE}/feedback/${feedbackId}/status`, {
    status,
  });
  return normalizeAdminFeedback(payload);
}

async function getFeedbackScreenshotBlob(feedbackId: string): Promise<Blob> {
  const response = await authenticatedFetch(
    `${ADMIN_BASE}/feedback/${feedbackId}/screenshot`,
    { method: "GET" },
    { includeLanguage: true },
  );

  if (!response.ok) {
    throw await readApiError(response);
  }

  return response.blob();
}

// ==================== Points Management API ====================

async function getPointsStats(): Promise<PointsStats> {
  const payload = await api.get<unknown>(`${ADMIN_BASE}/points/stats`);
  return normalizePointsStats(payload);
}

async function getUserPoints(userId: string): Promise<AdminPointsBalance> {
  const payload = await api.get<unknown>(`${ADMIN_BASE}/points/${userId}`);
  return normalizeUserPoints(payload);
}

async function getUserPointsTransactions(
  userId: string,
  params?: { page?: number; page_size?: number }
): Promise<PointsTransactionsResponse> {
  const searchParams = new URLSearchParams();
  if (params?.page) searchParams.set("page", String(params.page));
  if (params?.page_size) searchParams.set("page_size", String(params.page_size));
  const payload = await api.get<unknown>(
    `${ADMIN_BASE}/points/${userId}/transactions?${searchParams.toString()}`
  );
  const payloadRecord = requireRecord(payload, "Invalid points transactions response");
  const items = requireArray(payloadRecord.items, "Invalid points transactions response").map(
    normalizePointsTransaction
  );

  return {
    items,
    total: requireFiniteNumber(payloadRecord.total, "Invalid points transactions response"),
    page: requireFiniteNumber(payloadRecord.page, "Invalid points transactions response"),
    page_size: requireFiniteNumber(
      payloadRecord.page_size,
      "Invalid points transactions response"
    ),
  };
}

async function adjustUserPoints(
  userId: string,
  data: PointsAdjustRequest
): Promise<{ message: string; new_balance: number }> {
  return api.post<{ message: string; new_balance: number }>(
    `${ADMIN_BASE}/points/${userId}/adjust`,
    data
  );
}

// ==================== Check-in Stats API ====================

async function getCheckInStats(): Promise<CheckInStats> {
  const payload = await api.get<unknown>(`${ADMIN_BASE}/check-in/stats`);
  return normalizeCheckInStats(payload);
}

async function getCheckInRecords(params?: {
  page?: number;
  page_size?: number;
  user_id?: string;
}): Promise<CheckInRecordsResponse> {
  const searchParams = new URLSearchParams();
  if (params?.page) searchParams.set("page", String(params.page));
  if (params?.page_size) searchParams.set("page_size", String(params.page_size));
  if (params?.user_id) searchParams.set("user_id", params.user_id);
  const payload = await api.get<unknown>(
    `${ADMIN_BASE}/check-in/records?${searchParams.toString()}`
  );
  const payloadRecord = requireRecord(payload, "Invalid check-in records response");
  const items = requireArray(payloadRecord.items, "Invalid check-in records response").map(
    normalizeCheckInRecord
  );

  return {
    items,
    total: requireFiniteNumber(payloadRecord.total, "Invalid check-in records response"),
    page: requireFiniteNumber(payloadRecord.page, "Invalid check-in records response"),
    page_size: requireFiniteNumber(payloadRecord.page_size, "Invalid check-in records response"),
  };
}

// ==================== Referral Management API ====================

async function getReferralStats(): Promise<ReferralStats> {
  const payload = await api.get<unknown>(`${ADMIN_BASE}/referrals/stats`);
  return normalizeReferralStats(payload);
}

async function createAdminInviteCode(): Promise<InviteCodesResponse["items"][number]> {
  const payload = await api.post<unknown>(`${ADMIN_BASE}/invites`);
  return normalizeInviteCode(payload);
}

async function getInviteCodes(params?: {
  page?: number;
  page_size?: number;
  is_active?: boolean;
}): Promise<InviteCodesResponse> {
  const searchParams = new URLSearchParams();
  if (params?.page) searchParams.set("page", String(params.page));
  if (params?.page_size) searchParams.set("page_size", String(params.page_size));
  if (params?.is_active !== undefined) searchParams.set("is_active", String(params.is_active));
  const payload = await api.get<unknown>(`${ADMIN_BASE}/invites?${searchParams.toString()}`);
  const payloadRecord = requireRecord(payload, "Invalid invite codes response");
  const items = requireArray(payloadRecord.items, "Invalid invite codes response").map(
    normalizeInviteCode
  );

  return {
    items,
    total: requireFiniteNumber(payloadRecord.total, "Invalid invite codes response"),
    page: requireFiniteNumber(payloadRecord.page, "Invalid invite codes response"),
    page_size: requireFiniteNumber(payloadRecord.page_size, "Invalid invite codes response"),
  };
}

async function getReferralRewards(params?: {
  page?: number;
  page_size?: number;
}): Promise<RewardsResponse> {
  const searchParams = new URLSearchParams();
  if (params?.page) searchParams.set("page", String(params.page));
  if (params?.page_size) searchParams.set("page_size", String(params.page_size));
  const payload = await api.get<unknown>(
    `${ADMIN_BASE}/referrals/rewards?${searchParams.toString()}`
  );
  const payloadRecord = requireRecord(payload, "Invalid referral rewards response");
  const items = requireArray(payloadRecord.items, "Invalid referral rewards response").map(
    normalizeRewardRecord
  );

  return {
    items,
    total: requireFiniteNumber(payloadRecord.total, "Invalid referral rewards response"),
    page: requireFiniteNumber(payloadRecord.page, "Invalid referral rewards response"),
    page_size: requireFiniteNumber(payloadRecord.page_size, "Invalid referral rewards response"),
  };
}

// ==================== Quota Usage API ====================

async function getQuotaUsageStats(): Promise<QuotaUsageStats> {
  const payload = await api.get<unknown>(`${ADMIN_BASE}/quota/usage`);
  return normalizeQuotaUsageStats(payload);
}

async function getUserQuota(userId: string): Promise<UserQuotaDetail> {
  const payload = await api.get<unknown>(`${ADMIN_BASE}/quota/${userId}`);
  return normalizeUserQuota(payload);
}

// ==================== Export ====================

export const adminApi = {
  // 用户管理
  getUsers,
  getUser,
  updateUser,
  deleteUser,

  // Prompt 管理
  getPrompts,
  getPrompt,
  upsertPrompt,
  deletePrompt,
  reloadPrompts,

  // 技能审核
  getPendingSkills,
  approveSkill,
  rejectSkill,
  unpublishSkill,
  getSkillReviewResources,

  // 兑换码管理
  getCodes,
  createCode,
  createCodesBatch,
  updateCode,

  // 订阅管理
  getSubscriptions,
  getPaymentOrders,
  syncPaymentOrder,
  getUserSubscription,
  updateUserSubscription,

  // 用量与成本
  getUsageSummary,
  getUsageByUser,
  getUsageByModel,
  getUserDailyUsage,

  // 审计日志
  getAuditLogs,

  // Dashboard
  getDashboardStats,
  getGrowthDashboard,
  getAcquisitionStats,
  getActivationFunnel,
  getUpgradeConversionStats,
  getUpgradeFunnelStats,

  // Subscription Plans
  getPlans,
  updatePlan,

  // Inspirations (Admin)
  getInspirations,
  reviewInspiration,
  updateInspiration,
  deleteInspiration,

  // Feedback (Admin)
  getFeedbackList,
  updateFeedbackStatus,
  getFeedbackScreenshotBlob,

  // 积分管理
  getPointsStats,
  getUserPoints,
  getUserPointsTransactions,
  adjustUserPoints,

  // 签到统计
  getCheckInStats,
  getCheckInRecords,

  // 邀请系统管理
  getReferralStats,
  createAdminInviteCode,
  getInviteCodes,
  getReferralRewards,

  // 配额使用
  getQuotaUsageStats,
  getUserQuota,
};
