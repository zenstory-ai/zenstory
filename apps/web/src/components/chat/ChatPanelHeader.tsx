/**
 * 48px ChatPanel header bar with QuotaBadge, AI Memory action, and New Session action.
 *
 * @module components/chat/ChatPanelHeader
 */

import React from "react";
import { useTranslation } from "react-i18next";
import { Database, Loader2, Plus } from "lucide-react";
import { QuotaBadge } from "../subscription/QuotaBadge";
import { IconButton } from "../ui/IconButton";
import { formatChatSessionTitle } from "./chatHistoryHelpers";

export interface ChatPanelHeaderProps {
  headerRef: React.RefObject<HTMLDivElement | null>;
  isMobile: boolean;
  isStreaming: boolean;
  isLoadingHistory: boolean;
  isCreatingSession: boolean;
  newChatPending: boolean;
  isAlreadyEmptyNewChat: boolean;
  sessionTitle?: string | null;
  onOpenAIMemory: () => void;
  onRequestNewChat: () => void;
}

export const ChatPanelHeader: React.FC<ChatPanelHeaderProps> = ({
  headerRef,
  isMobile,
  isStreaming,
  isLoadingHistory,
  isCreatingSession,
  newChatPending,
  isAlreadyEmptyNewChat,
  sessionTitle,
  onOpenAIMemory,
  onRequestNewChat,
}) => {
  const { t, i18n } = useTranslation(["chat"]);
  const activeLanguage = i18n?.resolvedLanguage || i18n?.language;
  const displayTitle = sessionTitle
    ? formatChatSessionTitle(sessionTitle, t, activeLanguage)
    : t("chat:panel.title");

  return (
    <div
      ref={headerRef}
      className="shrink-0 flex items-center justify-between border-b border-[hsl(var(--separator-color))] h-12 px-3"
    >
      {/* 右栏可能只有 300px 宽：标题不折行，状态词放不下就省略，额度用紧凑胶囊。 */}
      <div
        className="flex min-w-0 flex-1 items-center gap-1 overflow-hidden"
        data-testid="chat-panel-header-title"
      >
        <span className="min-w-0 truncate whitespace-nowrap text-sm font-medium text-[hsl(var(--text-primary))]">
          {displayTitle}
        </span>
        {isStreaming && (
          <span className="min-w-0 truncate whitespace-nowrap text-xs text-[hsl(var(--success-text))] animate-[breathe_1.5s_ease-in-out_infinite] motion-reduce:animate-none">
            · {t("chat:panel.processing")}
          </span>
        )}
        {isLoadingHistory && (
          <span className="min-w-0 truncate whitespace-nowrap text-xs text-[hsl(var(--text-secondary))]">
            · {t("chat:panel.loadingHistory")}
          </span>
        )}
      </div>
      <div
        className={`flex shrink-0 items-center ${isMobile ? "gap-1" : "gap-2"}`}
        data-testid="chat-panel-header-actions"
      >
        <QuotaBadge compact />
        <IconButton
          onClick={onOpenAIMemory}
          size={isMobile ? "touch" : "md"}
          label={t("chat:panel.aiMemory")}
          icon={<Database size={isMobile ? 18 : 16} />}
        />
        <IconButton
          onClick={onRequestNewChat}
          disabled={isCreatingSession || newChatPending || isAlreadyEmptyNewChat}
          aria-busy={isCreatingSession}
          size={isMobile ? "touch" : "md"}
          label={t("chat:panel.newSession")}
          title={
            isAlreadyEmptyNewChat && !isCreatingSession && !newChatPending
              ? t("chat:panel.alreadyNewSession")
              : t("chat:panel.newSession")
          }
          icon={
            isCreatingSession ? (
              <>
                <Loader2 size={isMobile ? 18 : 16} className="animate-spin" />
                <span className="sr-only" role="status">
                  {t("chat:panel.creatingSession")}
                </span>
              </>
            ) : (
              <Plus size={isMobile ? 18 : 16} />
            )
          }
        />
      </div>
    </div>
  );
};
