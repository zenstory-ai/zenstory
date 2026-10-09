// Subscription tier type
export type SubscriptionTier = "free" | "pro";
export type SubscriptionStatus = "active" | "expired" | "cancelled" | "none";
export type CodeType = "single_use" | "multi_use";
export type SubscriptionAction =
  | "created"
  | "upgraded"
  | "renewed"
  | "expired"
  | "cancelled"
  | "migrated";

// Subscription plan features
export interface SubscriptionFeatures {
  ai_conversations_per_day?: number; // -1 = unlimited
  context_window_tokens?: number;
  file_versions_per_file?: number;
  max_projects?: number; // -1 = unlimited
  export_formats?: string[];
  custom_prompts?: boolean;
  materials_library_access?: boolean;
  material_uploads?: number; // -1 = unlimited
  material_decompositions?: number;
  custom_skills?: number; // -1 = unlimited
  inspiration_copies_monthly?: number; // -1 = unlimited
  priority_support?: boolean;
  [key: string]: unknown;
}

// Subscription plan
export interface SubscriptionPlan {
  id: string;
  name: SubscriptionTier;
  display_name: string;
  display_name_en?: string;
  price_monthly_cents: number;
  price_yearly_cents: number;
  features: SubscriptionFeatures;
  is_active: boolean;
  created_at?: string;
  updated_at?: string;
}

export interface SubscriptionCatalogEntitlements {
  /** Missing from APIs that predate the daily AI conversation entitlement. */
  ai_conversations_per_day?: number;
  /** @deprecated Fixed at zero for compatibility; not a runtime entitlement. */
  writing_credits_monthly: number;
  /** @deprecated Fixed at zero for compatibility; not a runtime entitlement. */
  agent_runs_monthly: number;
  active_projects_limit: number;
  materials_library_access: boolean;
  material_uploads_monthly: number;
  material_decompositions_monthly: number;
  custom_skills_limit: number;
  inspiration_copies_monthly: number;
  export_formats: string[];
}

export interface SubscriptionCatalogTier {
  id: string;
  name: string;
  display_name: string;
  display_name_en?: string;
  price_monthly_cents: number;
  price_yearly_cents: number;
  recommended: boolean;
  summary_key: string;
  target_user_key: string;
  entitlements: SubscriptionCatalogEntitlements;
}

export interface SubscriptionCatalogResponse {
  version: string;
  comparison_mode: string;
  pricing_anchor_monthly_cents: number;
  tiers: SubscriptionCatalogTier[];
}

// User subscription
export interface UserSubscription {
  id: string;
  user_id: string;
  plan_id: string;
  plan?: SubscriptionPlan;
  status: SubscriptionStatus;
  current_period_start: string;
  current_period_end: string;
  cancel_at_period_end: boolean;
  created_at: string;
  updated_at: string;
}

// Usage quota
export interface UsageQuota {
  id: string;
  user_id: string;
  period_start: string;
  period_end: string;
  ai_conversations_used: number;
  last_reset_at: string;
  created_at: string;
  updated_at: string;
}

// Redemption code
export interface RedemptionCode {
  id: string;
  code: string;
  code_type: CodeType;
  tier: SubscriptionTier;
  duration_days: number;
  max_uses: number | null;
  current_uses: number;
  created_by: string;
  is_active: boolean;
  expires_at: string | null;
  notes: string | null;
  created_at: string;
  redeemed_by: string[];
}

// Subscription history
export interface SubscriptionHistory {
  id: string;
  user_id: string;
  action: SubscriptionAction;
  plan_name: string;
  start_date: string;
  end_date: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
}

// Admin audit log
export interface AdminAuditLog {
  id: string;
  admin_user_id: string;
  action: string;
  resource_type: string;
  resource_id: string | null;
  old_value: Record<string, unknown> | null;
  new_value: Record<string, unknown> | null;
  ip_address: string | null;
  user_agent: string | null;
  created_at: string;
}

// API Response types
export interface SubscriptionStatusResponse {
  tier: SubscriptionTier;
  status: SubscriptionStatus;
  display_name: string;
  display_name_en?: string | null;
  current_period_end: string | null;
  days_remaining: number | null;
  features: SubscriptionFeatures;
}

export interface QuotaResponse {
  ai_conversations: QuotaMetric;
  projects: QuotaMetric;
  material_uploads: QuotaMetric;
  material_decompositions: QuotaMetric;
  skill_creates: QuotaMetric;
  inspiration_copies: QuotaMetric;
  /** Free material-breakdown trial; absent from older servers. */
  material_trial?: MaterialTrial;
}

/** One free book breakdown per account, first `max_chapters` chapters only. */
export interface MaterialTrial {
  available: boolean;
  used: boolean;
  max_chapters: number;
}

export interface QuotaMetric {
  used: number;
  limit: number;
  reset_at: string | null;
}

export interface RedeemCodeRequest {
  code: string;
  source?: string;
}

export interface RedeemCodeResponse {
  success: boolean;
  message: string;
  tier?: SubscriptionTier;
  duration_days?: number;
  subscription?: UserSubscription;
}
