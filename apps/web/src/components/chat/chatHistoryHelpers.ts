/**
 * Pure parsers, matchers, and history-merging helpers for ChatPanel.
 *
 * @module components/chat/chatHistoryHelpers
 */

import type { TFunction } from "i18next";
import type { MessageSegment } from "../../hooks/useAgentStream";
import type { Message } from "../MessageList";
import type { OpenEditorFlushResult } from "../../lib/editorSaveTracker";
import type {
  ChatMessage,
  MessageFeedbackData,
  MessageFeedbackVote,
} from "../../lib/chatApi";
import type { ToolCall } from "../../types";
import { parseChatDisplayEvents } from "../../lib/chatDisplayEvents";
import { parseUTCDate } from "../../lib/dateUtils";
import {
  exhaustionLeftFilesUnfinished,
  parseIterationExhaustedFiles,
  parseRoundStopOutcome,
} from "../../lib/chatRoundEnd";

export const clampNumber = (value: number, min: number, max: number): number =>
  Math.min(max, Math.max(min, value));

const LEGACY_ASSISTANT_SESSION_TITLES = new Set(["AI 助手对话", "AI Assistant"]);
const LEGACY_NEW_CHAT_SESSION_TITLES = new Set(["新对话", "New Chat"]);

/**
 * Localize legacy default chat session titles ("AI 助手对话" / "AI Assistant" / "新对话" / "New Chat")
 * symmetrically at display time while preserving any custom session titles verbatim.
 */
export const formatChatSessionTitle = (
  title: string | null | undefined,
  t: TFunction,
  _language?: string,
): string => {
  const trimmed = typeof title === "string" ? title.trim() : "";
  if (!trimmed) {
    return t("chat:panel.title");
  }
  if (LEGACY_ASSISTANT_SESSION_TITLES.has(trimmed)) {
    return t("chat:panel.title");
  }
  if (LEGACY_NEW_CHAT_SESSION_TITLES.has(trimmed)) {
    return t("chat:defaultTitle");
  }
  return trimmed;
};

export const isNearBottom = (el: HTMLElement, thresholdPx = 48): boolean =>
  el.scrollHeight - el.scrollTop - el.clientHeight < thresholdPx;

/**
 * Stop options for "the editor could not save the author's text before the round
 * ends": the server keeps that file even if it looks blank.
 */
export const keepUnsavedFile = (
  result: OpenEditorFlushResult,
): { keepFileIds: string[] } | undefined =>
  !result.saved && result.fileId ? { keepFileIds: [result.fileId] } : undefined;

/** The account's task slots are taken: the server started and charged nothing. */
export const isAccountRunRefusal = (code: string | null | undefined): boolean =>
  code === "ERR_ACCOUNT_RUNS_BUSY";

export {
  isUnexpectedStreamDisconnect,
  SSE_DISCONNECT_RECONCILE_DELAY_MS,
} from "../../lib/streamDisconnect";

/**
 * This network's shared free allowance is used up: nothing was started or charged. Sending stays
 * open (the author may change networks, and the server decides). This account's own count is
 * untouched, so this is not reported as the used-up day.
 */
export const isNetworkQuotaRefusal = (code: string | null | undefined): boolean =>
  code === "ERR_QUOTA_FREE_NETWORK_DAILY_EXCEEDED";

