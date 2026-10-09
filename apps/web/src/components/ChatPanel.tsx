/**
 * @fileoverview ChatPanel component - AI chat interface for the zenstory writing workbench.
 *
 * This component provides the main AI conversation interface, handling:
 * - Message history loading and persistence
 * - User input handling and draft persistence
 * - File attachment and text quote context assembly
 * - Resizable input panel with height persistence
 * - Coordination between useAgentStream and useChatStreaming hooks
 *
 * Streaming UI state management is delegated to {@link useChatStreaming} hook, which handles:
 * - Stream render items with throttled updates for performance
 * - Edit progress tracking for AI file modifications
 * - Skill matching state
 * - Callback handlers for all SSE events from useAgentStream
 *
 * Note: Suggestion refresh is kept in ChatPanel because it depends on current
 * project + live message history (which are owned by this component). Suggestions
 * are requested automatically only when a project opens without a cached set and
 * once after each completed turn (deduplicated); there is no idle polling.
 *
 * @see useChatStreaming - Streaming UI state management hook
 * @see useAgentStream - SSE streaming hook for AI responses
 * @module components/ChatPanel
 */
import React, { useState, useRef, useEffect, useCallback, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useProject } from "../contexts/ProjectContext";
import { useMobileLayout } from "../contexts/MobileLayoutContext";
import { useMaterialAttachment } from "../contexts/MaterialAttachmentContext";
import { useTextQuote } from "../contexts/TextQuoteContext";
import { useAgentStream } from "../hooks/useAgentStream";
import type { MessageSegment, StreamCompletionMeta } from "../hooks/useAgentStream";
import { parseChatDisplayEvents } from "../lib/chatDisplayEvents";
import { useChatStreaming } from "../hooks/useChatStreaming";
import { MessageList } from "./MessageList";
import type { Message, MessageListRef, MessageStatusCard } from "./MessageList";
import { MessageInput } from "./MessageInput";
import { ToolResultCard } from "./ToolResultCard";
import { Sparkles, Loader2, Plus, Edit3, Database, ArrowDown } from "lucide-react";
import { QuotaBadge } from "./subscription/QuotaBadge";
import { ConfirmDialog } from "./ui/ConfirmDialog";
import { subscriptionApi, subscriptionQueryKeys } from "../lib/subscriptionApi";
import type {
  AgentContextItem,
  AgentRequest,
  ApplyAction,
  FileEditUndoTarget,
  ProjectNextStep,
  SSEWorkflowStoppedData,
} from "../types";
import { logger } from "../lib/logger";
import {
  getRecentMessages,
  createNewSession,
  submitMessageFeedback,
  type ChatMessage,
  type MessageFeedbackData,
  type MessageFeedbackVote,
} from "../lib/chatApi";
import { fileVersionApi, projectApi, versionApi } from "../lib/api";
import { NextStepCard } from "./NextStepCard";
import { fetchSuggestions, type QuotaRefundKind } from "../lib/agentApi";
import { parseUTCDate } from "../lib/dateUtils";
import { ApiError } from "../lib/apiClient";
import { handleApiError } from "../lib/errorHandler";
import { toast } from "../lib/toast";
import { ProjectStatusDialog } from "./ProjectStatusDialog";
import { useDraftPersistence } from "../hooks/useDraftPersistence";
import { UpgradePromptModal } from "./subscription/UpgradePromptModal";
import { buildUpgradeUrl, getUpgradePromptDefinition } from "../config/upgradeExperience";
import { trackEvent } from "../lib/analytics";
import { useStreamActivity } from "../hooks/useStreamActivity";
import { useLeaveWhileGenerating } from "../hooks/useLeaveWhileGenerating";
import { StreamActivityLine } from "./StreamActivityLine";

const parseMessageStatusCardsFromMetadata = (metadataRaw?: string | null): Message["statusCards"] | undefined => {
  if (!metadataRaw) return undefined;

  try {
    const parsed = JSON.parse(metadataRaw) as { status_cards?: unknown };
    const statusCards = parsed.status_cards;
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
          const confidence = (card as { confidence?: unknown }).confidence;
          const evaluation = (card as { evaluation?: unknown }).evaluation;

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
            confidence: typeof confidence === "number" ? confidence : undefined,
            evaluation:
              evaluation && typeof evaluation === "object"
                ? {
                    complete_score: Number((evaluation as { complete_score?: unknown }).complete_score ?? 0),
                    clarification_score: Number((evaluation as { clarification_score?: unknown }).clarification_score ?? 0),
                    consistency_score: Number((evaluation as { consistency_score?: unknown }).consistency_score ?? 0),
                    decision_reason:
                      typeof (evaluation as { decision_reason?: unknown }).decision_reason === "string"
                        ? (evaluation as { decision_reason: string }).decision_reason
                        : "",
                  }
                : undefined,
          };
        }

        if (cardType === "iteration_exhausted") {
          const layer = (card as { layer?: unknown }).layer;
          const iterationsUsed = (card as { iterationsUsed?: unknown }).iterationsUsed;
          const maxIterations = (card as { maxIterations?: unknown }).maxIterations;
          const reason = (card as { reason?: unknown }).reason;
          const lastAgent = (card as { lastAgent?: unknown }).lastAgent;

          return {
            type: "iteration_exhausted",
            layer: layer === "collaboration" || layer === "tool_call" ? layer : undefined,
            iterationsUsed: typeof iterationsUsed === "number" ? iterationsUsed : undefined,
            maxIterations: typeof maxIterations === "number" ? maxIterations : undefined,
            reason: typeof reason === "string" ? reason : undefined,
            lastAgent: typeof lastAgent === "string" ? lastAgent : undefined,
          };
        }

        return null;
      })
      .filter((card): card is NonNullable<Message["statusCards"]>[number] => card !== null);

    return normalized.length > 0 ? normalized : undefined;
  } catch {
    return undefined;
  }
};

