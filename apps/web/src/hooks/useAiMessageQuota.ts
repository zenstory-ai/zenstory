import { useQuery } from "@tanstack/react-query";
import { subscriptionApi, subscriptionQueryKeys } from "../lib/subscriptionApi";
import { parseUTCDate } from "../lib/dateUtils";
import type { QuotaMetric } from "../types/subscription";

/** Whether today's AI messages are used up. Pro (limit -1) never runs out. */
export function isAiMessageQuotaExhausted(metric: QuotaMetric | undefined | null): boolean {
  return Boolean(metric && metric.limit !== -1 && metric.limit > 0 && metric.used >= metric.limit);
}

const BEIJING_OFFSET_MS = 8 * 60 * 60 * 1000;
const DAY_MS = 24 * 60 * 60 * 1000;

/** The next Beijing midnight after `now` (epoch ms): when the daily AI messages come back. */
export function nextAiQuotaResetMs(now: number): number {
  return Math.floor((now + BEIJING_OFFSET_MS) / DAY_MS) * DAY_MS + DAY_MS - BEIJING_OFFSET_MS;
}

/** One canonical cycle key even when the quota response arrives after a refusal. */
export function aiQuotaPeriodKey(resetAt: string | null | undefined, now = Date.now()): string {
  const reset = resetAt ? parseUTCDate(resetAt).getTime() : Number.NaN;
  return String(Number.isFinite(reset) ? reset : nextAiQuotaResetMs(now));
}

/**
 * The Beijing calendar day the daily AI messages come back on ("10月10日" / "October 10").
 * The server's `reset_at` is the next Beijing midnight; null when it is missing or unreadable.
 */
export function formatAiQuotaResetDay(resetAt: string | null | undefined, language?: string): string | null {
  if (!resetAt) return null;
  const date = parseUTCDate(resetAt);
  if (Number.isNaN(date.getTime())) return null;
  const locale = language?.startsWith("en") ? "en-US" : "zh-CN";
  return new Intl.DateTimeFormat(locale, { month: "long", day: "numeric", timeZone: "Asia/Shanghai" }).format(date);
}

/**
 * The reset moment in the reader's own time zone ("October 21 at 12:00 PM"), or null when that
 * reads the same as Beijing time. The quota day stays the Beijing calendar day; this only saves a
 * reader abroad the time-zone arithmetic. `timeZone` defaults to the browser's.
 */
export function formatAiQuotaResetLocal(
  resetAt: string | null | undefined,
  language?: string,
  timeZone?: string,
): string | null {
  if (!resetAt) return null;
  const date = parseUTCDate(resetAt);
  if (Number.isNaN(date.getTime())) return null;
  const locale = language?.startsWith("en") ? "en-US" : "zh-CN";
  const options: Intl.DateTimeFormatOptions = { month: "long", day: "numeric", hour: "numeric", minute: "2-digit" };
  const local = new Intl.DateTimeFormat(locale, { ...options, timeZone }).format(date);
  const beijing = new Intl.DateTimeFormat(locale, { ...options, timeZone: "Asia/Shanghai" }).format(date);
  return local === beijing ? null : local;
}

/**
 * Today's AI message allowance, from the same cached quota query the header badge polls.
 * `exhausted` is false while the quota is unknown, so nothing is blocked on a missing answer.
 *
 * Re-read on every mount (the app's queries otherwise keep a cached answer for minutes):
 * the home page is where an author lands after leaving a round, and the server may have
 * refunded that round after the chat's own refresh ran.
 */
export function useAiMessageQuota(): {
  metric: QuotaMetric | undefined;
  exhausted: boolean;
  limit: number | undefined;
  resetAt: string | null;
} {
  const { data: quota } = useQuery({
    queryKey: subscriptionQueryKeys.quota(),
    queryFn: () => subscriptionApi.getQuota(),
    refetchOnMount: "always",
  });
  const metric = quota?.ai_conversations;
  return {
    metric,
    exhausted: isAiMessageQuotaExhausted(metric),
    limit: metric?.limit,
    resetAt: metric?.reset_at ?? null,
  };
}
