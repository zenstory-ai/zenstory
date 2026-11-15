/**
 * Points types for frontend.
 */

// Points balance response
export interface PointsBalance {
  available: number;
  pending_expiration: number;
  nearest_expiration_date: string | null;
}

// Check-in response
export interface CheckInResponse {
  success: boolean;
  points_earned: number;
  streak_days: number;
  message: string;
}

// Check-in status response
export interface CheckInStatusResponse {
  checked_in: boolean;
  streak_days: number;
  points_earned_today: number;
}

// Transaction item
export interface PointsTransaction {
  id: string;
  amount: number;
  balance_after: number;
  transaction_type: string;
  source_id: string | null;
  description: string | null;
  expires_at: string | null;
  is_expired: boolean;
  created_at: string;
}

// Transaction history response
export interface TransactionHistoryResponse {
  transactions: PointsTransaction[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

// Earn opportunity
export interface EarnOpportunity {
  type: string;
  points: number;
  description: string;
  is_completed: boolean;
  is_available: boolean;
}

// Redeem Pro response
export interface RedeemProResponse {
  success: boolean;
  points_spent: number;
  pro_days: number;
  new_period_end: string;
}

// Points configuration
export interface PointsConfig {
  check_in: number;
  check_in_streak: number;
  referral: number;
  skill_contribution: number;
  inspiration_contribution: number;
  profile_complete: number;
  pro_7days_cost: number;
  streak_bonus_threshold: number;
}