const parseToolCallsFromHistory = (toolCallsRaw?: string | null): Pick<Message, "toolCalls" | "toolResults"> & { orderedToolCalls?: import("../types").ToolCall[] } => {
  if (!toolCallsRaw) return {};

  try {
    const parsed = JSON.parse(toolCallsRaw) as unknown;
    if (!Array.isArray(parsed)) return {};

    const normalized = parsed
      .map((item): import("../types").ToolCall | null => {
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
          id: typeof id === "string" && id.trim() ? id : `history-tool-${Math.random().toString(36).slice(2, 10)}`,
          tool_name: typeof toolName === "string" ? toolName : "unknown",
          arguments: normalizedArguments,
          status:
            status === "pending" || status === "error" || status === "success"
              ? status
              : "success",
          result: result && typeof result === "object" ? (result as Record<string, unknown>) : undefined,
          error: typeof error === "string" ? error : undefined,
        };
      })
      .filter((item): item is import("../types").ToolCall => item !== null);

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

/**
 * Props for the ChatPanel component.
 * The panel integrates with global contexts for project state, materials, and quotes.
 */
// eslint-disable-next-line @typescript-eslint/no-empty-object-type
interface ChatPanelProps {}

/** Local storage key for persisting chat input panel height */
const CHAT_INPUT_PANEL_HEIGHT_KEY = "zenstory_chat_input_panel_height_px";
const SUGGESTION_CACHE_KEY_PREFIX = "zenstory_suggestions_cache_";
const SUGGESTION_CACHE_TTL_MS = 10 * 60 * 1000;
const SUGGESTION_INITIAL_TIMEOUT_MS = 1500;
const GENERATION_MODE_STORAGE_KEY_PREFIX = "zenstory_generation_mode_";

type GenerationMode = "fast" | "quality";

/** Minimum height of the chat input panel in pixels */
const CHAT_INPUT_PANEL_MIN_HEIGHT_PX = 144;

/** Maximum height of the chat input panel in pixels */
const CHAT_INPUT_PANEL_MAX_HEIGHT_PX = 520;

/**
 * Maximum height ratio of the input panel relative to the chat panel.
 * Keeps the layout balanced by limiting input panel size.
 */
const CHAT_INPUT_PANEL_MAX_RATIO = 0.45;

/** Minimum height reserved for the messages list in pixels */
const CHAT_INPUT_PANEL_MIN_MESSAGES_PX = 160;

const isNearBottom = (el: HTMLElement, thresholdPx = 48) => {
  return el.scrollHeight - el.scrollTop - el.clientHeight < thresholdPx;
};

type SuggestionDisplayState = "loading" | "ready" | "fallback";

interface SuggestionCachePayload {
  suggestions: string[];
  updatedAt: number;
}

const extractCompletedToolResults = (segments: MessageSegment[]): Message["toolResults"] | undefined => {
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

const parseMessageFeedbackFromMetadata = (metadataRaw?: string | null): Message["feedback"] | undefined => {
  if (!metadataRaw) return undefined;

  try {
    const parsed = JSON.parse(metadataRaw) as { feedback?: unknown };
    const feedback = parsed.feedback;
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
  } catch {
    return undefined;
  }
};

const normalizeFeedbackFromResponse = (
  feedback: MessageFeedbackData | undefined,
  fallbackVote: MessageFeedbackVote,
  fallbackUpdatedAt: string,
): Message["feedback"] => {
  return {
    vote: feedback?.vote ?? fallbackVote,
    preset: feedback?.preset ?? null,
    comment: feedback?.comment ?? null,
    updated_at: feedback?.updated_at ?? fallbackUpdatedAt,
  };
};

const normalizeMessageContent = (content: string): string => {
  return content.replace(/\s+/g, " ").trim();
};

const findUnassignedAssistantMessage = (
  recentMessages: ChatMessage[],
  assignedBackendIds: Set<string>,
  expectedContent: string,
  expectedSessionId?: string | null,
): ChatMessage | null => {
  const candidates = recentMessages.filter(
    (message) =>
      message.role === "assistant"
      && !assignedBackendIds.has(message.id)
      && (!expectedSessionId || message.session_id === expectedSessionId),
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


/**
 * AI chat interface component providing the main conversation experience.
 *
 * This component serves as the UI layer for the chat interface, coordinating:
 * - Message display and history management
 * - User input handling with draft persistence
 * - Context assembly from file attachments and text quotes
 * - Resizable input panel with persisted height
 *
 * Streaming logic is delegated to specialized hooks:
 * - {@link useChatStreaming} - Manages streaming UI state (render items, edit progress, suggestions)
 * - {@link useAgentStream} - Handles SSE connection and event processing
 *
 * The component wires these hooks together, passing callbacks from useChatStreaming
 * to useAgentStream to handle streaming events with proper state updates.
 *
 * @returns React element containing the complete chat interface
 *
 * @example
 * // The ChatPanel is typically rendered in the main layout:
 * <ChatPanel />
 */
const ChatPanelComponent: React.FC<ChatPanelProps> = () => {
  const { t } = useTranslation(['chat', 'common', 'dashboard', 'home', 'editor', 'versions']);
  const chatQuotaUpgradePrompt = getUpgradePromptDefinition("chat_quota_blocked");
  const fileVersionUpgradePrompt = getUpgradePromptDefinition("file_version_quota_blocked");

  // Clamp utility function
  const clampNumber = useMemo(() => (value: number, min: number, max: number): number => {
    return Math.min(max, Math.max(min, value));
  }, []);
  const {
    currentProjectId,
    selectedItem,
    triggerFileTreeRefresh,
    triggerEditorRefresh,
    setAiEditingFileId,
    setSelectedItem,
    appendFileContent,
    finishFileStreaming,
    startFileStreaming,
    streamingFileId,
    enterDiffReview,
    refreshProject,
  } =
    useProject();
  const queryClient = useQueryClient();
  // Same query (and cache) as the header's QuotaBadge, which also polls it.
  const { data: quota } = useQuery({
    queryKey: subscriptionQueryKeys.quota(),
    queryFn: () => subscriptionApi.getQuota(),
  });
  const aiMessageQuota = quota?.ai_conversations;
  const quotaExhausted = Boolean(
    aiMessageQuota && aiMessageQuota.limit !== -1 && aiMessageQuota.used >= aiMessageQuota.limit,
  );
  /** Refresh the quota pill (and other quota readers) after the server charged or refunded. */
  const invalidateQuota = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: subscriptionQueryKeys.quota() });
    void queryClient.invalidateQueries({ queryKey: subscriptionQueryKeys.quotaLite() });
  }, [queryClient]);
  const { isMobile } = useMobileLayout();
  const { attachedFileIds, attachedLibraryMaterials, clearMaterials } = useMaterialAttachment();
  const { quotes, clearQuotes } = useTextQuote();

  // Use ref to track latest streamingFileId for use in callbacks
  const streamingFileIdRef = useRef<string | null>(streamingFileId);
  useEffect(() => {
    streamingFileIdRef.current = streamingFileId;
  }, [streamingFileId]);
  const selectedItemRef = useRef(selectedItem);
  useEffect(() => {
    selectedItemRef.current = selectedItem;
  }, [selectedItem]);

  const [messages, setMessages] = useState<Message[]>([]);
  const [feedbackPendingMessageId, setFeedbackPendingMessageId] = useState<string | null>(null);
  const [showQuotaUpgradeModal, setShowQuotaUpgradeModal] = useState(false);
  const [showFileVersionUpgradeModal, setShowFileVersionUpgradeModal] = useState(false);
  // 后端确实退还了这一轮的 AI 消息时才有值（quota_refunded 帧），按项目隔离，下一轮开始时清空。
  const [quotaRefund, setQuotaRefund] = useState<{ projectId: string; kind: QuotaRefundKind } | null>(null);
  const streamActivity = useStreamActivity();
  const pendingMaterialClearRef = useRef(false);
  const pendingQuoteClearRef = useRef(false);
  const currentAgentSessionIdRef = useRef<string | null>(null);
  /** The author's own wording for the current round (used for the auto snapshot description). */
  const lastUserRequestRef = useRef<string | null>(null);
  const [pendingUndoTarget, setPendingUndoTarget] = useState<FileEditUndoTarget | null>(null);
  const [isUndoing, setIsUndoing] = useState(false);
  const lastRetryRequestRef = useRef<{
    projectId: string;
    request: Omit<AgentRequest, "project_id">;
  } | null>(null);


  // Use the useChatStreaming hook for streaming UI state management
  const {
    streamRenderItems,
    getStreamItemsSnapshot,
    clearStreamItems,
    editProgress,
    setEditProgress,
    matchedSkills,
    setMatchedSkills,
    getStreamCallbacks,
  } = useChatStreaming();
  const [suggestionDisplayState, setSuggestionDisplayState] = useState<SuggestionDisplayState>("loading");
  const suggestionRequestSeqRef = useRef(0);
  const [aiSuggestions, setAiSuggestions] = useState<string[]>([]);
  const [isRefreshingSuggestions, setIsRefreshingSuggestions] = useState(false);
  const aiSuggestionsRef = useRef<string[]>([]);

  const contextItemsRef = useRef<AgentContextItem[]>([]);
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);
  const [showAIMemory, setShowAIMemory] = useState(false);
  const [generationMode, setGenerationMode] = useState<GenerationMode>("quality");
  const { draft, saveDraft, clearDraft } = useDraftPersistence(currentProjectId);
  const messageListRef = useRef<MessageListRef>(null);
  const messagesScrollContainerRef = useRef<HTMLDivElement>(null);
  const shouldAutoScrollRef = useRef(true);
  const [showJumpToLatest, setShowJumpToLatest] = useState(false);
  const autoScrollFrameRef = useRef<number | null>(null);
  const lastLoadedProjectRef = useRef<string | null>(null);
  const historyRequestSeqRef = useRef(0);
  const newSessionRequestSeqRef = useRef(0);

  useEffect(() => {
    lastRetryRequestRef.current = null;
    lastUserRequestRef.current = null;
  }, [currentProjectId]);

  // Persist generation mode per project in localStorage.
  useEffect(() => {
    if (!currentProjectId) return;
    const key = `${GENERATION_MODE_STORAGE_KEY_PREFIX}${currentProjectId}`;
    const stored = (localStorage.getItem(key) || "").trim();
    if (stored === "fast" || stored === "quality") {
      setGenerationMode(stored);
    } else {
      setGenerationMode("quality");
    }
  }, [currentProjectId]);

  const handleGenerationModeChange = useCallback((mode: GenerationMode) => {
    if (mode === generationMode) return;

    setGenerationMode(mode);
    if (!currentProjectId) return;
    const key = `${GENERATION_MODE_STORAGE_KEY_PREFIX}${currentProjectId}`;
    localStorage.setItem(key, mode);
    toast.success(
      t(mode === "fast" ? "chat:input.mode.switchedFast" : "chat:input.mode.switchedQuality"),
    );
  }, [currentProjectId, generationMode, t]);

  const currentProjectIdRef = useRef<string | null>(currentProjectId);
  // "Write chapter 1" once a framework exists but no prose yet; refreshed on open and after each round.
  const [nextStep, setNextStep] = useState<{ projectId: string; step: ProjectNextStep } | null>(null);
  const [dismissedNextStepProjects, setDismissedNextStepProjects] = useState<ReadonlySet<string>>(() => new Set());
  const refreshNextStep = useCallback(async (projectId: string) => {
    try {
      const { next_step: step } = await projectApi.getNextStep(projectId);
      if (currentProjectIdRef.current !== projectId) return;
      setNextStep(step ? { projectId, step } : null);
    } catch (error) {
      logger.warn("Failed to load the project's next step:", error);
    }
  }, []);
  useEffect(() => {
    currentProjectIdRef.current = currentProjectId;
    return () => {
      currentProjectIdRef.current = null;
      newSessionRequestSeqRef.current += 1;
    };
  }, [currentProjectId]);

  const chatPanelRef = useRef<HTMLDivElement>(null);
  const headerRef = useRef<HTMLDivElement>(null);
  const inputPanelRef = useRef<HTMLDivElement>(null);
  const resizeDragRef = useRef<{ startY: number; startHeight: number } | null>(null);
  const [inputPanelHeight, setInputPanelHeight] = useState<number | null>(() => {
    const saved = localStorage.getItem(CHAT_INPUT_PANEL_HEIGHT_KEY);
    const height = saved ? Number(saved) : Number.NaN;
    if (!Number.isFinite(height)) return null;
    return clampNumber(height, CHAT_INPUT_PANEL_MIN_HEIGHT_PX, CHAT_INPUT_PANEL_MAX_HEIGHT_PX);
  });
  const latestInputPanelHeightRef = useRef<number | null>(inputPanelHeight);
  useEffect(() => {
    latestInputPanelHeightRef.current = inputPanelHeight;
  }, [inputPanelHeight]);
  const suggestionTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const messagesRef = useRef<Message[]>(messages);  // Track latest messages for callbacks

  /**
   * 最近一次自动请求建议时的会话位置（项目 + 消息数 + 末条消息 id）。
   * 每轮对话结束只自动请求一次：同一位置重复触发（例如 onComplete 被调用两次）
   * 直接跳过，不重复打 /suggest。
   */
  const lastAutoSuggestionKeyRef = useRef<string | null>(null);

  // Keep messagesRef in sync
  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  useEffect(() => {
    aiSuggestionsRef.current = aiSuggestions;
  }, [aiSuggestions]);

  // 🔥 防止 onComplete 重复调用
  const onCompleteCalledRef = useRef<boolean>(false);
  const resetOnCompleteFlag = useCallback(() => {
    onCompleteCalledRef.current = false;
  }, []);

  // Terminal status cards emitted during the current stream (e.g. clarification needed).
  // These should be persisted into the final assistant message so they render as part of it,
  // rather than as a separate timestamped "message".
  const terminalStatusCardsRef = useRef<MessageStatusCard[]>([]);

  // Get stream callbacks from useChatStreaming hook with dependencies
  const streamCallbacks = useMemo(() => {
    return getStreamCallbacks({
      triggerFileTreeRefresh,
      triggerEditorRefresh,
      setAiEditingFileId,
      setSelectedItem,
      getCurrentSelectedItem: () => selectedItemRef.current,
      appendFileContent,
      finishFileStreaming,
      startFileStreaming,
      streamingFileId,
      getCurrentStreamingFileId: () => streamingFileIdRef.current,
      enterDiffReview,
      activeProjectId: currentProjectId,
      createSnapshot: (projectId, options) =>
        versionApi.createSnapshot(projectId, {
          description: options?.description,
          snapshotType: options?.snapshotType,
        }),
      getLatestUserRequest: () => lastUserRequestRef.current,
      // update_project 报告服务端给默认名项目改了名：只重读这一个项目，顶栏换上新名字。
      // 不走 refreshProjects——它会打开全局 loading，整个工作台（含本面板）被换成加载页。
      onProjectRenamed: (projectId) => {
        void refreshProject(projectId);
      },
      t: (key, options) => t(key as string, options as Record<string, unknown>),
    });
  }, [
    getStreamCallbacks,
    triggerFileTreeRefresh,
    triggerEditorRefresh,
    setAiEditingFileId,
    setSelectedItem,
    appendFileContent,
    finishFileStreaming,
    startFileStreaming,
    streamingFileId,
    enterDiffReview,
    currentProjectId,
    refreshProject,
    t,
  ]);

  const toRecentMessages = useCallback((sourceMessages: Message[]) => {
    return sourceMessages.slice(-5).map((m) => ({
      role: m.role,
      content: m.content,
    }));
  }, []);

  const getSuggestionCacheKey = useCallback((projectId: string): string => {
    return `${SUGGESTION_CACHE_KEY_PREFIX}${projectId}`;
  }, []);

  const readCachedSuggestions = useCallback((projectId: string): string[] => {
    try {
      const raw = localStorage.getItem(getSuggestionCacheKey(projectId));
      if (!raw) return [];
      const parsed = JSON.parse(raw) as SuggestionCachePayload;
      if (!Array.isArray(parsed.suggestions) || typeof parsed.updatedAt !== "number") {
        return [];
      }
      if (Date.now() - parsed.updatedAt > SUGGESTION_CACHE_TTL_MS) {
        localStorage.removeItem(getSuggestionCacheKey(projectId));
        return [];
      }
      return parsed.suggestions.filter((s): s is string => typeof s === "string" && Boolean(s.trim())).slice(0, 3);
    } catch {
      return [];
    }
  }, [getSuggestionCacheKey]);

  const writeSuggestionCache = useCallback((projectId: string, suggestions: string[]) => {
    try {
      const payload: SuggestionCachePayload = {
        suggestions: suggestions.slice(0, 3),
        updatedAt: Date.now(),
      };
      localStorage.setItem(getSuggestionCacheKey(projectId), JSON.stringify(payload));
    } catch {
      // Ignore cache write failures (non-critical UX optimization)
    }
  }, [getSuggestionCacheKey]);

  const fetchAndApplySuggestions = useCallback(async (
    projectId: string,
    sourceMessages: Message[],
    options?: { allowFallback?: boolean }
  ): Promise<string[]> => {
    const suggestions = await fetchSuggestions(projectId, toRecentMessages(sourceMessages), 3);

    // Ignore stale responses from previous projects/requests.
    if (currentProjectIdRef.current !== projectId) {
      return [];
    }

    if (suggestions.length > 0) {
      setAiSuggestions(suggestions);
      setSuggestionDisplayState("ready");
      writeSuggestionCache(projectId, suggestions);
      return suggestions;
    }

    if (options?.allowFallback && aiSuggestionsRef.current.length === 0) {
      setSuggestionDisplayState("fallback");
    }
    return [];
  }, [setAiSuggestions, toRecentMessages, writeSuggestionCache]);

  const requestInitialSuggestions = useCallback(async (
    projectId: string,
    sourceMessages: Message[],
  ) => {
    suggestionRequestSeqRef.current += 1;
    const requestSeq = suggestionRequestSeqRef.current;
    const cached = readCachedSuggestions(projectId);

    if (cached.length > 0) {
      // 有缓存就直接用，不再后台刷新：打开/切换项目不该凭空多打一次 /suggest。
      // 下一轮对话结束后会按新上下文自动刷新一次。
      setAiSuggestions(cached);
      setSuggestionDisplayState("ready");
      return;
    }

    setSuggestionDisplayState("loading");
    const fetchPromise = fetchAndApplySuggestions(projectId, sourceMessages);

    const firstResult = await Promise.race([
      fetchPromise.then((suggestions) => ({ timedOut: false, suggestions })),
      new Promise<{ timedOut: true; suggestions: string[] }>((resolve) => {
        setTimeout(() => resolve({ timedOut: true, suggestions: [] }), SUGGESTION_INITIAL_TIMEOUT_MS);
      }),
    ]);

    if (currentProjectIdRef.current !== projectId || requestSeq !== suggestionRequestSeqRef.current) {
      return;
    }

    if (firstResult.timedOut) {
      if (aiSuggestionsRef.current.length === 0) {
        setSuggestionDisplayState("fallback");
      }
      const eventual = await fetchPromise;
      if (currentProjectIdRef.current !== projectId || requestSeq !== suggestionRequestSeqRef.current) {
        return;
      }
      if (eventual.length === 0 && aiSuggestionsRef.current.length === 0) {
        setSuggestionDisplayState("fallback");
      }
      return;
    }

    if (firstResult.suggestions.length === 0 && aiSuggestionsRef.current.length === 0) {
      setSuggestionDisplayState("fallback");
    }
  }, [fetchAndApplySuggestions, readCachedSuggestions, setAiSuggestions]);

  const finalizePendingContextClears = useCallback(() => {
    if (pendingMaterialClearRef.current) {
      clearMaterials();
      pendingMaterialClearRef.current = false;
    }
    if (pendingQuoteClearRef.current) {
      clearQuotes();
      pendingQuoteClearRef.current = false;
    }
  }, [clearMaterials, clearQuotes]);

  const resetPendingContextClears = useCallback(() => {
    pendingMaterialClearRef.current = false;
    pendingQuoteClearRef.current = false;
  }, []);

  const hydrateAssistantBackendMessage = useCallback(async (
    projectId: string,
    localMessageId: string,
    expectedContent: string,
    options?: {
      assistantMessageId?: string;
      expectedSessionId?: string | null;
    },
  ) => {
    if (currentProjectIdRef.current !== projectId) {
      return;
    }

    try {
      const recentMessages = await getRecentMessages(projectId, 50);
      if (currentProjectIdRef.current !== projectId) {
        return;
      }

      setMessages((prev) => {
        const localMessage = prev.find((item) => item.id === localMessageId);
        if (
          !localMessage
          || (
            localMessage.backendMessageId
            && localMessage.backendMessageId !== options?.assistantMessageId
          )
        ) {
          return prev;
        }

        const assignedBackendIds = new Set(
          prev
            .filter((item) => item.id !== localMessageId)
            .map((item) => item.backendMessageId)
            .filter((id): id is string => Boolean(id)),
        );

        const matchedMessage = options?.assistantMessageId
          ? recentMessages.find(
            (message) =>
              message.id === options.assistantMessageId
              && message.role === "assistant"
              && !assignedBackendIds.has(message.id),
          ) ?? null
          : findUnassignedAssistantMessage(
            recentMessages,
            assignedBackendIds,
            expectedContent,
            options?.expectedSessionId,
          );
        if (!matchedMessage) {
          return prev;
        }

        const matchedFeedback = parseMessageFeedbackFromMetadata(matchedMessage.metadata);

        return prev.map((item) =>
          item.id === localMessageId
            ? {
              ...item,
              backendMessageId: matchedMessage.id,
              feedback: matchedFeedback ?? item.feedback,
              timestamp: parseUTCDate(matchedMessage.created_at),
            }
            : item,
        );
      });
    } catch (error) {
      logger.error("Failed to hydrate assistant backend message:", error);
    }
  }, []);

  /**
   * Handles completion of an AI response stream.
   * Adds message to the messages array and fetches suggestions.
   * The hook's onComplete handles the rest (snapshot creation, cleanup, etc.).
   * Protected against duplicate calls with a ref flag.
   *
   * @param completedSegments - Array of message segments from the completed stream
   * @param applyAction - Optional apply action from the stream
   */
  const onCompleteHandler = useCallback(async (
    completedSegments: MessageSegment[],
    applyAction: unknown,
    completionMeta?: StreamCompletionMeta,
  ) => {
    // 防止重复调用
    if (onCompleteCalledRef.current) {
      return;
    }
    onCompleteCalledRef.current = true;

    // 【关键修复】将流式内容持久化到 messages，防止下一轮对话时丢失
    const accumulatedContent = completedSegments
      .filter(s => s.type === 'content')
      .map(s => s.content || '')
      .filter(c => c.trim())  // 过滤空内容
      .join('\n\n');  // 用换行分隔不同的 content segment

    // Persist terminal workflow status cards (e.g. clarification needed) into the final assistant
    // message so they render as part of that message (no extra timestamped "message" row).
    const statusCards = terminalStatusCardsRef.current;
    terminalStatusCardsRef.current = [];
    const toolResults = extractCompletedToolResults(completedSegments);
    // React state can still be throttled when the final SSE event arrives.
    const displayItems = getStreamItemsSnapshot?.() ?? [];


    // 添加到 messages（正文、状态卡片、已完成的工具结果都会随消息一起保留）
    if (accumulatedContent.trim() || statusCards.length > 0 || toolResults?.length || displayItems.length) {
      const aiMessage: Message = {
        id: `ai-${Date.now()}-${Math.random().toString(36).substring(2, 11)}`,
        backendMessageId: completionMeta?.assistantMessageId,
        role: 'assistant',
        content: accumulatedContent,
        timestamp: new Date(),
        statusCards: statusCards.length > 0 ? statusCards : undefined,
        toolResults,
        displayItems: displayItems.length ? displayItems : undefined,
      };
      setMessages(prev => [...prev, aiMessage]);

      if (currentProjectId) {
        void hydrateAssistantBackendMessage(currentProjectId, aiMessage.id, accumulatedContent, {
          assistantMessageId: completionMeta?.assistantMessageId,
          expectedSessionId: completionMeta?.sessionId ?? currentAgentSessionIdRef.current,
        });
      }
    }

    finalizePendingContextClears();
    invalidateQuota();
    if (currentProjectId) void refreshNextStep(currentProjectId);

    // Let the streaming hook finalize cleanup/snapshot work in the background.
    void streamCallbacks.onComplete(
      completedSegments,
      applyAction as ApplyAction | null,
      completionMeta,
    );

    // Delay and request fresh project-aware suggestions (once per completed turn).
    if (suggestionTimeoutRef.current) {
      clearTimeout(suggestionTimeoutRef.current);
    }
    setSuggestionDisplayState("loading");
    suggestionTimeoutRef.current = setTimeout(async () => {
      const currentMessages = messagesRef.current;  // Use ref to get latest messages
      if (currentProjectId) {
        const lastMessage = currentMessages[currentMessages.length - 1];
        const suggestionKey = `${currentProjectId}:${currentMessages.length}:${lastMessage?.id ?? ""}`;
        if (lastAutoSuggestionKeyRef.current === suggestionKey) {
          if (aiSuggestionsRef.current.length > 0) {
            setSuggestionDisplayState("ready");
          } else {
            setSuggestionDisplayState("fallback");
          }
          return;
        }
        lastAutoSuggestionKeyRef.current = suggestionKey;
        try {
          await fetchAndApplySuggestions(currentProjectId, currentMessages, { allowFallback: true });
        } catch (error) {
          // Silent failure - suggestion is non-critical
          logger.error("Failed to fetch suggestions:", error);
          if (aiSuggestionsRef.current.length === 0) {
            setSuggestionDisplayState("fallback");
          }
        }
      }
    }, 1500);
  }, [
    streamCallbacks,
    getStreamItemsSnapshot,
    currentProjectId,
    fetchAndApplySuggestions,
    finalizePendingContextClears,
    hydrateAssistantBackendMessage,
    invalidateQuota,
    refreshNextStep,
  ]);

  // Agent stream hook - uses callbacks from useChatStreaming hook
  const {
    state,
    startStream,
    stop,
    isStopping,
    reset,
    isStreaming,
    isThinking,
    thinkingContent,
    conflicts,
    error,
    errorCode,
    retryable,
    sessionId,
    sendSteeringMessage,
  } = useAgentStream(currentProjectId!, {
    // Lifecycle callbacks
    onStart: () => {
      streamCallbacks.onStart();
      streamActivity.begin();
      terminalStatusCardsRef.current = [];
      setQuotaRefund(null);
      resetOnCompleteFlag();
    },

    // Context and Thinking callbacks
    onContext: (items) => {
      contextItemsRef.current = items || [];
      streamCallbacks.onContext(items);
    },

    onThinking: streamCallbacks.onThinking,
    onThinkingContent: streamCallbacks.onThinkingContent,

    // Segment callbacks
    onSegmentStart: (segment) => {
      streamActivity.onSegmentStart(segment);
      streamCallbacks.onSegmentStart(segment);
    },
    onSegmentUpdate: streamCallbacks.onSegmentUpdate,
    onSegmentUpdateToolCalls: streamCallbacks.onSegmentUpdateToolCalls,
    onSegmentEnd: streamCallbacks.onSegmentEnd,

    // Completion and error callbacks
    onComplete: onCompleteHandler,
    onError: (message, code, retryable) => {
      streamCallbacks.onError(message, code, retryable);
      invalidateQuota();
    },

    // Tool result callbacks
    onToolResult: streamCallbacks.onToolResult,

    // File operation callbacks
    onFileCreated: (fileId, fileType, title) => {
      streamActivity.onFileCreated(title);
      streamCallbacks.onFileCreated(fileId, fileType, title);
    },
    onFileContent: streamCallbacks.onFileContent,
    onFileContentEnd: streamCallbacks.onFileContentEnd,
    onFileEditStart: (fileId, title, totalEdits, fileType) => {
      streamActivity.onFileEditStart(title);
      streamCallbacks.onFileEditStart(fileId, title, totalEdits, fileType);
    },
    onFileEditApplied: streamCallbacks.onFileEditApplied,
    onFileEditEnd: streamCallbacks.onFileEditEnd,

    // Skill matching callbacks
    onSkillMatched: streamCallbacks.onSkillMatched,
    onSkillsMatched: streamCallbacks.onSkillsMatched,

    // Multi-agent workflow callbacks
    onAgentSelected: (agentType, agentName, iteration, maxIterations, remaining) => {
      streamActivity.onAgentSelected(agentType);
      streamCallbacks.onAgentSelected(agentType, agentName, iteration, maxIterations, remaining);
    },
    onToolCall: streamActivity.onToolCall,
    onIterationExhausted: (layer, iterationsUsed, maxIterations, reason, lastAgent) => {
      terminalStatusCardsRef.current = [{
        type: "iteration_exhausted",
        layer,
        iterationsUsed,
        maxIterations,
        reason,
        lastAgent,
      }];
      streamCallbacks.onIterationExhausted(layer, iterationsUsed, maxIterations, reason, lastAgent);
    },
    onRouterThinking: streamCallbacks.onRouterThinking,
    onRouterDecided: streamCallbacks.onRouterDecided,
    onHandoff: streamCallbacks.onHandoff,
    onWorkflowStopped: (data: SSEWorkflowStoppedData) => {
      terminalStatusCardsRef.current = [{
        type: "workflow_stopped",
        reason: data.reason,
        agentType: data.agent_type,
        message: data.message,
        question: data.question,
        context: data.context,
        details: data.details,
        confidence: data.confidence,
        evaluation: data.evaluation,
      }];
      streamCallbacks.onWorkflowStopped(data);
    },
    onWorkflowComplete: streamCallbacks.onWorkflowComplete,
    onSessionStarted: (sessionId) => {
      currentAgentSessionIdRef.current = sessionId;
      streamCallbacks.onSessionStarted(sessionId);
      // The server has already charged this round's AI message by now.
      invalidateQuota();
    },
    onParallelStart: streamCallbacks.onParallelStart,
    onParallelTaskStart: streamCallbacks.onParallelTaskStart,
    onParallelTaskEnd: streamCallbacks.onParallelTaskEnd,
    onParallelEnd: streamCallbacks.onParallelEnd,
    onSteeringReceived: streamCallbacks.onSteeringReceived,
    onQuotaRefunded: (kind) => {
      if (currentProjectId) setQuotaRefund({ projectId: currentProjectId, kind });
      invalidateQuota();
    },
  });
  // Stopping already ends the round on its own; no need to ask before leaving then.
  const leaveGuard = useLeaveWhileGenerating(isStreaming && !isStopping);
  const conflictCount = state.conflicts?.length ?? 0;
  const streamRenderItemCount = streamRenderItems?.length ?? 0;

  // 成本兜底与每日条数共用同一套标题和说明：不向作者透露第二个额度。
  const isAiQuotaLimit =
    errorCode === 'ERR_QUOTA_AI_CONVERSATIONS_EXCEEDED'
    || errorCode === 'ERR_QUOTA_AI_DAILY_COST_EXCEEDED';
  // Never pair a refund note with the used-up card: that would hint at a second, cost-based limit.
  const quotaRefundNote = quotaRefund && quotaRefund.projectId === currentProjectId && !isStreaming && !isAiQuotaLimit
    ? t(
      quotaRefund.kind === 'no_progress'
        ? 'chat:panel.notCharged'
        : quotaRefund.kind === 'stopped'
          ? 'chat:panel.notChargedStopped'
          : 'chat:panel.notChargedError',
    )
    : null;
  // Stopped before anything was written: offer to send the same request again.
  const resendAfterStop = quotaRefundNote && quotaRefund?.kind === 'stopped'
    ? [...messages].reverse().find((message) => message.role === 'user')?.content ?? null
    : null;

  useEffect(() => {
    if (isAiQuotaLimit && chatQuotaUpgradePrompt.surface === "modal") {
      setShowQuotaUpgradeModal(true);
    }
  }, [chatQuotaUpgradePrompt.surface, isAiQuotaLimit]);

  /**
   * Loads chat history for a specific project from the server.
   * Converts backend message format to frontend Message type, including
   * parsing and transforming tool_calls data.
   *
   * @param projectId - The ID of the project to load chat history for
   */
  // Load chat history when project changes
  const loadChatHistory = useCallback(async (projectId: string): Promise<Message[] | null> => {
    const requestId = ++historyRequestSeqRef.current;
    setIsLoadingHistory(true);
    try {
      const historyMessages = await getRecentMessages(projectId, 50);

      // A newer history load or new session supersedes this response, even
      // when the active project ID has returned to the same value.
      if (currentProjectIdRef.current !== projectId || requestId !== historyRequestSeqRef.current) {
        return null;
      }

      // Convert to Message format
      const loadedMessages: Message[] = historyMessages.map((msg) => {
        const feedback = parseMessageFeedbackFromMetadata(msg.metadata);
        const statusCards = parseMessageStatusCardsFromMetadata(msg.metadata);
        const { toolCalls, toolResults, orderedToolCalls } = parseToolCallsFromHistory(msg.tool_calls);

        return {
          id: msg.id,
          backendMessageId: msg.id,
          role: msg.role as "user" | "assistant",
          content: msg.content,
          timestamp: parseUTCDate(msg.created_at),
          feedback,
          statusCards,
          toolCalls,
          toolResults,
          displayItems: parseChatDisplayEvents(msg.metadata, orderedToolCalls ?? [], parseUTCDate(msg.created_at), t),
        };
      });

      setMessages(loadedMessages);
      return loadedMessages;
    } catch (err) {
      logger.error("Failed to load chat history:", err);
      // Don't clear messages or request suggestions for a superseded load.
      if (currentProjectIdRef.current !== projectId || requestId !== historyRequestSeqRef.current) {
        return null;
      }
      // Don't show error to user, just start fresh
      setMessages([]);
      setFeedbackPendingMessageId(null);
      return [];
    } finally {
      if (requestId === historyRequestSeqRef.current && currentProjectIdRef.current === projectId) {
        setIsLoadingHistory(false);
      }
    }
  }, [t]);

  const handleSubmitFeedback = useCallback(async (message: Message, vote: MessageFeedbackVote) => {
    const backendMessageId = message.backendMessageId ?? null;
    if (!backendMessageId) {
      toast.error(t("chat:feedback.unsupported"));
      return;
    }

    setFeedbackPendingMessageId(message.id);
    try {
      const response = await submitMessageFeedback(backendMessageId, { vote });
      const normalizedFeedback = normalizeFeedbackFromResponse(response.feedback, vote, response.updated_at);

      setMessages((prev) =>
        prev.map((item) =>
          item.id === message.id
            ? {
              ...item,
              backendMessageId,
              feedback: normalizedFeedback,
            }
            : item,
        ),
      );
    } catch (error) {
      logger.error("Failed to submit message feedback:", error);
      toast.error(t("chat:feedback.submitFailed"));
    } finally {
      setFeedbackPendingMessageId(null);
    }
  }, [t]);

  // Load history when project changes
  useEffect(() => {
    if (!currentProjectId) {
      return;
    }

    // Skip if already loaded this project (prevents duplicate requests)
    if (lastLoadedProjectRef.current === currentProjectId) {
      return;
    }

    // Mark as loaded to prevent duplicate requests
    lastLoadedProjectRef.current = currentProjectId;

    // Reset chat UI state
    setMessages([]);
    setFeedbackPendingMessageId(null);
    setEditProgress(null);
    // 旧项目的流式渲染残留（半截正文/工具卡片/技能芯片/冲突）不能带进新项目会话：
    // 旧流已被 useAgentStream 的项目切换 effect 中止，其 onComplete 永远不会执行，
    // 所以必须在这里主动清空。
    clearStreamItems();
    setMatchedSkills([]);
    reset();
    setAiSuggestions([]);
    setSuggestionDisplayState("loading");
    contextItemsRef.current = [];

    setNextStep(null);
    void refreshNextStep(currentProjectId);

    // Load chat history first, then request project suggestions immediately.
    void (async () => {
      const loadedMessages = await loadChatHistory(currentProjectId);
      if (loadedMessages === null) return;
      await requestInitialSuggestions(currentProjectId, loadedMessages);
    })();
  }, [currentProjectId, loadChatHistory, requestInitialSuggestions, refreshNextStep, setAiSuggestions, setEditProgress, clearStreamItems, setMatchedSkills, reset]); // 依赖 loadChatHistory

  // Auto-scroll to bottom when new messages arrive or streaming content meaningfully changes.
  // Coalesce scrolls in a single RAF to avoid completion-time jitter from multiple back-to-back
  // state commits (final message append, stream cleanup, backend hydration).
  useEffect(() => {
    if (shouldAutoScrollRef.current) {
      if (autoScrollFrameRef.current !== null) {
        cancelAnimationFrame(autoScrollFrameRef.current);
      }
      autoScrollFrameRef.current = requestAnimationFrame(() => {
        autoScrollFrameRef.current = null;
        messageListRef.current?.scrollToBottom(false);
      });
      return;
    }
    setShowJumpToLatest(true);
  }, [
    messages.length,
    state.content,
    thinkingContent,
    conflictCount,
    streamRenderItemCount,
    isStreaming,
    isThinking,
  ]);

  useEffect(() => {
    return () => {
      if (autoScrollFrameRef.current !== null) {
        cancelAnimationFrame(autoScrollFrameRef.current);
      }
    };
  }, []);

  useEffect(() => {
    const scrollEl = messagesScrollContainerRef.current;
    if (!scrollEl) {
      return;
    }

    const handleScroll = () => {
      const nearBottom = isNearBottom(scrollEl);
      shouldAutoScrollRef.current = nearBottom;
      if (nearBottom) {
        setShowJumpToLatest(false);
      }
    };

    scrollEl.addEventListener("scroll", handleScroll, { passive: true });
    handleScroll();
    return () => {
      scrollEl.removeEventListener("scroll", handleScroll);
    };
  }, [currentProjectId]);

  const handleJumpToLatest = useCallback(() => {
    shouldAutoScrollRef.current = true;
    setShowJumpToLatest(false);
    messageListRef.current?.scrollToBottom(true);
  }, []);

  /**
   * The author just sent (or re-sent) something: follow the conversation again
   * even if they had scrolled up to read earlier messages.
   */
  const followLatestMessage = useCallback(() => {
    shouldAutoScrollRef.current = true;
    setShowJumpToLatest(false);
    if (autoScrollFrameRef.current !== null) {
      cancelAnimationFrame(autoScrollFrameRef.current);
    }
    autoScrollFrameRef.current = requestAnimationFrame(() => {
      autoScrollFrameRef.current = null;
      messageListRef.current?.scrollToBottom(false);
    });
  }, []);

  /**
   * Handles sending a user message to the AI agent.
   * Creates a user message, adds it to the chat, constructs the agent request
   * with context (selected file, attachments, quotes), and initiates streaming.
   * Clears draft and attachments after sending.
   *
   * Every send entry (input box, suggestion chips via the input, the dashboard idea
   * auto-send) goes through here, so this is also where the view jumps back to the
   * latest message.
   *
   * @param message - The user's message text to send (shown and stored as-is)
   * @param selectedSkillIds - Skills explicitly selected via chips (sent as selected_skill_ids)
   * @param extraMetadata - Extra request metadata (e.g. `{ entry: "dashboard_idea" }`)
   */
  // Handle sending a message
  const handleSendMessage = useCallback(async (
    message: string,
    selectedSkillIds?: string[],
    extraMetadata?: Record<string, unknown>,
  ) => {
    if (!message.trim() || isStreaming) return;

    // Clear draft after sending
    clearDraft();

    // Clear AI suggestions when user sends a message
    setAiSuggestions([]);
    setSuggestionDisplayState("loading");
    if (suggestionTimeoutRef.current) {
      clearTimeout(suggestionTimeoutRef.current);
    }

    // Add user message to chat (optimistic update)
    const userMessage: Message = {
      id: `user-${Date.now()}`,
      role: "user",
      content: message,
      timestamp: new Date(),
    };

    setMessages((prev) => [...prev, userMessage]);
    lastUserRequestRef.current = message;
    followLatestMessage();

    // Get current editor content as context
    const request: Omit<AgentRequest, "project_id"> = {
      message,
      selected_text: undefined, // Editor selection via text quote feature - see TextQuoteContext
      context_before: undefined,
      context_after: undefined,
      outline_id: selectedItem?.id,
      selected_skill_ids: selectedSkillIds && selectedSkillIds.length > 0 ? selectedSkillIds : undefined,
      metadata: {
        generation_mode: generationMode,
        // Current focused file info (used by backend context assembler)
        current_file_id: selectedItem?.id,
        current_file_type: selectedItem?.type,
        current_file_title: selectedItem?.title,

        // Include attached material IDs (project files)
        attached_file_ids: attachedFileIds.length > 0 ? attachedFileIds : undefined,

        // Include attached library materials
        attached_library_materials: attachedLibraryMaterials.length > 0 ? attachedLibraryMaterials : undefined,

        // Include text quotes
        text_quotes: quotes.length > 0 ? quotes.map(q => ({
          text: q.text,
          fileId: q.fileId,
          fileTitle: q.fileTitle,
        })) : undefined,

        ...extraMetadata,
      },
    };

    pendingMaterialClearRef.current = attachedFileIds.length > 0 || attachedLibraryMaterials.length > 0;
    pendingQuoteClearRef.current = quotes.length > 0;

    trackEvent("ai_chat_submitted", {
      project_id: currentProjectId,
      // Where this message came from: suggestion / next_step / dashboard_idea, otherwise typed.
      entry: typeof extraMetadata?.entry === "string" ? extraMetadata.entry : "typed",
      generation_mode: generationMode,
      selected_item_type: selectedItem?.type,
      attached_file_count: attachedFileIds.length,
      attached_library_material_count: attachedLibraryMaterials.length,
      quote_count: quotes.length,
    });

    // Start streaming
    if (currentProjectId) {
      lastRetryRequestRef.current = { projectId: currentProjectId, request };
    }
    startStream(request);
  }, [isStreaming, selectedItem?.id, selectedItem?.type, selectedItem?.title, attachedFileIds, attachedLibraryMaterials, quotes, generationMode, startStream, clearDraft, setAiSuggestions, currentProjectId, followLatestMessage]);

  const handleRetry = useCallback(() => {
    const lastRequest = lastRetryRequestRef.current;
    if (!retryable || isStreaming || !currentProjectId || lastRequest?.projectId !== currentProjectId) {
      return;
    }
    followLatestMessage();
    startStream(lastRequest.request);
  }, [currentProjectId, isStreaming, retryable, startStream, followLatestMessage]);

  /**
   * Send a steering (follow-up) instruction while the agent is generating.
   * The backend queues it and the agent picks it up at its next step, so the
   * toast sets that expectation ("applies at the next step").
   */
  const handleSteer = useCallback(async (message: string) => {
    // 不能静默 return / 静默吞异常：调用方（MessageInput）以「没有抛错 = 已送达」
    // 来决定是否清空输入框，而 setInput("") 会连带把持久化草稿覆写成空串，
    // 用户敲的这段追加指令在 textarea 和 localStorage 里都不复存在。
    try {
      if (!message.trim()) {
        throw new Error("Empty steering message");
      }
      // 流可能在用户按下回车前就结束了（按钮的可见性是上一帧的快照）。
      if (!isStreaming) {
        throw new Error("No active stream for steering");
      }
      await sendSteeringMessage(message);
    } catch (err) {
      logger.error("Failed to send steering message", err);
      toast.error(t("chat:input.steerFailed"));
      // 继续抛出，让输入框保留用户已输入的内容。
      throw err;
    }
    toast.success(t("chat:input.steerSent"));
  }, [isStreaming, sendSteeringMessage, t]);

  const handleIterationAssistAction = useCallback(
    (action: 'continue' | 'split' | 'manual') => {
      if (action === 'continue') {
        saveDraft(t('chat:workflow.actionPrompt.continue'));
        return;
      }
      if (action === 'split') {
        saveDraft(t('chat:workflow.actionPrompt.split'));
        return;
      }
      saveDraft(t('chat:workflow.actionPrompt.manual'));
    },
    [saveDraft, t]
  );

  // Check for inspiration from Dashboard and auto-send
  const inspirationProcessedRef = useRef<Set<string>>(new Set());
  const inspirationSendTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!currentProjectId || isLoadingHistory || isStreaming) return;
    
    // Skip if we've already processed this project's inspiration
    if (inspirationProcessedRef.current.has(currentProjectId)) return;
    
    const inspirationKey = `zenstory_inspiration_${currentProjectId}`;
    const stored = localStorage.getItem(inspirationKey);
    
    if (stored) {
      try {
        const { content, timestamp } = JSON.parse(stored);

        // Only use if recent (within 5 minutes) and content is not empty
        const isRecent = Date.now() - timestamp < 5 * 60 * 1000;

        if (isRecent && typeof content === "string" && content.trim()) {
          // Send the author's idea as-is: the bubble and the stored message are the
          // author's own words. `entry` lets the server add its first-message hint
          // to the model context without persisting it (older servers ignore it).
          const idea = content;

          // Delay slightly to ensure UI is ready. Tracked in a ref so it can be
          // cleared on unmount/deps-change, otherwise a stray auto-send could
          // fire after the user has navigated away.
          // The "processed" mark and localStorage removal are deferred to the
          // timer callback: if this effect re-runs within the 500ms window (e.g.
          // handleSendMessage is recreated), the cleanup clears the pending timer
          // and the re-run must be able to reschedule — marking processed up front
          // would permanently drop the auto-send.
          const projectIdForSend = currentProjectId;
          if (inspirationSendTimerRef.current) {
            clearTimeout(inspirationSendTimerRef.current);
          }
          inspirationSendTimerRef.current = setTimeout(() => {
            inspirationSendTimerRef.current = null;
            inspirationProcessedRef.current.add(projectIdForSend);
            localStorage.removeItem(inspirationKey);
            void handleSendMessage(idea, undefined, { entry: "dashboard_idea" });
          }, 500);
        } else {
          // Clear old or invalid inspiration
          localStorage.removeItem(inspirationKey);
        }
      } catch (e) {
        logger.error("Failed to parse inspiration:", e);
        localStorage.removeItem(inspirationKey);
      }
    }

    return () => {
      if (inspirationSendTimerRef.current) {
        clearTimeout(inspirationSendTimerRef.current);
        inspirationSendTimerRef.current = null;
      }
    };
  }, [currentProjectId, isLoadingHistory, isStreaming, handleSendMessage]);

  /**
   * Starts a new chat session for the current project.
   * Clears existing messages, creates a new session on the server,
   * and resets the agent stream state.
   */
  // Handle new session
  const handleNewSession = async () => {
    if (!currentProjectId) return;
    const projectId = currentProjectId;
    const requestSeq = ++newSessionRequestSeqRef.current;

    resetPendingContextClears();
    currentAgentSessionIdRef.current = null;

    // Clear AI suggestions when starting new session
    setAiSuggestions([]);
    setSuggestionDisplayState("loading");
    if (suggestionTimeoutRef.current) {
      clearTimeout(suggestionTimeoutRef.current);
    }

    try {
      await createNewSession(projectId);
      if (currentProjectIdRef.current !== projectId || requestSeq !== newSessionRequestSeqRef.current) return;
      historyRequestSeqRef.current += 1;
      setIsLoadingHistory(false);
      setMessages([]);
      setFeedbackPendingMessageId(null);
      // 上一轮若被取消/出错，流式渲染残留不会被 onComplete 清理，这里一并清空。
      clearStreamItems();
      setMatchedSkills([]);
      reset();
      setQuotaRefund(null);
      await requestInitialSuggestions(projectId, []);
    } catch (err) {
      logger.error("Failed to create new session:", err);
      if (currentProjectIdRef.current === projectId && requestSeq === newSessionRequestSeqRef.current) {
        setSuggestionDisplayState("fallback");
      }
    }
  };
  
  // Cleanup suggestion timeout on unmount
  useEffect(() => {
    return () => {
      if (suggestionTimeoutRef.current) {
        clearTimeout(suggestionTimeoutRef.current);
      }
    };
  }, []);

  /**
   * Manually refreshes AI suggestions based on recent conversation context.
   * Cancels any pending suggestion timers and fetches new suggestions
   * from the server. Prevents duplicate fetches with loading state check.
   */
  // Manual suggestion refresh (triggered by the refresh button)
  const refreshSuggestionsNow = useCallback(async () => {
    if (!currentProjectId) return;
    if (isRefreshingSuggestions) return;

    // Avoid double-fetch: cancel any pending suggestion timer
    if (suggestionTimeoutRef.current) {
      clearTimeout(suggestionTimeoutRef.current);
      suggestionTimeoutRef.current = null;
    }

    setIsRefreshingSuggestions(true);
    try {
      setSuggestionDisplayState("loading");
      await fetchAndApplySuggestions(currentProjectId, messagesRef.current, { allowFallback: true });
    } catch (error) {
      logger.error("Failed to refresh suggestions:", error);
      if (aiSuggestionsRef.current.length === 0) {
        setSuggestionDisplayState("fallback");
      }
    } finally {
      setIsRefreshingSuggestions(false);
    }
  }, [currentProjectId, isRefreshingSuggestions, setIsRefreshingSuggestions, fetchAndApplySuggestions]);

  // Stop button: the server ends the round on the open stream (see useAgentStream.stop).
  const handleCancel = () => {
    stop();
  };

  /**
   * Handles undoing the exact AI edit represented by a persisted tool result.
   *
   * @param target - Immutable before-version and post-edit concurrency token
   */
  // Handle undo AI edit using the immutable provenance stored with that edit.
  // The click only opens the in-app confirm dialog; the rollback runs on confirm.
  const handleUndo = useCallback((target: FileEditUndoTarget) => {
    setPendingUndoTarget(target);
  }, []);

  const handleCloseUndoDialog = useCallback(() => {
    if (isUndoing) return;
    setPendingUndoTarget(null);
  }, [isUndoing]);

  const handleConfirmUndo = useCallback(async () => {
    const target = pendingUndoTarget;
    if (!target) return;
    setIsUndoing(true);
    try {
      const result = await fileVersionApi.rollback(
        target.fileId,
        target.beforeVersionNumber,
        target.expectedAfterUpdatedAt,
      );
      if (!result.snapshot_created) {
        if (result.version_quota_exceeded) {
          toast.error(t('versions:quota.limitDescription'));
          if (fileVersionUpgradePrompt.surface === "modal") {
            setShowFileVersionUpgradeModal(true);
          }
        } else {
          toast.error(t('versions:rollbackHistoryNotSaved'));
        }
      }
      triggerFileTreeRefresh();
      triggerEditorRefresh(target.fileId);
    } catch (err) {
      if (
        err instanceof ApiError &&
        err.errorCode === "ERR_QUOTA_FILE_VERSIONS_EXCEEDED"
      ) {
        toast.error(handleApiError(err));
        if (fileVersionUpgradePrompt.surface === "modal") {
          setShowFileVersionUpgradeModal(true);
        }
        return;
      }
      logger.error("Failed to undo:", err);
      toast.error(
        err instanceof ApiError
          ? handleApiError(err)
          : t('editor:versionHistory.rollbackFailed'),
      );
    } finally {
      setIsUndoing(false);
      setPendingUndoTarget(null);
    }
  }, [pendingUndoTarget, fileVersionUpgradePrompt.surface, triggerFileTreeRefresh, triggerEditorRefresh, t]);

  /**
   * Calculates the maximum allowed height for the input panel.
   * Considers both fixed pixel limits and layout ratio constraints
   * to maintain a balanced interface.
   *
   * @returns Maximum height in pixels for the input panel
   */
  const getMaxInputPanelHeight = useCallback(() => {
    const rootEl = chatPanelRef.current;
    if (!rootEl) return CHAT_INPUT_PANEL_MAX_HEIGHT_PX;

    const rootHeight = rootEl.getBoundingClientRect().height;
    const headerHeight = headerRef.current?.getBoundingClientRect().height || 48;

    const maxByLayout = rootHeight - headerHeight - CHAT_INPUT_PANEL_MIN_MESSAGES_PX;
    const maxByRatio = rootHeight * CHAT_INPUT_PANEL_MAX_RATIO;

    const max = Math.min(CHAT_INPUT_PANEL_MAX_HEIGHT_PX, maxByLayout, maxByRatio);
    const safeMax = Math.max(max, CHAT_INPUT_PANEL_MIN_HEIGHT_PX);

    return clampNumber(safeMax, CHAT_INPUT_PANEL_MIN_HEIGHT_PX, CHAT_INPUT_PANEL_MAX_HEIGHT_PX);
  }, [clampNumber]);

  const handleInputPanelResizeStart = (e: React.PointerEvent<HTMLDivElement>) => {
    const panelEl = inputPanelRef.current;
    if (!panelEl) return;

    const startHeight = panelEl.getBoundingClientRect().height;
    resizeDragRef.current = { startY: e.clientY, startHeight };

    // Ensure subsequent pointer events are delivered
    e.currentTarget.setPointerCapture(e.pointerId);

    // If height is currently "auto", lock it to the current measured height
    if (inputPanelHeight === null) {
      const maxHeight = getMaxInputPanelHeight();
      setInputPanelHeight(clampNumber(startHeight, CHAT_INPUT_PANEL_MIN_HEIGHT_PX, maxHeight));
    }
  };

  const handleInputPanelResizeMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const drag = resizeDragRef.current;
    if (!drag) return;

    const deltaY = e.clientY - drag.startY;
    const maxHeight = getMaxInputPanelHeight();
    const nextHeight = clampNumber(
      drag.startHeight - deltaY,
      CHAT_INPUT_PANEL_MIN_HEIGHT_PX,
      maxHeight,
    );

    setInputPanelHeight(nextHeight);
  };

  const handleInputPanelResizeEnd = () => {
    if (!resizeDragRef.current) return;
    resizeDragRef.current = null;

    const height = latestInputPanelHeightRef.current;
    if (height === null) return;
    localStorage.setItem(CHAT_INPUT_PANEL_HEIGHT_KEY, String(Math.round(height)));
  };

  const resetInputPanelHeight = () => {
    setInputPanelHeight(null);
    localStorage.removeItem(CHAT_INPUT_PANEL_HEIGHT_KEY);
  };

  // Enforce max threshold on mount and window resize (e.g. viewport changes)
  useEffect(() => {
    const clampToMax = () => {
      const h = latestInputPanelHeightRef.current;
      if (h === null) return;
      const max = getMaxInputPanelHeight();
      if (h > max) setInputPanelHeight(max);
    };

    clampToMax();
    window.addEventListener("resize", clampToMax);
    return () => window.removeEventListener("resize", clampToMax);
  }, [getMaxInputPanelHeight]);

  if (!currentProjectId) {
    return (
      <div className="h-full flex flex-col items-center justify-center text-[hsl(var(--text-secondary))] p-4 text-center">
        <Sparkles size={48} className="mb-4 opacity-50" />
        <p className="text-sm">{t('chat:panel.selectProject')}</p>
      </div>
    );
  }

  return (
    <div
      ref={chatPanelRef}
      className={`flex flex-col h-full bg-[hsl(var(--bg-primary))] overflow-hidden ${isMobile ? 'pb-safe-or-2' : ''}`}
      style={{ maxHeight: '100%' }}
    >
      {/* Header */}
      <div
        ref={headerRef}
        className={`shrink-0 flex items-center justify-between border-b border-[hsl(var(--separator-color))] ${isMobile ? 'h-11 px-2' : 'h-12 px-3'}`}
      >
        {/* 右栏可能只有 300px 宽：标题不折行，状态词放不下就省略，额度用紧凑胶囊。 */}
        <div className="flex min-w-0 items-center gap-1" data-testid="chat-panel-header-title">
          <span className={`shrink-0 whitespace-nowrap font-medium text-[hsl(var(--text-primary))] ${isMobile ? 'text-xs' : 'text-sm'}`}>{t('chat:panel.title')}</span>
          {isStreaming && (
            <span className="min-w-0 truncate whitespace-nowrap text-xs text-[hsl(var(--success))] animate-[breathe_1.5s_ease-in-out_infinite]">
              · {t('chat:panel.processing')}
            </span>
          )}
          {isLoadingHistory && (
            <span className="min-w-0 truncate whitespace-nowrap text-xs text-[hsl(var(--text-secondary))]">· {t('chat:panel.loadingHistory')}</span>
          )}
        </div>
        <div className={`flex min-w-0 items-center ${isMobile ? 'gap-1' : 'gap-2'}`} data-testid="chat-panel-header-actions">
          <QuotaBadge compact />
          <button
            onClick={() => setShowAIMemory(true)}
            className={`flex items-center justify-center text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))] rounded transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-primary))] ${isMobile ? 'p-2 min-h-[44px] min-w-[44px]' : 'p-1.5 min-h-0 min-w-0'}`}
            title={t('chat:panel.aiMemory')}
          >
            <Database size={isMobile ? 18 : 16} />
          </button>
          <button
            onClick={handleNewSession}
            className={`flex items-center justify-center text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))] rounded transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-primary))] ${isMobile ? 'p-2 min-h-[44px] min-w-[44px]' : 'p-1.5 min-h-0 min-w-0'}`}
            title={t('chat:panel.newSession')}
          >
            <Plus size={isMobile ? 18 : 16} />
          </button>
        </div>
      </div>

      {/* Messages - scrollable area */}
      {/* data-testid: message-list - Message container for message display tests */}
      <div ref={messagesScrollContainerRef} className="flex-1 min-h-0 overflow-y-auto overflow-x-hidden" data-testid="message-list">
        <div className={`py-3 ${isMobile ? 'px-2 pb-2' : 'px-3'}`}>
          {isLoadingHistory ? (
            <div className="h-[200px] flex flex-col items-center justify-center text-[hsl(var(--text-secondary))]">
              <Loader2 size={32} className="mb-4 animate-spin opacity-50" />
              <p className="text-sm">{t('chat:panel.loadingMessages')}</p>
            </div>
          ) : messages.length === 0 && !isStreaming && !isThinking && !(error && retryable) ? (
            <div className="h-[200px] flex flex-col items-center justify-center text-[hsl(var(--text-secondary))]">
              <Sparkles size={48} className="mb-4 opacity-30" />
              <p className={`text-center ${isMobile ? 'text-xs mb-1.5 px-2' : 'text-sm mb-2'}`}>{t('chat:panel.welcome')}</p>
              <p className={`text-[hsl(var(--text-secondary))] text-center ${isMobile ? 'text-xs px-4' : 'text-xs'}`}>
                {t('chat:panel.suggestions')}
              </p>
            </div>
          ) : (
            <div>
              {/* Chat messages */}
              <MessageList
                ref={messageListRef}
                messages={messages}
                streamRenderItems={streamRenderItems}
                onIterationAssistAction={handleIterationAssistAction}
                streamingMessageId={undefined}
                onUndo={handleUndo}
                onSubmitFeedback={handleSubmitFeedback}
                feedbackPendingMessageId={feedbackPendingMessageId}
                streamingThinkingContent={thinkingContent}
                isThinking={isThinking}
                scrollContainerRef={messagesScrollContainerRef}
              />

              {/* Edit progress indicator */}
              {editProgress && (
                <div className="mt-3">
                  <div className={`bg-[hsl(var(--result-bg))] border border-[hsl(var(--result-border))] rounded-lg ${isMobile ? 'p-2' : 'p-3'}`}>
                    <div className="flex items-center gap-2 mb-2">
                      <Edit3 className="w-4 h-4 text-[hsl(var(--success-light))]" />
                      <span className={`text-[hsl(var(--success-light))] font-medium ${isMobile ? 'text-xs truncate flex-1' : 'text-sm'}`}>
                        {t('chat:panel.editing')} {editProgress.title}
                      </span>
                    </div>
                    <div className="flex items-center gap-2">
                      <div className="flex-1 bg-[hsl(var(--bg-tertiary))] rounded-full h-2 overflow-hidden">
                        <div
                          className="h-full bg-[hsl(var(--success-light))] transition-all duration-300"
                          style={{
                            width: `${(editProgress.completedEdits / editProgress.totalEdits) * 100}%`,
                          }}
                        />
                      </div>
                      <span className="text-xs text-[hsl(var(--success-light))]">
                        {editProgress.completedEdits}/{editProgress.totalEdits}
                      </span>
                    </div>
                    {editProgress.currentOp && (
                      <div className="mt-2 text-xs text-[hsl(var(--text-secondary))] truncate">
                        {t('chat:panel.operation')} {editProgress.currentOp}
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* Conflicts */}
              {conflicts.length > 0 && (
                <div className="mt-3 space-y-2">
                  {conflicts.map((conflict, index) => (
                    <ToolResultCard
                      key={index}
                      type="conflict"
                      toolName="consistency_check"
                      result={{
                        conflict,
                      }}
                    />
                  ))}
                </div>
              )}

              {/* Error */}
              {error && (
                <div className="mt-3 bg-[hsl(var(--warning)/0.1)] border border-[hsl(var(--warning)/0.3)] rounded-lg p-3 animate-in fade-in slide-in-from-top-2 duration-200">
                  <div className="flex items-start gap-2">
                    <svg
                      className="w-4 h-4 text-[hsl(var(--warning))] mt-0.5 flex-shrink-0"
                      fill="currentColor"
                      viewBox="0 0 20 20"
                    >
                      <path
                        fillRule="evenodd"
                        d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z"
                        clipRule="evenodd"
                      />
                    </svg>
                    <div className="flex-1 min-w-0">
                      <p className="text-[hsl(var(--warning))] text-sm font-medium">
                        {isAiQuotaLimit
                          ? t('chat:panel.quotaExceededTitle')
                          : t('chat:panel.streamErrorTitle')}
                      </p>
                      {!isAiQuotaLimit && (
                        <p className="text-[hsl(var(--warning))] text-sm break-words">{error}</p>
                      )}
                      {retryable && lastRetryRequestRef.current?.projectId === currentProjectId && (
                        <button
                          type="button"
                          onClick={handleRetry}
                          className="mt-2 inline-flex h-8 items-center rounded-md border border-[hsl(var(--warning)/0.35)] px-3 text-xs font-medium text-[hsl(var(--warning))] hover:bg-[hsl(var(--warning)/0.12)] transition-colors"
                        >
                          {t('common:retry')}
                        </button>
                      )}
                      {isAiQuotaLimit && (
                        <div className="mt-2 space-y-2">
                          <p className="text-xs text-[hsl(var(--text-secondary))]">
                            {t('chat:panel.quotaExceededHint')}
                          </p>
                          <button
                            type="button"
                            onClick={() => setShowQuotaUpgradeModal(true)}
                            className="inline-flex h-8 items-center rounded-md border border-[hsl(var(--warning)/0.35)] bg-[hsl(var(--warning)/0.08)] px-3 text-xs font-medium text-[hsl(var(--warning))] hover:bg-[hsl(var(--warning)/0.16)] transition-colors"
                          >
                            {t('dashboard:billing.ctaUpgradePro')}
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              )}

              {isStreaming && (
                <StreamActivityLine activity={streamActivity.activity} startedAt={streamActivity.startedAt} />
              )}

              {/* 这一轮确实没扣 AI 消息（后端退还落库后才会收到） */}
              {quotaRefundNote && (
                <p
                  data-testid="chat-quota-refund-note"
                  className="mt-2 text-xs text-[hsl(var(--text-secondary))]"
                >
                  {quotaRefundNote}
                  {resendAfterStop && (
                    <button
                      type="button"
                      data-testid="chat-resend-after-stop"
                      onClick={() => void handleSendMessage(resendAfterStop, undefined, { entry: "resend_after_stop" })}
                      className="ml-2 text-[hsl(var(--accent-primary))] hover:underline focus-visible:outline-none focus-visible:underline"
                    >
                      {t('chat:panel.resend')}
                    </button>
                  )}
                </p>
              )}

              {/* Jump-to-latest button (only when user scrolled away and new content arrives) */}
              {showJumpToLatest && (
                <div className="sticky bottom-3 z-10 flex justify-center pointer-events-none">
                  <button
                    type="button"
                    onClick={handleJumpToLatest}
                    className="pointer-events-auto inline-flex items-center gap-1.5 rounded-full bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--border-color))] px-3 py-1.5 text-xs text-[hsl(var(--text-primary))] shadow-md hover:bg-[hsl(var(--bg-tertiary))] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-primary))]"
                    aria-label={t('chat:panel.jumpToLatest')}
                    title={t('chat:panel.jumpToLatest')}
                  >
                    <ArrowDown size={14} />
                    {t('chat:panel.jumpToLatest')}
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Input panel (resizable on desktop, fixed on mobile) */}
      <div
        ref={inputPanelRef}
        data-testid="chat-input-panel"
        className={`shrink-0 flex flex-col overflow-hidden ${isMobile ? 'border-t border-[hsl(var(--separator-color))]' : ''}`}
        style={
          isMobile
            ? { maxHeight: '45vh' }
            : inputPanelHeight !== null
              ? { height: `${Math.round(inputPanelHeight)}px` }
              : { maxHeight: `${CHAT_INPUT_PANEL_MAX_HEIGHT_PX}px` }
        }
      >
        {/* Resize handle (drag to adjust input panel height) - hidden on mobile */}
        {!isMobile && (
          <div
            className={`shrink-0 h-4 flex items-center justify-center select-none touch-none cursor-row-resize group border-t border-[hsl(var(--separator-color))] hover:bg-[hsl(var(--accent-primary)/0.08)] active:bg-[hsl(var(--accent-primary)/0.15)] ${resizeDragRef.current ? 'bg-[hsl(var(--accent-primary)/0.1)]' : ''}`}
            onPointerDown={handleInputPanelResizeStart}
            onPointerMove={handleInputPanelResizeMove}
            onPointerUp={handleInputPanelResizeEnd}
            onPointerCancel={handleInputPanelResizeEnd}
            onDoubleClick={resetInputPanelHeight}
            title={t('chat:panel.resizeHandle')}
            aria-label={t('chat:panel.resizeHandle')}
          >
            <div className="w-10 h-1 rounded-full bg-[hsl(var(--border-color))] opacity-40 group-hover:opacity-70 group-active:opacity-80 transition-opacity" />
          </div>
        )}

        <div
          className={`${
            isMobile
              ? "px-2 py-2"
              : `px-3 py-2 ${inputPanelHeight !== null ? "flex-1 min-h-0" : ""}`
          }`}
        >
          {nextStep
            && nextStep.projectId === currentProjectId
            && !dismissedNextStepProjects.has(nextStep.projectId)
            && !isStreaming
            && !isThinking
            && !quotaExhausted && (
            <NextStepCard
              step={nextStep.step}
              onStart={() => {
                const { projectId, step } = nextStep;
                trackEvent("ai_next_step_clicked", { project_id: projectId, kind: step.kind });
                setNextStep(null);
                void handleSendMessage(step.message, undefined, { entry: "next_step" });
              }}
              onDismiss={() => {
                trackEvent("ai_next_step_dismissed", { project_id: nextStep.projectId, kind: nextStep.step.kind });
                setDismissedNextStepProjects((prev) => new Set(prev).add(nextStep.projectId));
              }}
            />
          )}
          <MessageInput
            onSend={handleSendMessage}
            // Allow drafting while AI is streaming/thinking, but prevent sending until it finishes.
            disabled={isLoadingHistory}
            // Quota used up: drafting stays possible, sending waits for the daily reset.
            sendDisabled={isStreaming || isThinking || isLoadingHistory || quotaExhausted}
            onCancel={isStreaming ? handleCancel : undefined}
            isStopping={isStopping}
            // Steering: while streaming with an active session, the user can send
            // a follow-up instruction that the agent picks up at its next step.
            onSteer={handleSteer}
            canSteer={isStreaming && !!sessionId}
            placeholder={
              isStreaming || isThinking
                ? t("chat:input.placeholderWhileProcessing")
                : quotaExhausted
                  ? t("chat:input.placeholderQuotaExhausted", { limit: aiMessageQuota?.limit })
                  : undefined
            }
            aiSuggestions={aiSuggestions}
            messageCount={messages.length}
            suggestionDisplayState={suggestionDisplayState}
            onRefreshSuggestions={refreshSuggestionsNow}
            isRefreshingSuggestions={isRefreshingSuggestions}
            // Mobile input panel uses a max-height (not a fixed height). Using "fill" can cause
            // flex-shrink overflow overlaps when the keyboard reduces viewport height.
            layout={!isMobile && inputPanelHeight !== null ? "fill" : "auto"}
            matchedSkills={matchedSkills}
            externalDraft={draft}
            onDraftChange={saveDraft}
            generationMode={generationMode}
            onGenerationModeChange={handleGenerationModeChange}
          />
        </div>
      </div>

      <UpgradePromptModal
        open={showQuotaUpgradeModal}
        onClose={() => setShowQuotaUpgradeModal(false)}
        source={chatQuotaUpgradePrompt.source}
        primaryDestination="billing"
        secondaryDestination="pricing"
        title={t('chat:panel.quotaExceededTitle')}
        description={t('chat:panel.quotaExceededHint')}
        primaryLabel={t('dashboard:billing.ctaUpgradePro')}
        onPrimary={() => {
          window.location.assign(
            buildUpgradeUrl(chatQuotaUpgradePrompt.billingPath, chatQuotaUpgradePrompt.source)
          );
        }}
        secondaryLabel={t('home:pricingTeaser.viewPricing')}
        onSecondary={() => {
          window.location.assign(
            buildUpgradeUrl(chatQuotaUpgradePrompt.pricingPath, chatQuotaUpgradePrompt.source)
          );
        }}
      />

      <UpgradePromptModal
        open={showFileVersionUpgradeModal}
        onClose={() => setShowFileVersionUpgradeModal(false)}
        source={fileVersionUpgradePrompt.source}
        primaryDestination="billing"
        secondaryDestination="pricing"
        title={t('editor:versionHistory.fileVersionLimitTitle')}
        description={t('editor:versionHistory.fileVersionLimitUpgrade')}
        primaryLabel={t('common:viewUpgrade')}
        onPrimary={() => {
          window.location.assign(
            buildUpgradeUrl(fileVersionUpgradePrompt.billingPath, fileVersionUpgradePrompt.source)
          );
        }}
        secondaryLabel={t('common:viewPlans')}
        onSecondary={() => {
          window.location.assign(
            buildUpgradeUrl(fileVersionUpgradePrompt.pricingPath, fileVersionUpgradePrompt.source)
          );
        }}
      />

      {/* Undo an AI edit: the copy is the file-level undo message owned by the editor
          namespace (not the snapshot "restore" copy), shown in the in-app dialog
          instead of the browser's native confirm. */}
      <ConfirmDialog
        open={pendingUndoTarget !== null}
        onClose={handleCloseUndoDialog}
        onConfirm={handleConfirmUndo}
        title={t('chat:tool.undo_edit')}
        message={t('editor:versionHistory.confirmUndoAIEdit', '撤销这次 AI 修改？正文会换回修改前的内容，已有的历史版本都会保留。')}
        confirmLabel={t('chat:actions.undo')}
        cancelLabel={t('common:cancel')}
        variant="warning"
        loading={isUndoing}
      />

      {/* Leaving the workbench mid-round drops the stream and ends the round. */}
      <ConfirmDialog
        open={leaveGuard.leavePending}
        onClose={leaveGuard.cancelLeave}
        onConfirm={leaveGuard.confirmLeave}
        title={t('chat:leaveWhileGenerating.title')}
        message={t('chat:leaveWhileGenerating.message')}
        confirmLabel={t('chat:leaveWhileGenerating.leave')}
        cancelLabel={t('chat:leaveWhileGenerating.stay')}
        variant="warning"
      />

      {/* AI Memory Dialog */}
      {showAIMemory && currentProjectId && (
        <ProjectStatusDialog
          isOpen={showAIMemory}
          onClose={() => setShowAIMemory(false)}
          projectId={currentProjectId}
        />
      )}
    </div>
  );
};

export const ChatPanel = React.memo(ChatPanelComponent);
