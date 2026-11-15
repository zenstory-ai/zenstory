/**
 * Subscription API client for managing Pro subscriptions and quotas.
 *
 * Provides functions for:
 * - Querying subscription status and remaining quota
 * - Redeeming subscription codes for Pro access
 * - Viewing subscription history
 */

import { api } from './apiClient';
import type {
  SubscriptionCatalogResponse,
  SubscriptionStatusResponse,
  QuotaResponse,
  RedeemCodeRequest,
  RedeemCodeResponse,
  SubscriptionHistory,
  SubscriptionPlan,
} from '../types/subscription';

function getSubscriptionCacheScope(): string {
  if (typeof window === 'undefined') {
    return 'anonymous';
  }

  try {
    const rawUser = localStorage.getItem('user');
    if (!rawUser) {
      return 'anonymous';
    }

    const parsed = JSON.parse(rawUser) as { id?: unknown } | null;
    if (parsed && typeof parsed.id === 'string' && parsed.id.trim().length > 0) {
      return parsed.id;
    }
  } catch {
    // Ignore malformed cache and fall back to shared anonymous scope.
  }

  return 'anonymous';
}

const quotaQueryKey = () => ['subscription-quota', getSubscriptionCacheScope()] as const;

export const subscriptionQueryKeys = {
  status: () => ['subscription-status', getSubscriptionCacheScope()] as const,
  quota: quotaQueryKey,
  // Compatibility alias for existing imports. Both readers share one account-scoped cache.
  quotaLite: quotaQueryKey,
};

export const subscriptionApi = {
  /**
   * Get the current user's subscription status.
   *
   * @returns Promise resolving to subscription details including tier, expiry date, and active status
   */
  getStatus: () =>
    api.get<SubscriptionStatusResponse>('/api/v1/subscription/me'),

  /**
   * Get the current usage quota for the user.
   *
   * Returns AI usage limits, remaining quotas, and usage statistics
   * for the current billing period.
   *
   * @returns Promise resolving to quota information with usage limits and remaining amounts
   */
  getQuota: () =>
    api.get<QuotaResponse>('/api/v1/subscription/quota'),

  /**
   * Get active plans for user-facing plan comparison.
   */
  getPlans: () =>
    api.get<SubscriptionPlan[]>('/api/v1/subscription/plans'),

  /**
   * Get normalized plan catalog for pricing/billing pages.
   */
  getCatalog: () =>
    api.get<SubscriptionCatalogResponse>('/api/v1/subscription/catalog'),

  /**
   * Redeem a subscription code for Pro access.
   *
   * @param code - The subscription redemption code to apply
   * @returns Promise resolving to redemption result with new subscription details
   */
  redeemCode: (code: string, source?: string) => {
    const payload: RedeemCodeRequest = { code };
    const trimmedSource = source?.trim();
    if (trimmedSource) {
      payload.source = trimmedSource;
    }
    return api.post<RedeemCodeResponse>('/api/v1/subscription/redeem', payload);
  },

  /**
   * Get the user's subscription history.
   *
   * Returns a chronological list of subscription changes including
   * redemptions, renewals, and expirations.
   *
   * @param limit - Maximum number of history entries to return
   * @returns Promise resolving to array of subscription history records
   */
  getHistory: (limit = 50) =>
    api.get<SubscriptionHistory[]>(`/api/v1/subscription/history?limit=${limit}`),
};

export default subscriptionApi;
