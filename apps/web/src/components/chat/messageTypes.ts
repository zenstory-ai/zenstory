/**
 * Shared types for MessageList and its chat message subcomponents.
 *
 * @module components/chat/messageTypes
 */

import type React from 'react';
import type { StreamRenderItem } from '../../hooks/streaming/types';
import type { FileEditUndoTarget, IterationExhaustedLayer, ToolCall } from '../../types';
import type { RoundStopOutcome } from '../../lib/chatRoundEnd';
import type { EditFailureRetryContext } from './toolCards/toolCardHelpers';

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
export interface MessageListProps {
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
  /** Explicit user-initiated retry for an unresolved file-edit failure. */
  onRetryEditFailure?: (context: EditFailureRetryContext) => void;
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
 * Props passed to each message Row component.
 */
export interface RowDataProps {
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
  onIterationAssistAction?: MessageListProps['onIterationAssistAction'];
  /** Explicit user-initiated retry for an unresolved file-edit failure. */
  onRetryEditFailure?: MessageListProps['onRetryEditFailure'];
  nextStepsBlocked?: boolean;
}
