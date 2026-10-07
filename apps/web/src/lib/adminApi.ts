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
  ApiError,
  api,
  getAccessToken,
  getApiBase,
  resolveOwnedAuthSession,
  tryRefreshToken,
} from "./apiClient";
import { resolveApiErrorMessage } from "./errorHandler";
import type {
  User,
  UserUpdateRequest,
  UsersListResponse,
  SystemPromptConfig,
  PromptConfigRequest,
  PromptReloadResult,
  PromptsListResponse,
  PendingSkill,
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
  // 商业化类型
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

export type { PendingSkill, SkillReviewResource, SkillReviewStatus } from "../types/admin";

const ADMIN_BASE = "/api/admin";
const FALLBACK_TIMESTAMP = "1970-01-01T00:00:00.000Z";

const DEFAULT_DASHBOARD_STATS: DashboardStats = {
  total_users: 0,
  active_users: 0,
  new_users_today: 0,
  total_projects: 0,
  total_inspirations: 0,
  pending_inspirations: 0,
  active_subscriptions: 0,
  pro_users: 0,
  total_points_in_circulation: 0,
  today_check_ins: 0,
  active_invite_codes: 0,
  week_referrals: 0,
};

const DEFAULT_ACTIVATION_FUNNEL: ActivationFunnelStats = {
  window_days: 7,
  period_start: "",
  period_end: "",
  steps: [],
  activation_rate: 0,
};

const DEFAULT_UPGRADE_CONVERSION: UpgradeConversionStats = {
  window_days: 7,
  period_start: "",
  period_end: "",
  total_conversions: 0,
  paid_conversions: 0,
  unattributed_conversions: 0,
  sources: [],
  channels: [],
};

const DEFAULT_UPGRADE_FUNNEL: UpgradeFunnelStats = {
  window_days: 7,
  period_start: "",
  period_end: "",
  totals: {
    expose: 0,
    click: 0,
    conversion: 0,
  },
  sources: [],
};

type UnknownRecord = Record<string, unknown>;

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === "object" && value !== null;
}

