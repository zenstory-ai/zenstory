/**
 * Suggestion chip fetching, 10-minute localStorage caching, firstWork gating,
 * post-turn deduplicated refresh, and manual refresh controller for ChatPanel.
 *
 * @module components/chat/useChatSuggestionsController
 */

import { useCallback, useEffect, useRef, useState, type MutableRefObject } from "react";
import type { QueryClient } from "@tanstack/react-query";
import type { Message } from "../MessageList";
import { fetchSuggestions } from "../../lib/agentApi";
import { firstWorkApi, firstWorkQueryKey } from "../../lib/firstWorkApi";
import { needsStaticFirstWorkGuidance } from "../../hooks/useFirstWork";
import { optionsOfferedInReply } from "../../lib/replyOptions";
import { logger } from "../../lib/logger";
import { roundEndedUnfinished } from "./chatHistoryHelpers";

const SUGGESTION_COUNT = 3;
const SUGGESTION_CACHE_KEY_PREFIX = "zenstory_suggestions_cache_";
const SUGGESTION_CACHE_TTL_MS = 10 * 60 * 1000;
const SUGGESTION_INITIAL_TIMEOUT_MS = 1500;

export type SuggestionDisplayState = "loading" | "ready" | "fallback";

interface SuggestionCachePayload {
  suggestions: string[];
  updatedAt: number;
}

export interface UseChatSuggestionsControllerOptions {
  currentProjectId: string | null;
  currentProjectIdRef: MutableRefObject<string | null>;
  userId: string | null;
  queryClient: QueryClient;
  messagesRef: MutableRefObject<Message[]>;
}

