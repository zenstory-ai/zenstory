/**
 * @fileoverview MessageList component - Chat message display for AI conversations.
 *
 * This component renders the conversation history between users and the AI assistant,
 * handling:
 * - Message rendering with markdown support
 * - Streaming message display with cursor animation
 * - Tool call and result visualization
 * - AI thinking/reasoning content display
 * - Context items citation display
 * - Multi-agent workflow status indicators
 * - Mobile-responsive layout
 * - Consecutive message grouping for cleaner UI
 *
 * @module components/MessageList
 */
import React, { useMemo, useRef, useState, useEffect, forwardRef, useImperativeHandle, useCallback } from 'react';
import type { TFunction } from 'i18next';
import type { StreamRenderItem } from '../hooks/useChatStreaming';
import { useTranslation } from 'react-i18next';
import { Sparkles, Bot, AlertTriangle, CirclePause, ThumbsUp, ThumbsDown, Copy, Check, ChevronDown, Search, ArrowRight } from 'lucide-react';
import { toast } from '../lib/toast';
import { LazyMarkdown } from './LazyMarkdown';
import { ToolResultCard } from './ToolResultCard';
import { Badge, type BadgeProps } from './ui/Badge';
import { AnimatedCollapse } from './ui/AnimatedCollapse';
import { ThinkingContent } from './ThinkingContent';
import { HistoryCompactionMarker } from './HistoryCompactionMarker';
import type { AgentContextItem, FileEditUndoTarget, IterationExhaustedLayer, ToolCall } from '../types';
import { getLocaleCode } from '../lib/i18n-helpers';
import { stripThinkTags } from '../lib/utils';
import { getAgentDisplayName } from '../lib/agentDisplayName';
import { useMobileLayout } from '../contexts/MobileLayoutContext';
import { RoundEndNote } from './RoundEndNote';
import { exhaustionLeftFilesUnfinished, type RoundStopOutcome } from '../lib/chatRoundEnd';
import { isClarificationStop as isClarificationStopItem } from '../lib/nextStep';
import { isInternalOrderingRefusal } from '../lib/agentRoundProgress';
import {
  CHAT_CARD_PRIMARY_BUTTON,
  CHAT_CARD_PROMPT_SURFACE,
  CHAT_CARD_SECONDARY_BUTTON,
  CHAT_CARD_SURFACE,
  CHAT_CARD_WARNING_SURFACE,
} from './chatCardStyles';

/** The author pressed stop: a quiet note, not a warning (nothing went wrong). */
function UserStoppedNote() {
  const { t } = useTranslation(['chat']);
  return (
    <div className="mb-2 inline-flex items-center gap-1.5 text-xs text-[hsl(var(--text-secondary))]" data-testid="user-stopped-note">
      <CirclePause size={14} className="shrink-0" />
      <span>{t('workflow.userStopped', { ns: 'chat' })}</span>
    </div>
  );
}

/**
 * Represents a single chat message in the conversation history.
 * Messages can be from either the user or the AI assistant.
 */
export type MessageFeedbackVote = 'up' | 'down';

export interface MessageFeedback {
  vote: MessageFeedbackVote;
  preset?: string | null;
  comment?: string | null;
  updated_at?: string | null;
}

export type MessageStatusCard =
  | {
      type: 'workflow_stopped';
      reason?: "clarification_needed" | "error" | "user_cancelled" | "invalid_handoff" | (string & {});
      agentType?: string;
      message?: string;
      /**
       * Clarification payload (when reason === "clarification_needed").
       * Note: backend may also put the same string in `message` for backward compatibility.
       */
      question?: string;
      context?: string;
      details?: string[];
    }
  | {
      type: 'iteration_exhausted';
      layer?: IterationExhaustedLayer;
      iterationsUsed?: number;
      maxIterations?: number;
      reason?: string;
      lastAgent?: string;
      limit?: string;
      unfinishedFiles?: string[];
      rolledBackFiles?: string[];
    };

export interface Message {
  /** Unique identifier for the message */
  id: string;
  /** Optional backend message ID used for feedback submission */
  backendMessageId?: string;
  /** Role of the message sender - 'user' or 'assistant' */
  role: 'user' | 'assistant';
  /** Text content of the message (may contain markdown for assistant messages) */
  content: string;
  /** Timestamp when the message was created */
  timestamp: Date;
  /** Tool calls made by the assistant during message generation */
  toolCalls?: ToolCall[];
  /** Results from executed tool calls */
  toolResults?: ToolCall[];
  /** Ordered live/persisted display sequence, when the original order is known. */
  displayItems?: StreamRenderItem[];
  /** User feedback state for assistant message */
  feedback?: MessageFeedback;
  /**
   * Optional status cards that should be shown as part of this assistant message
   * (e.g. clarification needed / iteration exhausted).
   */
  statusCards?: MessageStatusCard[];
  /** Stopped / interrupted round, as recorded by the server (history only). */
  stopOutcome?: RoundStopOutcome;
  /**
   * No backend message can be matched to this bubble (a stopped round whose id never
   * arrived): hide feedback instead of showing "still saving" forever.
   */
  feedbackUnavailable?: boolean;
}

/**
 * Props for the MessageList component.
 */
interface MessageListProps {
  /** Array of messages to display in the conversation */
  messages: Message[];
  /** Callback invoked when user requests to undo a file edit operation */
  onUndo?: (target: FileEditUndoTarget) => void;
  /** Restore a soft-deleted file from its chat card. */
  onRestoreDeletion?: (
    fileId: string,
    card?: { chatMessageId?: string; toolUseId?: string },
  ) => void | Promise<void>;
  /** Open a successful file result in the current project. */
  onOpenFile?: (fileId: string, fileType: string, title: string) => void;
  /** Open AI Memory at the Writing Conventions field. */
  onOpenAIMemory?: () => void;
  /** Callback invoked when user submits thumbs up/down feedback for assistant message */
  onSubmitFeedback?: (message: Message, vote: MessageFeedbackVote) => void | Promise<void>;
  /** Message ID currently submitting feedback */
  feedbackPendingMessageId?: string | null;
  /** Stop notes may mention today's AI message count (false on plans without a daily limit). */
  showDailyCount?: boolean;
  /**
   * Stream render items for real-time display during streaming.
   * Items are rendered in server-sent order.
   */
  streamRenderItems?: StreamRenderItem[];
  /** Today's AI messages are used up: pause cards keep their facts but offer no 继续完成 / next-step buttons. */
  nextStepsBlocked?: boolean;
  /** Callback invoked when user chooses a guided action after iteration exhaustion */
  onIterationAssistAction?: (
    action: 'continue' | 'split' | 'manual',
    item: {
      layer?: IterationExhaustedLayer;
      maxIterations?: number;
      iterationsUsed?: number;
      reason?: string;
      lastAgent?: string;
    }
  ) => void;
  /**
   * Ref to the scroll container element (from parent).
   * Used by the virtualizer to determine scroll position.
   * If not provided, MessageList will use its own container.
   */
  scrollContainerRef?: React.RefObject<HTMLDivElement | null>;
}

/**
 * Ref handle for MessageList component.
 * Provides methods to programmatically control scroll behavior.
 */
export interface MessageListRef {
  /**
   * Scrolls to the bottom of the message list.
   * Used for auto-scrolling when new messages arrive or during streaming.
   * @param smooth - Whether to use smooth scrolling animation (default: true)
   */
  scrollToBottom: (smooth?: boolean) => void;
}

/**
 * Returns a localized label for a context item priority level.
 *
 * @param p - Priority string ('critical', 'constraint', 'relevant', 'inspiration')
 * @param t - i18next translation function
 * @returns Localized priority label
 */
