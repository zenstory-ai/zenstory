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
  UxHealthDashboard,
  ActivationFunnelStats,
  UpgradeConversionStats,
  UpgradeFunnelStats,
  PlanUpdateRequest,
  AdminInspiration,
  AdminFeedbackItem,
  AdminFeedbackListResponse,
  AdminFeedbackStatus,
  ChatVoteFilter,
  ChatVotesListResponse,
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
} from "../types/admin";
import type { SubscriptionPlan } from "../types/subscription";
import type { PaymentOrder } from "../types/payment";

export type {
  AuditLog,
  PendingSkill,
  RedemptionCode,
  SkillReviewResource,
  SkillReviewStatus,
} from "../types/admin";

const ADMIN_BASE = "/api/admin";

function clampWindowDays(days: number): number {
  return Number.isFinite(days) ? Math.min(Math.max(Math.floor(days), 1), 90) : 7;
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

  const payload = await api.get<{ items: User[]; total: number }>(
    `${ADMIN_BASE}/users?${params.toString()}`
  );
  return { users: payload.items, total: payload.total };
}

async function getUser(id: string): Promise<User> {
  return api.get<User>(`${ADMIN_BASE}/users/${encodeURIComponent(id)}`);
}

async function updateUser(id: string, data: UserUpdateRequest): Promise<User> {
  return api.put<User>(`${ADMIN_BASE}/users/${id}`, data);
}

async function deleteUser(id: string): Promise<User | { message: string }> {
  return api.delete<User | { message: string }>(`${ADMIN_BASE}/users/${id}`);
}

// ==================== Prompt 管理 API ====================

