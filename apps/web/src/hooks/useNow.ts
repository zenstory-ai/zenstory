import { useEffect, useState } from "react";

/**
 * Current time that advances every `intervalMs`, so labels such as
 * 「12 秒前」 or 「3 分钟前」 keep moving while the page stays open instead
 * of freezing at the value computed on the last unrelated re-render.
 */
export function useNow(intervalMs: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(id);
  }, [intervalMs]);
  return now;
}