export const parseMessageMetadataObject = (
  metadataRaw?: string | null,
): Record<string, unknown> | null => {
  if (!metadataRaw) return null;

  try {
    const parsed: unknown = JSON.parse(metadataRaw);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
};

export const parseMessageStatusCardsFromMetadata = (
  metadata: Record<string, unknown> | null,
): Message["statusCards"] | undefined => {
  if (!metadata) return undefined;

  const statusCards = metadata.status_cards;
  if (!Array.isArray(statusCards)) return undefined;

  const normalized = statusCards
    .map((card): NonNullable<Message["statusCards"]>[number] | null => {
      if (!card || typeof card !== "object") return null;

      const cardType = (card as { type?: unknown }).type;
      if (cardType === "workflow_stopped") {
        const reason = (card as { reason?: unknown }).reason;
        const agentType = (card as { agentType?: unknown }).agentType;
        const message = (card as { message?: unknown }).message;
        const question = (card as { question?: unknown }).question;
        const context = (card as { context?: unknown }).context;
        const details = (card as { details?: unknown }).details;

        return {
          type: "workflow_stopped",
          reason: typeof reason === "string" ? reason : undefined,
          agentType: typeof agentType === "string" ? agentType : undefined,
          message: typeof message === "string" ? message : undefined,
          question: typeof question === "string" ? question : undefined,
          context: typeof context === "string" ? context : undefined,
          details: Array.isArray(details)
            ? details.filter((detail): detail is string => typeof detail === "string")
            : undefined,
        };
      }

      if (cardType === "iteration_exhausted") {
        const layer = (card as { layer?: unknown }).layer;
        const iterationsUsed = (card as { iterationsUsed?: unknown }).iterationsUsed;
        const maxIterations = (card as { maxIterations?: unknown }).maxIterations;
        const reason = (card as { reason?: unknown }).reason;
        const lastAgent = (card as { lastAgent?: unknown }).lastAgent;
        const limit = (card as { limit?: unknown }).limit;
        const unfinished = parseIterationExhaustedFiles(
          (card as { unfinishedFiles?: unknown }).unfinishedFiles,
          (card as { rolledBackFiles?: unknown }).rolledBackFiles,
        );

        return {
          type: "iteration_exhausted",
          layer:
            layer === "collaboration" ||
            layer === "tool_call" ||
            layer === "empty_file_correction" ||
            layer === "unexecuted_tool_text"
              ? layer
              : undefined,
          ...unfinished,
          iterationsUsed: typeof iterationsUsed === "number" ? iterationsUsed : undefined,
          maxIterations: typeof maxIterations === "number" ? maxIterations : undefined,
          reason: typeof reason === "string" ? reason : undefined,
          lastAgent: typeof lastAgent === "string" ? lastAgent : undefined,
          limit: typeof limit === "string" ? limit : undefined,
        };
      }

      return null;
    })
    .filter((card): card is NonNullable<Message["statusCards"]>[number] => card !== null);

  return normalized.length > 0 ? normalized : undefined;
};

/**
 * A saved round that ended in an error (stop_outcome) or stopped with a file that never got its
 * text (an iteration_exhausted card saying so, in the saved timeline or the legacy status cards).
 */
export const roundEndedUnfinished = (message: Message): boolean =>
  message.stopOutcome?.reason === "error" ||
  message.stopOutcome?.reason === "incomplete" ||
  Boolean(
    message.statusCards?.some(
      (card) => card.type === "iteration_exhausted" && exhaustionLeftFilesUnfinished(card),
    ),
  ) ||
  Boolean(
    message.displayItems?.some(
      (item) => item.type === "iteration_exhausted" && exhaustionLeftFilesUnfinished(item),
    ),
  );

export const parseToolCallsFromHistory = (
  toolCallsRaw?: string | null,
): Pick<Message, "toolCalls" | "toolResults"> & { orderedToolCalls?: ToolCall[] } => {
  if (!toolCallsRaw) return {};

  try {
    const parsed = JSON.parse(toolCallsRaw) as unknown;
    if (!Array.isArray(parsed)) return {};

    const normalized = parsed
      .map((item): ToolCall | null => {
        if (!item || typeof item !== "object") return null;

        const id = (item as { id?: unknown }).id;
        const toolName = (item as { name?: unknown }).name;
        const rawArguments = (item as { arguments?: unknown }).arguments;
        const status = (item as { status?: unknown }).status;
        const result = (item as { result?: unknown }).result;
        const error = (item as { error?: unknown }).error;

        let normalizedArguments: Record<string, unknown> = {};
        if (rawArguments && typeof rawArguments === "object") {
          normalizedArguments = rawArguments as Record<string, unknown>;
        } else if (typeof rawArguments === "string") {
          try {
            const parsedArguments = JSON.parse(rawArguments) as unknown;
            if (parsedArguments && typeof parsedArguments === "object") {
              normalizedArguments = parsedArguments as Record<string, unknown>;
            }
          } catch {
            normalizedArguments = {};
          }
        }

        return {
          id:
            typeof id === "string" && id.trim()
              ? id
              : `history-tool-${Math.random().toString(36).slice(2, 10)}`,
          tool_name: typeof toolName === "string" ? toolName : "unknown",
          arguments: normalizedArguments,
          status:
            status === "pending" || status === "error" || status === "success"
              ? status
              : "success",
          result:
            result && typeof result === "object" ? (result as Record<string, unknown>) : undefined,
          error: typeof error === "string" ? error : undefined,
        };
      })
      .filter((item): item is ToolCall => item !== null);

    if (normalized.length === 0) return {};

    return {
      orderedToolCalls: normalized,
      toolCalls: normalized.filter((toolCall) => toolCall.status === "pending"),
      toolResults: normalized.filter((toolCall) => toolCall.status !== "pending"),
    };
  } catch {
    return {};
  }
};

export const extractCompletedToolResults = (
  segments: MessageSegment[],
): Message["toolResults"] | undefined => {
  const completedToolResults = segments.flatMap((segment) => {
    if (segment.type !== "tool_calls" || !segment.toolCalls?.length) {
      return [];
    }

    return segment.toolCalls
      .filter((toolCall) => toolCall.status !== "pending")
      .map((toolCall) => ({
        ...toolCall,
        arguments: toolCall.arguments ?? {},
      }));
  });

  return completedToolResults.length > 0 ? completedToolResults : undefined;
};

export const parseMessageFeedbackFromMetadata = (
  metadata: Record<string, unknown> | null,
): Message["feedback"] | undefined => {
  if (!metadata) return undefined;

  const feedback = metadata.feedback;
  if (!feedback || typeof feedback !== "object") return undefined;

  const vote = (feedback as { vote?: unknown }).vote;
  if (vote !== "up" && vote !== "down") return undefined;

  const preset = (feedback as { preset?: unknown }).preset;
  const comment = (feedback as { comment?: unknown }).comment;
  const updatedAt = (feedback as { updated_at?: unknown }).updated_at;

  return {
    vote,
    preset: typeof preset === "string" ? preset : null,
    comment: typeof comment === "string" ? comment : null,
    updated_at: typeof updatedAt === "string" ? updatedAt : null,
  };
};

export const normalizeFeedbackFromResponse = (
  feedback: MessageFeedbackData | undefined,
  fallbackVote: MessageFeedbackVote,
  fallbackUpdatedAt: string,
): Message["feedback"] => ({
  vote: feedback?.vote ?? fallbackVote,
  preset: feedback?.preset ?? null,
  comment: feedback?.comment ?? null,
  updated_at: feedback?.updated_at ?? fallbackUpdatedAt,
});

export const normalizeMessageContent = (content: string): string =>
  content.replace(/\s+/g, " ").trim();

export const findUnassignedAssistantMessage = (
  recentMessages: ChatMessage[],
  assignedBackendIds: Set<string>,
  expectedContent: string,
  expectedSessionId?: string | null,
): ChatMessage | null => {
  const candidates = recentMessages.filter(
    (message) =>
      message.role === "assistant" &&
      !assignedBackendIds.has(message.id) &&
      (!expectedSessionId || message.session_id === expectedSessionId),
  );

  if (candidates.length === 0) {
    return null;
  }

  const normalizedExpectedContent = normalizeMessageContent(expectedContent);
  if (normalizedExpectedContent) {
    for (let index = candidates.length - 1; index >= 0; index -= 1) {
      const candidate = candidates[index];
      if (normalizeMessageContent(candidate.content) === normalizedExpectedContent) {
        return candidate;
      }
    }
  }

  return candidates[candidates.length - 1] ?? null;
};

export const mapBackendHistoryMessages = (
  historyMessages: ChatMessage[],
  t: TFunction,
): Message[] =>
  historyMessages.map((msg) => {
    const parsedMetadata = parseMessageMetadataObject(msg.metadata);
    const createdAt = parseUTCDate(msg.created_at);
    const feedback = parseMessageFeedbackFromMetadata(parsedMetadata);
    const statusCards = parseMessageStatusCardsFromMetadata(parsedMetadata);
    const { toolCalls, toolResults, orderedToolCalls } = parseToolCallsFromHistory(msg.tool_calls);

    return {
      id: msg.id,
      backendMessageId: msg.id,
      role: msg.role as "user" | "assistant",
      content: msg.content,
      timestamp: createdAt,
      feedback,
      statusCards,
      toolCalls,
      toolResults,
      displayItems: parseChatDisplayEvents(parsedMetadata, orderedToolCalls ?? [], createdAt, t),
      stopOutcome:
        msg.role === "assistant" ? parseRoundStopOutcome(parsedMetadata) ?? undefined : undefined,
    };
  });

export function mergeEarlierHistoryMessages(
  fetchedHistory: Message[],
  currentMessages: Message[],
): Message[] {
  if (currentMessages.length === 0) return fetchedHistory;
  if (fetchedHistory.length === 0) return currentMessages;

  const currentKeys = new Set<string>();
  for (const msg of currentMessages) {
    currentKeys.add(msg.id);
    if (msg.backendMessageId) {
      currentKeys.add(msg.backendMessageId);
    }
  }

  const anchor = currentMessages[0];
  const anchorKeys = new Set<string>([anchor.id]);
  if (anchor.backendMessageId) {
    anchorKeys.add(anchor.backendMessageId);
  }

  const anchorIndex = fetchedHistory.findIndex(
    (msg) =>
      anchorKeys.has(msg.id) ||
      (msg.backendMessageId ? anchorKeys.has(msg.backendMessageId) : false),
  );

  let earlierCandidates: Message[];
  if (anchorIndex !== -1) {
    earlierCandidates = fetchedHistory.slice(0, anchorIndex);
  } else {
    // currentMessages[0] is absent from the fetched window (e.g. >35 new messages or
    // un-synced optimistic IDs). Only prepend if every fetched message is strictly
    // older than currentMessages[0] and does not overlap any existing message.
    const anchorMs = anchor.timestamp instanceof Date ? anchor.timestamp.getTime() : NaN;
    const allStrictlyOlder =
      Number.isFinite(anchorMs) &&
      fetchedHistory.every((msg) => {
        const msgMs = msg.timestamp instanceof Date ? msg.timestamp.getTime() : NaN;
        const overlapsCurrent =
          currentKeys.has(msg.id) ||
          (msg.backendMessageId ? currentKeys.has(msg.backendMessageId) : false);
        return Number.isFinite(msgMs) && msgMs < anchorMs && !overlapsCurrent;
      });
    if (!allStrictlyOlder) {
      return currentMessages;
    }
    earlierCandidates = fetchedHistory;
  }

  const earlierMessages = earlierCandidates.filter(
    (msg) =>
      !currentKeys.has(msg.id) &&
      !(msg.backendMessageId && currentKeys.has(msg.backendMessageId)),
  );

  if (earlierMessages.length === 0) return currentMessages;
  return [...earlierMessages, ...currentMessages];
}