async function getPrompts(): Promise<PromptsListResponse> {
  return api.get<PromptsListResponse>(`${ADMIN_BASE}/prompts`);
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
  return api.get<PendingSkill[]>(
    `${ADMIN_BASE}/skills/pending?status=${encodeURIComponent(status)}`
  );
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

  return api.get<CodesListResponse>(`${ADMIN_BASE}/codes?${searchParams.toString()}`);
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
  subscription: Partial<Subscription> & {
    id: string;
    user_id: string;
    plan_id?: string;
    status: string;
    current_period_start: string | null;
    current_period_end: string | null;
    created_at: string;
    updated_at: string;
  };
  plan: SubscriptionPlan | null;
  quota?: Record<string, unknown> | null;
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

  return api.get<SubscriptionsListResponse>(
    `${ADMIN_BASE}/subscriptions?${searchParams.toString()}`
  );
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
  status?: "pending" | "paid" | "refunded" | "disputed" | "failed";
  fulfillment_status?: "pending" | "succeeded" | "failed";
  payment_method?: "alipay" | "card";
  needs_attention?: boolean;
  search?: string;
  user_id?: string;
  include_needs_attention_total?: boolean;
} = {}): Promise<PaymentOrdersListResponse> {
  const query = new URLSearchParams();
  if (params.page) query.set("page", String(params.page));
  if (params.page_size) query.set("page_size", String(params.page_size));
  if (params.status) query.set("status", params.status);
  if (params.fulfillment_status) query.set("fulfillment_status", params.fulfillment_status);
  if (params.payment_method) query.set("payment_method", params.payment_method);
  if (params.needs_attention) query.set("needs_attention", "true");
  if (params.search) query.set("search", params.search);
  if (params.user_id) query.set("user_id", params.user_id);
  if (params.include_needs_attention_total === false) {
    query.set("include_needs_attention_total", "false");
  }
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

export interface SlowTailDistribution extends UsageDistribution {
  slow_count: number;
  slow_rate: number | null;
}

export interface ChapterFirstProseDistribution extends SlowTailDistribution {
  p90: number | null;
}

export interface DeviceStageLatencySlice {
  rounds: number;
  first_output_ms: SlowTailDistribution;
  chapter_first_prose_ms: ChapterFirstProseDistribution;
  client_disconnected_rounds: number;
  client_disconnected_rate: number | null;
}

export interface AiDraftSurvivalMetrics {
  eligible_drafts: number;
  survived_drafts: number;
  co_edited_drafts: number;
  deleted_or_reverted_drafts: number;
  survival_rate: number | null;
  co_edit_rate: number | null;
  observation_hours: number;
}

export interface StageLatencyDailyRow {
  date: string;
  rounds: number;
  prepare_context_p50_ms: number | null;
  first_output_p50_ms: number | null;
  first_output_p95_ms: number | null;
  first_output_slow_rate: number | null;
  chapter_first_prose_p50_ms: number | null;
  chapter_first_prose_p95_ms: number | null;
  chapter_first_prose_slow_rate: number | null;
  file_body_pending_rate: number | null;
  leaked_tool_call_rate: number | null;
  user_stopped_rate: number | null;
  client_disconnected_rate: number | null;
}

export interface StageLatencyResponse {
  days: UsageDetailDays;
  timezone: string;
  period_start: string;
  period_end: string;
  slow_threshold_ms: number;
  total_rounds: number;
  prepare_context_ms: UsageDistribution;
  first_output_ms: SlowTailDistribution;
  chapter_first_prose_ms: ChapterFirstProseDistribution;
  first_body_persisted_ms: UsageDistribution;
  file_body_pending_rounds: number;
  file_body_pending_rate: number | null;
  leaked_tool_call_rounds: number;
  leaked_tool_call_rate: number | null;
  user_stopped_rounds: number;
  user_stopped_rate: number | null;
  client_disconnected_rounds: number;
  client_disconnected_rate: number | null;
  desktop_vs_mobile: {
    desktop: DeviceStageLatencySlice;
    mobile: DeviceStageLatencySlice;
    unknown: DeviceStageLatencySlice;
  };
  ai_draft_survival: AiDraftSurvivalMetrics;
  daily: StageLatencyDailyRow[];
}

async function getUsageSummary(
  window: UsageWindow,
  options?: { refresh?: boolean }
): Promise<UsageSummaryResponse> {
  const suffix = options?.refresh ? "&refresh=true" : "";
  return api.get<UsageSummaryResponse>(`${ADMIN_BASE}/usage/summary?window=${window}${suffix}`);
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
  source?: UsageSource,
  options?: { refresh?: boolean }
): Promise<ModelMetricsResponse> {
  const query = new URLSearchParams({ window });
  if (source) query.set("source", source);
  if (options?.refresh) query.set("refresh", "true");
  return api.get<ModelMetricsResponse>(`${ADMIN_BASE}/usage/models?${query.toString()}`);
}

async function getUsageStageLatency(
  days: UsageDetailDays = 7,
  options?: { refresh?: boolean }
): Promise<StageLatencyResponse> {
  const suffix = options?.refresh ? "&refresh=true" : "";
  return api.get<StageLatencyResponse>(`${ADMIN_BASE}/usage/stage-latency?days=${days}${suffix}`);
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

type RawAuditLog = Omit<AuditLog, "admin_name"> & { admin_name: string | null };

async function getAuditLogs(params?: {
  page?: number;
  page_size?: number;
  resource_type?: string;
  action?: string;
  include_total?: boolean;
}): Promise<AuditLogsResponse> {
  const searchParams = new URLSearchParams();
  if (params?.page) searchParams.set("page", String(params.page));
  if (params?.page_size) searchParams.set("page_size", String(params.page_size));
  if (params?.resource_type) searchParams.set("resource_type", params.resource_type);
  if (params?.action) searchParams.set("action", params.action);
  if (params?.include_total === false) searchParams.set("include_total", "false");

  const payload = await api.get<Omit<AuditLogsResponse, "items"> & { items: RawAuditLog[] }>(
    `${ADMIN_BASE}/audit-logs?${searchParams.toString()}`
  );
  return {
    ...payload,
    items: payload.items.map((log) => ({
      ...log,
      admin_name: log.admin_name || "-",
    })),
  };
}

// ==================== Dashboard Stats API ====================

async function getGrowthDashboard(
  days: 7 | 14 | 30 = 7,
  options?: { refresh?: boolean }
): Promise<GrowthDashboard> {
  const suffix = options?.refresh ? "&refresh=true" : "";
  return api.get<GrowthDashboard>(`${ADMIN_BASE}/dashboard/growth?days=${days}${suffix}`);
}

async function getUxHealthDashboard(
  days: 7 | 14 | 30 = 7,
  options?: { refresh?: boolean }
): Promise<UxHealthDashboard> {
  const suffix = options?.refresh ? "&refresh=true" : "";
  return api.get<UxHealthDashboard>(`${ADMIN_BASE}/dashboard/ux-health?days=${days}${suffix}`);
}

async function getAcquisitionStats(
  days: 7 | 14 | 30 | 90 = 30,
  options?: { refresh?: boolean }
): Promise<AcquisitionStats> {
  const suffix = options?.refresh ? "&refresh=true" : "";
  return api.get<AcquisitionStats>(`${ADMIN_BASE}/dashboard/acquisition?days=${days}${suffix}`);
}

async function getDashboardStats(options?: {
  includeCommercialization?: boolean;
  refresh?: boolean;
}): Promise<DashboardStats> {
  const params = new URLSearchParams();
  if (options?.includeCommercialization === false) {
    params.set("include_commercialization", "false");
  }
  if (options?.refresh) {
    params.set("refresh", "true");
  }
  const suffix = params.toString();
  return api.get<DashboardStats>(
    suffix ? `${ADMIN_BASE}/dashboard/stats?${suffix}` : `${ADMIN_BASE}/dashboard/stats`
  );
}

async function getActivationFunnel(
  days = 7,
  options?: { refresh?: boolean }
): Promise<ActivationFunnelStats> {
  const suffix = options?.refresh ? "&refresh=true" : "";
  return api.get<ActivationFunnelStats>(
    `${ADMIN_BASE}/dashboard/activation-funnel?days=${clampWindowDays(days)}${suffix}`
  );
}

async function getUpgradeConversionStats(
  days = 7,
  options?: { refresh?: boolean }
): Promise<UpgradeConversionStats> {
  const suffix = options?.refresh ? "&refresh=true" : "";
  return api.get<UpgradeConversionStats>(
    `${ADMIN_BASE}/dashboard/upgrade-conversion?days=${clampWindowDays(days)}${suffix}`
  );
}

async function getUpgradeFunnelStats(
  days = 7,
  options?: { refresh?: boolean }
): Promise<UpgradeFunnelStats> {
  const suffix = options?.refresh ? "&refresh=true" : "";
  return api.get<UpgradeFunnelStats>(
    `${ADMIN_BASE}/dashboard/upgrade-funnel?days=${clampWindowDays(days)}${suffix}`
  );
}

// ==================== Subscription Plans API ====================

async function getPlans(): Promise<SubscriptionPlan[]> {
  return api.get<SubscriptionPlan[]>(`${ADMIN_BASE}/plans`);
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

  return api.get<AdminInspirationsListResponse>(url);
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

  return api.get<AdminFeedbackListResponse>(url);
}

async function updateFeedbackStatus(
  feedbackId: string,
  status: AdminFeedbackStatus
): Promise<AdminFeedbackItem> {
  return api.patch<AdminFeedbackItem>(`${ADMIN_BASE}/feedback/${feedbackId}/status`, {
    status,
  });
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

async function getChatVotes(params?: {
  days?: 7 | 14 | 30;
  vote?: ChatVoteFilter;
  page?: number;
  page_size?: number;
}): Promise<ChatVotesListResponse> {
  const searchParams = new URLSearchParams();
  if (params?.days) searchParams.set("days", String(params.days));
  if (params?.vote) searchParams.set("vote", params.vote);
  if (params?.page) searchParams.set("page", String(params.page));
  if (params?.page_size) searchParams.set("page_size", String(params.page_size));

  const queryString = searchParams.toString();
  const url = queryString
    ? `${ADMIN_BASE}/feedback/chat-votes?${queryString}`
    : `${ADMIN_BASE}/feedback/chat-votes`;

  return api.get<ChatVotesListResponse>(url);
}

// ==================== Points Management API ====================

async function getPointsStats(): Promise<PointsStats> {
  return api.get<PointsStats>(`${ADMIN_BASE}/points/stats`);
}

async function getUserPoints(userId: string): Promise<AdminPointsBalance> {
  return api.get<AdminPointsBalance>(`${ADMIN_BASE}/points/${userId}`);
}

async function getUserPointsTransactions(
  userId: string,
  params?: { page?: number; page_size?: number }
): Promise<PointsTransactionsResponse> {
  const searchParams = new URLSearchParams();
  if (params?.page) searchParams.set("page", String(params.page));
  if (params?.page_size) searchParams.set("page_size", String(params.page_size));
  return api.get<PointsTransactionsResponse>(
    `${ADMIN_BASE}/points/${userId}/transactions?${searchParams.toString()}`
  );
}

export interface AdjustUserPointsResponse {
  success?: boolean;
  message?: string;
  old_balance?: number;
  new_balance: number;
  transaction?: PointsTransactionsResponse["items"][number];
}

async function adjustUserPoints(
  userId: string,
  data: PointsAdjustRequest
): Promise<AdjustUserPointsResponse> {
  return api.post<AdjustUserPointsResponse>(
    `${ADMIN_BASE}/points/${userId}/adjust`,
    data
  );
}

// ==================== Check-in Stats API ====================

async function getCheckInStats(): Promise<CheckInStats> {
  return api.get<CheckInStats>(`${ADMIN_BASE}/check-in/stats`);
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
  return api.get<CheckInRecordsResponse>(
    `${ADMIN_BASE}/check-in/records?${searchParams.toString()}`
  );
}

// ==================== Referral Management API ====================

async function getReferralStats(): Promise<ReferralStats> {
  return api.get<ReferralStats>(`${ADMIN_BASE}/referrals/stats`);
}

async function createAdminInviteCode(): Promise<InviteCodesResponse["items"][number]> {
  return api.post<InviteCodesResponse["items"][number]>(`${ADMIN_BASE}/invites`);
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
  return api.get<InviteCodesResponse>(`${ADMIN_BASE}/invites?${searchParams.toString()}`);
}

async function getReferralRewards(params?: {
  page?: number;
  page_size?: number;
}): Promise<RewardsResponse> {
  const searchParams = new URLSearchParams();
  if (params?.page) searchParams.set("page", String(params.page));
  if (params?.page_size) searchParams.set("page_size", String(params.page_size));
  return api.get<RewardsResponse>(
    `${ADMIN_BASE}/referrals/rewards?${searchParams.toString()}`
  );
}

// ==================== Quota Usage API ====================

async function getQuotaUsageStats(): Promise<QuotaUsageStats> {
  return api.get<QuotaUsageStats>(`${ADMIN_BASE}/quota/usage`);
}

async function getUserQuota(userId: string): Promise<UserQuotaDetail> {
  return api.get<UserQuotaDetail>(`${ADMIN_BASE}/quota/${userId}`);
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
  getUsageStageLatency,
  getUserDailyUsage,

  // 审计日志
  getAuditLogs,

  // Dashboard
  getDashboardStats,
  getGrowthDashboard,
  getUxHealthDashboard,
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
  getChatVotes,

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
