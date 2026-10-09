import React, { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import type { StreamActivity } from "../hooks/useStreamActivity";

/** After this long without the round finishing, reassure the author that long text takes time. */
export const LONG_WAIT_HINT_AFTER_S = 20;

interface StreamActivityLineProps {
  activity: StreamActivity | null;
  /** Epoch ms when the round started. */
  startedAt: number | null;
}

/**
 * One quiet line under the chat while a round runs: what the agent is doing and
 * how long it has been. Ticks on its own so the chat panel does not re-render
 * every second.
 */
export const StreamActivityLine: React.FC<StreamActivityLineProps> = ({ activity, startedAt }) => {
  const { t } = useTranslation(["chat"]);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  if (!activity) return null;
  const seconds = startedAt ? Math.max(0, Math.floor((now - startedAt) / 1000)) : 0;
  const label = activity.title
    ? t(`chat:activity.${activity.kind}Titled`, { title: activity.title })
    : t(`chat:activity.${activity.kind}`);

  return (
    <div
      data-testid="stream-activity-line"
      className="mt-2 flex flex-wrap items-center gap-x-1.5 text-xs text-[hsl(var(--text-secondary))]"
    >
      <Loader2 size={12} className="animate-spin shrink-0" />
      {/* Only the activity is announced; the ticking seconds would be read out every second. */}
      <span role="status" aria-live="polite">{label}</span>
      <span aria-hidden="true">·</span>
      <span aria-hidden="true">{t("chat:activity.elapsed", { seconds })}</span>
      {seconds >= LONG_WAIT_HINT_AFTER_S && <span>{t("chat:activity.longWaitHint")}</span>}
    </div>
  );
};