function asRecord(value: unknown): UnknownRecord | null {
  if (isRecord(value)) {
    return value;
  }
  return null;
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

function asBoolean(value: unknown, fallback: boolean = false): boolean {
  if (typeof value === "boolean") {
    return value;
  }
  return fallback;
}

function isLikelyTestAccount(email: string, username: string): boolean {
  const normalizedEmail = email.toLowerCase();
  const tokenSource = `${username} ${email}`.toLowerCase();
  if (normalizedEmail.endsWith("@example.com")) {
    return true;
  }
  return ["test", "qa", "demo", "smoke"].some((token) => tokenSource.includes(token));
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
  const raw = resolvePayloadRecord(plan);
  const rawName = asText(raw.name, "free");
  const featuresRecord = asRecord(raw.features) ?? asRecord(raw.entitlements) ?? {};

  return {
    id: asText(raw.id, rawName),
    name: rawName as SubscriptionPlan["name"],
    display_name: asText(raw.display_name, rawName),
    display_name_en: asNullableText(raw.display_name_en) ?? undefined,
    price_monthly_cents: asNumber(raw.price_monthly_cents, 0),
    price_yearly_cents: asNumber(raw.price_yearly_cents, 0),
    features: featuresRecord as SubscriptionFeatures,
    is_active: asBoolean(raw.is_active, true),
    created_at: asNullableIsoDate(raw.created_at) ?? undefined,
    updated_at: asNullableIsoDate(raw.updated_at) ?? undefined,
  };
}

function pickArray<T>(payload: unknown, keys: string[]): T[] {
  if (Array.isArray(payload)) {
    return payload as T[];
  }

  const payloadRecord = asRecord(payload);
  if (!payloadRecord) {
    return [];
  }

  for (const key of keys) {
    const value = payloadRecord[key];
    if (Array.isArray(value)) {
      return value as T[];
    }
  }

  const nestedRecord = asRecord(payloadRecord.data);
  if (!nestedRecord) {
    return [];
  }

  for (const key of keys) {
    const value = nestedRecord[key];
    if (Array.isArray(value)) {
      return value as T[];
    }
  }

  return [];
}

function resolvePayloadRecord(payload: unknown): UnknownRecord {
  if (isRecord(payload)) {
    const nested = asRecord(payload.data);
    if (nested) {
      return nested;
    }
    return payload;
  }
  return {};
}

function normalizeUser(user: unknown): User {
  const raw = resolvePayloadRecord(user);
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
  const raw = resolvePayloadRecord(skill);
  return {
    id: asText(raw.id, ""),
    name: asText(raw.name, "Untitled Skill"),
    description: asNullableText(raw.description),
    instructions: asText(raw.instructions, ""),
    category: asText(raw.category, "general"),
    tags: Array.isArray(raw.tags) ? raw.tags.map((tag) => String(tag)) : [],
    skill_metadata:
      raw.skill_metadata && typeof raw.skill_metadata === "object" && !Array.isArray(raw.skill_metadata)
        ? (raw.skill_metadata as Record<string, unknown>)
        : {},
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
  const raw = resolvePayloadRecord(subscription);
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
    effective_plan_display_name: asNullableText(raw.effective_plan_display_name) ?? asNullableText(raw.plan_display_name),
    effective_plan_display_name_en:
      asNullableText(raw.effective_plan_display_name_en) ?? asNullableText(raw.plan_display_name_en),
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
        : isLikelyTestAccount(email, username),
  };
}

function normalizeInspiration(inspiration: unknown): AdminInspiration {
  const raw = resolvePayloadRecord(inspiration);
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
  const raw = resolvePayloadRecord(feedback);
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
  const raw = resolvePayloadRecord(config);
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
  const raw = resolvePayloadRecord(code);
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
  const raw = resolvePayloadRecord(log);
  if (
    !asText(raw.id) || !asText(raw.admin_id) ||
    (typeof raw.admin_name !== "string" && raw.admin_name !== null) ||
    !asText(raw.action) || !asText(raw.resource_type) ||
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
    details: asNullableText(raw.details),
    old_value: oldValue,
    new_value: newValue,
    ip_address: asNullableText(raw.ip_address),
    user_agent: asNullableText(raw.user_agent),
    created_at: asIsoDate(raw.created_at),
  };
}

function normalizePointsStats(stats: unknown): PointsStats {
  const raw = resolvePayloadRecord(stats);
  return {
    total_points_issued: asNumber(raw.total_points_issued, 0),
    total_points_spent: asNumber(raw.total_points_spent, 0),
    total_points_expired: asNumber(raw.total_points_expired, 0),
    active_users_with_points: asNumber(raw.active_users_with_points, 0),
  };
}

function normalizeUserPoints(balance: unknown): AdminPointsBalance {
  const raw = resolvePayloadRecord(balance);
  return {
    user_id: asText(raw.user_id, ""),
    username: asText(raw.username, "-"),
    email: asText(raw.email, "-"),
    available: asNumber(raw.available, 0),
    pending_expiration: asNumber(raw.pending_expiration, 0),
    total_earned: asNumber(raw.total_earned, 0),
    total_spent: asNumber(raw.total_spent, 0),
  };
}

function normalizePointsTransaction(
  transaction: unknown
): PointsTransactionsResponse["items"][number] {
  const raw = resolvePayloadRecord(transaction);
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
  const raw = resolvePayloadRecord(stats);
  const rawDistribution = asRecord(raw.streak_distribution) ?? {};
  const distribution = Object.entries(rawDistribution).reduce<Record<number, number>>(
    (acc, [key, value]) => {
      const numericKey = Number(key);
      if (!Number.isFinite(numericKey)) {
        return acc;
      }
      acc[numericKey] = asNumber(value, 0);
      return acc;
    },
    {}
  );

  return {
    today_count: asNumber(raw.today_count, 0),
    yesterday_count: asNumber(raw.yesterday_count, 0),
    week_total: asNumber(raw.week_total, 0),
    streak_distribution: distribution,
  };
}

function normalizeCheckInRecord(record: unknown): CheckInRecordsResponse["items"][number] {
  const raw = resolvePayloadRecord(record);
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
  const raw = resolvePayloadRecord(stats);
  return {
    total_codes: asNumber(raw.total_codes, 0),
    active_codes: asNumber(raw.active_codes, 0),
    total_referrals: asNumber(raw.total_referrals, 0),
    successful_referrals: asNumber(raw.successful_referrals, 0),
    pending_rewards: asNumber(raw.pending_rewards, 0),
    total_points_awarded: asNumber(raw.total_points_awarded, 0),
  };
}

function normalizeInviteCode(code: unknown): InviteCodesResponse["items"][number] {
  const raw = resolvePayloadRecord(code);
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
  const raw = resolvePayloadRecord(reward);
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
  const raw = resolvePayloadRecord(stats);
  return {
    period_start: asText(raw.period_start, ""),
    period_end: asText(raw.period_end, ""),
    material_decompositions: asNumber(raw.material_decompositions, 0),
    inspiration_copies: asNumber(raw.inspiration_copies, 0),
    skills_created: asNumber(raw.skills_created, 0),
  };
}

function normalizeQuotaCounter(counter: unknown): QuotaCounter {
  const raw = resolvePayloadRecord(counter);
  return {
    used: asNumber(raw.used, 0),
    limit: asNumber(raw.limit, 0),
    reset_at: asNullableText(raw.reset_at),
  };
}

function normalizeUserQuota(detail: unknown): UserQuotaDetail {
  const raw = resolvePayloadRecord(detail);
  return {
    user_id: asText(raw.user_id, ""),
    username: asText(raw.username, "-"),
    email: asText(raw.email, ""),
    plan_name: asText(raw.plan_name, "-"),
    plan_display_name: asNullableText(raw.plan_display_name),
    plan_display_name_en: asNullableText(raw.plan_display_name_en),
    ai_conversations: normalizeQuotaCounter(raw.ai_conversations),
    material_decompositions: normalizeQuotaCounter(raw.material_decompositions),
    inspiration_copies: normalizeQuotaCounter(raw.inspiration_copies),
    custom_skills: normalizeQuotaCounter(raw.custom_skills),
  };
}

// ==================== 用户管理 API ====================

/**
 * Fetch paginated list of users with optional search filter.
 *
 * @param skip - Number of records to skip for pagination (default: 0)
 * @param limit - Maximum number of records to return (default: 20)
 * @param search - Optional search keyword for username or email filtering
 * @returns Promise resolving to one page of users and the total matching the filter
 */
export async function getUsers(
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
  const users = pickArray<unknown>(payload, ["items", "users"]).map(normalizeUser);
  const rawTotal = asRecord(payload)?.total;
  const total = typeof rawTotal === "number" && Number.isFinite(rawTotal) ? rawTotal : null;
  return { users, total };
}

/**
 * Fetch a single user by ID.
 *
 * @param id - User ID (UUID string)
 * @returns Promise resolving to user object
 */
export async function getUser(id: string): Promise<User> {
  const payload = await api.get<unknown>(`${ADMIN_BASE}/users/${encodeURIComponent(id)}`);
  return normalizeUser(payload);
}

/**
 * Update user information.
 *
 * @param id - User ID (UUID string)
 * @param data - Partial user data to update
 * @returns Promise resolving to updated user object
 */
export async function updateUser(
  id: string,
  data: UserUpdateRequest
): Promise<User> {
  return api.put<User>(`${ADMIN_BASE}/users/${id}`, data);
}

/**
 * Soft delete a user account.
 *
 * @param id - User ID (UUID string)
 * @returns Promise resolving to backend delete result
 */
export async function deleteUser(id: string): Promise<User | { message: string }> {
  return api.delete<User | { message: string }>(`${ADMIN_BASE}/users/${id}`);
}

// ==================== Prompt 管理 API ====================

/**
 * Fetch all system prompt configurations.
 *
 * @returns Promise resolving to list of prompt configurations
 */
export async function getPrompts(): Promise<PromptsListResponse> {
  const payload = await api.get<unknown>(`${ADMIN_BASE}/prompts`);
  return pickArray<unknown>(payload, ["items", "prompts"]).map(normalizeSystemPrompt);
}

/**
 * Fetch prompt configuration for a specific project type.
 *
 * @param projectType - Project type identifier (e.g., "novel", "article")
 * @returns Promise resolving to prompt configuration object
 */
export async function getPrompt(
  projectType: string
): Promise<SystemPromptConfig> {
  return api.get<SystemPromptConfig>(
    `${ADMIN_BASE}/prompts/${encodeURIComponent(projectType)}`
  );
}

/**
 * Create or update prompt configuration for a project type.
 *
 * @param projectType - Project type identifier (e.g., "novel", "article")
 * @param data - Prompt configuration data to upsert
 * @returns Promise resolving to created/updated prompt configuration
 */
export async function upsertPrompt(
  projectType: string,
  data: PromptConfigRequest
): Promise<SystemPromptConfig> {
  return api.put<SystemPromptConfig>(
    `${ADMIN_BASE}/prompts/${encodeURIComponent(projectType)}`,
    data
  );
}

/**
 * Delete prompt configuration for a project type.
 *
 * @param projectType - Project type identifier to delete
 * @returns Promise resolving to success message
 */
export async function deletePrompt(
  projectType: string
): Promise<{ message: string }> {
  return api.delete<{ message: string }>(
    `${ADMIN_BASE}/prompts/${encodeURIComponent(projectType)}`
  );
}

/**
 * Hot reload prompt configurations from storage.
 *
 * @returns Promise resolving to success message
 */
export async function reloadPrompts(): Promise<PromptReloadResult> {
  return api.post<PromptReloadResult>(`${ADMIN_BASE}/prompts/reload`);
}

// ==================== 技能审核 API ====================

type PendingSkillsPayload =
  | PendingSkill[]
  | {
      items?: PendingSkill[];
      skills?: PendingSkill[];
    };

/**
 * Fetch list of skills pending admin review.
 *
 * @returns Promise resolving to array of pending skills
 */
export async function getPendingSkills(status: SkillReviewStatus = "pending"): Promise<PendingSkill[]> {
  const payload = await api.get<PendingSkillsPayload>(
    `${ADMIN_BASE}/skills/pending?status=${encodeURIComponent(status)}`,
  );
  return pickArray<unknown>(payload, ["items", "skills"]).map(normalizePendingSkill);
}

/**
 * Approve a pending skill for public use.
 *
 * @param id - Skill ID to approve
 * @returns Promise resolving to success message and approved skill ID
 */
export async function approveSkill(id: string): Promise<{ message: string; skill_id: string }> {
  return api.post<{ message: string; skill_id: string }>(`${ADMIN_BASE}/skills/${id}/approve`);
}

/**
 * Reject a pending skill with optional reason.
 *
 * @param id - Skill ID to reject
 * @param reason - Optional rejection reason for the author
 * @returns Promise resolving to success message and rejected skill ID
 */
export async function rejectSkill(
  id: string,
  reason?: string
): Promise<{ message: string; skill_id: string }> {
  return api.post<{ message: string; skill_id: string }>(
    `${ADMIN_BASE}/skills/${id}/reject`,
    { rejection_reason: reason }
  );
}

/**
 * Take an approved public skill down (status becomes "unpublished").
 *
 * @param id - Skill ID to unpublish
 * @param reason - Optional reason, stored and shown in the review history
 */
export async function unpublishSkill(
  id: string,
  reason?: string
): Promise<{ message: string; skill_id: string }> {
  return api.post<{ message: string; skill_id: string }>(
    `${ADMIN_BASE}/skills/${id}/unpublish`,
    { rejection_reason: reason }
  );
}

/**
 * Fetch every resource file (raw content) of a public skill for review.
 *
 * @param id - Public skill ID
 */
export async function getSkillReviewResources(id: string): Promise<SkillReviewResource[]> {
  const payload = await api.get<{ resources?: SkillReviewResource[] }>(
    `${ADMIN_BASE}/skills/${id}/resources`
  );
  return Array.isArray(payload?.resources) ? payload.resources : [];
}

// ==================== 兑换码管理 API ====================

/** Represents a redemption code for subscription or benefits. */
export interface RedemptionCode {
  /** Unique code identifier */
  id: string;
  /** The actual redemption code string */
  code: string;
  /** Subscription tier the code grants (e.g., "free", "pro") */
  tier: string;
  /** Duration of the benefit in days */
  duration_days: number;
  /** Type of code (single_use or multi_use) */
  code_type: string;
  /** Maximum number of times the code can be used */
  max_uses: number | null;
  /** Current number of times the code has been used */
  current_uses: number;
  /** Whether the code is currently active */
  is_active: boolean;
  /** Optional admin notes about the code */
  notes: string | null;
  /** ISO 8601 timestamp of code creation */
  created_at: string;
  /** ISO 8601 timestamp of last update */
  updated_at: string;
}

/** Paginated response for redemption code listings. */
export interface CodesListResponse {
  /** Array of redemption codes */
  items: RedemptionCode[];
  /** Total number of codes matching the query */
  total: number;
  /** Current page number */
  page: number;
  /** Number of items per page */
  page_size: number;
}

/**
 * Fetch paginated list of redemption codes with optional filters.
 *
 * @param params - Optional filter parameters including pagination and tier filter
 * @returns Promise resolving to paginated codes list
 */
export async function getCodes(params?: {
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
  const payloadRecord = resolvePayloadRecord(payload);
  const items = pickArray<unknown>(payload, ["items", "codes"]).map(normalizeRedemptionCode);

  return {
    items,
    total: asNumber(payloadRecord.total, items.length),
    page: asNumber(payloadRecord.page, params?.page ?? 1),
    page_size: asNumber(payloadRecord.page_size, (params?.page_size ?? items.length) || 20),
  };
}

/**
 * Create a single redemption code.
 *
 * @param data - Code creation parameters
 * @returns Promise resolving to created redemption code
 */
export async function createCode(data: {
  tier: string;
  duration_days: number;
  code_type?: "single_use" | "multi_use" | "single" | "multi";
  max_uses?: number;
  notes?: string;
}): Promise<RedemptionCode> {
  return api.post<RedemptionCode>(`${ADMIN_BASE}/codes`, data);
}

/**
 * Batch create multiple redemption codes with the same configuration.
 *
 * @param data - Batch creation parameters including count
 * @returns Promise resolving to array of created codes and count
 */
export interface CodeBatchCreateResponse {
  codes: string[];
  count?: number;
  created?: number;
  code_type?: "single_use" | "multi_use";
  max_uses?: number;
}

export async function createCodesBatch(data: {
  tier: string;
  duration_days: number;
  count: number;
  code_type?: "single_use" | "multi_use" | "single" | "multi";
  /** Required for multi-use codes: how many users can redeem each code. */
  max_uses?: number;
  notes?: string;
}): Promise<CodeBatchCreateResponse> {
  return api.post<CodeBatchCreateResponse>(`${ADMIN_BASE}/codes/batch`, data);
}

/**
 * Fetch a single redemption code by ID.
 *
 * @param id - Code ID (UUID string)
 * @returns Promise resolving to redemption code object
 */
export async function getCode(id: string): Promise<RedemptionCode> {
  return api.get<RedemptionCode>(`${ADMIN_BASE}/codes/${id}`);
}

/**
 * Update redemption code properties.
 *
 * @param id - Code ID (UUID string)
 * @param data - Partial code data to update
 * @returns Promise resolving to updated redemption code
 */
export async function updateCode(
  id: string,
  data: { is_active?: boolean; notes?: string }
): Promise<RedemptionCode> {
  return api.put<RedemptionCode>(`${ADMIN_BASE}/codes/${id}`, data);
}

// ==================== 订阅管理 API ====================

/** Represents a user subscription with plan and billing details. */
export interface Subscription {
  /** Unique subscription identifier */
  id: string;
  /** ID of the subscribed user */
  user_id: string;
  /** Username of the subscribed user */
  username: string;
  /** Email of the subscribed user */
  email: string;
  /** Name of the subscription plan */
  plan_name: string;
  /** Display name of the subscription plan */
  plan_display_name?: string | null;
  /** English display name of the subscription plan */
  plan_display_name_en?: string | null;
  /** Effective plan for user-centric view (falls back to free when record missing). */
  effective_plan_name?: string;
  /** Effective plan display name for UI. */
  effective_plan_display_name?: string | null;
  /** English effective plan display name for UI. */
  effective_plan_display_name_en?: string | null;
  /** Current subscription status (e.g., "active", "expired", "cancelled") */
  status: string;
  /** Effective status for user-centric view. */
  effective_status?: string;
  /** ISO 8601 timestamp of current billing period start */
  current_period_start: string | null;
  /** ISO 8601 timestamp of current billing period end */
  current_period_end: string | null;
  /** ISO 8601 timestamp of subscription creation */
  created_at: string;
  /** ISO 8601 timestamp of last subscription update */
  updated_at: string;
  /** Whether this item maps to a persisted user_subscription row */
  has_subscription_record?: boolean;
  /** Heuristic marker for QA/test/smoke accounts */
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
  /** Array of subscriptions */
  items: Subscription[];
  /** Total number of subscriptions matching the query */
  total: number;
  /** Current page number */
  page: number;
  /** Number of items per page */
  page_size: number;
}

/**
 * Fetch paginated list of subscriptions with optional status filter.
 *
 * @param params - Optional filter parameters including pagination and status
 * @returns Promise resolving to paginated subscriptions list
 */
export async function getSubscriptions(params?: {
  page?: number;
  page_size?: number;
  status?: string;
  /** Username or email fragment */
  search?: string;
}): Promise<SubscriptionsListResponse> {
  const searchParams = new URLSearchParams();
  if (params?.page) searchParams.set("page", String(params.page));
  if (params?.page_size) searchParams.set("page_size", String(params.page_size));
  if (params?.status) searchParams.set("status", params.status);
  if (params?.search) searchParams.set("search", params.search);

  const payload = await api.get<unknown>(`${ADMIN_BASE}/subscriptions?${searchParams.toString()}`);
  const payloadRecord = resolvePayloadRecord(payload);
  const items = pickArray<unknown>(payload, ["items", "subscriptions"]).map(normalizeSubscription);
  const total = asNumber(payloadRecord.total, items.length);
  const page = asNumber(payloadRecord.page, params?.page ?? 1);
  const pageSize = asNumber(payloadRecord.page_size, (params?.page_size ?? items.length) || 20);

  return {
    items,
    total,
    page,
    page_size: pageSize,
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
  /** Paid orders whose membership is not granted yet, across all pages. */
  needs_attention_total?: number;
}

export type PaymentOrderSyncOutcome = "already_fulfilled" | "fulfilled" | "unpaid" | "not_found";

export interface PaymentOrderSyncResponse {
  outcome: PaymentOrderSyncOutcome;
  order: AdminPaymentOrder;
}

export async function getPaymentOrders(params: {
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

/** Query Zpay for one order and grant it if paid (audited server-side). */
export async function syncPaymentOrder(orderId: string): Promise<PaymentOrderSyncResponse> {
  return api.post<PaymentOrderSyncResponse>(
    `${ADMIN_BASE}/payment-orders/${encodeURIComponent(orderId)}/sync`
  );
}

/** Fetch subscription details for a specific user. */
export async function getUserSubscription(userId: string): Promise<UserSubscriptionDetailResponse> {
  return api.get<UserSubscriptionDetailResponse>(`${ADMIN_BASE}/subscriptions/${userId}`);
}

/**
 * Update a user's subscription (admin override).
 *
 * @param userId - User ID (UUID string)
 * @param data - Subscription update parameters
 * @returns Promise resolving to operation result
 */
export async function updateUserSubscription(
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
  /** CNY as a decimal string rounded to 4 places. */
  cost_cny: string;
  peak_cost_cny: string;
  offpeak_cost_cny: string;
}

export interface UsageSourceRow extends UsageMetrics {
  source: string;
}

export interface UsageDailyRow extends UsageMetrics {
  /** Beijing calendar date, YYYY-MM-DD. */
  date: string;
  users?: number;
}

export interface UsagePeriod {
  timezone: string;
  period_start: string;
  period_end: string;
  pricing_version: string;
  /** CNY per 1M tokens, keyed by band then token kind. */
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

/** Usage and CNY cost totals for a Beijing-day window. */
export async function getUsageSummary(window: UsageWindow): Promise<UsageSummaryResponse> {
  return api.get<UsageSummaryResponse>(`${ADMIN_BASE}/usage/summary?window=${window}`);
}

/** Per-user usage for a window, sorted server-side. */
export async function getUsageByUser(params: {
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

/** One user's daily usage for the last 7, 14 or 30 Beijing days. */
export async function getUserDailyUsage(
  userId: string,
  days: UsageDetailDays = 7
): Promise<UserDailyUsageResponse> {
  return api.get<UserDailyUsageResponse>(
    `${ADMIN_BASE}/usage/users/${encodeURIComponent(userId)}/daily?days=${days}`
  );
}

// ==================== 审计日志 API ====================

/** Represents an admin action audit log entry. */
export interface AuditLog {
  /** Unique log entry identifier */
  id: string;
  /** ID of the admin who performed the action */
  admin_id: string;
  /** Display name of the admin */
  admin_name: string;
  /** Action type performed (e.g., "user.update", "plan.change") */
  action: string;
  /** Type of resource affected (e.g., "user", "subscription") */
  resource_type: string;
  /** ID of the affected resource */
  resource_id: string;
  /** Optional additional details about the action */
  details: string | null;
  /** Previous value before the change */
  old_value: Record<string, unknown> | null;
  /** New value after the change */
  new_value: Record<string, unknown> | null;
  /** IP address of the admin */
  ip_address: string | null;
  /** User agent string of the admin's browser */
  user_agent: string | null;
  /** ISO 8601 timestamp of the action */
  created_at: string;
}

/** Paginated response for audit log listings. */
export interface AuditLogsResponse {
  /** Array of audit log entries */
  items: AuditLog[];
  /** Total number of logs matching the query */
  total: number;
  /** Current page number */
  page: number;
  /** Number of items per page */
  page_size: number;
}

/**
 * Fetch paginated audit logs with optional filters.
 *
 * @param params - Optional filter parameters including pagination, resource type, and action
 * @returns Promise resolving to paginated audit logs list
 */
export async function getAuditLogs(params?: {
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
  const payloadRecord = resolvePayloadRecord(payload);
  const rawItems = payloadRecord.items ?? payloadRecord.logs;
  const total = asNumber(payloadRecord.total, NaN);
  const page = asNumber(payloadRecord.page, NaN);
  const pageSize = asNumber(payloadRecord.page_size, NaN);
  if (!Array.isArray(rawItems) || !Number.isInteger(total) || total < 0 ||
      !Number.isInteger(page) || page < 1 || !Number.isInteger(pageSize) || pageSize < 1) {
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

/**
 * Fetch dashboard statistics for admin overview.
 *
 * Includes metrics like total users, active subscriptions, revenue data, etc.
 *
 * @returns Promise resolving to dashboard statistics object
 */
export async function getGrowthDashboard(days: 7 | 14 | 30 = 7): Promise<GrowthDashboard> {
  return api.get<GrowthDashboard>(`${ADMIN_BASE}/dashboard/growth?days=${days}`);
}

export async function getDashboardStats(): Promise<DashboardStats> {
  const payload = await api.get<unknown>(`${ADMIN_BASE}/dashboard/stats`);
  const payloadRecord = resolvePayloadRecord(payload);

  return {
    total_users: asNumber(payloadRecord.total_users, DEFAULT_DASHBOARD_STATS.total_users),
    active_users: asNumber(payloadRecord.active_users, DEFAULT_DASHBOARD_STATS.active_users),
    new_users_today: asNumber(payloadRecord.new_users_today, DEFAULT_DASHBOARD_STATS.new_users_today),
    total_projects: asNumber(payloadRecord.total_projects, DEFAULT_DASHBOARD_STATS.total_projects),
    total_inspirations: asNumber(payloadRecord.total_inspirations, DEFAULT_DASHBOARD_STATS.total_inspirations),
    pending_inspirations: asNumber(payloadRecord.pending_inspirations, DEFAULT_DASHBOARD_STATS.pending_inspirations),
    active_subscriptions: asNumber(payloadRecord.active_subscriptions, DEFAULT_DASHBOARD_STATS.active_subscriptions),
    pro_users: asNumber(payloadRecord.pro_users, DEFAULT_DASHBOARD_STATS.pro_users),
    total_points_in_circulation: asNumber(
      payloadRecord.total_points_in_circulation,
      DEFAULT_DASHBOARD_STATS.total_points_in_circulation
    ),
    today_check_ins: asNumber(payloadRecord.today_check_ins, DEFAULT_DASHBOARD_STATS.today_check_ins),
    active_invite_codes: asNumber(payloadRecord.active_invite_codes, DEFAULT_DASHBOARD_STATS.active_invite_codes),
    week_referrals: asNumber(payloadRecord.week_referrals, DEFAULT_DASHBOARD_STATS.week_referrals),
  };
}

/**
 * Fetch activation funnel statistics for admin dashboard.
 *
 * @param days - Rolling window in days (1-90)
 * @returns Promise resolving to activation funnel metrics
 */
export async function getActivationFunnel(days = 7): Promise<ActivationFunnelStats> {
  const safeDays = Number.isFinite(days) ? Math.min(Math.max(Math.floor(days), 1), 90) : 7;
  const payload = await api.get<unknown>(`${ADMIN_BASE}/dashboard/activation-funnel?days=${safeDays}`);
  const payloadRecord = resolvePayloadRecord(payload);
  const rawSteps = pickArray<unknown>(payload, ["steps"]);

  return {
    window_days: asNumber(payloadRecord.window_days, DEFAULT_ACTIVATION_FUNNEL.window_days),
    period_start: asText(payloadRecord.period_start, DEFAULT_ACTIVATION_FUNNEL.period_start),
    period_end: asText(payloadRecord.period_end, DEFAULT_ACTIVATION_FUNNEL.period_end),
    activation_rate: asNumber(payloadRecord.activation_rate, DEFAULT_ACTIVATION_FUNNEL.activation_rate),
    steps: rawSteps.map((step) => {
      const rawStep = resolvePayloadRecord(step);
      return {
        event_name: asText(rawStep.event_name, ""),
        label: asText(rawStep.label, ""),
        users: asNumber(rawStep.users, 0),
        conversion_from_previous:
          rawStep.conversion_from_previous === null
            ? null
            : asNumber(rawStep.conversion_from_previous, 0),
        drop_off_from_previous:
          rawStep.drop_off_from_previous === null
            ? null
            : asNumber(rawStep.drop_off_from_previous, 0),
      };
    }),
  };
}

/**
 * Fetch upgrade conversion attribution stats grouped by source.
 *
 * @param days - Rolling window in days (1-90)
 * @returns Promise resolving to attribution conversion metrics
 */
export async function getUpgradeConversionStats(days = 7): Promise<UpgradeConversionStats> {
  const safeDays = Number.isFinite(days) ? Math.min(Math.max(Math.floor(days), 1), 90) : 7;
  const payload = await api.get<unknown>(`${ADMIN_BASE}/dashboard/upgrade-conversion?days=${safeDays}`);
  const payloadRecord = resolvePayloadRecord(payload);
  const rawSources = pickArray<unknown>(payload, ["sources"]);
  const rawChannels = pickArray<unknown>(payload, ["channels"]);

  return {
    window_days: asNumber(payloadRecord.window_days, DEFAULT_UPGRADE_CONVERSION.window_days),
    period_start: asText(payloadRecord.period_start, DEFAULT_UPGRADE_CONVERSION.period_start),
    period_end: asText(payloadRecord.period_end, DEFAULT_UPGRADE_CONVERSION.period_end),
    total_conversions: asNumber(
      payloadRecord.total_conversions,
      DEFAULT_UPGRADE_CONVERSION.total_conversions
    ),
    paid_conversions: asNumber(
      payloadRecord.paid_conversions,
      DEFAULT_UPGRADE_CONVERSION.paid_conversions
    ),
    channels: rawChannels.map((item) => {
      const raw = resolvePayloadRecord(item);
      return {
        channel: asText(raw.channel, "other"),
        conversions: asNumber(raw.conversions, 0),
        paid: raw.paid === true,
      };
    }),
    unattributed_conversions: asNumber(
      payloadRecord.unattributed_conversions,
      DEFAULT_UPGRADE_CONVERSION.unattributed_conversions
    ),
    sources: rawSources.map((item) => {
      const raw = resolvePayloadRecord(item);
      return {
        source: asText(raw.source, ""),
        conversions: asNumber(raw.conversions, 0),
        share: asNumber(raw.share, 0),
      };
    }),
  };
}

/**
 * Fetch upgrade funnel expose/click/conversion stats grouped by source.
 *
 * @param days - Rolling window in days (1-90)
 * @returns Promise resolving to upgrade funnel overview metrics
 */
export async function getUpgradeFunnelStats(days = 7): Promise<UpgradeFunnelStats> {
  const safeDays = Number.isFinite(days) ? Math.min(Math.max(Math.floor(days), 1), 90) : 7;
  const payload = await api.get<unknown>(`${ADMIN_BASE}/dashboard/upgrade-funnel?days=${safeDays}`);
  const payloadRecord = resolvePayloadRecord(payload);
  const rawTotals = resolvePayloadRecord(payloadRecord.totals);
  const rawSources = pickArray<unknown>(payload, ["sources"]);

  return {
    window_days: asNumber(payloadRecord.window_days, DEFAULT_UPGRADE_FUNNEL.window_days),
    period_start: asText(payloadRecord.period_start, DEFAULT_UPGRADE_FUNNEL.period_start),
    period_end: asText(payloadRecord.period_end, DEFAULT_UPGRADE_FUNNEL.period_end),
    totals: {
      expose: asNumber(rawTotals.expose, DEFAULT_UPGRADE_FUNNEL.totals.expose),
      click: asNumber(rawTotals.click, DEFAULT_UPGRADE_FUNNEL.totals.click),
      conversion: asNumber(rawTotals.conversion, DEFAULT_UPGRADE_FUNNEL.totals.conversion),
    },
    sources: rawSources.map((item) => {
      const raw = resolvePayloadRecord(item);
      return {
        source: asText(raw.source, ""),
        exposes: asNumber(raw.exposes, 0),
        clicks: asNumber(raw.clicks, 0),
        conversions: asNumber(raw.conversions, 0),
        click_through_rate: asNumber(raw.click_through_rate, 0),
        conversion_rate_from_click: asNumber(raw.conversion_rate_from_click, 0),
        conversion_rate_from_expose: asNumber(raw.conversion_rate_from_expose, 0),
      };
    }),
  };
}

// ==================== Subscription Plans API ====================

/**
 * Fetch all available subscription plans.
 *
 * @returns Promise resolving to array of subscription plan objects
 */
export async function getPlans(): Promise<SubscriptionPlan[]> {
  const payload = await api.get<unknown>(`${ADMIN_BASE}/plans`);
  return pickArray<unknown>(payload, ["items", "plans", "tiers"]).map(normalizeSubscriptionPlan);
}

/**
 * Update a subscription plan's configuration.
 *
 * @param planId - Plan ID (UUID string)
 * @param data - Partial plan data to update
 * @returns Promise resolving to updated subscription plan object
 */
export async function updatePlan(
  planId: string,
  data: PlanUpdateRequest
): Promise<SubscriptionPlan> {
  return api.put<SubscriptionPlan>(`${ADMIN_BASE}/plans/${planId}`, data);
}

// ==================== Inspirations (Admin) API ====================

/** Paginated response for admin inspiration listings. */
export interface AdminInspirationsListResponse {
  /** Array of inspiration objects with admin-visible fields */
  items: AdminInspiration[];
  /** Total number of inspirations matching the query */
  total: number;
}

/**
 * Fetch all inspirations with admin-level visibility.
 *
 * Unlike the public API, this returns inspirations of all statuses.
 *
 * @param params - Optional filter parameters for status, source, and pagination
 * @returns Promise resolving to paginated inspirations list
 */
export async function getInspirations(params?: {
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
  const url = queryString ? `${ADMIN_BASE}/inspirations?${queryString}` : `${ADMIN_BASE}/inspirations`;

  const payload = await api.get<unknown>(url);
  const payloadRecord = resolvePayloadRecord(payload);
  const items = pickArray<unknown>(payload, ["items", "inspirations"]).map(normalizeInspiration);

  return {
    items,
    total: asNumber(payloadRecord.total, items.length),
  };
}

/**
 * Review an inspiration submission (approve or reject).
 *
 * @param id - Inspiration ID (UUID string)
 * @param approve - Whether to approve (true) or reject (false) the inspiration
 * @param reason - Rejection reason (required when approve is false)
 * @returns Promise resolving to success message
 */
export async function reviewInspiration(
  id: string,
  approve: boolean,
  reason?: string
): Promise<{ message: string }> {
  return api.post<{ message: string }>(`${ADMIN_BASE}/inspirations/${id}/review`, {
    approve,
    rejection_reason: reason,
  });
}

/**
 * Update an inspiration's properties.
 *
 * @param id - Inspiration ID (UUID string)
 * @param data - Partial inspiration data to update
 * @returns Promise resolving to updated inspiration object
 */
export async function updateInspiration(
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

/**
 * Permanently delete an inspiration.
 *
 * @param id - Inspiration ID (UUID string)
 * @returns Promise resolving to success message
 */
export async function deleteInspiration(id: string): Promise<{ message: string }> {
  return api.delete<{ message: string }>(`${ADMIN_BASE}/inspirations/${id}`);
}

// ==================== Feedback (Admin) API ====================

export async function getFeedbackList(params?: {
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
  const payloadRecord = resolvePayloadRecord(payload);
  const items = pickArray<unknown>(payload, ["items", "feedback"]).map(normalizeAdminFeedback);

  return {
    items,
    total: asNumber(payloadRecord.total, items.length),
  };
}

export async function updateFeedbackStatus(
  feedbackId: string,
  status: AdminFeedbackStatus,
): Promise<AdminFeedbackItem> {
  const payload = await api.patch<unknown>(`${ADMIN_BASE}/feedback/${feedbackId}/status`, { status });
  return normalizeAdminFeedback(payload);
}

export async function getFeedbackScreenshotBlob(feedbackId: string): Promise<Blob> {
  const entryAccess = getAccessToken();
  const entryRefresh = localStorage.getItem("refresh_token");
  const endpoint = `${ADMIN_BASE}/feedback/${feedbackId}/screenshot`;
  const language = localStorage.getItem("zenstory-language") || "zh";

  const fetchOnce = async (accessToken: string | null): Promise<Response> => {
    return fetch(`${getApiBase()}${endpoint}`, {
      method: "GET",
      headers: {
        ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
        "Accept-Language": language,
      },
    });
  };

  let response = await fetchOnce(entryAccess);
  if (response.status === 401 && entryAccess && entryRefresh) {
    let ownedSession = resolveOwnedAuthSession(entryAccess, entryRefresh);
    if (ownedSession?.accessToken && ownedSession.refreshToken) {
      if (ownedSession.accessToken === entryAccess && ownedSession.refreshToken === entryRefresh) {
        const refreshed = await tryRefreshToken();
        ownedSession = refreshed ? resolveOwnedAuthSession(entryAccess, entryRefresh) : null;
      }
      if (ownedSession?.accessToken && ownedSession.refreshToken) {
        response = await fetchOnce(ownedSession.accessToken);
      }
    }
  }

  if (!response.ok) {
    let errorMessage = "ERR_INTERNAL_SERVER_ERROR";
    try {
      const errorData = await response.json();
      errorMessage = resolveApiErrorMessage(errorData, errorMessage);
    } catch {
      // keep fallback message
    }
    throw new ApiError(response.status, errorMessage);
  }

  return response.blob();
}

// ==================== Points Management API ====================

/**
 * Fetch overall points system statistics.
 *
 * @returns Promise resolving to points system statistics
 */
export async function getPointsStats(): Promise<PointsStats> {
  const payload = await api.get<unknown>(`${ADMIN_BASE}/points/stats`);
  return normalizePointsStats(payload);
}

/**
 * Fetch points balance details for a specific user.
 *
 * @param userId - User ID (UUID string)
 * @returns Promise resolving to user's points balance details
 */
export async function getUserPoints(userId: string): Promise<AdminPointsBalance> {
  const payload = await api.get<unknown>(`${ADMIN_BASE}/points/${userId}`);
  return normalizeUserPoints(payload);
}

/**
 * Fetch transaction history for a user's points.
 *
 * @param userId - User ID (UUID string)
 * @param params - Optional pagination parameters
 * @returns Promise resolving to paginated transaction history
 */
export async function getUserPointsTransactions(
  userId: string,
  params?: { page?: number; page_size?: number }
): Promise<PointsTransactionsResponse> {
  const searchParams = new URLSearchParams();
  if (params?.page) searchParams.set("page", String(params.page));
  if (params?.page_size) searchParams.set("page_size", String(params.page_size));
  const payload = await api.get<unknown>(
    `${ADMIN_BASE}/points/${userId}/transactions?${searchParams.toString()}`
  );
  const payloadRecord = resolvePayloadRecord(payload);
  const items = pickArray<unknown>(payload, ["items", "transactions"]).map(normalizePointsTransaction);

  return {
    items,
    total: asNumber(payloadRecord.total, items.length),
    page: asNumber(payloadRecord.page, params?.page ?? 1),
    page_size: asNumber(payloadRecord.page_size, (params?.page_size ?? items.length) || 20),
  };
}

/**
 * Manually adjust a user's points balance.
 *
 * @param userId - User ID (UUID string)
 * @param data - Adjustment details including amount and reason
 * @returns Promise resolving to success message and new balance
 */
export async function adjustUserPoints(
  userId: string,
  data: PointsAdjustRequest
): Promise<{ message: string; new_balance: number }> {
  return api.post<{ message: string; new_balance: number }>(
    `${ADMIN_BASE}/points/${userId}/adjust`,
    data
  );
}

// ==================== Check-in Stats API ====================

/**
 * Fetch overall check-in system statistics.
 *
 * @returns Promise resolving to check-in statistics
 */
export async function getCheckInStats(): Promise<CheckInStats> {
  const payload = await api.get<unknown>(`${ADMIN_BASE}/check-in/stats`);
  return normalizeCheckInStats(payload);
}

/**
 * Fetch paginated list of check-in records.
 *
 * @param params - Optional filter parameters including pagination and user filter
 * @returns Promise resolving to paginated check-in records
 */
export async function getCheckInRecords(params?: {
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
  const payloadRecord = resolvePayloadRecord(payload);
  const items = pickArray<unknown>(payload, ["items", "records"]).map(normalizeCheckInRecord);

  return {
    items,
    total: asNumber(payloadRecord.total, items.length),
    page: asNumber(payloadRecord.page, params?.page ?? 1),
    page_size: asNumber(payloadRecord.page_size, (params?.page_size ?? items.length) || 20),
  };
}

// ==================== Referral Management API ====================

/**
 * Fetch overall referral system statistics.
 *
 * @returns Promise resolving to referral statistics
 */
export async function getReferralStats(): Promise<ReferralStats> {
  const payload = await api.get<unknown>(`${ADMIN_BASE}/referrals/stats`);
  return normalizeReferralStats(payload);
}

/**
 * Create a new invite code from admin referrals page.
 *
 * @returns Promise resolving to the newly generated invite code
 */
export async function createAdminInviteCode(): Promise<InviteCodesResponse["items"][number]> {
  const payload = await api.post<unknown>(`${ADMIN_BASE}/invites`);
  return normalizeInviteCode(payload);
}

/**
 * Fetch paginated list of invite codes.
 *
 * @param params - Optional filter parameters including pagination and active status
 * @returns Promise resolving to paginated invite codes list
 */
export async function getInviteCodes(params?: {
  page?: number;
  page_size?: number;
  is_active?: boolean;
}): Promise<InviteCodesResponse> {
  const searchParams = new URLSearchParams();
  if (params?.page) searchParams.set("page", String(params.page));
  if (params?.page_size) searchParams.set("page_size", String(params.page_size));
  if (params?.is_active !== undefined) searchParams.set("is_active", String(params.is_active));
  const payload = await api.get<unknown>(`${ADMIN_BASE}/invites?${searchParams.toString()}`);
  const payloadRecord = resolvePayloadRecord(payload);
  const items = pickArray<unknown>(payload, ["items", "codes"]).map(normalizeInviteCode);

  return {
    items,
    total: asNumber(payloadRecord.total, items.length),
    page: asNumber(payloadRecord.page, params?.page ?? 1),
    page_size: asNumber(payloadRecord.page_size, (params?.page_size ?? items.length) || 20),
  };
}

/**
 * Fetch paginated list of referral reward distribution records.
 *
 * @param params - Optional pagination parameters
 * @returns Promise resolving to paginated rewards list
 */
export async function getReferralRewards(params?: {
  page?: number;
  page_size?: number;
}): Promise<RewardsResponse> {
  const searchParams = new URLSearchParams();
  if (params?.page) searchParams.set("page", String(params.page));
  if (params?.page_size) searchParams.set("page_size", String(params.page_size));
  const payload = await api.get<unknown>(`${ADMIN_BASE}/referrals/rewards?${searchParams.toString()}`);
  const payloadRecord = resolvePayloadRecord(payload);
  const items = pickArray<unknown>(payload, ["items", "rewards"]).map(normalizeRewardRecord);

  return {
    items,
    total: asNumber(payloadRecord.total, items.length),
    page: asNumber(payloadRecord.page, params?.page ?? 1),
    page_size: asNumber(payloadRecord.page_size, (params?.page_size ?? items.length) || 20),
  };
}

// ==================== Quota Usage API ====================

/**
 * Fetch overall quota usage statistics across the system.
 *
 * @returns Promise resolving to quota usage statistics
 */
export async function getQuotaUsageStats(): Promise<QuotaUsageStats> {
  const payload = await api.get<unknown>(`${ADMIN_BASE}/quota/usage`);
  return normalizeQuotaUsageStats(payload);
}

/**
 * Fetch quota usage details for a specific user.
 *
 * @param userId - User ID (UUID string)
 * @returns Promise resolving to user's quota usage details
 */
export async function getUserQuota(userId: string): Promise<UserQuotaDetail> {
  const payload = await api.get<unknown>(`${ADMIN_BASE}/quota/${userId}`);
  return normalizeUserQuota(payload);
}

// ==================== Export ====================

/**
 * Admin API namespace object providing access to all administrative functions.
 *
 * Organized by feature area for convenient access:
 * - User management
 * - Prompt configuration
 * - Skill review
 * - Redemption codes
 * - Subscription management
 * - Audit logs
 * - Dashboard statistics
 * - Points and check-in systems
 * - Referral management
 * - Quota monitoring
 */
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
  getCode,
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
  getUserDailyUsage,

  // 审计日志
  getAuditLogs,

  // Dashboard
  getDashboardStats,
  getGrowthDashboard,
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
