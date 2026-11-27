/**
 * React hook for streaming agent requests.
 *
 * Manages SSE connection lifecycle, epoch/project-switch cancellation fencing,
 * server stop grace period (`stop` / `whenStreamClosed`), steering session guard,
 * single-shot completion/partial finalization, and delegates segment batching to
 * `useAgentStreamSegments` and SSE callback wiring to `buildAgentStreamCallbacks`.
 *
 * @module hooks/useAgentStream
 */

import { useState, useCallback, useEffect, useRef } from "react";
import {
  sendSteeringRequest,
  stopAgentRun,
  streamAgentRequest,
} from "../lib/agentApi";
import type { AgentRequest, AgentStreamState } from "../types";
import {
  STOP_GRACE_MS,
  STREAM_CONTENT_FLUSH_INTERVAL_MS,
  type UseAgentStreamOptions,
  type UseAgentStreamReturn,
} from "./streaming/types";
import {
  buildAgentStreamCallbacks,
  type StreamClosedHandle,
} from "./streaming/buildAgentStreamCallbacks";
import { useAgentStreamSegments } from "./streaming/useAgentStreamSegments";

export { STOP_GRACE_MS } from "./streaming/types";
export type {
  MessageSegment,
  StreamCompletionMeta,
  UseAgentStreamOptions,
  UseAgentStreamReturn,
} from "./streaming/types";

const initialState: AgentStreamState = {
  isStreaming: false,
  isThinking: false,
  content: "",
  error: null,
  errorCode: null,
  retryable: false,
};

