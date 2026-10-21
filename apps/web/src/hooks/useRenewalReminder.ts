import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { trackEvent } from "../lib/analytics";
import { subscriptionApi, subscriptionQueryKeys } from "../lib/subscriptionApi";
import type { SubscriptionStatusResponse } from "../types/subscription";

export type RenewalReminderStage = "week" | "final";

export interface RenewalReminder {
  stage: RenewalReminderStage;
  periodEnd: string;
  daysRemaining: number;
  dismiss: () => void;
}

/** Paid plans are one-time purchases, so an active plan nearing its end needs a nudge to renew. */
function getRenewalReminderStage(
  status: Pick<SubscriptionStatusResponse, "tier" | "status" | "current_period_end" | "days_remaining"> | null | undefined,
): RenewalReminderStage | null {
  if (!status || status.tier === "free" || status.status !== "active") return null;
  if (!status.current_period_end || status.days_remaining == null) return null;
  if (status.days_remaining <= 1) return "final";
  if (status.days_remaining <= 7) return "week";
  return null;
}

function dismissalKey(accountId: string, periodEnd: string, stage: RenewalReminderStage): string {
  // Keyed by period end: a renewal moves it, so the next expiry is reminded afresh.
  return `zenstory:renewal-reminder-dismissed:${encodeURIComponent(accountId)}:${periodEnd}:${stage}`;
}

export function useRenewalReminder(accountId: string): RenewalReminder | null {
  const [dismissedInSession, setDismissedInSession] = useState<string | null>(null);
  const query = useQuery({
    queryKey: subscriptionQueryKeys.status(),
    queryFn: () => subscriptionApi.getStatus(),
    retry: false,
    staleTime: 60_000,
    // A renewal elsewhere must clear the reminder when the author returns home.
    refetchOnMount: "always",
  });

  const status = query.data;
  const stage = getRenewalReminderStage(status);
  if (!status?.current_period_end || status.days_remaining == null || !stage) return null;

  const periodEnd = status.current_period_end;
  const key = dismissalKey(accountId, periodEnd, stage);
  let dismissedPersistently = false;
  try {
    dismissedPersistently = localStorage.getItem(key) === "1";
  } catch {
    // Storage can be unavailable; the in-memory dismissal below still works.
  }
  if (dismissedPersistently || dismissedInSession === key) return null;

  return {
    stage,
    periodEnd,
    daysRemaining: status.days_remaining,
    dismiss: () => {
      setDismissedInSession(key);
      try {
        localStorage.setItem(key, "1");
      } catch {
        // Do not claim persistence when the browser rejects storage.
      }
      trackEvent("renewal_reminder_dismissed", { stage, days_remaining: status.days_remaining });
    },
  };
}
