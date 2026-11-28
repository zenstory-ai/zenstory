/**
 * Status banners, progress cards, round-end notes, and overlay controls for ChatPanel.
 *
 * @module components/chat/ChatStatusBanners
 */

import React from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, ArrowDown, Edit3, Loader2 } from "lucide-react";
import type { EditProgress } from "../../hooks/useChatStreaming";
import type { Message } from "../MessageList";
import { Button } from "../ui/Button";
import {
  CHAT_CARD_SECONDARY_BUTTON,
  CHAT_CARD_SURFACE,
  CHAT_CARD_WARNING_SURFACE,
} from "../chatCardStyles";
import { isAccountRunRefusal } from "./chatHistoryHelpers";

export const SessionRecoveryAlert: React.FC<{
  isLoadingHistory: boolean;
  onRetry: () => void;
}> = ({ isLoadingHistory, onRetry }) => {
  const { t } = useTranslation(["chat", "common"]);
  return (
    <div role="alert" className={`mb-3 p-3 ${CHAT_CARD_SURFACE}`}>
      <p className="text-xs leading-5 text-[hsl(var(--text-primary))]">
        {t("chat:panel.sessionRecoveryFailed")}
      </p>
      <button
        type="button"
        className={`mt-2.5 ${CHAT_CARD_SECONDARY_BUTTON}`}
        disabled={isLoadingHistory}
        onClick={onRetry}
      >
        {t("common:retry")}
      </button>
    </div>
  );
};

export const LoadEarlierMessagesButton: React.FC<{
  isLoadingMoreHistory: boolean;
  isDisabled: boolean;
  onLoadEarlier: () => void;
}> = ({ isLoadingMoreHistory, isDisabled, onLoadEarlier }) => {
  const { t } = useTranslation(["chat"]);
  return (
    <div className="mb-3 flex justify-center">
      <button
        type="button"
        data-testid="chat-load-earlier-messages"
        disabled={isDisabled}
        onClick={onLoadEarlier}
        className="inline-flex min-h-11 md:min-h-0 items-center gap-1.5 rounded-full border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] px-3.5 py-2 md:px-3 md:py-1 text-xs text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--bg-tertiary))] hover:text-[hsl(var(--text-primary))] disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-primary))]"
      >
        {isLoadingMoreHistory ? (
          <>
            <Loader2 size={12} className="animate-spin" aria-hidden="true" />
            {t("chat:panel.loadingEarlierMessages")}
          </>
        ) : (
          t("chat:panel.loadEarlierMessages")
        )}
      </button>
    </div>
  );
};

export const EditProgressCard: React.FC<{
  editProgress: EditProgress;
  isMobile: boolean;
}> = ({ editProgress, isMobile }) => {
  const { t } = useTranslation(["chat"]);
  return (
    <div className="mt-2.5">
      <div className={`${CHAT_CARD_SURFACE} ${isMobile ? "px-3 py-2.5" : "p-3"}`}>
        <div className="flex items-center gap-2 mb-2">
          <Edit3 className="w-4 h-4 shrink-0 text-[hsl(var(--accent-primary))]" />
          <span
            className={`text-[hsl(var(--text-primary))] font-medium ${
              isMobile ? "text-xs truncate flex-1" : "text-sm"
            }`}
          >
            {t("chat:panel.editing")} {editProgress.title}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex-1 bg-[hsl(var(--bg-tertiary))] rounded-full h-1.5 overflow-hidden">
            <div
              className="h-full w-full bg-[hsl(var(--accent-primary))] motion-progress-fill"
              style={{
                transform: `scaleX(${
                  editProgress.totalEdits > 0
                    ? Math.min(1, editProgress.completedEdits / editProgress.totalEdits)
                    : 0
                })`,
              }}
            />
          </div>
          <span className="tabular-nums text-xs text-[hsl(var(--text-secondary))]">
            {editProgress.completedEdits}/{editProgress.totalEdits}
          </span>
        </div>
        {editProgress.currentOp && (
          <div className="mt-1.5 text-xs text-[hsl(var(--text-secondary))] truncate">
            {t("chat:panel.operation")} {editProgress.currentOp}
          </div>
        )}
      </div>
    </div>
  );
};

