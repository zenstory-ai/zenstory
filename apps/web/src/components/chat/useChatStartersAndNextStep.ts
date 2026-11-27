/**
 * Project progress tracking, starter cards, NextStepCard gating, and Dashboard
 * 5-minute inspiration auto-send for ChatPanel.
 *
 * @module components/chat/useChatStartersAndNextStep
 */

import { useCallback, useEffect, useRef, useState, type MutableRefObject } from "react";
import type { Message } from "../MessageList";
import type { ManuscriptStarterKind } from "../StarterCards";
import { projectApi } from "../../lib/api";
import {
  EDITOR_CONTENT_SAVED_EVENT,
  type EditorContentSavedDetail,
} from "../../lib/editorSaveTracker";
import { useNextStepDismissal } from "../../hooks/useNextStepDismissal";
import { latestRoundAwaitsReply } from "../../lib/nextStep";
import { hasPendingInspiration } from "../../lib/pendingInspiration";
import { MAX_AGENT_MESSAGE_CHARS } from "../../lib/agentLimits";
import { trackEvent } from "../../lib/analytics";
import { logger } from "../../lib/logger";

const STARTER_HIGHLIGHT_MS = 1600;
/** How long a work's first chat waits for its progress before showing the example starter. */
const STARTER_PROGRESS_WAIT_MS = 1500;

export function useProjectProgressState(currentProjectIdRef: MutableRefObject<string | null>) {
  const [starterProjectId, setStarterProjectId] = useState<string | null>(null);
  const starterProjectIdRef = useRef<string | null>(null);
  useEffect(() => {
    starterProjectIdRef.current = starterProjectId;
  }, [starterProjectId]);

  const [frameworkReadyProjectId, setFrameworkReadyProjectId] = useState<string | null>(null);
  const frameworkReadyRef = useRef<string | null>(null);
  const nextStepDismissal = useNextStepDismissal();
  const isNextStepDismissedRef = useRef(nextStepDismissal.isDismissed);
  useEffect(() => {
    isNextStepDismissedRef.current = nextStepDismissal.isDismissed;
  }, [nextStepDismissal.isDismissed]);

  const nextStepRequestSeqRef = useRef(0);
  const [manuscriptProjectId, setManuscriptProjectId] = useState<string | null>(null);
  const [progressKnownProjectId, setProgressKnownProjectId] = useState<string | null>(null);

  const refreshNextStep = useCallback(
    async (projectId: string) => {
      const seq = ++nextStepRequestSeqRef.current;
      try {
        const [progress] = await projectApi.getProgress(projectId);
        if (currentProjectIdRef.current !== projectId || seq !== nextStepRequestSeqRef.current) {
          return;
        }
        const ready = progress?.framework_ready ? projectId : null;
        frameworkReadyRef.current = ready;
        setFrameworkReadyProjectId(ready);
        setManuscriptProjectId((progress?.written_units ?? 0) > 0 ? projectId : null);
        setProgressKnownProjectId(projectId);
      } catch (error) {
        logger.warn("Failed to load the project's progress:", error);
        if (currentProjectIdRef.current === projectId && seq === nextStepRequestSeqRef.current) {
          setProgressKnownProjectId(projectId);
        }
      }
    },
    [currentProjectIdRef],
  );

  const starterExampleShownRef = useRef<string | null>(null);

  useEffect(() => {
    const onSaved = (event: Event) => {
      const projectId = (event as CustomEvent<EditorContentSavedDetail>).detail?.projectId;
      if (!projectId || projectId !== currentProjectIdRef.current) return;
      const nextStepDue =
        frameworkReadyRef.current === projectId && !isNextStepDismissedRef.current(projectId);
      if (!nextStepDue && starterExampleShownRef.current !== projectId) return;
      void refreshNextStep(projectId);
    };
    window.addEventListener(EDITOR_CONTENT_SAVED_EVENT, onSaved);
    return () => window.removeEventListener(EDITOR_CONTENT_SAVED_EVENT, onSaved);
  }, [currentProjectIdRef, refreshNextStep]);

  const resetProgressForProject = useCallback(() => {
    frameworkReadyRef.current = null;
    setFrameworkReadyProjectId(null);
    setManuscriptProjectId(null);
    setProgressKnownProjectId(null);
    setStarterProjectId(null);
  }, []);

  return {
    starterProjectId,
    setStarterProjectId,
    starterProjectIdRef,
    frameworkReadyProjectId,
    setFrameworkReadyProjectId,
    frameworkReadyRef,
    manuscriptProjectId,
    progressKnownProjectId,
    setProgressKnownProjectId,
    starterExampleShownRef,
    nextStepDismissal,
    refreshNextStep,
    resetProgressForProject,
  };
}

