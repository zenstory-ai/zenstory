/**
 * Admin types and interfaces
 */

import type { User } from "../contexts/AuthContext";

// Re-export User from AuthContext for convenience
export type { User };

/**
 * User update request
 */
export interface UserUpdateRequest {
  username?: string;
  email?: string;
  is_active?: boolean;
  is_superuser?: boolean;
}

/**
 * Users list response. `total` counts every user matching the search filter;
 * it is null when an older API returned a bare array without a count.
 */
export interface UsersListResponse {
  users: User[];
  total: number | null;
}

/**
 * System Prompt Configuration
 */
export interface SystemPromptConfig {
  id?: string;
  project_type: string;
  role_definition: string;
  capabilities: string;
  directory_structure: string | null;
  content_structure: string | null;
  file_types: string | null;
  writing_guidelines: string | null;
  include_dialogue_guidelines: boolean;
  primary_content_type: string | null;
  is_active: boolean;
  version: number;
  created_by?: string | null;
  updated_by?: string | null;
  created_at?: string;
  updated_at?: string;
}

/**
 * Prompt configuration request
 */
export interface PromptConfigRequest {
  role_definition: string;
  capabilities: string;
  directory_structure: string;
  content_structure: string;
  file_types: string;
  writing_guidelines: string;
  include_dialogue_guidelines: boolean;
  is_active: boolean;
  expected_version?: number;
}

export interface PromptReloadResult {
  message: string;
  source: "primary_database";
  count: number;
  version: number;
}

/**
 * Prompts list response - 后端直接返回数组
 */
export type PromptsListResponse = SystemPromptConfig[];

// ==================== Skill Review ====================

export type SkillReviewStatus = "pending" | "approved" | "rejected" | "unpublished";

export interface PendingSkill {
  id: string;
  name: string;
  description: string | null;
  instructions: string;
  category: string;
  /** Trigger words copied from the author's skill */
  tags: string[];
  /** Standard SKILL.md frontmatter extras (license / allowed_tools / metadata) */
  skill_metadata: Record<string, unknown>;
  /** Number of references/ and assets/ files that ship with the skill */
  resource_count: number;
  source: string;
  author_id: string | null;
  author_name: string | null;
  status: SkillReviewStatus;
  reviewed_by: string | null;
  reviewer_name: string | null;
  reviewed_at: string | null;
  rejection_reason: string | null;
  created_at: string;
}

/** One resource file of a skill under review, with its raw content */
export interface SkillReviewResource {
  path: string;
  size: number;
  content: string;
}

// ==================== Dashboard Stats ====================

/**
 * Dashboard statistics for admin overview
 */
export interface DashboardStats {
  total_users: number;
  active_users: number;
  new_users_today: number;
  total_projects: number;
  author_created_projects: number;
  auto_initialized_projects: number;
  /** Paid (non-free) subscriptions whose period has not ended. */
  active_subscriptions: number;
  /** Users on a running Pro subscription. */
  pro_users: number;
}

export interface ActivationFunnelStep {
  event_name: string;
  label: string;
  users: number;
  conversion_from_previous: number | null;
  drop_off_from_previous: number | null;
}

export interface ActivationFunnelStats {
  window_days: number;
  period_start: string;
  period_end: string;
  steps: ActivationFunnelStep[];
  activation_rate: number;
}

export interface UpgradeConversionSource {
  source: string;
  conversions: number;
  share: number;
}

export interface UpgradeConversionChannel {
  /** How the plan was granted: zpay, redemption_code, points_redemption, admin_update, other. */
  channel: string;
  conversions: number;
  /** True only when money was received (Zpay). */
  paid: boolean;
}

export interface UpgradeConversionStats {
  window_days: number;
  period_start: string;
  period_end: string;
  total_conversions: number;
  paid_conversions: number;
  unattributed_conversions: number;
  sources: UpgradeConversionSource[];
  channels: UpgradeConversionChannel[];
  /** Conversions by authors who hit the daily AI message limit before converting. */
  after_ai_quota_wall_conversions: number;
  /** Of those, conversions backed by an actual payment. */
  paid_after_ai_quota_wall_conversions: number;
}

export interface UpgradeFunnelTotals {
  expose: number;
  click: number;
  conversion: number;
}

export interface UpgradeFunnelSource {
  source: string;
  exposes: number;
  clicks: number;
  conversions: number;
  click_through_rate: number;
  conversion_rate_from_click: number;
  conversion_rate_from_expose: number;
}

