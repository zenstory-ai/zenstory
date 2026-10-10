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
import React, { useMemo, useRef, forwardRef, useImperativeHandle, useCallback } from 'react';
import type { TFunction } from 'i18next';
import type { StreamRenderItem } from '../hooks/useChatStreaming';
import { useTranslation } from 'react-i18next';
import { User, Sparkles, Bot, AlertTriangle, CirclePause, ThumbsUp, ThumbsDown } from 'lucide-react';
import { LazyMarkdown } from './LazyMarkdown';
import { ToolResultCard } from './ToolResultCard';
import { ThinkingContent } from './ThinkingContent';
import type { AgentContextItem, FileEditUndoTarget, ToolCall } from '../types';
import { getLocaleCode } from '../lib/i18n-helpers';
import { stripThinkTags } from '../lib/utils';
import { getAgentDisplayName } from '../lib/agentDisplayName';
import { useAuth } from '../contexts/AuthContext';
import { useMobileLayout } from '../contexts/MobileLayoutContext';
import { RoundEndNote } from './RoundEndNote';
import type { RoundStopOutcome } from '../lib/chatRoundEnd';
import { isClarificationStop as isClarificationStopItem } from '../lib/nextStep';

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
      confidence?: number;
      evaluation?: {
        complete_score: number;
        clarification_score: number;
        consistency_score: number;
        decision_reason: string;
      };
    }
  | {
      type: 'iteration_exhausted';
      layer?: "collaboration" | "tool_call";
      iterationsUsed?: number;
      maxIterations?: number;
      reason?: string;
      lastAgent?: string;
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
  /** @deprecated Legacy field for backward compatibility - use reasoningSegments instead */
  reasoningContent?: string;
  /** Array of reasoning/thinking content segments with timestamps */
  reasoningSegments?: Array<{
    /** Content of the reasoning segment */
    content: string;
    /** When this reasoning segment was generated */
    timestamp: Date;
  }>;
  /** Tool calls made by the assistant during message generation */
  toolCalls?: ToolCall[];
  /** Results from executed tool calls */
  toolResults?: ToolCall[];
  /** Ordered live/persisted display sequence, when the original order is known. */
  displayItems?: StreamRenderItem[];
  /** Consistency conflicts detected during content generation */
  conflicts?: Array<{
    /** Type of conflict detected */
    type: string;
    /** Severity level: low, medium, or high */
    severity: 'low' | 'medium' | 'high';
    /** Human-readable conflict title */
    title: string;
    /** Detailed description of the conflict */
    description: string;
    /** Suggested resolutions for the conflict */
    suggestions: string[];
    /** References to related content causing the conflict */
    references: unknown[];
  }>;
  /** Context items (citations) used to generate this message */
  contextItems?: AgentContextItem[];
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
  /** ID of the message currently being streamed (enables cursor animation) */
  streamingMessageId?: string;
  /** Callback invoked when user requests to undo a file edit operation */
  onUndo?: (target: FileEditUndoTarget) => void;
  /** Callback invoked when user submits thumbs up/down feedback for assistant message */
  onSubmitFeedback?: (message: Message, vote: MessageFeedbackVote) => void | Promise<void>;
  /** Message ID currently submitting feedback */
  feedbackPendingMessageId?: string | null;
  /** Stop notes may mention today's AI message count (false on plans without a daily limit). */
  showDailyCount?: boolean;
  /** Real-time thinking content from the streaming agent response */
  streamingThinkingContent?: string;
  /** Whether the agent is actively thinking/processing */
  isThinking?: boolean;
  /**
   * Stream render items for real-time display during streaming.
   * Items are rendered in server-sent order.
   */
  streamRenderItems?: StreamRenderItem[];
  /** Callback invoked when user chooses a guided action after iteration exhaustion */
  onIterationAssistAction?: (
    action: 'continue' | 'split' | 'manual',
    item: {
      layer?: "collaboration" | "tool_call";
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
 * Returns the CSS class names for a context item based on its priority.
 *
 * @param p - Priority string ('critical', 'constraint', 'relevant', 'inspiration')
 * @returns Tailwind CSS class string for the priority badge
 */
const priorityClassName = (p?: string) => {
  switch (p) {
    case "critical":
      return "bg-[hsl(var(--error))] text-white border border-[hsl(var(--error)/0.5)]";
    case "constraint":
      return "bg-[hsl(var(--accent-primary))] text-white border border-[hsl(var(--accent-primary)/0.5)]";
    case "relevant":
      return "bg-[hsl(var(--success))] text-white border border-[hsl(var(--success)/0.5)]";
    case "inspiration":
      return "bg-[hsl(var(--accent-secondary)/0.15)] text-[hsl(var(--accent-secondary))] border border-[hsl(var(--accent-secondary)/0.3)]";
    default:
      return "bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-secondary))] border border-[hsl(var(--separator-color))]";
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
const CONTROL_FLOW_TOOL_NAMES = new Set(['handoff_to_agent', 'request_clarification']);

const visibleToolCalls = (calls: ToolCall[] | undefined): ToolCall[] =>
  (calls ?? []).filter((call) => !CONTROL_FLOW_TOOL_NAMES.has(call.tool_name));

/**
 * Props for the ContextItemsView component.
 */
interface ContextItemsViewProps {
  /** Array of context items to display */
  items: AgentContextItem[];
  /** Optional token count to display */
  tokenCount?: number | null;
  /** Custom label for the context section (defaults to localized "来源") */
  label?: string;
}

/**
 * Displays a collapsible list of context items (citations) used by the AI.
 * Each item shows its type, title, priority, relevance score, and content preview.
 *
 * @param props - Component props
 * @param props.items - Array of context items to display
 * @param props.tokenCount - Optional token count to display
 * @param props.label - Custom label for the context section
 * @returns Collapsible details element with context items, or null if empty
 */
export const ContextItemsView: React.FC<ContextItemsViewProps> = ({ items, tokenCount, label }) => {
  const { t } = useTranslation(['chat']);
  const [isOpen, setIsOpen] = React.useState(false);
  if (!items || items.length === 0) return null;

  return (
    <div className="mt-2">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="cursor-pointer select-none text-xs text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] transition-colors flex items-center gap-1 w-full text-left"
      >
        <svg
          className={`w-3 h-3 transition-transform duration-200 ${isOpen ? 'rotate-180' : ''}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
        <span>
          {label || t("context.label", { ns: "chat" })}
          <span className="ml-1">({items.length})</span>
        </span>
        {typeof tokenCount === "number" && (
          <span className="ml-2">{t("context.tokenEstimate", { ns: "chat", value: tokenCount })}</span>
        )}
      </button>

      {isOpen && (
        <div className="mt-2 space-y-1">
          {items.map((item, index) => {
            return (
              <details
                key={item.id || `${item.type}-${index}`}
                className="rounded-lg border border-[hsl(var(--separator-color))] bg-[hsl(var(--bg-tertiary))]"
              >
                <summary className="cursor-pointer select-none px-2 py-1.5 text-xs flex items-center gap-2 min-w-0 list-none"
                  style={{ listStyle: 'none' }}
                >
                  <svg
                    className="w-3 h-3 text-[hsl(var(--text-secondary))] transition-transform duration-200 shrink-0"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                  </svg>
                  <span
                    className="inline-block px-1.5 py-0.5 rounded bg-[hsl(var(--bg-primary))] text-[hsl(var(--accent-primary))] shrink-0 max-w-28 truncate"
                  >
                    {contextTypeLabel(item, t)}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-[hsl(var(--text-primary))]">
                    {contextItemTitle(item)}
                  </span>
                  <span
                    className={`inline-block px-1.5 py-0.5 rounded shrink-0 max-w-28 truncate ${priorityClassName(item.priority)}`}
                  >
                    {priorityLabel(item.priority, t)}
                  </span>
                </summary>

                <div className="px-2 pb-2">
                  <div className="text-xs text-[hsl(var(--text-primary))] whitespace-pre-wrap max-h-40 overflow-auto rounded bg-[hsl(var(--bg-primary))] p-2">
                    {item.content}
                  </div>
                </div>
              </details>
            );
          })}
        </div>
      )}
    </div>
  );
};

/**
 * Props passed to each message Row component.
 */
function OrderedMessageItems({ items, onUndo, onIterationAssistAction, isStreaming = false }: {
  items: StreamRenderItem[];
  onUndo?: MessageListProps['onUndo'];
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
  return <>
  {items.map((item) => {
    if (item.type === 'thinking_status' && item.content) {
      // 1. thinking 事件 - 状态小气泡，最小、最淡
      return (
        <div key={item.id} className="mb-2">
          <div className={`inline-block rounded-lg bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-tertiary))] text-xs opacity-70 ${isMobile ? 'px-2 py-0.5' : 'px-2.5 py-1'}`}>
            {item.content}
          </div>
        </div>
      );
    } else if (item.type === 'thinking_content' && item.content) {
      // 2. thinking_content - 思考过程，可折叠，半透明
      return (
        <div key={item.id} className={isMobile ? 'mb-2' : 'mb-3'}>
          <ThinkingContent content={item.content} isStreaming={isStreaming} />
        </div>
      );
    } else if (item.type === 'content' && item.content) {
      // 3. content - AI回复内容，主要
      const cleanContent = stripThinkTags(item.content);
      if (!cleanContent.trim()) return null;
      return (
        <div key={item.id} className={isMobile ? 'mb-2' : 'mb-3'}>
          <div className={`inline-block max-w-full rounded-xl text-sm bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-primary))] shadow-sm ${isMobile ? 'px-3 py-2' : 'px-4 py-3'}`}>
            <div className="markdown-content">
              <LazyMarkdown>{cleanContent}</LazyMarkdown>
            </div>
          </div>
        </div>
      );
    } else if (item.type === 'tool_calls' && item.toolCalls) {
      // 4. tool_calls - 工具调用卡片
      const toolCalls = visibleToolCalls(item.toolCalls);
      if (toolCalls.length === 0) return null;
      return (
        <div key={item.id} className={`${isMobile ? 'mb-2' : 'mb-3'} space-y-2`}>
          {toolCalls.map((toolCall, idx) => (
            <ToolResultCard
              key={`${item.id}-${idx}`}
              type={toolCall.status === 'pending' ? 'tool_call' : 'tool_result'}
              toolName={toolCall.tool_name}
              result={(toolCall.result || toolCall.arguments) as Record<string, unknown>}
              error={toolCall.error}
              // A call still pending in a finished round (stopped / dropped) never got its
              // result: it did not finish, it is not still running.
              isPending={toolCall.status === 'pending' && isStreaming}
              interrupted={toolCall.status === 'pending' && !isStreaming}
              onUndo={onUndo}
            />
          ))}
        </div>
      );
    } else if (item.type === 'context' && item.items) {
      // 5. context - 引用来源，次要
      return (
        <div key={item.id} className={`${isMobile ? 'mb-2' : 'mb-3'} opacity-80`}>
          <ContextItemsView items={item.items} />
        </div>
      );
    } else if (item.type === 'agent_selected' && agentLabel(item.agentType, item.agentName)) {
      // 6. agent_selected - Agent 选择提示
      // 只显示第几步：带上分母（「步骤 2/12」）会让作者以为这一轮没做完。
      const hasIteration = item.iteration !== undefined;
      const isLowTurns = item.remaining !== undefined && item.remaining <= 2;

      return (
        <div key={item.id} className="mb-2">
          <div className={`inline-flex items-center rounded-lg ${
            isMobile ? 'gap-1 px-2 py-0.5' : 'gap-1.5 px-2.5 py-1'
          } ${
            isLowTurns
              ? 'bg-[hsl(var(--warning)/0.1)] border border-[hsl(var(--warning)/0.3)]'
              : 'bg-[hsl(var(--accent-primary)/0.1)] border border-[hsl(var(--accent-primary)/0.2)]'
          }`}>
            <Bot size={12} className={isLowTurns ? 'text-[hsl(var(--warning))]' : 'text-[hsl(var(--accent-primary))]'} />
            <span className={`text-xs ${isLowTurns ? 'text-[hsl(var(--warning))]' : 'text-[hsl(var(--accent-primary))]'} ${isMobile ? 'truncate max-w-[120px]' : ''}`}>
              {agentLabel(item.agentType, item.agentName)}
            </span>
            {hasIteration && (
              <span className={`text-xs ${isLowTurns ? 'text-[hsl(var(--warning))]' : 'text-[hsl(var(--text-secondary))]'}`}>
                · {t('workflow.iteration', { ns: 'chat', step: item.iteration })}
                {isLowTurns && t('workflow.remaining', { ns: 'chat', n: item.remaining })}
              </span>
            )}
          </div>
          {isLowTurns && (
            <div className={`${isMobile ? 'mt-1 ml-1' : 'mt-1 ml-1.5'} text-[11px] text-[hsl(var(--warning))]`}>
              {t('workflow.lowTurnWarning', { ns: 'chat' })}
            </div>
          )}
        </div>
      );
    } else if (item.type === 'iteration_exhausted') {
      // 7. iteration_exhausted - 这一轮的步数/协作轮数用完：正常暂停，用中性样式。
      const summaryText = item.layer === 'collaboration'
        ? t('workflow.collaborationExhaustedSummary', { ns: 'chat' })
        : t('workflow.toolCallExhaustedSummary', { ns: 'chat' });

      return (
        <div key={item.id} className={isMobile ? 'mb-2' : 'mb-3'}>
          <div className={`inline-flex items-start gap-2 rounded-lg bg-[hsl(var(--bg-tertiary))] border border-[hsl(var(--border-color))] ${isMobile ? 'px-2 py-1.5' : 'px-3 py-2'}`}>
            <CirclePause size={14} className="text-[hsl(var(--text-secondary))] mt-0.5 shrink-0" />
            <div className="flex flex-col gap-0.5 min-w-0">
              <span className="text-xs font-medium text-[hsl(var(--text-primary))]">
                {t('workflow.iterationExhausted', { ns: 'chat' })}
              </span>
              <span className="text-xs text-[hsl(var(--text-primary))] break-words">
                {summaryText}
              </span>
              <span className="text-[11px] text-[hsl(var(--text-secondary))] break-words">
                {t('workflow.nextStepHint', { ns: 'chat' })}
              </span>
              <div className={`mt-1 flex flex-wrap ${isMobile ? 'gap-1' : 'gap-1.5'}`}>
                <button
                  type="button"
                  className="text-[11px] px-2 py-0.5 rounded border border-[hsl(var(--accent-primary)/0.4)] text-[hsl(var(--accent-primary))] hover:bg-[hsl(var(--accent-primary)/0.1)] transition-colors"
                  onClick={() => onIterationAssistAction?.('continue', item)}
                >
                  {t('workflow.actionContinue', { ns: 'chat' })}
                </button>
                <button
                  type="button"
                  className="text-[11px] px-2 py-0.5 rounded border border-[hsl(var(--warning)/0.4)] text-[hsl(var(--warning))] hover:bg-[hsl(var(--warning)/0.1)] transition-colors"
                  onClick={() => onIterationAssistAction?.('split', item)}
                >
                  {t('workflow.actionSplit', { ns: 'chat' })}
                </button>
                <button
                  type="button"
                  className="text-[11px] px-2 py-0.5 rounded border border-[hsl(var(--text-secondary)/0.4)] text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--bg-tertiary))] transition-colors"
                  onClick={() => onIterationAssistAction?.('manual', item)}
                >
                  {t('workflow.actionManual', { ns: 'chat' })}
                </button>
              </div>
            </div>
          </div>
        </div>
      );
    } else if (item.type === 'router_thinking' && item.content) {
      // router_thinking - Router 正在分析请求
      return (
        <div key={item.id} className="mb-2">
          <div className={`inline-flex items-center rounded-lg bg-[hsl(var(--accent-primary)/0.1)] border border-[hsl(var(--accent-primary)/0.2)] ${isMobile ? 'gap-1 px-2 py-0.5' : 'gap-1.5 px-2.5 py-1'}`}>
            <Sparkles size={12} className="text-[hsl(var(--accent-primary))] animate-pulse" />
            <span className="text-xs text-[hsl(var(--accent-primary))] truncate">
              {item.content}
            </span>
          </div>
        </div>
      );
    } else if (item.type === 'router_decided') {
      // router_decided - Router 决策完成
      const plannedAgents = [item.initialAgent, ...(item.workflowAgents ?? [])];
      const workflowSteps = plannedAgents
        .map((agent) => getAgentDisplayName(agent, translate))
        .filter((name, index, names): name is string => Boolean(name) && names.indexOf(name) === index);
      return (
        <div key={item.id} className="mb-2">
          <div className={`inline-flex items-center rounded-lg bg-[hsl(var(--success)/0.1)] border border-[hsl(var(--success)/0.2)] ${isMobile ? 'gap-1 px-2 py-0.5' : 'gap-1.5 px-2.5 py-1'}`}>
            <Bot size={12} className="text-[hsl(var(--success))]" />
            <span className="text-xs text-[hsl(var(--success))]">
              {t('workflow.workflowLabel', { ns: 'chat' })}: {workflowSteps.length > 0 ? workflowSteps.join(' → ') : t('workflow.singleMode', { ns: 'chat' })}
            </span>
          </div>
        </div>
      );
    } else if (item.type === 'workflow_stopped') {
      // workflow_stopped - 工作流因需要澄清而停止
      const workflowQuestion =
        (item as { question?: string; message?: string }).question?.trim() ||
        (item as { message?: string }).message?.trim() ||
        "";
      const workflowContext = (item as { context?: string }).context?.trim() || "";
      const workflowDetails = ((item as { details?: string[] }).details ?? [])
        .map((d) => d.trim())
        .filter(Boolean);
      const workflowReason = (item as { reason?: string }).reason;
      if (workflowReason === 'user_stopped') {
        return <UserStoppedNote key={item.id} />;
      }
      const isClarificationStop = isClarificationStopItem(item);

      if (!isClarificationStop) {
        const stopMessage =
          workflowQuestion
          || t('workflow.stoppedGeneric', { ns: 'chat' });
        return (
          <div key={item.id} className={isMobile ? 'mb-2' : 'mb-3'}>
            <div className={`inline-flex items-start gap-2 rounded-lg bg-[hsl(var(--warning)/0.1)] border border-[hsl(var(--warning)/0.2)] ${isMobile ? 'px-2 py-1.5' : 'px-3 py-2'}`}>
              <AlertTriangle size={14} className="text-[hsl(var(--warning))] mt-0.5 shrink-0" />
              <div className="flex flex-col gap-0.5 min-w-0">
                <span className="text-xs font-medium text-[hsl(var(--warning))]">
                  {t('workflow.stoppedTitle', { ns: 'chat' })}
                </span>
                <span className="text-xs text-[hsl(var(--text-primary))] break-words">
                  {stopMessage}
                </span>
              </div>
            </div>
          </div>
        );
      }
      return (
        <div key={item.id} className={isMobile ? 'mb-2' : 'mb-3'}>
          <div className={`inline-flex items-start gap-2 rounded-lg bg-[hsl(var(--warning)/0.1)] border border-[hsl(var(--warning)/0.2)] ${isMobile ? 'px-2 py-1.5' : 'px-3 py-2'}`}>
            <AlertTriangle size={14} className="text-[hsl(var(--warning))] mt-0.5 shrink-0" />
            <div className="flex flex-col gap-0.5 min-w-0">
              <span className="text-xs font-medium text-[hsl(var(--warning))]">
                {t('workflow.waitingForReply', { ns: 'chat' })}
              </span>
              {workflowQuestion ? (
                <span className="text-xs text-[hsl(var(--text-primary))] break-words">
                  {workflowQuestion}
                </span>
              ) : (
                <span className="text-xs text-[hsl(var(--text-secondary))] break-words">
                  {t('workflow.needsConfirmation', { ns: 'chat' })}
                </span>
              )}
              {workflowContext ? (
                <span className="text-[11px] text-[hsl(var(--text-secondary))] break-words">
                  {workflowContext}
                </span>
              ) : null}
              {workflowDetails.length > 0 ? (
                <ul className="mt-1 list-disc pl-4 text-xs text-[hsl(var(--text-primary))]">
                  {workflowDetails.map((detail, detailIdx) => (
                    <li key={detailIdx} className="break-words">
                      {detail}
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          </div>
        </div>
      );
    } else if (item.type === 'workflow_complete') {
      // workflow_complete - 任务完成
      return (
        <div key={item.id} className={isMobile ? 'mb-2' : 'mb-3'}>
          <div className={`inline-flex items-start gap-2 rounded-lg bg-[hsl(var(--success)/0.1)] border border-[hsl(var(--success)/0.2)] ${isMobile ? 'px-2 py-1.5' : 'px-3 py-2'}`}>
            <Sparkles size={14} className="text-[hsl(var(--success))] mt-0.5 shrink-0" />
            <div className="flex flex-col gap-0.5">
              <span className="text-xs font-medium text-[hsl(var(--success))]">
                {t('workflow.taskCompleted', { ns: 'chat' })}
              </span>
            </div>
          </div>
        </div>
      );
    }
    return null;
  })}
  </>;
}

interface RowDataProps {
  /** Array of all messages (for context awareness) */
  messages: Message[];
  /** ID of currently streaming message */
  streamingMessageId?: string;
  /** Function to format timestamps for display */
  formatTime: (date: Date) => string;
  /** Callback to undo a file edit */
  onUndo?: (target: FileEditUndoTarget) => void;
  /** Callback to submit message feedback */
  onSubmitFeedback?: (message: Message, vote: MessageFeedbackVote) => void | Promise<void>;
  /** Message ID currently submitting feedback */
  feedbackPendingMessageId?: string | null;
  /** Stop notes may mention today's AI message count */
  showDailyCount?: boolean;
  /** Callback invoked when user chooses a guided action after iteration exhaustion */
  onIterationAssistAction?: MessageListProps["onIterationAssistAction"];
  /** Real-time thinking content from streaming */
  streamingThinkingContent?: string;
  /** Whether agent is actively thinking */
  isThinking?: boolean;
}

/**
 * Renders a single message row in the conversation list.
 * Handles user/assistant avatars, message bubbles, thinking content,
 * tool calls/results, conflicts, and timestamps.
 *
 * Consecutive messages from the same role are grouped together with
 * the avatar only shown on the first message.
 *
 * @param props - Component props including index and RowDataProps
 * @returns React element for the message row
 */
function Row({
  index,
  messages,
  streamingMessageId,
  formatTime,
  onUndo,
  onSubmitFeedback,
  feedbackPendingMessageId,
  showDailyCount = true,
  onIterationAssistAction,
  streamingThinkingContent,
  isThinking,
}: {
  index: number;
} & RowDataProps): React.ReactElement {
  const { t } = useTranslation(['chat']);
  const { user } = useAuth();
  const { isMobile } = useMobileLayout();
  const message = messages[index];
  if (!message) return <div />;

  const isStreaming = message.id === streamingMessageId;
  // Show streaming thinking content only for the currently streaming message
  const showStreamingThinking = isStreaming && streamingThinkingContent;
  const displayContent =
    message.role === 'assistant' ? stripThinkTags(message.content) : message.content;
  const hasDisplayContent = Boolean(displayContent && displayContent.trim());
  const hasDisplayItems = message.role === 'assistant' && Boolean(message.displayItems?.length);
  const toolCalls = visibleToolCalls(message.toolCalls);
  const toolResults = visibleToolCalls(message.toolResults);
  const shouldShowFeedbackActions =
    message.role === 'assistant' && Boolean(onSubmitFeedback) && !message.feedbackUnavailable;
  const canSubmitFeedback = shouldShowFeedbackActions && Boolean(message.backendMessageId);
  const selectedFeedbackVote = message.feedback?.vote;
  const isFeedbackPending = feedbackPendingMessageId === message.id;
  const isFeedbackDisabled = isFeedbackPending || !canSubmitFeedback;

  // Check if this is a consecutive message from the same role
  const prevMessage = index > 0 ? messages[index - 1] : null;
  const nextMessage = index < messages.length - 1 ? messages[index + 1] : null;
  const isConsecutiveSameRole = prevMessage?.role === message.role;
  const isLastInRoleGroup = nextMessage?.role !== message.role;

  // Always show timestamp for the last message in a role group, or for standalone messages
  const shouldShowTimestamp = isLastInRoleGroup;

  // Avatar size based on mobile/desktop
  const avatarSize = isMobile ? 'w-7 h-7' : 'w-8 h-8';
  const iconSize = isMobile ? 14 : 16;
  const gap = isMobile ? 'gap-2' : 'gap-3';

  const paddingY = isMobile ? 'py-2' : 'py-3';

  return (
    <div className={`flex items-start ${gap} ${paddingY}`}>
      {/* Avatar - only show for first message in consecutive group */}
      {!isConsecutiveSameRole ? (
        message.role === 'user' ? (
          user?.avatar_url ? (
            <img
              src={user.avatar_url}
              alt={user.username || 'User'}
              className={`shrink-0 ${avatarSize} rounded-full object-cover ring-2 ring-[hsl(var(--message-user-gradient-end)/0.5)]`}
            />
          ) : (
            <div className={`shrink-0 ${avatarSize} rounded-full bg-gradient-to-br from-[hsl(var(--message-user-gradient-start))] to-[hsl(var(--message-user-gradient-end))] flex items-center justify-center ring-2 ring-[hsl(var(--message-user-gradient-end)/0.3)]`}>
              <User size={iconSize} className="text-white" />
            </div>
          )
        ) : (
          <div className={`shrink-0 ${avatarSize} rounded-full bg-gradient-to-br from-[hsl(var(--accent-primary))] to-[hsl(var(--accent-dark))] flex items-center justify-center ring-2 ring-[hsl(var(--accent-primary)/0.3)]`}>
            <Sparkles size={iconSize} className="text-white" />
          </div>
        )
      ) : (
        // Empty placeholder to maintain alignment with avatars that have ring-2 (ring adds ~4px total width)
        // Use the same base size as avatar, then add ring offset for visual alignment
        <div className={`shrink-0 ${avatarSize}`} />
      )}

      {/* Content */}
      <div className="flex-1 min-w-0 overflow-hidden">
        {hasDisplayItems ? (
          <OrderedMessageItems items={message.displayItems!} onUndo={onUndo} onIterationAssistAction={onIterationAssistAction} isStreaming={isStreaming} />
        ) : <>
        {/* Thinking content - show streaming if active, otherwise show historical segments */}
        {message.role === 'assistant' && (
          showStreamingThinking ? (
            <ThinkingContent content={streamingThinkingContent} isStreaming={isThinking} />
          ) : (
            <>
              {/* Show all reasoning segments */}
              {message.reasoningSegments && message.reasoningSegments.length > 0 && message.reasoningSegments.map((segment, idx) => (
                <ThinkingContent
                  key={`reasoning-${idx}`}
                  content={segment.content}
                  isStreaming={false}
                />
              ))}
              {/* Fallback to legacy reasoningContent */}
              {!message.reasoningSegments || message.reasoningSegments.length === 0 ? (
                message.reasoningContent ? (
                  <ThinkingContent content={message.reasoningContent} isStreaming={false} />
                ) : null
              ) : null}
            </>
          )
        )}

        {/* Message bubble - only show if there's visible content */}
        {hasDisplayContent && (
          <div className="flex items-end gap-0.5">
            <div
              className={`chat-message-bubble ${
                message.role === 'user'
                  ? 'chat-message-bubble-user'
                  : 'chat-message-bubble-ai'
              } inline-block max-w-full text-sm ${
                isMobile ? 'px-3 py-2' : 'px-3.5 py-2.5'
              }`}
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
            {/* Streaming cursor */}
            {isStreaming && message.role === 'assistant' && (
              <span className="inline-block w-1.5 h-4 bg-[hsl(var(--accent-primary))] animate-pulse shrink-0" />
            )}
          </div>
        )}

        </>}

        {message.role === 'assistant' && message.stopOutcome && (
          <RoundEndNote outcome={message.stopOutcome} showDailyCount={showDailyCount} />
        )}

        {shouldShowFeedbackActions && (
          <div className="mt-2 flex items-center gap-1.5">
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
                : undefined}
              className={`inline-flex items-center justify-center rounded-md border transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-primary))] w-10 h-10 sm:w-8 sm:h-8 ${
                selectedFeedbackVote === 'up'
                  ? 'border-[hsl(var(--success)/0.45)] bg-[hsl(var(--success)/0.12)] text-[hsl(var(--success))]'
                  : 'border-[hsl(var(--separator-color))] text-[hsl(var(--text-tertiary))] hover:border-[hsl(var(--text-secondary)/0.45)] hover:text-[hsl(var(--text-primary))]'
              } ${isFeedbackDisabled ? 'cursor-not-allowed opacity-60' : ''}`}
            >
              <ThumbsUp size={14} />
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
                : undefined}
              className={`inline-flex items-center justify-center rounded-md border transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-primary))] w-10 h-10 sm:w-8 sm:h-8 ${
                selectedFeedbackVote === 'down'
                  ? 'border-[hsl(var(--warning)/0.5)] bg-[hsl(var(--warning)/0.12)] text-[hsl(var(--warning))]'
                  : 'border-[hsl(var(--separator-color))] text-[hsl(var(--text-tertiary))] hover:border-[hsl(var(--text-secondary)/0.45)] hover:text-[hsl(var(--text-primary))]'
              } ${isFeedbackDisabled ? 'cursor-not-allowed opacity-60' : ''}`}
            >
              <ThumbsDown size={14} />
            </button>
            {!canSubmitFeedback && (
              <span className="text-xs text-[hsl(var(--text-secondary))]">
                {t('feedback.pendingSave', { ns: 'chat' })}
              </span>
            )}
          </div>
        )}

        {/* Context items for assistant messages */}
        {message.role === 'assistant' && !hasDisplayItems && message.contextItems && message.contextItems.length > 0 && (
          <ContextItemsView items={message.contextItems} label={t('context.citations', { ns: 'chat' })} />
        )}

        {/* Tool calls */}
        {message.role === 'assistant' && !hasDisplayItems && toolCalls.length > 0 && (
          <div className={hasDisplayContent ? "mt-3 space-y-2" : "space-y-2"}>
            {toolCalls.map((toolCall, idx) => (
              <ToolResultCard
                key={idx}
                type={toolCall.status === 'pending' ? 'tool_call' : 'tool_result'}
                toolName={toolCall.tool_name}
                result={toolCall.result ?? (toolCall.arguments as Record<string, unknown>)}
                error={toolCall.status === 'error' ? toolCall.error : undefined}
                isPending={toolCall.status === 'pending' && isStreaming}
                interrupted={toolCall.status === 'pending' && !isStreaming}
                onUndo={onUndo}
              />
            ))}
          </div>
        )}

        {/* Tool results */}
        {message.role === 'assistant' && !hasDisplayItems && toolResults.length > 0 && (
          <div className="mt-3 space-y-2">
            {toolResults.map((result, idx) => (
              <ToolResultCard
                key={idx}
                type="tool_result"
                toolName={result.tool_name}
                result={result.result ?? {}}
                error={result.status === 'error' ? result.error : undefined}
                onUndo={onUndo}
              />
            ))}
          </div>
        )}

        {/* Conflicts */}
        {message.role === 'assistant' && message.conflicts && message.conflicts.length > 0 && (
          <div className="mt-3 space-y-2">
            {message.conflicts.map((conflict, idx) => (
              <ToolResultCard
                key={idx}
                type="conflict"
                toolName="consistency_check"
                result={{
                  conflict,
                }}
              />
            ))}
          </div>
        )}

        {/* Status cards (e.g. clarification needed / iteration exhausted) */}
        {message.role === 'assistant' && !hasDisplayItems && message.statusCards && message.statusCards.length > 0 && (
          <div className="mt-3 space-y-2">
            {message.statusCards.map((card, idx) => {
              if (card.type === 'workflow_stopped' && card.reason === 'user_stopped') {
                return <UserStoppedNote key={`status-${idx}`} />;
              }
              if (card.type === 'workflow_stopped') {
                const workflowQuestion = card.question?.trim() || card.message?.trim() || "";
                const workflowContext = (card.context ?? "").trim();
                const workflowDetails = (card.details ?? []).map((d) => d.trim()).filter(Boolean);
                const isClarificationStop = isClarificationStopItem(card);

                if (!isClarificationStop) {
                  const stopMessage =
                    workflowQuestion
                    || t('workflow.stoppedGeneric', { ns: 'chat' });
                  return (
                    <div key={`status-${idx}`} className="max-w-full">
                      <div className={`inline-flex items-start gap-2 rounded-lg bg-[hsl(var(--warning)/0.1)] border border-[hsl(var(--warning)/0.2)] ${isMobile ? 'px-2 py-1.5' : 'px-3 py-2'}`}>
                        <AlertTriangle size={14} className="text-[hsl(var(--warning))] mt-0.5 shrink-0" />
                        <div className="flex flex-col gap-0.5 min-w-0">
                          <span className="text-xs font-medium text-[hsl(var(--warning))]">
                            {t('workflow.stoppedTitle', { ns: 'chat' })}
                          </span>
                          <span className="text-xs text-[hsl(var(--text-primary))] break-words">
                            {stopMessage}
                          </span>
                        </div>
                      </div>
                    </div>
                  );
                }
                return (
                  <div key={`status-${idx}`} className="max-w-full">
                    <div className={`inline-flex items-start gap-2 rounded-lg bg-[hsl(var(--warning)/0.1)] border border-[hsl(var(--warning)/0.2)] ${isMobile ? 'px-2 py-1.5' : 'px-3 py-2'}`}>
                      <AlertTriangle size={14} className="text-[hsl(var(--warning))] mt-0.5 shrink-0" />
                      <div className="flex flex-col gap-0.5 min-w-0">
                        <span className="text-xs font-medium text-[hsl(var(--warning))]">
                          {t('workflow.waitingForReply', { ns: 'chat' })}
                        </span>
                        {workflowQuestion ? (
                          <span className="text-xs text-[hsl(var(--text-primary))] break-words">
                            {workflowQuestion}
                          </span>
                        ) : (
                          <span className="text-xs text-[hsl(var(--text-secondary))] break-words">
                            {t('workflow.needsConfirmation', { ns: 'chat' })}
                          </span>
                        )}
                        {workflowContext ? (
                          <span className="text-[11px] text-[hsl(var(--text-secondary))] break-words">
                            {workflowContext}
                          </span>
                        ) : null}
                        {workflowDetails.length > 0 ? (
                          <ul className="mt-1 list-disc pl-4 text-xs text-[hsl(var(--text-primary))]">
                            {workflowDetails.map((detail, detailIdx) => (
                              <li key={detailIdx} className="break-words">
                                {detail}
                              </li>
                            ))}
                          </ul>
                        ) : null}
                      </div>
                    </div>
                  </div>
                );
              }

              if (card.type === 'iteration_exhausted') {
                // 正常暂停（不是失败）：中性样式。
                const summaryText = card.layer === 'collaboration'
                  ? t('workflow.collaborationExhaustedSummary', { ns: 'chat' })
                  : t('workflow.toolCallExhaustedSummary', { ns: 'chat' });

                return (
                  <div key={`status-${idx}`} className="max-w-full">
                    <div className={`inline-flex items-start gap-2 rounded-lg bg-[hsl(var(--bg-tertiary))] border border-[hsl(var(--border-color))] ${isMobile ? 'px-2 py-1.5' : 'px-3 py-2'}`}>
                      <CirclePause size={14} className="text-[hsl(var(--text-secondary))] mt-0.5 shrink-0" />
                      <div className="flex flex-col gap-0.5 min-w-0">
                        <span className="text-xs font-medium text-[hsl(var(--text-primary))]">
                          {t('workflow.iterationExhausted', { ns: 'chat' })}
                        </span>
                        <span className="text-xs text-[hsl(var(--text-primary))] break-words">
                          {summaryText}
                        </span>
                        <span className="text-[11px] text-[hsl(var(--text-secondary))] break-words">
                          {t('workflow.nextStepHint', { ns: 'chat' })}
                        </span>
                        <div className={`mt-1 flex flex-wrap ${isMobile ? 'gap-1' : 'gap-1.5'}`}>
                          <button
                            type="button"
                            className="text-[11px] px-2 py-0.5 rounded border border-[hsl(var(--accent-primary)/0.4)] text-[hsl(var(--accent-primary))] hover:bg-[hsl(var(--accent-primary)/0.1)] transition-colors"
                            onClick={() => onIterationAssistAction?.('continue', card)}
                          >
                            {t('workflow.actionContinue', { ns: 'chat' })}
                          </button>
                          <button
                            type="button"
                            className="text-[11px] px-2 py-0.5 rounded border border-[hsl(var(--warning)/0.4)] text-[hsl(var(--warning))] hover:bg-[hsl(var(--warning)/0.1)] transition-colors"
                            onClick={() => onIterationAssistAction?.('split', card)}
                          >
                            {t('workflow.actionSplit', { ns: 'chat' })}
                          </button>
                          <button
                            type="button"
                            className="text-[11px] px-2 py-0.5 rounded border border-[hsl(var(--text-secondary)/0.4)] text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--bg-tertiary))] transition-colors"
                            onClick={() => onIterationAssistAction?.('manual', card)}
                          >
                            {t('workflow.actionManual', { ns: 'chat' })}
                          </button>
                        </div>
                      </div>
                    </div>
                  </div>
                );
              }

              return null;
            })}
          </div>
        )}

        {/* Timestamp - always show at the bottom of each message group */}
        {shouldShowTimestamp && (
          <div className={`chat-timestamp ${isMobile ? 'mt-1.5' : 'mt-2'}`}>
            {formatTime(message.timestamp)}
          </div>
        )}
      </div>
    </div>
  );
};

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
  streamingMessageId,
  onUndo,
  onSubmitFeedback,
  feedbackPendingMessageId,
  showDailyCount,
  streamingThinkingContent,
  isThinking,
  streamRenderItems,
  onIterationAssistAction,
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
    const hasConflicts = m.role === 'assistant' && Boolean(m.conflicts?.length);
    const hasContextItems = m.role === 'assistant' && Boolean(m.contextItems?.length);
    const hasStatusCards = m.role === 'assistant' && Boolean(m.statusCards?.length);
    // Include messages with reasoning segments (thinking content)
    const hasReasoningSegments = m.role === 'assistant' && Boolean(m.reasoningSegments?.length);
    const hasReasoningContent = m.role === 'assistant' && Boolean(m.reasoningContent?.trim());

    // A stopped / interrupted round with nothing else to show still shows how it ended.
    const hasStopOutcome = m.role === 'assistant' && Boolean(m.stopOutcome);

    return (
      Boolean(m.displayItems?.length) ||
      hasStopOutcome ||
      hasVisibleContent ||
      hasToolCalls ||
      hasToolResults ||
      hasConflicts ||
      hasContextItems ||
      hasStatusCards ||
      hasReasoningSegments ||
      hasReasoningContent
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

  // Check if we should show AI avatar for stream items
  const shouldShowAIAvatar = useMemo(() => {
    if (!messages || messages.length === 0) return true; // No messages, show avatar
    const lastMessage = messages[messages.length - 1];
    return lastMessage.role !== 'assistant'; // Show avatar if last message is not from assistant
  }, [messages]);

  // 检查是否应该显示 streamRenderItems
  // 因为 onComplete 中立即清空了 streamRenderItems，所以只需要简单检查是否有内容即可
  const safeStreamRenderItems = useMemo(
    () => streamRenderItems ?? [],
    [streamRenderItems]
  );
  const shouldShowStreamItems = useMemo(() => {
    return safeStreamRenderItems.length > 0;
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

  // Show streaming thinking content when there are no messages yet
  // or when the thinking is not yet attached to a message
  const shouldShowFloatingThinking =
    streamingThinkingContent &&
    (!streamingMessageId || !visibleMessages.find(m => m.id === streamingMessageId));

  // Don't render anything if there are no visible messages and no thinking content and no stream items
  if (visibleMessages.length === 0 && !shouldShowFloatingThinking && !shouldShowStreamItems) {
    return null;
  }

  return (
    // ph-no-capture: prompts and replies must never reach analytics capture.
    <div className="ph-no-capture" aria-live="polite" aria-atomic="true">
      {visibleMessages.map((message, index) => (
        <Row
          key={message.id}
          index={index}
          messages={visibleMessages}
          streamingMessageId={streamingMessageId}
          formatTime={formatTime}
          onUndo={onUndo}
          onSubmitFeedback={onSubmitFeedback}
          feedbackPendingMessageId={feedbackPendingMessageId}
          showDailyCount={showDailyCount}
          onIterationAssistAction={onIterationAssistAction}
          streamingThinkingContent={streamingThinkingContent}
          isThinking={isThinking}
        />
      ))}

      {/* 然后渲染流式内容 - 按服务端顺序直接追加在最后 */}
      {shouldShowStreamItems && (
        <div key="stream-items" className={`flex items-start py-2 ${isMobile ? 'gap-2' : 'gap-3'}`}>
          {/* AI Avatar - 只在第一条消息是 assistant 时显示 */}
          {shouldShowAIAvatar ? (
            <div className={`shrink-0 rounded-full bg-[hsl(var(--accent-primary))] flex items-center justify-center ${isMobile ? 'w-7 h-7' : 'w-8 h-8'}`}>
              <Sparkles size={isMobile ? 14 : 16} className="text-white" />
            </div>
          ) : (
            <div className={`shrink-0 ${isMobile ? 'w-7 h-7' : 'w-8 h-8'}`} />
          )}

          {/* Stream items */}
          <div className="flex-1 min-w-0 overflow-hidden">
            <OrderedMessageItems items={safeStreamRenderItems} onUndo={onUndo} onIterationAssistAction={onIterationAssistAction} isStreaming={true} />

            {/* Timestamp - 只在非 streaming 状态时显示 */}
            {!streamingMessageId && streamRenderItems && streamRenderItems.length > 0 && (
              <div className={`text-xs text-[hsl(var(--text-secondary))] ${isMobile ? 'mt-2' : 'mt-3'}`}>
                {formatTime(streamRenderItems[streamRenderItems.length - 1].timestamp)}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}),
(prevProps, nextProps) => {
  // Custom comparison function for React.memo.
  // Prefer cheap reference checks to avoid O(n) message comparisons.
  if (prevProps.streamingMessageId !== nextProps.streamingMessageId) return false;
  if (prevProps.onUndo !== nextProps.onUndo) return false;
  if (prevProps.onSubmitFeedback !== nextProps.onSubmitFeedback) return false;
  // The iteration-exhausted action buttons call this; omitting it here let the
  // buttons keep invoking a stale closure after the parent re-created it.
  if (prevProps.onIterationAssistAction !== nextProps.onIterationAssistAction) return false;
  if (prevProps.feedbackPendingMessageId !== nextProps.feedbackPendingMessageId) return false;
  if (prevProps.showDailyCount !== nextProps.showDailyCount) return false;
  if (prevProps.streamingThinkingContent !== nextProps.streamingThinkingContent) return false;
  if (prevProps.isThinking !== nextProps.isThinking) return false;
  if (prevProps.messages !== nextProps.messages) return false;
  if (prevProps.streamRenderItems !== nextProps.streamRenderItems) return false;

  return true;
});
