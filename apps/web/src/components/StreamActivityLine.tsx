import React, { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";

/** After this long without the round finishing, reassure the author that long text takes time. */
export const LONG_WAIT_HINT_AFTER_S = 20;

/**
 * One quiet line under the chat while a round runs: how long it has been, plus a
 * reassurance once it gets long (authors used to stop rounds that looked stuck).
 * What the agent is doing is already shown by the step chips above. Ticks on its
 * own so the chat panel does not re-render every second.
 */
export const StreamActivityLine: React.FC<{ startedAt: number | null }> = ({ startedAt }) => {
  const { t } = useTranslation(["chat"]);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  if (startedAt === null) return null;
  const seconds = Math.max(0, Math.floor((now - startedAt) / 1000));

  return (
    <div
      data-testid="stream-activity-line"
      className="mt-2 flex flex-wrap items-center gap-x-1.5 text-xs text-[hsl(var(--text-secondary))]"
    >
      <Loader2 size={12} className="animate-spin shrink-0" />
      <span aria-hidden="true">{t("chat:activity.elapsed", { seconds })}</span>
      {seconds >= LONG_WAIT_HINT_AFTER_S && (
        <span role="status" aria-live="polite">{t("chat:activity.longWaitHint")}</span>
      )}
    </div>
  );
};
