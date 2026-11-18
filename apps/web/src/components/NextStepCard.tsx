import React from "react";
import { useTranslation } from "react-i18next";
import { PenLine } from "lucide-react";
import { nextStepKind } from "../lib/nextStep";
import {
  CHAT_CARD_PRIMARY_BUTTON,
  CHAT_CARD_PROMPT_SURFACE,
  CHAT_CARD_SECONDARY_BUTTON,
} from "./chatCardStyles";

interface NextStepCardProps {
  /** novel / short / screenplay: chapter 1, the opening, or episode 1. */
  projectType: string | undefined;
  /** Send the localized request right away. */
  onStart: (message: string) => void;
  onDismiss: () => void;
}

/**
 * Above the chat input once the framework exists but no prose does: one press
 * sends "write chapter 1" (the most common place new authors stopped). The
 * line and the button use the same unit as the project type (章 / 正文 / 剧本).
 */
export const NextStepCard: React.FC<NextStepCardProps> = ({ projectType, onStart, onDismiss }) => {
  const { t } = useTranslation(["chat"]);
  const kind = nextStepKind(projectType);
  return (
    <div
      data-testid="next-step-card"
      className={`mb-2 p-3 ${CHAT_CARD_PROMPT_SURFACE}`}
    >
      <p className="flex items-start gap-2 text-xs leading-5 text-[hsl(var(--text-primary))]">
        <PenLine size={15} className="mt-0.5 shrink-0 text-[hsl(var(--accent-primary))]" />
        <span>{t(`chat:nextStep.${kind}.frameworkReady`)}</span>
      </p>
      <div className="mt-2.5 flex flex-wrap items-center gap-2 pl-[23px]">
        <button
          type="button"
          data-testid="next-step-start"
          onClick={() => onStart(t(`chat:nextStep.${kind}.message`))}
          className={CHAT_CARD_PRIMARY_BUTTON}
        >
          {t(`chat:nextStep.${kind}.label`)}
        </button>
        <button
          type="button"
          onClick={onDismiss}
          className={CHAT_CARD_SECONDARY_BUTTON}
        >
          {t("chat:nextStep.dismiss")}
        </button>
      </div>
    </div>
  );
};
