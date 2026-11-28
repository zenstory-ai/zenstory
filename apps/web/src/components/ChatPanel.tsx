/**
 * @fileoverview ChatPanel component - AI chat interface for the zenstory writing workbench.
 *
 * Thin orchestration shell composing:
 * - History, pagination & session lifecycle (`useChatHistory`)
 * - Quota tracking, midnight resets & stop/refund notes (`useChatQuotaAndStops`)
 * - Suggestions cache & refresh (`useChatSuggestionsController`)
 * - Auto-scroll, ResizeObserver settle & desktop input resize (`useChatScrollAndResize`)
 * - Undo confirmation & deletion restore (`useChatUndoAndRestore`)
 * - Starters, NextStepCard & dashboard inspiration auto-send (`useChatStartersAndNextStep`)
 * - Streaming UI & SSE transport (`useChatStreaming` + `useAgentStream`)
 *
 * @module components/ChatPanel
 */

import React, {
  lazy,
  Suspense,
  useState,
  useRef,
  useEffect,
  useCallback,
  useMemo,
  useSyncExternalStore,
} from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2, Sparkles } from "lucide-react";
import { useProject } from "../contexts/ProjectContext";
import { useAuth } from "../contexts/AuthContext";
import { useFirstWork, needsStaticFirstWorkGuidance } from "../hooks/useFirstWork";
import { firstWorkQueryKey } from "../lib/firstWorkApi";
import { useMobileLayout } from "../contexts/MobileLayoutContext";
import { useMaterialAttachment } from "../contexts/MaterialAttachmentContext";
import { useTextQuote } from "../contexts/TextQuoteContext";
import {
  useAgentStream,
  type MessageSegment,
  type StreamCompletionMeta,
} from "../hooks/useAgentStream";
import { useChatStreaming } from "../hooks/useChatStreaming";
import { dropProgressOnlyItems } from "../lib/chatDisplayEvents";
import { MessageList, type Message, type MessageStatusCard } from "./MessageList";
import { MessageInput } from "./MessageInput";
import { ConfirmDialog } from "./ui/ConfirmDialog";
import { subscriptionQueryKeys } from "../lib/subscriptionApi";
import { flushOpenEditor } from "../lib/editorSaveTracker";
import type { AgentRequest, FileType, SSEWorkflowStoppedData } from "../types";
import { logger } from "../lib/logger";
import { fileApi } from "../lib/api";
import { NextStepCard } from "./NextStepCard";
import { StarterCards } from "./StarterCards";
import { duplicatesNextStep } from "../lib/nextStep";
import { UndoBlockedContext, UndoneEditsContext } from "../lib/undoneEdits";
import { ApiError } from "../lib/apiClient";
import { toast } from "../lib/toast";
import { appendDraftForProject, useDraftPersistence } from "../hooks/useDraftPersistence";
import { UpgradePromptModal } from "./subscription/UpgradePromptModal";
import { ChatQuotaCard } from "./subscription/ChatQuotaCard";
import { trackEvent } from "../lib/analytics";
import { useLeaveWhileGenerating } from "../hooks/useLeaveWhileGenerating";
import { useAnimatedPresence } from "../hooks/useAnimatedPresence";
import { StreamActivityLine } from "./StreamActivityLine";
import {
  createBodyWriteProgress,
  createProseCounter,
  getActiveStreamStatusText,
  isWriteToolResult,
} from "../lib/agentRoundProgress";
import { formatDailyResetLocalTime } from "../lib/dateUtils";
import { useQuotaRefreshAfterLeave } from "../hooks/useQuotaRefreshAfterLeave";
import {
  exhaustionLeftFilesUnfinished,
  hasSubstantiveDisplayItems,
} from "../lib/chatRoundEnd";
import {
  hasAnyUnprotectedDeletedDraft,
  notifyFileRemoved,
  subscribeDeletedDraftProtection,
} from "../lib/fileRemovalEvents";
import type { QuotaResponse } from "../types/subscription";
import {
  extractCompletedToolResults,
  isAccountRunRefusal,
  isNetworkQuotaRefusal,
  isUnexpectedStreamDisconnect,
  keepUnsavedFile,
  SSE_DISCONNECT_RECONCILE_DELAY_MS,
} from "./chat/chatHistoryHelpers";
import { useChatSuggestionsController } from "./chat/useChatSuggestionsController";
import {
  CHAT_INPUT_PANEL_MAX_HEIGHT_PX,
  useChatScrollAndResize,
} from "./chat/useChatScrollAndResize";
import {
  useChatQuotaEffectsAndNotes,
  useChatQuotaState,
} from "./chat/useChatQuotaAndStops";
import { useChatUndoAndRestore } from "./chat/useChatUndoAndRestore";
import {
  useChatStartersAndNextStepEffects,
  useProjectProgressState,
} from "./chat/useChatStartersAndNextStep";
import { useChatHistory } from "./chat/useChatHistory";
import { ChatPanelHeader } from "./chat/ChatPanelHeader";
import {
  ChatStreamErrorCard,
  EditProgressCard,
  JumpToLatestOverlay,
  LoadEarlierMessagesButton,
  RoundStatusNotes,
  SessionRecoveryAlert,
} from "./chat/ChatStatusBanners";

// eslint-disable-next-line react-refresh/only-export-components
export { mergeEarlierHistoryMessages } from "./chat/chatHistoryHelpers";

const ProjectStatusDialog = lazy(() =>
  import("./ProjectStatusDialog").then((m) => ({ default: m.ProjectStatusDialog })),
);
const LeaveWhileGeneratingDialog = lazy(() =>
  import("./LeaveWhileGeneratingDialog").then((m) => ({
    default: m.LeaveWhileGeneratingDialog,
  })),
);

// eslint-disable-next-line @typescript-eslint/no-empty-object-type
interface ChatPanelProps {}

