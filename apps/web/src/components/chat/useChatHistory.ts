/**
 * Chat history loading (/bootstrap handoff + fallback), load-earlier pagination
 * with scroll-anchor preservation, assistant backend message hydration, feedback
 * submission, and no-replay new-session creation for ChatPanel.
 *
 * @module components/chat/useChatHistory
 */

import {
  useCallback,
  useRef,
  useState,
  type Dispatch,
  type MutableRefObject,
  type SetStateAction,
} from "react";
import type { QueryClient } from "@tanstack/react-query";
import type { TFunction } from "i18next";
import type { Message } from "../MessageList";
import {
  createNewSession,
  getRecentMessages,
  submitMessageFeedback,
  type ChatMessage,
  type MessageFeedbackVote,
} from "../../lib/chatApi";
import {
  BOOTSTRAP_DEFAULT_CHAT_LIMIT,
  markBootstrappedChatConsumed,
  peekBootstrappedChat,
} from "../../lib/workbenchBootstrap";
import { parseUTCDate } from "../../lib/dateUtils";
import { logger } from "../../lib/logger";
import { toast } from "../../lib/toast";
import {
  findUnassignedAssistantMessage,
  mapBackendHistoryMessages,
  mergeEarlierHistoryMessages,
  normalizeFeedbackFromResponse,
  parseMessageFeedbackFromMetadata,
  parseMessageMetadataObject,
} from "./chatHistoryHelpers";
import type { SuggestionDisplayState } from "./useChatSuggestionsController";

export interface UseChatHistoryOptions {
  currentProjectIdRef: MutableRefObject<string | null>;
  userId: string | null;
  queryClient: QueryClient;
  t: TFunction;
  setMessages: Dispatch<SetStateAction<Message[]>>;
  currentAgentSessionIdRef: MutableRefObject<string | null>;
  setSuggestionDisplayState: Dispatch<SetStateAction<SuggestionDisplayState>>;
  onResetScrollForError: () => void;
}

