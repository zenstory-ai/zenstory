/**
 * Segment & content-batching state machine for useAgentStream.
 *
 * Manages interleaved `content` and `tool_calls` segments in refs (to avoid stale
 * closures across SSE callbacks) and batches text chunks every ~33ms before flushing
 * to React state.
 *
 * @module hooks/streaming/useAgentStreamSegments
 */

import { useCallback, useRef, type MutableRefObject } from "react";
import type { ToolCall } from "../../types";
import { generateSegmentId, type MessageSegment } from "./types";

export interface UseAgentStreamSegmentsOptions {
  flushIntervalMs: number;
  onSegmentStart?: (segment: MessageSegment) => void;
  onSegmentUpdate?: (segmentId: string, content: string) => void;
  onSegmentUpdateToolCalls?: (segmentId: string, toolCalls: ToolCall[]) => void;
  onSegmentEnd?: (segmentId: string) => void;
  onContentFlushed: (chunk: string, fullContent: string) => void;
}

export interface UseAgentStreamSegmentsReturn {
  segmentsRef: MutableRefObject<MessageSegment[]>;
  clearFlushTimer: () => void;
  flushPendingContent: () => void;
  clearContentBuffers: () => void;
  resetSegments: () => void;
  startContentSegment: () => string;
  appendContentText: (text: string, onFirstSegmentStart: () => void) => void;
  markCurrentContentSegmentDone: () => void;
  finishCurrentContentSegment: () => void;
  recordToolCall: (toolName: string, args: Record<string, unknown>, toolUseId?: string) => void;
  recordToolResult: (
    toolName: string,
    status: string,
    result?: Record<string, unknown>,
    error?: string,
    toolUseId?: string,
  ) => void;
}