const priorityLabel = (p: string | undefined, t: TFunction) => {
  switch (p) {
    case "critical":
      return t("context.priority.critical", { ns: "chat" });
    case "constraint":
      return t("context.priority.constraint", { ns: "chat" });
    case "relevant":
      return t("context.priority.relevant", { ns: "chat" });
    case "inspiration":
      return t("context.priority.inspiration", { ns: "chat" });
    default:
      return t("context.priority.default", { ns: "chat" });
  }
};

/**
 * Returns the shared badge variant for a context item's priority.
 *
 * @param p - Priority string ('critical', 'constraint', 'relevant', 'inspiration')
 * @returns Semantic badge variant
 */
const priorityVariant = (p?: string): BadgeProps['variant'] => {
  switch (p) {
    case "critical":
      return 'error';
    case "constraint":
      return 'info';
    case "relevant":
      return 'success';
    case "inspiration":
      return 'purple';
    default:
      return 'neutral';
  }
};

/**
 * Returns a localized label for a context item type.
 *
 * @param type - Context item type (e.g., 'outline', 'draft', 'character', 'lore')
 * @param t - i18next translation function
 * @returns Localized type label
 */
const contextTypeLabel = (item: AgentContextItem, t: TFunction) => {
  // 组装器把正文/剧本/文档都装成 type="outline" 的条目，真实类型在 metadata.file_type。
  const fileType = item.metadata?.file_type;
  const type = typeof fileType === 'string' && fileType ? fileType : item.type;
  return t(`fileType.${type}`, { ns: "chat", defaultValue: t('fileType.default', { ns: "chat" }) });
};

/**
 * 用户引用的条目标题由后端拼成「引用自: 文件名」，只在中文界面成立；
 * 有原文件名时直接显示文件名，类型徽标已经写明「引用」。
 */
const contextItemTitle = (item: AgentContextItem) => {
  const fileTitle = item.metadata?.file_title;
  if (item.type === 'quote' && typeof fileTitle === 'string' && fileTitle.trim()) {
    return fileTitle.trim();
  }
  return item.title || item.id;
};

/**
 * handoff_to_agent / request_clarification 是流程控制工具：交接已经有
 * thinking_status / agent_selected 提示，澄清已经有 workflow_stopped 提问卡片，
 * 再画一张「handoff_to_agent 完成」的工具卡片只会把内部名字露给作者。
 */
/**
 * What an iteration_exhausted card says. A round that stopped with a file that never got its text
 * (empty_file_correction, or a tool-turn stop that names such files) names the files left
 * unfinished and the empty ones removed, instead of the 「任务比较大」 summary.
 */
function iterationExhaustedText(
  t: TFunction,
  card: { layer?: IterationExhaustedLayer; limit?: string; unfinishedFiles?: string[]; rolledBackFiles?: string[] },
  nextStepsBlocked: boolean,
): { title: string; lines: string[] } {
  // A step only written out as text: say it did not run; nothing to resume, so no continue wording.
  if (card.layer === 'unexecuted_tool_text') {
    return {
      title: t('workflow.unexecutedToolText', { ns: 'chat' }),
      lines: [t('workflow.unexecutedToolTextSummary', { ns: 'chat' })],
    };
  }
  // Used-up day: the same facts without 「点继续完成」 (the quota card says when sending comes back).
  const paused = nextStepsBlocked ? 'Paused' : '';
  if (exhaustionLeftFilesUnfinished(card)) {
    const joiner = t('panel.removedFilesJoiner', { ns: 'chat' });
    const lines = [
      card.unfinishedFiles?.length
        ? t(`workflow.emptyFileUnresolved${paused}`, { ns: 'chat', titles: card.unfinishedFiles.join(joiner) })
        : null,
      card.rolledBackFiles?.length
        ? t(`workflow.emptyFileRolledBack${paused}`, { ns: 'chat', titles: card.rolledBackFiles.join(joiner) })
        : null,
    ].filter((line): line is string => Boolean(line));
    return {
      title: t('workflow.emptyFileUnfinished', { ns: 'chat' }),
      lines: lines.length > 0 ? lines : [t(`workflow.emptyFileUnfinishedSummary${paused}`, { ns: 'chat' })],
    };
  }
  const summary = card.limit === 'wall_clock'
    ? t(`workflow.timeLimitSummary${paused}`, { ns: 'chat' })
    : nextStepsBlocked
      ? t('workflow.stepLimitSummaryPaused', { ns: 'chat' })
      : card.layer === 'collaboration'
        ? t('workflow.collaborationExhaustedSummary', { ns: 'chat' })
        : t('workflow.toolCallExhaustedSummary', { ns: 'chat' });
  return { title: t('workflow.iterationExhausted', { ns: 'chat' }), lines: [summary] };
}

type IterationExhaustedCardData = Parameters<NonNullable<MessageListProps['onIterationAssistAction']>>[1]
  & Parameters<typeof iterationExhaustedText>[1];

