/**
 * SSE event callback builder for useAgentStream's `streamAgentRequest` call.
 *
 * Bridges low-level SSE callbacks from `agentApi.streamAgentRequest` into
 * epoch-guarded React state updates, segment state machine operations, and
 * caller hooks.
 *
 * @module hooks/streaming/buildAgentStreamCallbacks
 */

import type { Dispatch, MutableRefObject, SetStateAction } from "react";
import type { streamAgentRequest } from "../../lib/agentApi";
import type { AgentStreamState } from "../../types";
import { buildCompletionMeta } from "./completionMeta";
import type { UseAgentStreamOptions } from "./types";
import type { UseAgentStreamSegmentsReturn } from "./useAgentStreamSegments";

export interface StreamClosedHandle {
  promise: Promise<void>;
  settle: () => void;
}

export interface BuildAgentStreamCallbacksParams {
  isStaleEvent: () => boolean;
  closed: StreamClosedHandle;
  streamClosedRef: MutableRefObject<StreamClosedHandle | null>;
  runIdRef: MutableRefObject<string | null>;
  sessionIdRef: MutableRefObject<string | null>;
  activeStreamSessionIdRef: MutableRefObject<string | null>;
  onCompleteCalledRef: MutableRefObject<boolean>;
  setSessionId: Dispatch<SetStateAction<string | null>>;
  setState: Dispatch<SetStateAction<AgentStreamState>>;
  segments: Pick<
    UseAgentStreamSegmentsReturn,
    | "segmentsRef"
    | "flushPendingContent"
    | "startContentSegment"
    | "appendContentText"
    | "markCurrentContentSegmentDone"
    | "finishCurrentContentSegment"
    | "recordToolCall"
    | "recordToolResult"
  >;
  finalizeStream: () => void;
  callbacks: Pick<
    UseAgentStreamOptions,
    | "onContext"
    | "onThinking"
    | "onThinkingContent"
    | "onComplete"
    | "onError"
    | "onToolCall"
    | "onToolResult"
    | "onFileCreated"
    | "onFileContent"
    | "onFileContentEnd"
    | "onFileEditStart"
    | "onFileEditApplied"
    | "onFileEditEnd"
    | "onSkillMatched"
    | "onIterationExhausted"
    | "onWorkflowStopped"
    | "onSessionStarted"
    | "onSteeringReceived"
    | "onHistoryCompacted"
    | "onQuotaRefunded"
  >;
}

