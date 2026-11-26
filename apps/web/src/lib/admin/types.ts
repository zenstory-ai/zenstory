import type {
  AuditLog,
  RedemptionCode,
} from "../../types/admin";
import type { PaymentOrder } from "../../types/payment";
import type { SubscriptionPlan } from "../../types/subscription";

export type {
  AuditLog,
  PendingSkill,
  RedemptionCode,
  SkillReviewResource,
  SkillReviewStatus,
} from "../../types/admin";

// ==================== 兑换码管理类型 ====================

/** Paginated response for redemption code listings. */
export interface CodesListResponse {
  items: RedemptionCode[];
  total: number;
  page: number;
  page_size: number;
}

export interface CodeBatchCreateResponse {
  codes: string[];
  count?: number;
  created?: number;
  code_type?: "single_use" | "multi_use";
  max_uses?: number;
}

// ==================== 订阅与订单类型 ====================

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

// ==================== 用量与成本类型 ====================

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

// ==================== 审计日志响应类型 ====================

/** Paginated response for audit log listings. */
export interface AuditLogsResponse {
  items: AuditLog[];
  total: number;
  page: number;
  page_size: number;
}