/** A paused round (not a failure): calm card surface; one primary CTA and uniform neutral secondary options. */
function IterationExhaustedCard({ card, onIterationAssistAction, nextStepsBlocked }: {
  card: IterationExhaustedCardData;
  onIterationAssistAction?: MessageListProps['onIterationAssistAction'];
  nextStepsBlocked: boolean;
}) {
  const { t } = useTranslation(['chat']);
  const { isMobile } = useMobileLayout();
  const exhausted = iterationExhaustedText(t, card, nextStepsBlocked);
  const showNextSteps = !nextStepsBlocked && card.layer !== 'unexecuted_tool_text';
  return (
    <div className={`inline-flex max-w-full items-start gap-2.5 ${CHAT_CARD_SURFACE} ${isMobile ? 'px-3 py-2.5' : 'p-3'}`}>
      <CirclePause size={15} className="text-[hsl(var(--text-secondary))] mt-0.5 shrink-0" />
      <div className="flex flex-col gap-1 min-w-0">
        <span className="text-xs font-semibold leading-5 text-[hsl(var(--text-primary))]">
          {exhausted.title}
        </span>
        {exhausted.lines.map((line) => (
          <span key={line} className="text-xs leading-5 text-[hsl(var(--text-secondary))] break-words">
            {line}
          </span>
        ))}
        {showNextSteps && (
          <div className={`mt-1.5 flex flex-wrap items-center ${isMobile ? 'gap-1.5' : 'gap-2'}`}>
            <button
              type="button"
              className={CHAT_CARD_PRIMARY_BUTTON}
              onClick={() => onIterationAssistAction?.('continue', card)}
            >
              {t('workflow.actionContinue', { ns: 'chat' })}
            </button>
            <button
              type="button"
              className={CHAT_CARD_SECONDARY_BUTTON}
              onClick={() => onIterationAssistAction?.('split', card)}
            >
              {t('workflow.actionSplit', { ns: 'chat' })}
            </button>
            <button
              type="button"
              className={CHAT_CARD_SECONDARY_BUTTON}
              onClick={() => onIterationAssistAction?.('manual', card)}
            >
              {t('workflow.actionManual', { ns: 'chat' })}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

/** Shared card for clarification requests and workflow-stopped states across live and saved timelines. */
function WorkflowStoppedCard({ stop }: {
  stop: {
    type?: string;
    reason?: string;
    message?: string;
    question?: string;
    context?: string;
    details?: string[];
  };
}) {
  const { t } = useTranslation(['chat']);
  const { isMobile } = useMobileLayout();
  const workflowQuestion = stop.question?.trim() || stop.message?.trim() || '';
  const workflowContext = stop.context?.trim() || '';
  const workflowDetails = (stop.details ?? []).map((d) => d.trim()).filter(Boolean);
  const isClarificationStop = isClarificationStopItem({ ...stop, type: stop.type ?? 'workflow_stopped' });
  const paddingClass = isMobile ? 'px-3 py-2.5' : 'p-3';

  if (!isClarificationStop) {
    const isErrorStop = stop.reason === 'error';
    const stopMessage = workflowQuestion || t('workflow.stoppedGeneric', { ns: 'chat' });
    return (
      <div className={`inline-flex max-w-full items-start gap-2.5 ${isErrorStop ? CHAT_CARD_WARNING_SURFACE : CHAT_CARD_SURFACE} ${paddingClass}`}>
        {isErrorStop ? (
          <AlertTriangle size={15} className="text-[hsl(var(--warning-text))] mt-0.5 shrink-0" />
        ) : (
          <CirclePause size={15} className="text-[hsl(var(--text-secondary))] mt-0.5 shrink-0" />
        )}
        <div className="flex flex-col gap-1 min-w-0">
          <span className={`text-xs font-semibold leading-5 ${isErrorStop ? 'text-[hsl(var(--warning-text))]' : 'text-[hsl(var(--text-primary))]'}`}>
            {t('workflow.stoppedTitle', { ns: 'chat' })}
          </span>
          <span className="text-xs leading-5 text-[hsl(var(--text-secondary))] break-words">
            {stopMessage}
          </span>
        </div>
      </div>
    );
  }

  return (
    <div className={`inline-flex max-w-full items-start gap-2.5 ${CHAT_CARD_PROMPT_SURFACE} ${paddingClass}`}>
      <Bot size={15} className="text-[hsl(var(--accent-primary))] mt-0.5 shrink-0" />
      <div className="flex flex-col gap-1 min-w-0">
        <span className="text-xs font-semibold leading-5 text-[hsl(var(--text-primary))]">
          {t('workflow.waitingForReply', { ns: 'chat' })}
        </span>
        {workflowQuestion ? (
          <span className="text-xs leading-5 text-[hsl(var(--text-primary))] break-words">
            {workflowQuestion}
          </span>
        ) : (
          <span className="text-xs leading-5 text-[hsl(var(--text-secondary))] break-words">
            {t('workflow.needsConfirmation', { ns: 'chat' })}
          </span>
        )}
        {workflowContext ? (
          <span className="text-xs leading-5 text-[hsl(var(--text-secondary))] break-words">
            {workflowContext}
          </span>
        ) : null}
        {workflowDetails.length > 0 ? (
          <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs leading-5 text-[hsl(var(--text-primary))]">
            {workflowDetails.map((detail, detailIdx) => (
              <li key={detailIdx} className="break-words">
                {detail}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </div>
  );
}

const CONTROL_FLOW_TOOL_NAMES = new Set(['handoff_to_agent', 'request_clarification']);
const READ_ONLY_TOOL_NAMES = new Set(['query_files', 'hybrid_search', 'load_skill', 'read_skill_resource']);

const isCollapsibleReadOnlyToolCall = (call: ToolCall): boolean =>
  READ_ONLY_TOOL_NAMES.has(call.tool_name) && call.status === 'success' && !call.error;

type ToolCallRun =
  | { kind: 'single'; call: ToolCall; key: string }
  | { kind: 'readonly_group'; calls: ToolCall[]; key: string };

const groupToolCallsForDisplay = (calls: ToolCall[], keyPrefix: string): ToolCallRun[] => {
  const runs: ToolCallRun[] = [];
  let pendingReadOnly: ToolCall[] = [];
  let runStartIdx = 0;

  const flushReadOnly = () => {
    if (pendingReadOnly.length === 0) return;
    if (pendingReadOnly.length >= 2) {
      runs.push({
        kind: 'readonly_group',
        calls: pendingReadOnly,
        key: `${keyPrefix}-ro-${runStartIdx}`,
      });
    } else {
      runs.push({
        kind: 'single',
        call: pendingReadOnly[0],
        key: `${keyPrefix}-${runStartIdx}`,
      });
    }
    pendingReadOnly = [];
  };

  calls.forEach((call, idx) => {
    if (isCollapsibleReadOnlyToolCall(call)) {
      if (pendingReadOnly.length === 0) runStartIdx = idx;
      pendingReadOnly.push(call);
    } else {
      flushReadOnly();
      runs.push({ kind: 'single', call, key: `${keyPrefix}-${idx}` });
    }
  });
  flushReadOnly();
  return runs;
};

interface ReadOnlyToolGroupProps {
  calls: ToolCall[];
  isStreaming: boolean;
  onUndo?: MessageListProps['onUndo'];
  onRestoreDeletion?: MessageListProps['onRestoreDeletion'];
  chatMessageId?: string;
  onOpenFile?: MessageListProps['onOpenFile'];
  onOpenAIMemory?: MessageListProps['onOpenAIMemory'];
}

const ReadOnlyToolGroup: React.FC<ReadOnlyToolGroupProps> = ({
  calls,
  isStreaming,
  onUndo,
  onRestoreDeletion,
  chatMessageId,
  onOpenFile,
  onOpenAIMemory,
}) => {
  const { t } = useTranslation(['chat']);
  const [expanded, setExpanded] = useState(false);

  return (
    <div data-testid="readonly-tool-group" className="max-w-full">
      <button
        type="button"
        onClick={() => setExpanded((prev) => !prev)}
        aria-expanded={expanded}
        className="inline-flex max-w-full min-h-11 md:min-h-7 -ml-1.5 items-center gap-1.5 rounded-lg px-2 py-1 text-left text-xs text-[hsl(var(--text-tertiary))] hover:bg-[hsl(var(--bg-tertiary)/0.75)] hover:text-[hsl(var(--text-primary))] transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[hsl(var(--accent-primary))]"
      >
        <Search size={12} className="shrink-0 opacity-80" aria-hidden="true" />
        <span className="min-w-0 truncate tabular-nums">
          {t('tool.readonlyGroupSummary', { ns: 'chat', count: calls.length })}
        </span>
        <ChevronDown
          size={12}
          aria-hidden="true"
          className={`shrink-0 opacity-75 motion-chevron ${expanded ? 'rotate-180' : ''}`}
        />
      </button>
      <AnimatedCollapse open={expanded} fast>
        <div className="mt-1.5 space-y-1.5 pl-2.5 border-l border-[hsl(var(--separator-color))]">
          {calls.map((toolCall, idx) => (
            <ToolResultCard
              key={toolCall.id || idx}
              type="tool_result"
              toolName={toolCall.tool_name}
              result={toolCardPayload(toolCall)}
              error={toolCall.error}
              isPending={false}
              interrupted={false}
              onUndo={onUndo}
              onRestoreDeletion={isStreaming ? undefined : onRestoreDeletion}
              toolUseId={toolCall.id}
              chatMessageId={chatMessageId}
              onOpenFile={toolCall.status === 'success' && toolCall.result ? onOpenFile : undefined}
              onOpenAIMemory={onOpenAIMemory}
            />
          ))}
        </div>
      </AnimatedCollapse>
    </div>
  );
};

/**
 * The cards the author sees: no control-flow calls, and no write-order refusals (the server sent the
 * model back to write a created file's text first; nothing about the author's work failed).
 */
const visibleToolCalls = (calls: ToolCall[] | undefined): ToolCall[] =>
  (calls ?? []).filter((call) => !CONTROL_FLOW_TOOL_NAMES.has(call.tool_name)
    && !(call.status === 'error' && isInternalOrderingRefusal(call.result as Record<string, unknown> | undefined)));

const toolCardPayload = (tc: { result?: unknown; arguments?: unknown }): Record<string, unknown> =>
  tc.result && typeof tc.result === 'object'
    ? (tc.result as Record<string, unknown>)
    : tc.arguments && typeof tc.arguments === 'object'
      ? (tc.arguments as Record<string, unknown>)
      : {};

const hasRenderableDisplayItem = (item: StreamRenderItem): boolean => {
  if (item.type === 'content') {
    return Boolean(item.content && stripThinkTags(item.content).trim());
  }
  if (item.type === 'tool_calls') {
    return visibleToolCalls(item.toolCalls).length > 0;
  }
  return true;
};

/**
 * Displays a collapsible list of context items (citations) used by the AI.
 * Each item shows its type, title, priority, and content preview.
 */
const ContextItemsView: React.FC<{ items: AgentContextItem[] }> = ({ items }) => {
  const { t } = useTranslation(['chat']);
  const [isOpen, setIsOpen] = React.useState(false);
  if (!items || items.length === 0) return null;

  return (
    <div className="mt-2">
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className="cursor-pointer select-none min-h-11 md:min-h-0 -my-1.5 md:my-0 rounded text-xs text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] transition-colors flex items-center gap-1 w-full text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))]"
      >
        <svg
          className={`w-3 h-3 shrink-0 motion-chevron ${isOpen ? 'rotate-180' : ''}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
        <span>
          {t("context.label", { ns: "chat" })}
          <span className="ml-1 tabular-nums">({items.length})</span>
        </span>
      </button>

      <AnimatedCollapse open={isOpen}>
        <div className="mt-2 space-y-1.5">
          {items.map((item, index) => {
            return (
              <details
                key={item.id || `${item.type}-${index}`}
                className={`group ${CHAT_CARD_SURFACE}`}
              >
                <summary className="cursor-pointer select-none min-h-11 md:min-h-0 px-3 py-2 text-xs flex items-center gap-2 min-w-0 list-none rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))] [&::-webkit-details-marker]:hidden"
                  style={{ listStyle: 'none' }}
                >
                  <svg
                    className="w-3 h-3 text-[hsl(var(--text-secondary))] motion-chevron group-open:rotate-90 shrink-0"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                  </svg>
                  <Badge
                    className="shrink-0 max-w-28 [&>span]:truncate"
                  >
                    {contextTypeLabel(item, t)}
                  </Badge>
                  <span className="min-w-0 flex-1 truncate text-[hsl(var(--text-primary))]">
                    {contextItemTitle(item)}
                  </span>
                  <Badge
                    variant={priorityVariant(item.priority)}
                    className="shrink-0 max-w-28 [&>span]:truncate"
                  >
                    {priorityLabel(item.priority, t)}
                  </Badge>
                </summary>

                <div className="px-3 pb-2.5">
                  <div className="text-xs text-[hsl(var(--text-primary))] whitespace-pre-wrap max-h-40 overflow-auto rounded-lg border border-[hsl(var(--separator-color))] bg-[hsl(var(--bg-primary))] p-2.5">
                    {item.content}
                  </div>
                </div>
              </details>
            );
          })}
        </div>
      </AnimatedCollapse>
    </div>
  );
};

/**
 * Props passed to each message Row component.
 */
function OrderedMessageItems({ items, onUndo, onRestoreDeletion, onOpenFile, onOpenAIMemory, onIterationAssistAction, nextStepsBlocked = false, isStreaming = false, chatMessageId }: {
  items: StreamRenderItem[];
  nextStepsBlocked?: boolean;
  onUndo?: MessageListProps['onUndo'];
  onRestoreDeletion?: MessageListProps['onRestoreDeletion'];
  /** The saved assistant message these items belong to (absent while streaming). */
  chatMessageId?: string;
  onOpenFile?: MessageListProps['onOpenFile'];
  onOpenAIMemory?: MessageListProps['onOpenAIMemory'];
  onIterationAssistAction?: MessageListProps['onIterationAssistAction'];
  isStreaming?: boolean;
}) {
  const { t } = useTranslation(['chat']);
  const { isMobile } = useMobileLayout();
  const translate = t as unknown as (key: string, options?: Record<string, unknown>) => string;
  // Show role names (大纲规划师, 内容创作者...) instead of internal agent ids.
  const agentLabel = (agentType?: string, agentName?: string) =>
    getAgentDisplayName(agentType, translate)
    ?? (agentName && agentName !== agentType ? agentName : null);
  const normalizedItems = useMemo(() => {
    const merged: StreamRenderItem[] = [];
    for (const item of items) {
      if (item.type === 'tool_calls' && item.toolCalls) {
        const visible = visibleToolCalls(item.toolCalls);
        if (visible.length === 0) continue;
        const prev = merged[merged.length - 1];
        if (prev && prev.type === 'tool_calls' && prev.toolCalls) {
          merged[merged.length - 1] = {
            ...prev,
            toolCalls: [...prev.toolCalls, ...visible],
          };
          continue;
        }
        merged.push({ ...item, toolCalls: visible });
      } else {
        merged.push(item);
      }
    }
    return merged;
  }, [items]);

  return <>
  {normalizedItems.map((item) => {
    if (item.type === 'thinking_status' && item.content) {
      // 1. thinking 事件 - 轻量状态小标签（不透明文字 token）
      return (
        <div key={item.id} className="mb-2">
          <div className={`inline-block rounded-lg border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] text-[hsl(var(--text-secondary))] text-xs ${isMobile ? 'px-2.5 py-0.5' : 'px-2.5 py-1'}`}>
            {item.content}
          </div>
        </div>
      );
    } else if (item.type === 'history_compaction' && item.compactionState) {
      // 历史压缩：进行中是带转圈的一步，完成后是会话里可展开摘要的「对话已压缩」分隔标记。
      return (
        <HistoryCompactionMarker
          key={item.id}
          state={item.compactionState}
          summary={item.content}
          startedAt={item.timestamp}
        />
      );
    } else if (item.type === 'thinking_content' && item.content) {
      // 2. thinking_content - 思考过程，可折叠
      return (
        <div key={item.id} className={isMobile ? 'mb-2' : 'mb-2.5'}>
          <ThinkingContent content={item.content} isStreaming={isStreaming} />
        </div>
      );
    } else if (item.type === 'content' && item.content) {
      // 3. content - AI回复内容，无框排版直接落在面板表面
      const cleanContent = stripThinkTags(item.content);
      if (!cleanContent.trim()) return null;
      return (
        <div key={item.id} className={isMobile ? 'mb-2' : 'mb-2.5'}>
          <div className="w-full max-w-full text-sm text-[hsl(var(--text-primary))]">
            <div className="markdown-content">
              <LazyMarkdown>{cleanContent}</LazyMarkdown>
            </div>
          </div>
        </div>
      );
    } else if (item.type === 'tool_calls' && item.toolCalls) {
      // 4. tool_calls - 工具调用卡片（连续 >=2 个成功的纯只读探索工具自动收纳，写卡/进行中/错误绝不折叠）
      const toolCalls = visibleToolCalls(item.toolCalls);
      if (toolCalls.length === 0) return null;
      const runs = groupToolCallsForDisplay(toolCalls, item.id);
      return (
        <div key={item.id} className={`${isMobile ? 'mb-2' : 'mb-2.5'} space-y-2`}>
          {runs.map((run) =>
            run.kind === 'readonly_group' ? (
              <ReadOnlyToolGroup
                key={run.key}
                calls={run.calls}
                isStreaming={isStreaming}
                onUndo={onUndo}
                onRestoreDeletion={onRestoreDeletion}
                chatMessageId={chatMessageId}
                onOpenFile={onOpenFile}
                onOpenAIMemory={onOpenAIMemory}
              />
            ) : (
              <ToolResultCard
                key={run.key}
                type={run.call.status === 'pending' ? 'tool_call' : 'tool_result'}
                toolName={run.call.tool_name}
                result={toolCardPayload(run.call)}
                error={run.call.error}
                // A call still pending in a finished round (stopped / dropped) never got its
                // result: it did not finish, it is not still running.
                isPending={run.call.status === 'pending' && isStreaming}
                interrupted={run.call.status === 'pending' && !isStreaming}
                onUndo={onUndo}
                onRestoreDeletion={isStreaming ? undefined : onRestoreDeletion}
                toolUseId={run.call.id}
                chatMessageId={chatMessageId}
                onOpenFile={run.call.status === 'success' && run.call.result ? onOpenFile : undefined}
                onOpenAIMemory={onOpenAIMemory}
              />
            ),
          )}
        </div>
      );
    } else if (item.type === 'context' && item.items) {
      // 5. context - 引用来源，次要
      return (
        <div key={item.id} className={isMobile ? 'mb-2' : 'mb-2.5'}>
          <ContextItemsView items={item.items} />
        </div>
      );
    } else if (item.type === 'agent_selected' && agentLabel(item.agentType, item.agentName)) {
      // Keep the useful task-role activity label, not internal iteration budgets.
      return (
        <div key={item.id} className="mb-2">
          <div className={`inline-flex items-center rounded-lg ${
            isMobile ? 'gap-1.5 px-2.5 py-0.5' : 'gap-1.5 px-2.5 py-1'
          } bg-[hsl(var(--accent-primary)/0.08)] border border-[hsl(var(--accent-primary)/0.2)]`}>
            <Bot size={13} className="text-[hsl(var(--accent-primary))] shrink-0" />
            <span className={`text-xs font-medium text-[hsl(var(--accent-primary))] ${isMobile ? 'truncate max-w-[140px]' : ''}`}>
              {agentLabel(item.agentType, item.agentName)}
            </span>
          </div>
        </div>
      );
    } else if (item.type === 'iteration_exhausted') {
      // History is saved asynchronously before done. Keep this exact internal
      // card in raw state, but wait for the canonical footer while streaming.
      // If the stream fails, the retained card still offers recovery afterward.
      if (isStreaming && item.layer === 'empty_file_correction') return null;
      // 7. iteration_exhausted - 这一轮的步数/协作轮数用完，或有文件没写成正文：暂停，用中性样式。
      return (
        <div key={item.id} className={isMobile ? 'mb-2' : 'mb-2.5'}>
          <IterationExhaustedCard card={item} onIterationAssistAction={onIterationAssistAction} nextStepsBlocked={nextStepsBlocked} />
        </div>
      );
    } else if (item.type === 'workflow_stopped') {
      const workflowReason = (item as { reason?: string }).reason;
      if (workflowReason === 'user_stopped') {
        return <UserStoppedNote key={item.id} />;
      }
      return (
        <div key={item.id} className={isMobile ? 'mb-2' : 'mb-2.5'}>
          <WorkflowStoppedCard stop={item as { reason?: string; message?: string; question?: string; context?: string; details?: string[] }} />
        </div>
      );
    } else if (item.type === 'workflow_complete') {
      // workflow_complete - 任务完成
      return (
        <div key={item.id} className={isMobile ? 'mb-2' : 'mb-2.5'}>
          <div className={`inline-flex items-center gap-2 ${CHAT_CARD_SURFACE} ${isMobile ? 'px-3 py-2' : 'px-3 py-2'}`}>
            <Sparkles size={14} className="text-[hsl(var(--success-text))] shrink-0" />
            <span className="text-xs font-medium text-[hsl(var(--text-primary))]">
              {t('workflow.taskCompleted', { ns: 'chat' })}
            </span>
          </div>
        </div>
      );
    }
    return null;
  })}
  </>;
}

interface RowDataProps {
  message: Message;
  previousRole?: Message['role'];
  nextRole?: Message['role'];
  /** Function to format timestamps for display */
  formatTime: (date: Date) => string;
  /** Callback to undo a file edit */
  onUndo?: (target: FileEditUndoTarget) => void;
  /** Restore a soft-deleted file from its chat card. */
  onRestoreDeletion?: MessageListProps['onRestoreDeletion'];
  /** Open a successful file result in the current project. */
  onOpenFile?: (fileId: string, fileType: string, title: string) => void;
  /** Open AI Memory at the Writing Conventions field. */
  onOpenAIMemory?: () => void;
  /** Callback to submit message feedback */
  onSubmitFeedback?: (message: Message, vote: MessageFeedbackVote) => void | Promise<void>;
  isFeedbackPending: boolean;
  /** Stop notes may mention today's AI message count */
  showDailyCount?: boolean;
  /** Callback invoked when user chooses a guided action after iteration exhaustion */
  onIterationAssistAction?: MessageListProps["onIterationAssistAction"];
  nextStepsBlocked?: boolean;
}

/**
 * Renders a single message row in the conversation list.
 * Handles user/assistant avatars, message bubbles, thinking content,
 * tool calls/results, conflicts, and timestamps.
 *
 * Consecutive messages from the same role are grouped together with
 * the avatar only shown on the first message.
 *
 * @param props - The message and its row-local display state
 * @returns React element for the message row
 */
const Row = React.memo(function Row({
  message,
  previousRole,
  nextRole,
  formatTime,
  onUndo,
  onRestoreDeletion,
  onOpenFile,
  onOpenAIMemory,
  onSubmitFeedback,
  isFeedbackPending,
  showDailyCount = true,
  onIterationAssistAction,
  nextStepsBlocked = false,
}: RowDataProps): React.ReactElement {
  const { t } = useTranslation(['chat']);
  const { isMobile, switchToEditor } = useMobileLayout();
  // Phones keep the chat in front while the AI writes; the latest round that wrote files offers
  // the manuscript (the editor already follows the AI's writes).
  const showReadWritten = isMobile
    && nextRole === undefined
    && message.role === 'assistant'
    && message.stopOutcome?.reason === 'completed'
    && message.stopOutcome.writtenFiles.length > 0;
  const displayContent =
    message.role === 'assistant' ? stripThinkTags(message.content) : message.content;
  const hasDisplayContent = Boolean(displayContent && displayContent.trim());
  const quietIncompleteCard = message.stopOutcome?.reason === 'incomplete'
    ? message.displayItems?.find((item) => item.type === 'iteration_exhausted' && item.layer === 'empty_file_correction')
      ?? message.statusCards?.find((card) => card.type === 'iteration_exhausted' && card.layer === 'empty_file_correction')
    : undefined;
  // A round that dropped (the author left or lost the connection) after files were saved: the chat may
  // show little or nothing of it, so the receipt offers the saved files directly (at most three).
  const deliveredFiles = message.role === 'assistant'
    && message.stopOutcome?.reason === 'client_disconnected'
    && message.stopOutcome.savedOutput
    && onOpenFile
    ? message.stopOutcome.writtenFiles
      .filter((file): file is typeof file & { id: string } => Boolean(file.id))
      .slice(0, 3)
    : [];
  const orderedDisplayItems = quietIncompleteCard
    ? message.displayItems?.filter((item) => item !== quietIncompleteCard)
    : message.displayItems;
  const hasDisplayItems = message.role === 'assistant' && Boolean(orderedDisplayItems?.length);
  const hasVisibleIterationExhaustedCard = message.role === 'assistant' && (
    hasDisplayItems
      ? Boolean(orderedDisplayItems?.some((item) => item.type === 'iteration_exhausted'))
      : Boolean(message.statusCards?.some(
          (card) => card.type === 'iteration_exhausted'
            && !(message.stopOutcome?.reason === 'incomplete' && card.layer === 'empty_file_correction'),
        ))
  );
  const toolCalls = visibleToolCalls(message.toolCalls);
  const toolResults = visibleToolCalls(message.toolResults);
  const shouldShowFeedbackActions =
    message.role === 'assistant' && Boolean(onSubmitFeedback) && !message.feedbackUnavailable;
  const canSubmitFeedback = shouldShowFeedbackActions && Boolean(message.backendMessageId);
  const selectedFeedbackVote = message.feedback?.vote;
  const isFeedbackDisabled = isFeedbackPending || !canSubmitFeedback;

  const copyableText = useMemo(() => {
    if (message.role !== 'assistant') return '';
    if (hasDisplayItems && orderedDisplayItems) {
      return orderedDisplayItems
        .filter((item): item is StreamRenderItem & { content: string } => item.type === 'content' && Boolean(item.content))
        .map((item) => stripThinkTags(item.content).trim())
        .filter(Boolean)
        .join('\n\n');
    }
    return displayContent ? displayContent.trim() : '';
  }, [message.role, hasDisplayItems, orderedDisplayItems, displayContent]);

  const [copied, setCopied] = useState(false);
  const copyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    return () => {
      if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
    };
  }, []);

  const handleCopyReply = useCallback(async () => {
    if (!copyableText) return;
    try {
      await navigator.clipboard.writeText(copyableText);
      setCopied(true);
      if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
      copyTimerRef.current = setTimeout(() => {
        copyTimerRef.current = null;
        setCopied(false);
      }, 2000);
    } catch {
      toast.error(t('copyFailed', { ns: 'common', defaultValue: '复制失败，请手动复制' }));
    }
  }, [copyableText, t]);

  // Check if this is a consecutive message from the same role
  const isConsecutiveSameRole = previousRole === message.role;
  const isLastInRoleGroup = nextRole !== message.role;

  // Always show timestamp for the last message in a role group, or for standalone messages
  const shouldShowTimestamp = isLastInRoleGroup;
  const isUser = message.role === 'user';
  const hasAssistantFooterActions = !isUser && (shouldShowFeedbackActions || Boolean(copyableText));
  const hasActiveFooterState = copied || Boolean(selectedFeedbackVote) || (shouldShowFeedbackActions && !canSubmitFeedback);

  const rowPaddingClass = isUser
    ? isConsecutiveSameRole
      ? 'pt-1 pb-1.5'
      : previousRole
        ? (isMobile ? 'pt-3.5 pb-1.5' : 'pt-4 pb-1.5')
        : 'pt-1 pb-1.5'
    : isConsecutiveSameRole
      ? 'pt-1 pb-2.5'
      : (isMobile ? 'pt-1.5 pb-2.5' : 'pt-2 pb-3');

  return (
    <div className={`group/msg flex flex-col ${isUser ? 'items-end' : 'items-stretch'} ${rowPaddingClass}`}>
      {/* Content */}
      <div className={isUser ? 'max-w-[88%] min-w-0' : 'w-full min-w-0 overflow-hidden'}>
        {hasDisplayItems ? (
          <OrderedMessageItems items={orderedDisplayItems!} onUndo={onUndo} onRestoreDeletion={onRestoreDeletion} onOpenFile={onOpenFile} onOpenAIMemory={onOpenAIMemory} onIterationAssistAction={onIterationAssistAction} nextStepsBlocked={nextStepsBlocked} isStreaming={false} chatMessageId={message.backendMessageId} />
        ) : (
          hasDisplayContent && (
            <div className={isUser ? 'flex justify-end' : 'w-full'}>
              <div
                className={
                  isUser
                    ? `chat-message-bubble chat-message-bubble-user inline-block max-w-full text-sm ${
                        isMobile ? 'px-3.5 py-2' : 'px-3.5 py-2.5'
                      }`
                    : 'chat-message-bubble-ai w-full max-w-full text-sm'
                }
              >
                {message.role === 'assistant' ? (
                  <div className="markdown-content">
                    <LazyMarkdown>{displayContent}</LazyMarkdown>
                  </div>
                ) : (
                  <div className="whitespace-pre-wrap">
                    {displayContent}
                  </div>
                )}
              </div>
            </div>
          )
        )}

        {/* Tool calls */}
        {message.role === 'assistant' && !hasDisplayItems && toolCalls.length > 0 && (
          <div className={hasDisplayContent ? "mt-2.5 space-y-2" : "space-y-2"}>
            {toolCalls.map((toolCall, idx) => (
              <ToolResultCard
                key={idx}
                type={toolCall.status === 'pending' ? 'tool_call' : 'tool_result'}
                toolName={toolCall.tool_name}
                result={toolCardPayload(toolCall)}
                error={toolCall.status === 'error' ? toolCall.error : undefined}
                isPending={false}
                interrupted={toolCall.status === 'pending'}
                onUndo={onUndo}
                onRestoreDeletion={onRestoreDeletion}
                toolUseId={toolCall.id}
                chatMessageId={message.backendMessageId}
                onOpenFile={toolCall.status === 'success' && toolCall.result ? onOpenFile : undefined}
                onOpenAIMemory={onOpenAIMemory}
              />
            ))}
          </div>
        )}

        {/* Tool results */}
        {message.role === 'assistant' && !hasDisplayItems && toolResults.length > 0 && (
          <div className="mt-2.5 space-y-2">
            {groupToolCallsForDisplay(toolResults, `${message.id}-res`).map((run) =>
              run.kind === 'readonly_group' ? (
                <ReadOnlyToolGroup
                  key={run.key}
                  calls={run.calls}
                  isStreaming={false}
                  onUndo={onUndo}
                  onRestoreDeletion={onRestoreDeletion}
                  chatMessageId={message.backendMessageId}
                  onOpenFile={onOpenFile}
                  onOpenAIMemory={onOpenAIMemory}
                />
              ) : (
                <ToolResultCard
                  key={run.key}
                  type="tool_result"
                  toolName={run.call.tool_name}
                  result={toolCardPayload(run.call)}
                  error={run.call.status === 'error' ? run.call.error : undefined}
                  onUndo={onUndo}
                  onRestoreDeletion={onRestoreDeletion}
                  toolUseId={run.call.id}
                  chatMessageId={message.backendMessageId}
                  onOpenFile={run.call.status === 'success' ? onOpenFile : undefined}
                  onOpenAIMemory={onOpenAIMemory}
                />
              ),
            )}
          </div>
        )}

        {/* Status cards (e.g. clarification needed / iteration exhausted) */}
        {message.role === 'assistant' && !hasDisplayItems && message.statusCards && message.statusCards.length > 0 && (
          <div className="mt-2.5 space-y-2">
            {message.statusCards.map((card, idx) => {
              if (card.type === 'workflow_stopped' && card.reason === 'user_stopped') {
                return <UserStoppedNote key={`status-${idx}`} />;
              }
              if (card.type === 'workflow_stopped') {
                return (
                  <div key={`status-${idx}`} className="max-w-full">
                    <WorkflowStoppedCard stop={card} />
                  </div>
                );
              }

              if (card.type === 'iteration_exhausted') {
                if (message.stopOutcome?.reason === 'incomplete' && card.layer === 'empty_file_correction') return null;
                // 暂停（不是失败）：中性样式。
                return (
                  <div key={`status-${idx}`} className="max-w-full">
                    <IterationExhaustedCard card={card} onIterationAssistAction={onIterationAssistAction} nextStepsBlocked={nextStepsBlocked} />
                  </div>
                );
              }

              return null;
            })}
          </div>
        )}

        {/* Round-end delivery receipt (+ compact mobile jump-to-editor pill), chronologically after tool cards */}
        {message.role === 'assistant' && message.stopOutcome && (
          <>
            <div className="mt-2.5 flex flex-wrap items-center justify-between gap-x-2 gap-y-1.5">
              <RoundEndNote
                outcome={message.stopOutcome}
                showDailyCount={showDailyCount}
                nextStepsBlocked={nextStepsBlocked}
                pauseCardShown={hasVisibleIterationExhaustedCard}
                className="min-w-0"
              />
              {showReadWritten && (
                <button
                  type="button"
                  data-testid="read-written-files"
                  onClick={switchToEditor}
                  className="relative inline-flex shrink-0 items-center gap-1 h-8 rounded-full border border-[hsl(var(--accent-primary)/0.28)] bg-[hsl(var(--accent-primary)/0.12)] px-3 text-xs font-medium text-[hsl(var(--info-text))] hover:bg-[hsl(var(--accent-primary)/0.2)] transition-colors active:scale-[0.97] motion-reduce:active:scale-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))] focus-visible:ring-offset-1 focus-visible:ring-offset-[hsl(var(--bg-rail))] before:content-[''] before:absolute before:-inset-y-1.5 before:inset-x-0"
                >
                  <span>{t('chat:panel.readWrittenFiles')}</span>
                  <ArrowRight size={13} className="shrink-0" aria-hidden="true" />
                </button>
              )}
            </div>
            {quietIncompleteCard && !nextStepsBlocked && (
              <div className="mt-2">
                <button
                  type="button"
                  className={CHAT_CARD_PRIMARY_BUTTON}
                  onClick={() => onIterationAssistAction?.('continue', quietIncompleteCard)}
                >
                  {t('workflow.actionContinue', { ns: 'chat' })}
                </button>
              </div>
            )}
            {deliveredFiles.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-2" data-testid="round-delivery-receipt">
                {deliveredFiles.map((file) => (
                  <button
                    key={file.id}
                    type="button"
                    className={`max-w-full text-left break-words ${CHAT_CARD_SECONDARY_BUTTON}`}
                    onClick={() => onOpenFile?.(file.id, '', file.title)}
                  >
                    {t('panel.openWrittenFile', { ns: 'chat', title: file.title })}
                  </button>
                ))}
              </div>
            )}
          </>
        )}

        {/* Footer: borderless ghost actions + muted timestamp on one compact row */}
        {(hasAssistantFooterActions || shouldShowTimestamp) && (
          <div
            className={`flex items-center gap-2 ${
              isUser
                ? 'justify-end mt-1 px-0.5'
                : hasAssistantFooterActions
                  ? 'justify-between mt-1.5 -ml-1'
                  : 'justify-start mt-1.5'
            }`}
          >
            {hasAssistantFooterActions && (
              <div
                className={`flex items-center gap-0.5 transition-opacity duration-[var(--duration-instant)] ease-[var(--ease-standard)] ${
                  hasActiveFooterState
                    ? 'opacity-100'
                    : 'opacity-100 md:opacity-50 md:group-hover/msg:opacity-100 md:group-focus-within/msg:opacity-100'
                }`}
              >
                {copyableText && (
                  <>
                    <button
                      type="button"
                      data-testid="copy-message-button"
                      onClick={() => { void handleCopyReply(); }}
                      aria-label={copied
                        ? t('message.copiedReply', { ns: 'chat', defaultValue: '已复制' })
                        : t('message.copyReply', { ns: 'chat', defaultValue: '复制回复' })}
                      title={copied
                        ? t('message.copiedReply', { ns: 'chat', defaultValue: '已复制' })
                        : t('message.copyReply', { ns: 'chat', defaultValue: '复制回复' })}
                      className={`inline-flex items-center justify-center rounded-md border transition-[transform,background-color,border-color,color] duration-[var(--duration-instant)] ease-[var(--ease-standard)] active:scale-95 motion-reduce:active:scale-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-1 focus-visible:ring-offset-[hsl(var(--bg-rail))] w-11 h-11 md:w-7 md:h-7 ${
                        copied
                          ? 'border-[hsl(var(--success)/0.35)] bg-[hsl(var(--success)/0.12)] text-[hsl(var(--success-text))]'
                          : 'border-transparent bg-transparent text-[hsl(var(--text-tertiary))] hover:bg-[hsl(var(--bg-tertiary))] hover:text-[hsl(var(--text-primary))]'
                      }`}
                    >
                      {copied ? (
                        <Check key="copied" size={14} className="motion-icon-pop" />
                      ) : (
                        <Copy key="copy" size={14} />
                      )}
                    </button>
                    <span role="status" aria-live="polite" className="sr-only">
                      {copied ? t('message.copiedReply', { ns: 'chat', defaultValue: '已复制' }) : ''}
                    </span>
                  </>
                )}
                {shouldShowFeedbackActions && (
                  <>
                    <button
                      type="button"
                      onClick={() => {
                        if (!canSubmitFeedback) return;
                        void onSubmitFeedback?.(message, 'up');
                      }}
                      disabled={isFeedbackDisabled}
                      aria-pressed={selectedFeedbackVote === 'up'}
                      aria-label={t('feedback.like', { ns: 'chat' })}
                      title={!canSubmitFeedback
                        ? t('feedback.pendingSave', { ns: 'chat' })
                        : t('feedback.like', { ns: 'chat' })}
                      className={`inline-flex items-center justify-center rounded-md border transition-[transform,background-color,border-color,color] duration-[var(--duration-instant)] ease-[var(--ease-standard)] active:scale-95 motion-reduce:active:scale-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-1 focus-visible:ring-offset-[hsl(var(--bg-rail))] w-11 h-11 md:w-7 md:h-7 ${
                        selectedFeedbackVote === 'up'
                          ? 'border-[hsl(var(--success)/0.35)] bg-[hsl(var(--success)/0.12)] text-[hsl(var(--success-text))]'
                          : 'border-transparent bg-transparent text-[hsl(var(--text-tertiary))] hover:bg-[hsl(var(--bg-tertiary))] hover:text-[hsl(var(--text-primary))]'
                      } ${isFeedbackDisabled ? 'cursor-not-allowed opacity-55' : ''}`}
                    >
                      <ThumbsUp key={selectedFeedbackVote === 'up' ? 'up-active' : 'up'} size={14} className={selectedFeedbackVote === 'up' ? 'motion-icon-pop' : undefined} />
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        if (!canSubmitFeedback) return;
                        void onSubmitFeedback?.(message, 'down');
                      }}
                      disabled={isFeedbackDisabled}
                      aria-pressed={selectedFeedbackVote === 'down'}
                      aria-label={t('feedback.dislike', { ns: 'chat' })}
                      title={!canSubmitFeedback
                        ? t('feedback.pendingSave', { ns: 'chat' })
                        : t('feedback.dislike', { ns: 'chat' })}
                      className={`inline-flex items-center justify-center rounded-md border transition-[transform,background-color,border-color,color] duration-[var(--duration-instant)] ease-[var(--ease-standard)] active:scale-95 motion-reduce:active:scale-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-1 focus-visible:ring-offset-[hsl(var(--bg-rail))] w-11 h-11 md:w-7 md:h-7 ${
                        selectedFeedbackVote === 'down'
                          ? 'border-[hsl(var(--warning)/0.4)] bg-[hsl(var(--warning)/0.12)] text-[hsl(var(--warning-text))]'
                          : 'border-transparent bg-transparent text-[hsl(var(--text-tertiary))] hover:bg-[hsl(var(--bg-tertiary))] hover:text-[hsl(var(--text-primary))]'
                      } ${isFeedbackDisabled ? 'cursor-not-allowed opacity-55' : ''}`}
                    >
                      <ThumbsDown key={selectedFeedbackVote === 'down' ? 'down-active' : 'down'} size={14} className={selectedFeedbackVote === 'down' ? 'motion-icon-pop' : undefined} />
                    </button>
                    {!canSubmitFeedback && (
                      <span className="ml-1 text-xs text-[hsl(var(--text-secondary))]">
                        {t('feedback.pendingSave', { ns: 'chat' })}
                      </span>
                    )}
                  </>
                )}
              </div>
            )}
            {shouldShowTimestamp && (
              <div className="chat-timestamp shrink-0">
                {formatTime(message.timestamp)}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
});

/**
 * Main chat message list component that displays conversation history.
 *
 * Features:
 * - Renders user and assistant messages with appropriate styling
 * - Supports markdown rendering for assistant messages
 * - Shows streaming content with animated cursor
 * - Displays AI thinking/reasoning content in collapsible sections
 * - Shows tool call results and conflicts
 * - Handles multi-agent workflow status indicators
 * - Groups consecutive messages from the same role
 * - Mobile-responsive layout
 *
 * Uses React.memo with custom comparison for optimized re-rendering
 * during streaming updates.
 *
 * @param props - Component props
 * @param props.messages - Array of messages to display
 * @param props.streamingMessageId - ID of message currently streaming
 * @param props.onUndo - Callback to undo file edits
 * @param props.streamingThinkingContent - Real-time thinking content
 * @param props.isThinking - Whether agent is actively thinking
 * @param props.streamRenderItems - Items for real-time stream display
 * @returns Message list element or null if no content to display
 *
 * @example
 * ```tsx
 * <MessageList
 *   messages={conversationMessages}
 *   streamingMessageId="msg-123"
 *   onUndo={(target) => handleUndoEdit(target)}
 *   isThinking={isAgentThinking}
 *   streamingThinkingContent={thinkingContent}
 * />
 * ```
 */
export const MessageList = React.memo(
  forwardRef<MessageListRef, MessageListProps>(({
  messages,
  onUndo,
  onRestoreDeletion,
  onOpenFile,
  onOpenAIMemory,
  onSubmitFeedback,
  feedbackPendingMessageId,
  showDailyCount,
  streamRenderItems,
  onIterationAssistAction,
  nextStepsBlocked,
  scrollContainerRef: externalScrollContainerRef,
}, ref) => {
  const { isMobile } = useMobileLayout();

  // Internal scroll container ref (used if external ref not provided)
  const internalScrollContainerRef = useRef<HTMLDivElement>(null);

  // Use external ref if provided, otherwise use internal ref
  const scrollContainerRef = externalScrollContainerRef || internalScrollContainerRef;

  // Filter out completely invisible messages (e.g. empty assistant content segments)
  // so we don't render ugly empty rows/bubbles.
  const visibleMessages = useMemo(() => messages.filter((m) => {
    const visibleContent =
      m.role === 'assistant' ? stripThinkTags(m.content) : m.content;

    const hasVisibleContent = Boolean(visibleContent && visibleContent.trim());
    const hasToolCalls = m.role === 'assistant' && visibleToolCalls(m.toolCalls).length > 0;
    const hasToolResults = m.role === 'assistant' && visibleToolCalls(m.toolResults).length > 0;
    const hasStatusCards = m.role === 'assistant' && Boolean(m.statusCards?.length);

    // A stopped / interrupted round with nothing else to show still shows how it ended.
    const hasStopOutcome = m.role === 'assistant' && Boolean(m.stopOutcome);

    return (
      Boolean(m.displayItems?.some(hasRenderableDisplayItem)) ||
      hasStopOutcome ||
      hasVisibleContent ||
      hasToolCalls ||
      hasToolResults ||
      hasStatusCards
    );
  }), [messages]);

  /**
   * Scrolls to the bottom of the message list.
   * Uses direct container scrolling to avoid virtualization measurement jitter.
   *
   * @param smooth - Whether to use smooth scrolling (default: true)
   */
  const scrollToBottom = useCallback((smooth: boolean = true) => {
    const scrollElement = scrollContainerRef.current;
    if (!scrollElement) return;

    requestAnimationFrame(() => {
      if (scrollElement) {
        scrollElement.scrollTo({
          top: scrollElement.scrollHeight,
          behavior: smooth ? 'smooth' : 'instant',
        });
      }
    });
  }, [scrollContainerRef]);

  // Expose scrollToBottom method via ref
  useImperativeHandle(ref, () => ({
    scrollToBottom,
  }), [scrollToBottom]);

  // 检查是否应该显示 streamRenderItems
  // 因为 onComplete 中立即清空了 streamRenderItems，所以只需要简单检查是否有内容即可
  const safeStreamRenderItems = useMemo(
    () => streamRenderItems ?? [],
    [streamRenderItems]
  );
  const shouldShowStreamItems = useMemo(() => {
    return safeStreamRenderItems.some(hasRenderableDisplayItem);
  }, [safeStreamRenderItems]);

  const formatTime = useMemo(() => {
    const localeCode = getLocaleCode();
    return (date: Date) => {
      return new Intl.DateTimeFormat(localeCode, {
        hour: '2-digit',
        minute: '2-digit',
      }).format(date);
    };
  }, []); // Empty deps since getLocaleCode reads from localStorage which is stable

  // Don't render anything if there are no visible messages and no stream items
  if (visibleMessages.length === 0 && !shouldShowStreamItems) {
    return null;
  }

  return (
    // ph-no-capture: prompts and replies must never reach analytics capture.
    <div className="ph-no-capture" aria-live="polite" aria-atomic="true">
      {visibleMessages.map((message, index) => (
        <Row
          key={message.id}
          message={message}
          previousRole={visibleMessages[index - 1]?.role}
          nextRole={visibleMessages[index + 1]?.role}
          formatTime={formatTime}
          onUndo={onUndo}
          onRestoreDeletion={onRestoreDeletion}
          onOpenFile={onOpenFile}
          onOpenAIMemory={onOpenAIMemory}
          onSubmitFeedback={onSubmitFeedback}
          isFeedbackPending={feedbackPendingMessageId === message.id}
          showDailyCount={showDailyCount}
          onIterationAssistAction={onIterationAssistAction}
          nextStepsBlocked={nextStepsBlocked}
        />
      ))}

      {/* 然后渲染流式内容 - 按服务端顺序直接追加在最后 */}
      {shouldShowStreamItems && (
        <div key="stream-items" className={`flex flex-col ${isMobile ? 'pt-1.5 pb-2.5' : 'pt-2 pb-3'}`}>
          {/* Stream items */}
          <div className="w-full min-w-0 overflow-hidden">
            <OrderedMessageItems items={safeStreamRenderItems} onUndo={onUndo} onRestoreDeletion={onRestoreDeletion} onOpenFile={onOpenFile} onOpenAIMemory={onOpenAIMemory} onIterationAssistAction={onIterationAssistAction} nextStepsBlocked={nextStepsBlocked} isStreaming={true} />

            {/* Timestamp */}
            <div className="chat-timestamp mt-1.5">
              {formatTime(safeStreamRenderItems[safeStreamRenderItems.length - 1].timestamp)}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}),
(prevProps, nextProps) => {
  // Custom comparison function for React.memo.
  // Prefer cheap reference checks to avoid O(n) message comparisons.
  if (prevProps.onUndo !== nextProps.onUndo) return false;
  if (prevProps.onRestoreDeletion !== nextProps.onRestoreDeletion) return false;
  if (prevProps.onOpenFile !== nextProps.onOpenFile) return false;
  if (prevProps.onOpenAIMemory !== nextProps.onOpenAIMemory) return false;
  if (prevProps.onSubmitFeedback !== nextProps.onSubmitFeedback) return false;
  // The iteration-exhausted action buttons call this; omitting it here let the
  // buttons keep invoking a stale closure after the parent re-created it.
  if (prevProps.onIterationAssistAction !== nextProps.onIterationAssistAction) return false;
  if (prevProps.feedbackPendingMessageId !== nextProps.feedbackPendingMessageId) return false;
  if (prevProps.showDailyCount !== nextProps.showDailyCount) return false;
  if (prevProps.nextStepsBlocked !== nextProps.nextStepsBlocked) return false;
  if (prevProps.messages !== nextProps.messages) return false;
  if (prevProps.streamRenderItems !== nextProps.streamRenderItems) return false;

  return true;
});
