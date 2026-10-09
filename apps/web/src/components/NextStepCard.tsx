import React from "react";
import { useTranslation } from "react-i18next";
import { PenLine } from "lucide-react";

interface NextStepCardProps {
  /** novel / short / screenplay: chapter 1, the opening, or episode 1. */
  projectType: string | undefined;
  /** Send the localized request right away. */
  onStart: (message: string) => void;
  onDismiss: () => void;
}

/**
 * Above the chat input once the framework exists but no prose does: one press
 * sends "write chapter 1" (the most common place new authors stopped).
 */
export const NextStepCard: React.FC<NextStepCardProps> = ({ projectType, onStart, onDismiss }) => {
  const { t } = useTranslation(["chat"]);
  const kind = projectType === "screenplay" || projectType === "short" ? projectType : "novel";
  return (
    <div
      data-testid="next-step-card"
      className="mb-2 flex flex-wrap items-center gap-2 rounded-lg border border-[hsl(var(--accent-primary)/0.25)] bg-[hsl(var(--accent-primary)/0.08)] px-3 py-2"
    >
      <PenLine size={14} className="shrink-0 text-[hsl(var(--accent-primary))]" />
      <span className="flex-1 min-w-0 text-xs text-[hsl(var(--text-primary))]">
        {t("chat:nextStep.frameworkReady")}
      </span>
      <button
        type="button"
        data-testid="next-step-start"
        onClick={() => onStart(t(`chat:nextStep.${kind}.message`))}
        className="btn-primary rounded-lg px-3 text-xs min-h-[44px] md:min-h-0 md:h-8"
      >
        {t(`chat:nextStep.${kind}.label`)}
      </button>
      <button
        type="button"
        onClick={onDismiss}
        className="rounded-lg px-2 text-xs min-h-[44px] md:min-h-0 md:h-8 text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--bg-tertiary))]"
      >
        {t("chat:nextStep.dismiss")}
      </button>
    </div>
  );
};
