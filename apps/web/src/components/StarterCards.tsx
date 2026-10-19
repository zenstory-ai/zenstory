import React from "react";
import { useTranslation } from "react-i18next";
import { BookOpenCheck, PenLine, Sparkles, Upload } from "lucide-react";
import { nextStepKind } from "../lib/nextStep";

export type StarterKind = "example" | "manuscript";

interface StarterCardsProps {
  /** novel / short / screenplay: picks the example and the unit it asks for. */
  projectType: string | undefined;
  /** The work already has manuscript text: offer a read-through of it instead of the example. */
  hasManuscript: boolean;
  /** AI messages left today; null hides the line (Pro, or the quota is still loading). */
  remainingToday: number | null;
  /** Send the card's message exactly as shown. */
  onSend: (message: string, kind: StarterKind) => void;
  /** Put the card's message in the input to change it first. */
  onEdit: (message: string, kind: StarterKind) => void;
  /** Focus the input for the author's own idea. */
  onOwnIdea: () => void;
  /** Import manuscript files (.txt / .md) into the work. */
  onImport: () => void;
  isImporting: boolean;
}

/**
 * The empty chat of a work's first AI conversation. The featured card is shown in full and
 * 「发送」 sends exactly that text, the same as typing it and pressing Enter; rendering never
 * requests anything. A work with manuscript text gets a read-through of it; an empty one gets
 * an example plus a one-line import. See feature/2026-10-19-chat-starter-cards.md and
 * feature/2026-10-19-manuscript-import-entries.md.
 */
export const StarterCards: React.FC<StarterCardsProps> = ({
  projectType,
  hasManuscript,
  remainingToday,
  onSend,
  onEdit,
  onOwnIdea,
  onImport,
  isImporting,
}) => {
  const { t } = useTranslation(["chat"]);
  const kind: StarterKind = hasManuscript ? "manuscript" : "example";
  const message = hasManuscript
    ? t("chat:starter.manuscript.message")
    : t(`chat:starter.example.${nextStepKind(projectType)}`);
  const Icon = hasManuscript ? BookOpenCheck : Sparkles;
  return (
    <section data-testid="starter-cards" aria-labelledby="starter-cards-title" className="mx-auto w-full max-w-md space-y-2 py-2">
      <div className="px-1">
        <p id="starter-cards-title" className="text-sm font-semibold text-[hsl(var(--text-primary))]">
          {t("chat:starter.title")}
        </p>
        <p className="mt-0.5 text-xs leading-5 text-[hsl(var(--text-secondary))]">{t("chat:starter.subtitle")}</p>
      </div>

      <div
        data-testid={`starter-${kind}`}
        className="rounded-lg border border-[hsl(var(--accent-primary)/0.25)] bg-[hsl(var(--accent-primary)/0.06)] px-3 py-2.5"
      >
        <p className="flex items-center gap-1.5 text-xs font-medium text-[hsl(var(--text-primary))]">
          <Icon size={14} className="shrink-0 text-[hsl(var(--accent-primary))]" />
          {t(hasManuscript ? "chat:starter.manuscript.label" : "chat:starter.example.label")}
        </p>
        <p data-testid="starter-message-text" className="mt-1.5 pl-5 text-xs leading-5 text-[hsl(var(--text-primary))]">
          {message}
        </p>
        <div className="mt-2 flex items-center gap-1 pl-5">
          <button
            type="button"
            data-testid="starter-send"
            onClick={() => onSend(message, kind)}
            className="btn-primary rounded-md px-3 text-xs min-h-[44px] md:min-h-0 md:h-7"
          >
            {t("chat:starter.send")}
          </button>
          <button
            type="button"
            data-testid="starter-edit"
            onClick={() => onEdit(message, kind)}
            className="rounded-md px-2 text-xs min-h-[44px] md:min-h-0 md:h-7 text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--bg-tertiary))]"
          >
            {t("chat:starter.edit")}
          </button>
        </div>
        {hasManuscript && (
          <p className="mt-1.5 pl-5 text-xs leading-5 text-[hsl(var(--text-secondary))]">{t("chat:starter.manuscript.trust")}</p>
        )}
      </div>

      <div className="rounded-lg border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] px-3 py-2.5">
        <p className="flex items-center gap-1.5 text-xs font-medium text-[hsl(var(--text-primary))]">
          <PenLine size={14} className="shrink-0 text-[hsl(var(--accent-primary))]" />
          {t("chat:starter.own.label")}
        </p>
        <p className="mt-1 pl-5 text-xs leading-5 text-[hsl(var(--text-secondary))]">{t("chat:starter.own.description")}</p>
        <div className="mt-2 pl-5">
          <button
            type="button"
            data-testid="starter-own-idea"
            onClick={onOwnIdea}
            className="rounded-md border border-[hsl(var(--border-color))] px-3 text-xs min-h-[44px] md:min-h-0 md:h-7 text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))]"
          >
            {t("chat:starter.own.action")}
          </button>
        </div>
      </div>

      {!hasManuscript && (
        <button
          type="button"
          data-testid="starter-import"
          onClick={onImport}
          disabled={isImporting}
          className="flex min-h-[44px] md:min-h-0 items-center gap-1.5 rounded-md px-1 py-1 text-xs text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] disabled:opacity-50"
        >
          <Upload size={14} className="shrink-0" />
          {t("chat:starter.manuscript.import")}
        </button>
      )}

      {remainingToday !== null && (
        <p data-testid="starter-quota" className="px-1 text-xs leading-5 text-[hsl(var(--text-secondary))]">
          {t("chat:starter.quota", { count: remainingToday })}
        </p>
      )}
    </section>
  );
};