export interface UpgradeFunnelSurface {
  source: string;
  surface: string;
  exposes: number;
  exposed_users: number;
  clicks: number;
  clicking_users: number;
  page_arrivals: number;
}

export interface UpgradeFunnelStats {
  window_days: number;
  period_start: string;
  period_end: string;
  totals: UpgradeFunnelTotals;
  sources: UpgradeFunnelSource[];
  surfaces?: UpgradeFunnelSurface[];
}

// ==================== Plan Update ====================

/**
 * Request for updating subscription plan
 */
export interface PlanUpdateRequest {
  display_name?: string;
  display_name_en?: string;
  price_monthly_cents?: number;
  price_yearly_cents?: number;
  price_monthly_usd_cents?: number;
  price_yearly_usd_cents?: number;
  features?: Record<string, unknown>;
  is_active?: boolean;
}

// ==================== Admin Feedback ====================

export type AdminFeedbackStatus = "open" | "processing" | "resolved";
export type AdminFeedbackSourcePage = "dashboard" | "editor";

export interface AdminFeedbackItem {
  id: string;
  user_id: string;
  username: string;
  email: string;
  source_page: AdminFeedbackSourcePage;
  source_route: string | null;
  issue_text: string;
  trace_id: string | null;
  request_id: string | null;
  agent_run_id: string | null;
  project_id: string | null;
  agent_session_id: string | null;
  has_screenshot: boolean;
  screenshot_original_name: string | null;
  screenshot_content_type: string | null;
  screenshot_size_bytes: number | null;
  screenshot_download_url: string | null;
  status: AdminFeedbackStatus;
  created_at: string;
  updated_at: string;
}

export interface AdminFeedbackListResponse {
  items: AdminFeedbackItem[];
  total: number;
}

export type ChatVoteFilter = "all" | "up" | "down";

export interface ChatVotePresetCount {
  preset: string;
  count: number;
}

export interface ChatVoteSummary {
  up: number;
  down: number;
  total_votes: number;
  up_rate: number | null;
  preset_counts: ChatVotePresetCount[];
}

export interface ChatVoteItem {
  message_id: string;
  user_id: string;
  username: string;
  vote: "up" | "down";
  preset: string | null;
  comment: string | null;
  created_at: string;
}

export interface ChatVotesListResponse {
  days: 7 | 14 | 30;
  timezone: string;
  period_start: string;
  period_end: string;
  summary: ChatVoteSummary;
  items: ChatVoteItem[];
  total: number;
  page: number;
  page_size: number;
}

// ==================== Quota Usage Stats ====================

/**
 * 配额使用统计
 */
export interface QuotaUsageStats {
  /** Current monthly quota period (Beijing month) */
  period_start: string;
  period_end: string;
  material_decompositions: number;
  inspiration_copies: number;
  /** Custom skills created this month */
  skills_created: number;
}

/** Used vs. limit for one quota; limit -1 means unlimited. */
export interface QuotaCounter {
  used: number;
  limit: number;
  reset_at: string | null;
}

/**
 * 用户配额详情（与后端实际执行的配额一致）
 */
export interface UserQuotaDetail {
  user_id: string;
  username: string;
  email: string;
  plan_name: string;
  plan_display_name: string | null;
  plan_display_name_en: string | null;
  ai_conversations: QuotaCounter;
  material_decompositions: QuotaCounter;
  inspiration_copies: QuotaCounter;
  custom_skills: QuotaCounter;
}

/** Beijing-window growth; rates are null when the signup cohort is empty. */
export interface GrowthMetrics {
  new_users: number; ai_active_users: number; cohort_activated_users: number;
  cohort_activation_rate: number | null; paid_orders: number; revenue_cents: number;
  revenue_usd_cents?: number; revenue_by_currency?: Record<string, number>;
  paid_users: number; cohort_paid_users: number; signup_to_paid_rate: number | null;
  grant_upgrade_events: number; grant_upgrade_users: number;
  grant_channels: Array<{ channel: string; events: number; users: number }>;
}
export interface GrowthDashboard {
  days: number; timezone: string;
  current: { period_start: string; period_end: string; metrics: GrowthMetrics };
  previous: { period_start: string; period_end: string; metrics: GrowthMetrics };
  daily: Array<GrowthMetrics & { date: string }>;
  definitions: Record<string, string>;
}

export interface UxAbnormalStop {
  reason: string;
  count: number;
  rate: number | null;
}