export const ChatStreamErrorCard: React.FC<{
  error: string;
  errorCode: string | null;
  isAiQuotaLimit: boolean;
  isNetworkQuotaLimit: boolean;
  aiSendBlocked: boolean;
  canRetry: boolean;
  isMobile: boolean;
  onRetry: () => void;
  onOpenUpgradeModal: () => void;
}> = ({
  error,
  errorCode,
  isAiQuotaLimit,
  isNetworkQuotaLimit,
  aiSendBlocked,
  canRetry,
  isMobile,
  onRetry,
  onOpenUpgradeModal,
}) => {
  const { t } = useTranslation(["chat", "common", "dashboard"]);
  return (
    <div role="alert" className={`mt-2.5 p-3 ${CHAT_CARD_WARNING_SURFACE}`}>
      <div className="flex items-start gap-2.5">
        <AlertTriangle
          size={15}
          aria-hidden="true"
          className="text-[hsl(var(--warning-text))] mt-0.5 shrink-0"
        />
        <div className="flex-1 min-w-0">
          <p className="text-[hsl(var(--warning-text))] text-xs font-semibold leading-5">
            {isAiQuotaLimit
              ? t("chat:panel.quotaExceededTitle")
              : isNetworkQuotaLimit
                ? t("chat:panel.networkQuotaExceededTitle")
                : isAccountRunRefusal(errorCode)
                  ? t("chat:panel.accountRunsBusyTitle")
                  : t("chat:panel.streamErrorTitle")}
          </p>
          {!isAiQuotaLimit && !isNetworkQuotaLimit && (
            <p className="mt-0.5 text-[hsl(var(--text-primary))] text-xs leading-5 break-words whitespace-pre-line">
              {aiSendBlocked ? t("chat:panel.streamErrorQuotaPaused") : error}
            </p>
          )}
          {canRetry && (
            <button
              type="button"
              onClick={onRetry}
              className={`mt-2.5 ${CHAT_CARD_SECONDARY_BUTTON}`}
            >
              {t("common:retry")}
            </button>
          )}
          {(isAiQuotaLimit || isNetworkQuotaLimit) && (
            <div className="mt-1 space-y-2.5">
              <p className="text-xs leading-5 text-[hsl(var(--text-secondary))]">
                {isNetworkQuotaLimit
                  ? t("chat:panel.networkQuotaExceededHint")
                  : t("chat:panel.quotaExceededHint")}
              </p>
              <Button
                type="button"
                variant="primary"
                size={isMobile ? "touch" : "sm"}
                onClick={onOpenUpgradeModal}
              >
                {t("dashboard:billing.ctaUpgradePro")}
              </Button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export const RoundStatusNotes: React.FC<{
  userStopNote: string | null;
  quotaRefundNote: string | null;
  resendAfterStop: boolean;
  unansweredUserMessage: Message | null;
  aiSendBlocked: boolean;
  onResendLastRound: () => void;
  onRetryUnanswered: (message: Message) => void;
}> = ({
  userStopNote,
  quotaRefundNote,
  resendAfterStop,
  unansweredUserMessage,
  aiSendBlocked,
  onResendLastRound,
  onRetryUnanswered,
}) => {
  const { t } = useTranslation(["chat"]);
  return (
    <>
      {userStopNote && (
        <p
          data-testid="chat-user-stop-note"
          role="status"
          className="mt-2 text-xs text-[hsl(var(--text-secondary))]"
        >
          {userStopNote}
        </p>
      )}

      {quotaRefundNote && (
        <p
          data-testid="chat-quota-refund-note"
          role="status"
          className="mt-2 text-xs text-[hsl(var(--text-secondary))]"
        >
          {quotaRefundNote}
          {resendAfterStop && (
            <button
              type="button"
              data-testid="chat-resend-after-stop"
              onClick={onResendLastRound}
              className="ml-2 rounded-sm text-[hsl(var(--accent-primary))] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))] focus-visible:ring-offset-1 focus-visible:ring-offset-[hsl(var(--bg-primary))]"
            >
              {t("chat:panel.resend")}
            </button>
          )}
        </p>
      )}

      {unansweredUserMessage && !resendAfterStop && (
        <p
          data-testid="chat-unanswered-note"
          className="mt-2 text-xs text-[hsl(var(--text-secondary))]"
        >
          {t("chat:panel.unanswered", { defaultValue: "这条消息没有收到回复。" })}
          <button
            type="button"
            data-testid="chat-retry-unanswered"
            onClick={() => onRetryUnanswered(unansweredUserMessage)}
            disabled={aiSendBlocked}
            className="ml-2 rounded-sm text-[hsl(var(--accent-primary))] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))] focus-visible:ring-offset-1 focus-visible:ring-offset-[hsl(var(--bg-primary))] disabled:cursor-not-allowed disabled:text-[hsl(var(--text-tertiary))] disabled:no-underline"
          >
            {t("chat:panel.resend")}
          </button>
        </p>
      )}
    </>
  );
};

export const JumpToLatestOverlay: React.FC<{
  isExiting: boolean;
  onJump: () => void;
}> = ({ isExiting, onJump }) => {
  const { t } = useTranslation(["chat"]);
  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-3 z-10 flex justify-center">
      <button
        type="button"
        onClick={onJump}
        aria-hidden={isExiting ? true : undefined}
        inert={isExiting ? true : undefined}
        className={`inline-flex min-h-11 md:min-h-0 items-center gap-1.5 rounded-full bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--border-color))] px-3.5 py-2 md:px-3 md:py-1.5 text-xs text-[hsl(var(--text-primary))] shadow-md hover:bg-[hsl(var(--bg-tertiary))] transition-[transform,background-color,color] duration-[var(--duration-instant)] ease-[var(--ease-standard)] active:scale-[0.97] motion-reduce:active:scale-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-primary))] ${
          isExiting
            ? "motion-popover-exit-up pointer-events-none"
            : "motion-popover-enter-up pointer-events-auto"
        }`}
        aria-label={t("chat:panel.jumpToLatest")}
        title={t("chat:panel.jumpToLatest")}
      >
        <ArrowDown size={14} />
        {t("chat:panel.jumpToLatest")}
      </button>
    </div>
  );
};
