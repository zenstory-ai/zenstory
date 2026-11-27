/**
 * Quota tracking, Beijing-midnight limit/network refusal resets, refund/stop notes,
 * and auto-upgrade prompt claiming for ChatPanel.
 *
 * @module components/chat/useChatQuotaAndStops
 */

import { useCallback, useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";
import { useQuery, type QueryClient } from "@tanstack/react-query";
import type { TFunction } from "i18next";
import { subscriptionApi, subscriptionQueryKeys } from "../../lib/subscriptionApi";
import {
  aiQuotaPeriodKey,
  isAiMessageQuotaExhausted,
  nextAiQuotaResetMs,
} from "../../hooks/useAiMessageQuota";
import { getUpgradePromptDefinition } from "../../config/upgradeExperience";
import { claimAutoUpgradePrompt } from "../../lib/upgradeTriggerStrategy";
import { reportActivationEvent } from "../../lib/activationEvents";
import { formatRoundDelivery, type RoundWrittenFile } from "../../lib/chatRoundEnd";
import type { QuotaRefundKind, RemovedPlaceholderFile } from "../../lib/agentApi";
import type { QuotaResponse } from "../../types/subscription";
import type { Message } from "../MessageList";
import { isNetworkQuotaRefusal } from "./chatHistoryHelpers";

export interface QuotaRefundState {
  projectId: string;
  kind: QuotaRefundKind;
  removedFiles: RemovedPlaceholderFile[];
}

export interface UserStopState {
  projectId: string;
  wroteFiles: boolean;
  counted: boolean;
  /** The files the server says this round wrote (done.written_files); empty from older servers. */
  writtenFiles: RoundWrittenFile[];
  unwrittenFiles?: RoundWrittenFile[];
}

export interface UseChatQuotaStateOptions {
  currentProjectId: string | null;
  userId: string | null;
  isMobile: boolean;
  queryClient: QueryClient;
}

export function useChatQuotaState({
  currentProjectId,
  userId,
  isMobile,
  queryClient,
}: UseChatQuotaStateOptions) {
  const { data: quota } = useQuery({
    queryKey: subscriptionQueryKeys.quota(),
    queryFn: () => subscriptionApi.getQuota(),
  });
  const aiMessageQuota = quota?.ai_conversations;
  // Same rule as the home page (a plan without a positive daily limit is never "used up").
  const quotaExhausted = isAiMessageQuotaExhausted(aiMessageQuota);
  // Pro (no daily limit) never hears about today's count, and neither does anyone while the
  // quota is still loading (it may turn out to be Pro).
  const showDailyCount = Boolean(aiMessageQuota) && aiMessageQuota?.limit !== -1;

  const invalidateQuota = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: subscriptionQueryKeys.quota() });
  }, [queryClient]);

  /** Today's used count in the cache when this round was sent (before the server reserved one). */
  const roundQuotaBaselineRef = useRef<number | null>(null);

  /**
   * The server confirmed this round's AI message was refunded: put the cached count back to
   * where it was before the round right away (the refetch can take seconds, and the refund
   * note already says 「不计入」). min() makes a repeated frame a no-op.
   */
  const applyConfirmedRefund = useCallback(() => {
    const baseline = roundQuotaBaselineRef.current;
    if (baseline == null) return;
    const restore = (old: QuotaResponse | undefined) =>
      old && old.ai_conversations.limit !== -1
        ? {
            ...old,
            ai_conversations: {
              ...old.ai_conversations,
              used: Math.min(old.ai_conversations.used, baseline),
            },
          }
        : old;
    queryClient.setQueryData<QuotaResponse>(subscriptionQueryKeys.quota(), restore);
  }, [queryClient]);

  const reportQuotaBlockedSend = useCallback(
    (serverRefused: boolean) => {
      void reportActivationEvent(userId, {
        event_name: "ai_send_blocked_quota",
        surface: "chat",
        project_id: currentProjectId,
        server_refused: serverRefused,
        is_mobile: isMobile,
      });
    },
    [userId, currentProjectId, isMobile],
  );

  const [showQuotaUpgradeModal, setShowQuotaUpgradeModal] = useState(false);
  const [quotaRefund, setQuotaRefund] = useState<QuotaRefundState | null>(null);
  const [userStop, setUserStop] = useState<UserStopState | null>(null);

  return {
    aiMessageQuota,
    quotaExhausted,
    showDailyCount,
    invalidateQuota,
    roundQuotaBaselineRef,
    applyConfirmedRefund,
    reportQuotaBlockedSend,
    showQuotaUpgradeModal,
    setShowQuotaUpgradeModal,
    quotaRefund,
    setQuotaRefund,
    userStop,
    setUserStop,
  };
}

export interface UseChatQuotaEffectsAndNotesOptions {
  currentProjectId: string | null;
  aiMessageQuota: QuotaResponse["ai_conversations"] | undefined;
  quotaExhausted: boolean;
  showDailyCount: boolean;
  quotaRefund: QuotaRefundState | null;
  userStop: UserStopState | null;
  setShowQuotaUpgradeModal: Dispatch<SetStateAction<boolean>>;
  isStreaming: boolean;
  isThinking: boolean;
  isLoadingHistory: boolean;
  error: string | null;
  errorCode: string | null;
  clearError: () => void;
  messages: Message[];
  hasLastRetryForProject: boolean;
  t: TFunction;
  language: string | undefined;
}