export interface UxHealthMetrics {
  ttft_p50_ms: number | null;
  ttft_p95_ms: number | null;
  ttft_samples: number;
  ttft_slow_tail_count: number;
  ttft_slow_tail_rate: number | null;
  duration_samples: number;
  duration_slow_tail_count: number;
  duration_slow_tail_rate: number | null;
  cache_hit_tokens: number;
  cache_miss_tokens: number;
  prompt_cache_hit_rate: number | null;
  total_rounds: number;
  healthy_rounds: number;
  healthy_round_rate: number | null;
  user_stopped_rounds: number;
  user_stopped_rate: number | null;
  client_disconnected_rounds: number;
  client_disconnected_rate: number | null;
  abnormal_stop_rounds: number;
  abnormal_stop_rate: number | null;
  abnormal_stops: UxAbnormalStop[];
  ai_edit_cards: number;
  ai_edit_undone: number;
  ai_edit_undo_rate: number | null;
  ai_delete_cards: number;
  ai_delete_restored: number;
  ai_delete_restore_rate: number | null;
  feedback_up: number;
  feedback_down: number;
  feedback_total: number;
  thumbs_up_rate: number | null;
  top_dislike_presets: ChatVotePresetCount[];
  ttfv_p50_minutes: number | null;
  ttfv_samples: number;
  w1_returning_writers: number;
  w1_eligible_writers: number;
  w1_retention_rate: number | null;
  chapters_written: number;
  net_words_written: number;
  mobile_users: number;
  desktop_users: number;
  mobile_share: number | null;
}

export interface UxHealthDelta {
  ttft_p50_ms: number | null;
  ttft_p95_ms: number | null;
  ttft_slow_tail_rate: number | null;
  duration_slow_tail_rate: number | null;
  prompt_cache_hit_rate: number | null;
  healthy_round_rate: number | null;
  user_stopped_rate: number | null;
  client_disconnected_rate: number | null;
  abnormal_stop_rate: number | null;
  ai_edit_undo_rate: number | null;
  ai_delete_restore_rate: number | null;
  thumbs_up_rate: number | null;
  ttfv_p50_minutes: number | null;
  w1_retention_rate: number | null;
  chapters_written: number | null;
  net_words_written: number | null;
  mobile_share: number | null;
}

export interface UxHealthDailyRow {
  date: string;
  rounds: number;
  healthy_rate: number | null;
  undo_rate: number | null;
  ttft_p95_ms: number | null;
  thumbs_up_rate: number | null;
}

export interface UxHealthDashboard {
  days: 7 | 14 | 30;
  timezone: string;
  current: { period_start: string; period_end: string; metrics: UxHealthMetrics };
  previous: { period_start: string; period_end: string; metrics: UxHealthMetrics };
  delta: UxHealthDelta;
  daily: UxHealthDailyRow[];
  definitions: Record<string, string>;
}

export type AcquisitionChannel =
  | "search" | "ai" | "social" | "org_site" | "referral" | "campaign" | "paid" | "direct" | "unknown";

export interface AcquisitionCounts {
  signups: number;
  activated_users: number;
  activation_rate: number | null;
  paid_users: number;
  paid_rate: number | null;
}

export interface AcquisitionChannelRow extends AcquisitionCounts {
  channel: AcquisitionChannel;
}

export interface AcquisitionSourceRow extends AcquisitionChannelRow {
  /** UTM source, referrer host or internal source code; empty when none was recorded. */
  source: string;
}

export interface AcquisitionStats {
  window_days: number;
  period_start: string;
  period_end: string;
  total_signups: number;
  attributed_signups: number;
  channels: AcquisitionChannelRow[];
  sources: AcquisitionSourceRow[];
}

// ==================== Redemption Codes & Audit Logs ====================

/** Represents a redemption code for subscription or benefits. */
export interface RedemptionCode {
  id: string;
  code: string;
  tier: string;
  duration_days: number;
  code_type: string;
  max_uses: number | null;
  current_uses: number;
  is_active: boolean;
  notes: string | null;
  created_at: string;
  updated_at?: string;
}

/** Represents an admin action audit log entry. */
export interface AuditLog {
  id: string;
  admin_id: string;
  admin_name: string;
  action: string;
  resource_type: string;
  resource_id: string | null;
  old_value: Record<string, unknown> | null;
  new_value: Record<string, unknown> | null;
  ip_address: string | null;
  user_agent: string | null;
  created_at: string;
}
