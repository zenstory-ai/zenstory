import React from "react";
import { useTranslation } from "react-i18next";
import { PenLine, Sparkles } from "lucide-react";
import { nextStepKind } from "../lib/nextStep";

interface StarterCardsProps {
  /** novel / short / screenplay: picks the example and the unit it asks for. */
  projectType: string | undefined;
  /** AI messages left today; null hides the line (Pro, or the quota is still loading). */
  remainingToday: number | null;
  /** Send the example exactly as shown. */
  onSend: (message: string) => void;
  /** Put the example in the input to change it first. */
  onEdit: (message: string) => void;
  /** Focus the input for the author's own idea. */
  onOwnIdea: () => void;
}

/**
 * The empty chat of a work's first AI conversation. The example is shown in full and
 * 「发送」 sends exactly that text, the same as typing it and pressing Enter; rendering
 * never requests anything. See feature/2026-10-19-chat-starter-cards.md.
 */
export const StarterCards: React.FC<StarterCardsProps> = ({
  projectType,
  remainingToday,
  onSend,
  onEdit,
  onOwnIdea,
}) => {
  const { t } = useTranslation(["chat"]);
  const example = t(`chat:starter.example.${nextStepKind(projectType)}`);
  return (
    <section data-testid="starter-cards" aria-labelledby="starter-cards-title" className="mx-auto w-full max-w-md space-y-2 py-2">
      <div className="px-1">
        <p id="starter-cards-title" className="text-sm font-semibold text-[hsl(var(--text-primary))]">
          {t("chat:starter.title")}
        </p>
        <p className="mt-0.5 text-xs leading-5 text-[hsl(var(--text-secondary))]">{t("chat:starter.subtitle")}</p>
      </div>

      <div className="rounded-lg border border-[hsl(var(--accent-primary)/0.25)] bg-[hsl(var(--accent-primary)/0.06)] px-3 py-2.5">
        <p className="flex items-center gap-1.5 text-xs font-medium text-[hsl(var(--text-primary))]">
          <Sparkles size={14} className="shrink-0 text-[hsl(var(--accent-primary))]" />
          {t("chat:starter.example.label")}
        </p>
        <p data-testid="starter-example-text" className="mt-1.5 pl-5 text-xs leading-5 text-[hsl(var(--text-primary))]">
          {example}
        </p>
        <div className="mt-2 flex items-center gap-1 pl-5">
          <button
            type="button"
            data-testid="starter-send"
            onClick={() => onSend(example)}
            className="btn-primary rounded-md px-3 text-xs min-h-[44px] md:min-h-0 md:h-7"
          >
            {t("chat:starter.send")}
          </button>
          <button
            type="button"
            data-testid="starter-edit"
            onClick={() => onEdit(example)}
            className="rounded-md px-2 text-xs min-h-[44px] md:min-h-0 md:h-7 text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--bg-tertiary))]"
          >
            {t("chat:starter.edit")}
          </button>
        </div>
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

      {remainingToday !== null && (
        <p data-testid="starter-quota" className="px-1 text-xs leading-5 text-[hsl(var(--text-secondary))]">
          {t("chat:starter.quota", { count: remainingToday })}
        </p>
      )}
    </section>
  );
};