export function useAgentStreamSegments({
  flushIntervalMs,
  onSegmentStart,
  onSegmentUpdate,
  onSegmentUpdateToolCalls,
  onSegmentEnd,
  onContentFlushed,
}: UseAgentStreamSegmentsOptions): UseAgentStreamSegmentsReturn {
  // Ref to track current segments value for onComplete callback (avoids stale closure)
  const segmentsRef = useRef<MessageSegment[]>([]);

  const updateSegmentsSync = useCallback((updater: (prev: MessageSegment[]) => MessageSegment[]) => {
    segmentsRef.current = updater(segmentsRef.current);
  }, []);

  // Track current segment for streaming
  const currentSegmentIdRef = useRef<string | null>(null);
  const currentSegmentContentRef = useRef<string>("");
  const pendingToolCallsRef = useRef<ToolCall[]>([]);

  // Streaming content batching (frontend perf)
  const contentRef = useRef<string>("");
  const pendingContentChunkRef = useRef<string>("");
  const flushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Track current tool calls segment (for live updates)
  const currentToolCallsSegmentIdRef = useRef<string | null>(null);

  /** Clear any scheduled content flush timer */
  const clearFlushTimer = useCallback(() => {
    if (flushTimerRef.current) {
      clearTimeout(flushTimerRef.current);
      flushTimerRef.current = null;
    }
  }, []);

  /**
   * Flush pending streamed content into React state / segments.
   * Uses refs to avoid string concatenation on every token causing re-renders.
   */
  const flushPendingContent = useCallback(() => {
    clearFlushTimer();

    const chunk = pendingContentChunkRef.current;
    if (!chunk) return;

    pendingContentChunkRef.current = "";

    const segmentId = currentSegmentIdRef.current;
    const segmentContent = currentSegmentContentRef.current;
    const fullContent = contentRef.current;

    if (segmentId) {
      updateSegmentsSync((prev) =>
        prev.map((s) => (s.id === segmentId ? { ...s, content: segmentContent } : s)),
      );
      onSegmentUpdate?.(segmentId, segmentContent);
    }

    onContentFlushed(chunk, fullContent);
  }, [clearFlushTimer, onContentFlushed, onSegmentUpdate, updateSegmentsSync]);

  /** Schedule a flush if not already scheduled */
  const scheduleContentFlush = useCallback(() => {
    if (flushIntervalMs <= 0) {
      flushPendingContent();
      return;
    }
    if (flushTimerRef.current) return;

    flushTimerRef.current = setTimeout(() => {
      flushTimerRef.current = null;
      flushPendingContent();
    }, flushIntervalMs);
  }, [flushIntervalMs, flushPendingContent]);

  const clearContentBuffers = useCallback(() => {
    clearFlushTimer();
    pendingContentChunkRef.current = "";
    contentRef.current = "";
  }, [clearFlushTimer]);

  const resetSegments = useCallback(() => {
    clearContentBuffers();
    updateSegmentsSync(() => []);
    currentSegmentIdRef.current = null;
    currentToolCallsSegmentIdRef.current = null;
    currentSegmentContentRef.current = "";
    pendingToolCallsRef.current = [];
  }, [clearContentBuffers, updateSegmentsSync]);

  /** Start a new content segment */
  const startContentSegment = useCallback(() => {
    // Ensure any pending content is flushed before ending the previous segment.
    flushPendingContent();

    // End previous segment if exists
    if (currentSegmentIdRef.current) {
      onSegmentEnd?.(currentSegmentIdRef.current);
    }

    const segmentId = generateSegmentId();
    currentSegmentIdRef.current = segmentId;
    currentSegmentContentRef.current = "";

    const segment: MessageSegment = {
      id: segmentId,
      type: "content",
      content: "",
      isStreaming: true,
    };

    updateSegmentsSync((prev) => [...prev, segment]);
    onSegmentStart?.(segment);

    return segmentId;
  }, [flushPendingContent, onSegmentStart, onSegmentEnd, updateSegmentsSync]);

  const appendContentText = useCallback(
    (text: string, onFirstSegmentStart: () => void) => {
      if (!currentSegmentIdRef.current) {
        onFirstSegmentStart();
        startContentSegment();
      }

      // Batch content updates for smoother UI.
      currentSegmentContentRef.current += text;
      contentRef.current += text;
      pendingContentChunkRef.current += text;
      scheduleContentFlush();
    },
    [scheduleContentFlush, startContentSegment],
  );

  /** Mark current segment as not streaming (used by `content_end`, preserves currentSegmentIdRef). */
  const markCurrentContentSegmentDone = useCallback(() => {
    if (currentSegmentIdRef.current) {
      const segmentId = currentSegmentIdRef.current;
      updateSegmentsSync((prev) =>
        prev.map((s) => (s.id === segmentId ? { ...s, isStreaming: false } : s)),
      );
    }
  }, [updateSegmentsSync]);

  /** End current segment and emit `onSegmentEnd` (used by `cancel`, `done`, `error`). */
  const finishCurrentContentSegment = useCallback(() => {
    if (currentSegmentIdRef.current) {
      const segmentId = currentSegmentIdRef.current;
      updateSegmentsSync((prev) =>
        prev.map((s) => (s.id === segmentId ? { ...s, isStreaming: false } : s)),
      );
      onSegmentEnd?.(segmentId);
      currentSegmentIdRef.current = null;
    }
  }, [onSegmentEnd, updateSegmentsSync]);

  /** Create or update tool calls segment (called on each tool call) */
  const upsertToolCallsSegment = useCallback((toolCalls: ToolCall[], isComplete: boolean = false) => {
    // End current content segment if exists and this is the first tool call
    if (!currentToolCallsSegmentIdRef.current && currentSegmentIdRef.current) {
      onSegmentEnd?.(currentSegmentIdRef.current);
      currentSegmentIdRef.current = null;
    }

    if (!currentToolCallsSegmentIdRef.current) {
      // Create new tool calls segment
      const segmentId = generateSegmentId();
      currentToolCallsSegmentIdRef.current = segmentId;

      const segment: MessageSegment = {
        id: segmentId,
        type: "tool_calls",
        toolCalls,
        isStreaming: !isComplete, // streaming until all tool calls complete
      };

      updateSegmentsSync((prev) => [...prev, segment]);
      onSegmentStart?.(segment);
    } else {
      // Update existing segment
      const segmentId = currentToolCallsSegmentIdRef.current;
      updateSegmentsSync((prev) =>
        prev.map((s) => (s.id === segmentId ? { ...s, toolCalls, isStreaming: !isComplete } : s)),
      );
      // Notify ChatPanel to update streamRenderItems
      onSegmentUpdateToolCalls?.(segmentId, toolCalls);
    }

    // If complete, end the segment
    if (isComplete && currentToolCallsSegmentIdRef.current) {
      onSegmentEnd?.(currentToolCallsSegmentIdRef.current);
      currentToolCallsSegmentIdRef.current = null;
    }
  }, [onSegmentStart, onSegmentEnd, onSegmentUpdateToolCalls, updateSegmentsSync]);

  /**
   * Replaces the result of a tool call that already resolved, matched strictly by tool_use_id: in the
   * calls still being collected, otherwise in the tool-calls segment that holds it.
   */
  const replaceResolvedToolResult = useCallback((
    toolUseId: string,
    status: string,
    result?: Record<string, unknown>,
    error?: string,
  ): boolean => {
    const apply = (tc: ToolCall): ToolCall => ({
      ...tc,
      status: status as "success" | "error",
      result,
      error,
    });
    const inCurrent = pendingToolCallsRef.current.some((tc) => tc.id === toolUseId && tc.status !== "pending");
    if (inCurrent) {
      const nextCalls = pendingToolCallsRef.current.map((tc) => (tc.id === toolUseId ? apply(tc) : tc));
      pendingToolCallsRef.current = nextCalls;
      upsertToolCallsSegment(nextCalls, nextCalls.every((tc) => tc.status !== "pending"));
      return true;
    }
    const segment = segmentsRef.current.find(
      (s) => s.type === "tool_calls" && s.toolCalls?.some((tc) => tc.id === toolUseId && tc.status !== "pending"),
    );
    if (!segment?.toolCalls) return false;
    const segmentId = segment.id;
    const nextCalls = segment.toolCalls.map((tc) => (tc.id === toolUseId ? apply(tc) : tc));
    updateSegmentsSync((prev) =>
      prev.map((s) => (s.id === segmentId ? { ...s, toolCalls: nextCalls } : s)),
    );
    onSegmentUpdateToolCalls?.(segmentId, nextCalls);
    return true;
  }, [onSegmentUpdateToolCalls, updateSegmentsSync, upsertToolCallsSegment]);

  const recordToolCall = useCallback(
    (toolName: string, args: Record<string, unknown>, toolUseId?: string) => {
      flushPendingContent();
      const toolCall: ToolCall = {
        id: (toolUseId && toolUseId.trim()) || Date.now().toString(),
        tool_name: toolName,
        arguments: args,
        status: "pending",
      };

      pendingToolCallsRef.current.push(toolCall);

      // Immediately create/update tool calls segment with pending status
      upsertToolCallsSegment([...pendingToolCallsRef.current], false);
    },
    [flushPendingContent, upsertToolCallsSegment],
  );

  const recordToolResult = useCallback(
    (
      toolName: string,
      status: string,
      result?: Record<string, unknown>,
      error?: string,
      toolUseId?: string,
    ) => {
      flushPendingContent();
      const normalizedToolUseId = (toolUseId || "").trim();
      // Update tool call status (prefer tool_use_id match, fallback to first pending by name)
      let updated = false;
      const updatedToolCalls = pendingToolCallsRef.current.map((tc) => {
        const idMatched = normalizedToolUseId ? tc.id === normalizedToolUseId : false;
        const nameMatched =
          !normalizedToolUseId && tc.tool_name === toolName && tc.status === "pending";
        if (!updated && (idMatched || nameMatched) && tc.status === "pending") {
          updated = true;
          return {
            ...tc,
            status: status as "success" | "error",
            result,
            error,
          };
        }
        return tc;
      });
      if (!updated && normalizedToolUseId) {
        // A later result for a call that already resolved (write_chapter re-sends its card once the
        // replacement text is saved or refused): replace that call's result, keyed only by tool_use_id.
        if (replaceResolvedToolResult(normalizedToolUseId, status, result, error)) {
          return;
        }
      }
      pendingToolCallsRef.current = updatedToolCalls;

      // Check if all pending tool calls are done
      const allDone = updatedToolCalls.every((tc) => tc.status !== "pending");

      // Update the tool calls segment with new status
      upsertToolCallsSegment(updatedToolCalls, allDone);

      if (allDone && updatedToolCalls.length > 0) {
        // Clear pending tool calls for next round
        pendingToolCallsRef.current = [];
      }
    },
    [flushPendingContent, replaceResolvedToolResult, upsertToolCallsSegment],
  );

  return {
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
  };
}
