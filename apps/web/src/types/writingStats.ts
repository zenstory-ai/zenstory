/**
 * Writing statistics types for frontend.
 * Matches backend schemas in apps/server/api/projects.py
 */

// ==================== Word Count Types ====================

/**
 * Single day word count data.
 */
export interface DailyWordCountItem {
  date: string;
  word_count: number;
  words_added: number;
  words_deleted: number;
  net_words: number;
}

/**
 * Weekly aggregated word count data.
 */
export interface WeeklyWordCountItem {
  date: string;
  period_label: string;
  word_count: number;
  words_added: number;
  words_deleted: number;
  net_words: number;
  days_with_activity: number;
  avg_words_per_day: number;
}

/**
 * Monthly aggregated word count data.
 */
export interface MonthlyWordCountItem {
  date: string;
  period_label: string;
  word_count: number;
  words_added: number;
  words_deleted: number;
  net_words: number;
  days_with_activity: number;
  avg_words_per_day: number;
}

/**
 * Response for word count trend endpoint.
 */
export interface WordCountTrendResponse {
  period: "daily" | "weekly" | "monthly";
  days: number;
  data: DailyWordCountItem[] | WeeklyWordCountItem[] | MonthlyWordCountItem[];
  total_words_added: number;
  total_words_deleted: number;
}

// ==================== Chapter Completion Types ====================

/**
 * Single chapter completion status.
 */
export interface ChapterDetailItem {
  outline_id: string;
  draft_id: string | null;
  title: string;
  word_count: number;
  target_word_count: number | null;
  status: "complete" | "in_progress" | "not_started";
  completion_percentage: number;
}

/**
 * Response for chapter completion statistics.
 */
export interface ChapterCompletionResponse {
  total_chapters: number;
  completed_chapters: number;
  in_progress_chapters: number;
  not_started_chapters: number;
  /**
   * Completed share of planned_total. Only meaningful when planned_total is
   * known; older servers send the share of total_chapters.
   */
  completion_percentage: number;
  /** Planned chapter count from the outline; null/absent when unknown. */
  planned_total?: number | null;
  chapter_details: ChapterDetailItem[];
}

// ==================== Writing Streak Types ====================

/**
 * Response for writing streak status.
 */
export interface WritingStreakResponse {
  current_streak: number;
  longest_streak: number;
  streak_status: "active" | "at_risk" | "broken" | "none";
  days_until_break: number | null;
  last_writing_date: string | null;
  streak_start_date: string | null;
  streak_recovery_count: number;
}

/**
 * Single day in streak history.
 */
export interface StreakHistoryItem {
  date: string;
  wrote: boolean;
  word_count: number;
  streak_count: number;
}

/**
 * Response for streak history endpoint.
 */
export interface StreakHistoryResponse {
  days: number;
  history: StreakHistoryItem[];
}

// ==================== AI Usage Types ====================

/**
 * AI usage statistics for a project.
 */
export interface AIUsageStatsResponse {
  total_sessions: number;
  active_session_id: string | null;
  total_messages: number;
  user_messages: number;
  // Backend currently returns ai_messages; keep assistant_messages for compatibility.
  ai_messages?: number;
  assistant_messages?: number;
  tool_messages: number;
  input_tokens?: number;
  output_tokens?: number;
  cache_read_tokens?: number;
  cache_write_tokens?: number;
  total_tokens?: number;
  estimated_tokens: number;
  estimated_cost_usd?: number;
  // Backend currently returns *_date; keep *_at for compatibility.
  first_interaction_date?: string | null;
  last_interaction_date?: string | null;
  first_interaction_at?: string | null;
  last_interaction_at?: string | null;
}

/**
 * Single period AI usage data.
 */
export interface AIUsageTrendItem {
  date: string;
  period_label: string | null;
  total_messages: number;
  user_messages: number;
  ai_messages?: number;
  assistant_messages?: number;
  tool_messages?: number;
  input_tokens?: number;
  output_tokens?: number;
  cache_read_tokens?: number;
  cache_write_tokens?: number;
  total_tokens?: number;
  estimated_tokens: number;
  estimated_cost_usd?: number;
}

/**
 * Response for AI usage trend endpoint.
 */
export interface AIUsageTrendResponse {
  period: "daily" | "weekly" | "monthly";
  days: number;
  data: AIUsageTrendItem[];
}

/**
 * AI usage summary for a period.
 */
export interface AIUsagePeriodSummary {
  total: number;
  user: number;
  ai: number;
  input_tokens?: number;
  output_tokens?: number;
  cache_read_tokens?: number;
  cache_write_tokens?: number;
  total_tokens?: number;
  estimated_tokens: number;
  estimated_cost_usd?: number;
}

/**
 * Comprehensive AI usage summary for dashboard.
 */
export interface AIUsageSummaryResponse {
  current: AIUsageStatsResponse;
  today: AIUsagePeriodSummary;
  this_week: AIUsagePeriodSummary;
  this_month: AIUsagePeriodSummary;
}

// ==================== Recording Stats Types ====================

/**
 * Request body for recording daily writing stats.
 */
export interface RecordStatsRequest {
  word_count: number;
  words_added?: number;
  words_deleted?: number;
  edit_time_seconds?: number;
  stats_date?: string | null; // YYYY-MM-DD format, defaults to today
}

/**
 * Response for recording writing stats.
 */
export interface RecordStatsResponse {
  id: string;
  user_id: string;
  project_id: string;
  stats_date: string;
  word_count: number;
  words_added: number;
  words_deleted: number;
  edit_sessions: number;
  total_edit_time_seconds: number;
  created_at?: string;
  updated_at?: string;
  streak_updated: boolean;
  new_streak: number | null;
}

// ==================== Combined Dashboard Stats ====================

/**
 * Combined statistics for project dashboard.
 */
export interface ProjectDashboardStatsResponse {
  project_id: string;
  project_name: string;
  /** novel / short / screenplay; older servers omit it (treated as novel). */
  project_type?: string;
  // Word count
  total_word_count: number;
  words_today: number;
  words_this_week: number;
  words_this_month: number;
  /** Words the AI added to manuscripts (absent from older servers: 0). words_* are the author's own. */
  ai_words_today?: number;
  ai_words_this_week?: number;
  ai_words_this_month?: number;
  // Chapter completion
  chapter_completion: ChapterCompletionResponse;
  // Writing streak
  streak: WritingStreakResponse;
  // AI usage
  ai_usage: AIUsageSummaryResponse;
  // Timestamps
  generated_at: string;
}

// ==================== First-day Activation Guide ====================

export interface ActivationGuideStepResponse {
  event_name: string;
  label: string;
  completed: boolean;
  completed_at: string | null;
  action_path: string;
}

export interface ActivationGuideResponse {
  user_id: string;
  window_hours: number;
  within_first_day: boolean;
  total_steps: number;
  completed_steps: number;
  completion_rate: number;
  is_activated: boolean;
  next_event_name: string | null;
  next_action: string | null;
  steps: ActivationGuideStepResponse[];
}
