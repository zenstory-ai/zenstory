import { useNow } from "../hooks/useNow";
import { formatRelativeTime } from "../lib/dateUtils";

/**
 * 「3 分钟前」 for a backend UTC timestamp, re-evaluated every 30 seconds so a
 * dashboard left open does not keep showing the time from when it rendered.
 */
export function RelativeTime({ value, fallback = "-" }: { value?: string | null; fallback?: string }) {
  const now = useNow(30_000);
  if (!value) return <>{fallback}</>;
  return <>{formatRelativeTime(value, new Date(now))}</>;
}
