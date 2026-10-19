import React from "react";
import { useTranslation } from "react-i18next";
import { Sparkles, Upload } from "lucide-react";
import { nextStepKind } from "../lib/nextStep";

export type StarterKind = "example" | "manuscript";

interface StarterCardsProps {
  /** novel / short / screenplay: picks the example premise. */
  projectType: string | undefined;
  /** The work already has manuscript text: offer to read it instead of an example. */
  hasManuscript: boolean;
  /** AI messages left today; null hides the line (Pro, or the quota is still loading). */
  remainingToday: number | null;
  /** Offer the one-line import (hidden where the editor's empty state already shows it). */
  showImport: boolean;
  /** Send the message the button names, as the author. */
  onSend: (message: string, kind: StarterKind) => void;
  /** Put the example in the input to change it first. */
  onEdit: (message: string, kind: StarterKind) => void;
  /** Focus the input for something else. */
  onAskOther: () => void;
  /** Import manuscript files (.txt / .md) into the work. */
  onImport: () => void;
  isImporting: boolean;
}

/**
 * The empty chat of a work's first AI conversation, written as the assistant's opening
 * line: say an idea and it writes the opening, or try the example premise as is. A work
 * that already has text is offered a read-through instead. Rendering never requests
 * anything; only the author's press sends, and the sent words appear as the author's
 * message. See feature/2026-10-19-chat-starter-cards.md.
 */
export const StarterCards: React.FC<StarterCardsProps> = ({
  projectType,
  hasManuscript,
  remainingToday,
  showImport,
  onSend,
  onEdit,
  onAskOther,
  onImport,
  isImporting,
}) => {
  const { t } = useTranslation(["chat"]);
  const example = t(`chat:starter.example.${nextStepKind(projectType)}`);
  const manuscriptMessage = t("chat:starter.manuscript.message");
  const secondaryButton =
    "rounded-md px-2 text-xs min-h-[44px] md:min-h-0 md:h-7 text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--bg-tertiary))] hover:text-[hsl(var(--text-primary))]";
  const primaryButton = "btn-primary rounded-md px-3 text-xs min-h-[44px] md:min-h-0 md:h-7";

  return (
    <section data-testid="starter-cards" aria-label={t("chat:starter.label")} className="mx-auto w-full max-w-md py-2">
      <div className="flex items-start gap-2">
        <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-[hsl(var(--accent-primary)/0.12)] text-[hsl(var(--accent-primary))]">
          <Sparkles size={14} />
        </span>
        <div className="min-w-0 flex-1 space-y-2.5">
          <div className="rounded-xl rounded-tl-sm bg-[hsl(var(--bg-tertiary))] px-3 py-2.5 text-sm leading-6 text-[hsl(var(--text-primary))]">
            {hasManuscript ? (
              <p data-testid="starter-manuscript">{t("chat:starter.manuscript.greeting")}</p>
            ) : (
              <>
                <p>{t("chat:starter.greeting.title")}</p>
                <p>{t("chat:starter.greeting.body")}</p>
              </>
            )}
          </div>

          {hasManuscript ? (
            <div className="flex flex-wrap items-center gap-1">
              <button
                type="button"
                data-testid="starter-send"
                onClick={() => onSend(manuscriptMessage, "manuscript")}
                className={primaryButton}
              >
                {t("chat:starter.manuscript.send")}
              </button>
              <button type="button" data-testid="starter-ask-other" onClick={onAskOther} className={secondaryButton}>
                {t("chat:starter.manuscript.other")}
              </button>
            </div>
          ) : (
            <div data-testid="starter-example">
              <p className="text-xs text-[hsl(var(--text-secondary))]">{t("chat:starter.example.hint")}</p>
              <div className="mt-1.5 rounded-lg border border-[hsl(var(--accent-primary)/0.25)] bg-[hsl(var(--accent-primary)/0.05)] px-3 py-2.5">
                <p data-testid="starter-message-text" className="text-sm leading-6 text-[hsl(var(--text-primary))]">
                  {example}
                </p>
                <div className="mt-2 flex flex-wrap items-center gap-1">
                  <button type="button" data-testid="starter-send" onClick={() => onSend(example, "example")} className={primaryButton}>
                    {t("chat:starter.example.use")}
                  </button>
                  <button type="button" data-testid="starter-edit" onClick={() => onEdit(example, "example")} className={secondaryButton}>
                    {t("chat:starter.example.edit")}
                  </button>
                </div>
              </div>
            </div>
          )}

          {!hasManuscript && showImport && (
            <button
              type="button"
              data-testid="starter-import"
              onClick={onImport}
              disabled={isImporting}
              className="flex min-h-[44px] md:min-h-0 items-center gap-1.5 rounded-md py-1 text-xs text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] disabled:opacity-50"
            >
              <Upload size={14} className="shrink-0" />
              {t("chat:starter.manuscript.import")}
            </button>
          )}

          {remainingToday !== null && (
            <p data-testid="starter-quota" className="text-xs text-[hsl(var(--text-secondary))]">
              {t("chat:starter.quota", { count: remainingToday })}
            </p>
          )}
        </div>
      </div>
    </section>
  );
};