const ChatPanelComponent: React.FC<ChatPanelProps> = () => {
  const { t, i18n } = useTranslation(["chat", "common", "dashboard", "home", "editor", "versions"]);
  const {
    currentProjectId,
    currentProject,
    selectedItem,
    fileTreeVersion,
    triggerFileTreeRefresh,
    triggerEditorRefresh,
    setAiEditingFileId,
    setIsChatStreaming,
    pendingAgentUiAction,
    consumeAgentUiAction,
    setSelectedItem,
    appendFileContent,
    finishFileStreaming,
    startFileStreaming,
    streamingFileId,
    refreshProject,
  } = useProject();

  const queryClient = useQueryClient();
  const { user } = useAuth();
  const userId = user?.id ?? null;
  const firstWork = useFirstWork(userId);
  const staticFirstWork = needsStaticFirstWorkGuidance(firstWork.data, currentProjectId);
  const { isMobile, activePanel, chatFocusRequest, switchToEditor, focusChatInput } =
    useMobileLayout();
  const { attachedFileIds, attachedLibraryMaterials, clearMaterials } = useMaterialAttachment();
  const { quotes, clearQuotes } = useTextQuote();

  const streamingFileIdRef = useRef<string | null>(streamingFileId);
  useEffect(() => {
    streamingFileIdRef.current = streamingFileId;
  }, [streamingFileId]);
  const selectedItemRef = useRef(selectedItem);
  useEffect(() => {
    selectedItemRef.current = selectedItem;
  }, [selectedItem]);

  const currentProjectIdRef = useRef<string | null>(currentProjectId);
  const [messages, setMessages] = useState<Message[]>([]);
  const messagesRef = useRef<Message[]>(messages);
  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  const [streamStartedAt, setStreamStartedAt] = useState<number | null>(null);
  const roundProgressRef = useRef({
    wroteFiles: false,
    charged: false,
    producedOutput: false,
    endedInError: false,
    endedIncomplete: false,
  });
  const proseCounterRef = useRef(createProseCounter());
  const bodyWriteRef = useRef(createBodyWriteProgress());
  const readBodyWrite = useCallback(() => bodyWriteRef.current.snapshot(), []);
  const stopRequestedProjectRef = useRef<string | null>(null);
  const disconnectReconcileTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const disconnectReconcileSeqRef = useRef(0);
  const roundBaselineBackendIdsRef = useRef<Set<string>>(new Set());
  const isStreamingRef = useRef(false);
  const isThinkingRef = useRef(false);
  const clearErrorRef = useRef<() => void>(() => {});
  const cancelPendingDisconnectReconcile = useCallback(() => {
    disconnectReconcileSeqRef.current += 1;
    if (disconnectReconcileTimerRef.current) {
      clearTimeout(disconnectReconcileTimerRef.current);
      disconnectReconcileTimerRef.current = null;
    }
  }, []);
  const sendLockRef = useRef(false);
  const newChatAfterStopRef = useRef(false);
  const [newChatPending, setNewChatPending] = useState(false);
  const pendingMaterialClearRef = useRef(false);
  const pendingQuoteClearRef = useRef(false);
  const currentAgentSessionIdRef = useRef<string | null>(null);
  const terminalStatusCardsRef = useRef<MessageStatusCard[]>([]);
  const lastRetryRequestRef = useRef<{
    projectId: string;
    request: Omit<AgentRequest, "project_id">;
  } | null>(null);
  const [lastRetryMeta, setLastRetryMeta] = useState<{
    projectId: string;
    entry: unknown;
  } | null>(null);

  const [showAIMemory, setShowAIMemory] = useState(false);
  const [aiMemoryInitialSection, setAIMemoryInitialSection] = useState<
    "writing-agreements" | undefined
  >();
  const openAIMemory = useCallback((initialSection?: "writing-agreements") => {
    setAIMemoryInitialSection(initialSection);
    setShowAIMemory(true);
  }, []);
  const openWritingAgreements = useCallback(
    () => openAIMemory("writing-agreements"),
    [openAIMemory],
  );

  const { draft, saveDraft, clearDraft } = useDraftPersistence(currentProjectId);
  const draftRef = useRef(draft);
  useEffect(() => {
    draftRef.current = draft;
  }, [draft]);

  const blockedCandidateRef = useRef<{
    id: string;
    content: string;
    skillIds: string[];
    keepIfBusy?: boolean;
  } | null>(null);
  const [refusedSend, setRefusedSend] = useState<{
    seq: number;
    message: string;
    skillIds: string[];
  } | null>(null);
  const returnBlockedMessageToInput = useCallback(() => {
    const candidate = blockedCandidateRef.current;
    blockedCandidateRef.current = null;
    if (!candidate) return false;
    setMessages((prev) => prev.filter((message) => message.id !== candidate.id));
    setRefusedSend((prev) => ({
      seq: (prev?.seq ?? 0) + 1,
      message: candidate.content,
      skillIds: candidate.skillIds,
    }));
    const typedSince = draftRef.current;
    if (!typedSince.trim()) {
      saveDraft(candidate.content);
    } else if (!typedSince.includes(candidate.content)) {
      saveDraft(`${candidate.content}\n\n${typedSince}`);
    }
    return true;
  }, [saveDraft]);

  useEffect(() => {
    lastRetryRequestRef.current = null;
    setLastRetryMeta(null);
    blockedCandidateRef.current = null;
  }, [currentProjectId]);

  const quotaState = useChatQuotaState({
    currentProjectId,
    userId,
    isMobile,
    queryClient,
  });
  const {
    invalidateQuota,
    roundQuotaBaselineRef,
    reportQuotaBlockedSend,
    setShowQuotaUpgradeModal,
    setUserStop,
  } = quotaState;

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

  const suggestions = useChatSuggestionsController({
    currentProjectId,
    currentProjectIdRef,
    userId,
    queryClient,
    messagesRef,
  });
  const {
    requestInitialSuggestions,
    schedulePostTurnSuggestions,
    invalidateInFlightSuggestions,
    resetSuggestionsForNewRound,
    clearSuggestionsForUnfinishedRound,
    setAiSuggestions,
    setSuggestionDisplayState,
  } = suggestions;

  const progressState = useProjectProgressState(currentProjectIdRef);
  const {
    starterProjectIdRef,
    refreshNextStep,
    resetProgressForProject,
    setStarterProjectId,
  } = progressState;

  // Reset scroll helper ref bridged once scroll controller initializes below
  const resetScrollStateRef = useRef<() => void>(() => {});
  const onResetScrollForError = useCallback(() => {
    resetScrollStateRef.current();
  }, []);

  const history = useChatHistory({
    currentProjectIdRef,
    userId,
    queryClient,
    t,
    setMessages,
    currentAgentSessionIdRef,
    setSuggestionDisplayState,
    onResetScrollForError,
  });

  const {
    creatingSessionRef,
    newSessionRequestSeqRef,
    sessionRecoveryBlockedRef,
    historyRequestSeqRef,
    lastLoadedProjectRef,
    loadChatHistory,
    hydrateAssistantBackendMessage,
    reconcileAfterDisconnect,
    loadEarlierMessages,
    setHasMoreHistory,
    setIsLoadingMoreHistory,
    setFeedbackPendingMessageId,
    setIsCreatingSession,
    setSessionRecoveryError,
  } = history;

  useEffect(() => {
    currentProjectIdRef.current = currentProjectId;
    creatingSessionRef.current = null;
    setIsCreatingSession(false);
    sessionRecoveryBlockedRef.current = null;
    setSessionRecoveryError(null);
    cancelPendingDisconnectReconcile();
    return () => {
      currentProjectIdRef.current = null;
      newSessionRequestSeqRef.current += 1;
      cancelPendingDisconnectReconcile();
    };
  }, [
    cancelPendingDisconnectReconcile,
    currentProjectId,
    creatingSessionRef,
    newSessionRequestSeqRef,
    sessionRecoveryBlockedRef,
    setIsCreatingSession,
    setSessionRecoveryError,
  ]);

  const handleOpenFile = useCallback(
    (fileId: string, fileType: string, title: string) => {
      if (
        !currentProjectId ||
        currentProject?.id !== currentProjectId ||
        currentProjectIdRef.current !== currentProjectId
      ) {
        return;
      }
      const typeMap: Record<string, FileType> = {
        folder: "folder",
        outline: "outline",
        draft: "draft",
        document: "document",
        script: "script",
        character: "character",
        lore: "lore",
        snippet: "material",
        material: "material",
      };
      const selectedAtClick = selectedItemRef.current?.id ?? null;
      const open = (type: string, openTitle: string) => {
        if (currentProjectIdRef.current !== currentProjectId) return;
        if ((selectedItemRef.current?.id ?? null) !== selectedAtClick) return;
        if (selectedItemRef.current?.id !== fileId) {
          setSelectedItem({ id: fileId, type: typeMap[type] || "draft", title: openTitle });
        }
        if (isMobile) switchToEditor();
      };
      if (fileType) {
        open(fileType, title);
        return;
      }
      void fileApi.get(fileId).then(
        (file) => open(file.file_type, file.title || title),
        () => open("draft", title),
      );
    },
    [currentProjectId, currentProject, setSelectedItem, isMobile, switchToEditor],
  );

  const streamCallbacks = useMemo(
    () =>
      getStreamCallbacks({
        triggerFileTreeRefresh,
        triggerEditorRefresh,
        setAiEditingFileId,
        setSelectedItem,
        getCurrentSelectedItem: () => selectedItemRef.current,
        appendFileContent,
        finishFileStreaming,
        startFileStreaming,
        getCurrentStreamingFileId: () => streamingFileIdRef.current,
        activeProjectId: currentProjectId,
        currentUserId: userId,
        onProjectRenamed: (projectId) => {
          void refreshProject(projectId);
        },
        t: (key, options) => t(key as string, options as Record<string, unknown>),
      }),
    [
      getStreamCallbacks,
      triggerFileTreeRefresh,
      triggerEditorRefresh,
      setAiEditingFileId,
      setSelectedItem,
      appendFileContent,
      finishFileStreaming,
      startFileStreaming,
      currentProjectId,
      userId,
      refreshProject,
      t,
    ],
  );

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

  const onCompleteHandler = useCallback(
    async (completedSegments: MessageSegment[], completionMeta?: StreamCompletionMeta) => {
      void queryClient.invalidateQueries({ queryKey: firstWorkQueryKey(userId) });

      const accumulatedContent = completedSegments
        .filter((s) => s.type === "content")
        .map((s) => s.content || "")
        .filter((c) => c.trim())
        .join("\n\n");

      const statusCards = terminalStatusCardsRef.current;
      terminalStatusCardsRef.current = [];
      const toolResults = extractCompletedToolResults(completedSegments);
      const displayItems = getStreamItemsSnapshot?.() ?? [];
      const endedEarly =
        completionMeta?.partial === true || completionMeta?.stoppedByAuthor === true;
      const assistantMessageId = completionMeta?.assistantMessageId;

      const finishedItems = dropProgressOnlyItems(displayItems);
      const hasNonBlankFinishedItem = finishedItems.some(
        (item) =>
          (item.type !== "content" && item.type !== "thinking_content") ||
          Boolean(item.content?.trim()),
      );
      const hasSubstance =
        Boolean(
          accumulatedContent.trim() ||
            statusCards.length > 0 ||
            toolResults?.length ||
            completionMeta?.deliveryOutcome,
        ) ||
        (endedEarly ? hasSubstantiveDisplayItems(displayItems) : hasNonBlankFinishedItem);

      if (hasSubstance) {
        const aiMessage: Message = {
          id: `ai-${Date.now()}-${Math.random().toString(36).substring(2, 11)}`,
          backendMessageId: assistantMessageId,
          role: "assistant",
          content: accumulatedContent,
          timestamp: new Date(),
          statusCards: statusCards.length > 0 ? statusCards : undefined,
          toolResults,
          stopOutcome: completionMeta?.deliveryOutcome,
          displayItems: finishedItems.length ? finishedItems : undefined,
          feedbackUnavailable: endedEarly && !assistantMessageId ? true : undefined,
        };
        setMessages((prev) => [...prev, aiMessage]);

        if (currentProjectId && (!endedEarly || assistantMessageId)) {
          void hydrateAssistantBackendMessage(
            currentProjectId,
            aiMessage.id,
            accumulatedContent,
            {
              assistantMessageId,
              expectedSessionId: completionMeta?.sessionId ?? currentAgentSessionIdRef.current,
            },
          );
        }
      }

      const stoppedThisRound =
        completionMeta?.stoppedByAuthor === true ||
        (completionMeta?.partial === true && stopRequestedProjectRef.current === currentProjectId);
      stopRequestedProjectRef.current = null;
      if (stoppedThisRound && currentProjectId) {
        const { wroteFiles, charged } = roundProgressRef.current;
        const producedOutput =
          completionMeta?.producedOutput ?? roundProgressRef.current.producedOutput;
        const writtenFiles = completionMeta?.writtenFiles ?? [];
        setUserStop({
          projectId: currentProjectId,
          wroteFiles: wroteFiles || writtenFiles.length > 0,
          counted: completionMeta?.charged ?? (charged && producedOutput),
          writtenFiles,
          unwrittenFiles: completionMeta?.unwrittenFiles,
        });
      }

      finalizePendingContextClears();
      invalidateQuota();
      if (currentProjectId) void refreshNextStep(currentProjectId);

      void streamCallbacks.onComplete(completedSegments, completionMeta);

      const nothingWritten = completionMeta?.stoppedByAuthor
        ? completionMeta.producedOutput === false
        : completionMeta?.partial === true && !roundProgressRef.current.producedOutput;
      const endedInError =
        completionMeta?.partial === true && roundProgressRef.current.endedInError;
      const endedUnfinished =
        endedInError ||
        completionMeta?.deliveryOutcome?.reason === "error" ||
        completionMeta?.deliveryOutcome?.reason === "incomplete" ||
        roundProgressRef.current.endedIncomplete;

      if (nothingWritten || endedUnfinished) {
        clearSuggestionsForUnfinishedRound(currentProjectId, endedUnfinished);
        return;
      }
      schedulePostTurnSuggestions(currentProjectId);
    },
    [
      clearSuggestionsForUnfinishedRound,
      currentProjectId,
      finalizePendingContextClears,
      getStreamItemsSnapshot,
      hydrateAssistantBackendMessage,
      invalidateQuota,
      queryClient,
      refreshNextStep,
      schedulePostTurnSuggestions,
      setUserStop,
      streamCallbacks,
      userId,
    ],
  );

  const {
    state,
    startStream,
    stop,
    isStopping,
    reset,
    whenStreamClosed,
    isStreaming,
    isThinking,
    error,
    errorCode,
    clearError,
    retryable,
    sessionId,
    sendSteeringMessage,
  } = useAgentStream(currentProjectId!, {
    onStart: () => {
      streamCallbacks.onStart();
      setStreamStartedAt(Date.now());
      terminalStatusCardsRef.current = [];
      quotaState.setQuotaRefund(null);
      quotaState.setUserStop(null);
      roundProgressRef.current = {
        wroteFiles: false,
        charged: false,
        producedOutput: false,
        endedInError: false,
        endedIncomplete: false,
      };
      suggestions.invalidateInFlightSuggestions();
      proseCounterRef.current.reset();
      bodyWriteRef.current.reset();
      stopRequestedProjectRef.current = null;
    },
    onContext: (items) => {
      streamCallbacks.onContext(items);
    },
    onThinking: streamCallbacks.onThinking,
    onThinkingContent: streamCallbacks.onThinkingContent,
    onHistoryCompacted: streamCallbacks.onHistoryCompacted,
    onSegmentStart: streamCallbacks.onSegmentStart,
    onSegmentUpdate: (segmentId, content) => {
      if (proseCounterRef.current.update(segmentId, content)) {
        roundProgressRef.current.producedOutput = true;
      }
      streamCallbacks.onSegmentUpdate(segmentId, content);
    },
    onSegmentUpdateToolCalls: streamCallbacks.onSegmentUpdateToolCalls,
    onComplete: onCompleteHandler,
    onError: (message, code, retryableFlag) => {
      roundProgressRef.current.endedInError = true;
      suggestions.invalidateInFlightSuggestions();
      streamCallbacks.onError(message, code, retryableFlag);
      quotaState.invalidateQuota();
      if (
        code === "ERR_QUOTA_AI_CONVERSATIONS_EXCEEDED" ||
        code === "ERR_QUOTA_AI_DAILY_COST_EXCEEDED"
      ) {
        returnBlockedMessageToInput();
        quotaState.reportQuotaBlockedSend(true);
      }
      if (isAccountRunRefusal(code) || isNetworkQuotaRefusal(code)) {
        resetPendingContextClears();
        if (!blockedCandidateRef.current?.keepIfBusy && returnBlockedMessageToInput()) {
          lastRetryRequestRef.current = null;
          setLastRetryMeta(null);
        }
      }
      if (
        currentProjectId &&
        isUnexpectedStreamDisconnect(code, retryableFlag) &&
        stopRequestedProjectRef.current !== currentProjectId
      ) {
        cancelPendingDisconnectReconcile();
        const reconcileSeq = ++disconnectReconcileSeqRef.current;
        const targetProjectId = currentProjectId;
        const baselineIds = new Set(roundBaselineBackendIdsRef.current);
        const targetSessionId = currentAgentSessionIdRef.current;
        disconnectReconcileTimerRef.current = setTimeout(() => {
          disconnectReconcileTimerRef.current = null;
          const shouldApply = () =>
            disconnectReconcileSeqRef.current === reconcileSeq &&
            currentProjectIdRef.current === targetProjectId &&
            !isStreamingRef.current &&
            stopRequestedProjectRef.current !== targetProjectId &&
            creatingSessionRef.current === null;
          if (!shouldApply()) {
            return;
          }
          // Strictly read-only GET reconcile: surface server-persisted draft/final reply,
          // NEVER automatically retry a generation POST.
          void reconcileAfterDisconnect(
            targetProjectId,
            baselineIds,
            targetSessionId,
            shouldApply,
          ).then((reconciled) => {
            if (!reconciled || !shouldApply()) {
              return;
            }
            clearErrorRef.current();
            quotaState.invalidateQuota();
            triggerFileTreeRefresh();
            void refreshNextStep(targetProjectId);
          });
        }, SSE_DISCONNECT_RECONCILE_DELAY_MS);
      }
    },
    onToolCall: (toolName, args) => {
      proseCounterRef.current.boundary();
      bodyWriteRef.current.toolCall(toolName, args);
    },
    onToolResult: (toolName, status, result, toolError) => {
      proseCounterRef.current.boundary();
      bodyWriteRef.current.toolResult(toolName, status, result);
      if (isWriteToolResult(toolName, status, result)) {
        roundProgressRef.current.wroteFiles = true;
        roundProgressRef.current.producedOutput = true;
      }
      streamCallbacks.onToolResult(toolName, status, result, toolError);
    },
    onFileCreated: (fileId, fileType, title) => {
      proseCounterRef.current.boundary();
      bodyWriteRef.current.fileCreated(fileId, title);
      streamCallbacks.onFileCreated(fileId, fileType, title);
    },
    onFileContent: (fileId, chunk) => {
      if (chunk.trim()) {
        roundProgressRef.current.wroteFiles = true;
        roundProgressRef.current.producedOutput = true;
      }
      bodyWriteRef.current.chunk(fileId, chunk);
      streamCallbacks.onFileContent(fileId, chunk);
    },
    onFileContentEnd: (fileId) => {
      bodyWriteRef.current.end(fileId);
      streamCallbacks.onFileContentEnd(fileId);
    },
    onFileEditStart: streamCallbacks.onFileEditStart,
    onFileEditApplied: streamCallbacks.onFileEditApplied,
    onFileEditEnd: streamCallbacks.onFileEditEnd,
    onSkillMatched: streamCallbacks.onSkillMatched,
    onIterationExhausted: (
      layer,
      iterationsUsed,
      maxIterations,
      reason,
      lastAgent,
      limit,
      files,
    ) => {
      if (exhaustionLeftFilesUnfinished({ layer, ...files })) {
        roundProgressRef.current.endedIncomplete = true;
      }
      terminalStatusCardsRef.current = [
        {
          type: "iteration_exhausted",
          layer,
          iterationsUsed,
          maxIterations,
          reason,
          lastAgent,
          limit,
          ...files,
        },
      ];
      streamCallbacks.onIterationExhausted(
        layer,
        iterationsUsed,
        maxIterations,
        reason,
        lastAgent,
        limit,
        ...(files ? [files] : []),
      );
    },
    onWorkflowStopped: (data: SSEWorkflowStoppedData) => {
      terminalStatusCardsRef.current = [
        {
          type: "workflow_stopped",
          reason: data.reason,
          agentType: data.agent_type,
          message: data.message,
          question: data.question,
          context: data.context,
          details: data.details,
        },
      ];
      streamCallbacks.onWorkflowStopped(data);
    },
    onSessionStarted: (startedSessionId) => {
      blockedCandidateRef.current = null;
      currentAgentSessionIdRef.current = startedSessionId;
      streamCallbacks.onSessionStarted(startedSessionId);
      roundProgressRef.current.charged = true;
      quotaState.invalidateQuota();
      void queryClient.invalidateQueries({ queryKey: firstWorkQueryKey(userId) });
    },
    onSteeringReceived: streamCallbacks.onSteeringReceived,
    onQuotaRefunded: (kind, removedFiles = []) => {
      if (currentProjectId) {
        quotaState.setQuotaRefund({ projectId: currentProjectId, kind, removedFiles });
      }
      quotaState.applyConfirmedRefund();
      quotaState.invalidateQuota();
      if (removedFiles.length > 0) {
        if (currentProjectId) {
          removedFiles.forEach((file) => notifyFileRemoved(currentProjectId, file.id));
        }
        const removedIds = new Set(removedFiles.map((file) => file.id));
        if (selectedItemRef.current && removedIds.has(selectedItemRef.current.id)) {
          setSelectedItem(null);
        }
        triggerFileTreeRefresh();
      }
    },
  });

  isStreamingRef.current = isStreaming;
  isThinkingRef.current = isThinking;
  clearErrorRef.current = clearError;

  const readStreamStatusText = useCallback(
    () => getActiveStreamStatusText(getStreamItemsSnapshot?.()),
    [getStreamItemsSnapshot],
  );
  const readIsThinkingWithoutProse = useCallback(() => {
    if (roundProgressRef.current.producedOutput) return false;
    const items = getStreamItemsSnapshot?.() ?? [];
    const hasVisibleProse = items.some(
      (item) => item.type === "content" && typeof item.content === "string" && item.content.trim().length > 0,
    );
    if (hasVisibleProse) return false;
    return isThinkingRef.current || items.some((item) => item.type === "thinking_content");
  }, [getStreamItemsSnapshot]);

  const scroll = useChatScrollAndResize({
    currentProjectId,
    messagesRef,
    messagesLength: messages.length,
    streamContent: state.content,
    streamRenderItemCount: streamRenderItems?.length ?? 0,
    isStreaming,
    isThinking,
    isLoadingHistory: history.isLoadingHistory,
  });
  resetScrollStateRef.current = scroll.resetScrollState;

  const undo = useChatUndoAndRestore({
    currentProjectId,
    messages,
    setMessages,
    streamRenderItems,
    triggerFileTreeRefresh,
    triggerEditorRefresh,
    t,
  });

  const hasLastRetryForProject = lastRetryMeta?.projectId === currentProjectId;

  const quotaNotes = useChatQuotaEffectsAndNotes({
    currentProjectId,
    aiMessageQuota: quotaState.aiMessageQuota,
    quotaExhausted: quotaState.quotaExhausted,
    showDailyCount: quotaState.showDailyCount,
    quotaRefund: quotaState.quotaRefund,
    userStop: quotaState.userStop,
    setShowQuotaUpgradeModal: quotaState.setShowQuotaUpgradeModal,
    isStreaming,
    isThinking,
    isLoadingHistory: history.isLoadingHistory,
    error,
    errorCode,
    clearError,
    messages,
    hasLastRetryForProject,
    t,
    language: i18n?.language,
  });

  const unprotectedDeletedDraft = useSyncExternalStore(
    subscribeDeletedDraftProtection,
    hasAnyUnprotectedDeletedDraft,
    () => false,
  );
  const leaveGuard = useLeaveWhileGenerating(
    (isStreaming && !isStopping) || unprotectedDeletedDraft,
  );
  useQuotaRefreshAfterLeave(isStreaming, currentProjectId ?? null);

  const leaveAfterSavePendingRef = useRef(false);
  const handleConfirmLeave = () => {
    const confirmLeave = leaveGuard.confirmLeave;
    if (!isStreaming || leaveGuard.roundEnded) {
      confirmLeave();
      return;
    }
    if (leaveAfterSavePendingRef.current) return;
    const editorSaved = flushOpenEditor();
    if (!editorSaved) {
      confirmLeave();
      return;
    }
    leaveAfterSavePendingRef.current = true;
    void editorSaved.then(async (result) => {
      const keep = keepUnsavedFile(result);
      if (keep) await stop(keep);
      leaveAfterSavePendingRef.current = false;
      confirmLeave();
    });
  };

  const isHistoryLoadBlocked =
    isStreaming || isThinking || history.isCreatingSession || newChatPending;
  const isHistoryLoadBlockedRef = useRef(isHistoryLoadBlocked);
  isHistoryLoadBlockedRef.current = isHistoryLoadBlocked;

  const {
    messagesScrollContainerRef,
    shouldAutoScrollRef,
    followLatestMessage,
    resetScrollState,
  } = scroll;

  const handleLoadEarlierMessages = useCallback(
    () =>
      loadEarlierMessages({
        isHistoryLoadBlockedRef,
        sendLockRef,
        newChatAfterStopRef,
        messagesScrollContainerRef,
        shouldAutoScrollRef,
      }),
    [loadEarlierMessages, messagesScrollContainerRef, shouldAutoScrollRef],
  );

  // Load history when project changes

  useEffect(() => {
    if (!currentProjectId) return;
    if (lastLoadedProjectRef.current === currentProjectId) return;

    lastLoadedProjectRef.current = currentProjectId;

    currentAgentSessionIdRef.current = null;
    setMessages([]);
    setHasMoreHistory(false);
    setIsLoadingMoreHistory(false);
    resetScrollState();
    setFeedbackPendingMessageId(null);
    setEditProgress(null);
    clearStreamItems();
    setMatchedSkills([]);
    reset();
    invalidateInFlightSuggestions();
    setAiSuggestions([]);
    setSuggestionDisplayState("loading");

    resetProgressForProject();
    void refreshNextStep(currentProjectId);

    void (async () => {
      const loadedMessages = await loadChatHistory(currentProjectId);
      if (loadedMessages === null) return;
      if (loadedMessages.length === 0) {
        setStarterProjectId(currentProjectId);
        setAiSuggestions([]);
        setSuggestionDisplayState("ready");
        return;
      }
      await requestInitialSuggestions(currentProjectId, loadedMessages);
    })();
  }, [
    currentProjectId,
    lastLoadedProjectRef,
    loadChatHistory,
    requestInitialSuggestions,
    invalidateInFlightSuggestions,
    refreshNextStep,
    resetProgressForProject,
    resetScrollState,
    setAiSuggestions,
    setSuggestionDisplayState,
    setStarterProjectId,
    setHasMoreHistory,
    setIsLoadingMoreHistory,
    setFeedbackPendingMessageId,
    setEditProgress,
    clearStreamItems,
    setMatchedSkills,
    reset,
  ]);

  const startRound = useCallback(
    (
      request: Omit<AgentRequest, "project_id">,
      options: { userBubble: boolean; keepIfBusy?: boolean },
    ): boolean => {
      if (
        sendLockRef.current ||
        isStreaming ||
        newChatAfterStopRef.current ||
        creatingSessionRef.current !== null ||
        sessionRecoveryBlockedRef.current
      ) {
        return false;
      }
      sendLockRef.current = true;
      setTimeout(() => {
        sendLockRef.current = false;
      }, 0);

      historyRequestSeqRef.current += 1;
      cancelPendingDisconnectReconcile();
      roundBaselineBackendIdsRef.current = new Set(
        messagesRef.current
          .map((item) => item.backendMessageId)
          .filter((id): id is string => Boolean(id)),
      );
      setIsLoadingMoreHistory(false);
      resetSuggestionsForNewRound();

      const userMessageId = `user-${Date.now()}-${Math.random().toString(36).substring(2, 8)}`;
      if (options.userBubble) {
        const userMessage: Message = {
          id: userMessageId,
          role: "user",
          content: request.message,
          timestamp: new Date(),
        };
        setMessages((prev) => [...prev, userMessage]);
      }
      blockedCandidateRef.current = options.userBubble
        ? {
            id: userMessageId,
            content: request.message,
            skillIds: request.selected_skill_ids ?? [],
            keepIfBusy: options.keepIfBusy,
          }
        : null;
      roundQuotaBaselineRef.current =
        queryClient.getQueryData<QuotaResponse>(subscriptionQueryKeys.quota())?.ai_conversations
          .used ?? null;
      followLatestMessage();

      if (currentProjectId) {
        lastRetryRequestRef.current = { projectId: currentProjectId, request };
        setLastRetryMeta({ projectId: currentProjectId, entry: request.metadata?.entry });
      }
      startStream(request);
      return true;
    },
    [
      cancelPendingDisconnectReconcile,
      creatingSessionRef,
      currentProjectId,
      followLatestMessage,
      historyRequestSeqRef,
      isStreaming,
      queryClient,
      resetSuggestionsForNewRound,
      roundQuotaBaselineRef,
      sessionRecoveryBlockedRef,
      setIsLoadingMoreHistory,
      startStream,
    ],
  );

  const handleSendMessage = useCallback(
    async (
      message: string,
      selectedSkillIds?: string[],
      extraMetadata?: Record<string, unknown>,
      { keepComposer = false }: { keepComposer?: boolean } = {},
    ) => {
      if (
        !message.trim() ||
        isStreaming ||
        sendLockRef.current ||
        newChatAfterStopRef.current ||
        creatingSessionRef.current !== null ||
        sessionRecoveryBlockedRef.current
      ) {
        return false;
      }

      if (quotaNotes.aiSendBlocked) {
        if (!keepComposer) saveDraft(message);
        reportQuotaBlockedSend(false);
        setShowQuotaUpgradeModal(true);
        return false;
      }

      if (!keepComposer) clearDraft();
      const sentFileIds = keepComposer ? [] : attachedFileIds;
      const sentLibraryMaterials = keepComposer ? [] : attachedLibraryMaterials;
      const sentQuotes = keepComposer ? [] : quotes;

      const entry =
        typeof extraMetadata?.entry === "string"
          ? extraMetadata.entry
          : starterProjectIdRef.current === currentProjectId &&
              messagesRef.current.length === 0
            ? "first_message"
            : undefined;
      const requestMetadata = entry ? { ...extraMetadata, entry } : extraMetadata;

      const request: Omit<AgentRequest, "project_id"> = {
        message,
        selected_text: undefined,
        selected_skill_ids:
          selectedSkillIds && selectedSkillIds.length > 0 ? selectedSkillIds : undefined,
        metadata: {
          current_file_id: selectedItem?.id,
          current_file_type: selectedItem?.type,
          current_file_title: selectedItem?.title,
          attached_file_ids: sentFileIds.length > 0 ? sentFileIds : undefined,
          attached_library_materials:
            sentLibraryMaterials.length > 0 ? sentLibraryMaterials : undefined,
          text_quotes:
            sentQuotes.length > 0
              ? sentQuotes.map((q) => ({
                  text: q.text,
                  fileId: q.fileId,
                  fileTitle: q.fileTitle,
                }))
              : undefined,
          ...requestMetadata,
        },
      };

      pendingMaterialClearRef.current =
        sentFileIds.length > 0 || sentLibraryMaterials.length > 0;
      pendingQuoteClearRef.current = sentQuotes.length > 0;

      trackEvent("ai_chat_submitted", {
        project_id: currentProjectId,
        entry: entry ?? "typed",
        selected_item_type: selectedItem?.type,
        attached_file_count: sentFileIds.length,
        attached_library_material_count: sentLibraryMaterials.length,
        quote_count: sentQuotes.length,
      });

      return startRound(request, { userBubble: true });
    },
    [
      attachedFileIds,
      attachedLibraryMaterials,
      clearDraft,
      creatingSessionRef,
      currentProjectId,
      isStreaming,
      quotaNotes.aiSendBlocked,
      quotes,
      reportQuotaBlockedSend,
      saveDraft,
      selectedItem?.id,
      selectedItem?.title,
      selectedItem?.type,
      sessionRecoveryBlockedRef,
      setShowQuotaUpgradeModal,
      starterProjectIdRef,
      startRound,
    ],
  );

  // Share chat streaming status with the editor so editor-initiated AI actions disable while busy.
  useEffect(() => {
    setIsChatStreaming?.(isStreaming);
  }, [isStreaming, setIsChatStreaming]);
  useEffect(() => {
    return () => {
      setIsChatStreaming?.(false);
    };
  }, [setIsChatStreaming]);

  // Handle one-shot structured UI actions dispatched from the editor (e.g. 去AI味).
  const lastHandledUiActionIdRef = useRef<number | null>(null);
  useEffect(() => {
    if (
      !pendingAgentUiAction ||
      !currentProjectId ||
      pendingAgentUiAction.projectId !== currentProjectId
    ) {
      return;
    }
    if (lastHandledUiActionIdRef.current === pendingAgentUiAction.id) return;
    lastHandledUiActionIdRef.current = pendingAgentUiAction.id;
    consumeAgentUiAction?.(pendingAgentUiAction.id);
    if (pendingAgentUiAction.action !== "natural_polish") return;

    if (
      isStreaming ||
      sendLockRef.current ||
      newChatAfterStopRef.current ||
      creatingSessionRef.current !== null ||
      sessionRecoveryBlockedRef.current
    ) {
      toast.info(t("editor:naturalPolishBusy"));
      return;
    }
    if (quotaNotes.aiSendBlocked) {
      reportQuotaBlockedSend(false);
      setShowQuotaUpgradeModal(true);
      return;
    }

    const title = pendingAgentUiAction.fileTitle?.trim();
    const hasSelection = Boolean(pendingAgentUiAction.selectedText?.trim());
    const userMessage = hasSelection
      ? title
        ? t("editor:naturalPolishPromptSelection", { title })
        : t("editor:naturalPolishPromptSelectionFallback")
      : title
        ? t("editor:naturalPolishPromptChapter", { title })
        : t("editor:naturalPolishPromptChapterFallback");

    const request: Omit<AgentRequest, "project_id"> = {
      message: userMessage,
      selected_text: hasSelection ? pendingAgentUiAction.selectedText : undefined,
      ui_action: "natural_polish",
      metadata: {
        current_file_id: pendingAgentUiAction.fileId,
        current_file_type: pendingAgentUiAction.fileType,
        current_file_title: pendingAgentUiAction.fileTitle,
        entry: "editor_natural_polish",
      },
    };

    trackEvent("ai_chat_submitted", {
      project_id: currentProjectId,
      entry: "editor_natural_polish",
      selected_item_type: pendingAgentUiAction.fileType,
      has_selection: hasSelection,
    });

    startRound(request, { userBubble: true });
  }, [
    pendingAgentUiAction,
    currentProjectId,
    consumeAgentUiAction,
    isStreaming,
    creatingSessionRef,
    sessionRecoveryBlockedRef,
    quotaNotes.aiSendBlocked,
    reportQuotaBlockedSend,
    setShowQuotaUpgradeModal,
    t,
    startRound,
  ]);

  const resendLastRound = useCallback(() => {
    const last = lastRetryRequestRef.current;
    if (!last || last.projectId !== currentProjectId) return;
    trackEvent("ai_chat_submitted", {
      project_id: currentProjectId,
      entry: "resend_after_stop",
    });
    startRound(
      { ...last.request, metadata: { ...last.request.metadata, resent_after_stop: true } },
      { userBubble: true, keepIfBusy: true },
    );
  }, [currentProjectId, startRound]);

  const handleRetry = useCallback(() => {
    const lastRequest = lastRetryRequestRef.current;
    if (
      !retryable ||
      isStreaming ||
      !currentProjectId ||
      lastRequest?.projectId !== currentProjectId
    ) {
      return;
    }
    const started = startRound(
      { ...lastRequest.request, metadata: { ...lastRequest.request.metadata, retry_unanswered: true } },
      { userBubble: false },
    );
    if (started) {
      lastRetryRequestRef.current = lastRequest;
      setLastRetryMeta({
        projectId: lastRequest.projectId,
        entry: lastRequest.request.metadata?.entry,
      });
    }
  }, [currentProjectId, isStreaming, retryable, startRound]);

  const retryUnanswered = useCallback(
    (message: Message) => {
      trackEvent("ai_chat_submitted", {
        project_id: currentProjectId,
        entry: "retry_unanswered",
      });
      const firstMessage = messagesRef.current.every((item) => item.role === "user");
      startRound(
        {
          message: message.content,
          metadata: {
            retry_unanswered: true,
            ...(firstMessage ? { entry: "first_message" } : {}),
          },
        },
        { userBubble: false },
      );
    },
    [currentProjectId, startRound],
  );

  const [steerFallback, setSteerFallback] = useState<{
    projectId: string;
    message: string;
    stopped?: boolean;
  } | null>(null);

  const handleSteer = useCallback(
    async (message: string) => {
      try {
        if (!message.trim()) {
          throw new Error("Empty steering message");
        }
        if (!isStreaming) {
          throw new Error("No active stream for steering");
        }
        await sendSteeringMessage(message);
      } catch (err) {
        if (err instanceof ApiError && err.status === 404 && currentProjectId) {
          setSteerFallback({ projectId: currentProjectId, message });
          toast.success(t("chat:input.steerSentAsNextMessage"));
          return;
        }
        logger.error("Failed to send steering message", err);
        toast.error(t("chat:input.steerFailed"));
        throw err;
      }
      toast.success(t("chat:input.steerSent"));
    },
    [currentProjectId, isStreaming, sendSteeringMessage, t],
  );

  useEffect(() => {
    if (!steerFallback || isStreaming) return;
    setSteerFallback(null);
    const { projectId, message, stopped } = steerFallback;
    if (currentProjectIdRef.current !== projectId) {
      if (appendDraftForProject(projectId, message)) {
        toast.success(t("chat:input.steerReturnedToInput"));
      } else {
        toast.error(t("chat:input.steerFailed"));
      }
      return;
    }
    const backToInput = () =>
      saveDraft(draft.trim() ? `${draft.trimEnd()}\n${message}` : message);
    if (stopped) {
      backToInput();
      toast.success(t("chat:input.steerReturnedToInput"));
      return;
    }
    void handleSendMessage(message, undefined, undefined, { keepComposer: true }).then((sent) => {
      if (!sent) backToInput();
    });
  }, [steerFallback, isStreaming, handleSendMessage, saveDraft, draft, t]);

  const handleIterationAssistAction = useCallback(
    (action: "continue" | "split" | "manual") => {
      if (action === "continue") {
        saveDraft(t("chat:workflow.actionPrompt.continue"));
        return;
      }
      if (action === "split") {
        saveDraft(t("chat:workflow.actionPrompt.split"));
        return;
      }
      saveDraft(t("chat:workflow.actionPrompt.manual"));
    },
    [saveDraft, t],
  );

  const starters = useChatStartersAndNextStepEffects({
    currentProjectId,
    currentProjectIdRef,
    currentProjectType: currentProject?.project_type,
    fileTreeVersion,
    messages,
    isLoadingHistory: history.isLoadingHistory,
    isStreaming,
    isThinking,
    error,
    aiSendBlocked: quotaNotes.aiSendBlocked,
    newChatPending,
    isCreatingSession: history.isCreatingSession,
    sessionRecoveryError: history.sessionRecoveryError,
    resendAfterStop: quotaNotes.resendAfterStop,
    lastRetryEntry: hasLastRetryForProject ? lastRetryMeta?.entry : undefined,
    chatFocusRequest,
    focusChatInput,
    draft,
    saveDraft,
    handleSendMessage,
    creatingSessionRef: history.creatingSessionRef,
    newChatAfterStopRef,
    sessionRecoveryBlockedRef: history.sessionRecoveryBlockedRef,
    sendLockRef,
    progressState,
  });

  const handleNewSession = () => {
    cancelPendingDisconnectReconcile();
    return history.runNewSession({
      projectId: currentProjectId,
      setStarterProjectId: progressState.setStarterProjectId,
      resetSuggestionsForNewRound: suggestions.resetSuggestionsForNewRound,
      resetPendingContextClears,
      resetScrollState: scroll.resetScrollState,
      clearStreamItems,
      clearMatchedSkills: () => setMatchedSkills([]),
      resetStream: reset,
      clearQuotaAndStop: () => {
        quotaState.setQuotaRefund(null);
        quotaState.setUserStop(null);
      },
      requestInitialSuggestions: suggestions.requestInitialSuggestions,
    });
  };

  const stopAfterSavePendingRef = useRef(false);
  const stopRound = (): Promise<void> => {
    cancelPendingDisconnectReconcile();
    if (stopAfterSavePendingRef.current) return Promise.resolve();
    if (currentProjectId) stopRequestedProjectRef.current = currentProjectId;
    setSteerFallback((pending) =>
      pending && !pending.stopped ? { ...pending, stopped: true } : pending,
    );
    const editorSaved = flushOpenEditor();
    if (!editorSaved) return stop();
    stopAfterSavePendingRef.current = true;
    return editorSaved.then((result) => {
      stopAfterSavePendingRef.current = false;
      return stop(keepUnsavedFile(result));
    });
  };
  const handleCancel = () => {
    void stopRound();
  };

  const [newChatConfirmOpen, setNewChatConfirmOpen] = useState(false);
  const { isMounted: isLeaveDialogMounted } = useAnimatedPresence(leaveGuard.leavePending, 180);
  const { isMounted: isNewChatConfirmMounted } = useAnimatedPresence(newChatConfirmOpen, 180);
  const handleNewSessionRef = useRef(handleNewSession);
  useEffect(() => {
    handleNewSessionRef.current = handleNewSession;
  });

  const startNewChatAfterRound = async (stopFirst: boolean) => {
    const projectId = currentProjectId;
    if (!projectId || newChatAfterStopRef.current) return;
    newChatAfterStopRef.current = true;
    setNewChatPending(true);
    try {
      if (stopFirst) await stopRound();
      await whenStreamClosed();
    } finally {
      newChatAfterStopRef.current = false;
      setNewChatPending(false);
    }
    if (currentProjectIdRef.current !== projectId) return;
    await handleNewSessionRef.current();
  };

  const requestNewChat = () => {
    if (newChatAfterStopRef.current || history.creatingSessionRef.current !== null) return;
    if (!isStreaming) {
      void handleNewSession();
      return;
    }
    if (isStopping) {
      void startNewChatAfterRound(false);
      return;
    }
    setNewChatConfirmOpen(true);
  };

  const confirmNewChat = () => {
    setNewChatConfirmOpen(false);
    void startNewChatAfterRound(isStreaming && !isStopping);
  };

  const leaveConfirmation = isLeaveDialogMounted ? (
    <Suspense fallback={null}>
      <LeaveWhileGeneratingDialog
        open={leaveGuard.leavePending}
        unprotectedDraft={unprotectedDeletedDraft}
        roundEnded={unprotectedDeletedDraft ? !isStreaming : leaveGuard.roundEnded}
        producedOutput={roundProgressRef.current.producedOutput}
        showDailyCount={quotaState.showDailyCount}
        onStay={leaveGuard.cancelLeave}
        onLeave={handleConfirmLeave}
      />
    </Suspense>
  ) : null;

  if (!currentProjectId) {
    return (
      <>
        <div className="h-full flex flex-col items-center justify-center bg-[hsl(var(--bg-rail))] text-[hsl(var(--text-secondary))] p-4 text-center">
          <Sparkles size={48} className="mb-4 opacity-50" />
          <p className="text-sm">{t("chat:panel.selectProject")}</p>
        </div>
        {leaveConfirmation}
      </>
    );
  }

  const isAlreadyEmptyNewChat =
    !history.isLoadingHistory &&
    messages.length === 0 &&
    !isStreaming &&
    !isThinking &&
    !error &&
    !quotaState.userStop &&
    !quotaState.quotaRefund &&
    history.sessionRecoveryError !== currentProjectId;

  const canRetryStreamError = Boolean(
    retryable && !quotaNotes.aiSendBlocked && hasLastRetryForProject,
  );

  return (
    <div
      ref={scroll.chatPanelRef}
      className={`flex flex-col h-full bg-[hsl(var(--bg-rail))] overflow-hidden ${isMobile ? "pb-safe-or-2" : ""}`}
      style={{ maxHeight: "100%" }}
    >
      <ChatPanelHeader
        headerRef={scroll.headerRef}
        isMobile={isMobile}
        isStreaming={isStreaming}
        isLoadingHistory={history.isLoadingHistory}
        isCreatingSession={history.isCreatingSession}
        newChatPending={newChatPending}
        isAlreadyEmptyNewChat={isAlreadyEmptyNewChat}
        sessionTitle={history.currentSessionTitle}
        onOpenAIMemory={() => openAIMemory()}
        onRequestNewChat={requestNewChat}
      />

      {/* Messages - scrollable area */}
      <div className="relative flex-1 min-h-0 flex flex-col">
        <div
          ref={scroll.messagesScrollContainerRef}
          className="flex-1 min-h-0 overflow-y-auto overflow-x-hidden chat-message-feed-fade"
          data-testid="message-list"
        >
          <div className={`pt-3.5 pb-6 ${isMobile ? "px-3" : "px-4"}`}>
            {history.sessionRecoveryError === currentProjectId && (
              <SessionRecoveryAlert
                isLoadingHistory={history.isLoadingHistory}
                onRetry={() => {
                  if (currentProjectId) void history.loadChatHistory(currentProjectId, true);
                }}
              />
            )}
            {history.isLoadingHistory ? (
              <div className="h-[200px] flex flex-col items-center justify-center text-[hsl(var(--text-secondary))]">
                <Loader2 size={32} className="mb-4 animate-spin opacity-50" />
                <p className="text-sm">{t("chat:panel.loadingMessages")}</p>
              </div>
            ) : messages.length === 0 &&
              !isStreaming &&
              !isThinking &&
              !(error && retryable) &&
              history.sessionRecoveryError !== currentProjectId ? (
              starters.showStarterCards ? (
                <StarterCards
                  highlighted={starters.starterHighlighted}
                  projectType={currentProject?.project_type}
                  remainingToday={
                    quotaState.showDailyCount && quotaState.aiMessageQuota
                      ? Math.max(0, quotaState.aiMessageQuota.limit - quotaState.aiMessageQuota.used)
                      : null
                  }
                  onSend={starters.handleStarterSend}
                  onEdit={starters.handleStarterEdit}
                  hasManuscript={starters.starterHasManuscript}
                  onSendManuscript={starters.handleStarterManuscriptSend}
                />
              ) : starters.starterCardsDue ? (
                <div data-testid="starter-pending" className="h-[200px]" />
              ) : (
                <div className="h-[200px] flex flex-col items-center justify-center text-[hsl(var(--text-secondary))]">
                  <Sparkles size={48} className="mb-4 opacity-30" />
                  <p className={`text-center ${isMobile ? "text-xs mb-1.5 px-2" : "text-sm mb-2"}`}>
                    {t("chat:panel.welcome")}
                  </p>
                  <p
                    className={`text-[hsl(var(--text-secondary))] text-center ${
                      isMobile ? "text-xs px-4" : "text-xs"
                    }`}
                  >
                    {t("chat:panel.suggestions")}
                  </p>
                </div>
              )
            ) : (
              <div className="motion-content-reveal">
                {history.hasMoreHistory && !history.isLoadingHistory && messages.length > 0 && (
                  <LoadEarlierMessagesButton
                    isLoadingMoreHistory={history.isLoadingMoreHistory}
                    isDisabled={history.isLoadingMoreHistory || isHistoryLoadBlocked}
                    onLoadEarlier={() => void handleLoadEarlierMessages()}
                  />
                )}
                <UndoneEditsContext.Provider value={undo.undoneEdits}>
                  <UndoBlockedContext.Provider value={undo.undoBlocked}>
                    <MessageList
                      ref={scroll.messageListRef}
                      messages={messages}
                      streamRenderItems={streamRenderItems}
                      onIterationAssistAction={handleIterationAssistAction}
                      nextStepsBlocked={quotaNotes.aiSendBlocked}
                      onUndo={undo.handleUndo}
                      onRestoreDeletion={undo.handleRestoreDeletion}
                      onOpenFile={
                        currentProjectId && currentProject?.id === currentProjectId
                          ? handleOpenFile
                          : undefined
                      }
                      onOpenAIMemory={currentProjectId ? openWritingAgreements : undefined}
                      onSubmitFeedback={history.handleSubmitFeedback}
                      feedbackPendingMessageId={history.feedbackPendingMessageId}
                      showDailyCount={quotaState.showDailyCount}
                      scrollContainerRef={scroll.messagesScrollContainerRef}
                    />
                  </UndoBlockedContext.Provider>
                </UndoneEditsContext.Provider>

                {editProgress && (
                  <EditProgressCard editProgress={editProgress} isMobile={isMobile} />
                )}

                {error && !(quotaNotes.isAiQuotaLimit && quotaNotes.aiSendBlocked) && (
                  <ChatStreamErrorCard
                    error={error}
                    errorCode={errorCode}
                    isAiQuotaLimit={quotaNotes.isAiQuotaLimit}
                    isNetworkQuotaLimit={quotaNotes.isNetworkQuotaLimit}
                    aiSendBlocked={quotaNotes.aiSendBlocked}
                    canRetry={canRetryStreamError}
                    isMobile={isMobile}
                    onRetry={handleRetry}
                    onOpenUpgradeModal={() => quotaState.setShowQuotaUpgradeModal(true)}
                  />
                )}

                {isStreaming && (
                  <StreamActivityLine
                    startedAt={streamStartedAt}
                    isMobile={isMobile}
                    bodyWrite={readBodyWrite}
                    statusText={readStreamStatusText}
                    isThinking={readIsThinkingWithoutProse}
                  />
                )}

                <RoundStatusNotes
                  userStopNote={quotaNotes.userStopNote}
                  quotaRefundNote={quotaNotes.quotaRefundNote}
                  resendAfterStop={quotaNotes.resendAfterStop}
                  unansweredUserMessage={quotaNotes.unansweredUserMessage}
                  aiSendBlocked={quotaNotes.aiSendBlocked}
                  onResendLastRound={resendLastRound}
                  onRetryUnanswered={retryUnanswered}
                />
              </div>
            )}
          </div>
        </div>

        {scroll.isJumpToLatestMounted && (
          <JumpToLatestOverlay
            isExiting={scroll.isJumpToLatestExiting}
            onJump={scroll.handleJumpToLatest}
          />
        )}
      </div>

      {/* Input panel (resizable on desktop, fixed on mobile) */}
      <div
        ref={scroll.inputPanelRef}
        data-testid="chat-input-panel"
        className={`shrink-0 flex flex-col overflow-hidden ${
          isMobile ? "border-t border-[hsl(var(--separator-color))]" : ""
        }`}
        style={
          isMobile
            ? { maxHeight: "45vh" }
            : scroll.inputPanelHeight !== null
              ? { height: `${Math.round(scroll.inputPanelHeight)}px` }
              : { maxHeight: `${CHAT_INPUT_PANEL_MAX_HEIGHT_PX}px` }
        }
      >
        {!isMobile && (
          <div
            role="separator"
            aria-orientation="horizontal"
            className={`shrink-0 h-3.5 flex items-center justify-center select-none touch-none cursor-row-resize group border-t border-[hsl(var(--separator-color)/0.55)] hover:bg-[hsl(var(--accent-primary)/0.08)] active:bg-[hsl(var(--accent-primary)/0.15)] ${
              scroll.resizeDragRef.current ? "bg-[hsl(var(--accent-primary)/0.1)]" : ""
            }`}
            onPointerDown={scroll.handleInputPanelResizeStart}
            onPointerMove={scroll.handleInputPanelResizeMove}
            onPointerUp={scroll.handleInputPanelResizeEnd}
            onPointerCancel={scroll.handleInputPanelResizeEnd}
            onDoubleClick={scroll.resetInputPanelHeight}
            title={t("chat:panel.resizeHandle")}
            aria-label={t("chat:panel.resizeHandle")}
          >
            <div className="w-10 h-1 rounded-full bg-[hsl(var(--border-color))] opacity-40 group-hover:opacity-70 group-active:opacity-80 transition-opacity" />
          </div>
        )}

        <div
          className={
            isMobile
              ? "px-2 py-2"
              : `px-3 py-2 ${scroll.inputPanelHeight !== null ? "flex-1 min-h-0" : ""}`
          }
        >
          {starters.showNextStep && progressState.frameworkReadyProjectId && (
            <NextStepCard
              projectType={currentProject?.project_type}
              onStart={(message) => {
                const readyProjectId = progressState.frameworkReadyProjectId!;
                trackEvent("ai_next_step_clicked", { project_id: readyProjectId });
                progressState.frameworkReadyRef.current = null;
                progressState.setFrameworkReadyProjectId(null);
                void handleSendMessage(message, undefined, { entry: "next_step" });
              }}
              onDismiss={() => {
                const readyProjectId = progressState.frameworkReadyProjectId!;
                trackEvent("ai_next_step_dismissed", { project_id: readyProjectId });
                progressState.nextStepDismissal.dismiss(readyProjectId);
              }}
            />
          )}
          <ChatQuotaCard
            open={quotaNotes.aiSendBlocked && !isStreaming && !isThinking}
            limit={quotaState.quotaExhausted ? quotaState.aiMessageQuota?.limit : undefined}
            resetAt={quotaState.aiMessageQuota?.reset_at ?? null}
          />
          <MessageInput
            focusRequest={!isMobile || activePanel === "chat" ? chatFocusRequest : 0}
            onSend={handleSendMessage}
            refusedSend={refusedSend}
            disabled={history.isLoadingHistory}
            sendDisabled={
              isStreaming ||
              isThinking ||
              history.isLoadingHistory ||
              newChatPending ||
              history.isCreatingSession ||
              history.sessionRecoveryError === currentProjectId
            }
            onBlockedSend={
              quotaNotes.aiSendBlocked
                ? () => {
                    quotaState.reportQuotaBlockedSend(false);
                    quotaState.setShowQuotaUpgradeModal(true);
                  }
                : undefined
            }
            onCancel={isStreaming || newChatPending ? handleCancel : undefined}
            isStopping={isStopping || newChatPending}
            onSteer={handleSteer}
            canSteer={isStreaming && !!sessionId}
            placeholder={
              isStreaming || isThinking || newChatPending
                ? t("chat:input.placeholderWhileProcessing")
                : quotaNotes.aiSendBlocked
                  ? t("chat:input.placeholderQuotaExhausted")
                  : undefined
            }
            aiSuggestions={suggestions.aiSuggestions}
            hideSuggestion={starters.showNextStep ? duplicatesNextStep : undefined}
            hideSuggestions={quotaNotes.aiSendBlocked && !isStreaming && !isThinking}
            suggestionDisplayState={
              staticFirstWork || !firstWork.isSuccess ? "ready" : suggestions.suggestionDisplayState
            }
            onRefreshSuggestions={suggestions.refreshSuggestionsNow}
            isRefreshingSuggestions={suggestions.isRefreshingSuggestions}
            layout={!isMobile && scroll.inputPanelHeight !== null ? "fill" : "auto"}
            matchedSkills={matchedSkills}
            externalDraft={draft}
            onDraftChange={saveDraft}
          />
        </div>
      </div>

      <UpgradePromptModal
        open={quotaState.showQuotaUpgradeModal}
        onClose={() => quotaState.setShowQuotaUpgradeModal(false)}
        promptId="chat_quota_blocked"
        title={
          quotaNotes.isNetworkQuotaLimit
            ? t("chat:panel.networkQuotaExceededTitle")
            : t("chat:panel.quotaExceededTitle")
        }
        description={(() => {
          const activeLanguage = (i18n?.resolvedLanguage || i18n?.language || "").trim().toLowerCase();
          const localResetTime = Boolean(activeLanguage) && !activeLanguage.startsWith("zh")
            ? formatDailyResetLocalTime(activeLanguage)
            : null;
          if (quotaNotes.isNetworkQuotaLimit) {
            return localResetTime
              ? t("chat:panel.networkQuotaExceededHintLocal", { localTime: localResetTime })
              : t("chat:panel.networkQuotaExceededHint");
          }
          return localResetTime
            ? t("chat:panel.quotaExceededHintLocal", { localTime: localResetTime })
            : t("chat:panel.quotaExceededHint");
        })()}
        primaryLabel={t("dashboard:billing.ctaUpgradePro")}
        secondaryLabel={t("home:pricingTeaser.viewPricing")}
      />

      <UpgradePromptModal
        open={undo.showFileVersionUpgradeModal}
        onClose={() => undo.setShowFileVersionUpgradeModal(false)}
        promptId="file_version_quota_blocked"
        title={t("editor:versionHistory.fileVersionLimitTitle")}
        description={t("editor:versionHistory.fileVersionLimitUpgrade")}
        paidDescription={t("editor:versionHistory.fileVersionLimitPaid", {
          defaultValue: "正文照常保存，只是这个文件不再生成新版本。",
        })}
        primaryLabel={t("common:viewUpgrade")}
        secondaryLabel={t("common:viewPlans")}
      />

      <ConfirmDialog
        open={undo.pendingUndoTarget !== null}
        onClose={undo.handleCloseUndoDialog}
        onConfirm={undo.handleConfirmUndo}
        title={t("chat:tool.undo_edit")}
        message={t(
          "editor:versionHistory.confirmUndoAIEdit",
          "撤销这次 AI 修改？正文会换回修改前的内容，已有的历史版本都会保留。",
        )}
        confirmLabel={t("chat:actions.undo")}
        cancelLabel={t("common:cancel")}
        variant="warning"
        loading={undo.isUndoing}
      />

      {leaveConfirmation}

      {isNewChatConfirmMounted && (
        <Suspense fallback={null}>
          <LeaveWhileGeneratingDialog
            intent="newChat"
            open={newChatConfirmOpen}
            roundEnded={newChatConfirmOpen && !isStreaming}
            producedOutput={roundProgressRef.current.producedOutput}
            showDailyCount={quotaState.showDailyCount}
            onStay={() => setNewChatConfirmOpen(false)}
            onLeave={confirmNewChat}
          />
        </Suspense>
      )}

      {showAIMemory && currentProjectId && (
        <Suspense fallback={null}>
          <ProjectStatusDialog
            isOpen={showAIMemory}
            onClose={() => setShowAIMemory(false)}
            projectId={currentProjectId}
            initialSection={aiMemoryInitialSection}
          />
        </Suspense>
      )}
    </div>
  );
};

export const ChatPanel = React.memo(ChatPanelComponent);
