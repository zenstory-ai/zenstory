/**
 * Writing Statistics API client for tracking project metrics and progress.
 *
 * Provides functions for:
 * - Fetching dashboard statistics including word count and streak data
 * - Recording daily writing statistics for progress tracking
 *
 * Dates default to the Beijing calendar day, the same day boundary as quotas.
 */

import { api } from './apiClient';
import { getBeijingDateString } from './dateUtils';
import type {
  ActivationGuideResponse,
  ProjectDashboardStatsResponse,
  RecordStatsRequest,
  RecordStatsResponse,
  WordCountTrendResponse,
} from '../types/writingStats';

export const writingStatsApi = {
  /**
   * Get current user's first-day activation guide.
   */
  getActivationGuide: () =>
    api.get<ActivationGuideResponse>('/api/v1/activation/guide'),

  /**
   * Get combined dashboard statistics for a project.
   *
   * Retrieves comprehensive metrics including:
   * - Total word count and chapter completion percentage
   * - Writing streak information (current and longest)
   * - AI usage statistics (queries made, tokens used)
   * - Recent activity timeline
   *
   * @param projectId - The unique identifier of the project
   * @returns Promise resolving to dashboard statistics with all metrics
   */
  getDashboardStats: (
    projectId: string,
    params: { clientDate?: string } = {}
  ) => {
    const search = new URLSearchParams();
    search.set('client_date', params.clientDate ?? getBeijingDateString());
    return api.get<ProjectDashboardStatsResponse>(
      `/api/v1/projects/${projectId}/stats?${search.toString()}`
    );
  },

  /**
   * Get word count trend time-series data for a project.
   */
  getWordCountTrend: (
    projectId: string,
    params: {
      period?: 'daily' | 'weekly' | 'monthly';
      days?: number;
      clientDate?: string;
    } = {}
  ) => {
    const search = new URLSearchParams();
    if (params.period) search.set('period', params.period);
    if (typeof params.days === 'number') search.set('days', String(params.days));
    search.set('client_date', params.clientDate ?? getBeijingDateString());
    const query = search.toString();
    const suffix = query ? `?${query}` : '';
    return api.get<WordCountTrendResponse>(
      `/api/v1/projects/${projectId}/stats/word-count-trend${suffix}`
    );
  },

  /**
   * Record daily writing statistics for a project.
   *
   * Updates word count, checks streak continuation, and stores
   * daily snapshot for historical tracking. Should be called
   * when user makes significant progress or at session end.
   *
   * @param projectId - The unique identifier of the project
   * @param data - The statistics data to record (word count, chapter progress, etc.)
   * @returns Promise resolving to updated streak info and recorded stats summary
   */
  recordStats: (projectId: string, data: RecordStatsRequest) =>
    api.post<RecordStatsResponse>(`/api/v1/projects/${projectId}/stats/record`, {
      ...data,
      stats_date: data.stats_date ?? getBeijingDateString(),
    }),
};

export default writingStatsApi;