export function useChatSuggestionsController({
  currentProjectId,
  currentProjectIdRef,
  userId,
  queryClient,
  messagesRef,
}: UseChatSuggestionsControllerOptions) {
  const [suggestionDisplayState, setSuggestionDisplayState] =
    useState<SuggestionDisplayState>("loading");
  const [aiSuggestions, setAiSuggestions] = useState<string[]>([]);
  const [isRefreshingSuggestions, setIsRefreshingSuggestions] = useState(false);

  const suggestionRequestSeqRef = useRef(0);
  const aiSuggestionsRef = useRef<string[]>([]);
  const suggestionTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastAutoSuggestionKeyRef = useRef<string | null>(null);
  const lastAutoSuggestionStateRef = useRef<"ready" | "fallback">("ready");

  useEffect(() => {
    aiSuggestionsRef.current = aiSuggestions;
  }, [aiSuggestions]);

  // Cleanup suggestion timeout on unmount
  useEffect(() => {
    return () => {
      if (suggestionTimeoutRef.current) {
        clearTimeout(suggestionTimeoutRef.current);
      }
    };
  }, []);

  const toRecentMessages = useCallback(
    (sourceMessages: Message[]) =>
      sourceMessages.slice(-5).map((m) => ({
        role: m.role,
        content: m.content,
      })),
    [],
  );

  const getSuggestionCacheKey = useCallback(
    (projectId: string): string => `${SUGGESTION_CACHE_KEY_PREFIX}${projectId}`,
    [],
  );

  const readCachedSuggestions = useCallback(
    (projectId: string): string[] | null => {
      try {
        const raw = localStorage.getItem(getSuggestionCacheKey(projectId));
        if (!raw) return null;
        const parsed = JSON.parse(raw) as SuggestionCachePayload;
        if (!Array.isArray(parsed.suggestions) || typeof parsed.updatedAt !== "number") {
          return null;
        }
        if (Date.now() - parsed.updatedAt > SUGGESTION_CACHE_TTL_MS) {
          localStorage.removeItem(getSuggestionCacheKey(projectId));
          return null;
        }
        return parsed.suggestions
          .filter((s): s is string => typeof s === "string" && Boolean(s.trim()))
          .slice(0, 3);
      } catch {
        return null;
      }
    },
    [getSuggestionCacheKey],
  );

  const clearSuggestionCache = useCallback(
    (projectId: string) => {
      try {
        localStorage.removeItem(getSuggestionCacheKey(projectId));
      } catch {
        // Ignore cache failures (non-critical UX optimization)
      }
    },
    [getSuggestionCacheKey],
  );

  const writeSuggestionCache = useCallback(
    (projectId: string, suggestions: string[]) => {
      try {
        const payload: SuggestionCachePayload = {
          suggestions: suggestions.slice(0, 3),
          updatedAt: Date.now(),
        };
        localStorage.setItem(getSuggestionCacheKey(projectId), JSON.stringify(payload));
      } catch {
        // Ignore cache write failures (non-critical UX optimization)
      }
    },
    [getSuggestionCacheKey],
  );

  const canRequestSuggestions = useCallback(
    async (projectId: string) => {
      if (!userId) return false;
      try {
        const state = await queryClient.fetchQuery({
          queryKey: firstWorkQueryKey(userId),
          queryFn: firstWorkApi.get,
          retry: false,
          staleTime: 1000,
        });
        return !needsStaticFirstWorkGuidance(state, projectId);
      } catch {
        // Unknown status must not spend an AI request. Author-initiated chat and writing remain available.
        return false;
      }
    },
    [queryClient, userId],
  );

  const fetchAndApplySuggestions = useCallback(
    async (
      projectId: string,
      sourceMessages: Message[],
      options?: { allowFallback?: boolean; lead?: string[]; requestSeq?: number },
    ): Promise<string[] | null> => {
      const requestSeq = options?.requestSeq ?? ++suggestionRequestSeqRef.current;
      const stillCurrent = () =>
        currentProjectIdRef.current === projectId && suggestionRequestSeqRef.current === requestSeq;
      const lead = options?.lead ?? [];
      const keepLead = () => {
        if (!stillCurrent()) return null;
        writeSuggestionCache(projectId, lead);
        return lead;
      };
      if (lead.length > 0) {
        if (!stillCurrent()) return null;
        setAiSuggestions(lead);
        setSuggestionDisplayState("ready");
        if (lead.length >= SUGGESTION_COUNT) return keepLead();
      }
      const allowed = await canRequestSuggestions(projectId);
      if (!stillCurrent()) return null;
      if (!allowed) {
        setAiSuggestions(lead);
        setSuggestionDisplayState("ready");
        return lead;
      }
      const fetched = await fetchSuggestions(
        projectId,
        toRecentMessages(sourceMessages),
        SUGGESTION_COUNT,
      );

      if (!stillCurrent()) {
        return null;
      }

      if (fetched !== null) {
        const suggestions = [
          ...lead,
          ...fetched.filter((item) => !lead.includes(item)),
        ].slice(0, SUGGESTION_COUNT);
        setAiSuggestions(suggestions);
        setSuggestionDisplayState("ready");
        writeSuggestionCache(projectId, suggestions);
        return suggestions;
      }
      if (lead.length > 0) return keepLead();

      if (options?.allowFallback && aiSuggestionsRef.current.length === 0) {
        setSuggestionDisplayState("fallback");
      }
      return null;
    },
    [canRequestSuggestions, currentProjectIdRef, toRecentMessages, writeSuggestionCache],
  );

  const requestInitialSuggestions = useCallback(
    async (projectId: string, sourceMessages: Message[]) => {
      suggestionRequestSeqRef.current += 1;
      const requestSeq = suggestionRequestSeqRef.current;
      const allowed = await canRequestSuggestions(projectId);
      if (currentProjectIdRef.current !== projectId || requestSeq !== suggestionRequestSeqRef.current) {
        return;
      }
      if (!allowed) {
        setAiSuggestions([]);
        setSuggestionDisplayState("ready");
        return;
      }
      const lastLoaded = sourceMessages[sourceMessages.length - 1];
      if (lastLoaded?.role === "assistant" && roundEndedUnfinished(lastLoaded)) {
        clearSuggestionCache(projectId);
        setAiSuggestions([]);
        setSuggestionDisplayState("ready");
        return;
      }
      const cached = readCachedSuggestions(projectId);
      const offered =
        cached === null && lastLoaded?.role === "assistant"
          ? optionsOfferedInReply(lastLoaded.content)
          : null;
      if (offered) {
        await fetchAndApplySuggestions(projectId, sourceMessages, { lead: offered, requestSeq });
        return;
      }

      if (cached !== null) {
        setAiSuggestions(cached);
        setSuggestionDisplayState("ready");
        return;
      }

      setSuggestionDisplayState("loading");
      const fetchPromise = fetchAndApplySuggestions(projectId, sourceMessages, { requestSeq });

      const firstResult = await Promise.race([
        fetchPromise.then((suggestions) => ({ timedOut: false, suggestions })),
        new Promise<{ timedOut: true; suggestions: null }>((resolve) => {
          setTimeout(() => resolve({ timedOut: true, suggestions: null }), SUGGESTION_INITIAL_TIMEOUT_MS);
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
        if (eventual === null && aiSuggestionsRef.current.length === 0) {
          setSuggestionDisplayState("fallback");
        }
        return;
      }

      if (firstResult.suggestions === null && aiSuggestionsRef.current.length === 0) {
        setSuggestionDisplayState("fallback");
      }
    },
    [
      canRequestSuggestions,
      clearSuggestionCache,
      currentProjectIdRef,
      fetchAndApplySuggestions,
      readCachedSuggestions,
    ],
  );

  const schedulePostTurnSuggestions = useCallback(
    (projectId: string | null) => {
      if (suggestionTimeoutRef.current) {
        clearTimeout(suggestionTimeoutRef.current);
      }
      setSuggestionDisplayState("loading");
      const completionRequestSeq = ++suggestionRequestSeqRef.current;
      suggestionTimeoutRef.current = setTimeout(async () => {
        const currentMessages = messagesRef.current;
        if (suggestionRequestSeqRef.current !== completionRequestSeq) return;
        if (projectId) {
          const lastMessage = currentMessages[currentMessages.length - 1];
          const suggestionKey = `${projectId}:${currentMessages.length}:${lastMessage?.id ?? ""}`;
          if (lastAutoSuggestionKeyRef.current === suggestionKey) {
            setSuggestionDisplayState(lastAutoSuggestionStateRef.current);
            return;
          }
          lastAutoSuggestionKeyRef.current = suggestionKey;
          const offered =
            lastMessage?.role === "assistant" ? optionsOfferedInReply(lastMessage.content) : null;
          try {
            const result = await fetchAndApplySuggestions(projectId, currentMessages, {
              allowFallback: true,
              lead: offered ?? undefined,
              requestSeq: completionRequestSeq,
            });
            if (
              currentProjectIdRef.current !== projectId ||
              lastAutoSuggestionKeyRef.current !== suggestionKey ||
              suggestionRequestSeqRef.current !== completionRequestSeq
            ) {
              return;
            }
            const displayState =
              result === null && aiSuggestionsRef.current.length === 0 ? "fallback" : "ready";
            lastAutoSuggestionStateRef.current = displayState;
            setSuggestionDisplayState(displayState);
          } catch (error) {
            if (
              currentProjectIdRef.current !== projectId ||
              lastAutoSuggestionKeyRef.current !== suggestionKey ||
              suggestionRequestSeqRef.current !== completionRequestSeq
            ) {
              return;
            }
            logger.error("Failed to fetch suggestions:", error);
            const displayState = aiSuggestionsRef.current.length === 0 ? "fallback" : "ready";
            lastAutoSuggestionStateRef.current = displayState;
            setSuggestionDisplayState(displayState);
          }
        }
      }, 1500);
    },
    [currentProjectIdRef, fetchAndApplySuggestions, messagesRef],
  );

  const clearPendingSuggestionTimer = useCallback(() => {
    if (suggestionTimeoutRef.current) {
      clearTimeout(suggestionTimeoutRef.current);
      suggestionTimeoutRef.current = null;
    }
  }, []);

  const invalidateInFlightSuggestions = useCallback(() => {
    suggestionRequestSeqRef.current += 1;
  }, []);

  const resetSuggestionsForNewRound = useCallback(() => {
    suggestionRequestSeqRef.current += 1;
    setAiSuggestions([]);
    setSuggestionDisplayState("loading");
    clearPendingSuggestionTimer();
  }, [clearPendingSuggestionTimer]);

  const clearSuggestionsForUnfinishedRound = useCallback(
    (projectId: string | null, shouldClearCache: boolean) => {
      clearPendingSuggestionTimer();
      suggestionRequestSeqRef.current += 1;
      if (shouldClearCache && projectId) clearSuggestionCache(projectId);
      setAiSuggestions([]);
      setSuggestionDisplayState("ready");
    },
    [clearPendingSuggestionTimer, clearSuggestionCache],
  );

  const refreshSuggestionsNow = useCallback(async () => {
    if (!currentProjectId) return;
    if (isRefreshingSuggestions) return;

    clearPendingSuggestionTimer();

    setIsRefreshingSuggestions(true);
    const requestSeq = ++suggestionRequestSeqRef.current;
    try {
      setSuggestionDisplayState("loading");
      await fetchAndApplySuggestions(currentProjectId, messagesRef.current, {
        allowFallback: true,
        requestSeq,
      });
    } catch (error) {
      logger.error("Failed to refresh suggestions:", error);
      if (suggestionRequestSeqRef.current === requestSeq && aiSuggestionsRef.current.length === 0) {
        setSuggestionDisplayState("fallback");
      }
    } finally {
      setIsRefreshingSuggestions(false);
    }
  }, [
    clearPendingSuggestionTimer,
    currentProjectId,
    fetchAndApplySuggestions,
    isRefreshingSuggestions,
    messagesRef,
  ]);

  return {
    aiSuggestions,
    setAiSuggestions,
    suggestionDisplayState,
    setSuggestionDisplayState,
    isRefreshingSuggestions,
    requestInitialSuggestions,
    schedulePostTurnSuggestions,
    refreshSuggestionsNow,
    invalidateInFlightSuggestions,
    resetSuggestionsForNewRound,
    clearSuggestionsForUnfinishedRound,
  };
}
