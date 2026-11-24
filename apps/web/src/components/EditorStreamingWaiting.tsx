import React, { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";

/**
 * Self-contained elapsed-seconds timer so `SimpleEditor` does not re-render every second
 * while waiting for the first streamed character of a newly created or replaced chapter.
 * Mounts only while waiting, so each wait resets cleanly from 0s without effect setState.
 */
function useElapsedSeconds(): number {
  const [startedAt] = useState(() => Date.now());
  const [now, setNow] = useState(startedAt);

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  return Math.max(0, Math.floor((now - startedAt) / 1000));
}

const BannerElapsedLabel: React.FC = () => {
  const { t } = useTranslation(["editor"]);
  const seconds = useElapsedSeconds();
  return (
    <span
      data-testid="editor-streaming-banner-elapsed"
      aria-hidden="true"
      className="ml-auto shrink-0 text-xs text-[hsl(var(--text-secondary))] tabular-nums"
    >
      {t("editor:aiDraftingBannerElapsed", { seconds })}
    </span>
  );
};

export const EditorStreamingBanner: React.FC<{
  isWaitingForFirstToken: boolean;
  title: string;
}> = ({ isWaitingForFirstToken, title }) => {
  const { t } = useTranslation(["editor"]);
  const trimmedTitle = title.trim();

  const message = isWaitingForFirstToken
    ? trimmedTitle
      ? t("editor:aiDraftingOpening", { title: trimmedTitle })
      : t("editor:aiDraftingOpeningUntitled")
    : t("editor:aiWriting");

  return (
    <div
      data-testid="editor-streaming-banner"
      className="shrink-0 bg-[hsl(var(--accent-primary)/0.08)] px-4 sm:px-6 py-2.5 flex items-center gap-2.5 border-b border-[hsl(var(--accent-primary)/0.2)] motion-banner-enter"
    >
      <Loader2
        size={16}
        className="shrink-0 animate-spin motion-reduce:animate-none text-[hsl(var(--accent-primary))]"
        aria-hidden="true"
      />
      <span
        role="status"
        aria-live="polite"
        className="text-sm text-[hsl(var(--accent-primary))] font-medium break-words"
      >
        {message}
      </span>
      {isWaitingForFirstToken && <BannerElapsedLabel />}
    </div>
  );
};

/**
 * Quiet visual placeholder inside the blank canvas before the first streamed byte arrives.
 * Status text and elapsed time live once in `EditorStreamingBanner` above to avoid repetition.
 */
export const EditorStreamingCanvasWaiting: React.FC = () => {
  return (
    <div
      data-testid="editor-streaming-waiting"
      aria-hidden="true"
      className="pointer-events-none select-none absolute inset-x-0 top-0 py-1.5 space-y-3 max-w-xl"
    >
      <div className="h-3 w-4/5 rounded-full bg-[hsl(var(--text-secondary)/0.12)] animate-pulse motion-reduce:animate-none" />
      <div className="h-3 w-full rounded-full bg-[hsl(var(--text-secondary)/0.1)] animate-pulse motion-reduce:animate-none" />
      <div className="h-3 w-3/5 rounded-full bg-[hsl(var(--text-secondary)/0.08)] animate-pulse motion-reduce:animate-none" />
    </div>
  );
};
