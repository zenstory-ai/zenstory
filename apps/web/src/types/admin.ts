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
  directory_structure: string;
  content_structure: string;
  file_types: string;
  writing_guidelines: string;
  include_dialogue_guidelines: boolean;
  primary_content_type: string;
  is_active: boolean;
  version: number;
  created_by?: string;
  updated_by?: string;
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
  total_inspirations: number;
  pending_inspirations: number;
  /** Paid (non-free) subscriptions whose period has not ended. */
  active_subscriptions: number;
  /** Users on a running Pro subscription. */
  pro_users: number;
  // 商业化统计
  total_points_in_circulation: number;
  today_check_ins: number;
  active_invite_codes: number;
  week_referrals: number;
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

export interface UpgradeFunnelStats {
  window_days: number;
  period_start: string;
  period_end: string;
  totals: UpgradeFunnelTotals;
  sources: UpgradeFunnelSource[];
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
  features?: Record<string, unknown>;
  is_active?: boolean;
}

// ==================== Admin Inspiration ====================

/**
 * Inspiration source type
 */
export type InspirationSource = "official" | "community";

/**
 * Inspiration status
 */
export type InspirationStatus = "pending" | "approved" | "rejected";

/**
 * Admin view of Inspiration with moderation fields
 */
export interface AdminInspiration {
  id: string;
  name: string;
  description: string | null;
  cover_image?: string | null;
  tags: string[];
  source: InspirationSource;
  status: InspirationStatus;
  is_featured: boolean;
  sort_order?: number;
  copy_count: number;
  creator_id: string | null;
  creator_name?: string;
  reviewer_id?: string | null;
  reviewer_name?: string | null;
  reviewed_at?: string;
  rejection_reason?: string;
  original_project_id?: string | null;
  created_at: string;
  updated_at: string;
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

// ==================== Points Management ====================

/**
 * 用户积分详情
 */
export interface AdminPointsBalance {
  user_id: string;
  username: string;
  email: string;
  available: number;
  pending_expiration: number;
  total_earned: number;
  total_spent: number;
}

/**
 * 积分调整请求
 */
export interface PointsAdjustRequest {
  amount: number;  // 正数增加，负数扣除
  reason: string;  // 调整原因
}

/**
 * 积分系统统计
 */
export interface PointsStats {
  total_points_issued: number;
  total_points_spent: number;
  total_points_expired: number;
  active_users_with_points: number;
}

/**
 * 积分交易记录
 */
export interface PointsTransactionRecord {
  id: string;
  user_id: string;
  username: string;
  amount: number;
  balance_after: number;
  transaction_type: string;
  source_id: string | null;
  description: string | null;
  expires_at: string | null;
  is_expired: boolean;
  created_at: string;
}

/**
 * 积分交易历史响应
 */
export interface PointsTransactionsResponse {
  items: PointsTransactionRecord[];
  total: number;
  page: number;
  page_size: number;
}

// ==================== Check-in Stats ====================

/**
 * 签到统计
 */
export interface CheckInStats {
  today_count: number;
  yesterday_count: number;
  week_total: number;
  streak_distribution: Record<number, number>;
}

/**
 * 签到记录
 */
export interface CheckInRecord {
  id: string;
  user_id: string;
  username: string;
  check_in_date: string;
  streak_days: number;
  points_earned: number;
  created_at: string;
}

/**
 * 签到记录列表响应
 */
export interface CheckInRecordsResponse {
  items: CheckInRecord[];
  total: number;
  page: number;
  page_size: number;
}

// ==================== Referral Management ====================

/**
 * 邀请系统统计
 */
export interface ReferralStats {
  total_codes: number;
  active_codes: number;
  total_referrals: number;
  successful_referrals: number;
  pending_rewards: number;
  total_points_awarded: number;
}

/**
 * 邀请码详情
 */
export interface AdminInviteCode {
  id: string;
  code: string;
  owner_id: string;
  owner_name: string;
  max_uses: number;
  current_uses: number;
  is_active: boolean;
  expires_at: string | null;
  created_at: string;
}

/**
 * 邀请码列表响应
 */
export interface InviteCodesResponse {
  items: AdminInviteCode[];
  total: number;
  page: number;
  page_size: number;
}

/**
 * 奖励记录
 */
export interface RewardRecord {
  id: string;
  user_id: string;
  username: string;
  reward_type: string;
  amount: number;
  source: string;
  is_used: boolean;
  expires_at: string | null;
  created_at: string;
}

/**
 * 奖励记录列表响应
 */
export interface RewardsResponse {
  items: RewardRecord[];
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