export function buildAgentStreamCallbacks({
  isStaleEvent,
  closed,
  streamClosedRef,
  runIdRef,
  sessionIdRef,
  activeStreamSessionIdRef,
  onCompleteCalledRef,
  setSessionId,
  setState,
  segments,
  finalizeStream,
  callbacks,
}: BuildAgentStreamCallbacksParams): Parameters<typeof streamAgentRequest>[1] {
  return {
    onRunStarted: (agentRunId) => {
      if (isStaleEvent()) return;
      runIdRef.current = agentRunId;
    },

    // Not epoch-gated: a dropped earlier stream only settles its own connection.
    onClosed: () => {
      if (streamClosedRef.current === closed) streamClosedRef.current = null;
      closed.settle();
    },

    onThinking: (message, step) => {
      if (isStaleEvent()) return;
      setState((prev) => ({
        ...prev,
        isThinking: true,
      }));
      callbacks.onThinking?.(message, step);
    },

    onThinkingContent: (content, isComplete) => {
      if (isStaleEvent()) return;
      callbacks.onThinkingContent?.(content, isComplete ?? false);
    },

    onContext: (items, tokenCount) => {
      if (isStaleEvent()) return;
      callbacks.onContext?.(items, tokenCount);
    },

    onContentStart: () => {
      if (isStaleEvent()) return;
      segments.flushPendingContent();
      setState((prev) => ({
        ...prev,
        isThinking: false,
      }));
      segments.startContentSegment();
    },

    onContent: (text) => {
      if (isStaleEvent()) return;
      segments.appendContentText(text, () => {
        setState((prev) => ({
          ...prev,
          isThinking: false,
        }));
      });
    },

    onContentEnd: () => {
      if (isStaleEvent()) return;
      segments.flushPendingContent();
      segments.markCurrentContentSegmentDone();
    },

    onToolCall: (toolName, args, toolUseId) => {
      if (isStaleEvent()) return;
      segments.recordToolCall(toolName, args, toolUseId);
      if (toolUseId) {
        callbacks.onToolCall?.(toolName, args, toolUseId);
      } else {
        callbacks.onToolCall?.(toolName, args);
      }
    },

    onToolResult: (toolName, status, result, error, toolUseId) => {
      if (isStaleEvent()) return;
      segments.recordToolResult(toolName, status, result, error, toolUseId);
      if (toolUseId) {
        callbacks.onToolResult?.(toolName, status, result, error, toolUseId);
      } else {
        callbacks.onToolResult?.(toolName, status, result, error);
      }
    },

    onFileCreated: (fileId, fileType, title) => {
      if (isStaleEvent()) return;
      segments.flushPendingContent();
      callbacks.onFileCreated?.(fileId, fileType, title);
    },

    onFileContent: (fileId, chunk) => {
      if (isStaleEvent()) return;
      callbacks.onFileContent?.(fileId, chunk);
    },

    onFileContentEnd: (fileId) => {
      if (isStaleEvent()) return;
      callbacks.onFileContentEnd?.(fileId);
    },

    onFileEditStart: (fileId, title, totalEdits, fileType) => {
      if (isStaleEvent()) return;
      callbacks.onFileEditStart?.(fileId, title, totalEdits, fileType);
    },

    onFileEditApplied: (editIndex, op) => {
      if (isStaleEvent()) return;
      callbacks.onFileEditApplied?.(editIndex, op);
    },

    onFileEditEnd: (
      fileId,
      editsApplied,
      newContent,
      originalContent,
      fileType,
      title,
      outcome,
    ) => {
      if (isStaleEvent()) return;
      callbacks.onFileEditEnd?.(
        fileId,
        editsApplied,
        newContent,
        originalContent,
        fileType,
        title,
        outcome,
      );
    },

    onSkillMatched: (skillId, skillName, matchedTrigger) => {
      if (isStaleEvent()) return;
      callbacks.onSkillMatched?.(skillId, skillName, matchedTrigger);
    },

    onIterationExhausted: (layer, iterationsUsed, maxIterations, reason, lastAgent, limit, files) => {
      if (isStaleEvent()) return;
      callbacks.onIterationExhausted?.(
        layer,
        iterationsUsed,
        maxIterations,
        reason,
        lastAgent,
        limit,
        ...(files ? [files] : []),
      );
    },

    onWorkflowStopped: (data) => {
      if (isStaleEvent()) return;
      segments.flushPendingContent();
      // The author's stop card comes before done (which carries this round's
      // assistant message id and arrives once the server saved it): keep the round
      // open ("正在停止…") until then, so a new send cannot cut the round off midway.
      const keepOpenUntilDone = data.reason === "user_stopped";
      setState((prev) => ({
        ...prev,
        isStreaming: keepOpenUntilDone ? prev.isStreaming : false,
        isThinking: false,
      }));
      callbacks.onWorkflowStopped?.(data);
    },

    onSessionStarted: (session_id) => {
      if (isStaleEvent()) return;
      sessionIdRef.current = session_id;
      // 后端已注册 steering 队列，此刻起本轮才真正可被追加指令。
      activeStreamSessionIdRef.current = session_id;
      setSessionId(session_id);
      callbacks.onSessionStarted?.(session_id);
    },

    onSteeringReceived: (message_id, preview) => {
      if (isStaleEvent()) return;
      callbacks.onSteeringReceived?.(message_id, preview);
    },

    onHistoryCompacted: (summary) => {
      if (isStaleEvent()) return;
      callbacks.onHistoryCompacted?.(summary);
    },

    onQuotaRefunded: (kind, removedFiles) => {
      if (isStaleEvent()) return;
      callbacks.onQuotaRefunded?.(kind, removedFiles);
    },

    onDone: (data) => {
      if (isStaleEvent()) return;
      try {
        segments.flushPendingContent();
        segments.finishCurrentContentSegment();

        setState((prev) => ({
          ...prev,
          isStreaming: false,
          isThinking: false,
        }));

        // 防止重复调用 onComplete（React 并发模式下回调可能多次触发）
        if (!onCompleteCalledRef.current) {
          onCompleteCalledRef.current = true;
          callbacks.onComplete?.(segments.segmentsRef.current, buildCompletionMeta(data));
        }
      } finally {
        // agentApi 不会把 done 之后的回调异常报成失败（不再走 onError 收尾）：
        // 中途抛错时这里也要结束流式状态，并补发一次终止回调释放文件只读锁。
        setState((prev) =>
          prev.isStreaming || prev.isThinking
            ? { ...prev, isStreaming: false, isThinking: false }
            : prev,
        );
        finalizeStream();
      }
    },

    onError: (message, code, retryable) => {
      if (isStaleEvent()) return;
      segments.flushPendingContent();
      segments.finishCurrentContentSegment();

      // 注意：这里**不再**把 onCompleteCalledRef 重置为 false。
      // 该标志由 startStream 在每轮开始时重置即可；在出错时清零会让
      // 下面的 finalizeStream 与已发生过的 done 提交重复叠加。
      setState((prev) => ({
        ...prev,
        isStreaming: false,
        isThinking: false,
        error: message,
        errorCode: code ?? null,
        retryable: retryable === true,
      }));

      if (code !== undefined || retryable !== undefined) {
        callbacks.onError?.(message, code, retryable);
      } else {
        callbacks.onError?.(message);
      }

      // SSE error / 代理掐断（STREAM_CLOSED）同样拿不到 done：
      // 补发终止回调，把已渲染的正文落进 messages 并释放文件流式只读态。
      finalizeStream();
    },
  };
}
