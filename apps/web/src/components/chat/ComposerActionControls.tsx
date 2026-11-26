import React from "react";
import { Send, Square } from "lucide-react";
import { useTranslation } from "react-i18next";
import { VoiceInputButton } from "../VoiceInputButton";
import { MAX_AGENT_MESSAGE_CHARS } from "../../lib/agentLimits";

interface ComposerActionControlsProps {
  isMobile: boolean;
  input: string;
  inputDisabled: boolean;
  effectiveSendDisabled: boolean;
  inputTooLong: boolean;
  steerActive: boolean;
  steerPending: boolean;
  hasCancelHandler: boolean;
  isStopping: boolean;
  stopArmed: boolean;
  stopButtonLabel: string;
  onVoiceResult: (text: string) => void;
  onSteer: () => void;
  onCancel: () => void;
  onSubmit: () => void;
}

export const ComposerActionControls: React.FC<ComposerActionControlsProps> = ({
  isMobile,
  input,
  inputDisabled,
  effectiveSendDisabled,
  inputTooLong,
  steerActive,
  steerPending,
  hasCancelHandler,
  isStopping,
  stopArmed,
  stopButtonLabel,
  onVoiceResult,
  onSteer,
  onCancel,
  onSubmit,
}) => {
  const { t } = useTranslation(["chat", "common"]);

  return (
    <div
      className={
        isMobile
          ? "shrink-0 flex items-center"
          : "shrink-0 flex items-center justify-between gap-2 px-2 pb-1.5 pt-0.5"
      }
    >
      {!isMobile && (
        <div className="flex items-center gap-1">
          {/* 语音输入按钮 */}
          <VoiceInputButton
            onResult={onVoiceResult}
            disabled={inputDisabled}
            className="!border-0 !bg-transparent hover:!bg-[hsl(var(--bg-tertiary))] !rounded-full"
          />
        </div>
      )}

      <div className="flex items-center gap-1.5">
        {steerActive && input.trim() ? (
          <button
            onClick={onSteer}
            disabled={!input.trim() || steerPending || inputTooLong}
            className={`shrink-0 flex items-center justify-center bg-[hsl(var(--primary))] hover:bg-[hsl(var(--primary-hover))] text-[hsl(var(--primary-foreground))] disabled:bg-[hsl(var(--bg-tertiary))] disabled:text-[hsl(var(--text-secondary))] disabled:opacity-60 disabled:cursor-not-allowed rounded-full transition-[transform,background-color,color] duration-[var(--duration-instant)] ease-[var(--ease-standard)] active:scale-[0.95] motion-reduce:active:scale-100 focus-visible:outline-none focus-visible:shadow-[0_0_0_2px_hsl(var(--bg-secondary)),_0_0_0_4px_hsl(var(--accent-primary))] ${isMobile ? "w-11 h-11" : "w-8 h-8"}`}
            title={t("chat:input.steerButton")}
            aria-label={t("chat:input.steerButton")}
            data-testid="steer-button"
          >
            <Send key="steer-icon" size={15} className="motion-icon-pop" />
          </button>
        ) : hasCancelHandler && effectiveSendDisabled ? (
          <button
            onClick={onCancel}
            // While arming the button stays clickable (aria-disabled) so a press gets an answer.
            disabled={isStopping}
            aria-disabled={!stopArmed || isStopping}
            className={`shrink-0 flex items-center justify-center rounded-full transition-[transform,background-color,color] duration-[var(--duration-instant)] ease-[var(--ease-standard)] active:scale-[0.95] motion-reduce:active:scale-100 focus-visible:outline-none ${
              !stopArmed || isStopping
                ? // Muted while arming: secondary ink on the tertiary fill, hairline ring.
                  "bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-secondary))] shadow-[inset_0_0_0_1px_hsl(var(--border-color))] cursor-not-allowed focus-visible:shadow-[0_0_0_2px_hsl(var(--bg-secondary)),_0_0_0_4px_hsl(var(--accent-primary))]"
                : // Same solid danger colors as ui/Button's danger variant.
                  "bg-[hsl(var(--danger-solid))] text-[hsl(var(--primary-foreground))] hover:bg-[hsl(var(--danger-hover))] focus-visible:shadow-[0_0_0_2px_hsl(var(--bg-secondary)),_0_0_0_4px_hsl(var(--error))]"
            } ${isMobile ? "w-11 h-11" : "w-8 h-8"}`}
            title={stopButtonLabel}
            aria-label={stopButtonLabel}
            data-testid="stop-button"
          >
            <Square key="stop-icon" size={13} fill="currentColor" className="motion-icon-pop" />
          </button>
        ) : (
          <button
            type="button"
            onClick={onSubmit}
            disabled={!input.trim() || effectiveSendDisabled || inputTooLong}
            className={`shrink-0 flex items-center justify-center bg-[hsl(var(--primary))] hover:bg-[hsl(var(--primary-hover))] text-[hsl(var(--primary-foreground))] disabled:bg-[hsl(var(--bg-tertiary))] disabled:text-[hsl(var(--text-secondary))] disabled:opacity-60 disabled:cursor-not-allowed rounded-full transition-[transform,background-color,color] duration-[var(--duration-instant)] ease-[var(--ease-standard)] active:scale-[0.95] disabled:active:scale-100 motion-reduce:active:scale-100 focus-visible:outline-none focus-visible:shadow-[0_0_0_2px_hsl(var(--bg-secondary)),_0_0_0_4px_hsl(var(--accent-primary))] disabled:focus-visible:shadow-[0_0_0_2px_hsl(var(--bg-secondary)),_0_0_0_4px_hsl(var(--bg-tertiary))] ${isMobile ? "w-11 h-11" : "w-8 h-8"}`}
            title={
              inputTooLong
                ? t("chat:input.tooLong", { count: input.length, max: MAX_AGENT_MESSAGE_CHARS })
                : effectiveSendDisabled
                  ? t("chat:input.sendDisabledBusy")
                  : !input.trim()
                    ? t("chat:input.sendDisabledEmpty")
                    : t("chat:input.sendWithShortcut")
            }
            aria-label={t("common:send")}
            data-testid="send-button"
          >
            <Send key="send-icon" size={15} className="motion-icon-pop" />
          </button>
        )}
      </div>
    </div>
  );
};