export function useChatQuotaEffectsAndNotes({
  currentProjectId,
  aiMessageQuota,
  quotaExhausted,
  showDailyCount,
  quotaRefund,
  userStop,
  setShowQuotaUpgradeModal,
  isStreaming,
  isThinking,
  isLoadingHistory,
  error,
  errorCode,
  clearError,
  messages,
  hasLastRetryForProject,
  t,
  language,
}: UseChatQuotaEffectsAndNotesOptions) {
  const chatQuotaUpgradePrompt = getUpgradePromptDefinition("chat_quota_blocked");

  // 成本兜底与每日条数共用同一套标题和说明：不向作者透露第二个额度。
  const isAiQuotaLimit =
    errorCode === "ERR_QUOTA_AI_CONVERSATIONS_EXCEEDED" ||
    errorCode === "ERR_QUOTA_AI_DAILY_COST_EXCEEDED";

  const [aiLimitHitUntil, setAiLimitHitUntil] = useState<number | null>(null);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- lock blocked state until next Beijing midnight when server refuses on daily limit.
    if (isAiQuotaLimit) setAiLimitHitUntil(nextAiQuotaResetMs(Date.now()));
  }, [isAiQuotaLimit]);

  const isNetworkQuotaLimit = isNetworkQuotaRefusal(errorCode);
  useEffect(() => {
    if (!isNetworkQuotaLimit) return;
    const timer = setTimeout(clearError, Math.max(0, nextAiQuotaResetMs(Date.now()) - Date.now()));
    return () => clearTimeout(timer);
  }, [isNetworkQuotaLimit, clearError]);

  // Upgraded elsewhere: paid plans are exempt, and the next send goes through.
  useEffect(() => {
    if (isNetworkQuotaLimit && aiMessageQuota?.limit === -1) clearError();
  }, [isNetworkQuotaLimit, aiMessageQuota?.limit, clearError]);

  const isAiQuotaLimitRef = useRef(isAiQuotaLimit);
  useEffect(() => {
    isAiQuotaLimitRef.current = isAiQuotaLimit;
  }, [isAiQuotaLimit]);

  useEffect(() => {
    if (aiLimitHitUntil === null) return;
    const timer = setTimeout(() => {
      setAiLimitHitUntil(null);
      if (isAiQuotaLimitRef.current) clearError();
    }, Math.max(0, aiLimitHitUntil - Date.now()));
    return () => clearTimeout(timer);
  }, [aiLimitHitUntil, clearError]);

  const aiLimitHit = aiLimitHitUntil !== null && aiMessageQuota?.limit !== -1;
  /** Today's AI messages are used up, by the cached count or by the server's refusal. */
  const aiSendBlocked = quotaExhausted || aiLimitHit;

  // Never pair a refund note with the used-up card: that would hint at a second, cost-based limit.
  const quotaRefundNote =
    quotaRefund && quotaRefund.projectId === currentProjectId && !isStreaming && !isAiQuotaLimit
      ? [
          showDailyCount
            ? t(
                quotaRefund.kind === "no_progress" || quotaRefund.kind === "no_change"
                  ? "chat:panel.notCharged"
                  : quotaRefund.kind === "stopped"
                    ? "chat:panel.notChargedStopped"
                    : "chat:panel.notChargedError",
              )
            : quotaRefund.kind === "stopped"
              ? t("chat:panel.stoppedNothingWritten", {
                  defaultValue: "已停止，这一轮还没有开始生成。",
                })
              : null,
        ]
          .filter(Boolean)
          .join("")
          .trim() || null
      : null;

  const resendAfterStop = Boolean(
    quotaRefundNote &&
      quotaRefund?.kind === "stopped" &&
      hasLastRetryForProject,
  );

  const stopRefunded =
    quotaRefund?.projectId === currentProjectId && quotaRefund?.kind === "stopped";
  const userStopNote =
    userStop && userStop.projectId === currentProjectId && !isStreaming && !stopRefunded
      ? [
          t("chat:panel.userStopped", { defaultValue: "已停止" }),
          userStop.writtenFiles.length > 0 || (userStop.unwrittenFiles?.length ?? 0) > 0
            ? formatRoundDelivery(
                t,
                userStop.writtenFiles,
                userStop.unwrittenFiles ?? [],
                language,
              )
            : userStop.wroteFiles
              ? t("chat:panel.userStoppedSaved", { defaultValue: "已写入的内容已保存" })
              : null,
          userStop.counted && showDailyCount
            ? t("chat:panel.userStoppedCounted", { defaultValue: "本条计入今日 AI 消息" })
            : null,
        ]
          .filter(Boolean)
          .join(" · ")
      : null;

  const lastMessage = messages[messages.length - 1];
  const unansweredUserMessage =
    lastMessage?.role === "user" &&
    !isStreaming &&
    !isThinking &&
    !isLoadingHistory &&
    !error
      ? lastMessage
      : null;

  const quotaAccount = subscriptionQueryKeys.status()[1];
  const quotaPeriod = aiQuotaPeriodKey(aiMessageQuota?.reset_at);
  useEffect(() => {
    if (
      isAiQuotaLimit &&
      chatQuotaUpgradePrompt.surface === "modal" &&
      claimAutoUpgradePrompt(quotaAccount, chatQuotaUpgradePrompt.source, quotaPeriod)
    ) {
      setShowQuotaUpgradeModal(true);
    }
  }, [
    chatQuotaUpgradePrompt.surface,
    chatQuotaUpgradePrompt.source,
    isAiQuotaLimit,
    quotaAccount,
    quotaPeriod,
    setShowQuotaUpgradeModal,
  ]);

  return {
    isAiQuotaLimit,
    isNetworkQuotaLimit,
    aiSendBlocked,
    quotaRefundNote,
    resendAfterStop,
    userStopNote,
    unansweredUserMessage,
  };
}
