/**
 * React hook for checking and managing feature quotas.
 *
 * Provides state management and methods for:
 * - Fetching quota status for different features
 * - Checking if a feature can be used (canUseFeature)
 * - Getting detailed quota status (getQuotaStatus)
 * - Refreshing quota data after consumption
 * - Accessing subscription tier information
 *
 * @module hooks/useQuota
 */

import { useCallback, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { subscriptionApi, subscriptionQueryKeys } from '../lib/subscriptionApi';
import { useSubscriptionStore } from '../stores/subscriptionStore';

/**
 * Feature types that have usage quotas.
 *
 * Each feature has a configurable limit based on subscription tier:
 * - `ai_conversations` - Number of AI chat conversations
 * - `material_uploads` - Number of material file uploads
 * - `material_decompositions` - Number of material decomposition operations
 * - `skill_creates` - Number of custom skills that can be created
 * - `inspiration_copies` - Number of inspiration board copies
 */
export type QuotaFeature =
  | 'ai_conversations'
  | 'material_uploads'
  | 'material_decompositions'
  | 'skill_creates'
  | 'inspiration_copies';

/**
 * Quota status information for a single feature.
 */
export interface QuotaStatus {
  /** Number of times the feature has been used */
  used: number;
  /** Maximum allowed uses (-1 = unlimited) */
  limit: number;
  /** Remaining uses (-1 = unlimited) */
  remaining: number;
  /** Whether this feature has unlimited usage */
  isUnlimited: boolean;
  /** Whether the quota has been exceeded */
  isExceeded: boolean;
  /** Usage percentage (0-100, -1 if unlimited) */
  percentage: number;
}

/**
 * Return type for the useQuota hook.
 */
export interface UseQuotaResult {
  /** Quota status for all features, keyed by feature name */
  quotas: Record<QuotaFeature, QuotaStatus> | null;

  /** Whether quota data is currently being fetched */
  isLoading: boolean;

  /** Error if quota fetch failed */
  error: Error | null;

  /**
   * Check if a feature can be used based on quota status.
   * Pro users always return true.
   *
   * @param feature - The feature to check
   * @returns Whether the feature can be used
   */
  canUseFeature: (feature: QuotaFeature) => boolean;

  /**
   * Get detailed quota status for a specific feature.
   *
   * @param feature - The feature to get status for
   * @returns Quota status or null if not available
   */
  getQuotaStatus: (feature: QuotaFeature) => QuotaStatus | null;

  /**
   * Refresh quota data from the server.
   * Call this after consuming a quota to get updated values.
   */
  refreshQuota: () => Promise<void>;

  /** Whether the current user has a Pro subscription */
  isPro: boolean;

  /** Current subscription tier name */
  tier: string;
}

/** Default quota status when data is not available */
const DEFAULT_QUOTA: QuotaStatus = {
  used: 0,
  limit: 0,
  remaining: 0,
  isUnlimited: false,
  isExceeded: false,
  percentage: 0,
};

/**
 * Hook for managing feature quotas and subscription limits.
 *
 * Fetches quota data from the server and provides helper methods
 * to check if features can be used. Pro users bypass all quota checks.
 *
 * @returns Object containing quota status and helper methods
 *
 * @example
 * ```tsx
 * function ChatComponent() {
 *   const { canUseFeature, getQuotaStatus, isLoading } = useQuota();
 *
 *   const handleStartChat = () => {
 *     if (!canUseFeature('ai_conversations')) {
 *       const status = getQuotaStatus('ai_conversations');
 *       alert(`Quota exceeded: ${status?.used}/${status?.limit} used`);
 *       return;
 *     }
 *     // Proceed with chat
 *   };
 *
 *   if (isLoading) return <Spinner />;
 *
 *   return <Button onClick={handleStartChat}>Start Chat</Button>;
 * }
 * ```
 *
 * @example
 * ```tsx
 * // Display quota status with refresh capability
 * function QuotaDisplay() {
 *   const { quotas, refreshQuota, isPro, tier } = useQuota();
 *
 *   useEffect(() => {
 *     // Refresh on mount
 *     refreshQuota();
 *   }, []);
 *
 *   if (isPro) {
 *     return <div>Pro user - Unlimited access</div>;
 *   }
 *
 *   return (
 *     <div>
 *       <p>Tier: {tier}</p>
 *       {quotas && (
 *         <p>AI Conversations: {quotas.ai_conversations.used}/{quotas.ai_conversations.limit}</p>
 *       )}
 *     </div>
 *   );
 * }
 * ```
 */
export function useQuota(): UseQuotaResult {
  const { isPro, getTier } = useSubscriptionStore();

  // Fetch quota data
  const { data: quotaData, isLoading, error, refetch } = useQuery({
    queryKey: subscriptionQueryKeys.quota(),
    queryFn: () => subscriptionApi.getQuota(),
    staleTime: 30 * 1000, // 30 seconds
    refetchOnWindowFocus: true,
  });

  // Calculate quota status for all features
  const calculateQuotaStatus = useCallback((used: number, limit: number): QuotaStatus => {
    const isUnlimited = limit === -1;
    const remaining = isUnlimited ? -1 : Math.max(0, limit - used);
    const isExceeded = !isUnlimited && used >= limit;
    const percentage = isUnlimited ? -1 : Math.min(100, Math.round((used / limit) * 100));

    return {
      used,
      limit,
      remaining,
      isUnlimited,
      isExceeded,
      percentage,
    };
  }, []);

  // Build quota status map
  const quotas: Record<QuotaFeature, QuotaStatus> | null = useMemo(
    () =>
      quotaData
        ? {
            ai_conversations: calculateQuotaStatus(
              quotaData.ai_conversations.used,
              quotaData.ai_conversations.limit
            ),
            material_uploads: quotaData.material_uploads
              ? calculateQuotaStatus(quotaData.material_uploads.used, quotaData.material_uploads.limit)
              : DEFAULT_QUOTA,
            material_decompositions: quotaData.material_decompositions
              ? calculateQuotaStatus(quotaData.material_decompositions.used, quotaData.material_decompositions.limit)
              : DEFAULT_QUOTA,
            skill_creates: quotaData.skill_creates
              ? calculateQuotaStatus(quotaData.skill_creates.used, quotaData.skill_creates.limit)
              : DEFAULT_QUOTA,
            inspiration_copies: quotaData.inspiration_copies
              ? calculateQuotaStatus(quotaData.inspiration_copies.used, quotaData.inspiration_copies.limit)
              : DEFAULT_QUOTA,
          }
        : null,
    [quotaData, calculateQuotaStatus]
  );

  // Check if a feature can be used
  const canUseFeature = useCallback(
    (feature: QuotaFeature): boolean => {
      // Pro users always have access
      if (isPro()) return true;

      const status = quotas?.[feature];
      if (!status) return false;

      return status.isUnlimited || !status.isExceeded;
    },
    [quotas, isPro]
  );

  // Get quota status for a specific feature
  const getQuotaStatus = useCallback(
    (feature: QuotaFeature): QuotaStatus | null => {
      return quotas?.[feature] ?? null;
    },
    [quotas]
  );

  // Refresh quota data
  const refreshQuota = useCallback(async () => {
    await refetch();
  }, [refetch]);

  return {
    quotas,
    isLoading,
    error: error as Error | null,
    canUseFeature,
    getQuotaStatus,
    refreshQuota,
    isPro: isPro(),
    tier: getTier(),
  };
}

export default useQuota;
