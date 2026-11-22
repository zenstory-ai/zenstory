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
      className="shrink-0 bg-[hsl(var(--accent-primary)/0.08)] px-4 sm:px-6 py-2.5 flex items-center gap-2.5 border-b border-[hsl(var(--accent-primary)/0.2)]"
    >
      <Loader2
        size={16}
        className="shrink-0 animate-spin motion-reduce:animate-none text-[hsl(var(--accent-primary))]"
        aria-hidden="true"
      />
      <span className="text-sm text-[hsl(var(--accent-primary))] font-medium break-words">
        {message}
      </span>
      {isWaitingForFirstToken && <BannerElapsedLabel />}
    </div>
  );
};

export const EditorStreamingCanvasWaiting: React.FC<{
  title: string;
}> = ({ title }) => {
  const { t } = useTranslation(["editor"]);
  const seconds = useElapsedSeconds();
  const trimmedTitle = title.trim();

  const message = trimmedTitle
    ? t("editor:aiDraftingOpening", { title: trimmedTitle })
    : t("editor:aiDraftingOpeningUntitled");

  return (
    <div
      data-testid="editor-streaming-waiting"
      role="status"
      aria-live="polite"
      className="pointer-events-none select-none absolute inset-x-0 top-0 py-1 space-y-4"
    >
      <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-sm text-[hsl(var(--text-secondary))]">
        <span
          aria-hidden="true"
          className="inline-block h-2 w-2 rounded-full bg-[hsl(var(--accent-primary))] animate-pulse motion-reduce:animate-none"
        />
        <span className="text-[hsl(var(--text-primary))] font-medium break-words">
          {message}
        </span>
        <span className="text-xs text-[hsl(var(--text-secondary))] tabular-nums">
          {t("editor:aiDraftingElapsed", { seconds })}
        </span>
      </div>
      <div aria-hidden="true" className="space-y-2.5 max-w-xl pt-1">
        <div className="h-3 w-4/5 rounded-full bg-[hsl(var(--text-secondary)/0.12)] animate-pulse motion-reduce:animate-none" />
        <div className="h-3 w-full rounded-full bg-[hsl(var(--text-secondary)/0.1)] animate-pulse motion-reduce:animate-none" />
        <div className="h-3 w-3/5 rounded-full bg-[hsl(var(--text-secondary)/0.08)] animate-pulse motion-reduce:animate-none" />
      </div>
    </div>
  );
};
