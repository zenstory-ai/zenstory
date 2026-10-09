import { useEffect, useState } from "react";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { subscriptionApi, subscriptionQueryKeys } from "../lib/subscriptionApi";
import type { SubscriptionStatusResponse } from "../types/subscription";

/**
 * Whether the signed-in author is on a paid plan, checked when a limit prompt opens.
 *
 * Reads the cached /subscription/me first and refreshes it on open. Works without a
 * QueryClientProvider (some surfaces and tests render without one): then the plan is
 * unknown and the author is treated as free. `resolved` turns true once the answer is
 * known (or cannot be), so callers can hold upgrade copy and telemetry until then.
 */
function useOptionalQueryClient(): QueryClient | undefined {
  try {
    return useQueryClient();
  } catch {
    // No provider (or a test double without one): the plan is simply unknown.
    return undefined;
  }
}

const PLAN_LOOKUP_TIMEOUT_MS = 4000;

export function usePaidPlanWhenOpen(open: boolean): { isPaid: boolean; resolved: boolean } {
  const client = useOptionalQueryClient();
  const [tier, setTier] = useState<string | undefined>(
    () => client?.getQueryData<SubscriptionStatusResponse>(subscriptionQueryKeys.status())?.tier,
  );
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!open || !client) return;
    let cancelled = false;
    client
      .fetchQuery({
        queryKey: subscriptionQueryKeys.status(),
        queryFn: () => subscriptionApi.getStatus(),
        staleTime: 60_000,
      })
      .then((status) => {
        if (!cancelled) setTier(status?.tier);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    // A slow or stuck request must not keep the prompt blank: fall back to the free copy.
    const giveUp = setTimeout(() => {
      if (!cancelled) setFailed(true);
    }, PLAN_LOOKUP_TIMEOUT_MS);
    return () => {
      cancelled = true;
      clearTimeout(giveUp);
    };
  }, [open, client]);

  // The cache may have been filled after this prompt mounted (the page loads the plan
  // on its own): use it right away instead of waiting a frame for the refresh.
  const knownTier =
    tier ?? client?.getQueryData<SubscriptionStatusResponse>(subscriptionQueryKeys.status())?.tier;

  return {
    isPaid: Boolean(knownTier && knownTier !== "free"),
    resolved: !client || knownTier !== undefined || failed,
  };
}
