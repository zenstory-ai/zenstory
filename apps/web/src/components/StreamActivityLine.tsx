import React, { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import type { BodyWriteSnapshot } from "../lib/agentRoundProgress";

/** After this long without the round finishing, reassure the author that long text takes time. */
export const LONG_WAIT_HINT_AFTER_S = 20;

/**
 * One quiet line under the chat while a round runs: how long it has been, plus a
 * reassurance once it gets long (authors used to stop rounds that looked stuck).
 * What the agent is doing is already shown by the step chips above, except while a file gets
 * its text: then the line names the file and the words written so far (`bodyWrite`, read on
 * each tick from the round's own events), which also covers an empty-file correction rerun.
 * Ticks on its own so the chat panel does not re-render every second.
 */
export const StreamActivityLine: React.FC<{
  startedAt: number | null;
  isMobile?: boolean;
  bodyWrite?: () => BodyWriteSnapshot | null;
}> = ({ startedAt, isMobile = false, bodyWrite }) => {
  const { t, i18n } = useTranslation(["chat"]);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  if (startedAt === null) return null;
  const seconds = Math.max(0, Math.floor((now - startedAt) / 1000));
  const writing = bodyWrite?.() ?? null;
  let stage: string | null = null;
  if (writing) {
    let numberFormat: Intl.NumberFormat;
    try {
      numberFormat = new Intl.NumberFormat(i18n?.language || undefined);
    } catch {
      numberFormat = new Intl.NumberFormat();
    }
    stage = writing.words > 0
      ? t("chat:activity.writingBodyWords", { title: writing.title, count: writing.words, words: numberFormat.format(writing.words) })
      : t("chat:activity.writingBody", { title: writing.title });
  }

  return (
    <div
      data-testid="stream-activity-line"
      className="mt-2 flex flex-wrap items-center gap-x-1.5 text-xs text-[hsl(var(--text-secondary))]"
    >
      <Loader2 size={12} className="animate-spin shrink-0" />
      {stage && (
        <>
          <span aria-hidden="true" className="break-words">{stage}</span>
          <span aria-hidden="true">·</span>
        </>
      )}
      <span aria-hidden="true">{stage ? t("chat:activity.seconds", { seconds }) : t("chat:activity.elapsed", { seconds })}</span>
      {seconds >= LONG_WAIT_HINT_AFTER_S && (
        // Phones: switching apps can drop the stream, and a dropped round does not resume.
        <span role="status" aria-live="polite">{t(isMobile ? "chat:activity.longWaitHintMobile" : "chat:activity.longWaitHint")}</span>
      )}
    </div>
  );
};
