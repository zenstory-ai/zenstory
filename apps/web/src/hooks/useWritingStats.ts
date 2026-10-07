import { useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { writingStatsApi } from '../lib/writingStatsApi';
import type {
  ProjectDashboardStatsResponse,
  RecordStatsRequest,
  RecordStatsResponse,
} from '../types/writingStats';

export interface UseWritingStatsOptions {
  /** Project ID to fetch stats for */
  projectId: string | undefined;
  /** Enable/disable automatic fetching */
  enabled?: boolean;
  /** Stale time in milliseconds (default: 1 minute) */
  staleTime?: number;
}

export interface UseWritingStatsReturn {
  /** Dashboard statistics */
  stats: ProjectDashboardStatsResponse | null;
  /** Loading state */
  isLoading: boolean;
  /** Background fetching state */
  isFetching: boolean;
  /** Error state */
  error: Error | null;
  /** Refetch statistics */
  refetch: () => Promise<void>;
  /** Record daily stats */
  recordStats: (data: RecordStatsRequest) => Promise<RecordStatsResponse>;
  /** Loading state for recording stats */
  isRecording: boolean;
}

/**
 * Hook for managing writing statistics for a project
 *
 * Provides functionality for:
 * - Fetching combined dashboard statistics (word count, chapter completion, streak, AI usage)
 * - Recording daily writing statistics
 * - Auto-updating streak information
 *
 * @param options - Configuration options for the hook
 * @param options.projectId - Project ID to fetch stats for (required for data fetching)
 * @param options.enabled - Enable/disable automatic fetching (default: true)
 * @param options.staleTime - Stale time in milliseconds (default: 60000)
 * @returns Object containing stats data and control functions
 * @returns stats - Dashboard statistics or null if not loaded
 * @returns isLoading - Loading state for the initial fetch
 * @returns error - Error object if fetch failed, null otherwise
 * @returns refetch - Function to manually refetch statistics
 * @returns recordStats - Function to record daily writing statistics
 * @returns isRecording - Loading state for the record mutation
 *
 * @example
 * ```tsx
 * const { stats, isLoading, recordStats, isRecording } = useWritingStats({
 *   projectId: currentProjectId,
 *   enabled: !!currentProjectId,
 *   staleTime: 5 * 60 * 1000, // 5 minutes
 * });
 *
 * // Display stats
 * if (isLoading) return <Spinner />;
 * if (stats) {
 *   console.log(`Word count: ${stats.wordCount.total}`);
 *   console.log(`Streak: ${stats.streak.current} days`);
 * }
 *
 * // Record today's stats
 * await recordStats({ wordCount: 1500, wordsWritten: 500 });
 * ```
 */
export function useWritingStats(options: UseWritingStatsOptions): UseWritingStatsReturn {
  const { projectId, enabled = true, staleTime = 60 * 1000 } = options;

  const queryClient = useQueryClient();

  // Fetch dashboard stats
  const {
    data: stats,
    isLoading,
    isFetching,
    error,
    refetch,
  } = useQuery<ProjectDashboardStatsResponse | null, Error>({
    queryKey: ['writing-stats', projectId],
    queryFn: async () => {
      if (!projectId) return null;
      return writingStatsApi.getDashboardStats(projectId);
    },
    enabled: enabled && !!projectId,
    staleTime,
    retry: 2,
  });

  // Record stats mutation
  const recordMutation = useMutation<
    RecordStatsResponse,
    Error,
    { projectId: string; data: RecordStatsRequest }
  >({
    mutationFn: ({ projectId, data }) => writingStatsApi.recordStats(projectId, data),
    onSuccess: (_data, variables) => {
      // Invalidate stats to refresh with new data
      queryClient.invalidateQueries({ queryKey: ['writing-stats', variables.projectId], refetchType: 'active' });
      queryClient.invalidateQueries({ queryKey: ['writing-stats-word-trend', variables.projectId], refetchType: 'active' });
    },
  });

  // Refetch wrapper
  const handleRefetch = useCallback(async () => {
    await Promise.all([
      refetch(),
      projectId
        ? queryClient.invalidateQueries({ queryKey: ['writing-stats-word-trend', projectId], refetchType: 'active' })
        : Promise.resolve(),
    ]);
  }, [refetch, queryClient, projectId]);

  // Record stats wrapper
  const recordStats = useCallback(
    async (data: RecordStatsRequest): Promise<RecordStatsResponse> => {
      if (!projectId) {
        throw new Error('Project ID is required to record stats');
      }
      return recordMutation.mutateAsync({ projectId, data });
    },
    [projectId, recordMutation]
  );

  return {
    stats: stats ?? null,
    isLoading,
    isFetching: Boolean(isFetching),
    error: error ?? null,
    refetch: handleRefetch,
    recordStats,
    isRecording: recordMutation.isPending,
  };
}

export default useWritingStats;