export interface UseChatStartersAndNextStepEffectsOptions {
  currentProjectId: string | null;
  currentProjectIdRef: MutableRefObject<string | null>;
  currentProjectType: string | undefined;
  fileTreeVersion: number;
  messages: Message[];
  isLoadingHistory: boolean;
  isStreaming: boolean;
  isThinking: boolean;
  error: string | null;
  aiSendBlocked: boolean;
  newChatPending: boolean;
  isCreatingSession: boolean;
  sessionRecoveryError: string | null;
  resendAfterStop: boolean;
  lastRetryEntry: unknown;
  chatFocusRequest: number;
  focusChatInput: () => void;
  draft: string;
  saveDraft: (value: string) => boolean;
  handleSendMessage: (
    message: string,
    selectedSkillIds?: string[],
    extraMetadata?: Record<string, unknown>,
    options?: { keepComposer?: boolean },
  ) => Promise<boolean>;
  creatingSessionRef: MutableRefObject<number | null>;
  newChatAfterStopRef: MutableRefObject<boolean>;
  sessionRecoveryBlockedRef: MutableRefObject<string | null>;
  sendLockRef: MutableRefObject<boolean>;
  progressState: ReturnType<typeof useProjectProgressState>;
}

export function useChatStartersAndNextStepEffects({
  currentProjectId,
  currentProjectIdRef,
  currentProjectType,
  fileTreeVersion,
  messages,
  isLoadingHistory,
  isStreaming,
  isThinking,
  error,
  aiSendBlocked,
  newChatPending,
  isCreatingSession,
  sessionRecoveryError,
  resendAfterStop,
  lastRetryEntry,
  chatFocusRequest,
  focusChatInput,
  draft,
  saveDraft,
  handleSendMessage,
  creatingSessionRef,
  newChatAfterStopRef,
  sessionRecoveryBlockedRef,
  sendLockRef,
  progressState,
}: UseChatStartersAndNextStepEffectsOptions) {
  const {
    starterProjectId,
    frameworkReadyProjectId,
    manuscriptProjectId,
    progressKnownProjectId,
    setProgressKnownProjectId,
    starterExampleShownRef,
    nextStepDismissal,
    refreshNextStep,
  } = progressState;

  const [settledChatFocusRequest, setSettledChatFocusRequest] = useState(chatFocusRequest);
  const starterHighlighted = chatFocusRequest !== settledChatFocusRequest;
  useEffect(() => {
    if (!starterHighlighted) return;
    const timer = window.setTimeout(
      () => setSettledChatFocusRequest(chatFocusRequest),
      STARTER_HIGHLIGHT_MS,
    );
    return () => window.clearTimeout(timer);
  }, [chatFocusRequest, starterHighlighted]);

  const hasPendingDashboardIdea =
    Boolean(currentProjectId) &&
    messages.length === 0 &&
    hasPendingInspiration(currentProjectId);

  const starterCardsDue =
    Boolean(currentProjectId) &&
    starterProjectId === currentProjectId &&
    messages.length === 0 &&
    !aiSendBlocked &&
    !hasPendingDashboardIdea &&
    !newChatPending &&
    !isCreatingSession;

  const showStarterCards = starterCardsDue && progressKnownProjectId === currentProjectId;
  const starterHasManuscript = manuscriptProjectId === currentProjectId;

  useEffect(() => {
    starterExampleShownRef.current =
      showStarterCards && !starterHasManuscript ? currentProjectId : null;
  }, [showStarterCards, starterHasManuscript, currentProjectId, starterExampleShownRef]);

  const starterProgressPending = starterCardsDue && progressKnownProjectId !== currentProjectId;
  useEffect(() => {
    if (!starterProgressPending || !currentProjectId) return;
    const projectId = currentProjectId;
    const timer = setTimeout(() => {
      if (currentProjectIdRef.current === projectId) setProgressKnownProjectId(projectId);
    }, STARTER_PROGRESS_WAIT_MS);
    return () => clearTimeout(timer);
  }, [starterProgressPending, currentProjectId, currentProjectIdRef, setProgressKnownProjectId]);

  const starterTreeVersionRef = useRef(fileTreeVersion);
  useEffect(() => {
    if (starterTreeVersionRef.current === fileTreeVersion) return;
    starterTreeVersionRef.current = fileTreeVersion;
    if (showStarterCards && currentProjectId) void refreshNextStep(currentProjectId);
  }, [fileTreeVersion, showStarterCards, currentProjectId, refreshNextStep]);

  const starterViewedRef = useRef<string | null>(null);
  useEffect(() => {
    if (!showStarterCards || !currentProjectId || starterViewedRef.current === currentProjectId) {
      return;
    }
    starterViewedRef.current = currentProjectId;
    trackEvent("starter_viewed", {
      project_id: currentProjectId,
      project_type: currentProjectType,
      variant: starterHasManuscript ? "manuscript" : "example",
    });
  }, [showStarterCards, currentProjectId, currentProjectType, starterHasManuscript]);

  const handleStarterSend = useCallback(
    (message: string) => {
      trackEvent("starter_sent", { project_id: currentProjectId, variant: "example" });
      void handleSendMessage(message, undefined, { entry: "starter_example" });
    },
    [currentProjectId, handleSendMessage],
  );

  const handleStarterManuscriptSend = useCallback(
    (message: string, kind: ManuscriptStarterKind) => {
      trackEvent("starter_sent", { project_id: currentProjectId, variant: "manuscript", kind });
      void handleSendMessage(message, undefined, { entry: "starter_manuscript" });
    },
    [currentProjectId, handleSendMessage],
  );

  const handleStarterEdit = useCallback(
    (message: string) => {
      trackEvent("starter_edited", { project_id: currentProjectId });
      saveDraft(draft.trim() ? `${draft.trimEnd()}\n${message}` : message);
      focusChatInput();
    },
    [currentProjectId, draft, saveDraft, focusChatInput],
  );

  // Check for inspiration from Dashboard and auto-send
  const inspirationProcessedRef = useRef<Set<string>>(new Set());
  const inspirationSendTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (
      !currentProjectId ||
      isLoadingHistory ||
      isStreaming ||
      newChatPending ||
      isCreatingSession ||
      sessionRecoveryError
    ) {
      return;
    }

    if (inspirationProcessedRef.current.has(currentProjectId)) return;

    const inspirationKey = `zenstory_inspiration_${currentProjectId}`;
    const stored = localStorage.getItem(inspirationKey);

    if (stored) {
      try {
        const { content, timestamp } = JSON.parse(stored);

        if (typeof content === "string" && content.length > MAX_AGENT_MESSAGE_CHARS) {
          inspirationProcessedRef.current.add(currentProjectId);
          const saved = saveDraft(
            draft && !draft.includes(content) ? `${draft}\n\n${content}` : draft || content,
          );
          if (saved) localStorage.removeItem(inspirationKey);
          return;
        }

        const isRecent = Date.now() - timestamp < 5 * 60 * 1000;

        if (isRecent && typeof content === "string" && content.trim()) {
          const idea = content;
          const projectIdForSend = currentProjectId;
          if (inspirationSendTimerRef.current) {
            clearTimeout(inspirationSendTimerRef.current);
          }
          inspirationSendTimerRef.current = setTimeout(() => {
            inspirationSendTimerRef.current = null;
            if (
              currentProjectIdRef.current !== projectIdForSend ||
              creatingSessionRef.current !== null ||
              newChatAfterStopRef.current ||
              sessionRecoveryBlockedRef.current ||
              sendLockRef.current
            ) {
              return;
            }
            inspirationProcessedRef.current.add(projectIdForSend);
            localStorage.removeItem(inspirationKey);
            void handleSendMessage(idea, undefined, { entry: "dashboard_idea" });
          }, 500);
        } else {
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
  }, [
    currentProjectId,
    currentProjectIdRef,
    isLoadingHistory,
    isStreaming,
    newChatPending,
    isCreatingSession,
    sessionRecoveryError,
    handleSendMessage,
    draft,
    saveDraft,
    creatingSessionRef,
    newChatAfterStopRef,
    sessionRecoveryBlockedRef,
    sendLockRef,
  ]);

  const resendRepeatsNextStep =
    resendAfterStop && lastRetryEntry === "next_step";

  const showNextStep = Boolean(
    frameworkReadyProjectId &&
      frameworkReadyProjectId === currentProjectId &&
      !nextStepDismissal.isDismissed(frameworkReadyProjectId) &&
      !isStreaming &&
      !isThinking &&
      !error &&
      !aiSendBlocked &&
      !resendRepeatsNextStep &&
      !isLoadingHistory &&
      sessionRecoveryError !== currentProjectId &&
      !latestRoundAwaitsReply(messages),
  );

  return {
    starterHighlighted,
    starterCardsDue,
    showStarterCards,
    starterHasManuscript,
    showNextStep,
    handleStarterSend,
    handleStarterManuscriptSend,
    handleStarterEdit,
  };
}