export function useChatHistory({
  currentProjectIdRef,
  userId,
  queryClient,
  t,
  setMessages,
  currentAgentSessionIdRef,
  setSuggestionDisplayState,
  onResetScrollForError,
}: UseChatHistoryOptions) {
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);
  const [hasMoreHistory, setHasMoreHistory] = useState(false);
  const [isLoadingMoreHistory, setIsLoadingMoreHistory] = useState(false);
  const [feedbackPendingMessageId, setFeedbackPendingMessageId] = useState<string | null>(null);
  const [isCreatingSession, setIsCreatingSession] = useState(false);
  const [sessionRecoveryError, setSessionRecoveryError] = useState<string | null>(null);

  const historyLimitRef = useRef(50);
  const historyRequestSeqRef = useRef(0);
  const newSessionRequestSeqRef = useRef(0);
  const creatingSessionRef = useRef<number | null>(null);
  const sessionRecoveryBlockedRef = useRef<string | null>(null);
  const lastLoadedProjectRef = useRef<string | null>(null);

  const hydrateAssistantBackendMessage = useCallback(
    async (
      projectId: string,
      localMessageId: string,
      expectedContent: string,
      options?: {
        assistantMessageId?: string;
        expectedSessionId?: string | null;
      },
    ) => {
      if (currentProjectIdRef.current !== projectId) return;

      try {
        const recentMessages = options?.assistantMessageId
          ? await getRecentMessages(projectId, 50, undefined, options.assistantMessageId)
          : await getRecentMessages(projectId, 50);
        if (currentProjectIdRef.current !== projectId) return;

        setMessages((prev) => {
          const localMessage = prev.find((item) => item.id === localMessageId);
          if (
            !localMessage ||
            (localMessage.backendMessageId &&
              localMessage.backendMessageId !== options?.assistantMessageId)
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
                  message.id === options.assistantMessageId &&
                  message.role === "assistant" &&
                  !assignedBackendIds.has(message.id),
              ) ?? null
            : findUnassignedAssistantMessage(
                recentMessages,
                assignedBackendIds,
                expectedContent,
                options?.expectedSessionId,
              );
          if (!matchedMessage) return prev;

          const matchedFeedback = parseMessageFeedbackFromMetadata(
            parseMessageMetadataObject(matchedMessage.metadata),
          );

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
    },
    [currentProjectIdRef, setMessages],
  );

  const loadChatHistory = useCallback(
    async (projectId: string, recoverSession = false): Promise<Message[] | null> => {
      sessionRecoveryBlockedRef.current = projectId;
      setSessionRecoveryError(null);
      const requestId = ++historyRequestSeqRef.current;
      setIsLoadingHistory(true);
      try {
        let historyMessages: ChatMessage[] | null = null;
        let hasMore = false;
        let hydratedSessionId: string | null = null;
        let loadedLimit = 50;

        if (!recoverSession) {
          const pendingBootstrap = peekBootstrappedChat(queryClient, userId, projectId);
          if (pendingBootstrap) {
            const bootstrapped = await pendingBootstrap.promise;
            if (currentProjectIdRef.current !== projectId || requestId !== historyRequestSeqRef.current) {
              return null;
            }
            markBootstrappedChatConsumed(queryClient, userId, projectId, pendingBootstrap);
            if (bootstrapped?.chatReady) {
              historyMessages = bootstrapped.data.recent_messages || [];
              hasMore = Boolean(bootstrapped.data.has_more_messages);
              hydratedSessionId = bootstrapped.data.chat_session?.id ?? null;
              loadedLimit = Math.max(BOOTSTRAP_DEFAULT_CHAT_LIMIT, historyMessages.length);
            }
          }
        }

        if (historyMessages === null) {
          historyMessages = await getRecentMessages(projectId, 50);
          hasMore = historyMessages.length >= 50;
          loadedLimit = 50;
        }

        if (currentProjectIdRef.current !== projectId || requestId !== historyRequestSeqRef.current) {
          return null;
        }

        const loadedMessages = mapBackendHistoryMessages(historyMessages, t);

        setMessages(loadedMessages);
        historyLimitRef.current = loadedLimit;
        setHasMoreHistory(hasMore);
        sessionRecoveryBlockedRef.current = null;
        setSessionRecoveryError(null);
        if (recoverSession) {
          currentAgentSessionIdRef.current = historyMessages.at(-1)?.session_id ?? null;
        } else {
          currentAgentSessionIdRef.current =
            hydratedSessionId ?? historyMessages.at(-1)?.session_id ?? null;
        }
        return loadedMessages;
      } catch (err) {
        logger.error("Failed to load chat history:", err);
        if (currentProjectIdRef.current !== projectId || requestId !== historyRequestSeqRef.current) {
          return null;
        }
        setMessages([]);
        setHasMoreHistory(false);
        onResetScrollForError();
        setFeedbackPendingMessageId(null);
        setSessionRecoveryError(projectId);
        setSuggestionDisplayState("ready");
        return null;
      } finally {
        if (requestId === historyRequestSeqRef.current && currentProjectIdRef.current === projectId) {
          setIsLoadingHistory(false);
        }
      }
    },
    [
      currentAgentSessionIdRef,
      currentProjectIdRef,
      onResetScrollForError,
      queryClient,
      setMessages,
      setSuggestionDisplayState,
      t,
      userId,
    ],
  );

  const loadEarlierMessages = useCallback(
    async (params: {
      isHistoryLoadBlockedRef: MutableRefObject<boolean>;
      sendLockRef: MutableRefObject<boolean>;
      newChatAfterStopRef: MutableRefObject<boolean>;
      messagesScrollContainerRef: MutableRefObject<HTMLDivElement | null>;
      shouldAutoScrollRef: MutableRefObject<boolean>;
    }) => {
      const projectId = currentProjectIdRef.current;
      if (
        !projectId ||
        isLoadingMoreHistory ||
        isLoadingHistory ||
        params.isHistoryLoadBlockedRef.current ||
        params.sendLockRef.current ||
        creatingSessionRef.current !== null ||
        params.newChatAfterStopRef.current
      ) {
        return;
      }
      const targetLimit = historyLimitRef.current < 50 ? 50 : 100;
      const fetchLimit = targetLimit < 100 ? targetLimit + 1 : 100;
      const requestId = ++historyRequestSeqRef.current;
      const container = params.messagesScrollContainerRef.current;
      const prevScrollHeight = container?.scrollHeight ?? 0;
      const prevScrollTop = container?.scrollTop ?? 0;

      setIsLoadingMoreHistory(true);
      try {
        const olderMessages = await getRecentMessages(projectId, fetchLimit);
        if (
          currentProjectIdRef.current !== projectId ||
          requestId !== historyRequestSeqRef.current ||
          params.isHistoryLoadBlockedRef.current ||
          params.sendLockRef.current ||
          creatingSessionRef.current !== null ||
          params.newChatAfterStopRef.current
        ) {
          return;
        }
        const hasMore = targetLimit < 100 && olderMessages.length > targetLimit;
        const displayedMessages = hasMore
          ? olderMessages.slice(olderMessages.length - targetLimit)
          : olderMessages;
        params.shouldAutoScrollRef.current = false;
        const mappedEarlier = mapBackendHistoryMessages(displayedMessages, t);
        setMessages((prev) => mergeEarlierHistoryMessages(mappedEarlier, prev));
        historyLimitRef.current = targetLimit;
        setHasMoreHistory(hasMore);
        if (container) {
          requestAnimationFrame(() => {
            const delta = container.scrollHeight - prevScrollHeight;
            if (delta > 0) {
              container.scrollTop = prevScrollTop + delta;
            }
          });
        }
      } catch (err) {
        logger.error("Failed to load earlier chat history:", err);
      } finally {
        if (currentProjectIdRef.current === projectId && requestId === historyRequestSeqRef.current) {
          setIsLoadingMoreHistory(false);
        }
      }
    },
    [currentProjectIdRef, isLoadingHistory, isLoadingMoreHistory, setMessages, t],
  );

  const handleSubmitFeedback = useCallback(
    async (message: Message, vote: MessageFeedbackVote) => {
      const backendMessageId = message.backendMessageId ?? null;
      if (!backendMessageId) {
        toast.error(t("chat:feedback.unsupported"));
        return;
      }

      setFeedbackPendingMessageId(message.id);
      try {
        const response = await submitMessageFeedback(backendMessageId, { vote });
        const normalizedFeedback = normalizeFeedbackFromResponse(
          response.feedback,
          vote,
          response.updated_at,
        );

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
    },
    [setMessages, t],
  );

  const runNewSession = useCallback(
    async (params: {
      projectId: string | null;
      setStarterProjectId: Dispatch<SetStateAction<string | null>>;
      resetSuggestionsForNewRound: () => void;
      resetPendingContextClears: () => void;
      resetScrollState: () => void;
      clearStreamItems: () => void;
      clearMatchedSkills: () => void;
      resetStream: () => void;
      clearQuotaAndStop: () => void;
      requestInitialSuggestions: (projectId: string, sourceMessages: Message[]) => Promise<void>;
    }) => {
      const { projectId } = params;
      if (!projectId || creatingSessionRef.current !== null) return;
      const requestSeq = ++newSessionRequestSeqRef.current;
      creatingSessionRef.current = requestSeq;
      setIsCreatingSession(true);
      params.setStarterProjectId(null);
      params.resetSuggestionsForNewRound();
      const ownsRequest = () =>
        currentProjectIdRef.current === projectId && requestSeq === newSessionRequestSeqRef.current;

      try {
        const created = await createNewSession(projectId);
        if (!ownsRequest()) return;
        params.resetPendingContextClears();
        currentAgentSessionIdRef.current = created.id;
        sessionRecoveryBlockedRef.current = null;
        setSessionRecoveryError(null);
        historyRequestSeqRef.current += 1;
        setIsLoadingHistory(false);
        setHasMoreHistory(false);
        setIsLoadingMoreHistory(false);
        setMessages([]);
        params.resetScrollState();
        setFeedbackPendingMessageId(null);
        params.clearStreamItems();
        params.clearMatchedSkills();
        params.resetStream();
        params.clearQuotaAndStop();
        // Suggestions cannot turn a committed session into a creation failure.
        void params.requestInitialSuggestions(projectId, []).catch((error) => {
          logger.error("Failed to load new-session suggestions:", error);
          if (ownsRequest()) setSuggestionDisplayState("fallback");
        });
      } catch (err) {
        logger.error("Failed to create new session:", err);
        if (!ownsRequest()) return;
        currentAgentSessionIdRef.current = null;
        params.resetStream();
        params.clearStreamItems();
        params.clearMatchedSkills();
        setSuggestionDisplayState("fallback");
        // A failed response may follow a committed creation. Read the actual
        // active history; never replay the POST or guess that the old chat won.
        const recoveredMessages = await loadChatHistory(projectId, true);
        if (recoveredMessages !== null && ownsRequest()) {
          toast.error(t("chat:panel.newSessionFailed"));
        }
      } finally {
        if (ownsRequest() && creatingSessionRef.current === requestSeq) {
          creatingSessionRef.current = null;
          setIsCreatingSession(false);
        }
      }
    },
    [
      currentAgentSessionIdRef,
      currentProjectIdRef,
      loadChatHistory,
      setMessages,
      setSuggestionDisplayState,
      t,
    ],
  );

  return {
    isLoadingHistory,
    hasMoreHistory,
    setHasMoreHistory,
    isLoadingMoreHistory,
    setIsLoadingMoreHistory,
    feedbackPendingMessageId,
    setFeedbackPendingMessageId,
    isCreatingSession,
    setIsCreatingSession,
    sessionRecoveryError,
    setSessionRecoveryError,
    historyRequestSeqRef,
    newSessionRequestSeqRef,
    creatingSessionRef,
    sessionRecoveryBlockedRef,
    lastLoadedProjectRef,
    loadChatHistory,
    hydrateAssistantBackendMessage,
    loadEarlierMessages,
    handleSubmitFeedback,
    runNewSession,
  };
}