export function useAgentStream(
  projectId: string,
  options: UseAgentStreamOptions = {},
): UseAgentStreamReturn {
  const [state, setState] = useState<AgentStreamState>(initialState);
  const abortControllerRef = useRef<AbortController | null>(null);

  // Track if onComplete has been called (prevent duplicate calls)
  const onCompleteCalledRef = useRef<boolean>(false);

  // Session and parallel execution refs
  const sessionIdRef = useRef<string | null>(null);
  // 「本轮流真正拿到过 session_started」的会话 id。
  //
  // sessionIdRef 会跨轮保留（用于把 session_id 带进下一次请求实现会话延续），
  // 但 steering 只能投递给**当前正在跑**的那一轮：startStream 是同步把
  // isStreaming 置 true 的，而后端要到注册 steering 队列之后才会发 session_started。
  // 这段窗口里若拿上一轮的陈旧 session id 去 /agent/steer，必然 404。
  // 所以 canSteer 与 sendSteeringMessage 一律以本 ref 为准。
  const activeStreamSessionIdRef = useRef<string | null>(null);
  const streamEpochRef = useRef(0);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const currentProjectIdRef = useRef(projectId);
  const lastProjectIdRef = useRef(projectId);
  // X-Agent-Run-ID of the in-flight stream; needed to ask the server to stop it.
  const runIdRef = useRef<string | null>(null);
  const stopRequestedRef = useRef(false);
  const stopFallbackTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [stopRequested, setStopRequested] = useState(false);
  // The in-flight request's connection: settled when the server closes it or we drop it.
  const streamClosedRef = useRef<StreamClosedHandle | null>(null);

  /** The connection was dropped on our side: nothing more will arrive from it. */
  const settleStreamClosed = useCallback(() => {
    const closed = streamClosedRef.current;
    streamClosedRef.current = null;
    closed?.settle();
  }, []);

  useEffect(() => {
    currentProjectIdRef.current = projectId;
  }, [projectId]);

  const {
    contentFlushIntervalMs,
    onStart,
    onContext: onContextOption,
    onThinking: onThinkingOption,
    onThinkingContent: onThinkingContentOption,
    onSegmentStart,
    onSegmentUpdate,
    onSegmentUpdateToolCalls,
    onSegmentEnd,
    onContent,
    onComplete,
    onError,
    onToolCall,
    onToolResult,
    onFileCreated,
    onFileContent,
    onFileContentEnd,
    onFileEditStart,
    onFileEditApplied,
    onFileEditEnd,
    onSkillMatched,
    onIterationExhausted,
    onWorkflowStopped,
    onSessionStarted,
    onSteeringReceived,
    onHistoryCompacted,
    onQuotaRefunded,
  } = options;

  const flushIntervalMs =
    Number.isFinite(contentFlushIntervalMs)
      ? Math.max(0, Math.floor(contentFlushIntervalMs as number))
      : STREAM_CONTENT_FLUSH_INTERVAL_MS;

  const handleContentFlushed = useCallback(
    (chunk: string, fullContent: string) => {
      setState((prev) => ({
        ...prev,
        isThinking: false,
        content: fullContent,
      }));
      onContent?.(chunk, fullContent);
    },
    [onContent],
  );

  const {
    segmentsRef,
    clearFlushTimer,
    flushPendingContent,
    clearContentBuffers,
    resetSegments,
    startContentSegment,
    appendContentText,
    markCurrentContentSegmentDone,
    finishCurrentContentSegment,
    recordToolCall,
    recordToolResult,
  } = useAgentStreamSegments({
    flushIntervalMs,
    onSegmentStart,
    onSegmentUpdate,
    onSegmentUpdateToolCalls,
    onSegmentEnd,
    onContentFlushed: handleContentFlushed,
  });

  // Cleanup on unmount: abort the in-flight SSE stream and invalidate the epoch
  // so no late callbacks fire, and clear pending timers.
  useEffect(() => {
    return () => {
      streamEpochRef.current += 1;
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
        abortControllerRef.current = null;
      }
      settleStreamClosed();
      if (stopFallbackTimerRef.current) {
        clearTimeout(stopFallbackTimerRef.current);
        stopFallbackTimerRef.current = null;
      }
      clearFlushTimer();
    };
  }, [clearFlushTimer, settleStreamClosed]);

  const clearError = useCallback(() => {
    setState((prev) =>
      prev.error === null && prev.errorCode == null && !prev.retryable
        ? prev
        : { ...prev, error: null, errorCode: null, retryable: false },
    );
  }, []);

  // Prevent session_id reuse across different projects.
  useEffect(() => {
    if (lastProjectIdRef.current === projectId) {
      return;
    }

    lastProjectIdRef.current = projectId;
    streamEpochRef.current += 1;
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    settleStreamClosed();
    clearContentBuffers();
    sessionIdRef.current = null;
    activeStreamSessionIdRef.current = null;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- project switch synchronously aborts the active stream and resets stream/session state.
    setSessionId(null);
    setState((prev) => ({
      ...prev,
      isStreaming: false,
      isThinking: false,
    }));
  }, [projectId, clearContentBuffers, settleStreamClosed]);

  /**
   * Send a steering message to the active session.
   */
  const sendSteeringMessage = useCallback(async (message: string) => {
    // 只认本轮流已确认的 session id：用陈旧 id 投递必然 404，
    // 而调用方（MessageInput）会因为「已发送」而清空输入框，造成用户输入丢失。
    const currentSessionId = activeStreamSessionIdRef.current;
    if (!currentSessionId) {
      throw new Error("No active session for steering");
    }

    await sendSteeringRequest(currentSessionId, message);
  }, []);

  /**
   * Reset state to initial values.
   */
  const reset = useCallback(() => {
    streamEpochRef.current += 1;
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    settleStreamClosed();
    resetSegments();
    setState(initialState);
    sessionIdRef.current = null;
    activeStreamSessionIdRef.current = null;
    setSessionId(null);
    onCompleteCalledRef.current = false;
  }, [resetSegments, settleStreamClosed]);

  /**
   * 非正常终止的统一收尾通道（取消 / SSE error / 反向代理掐断空闲连接）。
   * 复用 onCompleteCalledRef，保证一轮流至多提交一次（done 已提交过就不再补发）。
   */
  const finalizeStream = useCallback(() => {
    if (onCompleteCalledRef.current) return;
    onCompleteCalledRef.current = true;
    onComplete?.(segmentsRef.current, {
      partial: true,
      sessionId: activeStreamSessionIdRef.current ?? undefined,
    });
  }, [onComplete, segmentsRef]);

  /**
   * Cancel ongoing stream.
   */
  const cancel = useCallback(() => {
    streamEpochRef.current += 1;
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    settleStreamClosed();
    if (stopFallbackTimerRef.current) {
      clearTimeout(stopFallbackTimerRef.current);
      stopFallbackTimerRef.current = null;
    }
    setStopRequested(false);
    // Flush what we already received so the UI doesn't "lose" the last chunk.
    flushPendingContent();
    clearFlushTimer();
    finishCurrentContentSegment();

    setState((prev) => ({
      ...prev,
      isStreaming: false,
      isThinking: false,
    }));

    // abort 之后 fetch 侧只会抛 AbortError 并被忽略，不会再有任何回调，
    // 所以必须在这里主动补发终止回调（不经过 isStaleEvent，epoch 已自增也不受影响）。
    finalizeStream();
  }, [
    clearFlushTimer,
    finalizeStream,
    finishCurrentContentSegment,
    flushPendingContent,
    settleStreamClosed,
  ]);

  /**
   * Author pressed stop. The server ends the run on the open stream (stop card +
   * done, and quota_refunded when nothing was produced), so keep reading it; only
   * drop the connection when the stop request fails or the server does not wrap
   * up within STOP_GRACE_MS. A second press while stopping drops it right away.
   */
  const stop = useCallback(async (options?: { keepFileIds?: string[] }) => {
    // The round already ended (done arrived, e.g. while the editor was saving before the
    // stop): there is nothing to stop. Asking /stop or arming the fallback would only
    // cancel the finished stream and drop the frames that still follow done.
    if (onCompleteCalledRef.current) return;
    const runId = runIdRef.current;
    if (!runId || stopRequestedRef.current) {
      cancel();
      return;
    }
    stopRequestedRef.current = true;
    setStopRequested(true);
    const epoch = streamEpochRef.current;
    const dropConnection = () => {
      if (streamEpochRef.current === epoch) cancel();
    };
    stopFallbackTimerRef.current = setTimeout(dropConnection, STOP_GRACE_MS);
    const recorded = await stopAgentRun(runId, options?.keepFileIds ?? []);
    if (!recorded) dropConnection();
  }, [cancel]);

  const whenStreamClosed = useCallback((timeoutMs: number = STOP_GRACE_MS) => {
    const closed = streamClosedRef.current;
    if (!closed) return Promise.resolve();
    return new Promise<void>((resolve) => {
      // Bounded: a server that never closes the stream after done must not hold the caller.
      const timer = setTimeout(resolve, timeoutMs);
      void closed.promise.then(() => {
        clearTimeout(timer);
        resolve();
      });
    });
  }, []);

  // The round ended (done / error / stop card): the stop has been honoured.
  useEffect(() => {
    if (state.isStreaming || !stopFallbackTimerRef.current) return;
    clearTimeout(stopFallbackTimerRef.current);
    stopFallbackTimerRef.current = null;
  }, [state.isStreaming]);

  /**
   * Start a streaming request.
   */
  const startStream = useCallback(
    (request: Omit<AgentRequest, "project_id">) => {
      const streamProjectId = projectId;
      const streamEpoch = ++streamEpochRef.current;
      currentProjectIdRef.current = streamProjectId;

      // Cancel any existing stream
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
      settleStreamClosed();
      let settleClosed: () => void = () => {};
      const closed: StreamClosedHandle = {
        promise: new Promise<void>((resolve) => { settleClosed = resolve; }),
        settle: () => settleClosed(),
      };
      streamClosedRef.current = closed;
      runIdRef.current = null;
      stopRequestedRef.current = false;
      if (stopFallbackTimerRef.current) {
        clearTimeout(stopFallbackTimerRef.current);
        stopFallbackTimerRef.current = null;
      }
      setStopRequested(false);

      resetSegments();
      setState({
        ...initialState,
        isStreaming: true,
        isThinking: true,
      });
      onCompleteCalledRef.current = false;
      // 本轮的 session 尚未确认：在收到 session_started 之前不允许 steering，
      // 否则会拿上一轮的陈旧 session id 去请求并稳定 404。
      // 注意 sessionIdRef 不能清（它负责把 session_id 带进请求实现会话延续）。
      activeStreamSessionIdRef.current = null;
      setSessionId(null);

      onStart?.();

      const isStaleEvent = () =>
        streamEpoch !== streamEpochRef.current
        || currentProjectIdRef.current !== streamProjectId;

      abortControllerRef.current = streamAgentRequest(
        {
          project_id: streamProjectId,
          message: request.message,
          session_id: request.session_id ?? sessionIdRef.current ?? undefined,
          selected_text: request.selected_text,
          ui_action: request.ui_action,
          metadata: request.metadata,
          selected_skill_ids: request.selected_skill_ids,
        },
        buildAgentStreamCallbacks({
          isStaleEvent,
          closed,
          streamClosedRef,
          runIdRef,
          sessionIdRef,
          activeStreamSessionIdRef,
          onCompleteCalledRef,
          setSessionId,
          setState,
          segments: {
            segmentsRef,
            flushPendingContent,
            startContentSegment,
            appendContentText,
            markCurrentContentSegmentDone,
            finishCurrentContentSegment,
            recordToolCall,
            recordToolResult,
          },
          finalizeStream,
          callbacks: {
            onContext: onContextOption,
            onThinking: onThinkingOption,
            onThinkingContent: onThinkingContentOption,
            onComplete,
            onError,
            onToolCall,
            onToolResult,
            onFileCreated,
            onFileContent,
            onFileContentEnd,
            onFileEditStart,
            onFileEditApplied,
            onFileEditEnd,
            onSkillMatched,
            onIterationExhausted,
            onWorkflowStopped,
            onSessionStarted,
            onSteeringReceived,
            onHistoryCompacted,
            onQuotaRefunded,
          },
        }),
      );
    },
    [
      projectId,
      onStart,
      onComplete,
      onError,
      onToolCall,
      onToolResult,
      onFileCreated,
      onFileContent,
      onFileContentEnd,
      onFileEditStart,
      onFileEditApplied,
      onFileEditEnd,
      onSkillMatched,
      onIterationExhausted,
      onWorkflowStopped,
      onSessionStarted,
      onSteeringReceived,
      onHistoryCompacted,
      onQuotaRefunded,
      onThinkingOption,
      onThinkingContentOption,
      onContextOption,
      appendContentText,
      finalizeStream,
      finishCurrentContentSegment,
      flushPendingContent,
      markCurrentContentSegmentDone,
      recordToolCall,
      recordToolResult,
      resetSegments,
      segmentsRef,
      settleStreamClosed,
      startContentSegment,
    ],
  );

  return {
    state,
    startStream,
    stop,
    isStopping: stopRequested && state.isStreaming,
    reset,
    whenStreamClosed,
    isStreaming: state.isStreaming,
    isThinking: state.isThinking,
    error: state.error,
    errorCode: state.errorCode ?? null,
    clearError,
    retryable: state.retryable,
    sessionId,
    sendSteeringMessage,
  };
}
