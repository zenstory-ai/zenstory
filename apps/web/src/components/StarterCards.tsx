import React from "react";
import { useTranslation } from "react-i18next";
import { Sparkles } from "lucide-react";
import { nextStepKind } from "../lib/nextStep";

interface StarterCardsProps {
  /** novel / short / screenplay: picks the example premise. */
  projectType: string | undefined;
  /** AI messages left today; null hides the line (Pro, or the quota is still loading). */
  remainingToday: number | null;
  /** Send the example premise exactly as shown. */
  onSend: (message: string) => void;
  /** Put the example in the input to change it first. */
  onEdit: (message: string) => void;
  /** Briefly lit up when the author is sent here from elsewhere (the empty editor's AI button). */
  highlighted?: boolean;
  /**
   * The work already has non-empty prose the author brought (imported or typed; no AI has
   * replied in it yet): offer requests about that manuscript instead of the example premise.
   */
  hasManuscript?: boolean;
  /** Send one manuscript request exactly as shown; `kind` names which one (for analytics). */
  onSendManuscript?: (message: string, kind: ManuscriptStarterKind) => void;
}

export const MANUSCRIPT_STARTER_KINDS = ["review", "characters", "continue"] as const;
export type ManuscriptStarterKind = (typeof MANUSCRIPT_STARTER_KINDS)[number];

/**
 * The empty chat of a work's first AI conversation, written as the assistant's opening
 * line: say an idea and it writes the opening, or try the example premise as is. Rendering
 * never requests anything; only the author's press sends, and the sent words are exactly
 * the premise shown. See feature/2026-10-19-chat-starter-cards.md.
 *
 * A work that already holds the author's own prose gets manuscript requests instead (read it
 * through without changing it, list the characters, continue in its style); each button is the
 * full sentence it sends. See feature/2026-10-31-manuscript-starter-cards.md.
 */
export const StarterCards: React.FC<StarterCardsProps> = ({
  projectType,
  remainingToday,
  onSend,
  onEdit,
  highlighted = false,
  hasManuscript = false,
  onSendManuscript,
}) => {
  const { t } = useTranslation(["chat"]);
  const kind = nextStepKind(projectType);
  const example = t(`chat:starter.example.${kind}`);
  const quotaLine = remainingToday !== null && (
    <p data-testid="starter-quota" className="text-xs text-[hsl(var(--text-secondary))]">
      {t("chat:starter.quota", { count: remainingToday })}
    </p>
  );
  const avatar = (
    <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-[hsl(var(--accent-primary)/0.12)] text-[hsl(var(--accent-primary))]">
      <Sparkles size={14} />
    </span>
  );

  if (hasManuscript && onSendManuscript) {
    return (
      <section data-testid="starter-cards" data-variant="manuscript" aria-label={t("chat:starter.label")} className="mx-auto w-full max-w-md py-2">
        <div className="flex items-start gap-2">
          {avatar}
          <div className="min-w-0 flex-1 space-y-2.5">
            <div className="rounded-xl rounded-tl-sm bg-[hsl(var(--bg-tertiary))] px-3 py-2.5 text-sm leading-6 text-[hsl(var(--text-primary))]">
              <p>{t("chat:starter.manuscript.title")}</p>
              <p>{t("chat:starter.manuscript.body")}</p>
            </div>
            <div
              data-testid="starter-manuscript"
              data-highlighted={highlighted || undefined}
              className={`space-y-1.5 rounded-lg transition-[box-shadow] duration-slow ${
                highlighted ? "shadow-[0_0_0_3px_hsl(var(--accent-primary)/0.18)]" : ""
              }`}
            >
              {MANUSCRIPT_STARTER_KINDS.map((requestKind) => {
                const message = t(`chat:starter.manuscript.${requestKind}.${kind}`);
                return (
                  <button
                    key={requestKind}
                    type="button"
                    data-testid={`starter-manuscript-${requestKind}`}
                    onClick={() => onSendManuscript(message, requestKind)}
                    className="block w-full min-h-[44px] md:min-h-0 rounded-lg border border-[hsl(var(--accent-primary)/0.25)] bg-[hsl(var(--accent-primary)/0.05)] px-3 py-2 text-left text-sm leading-6 text-[hsl(var(--text-primary))] [overflow-wrap:anywhere] hover:border-[hsl(var(--accent-primary))] hover:bg-[hsl(var(--accent-primary)/0.1)]"
                  >
                    {message}
                  </button>
                );
              })}
            </div>
            {quotaLine}
          </div>
        </div>
      </section>
    );
  }

  return (
    <section data-testid="starter-cards" data-variant="example" aria-label={t("chat:starter.label")} className="mx-auto w-full max-w-md py-2">
      <div className="flex items-start gap-2">
        {avatar}
        <div className="min-w-0 flex-1 space-y-2.5">
          <div className="rounded-xl rounded-tl-sm bg-[hsl(var(--bg-tertiary))] px-3 py-2.5 text-sm leading-6 text-[hsl(var(--text-primary))]">
            <p>{t("chat:starter.greeting.title")}</p>
            <p>{t("chat:starter.greeting.body")}</p>
          </div>

          <div data-testid="starter-example">
            <p className="text-xs text-[hsl(var(--text-secondary))]">{t("chat:starter.example.hint")}</p>
            <div
              data-highlighted={highlighted || undefined}
              className={`mt-1.5 rounded-lg border bg-[hsl(var(--accent-primary)/0.05)] px-3 py-2.5 transition-[border-color,box-shadow] duration-slow ${
                highlighted
                  ? "border-[hsl(var(--accent-primary))] shadow-[0_0_0_3px_hsl(var(--accent-primary)/0.18)]"
                  : "border-[hsl(var(--accent-primary)/0.25)]"
              }`}
            >
              <p data-testid="starter-message-text" className="text-sm leading-6 text-[hsl(var(--text-primary))]">
                {example}
              </p>
              <div className="mt-2 flex flex-wrap items-center gap-1">
                <button
                  type="button"
                  data-testid="starter-send"
                  onClick={() => onSend(example)}
                  className="btn-primary rounded-md px-3 text-xs min-h-[44px] md:min-h-0 md:h-7"
                >
                  {t("chat:starter.example.use")}
                </button>
                <button
                  type="button"
                  data-testid="starter-edit"
                  onClick={() => onEdit(example)}
                  className="rounded-md px-2 text-xs min-h-[44px] md:min-h-0 md:h-7 text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--bg-tertiary))] hover:text-[hsl(var(--text-primary))]"
                >
                  {t("chat:starter.example.edit")}
                </button>
              </div>
            </div>
          </div>

          {quotaLine}
        </div>
      </div>
    </section>
  );
};
