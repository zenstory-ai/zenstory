/**
 * React hook for managing chat streaming UI state.
 *
 * Thin orchestration shell composing:
 * - Throttled StreamRenderItem state (`useThrottledStreamItems`)
 * - Pure timeline reducers (`streamRenderReducer`)
 * - File-tree / editor / autosave-lock sync (`createStreamFileSyncHandlers`)
 *
 * @module hooks/useChatStreaming
 */

import { useState, useRef, useCallback, useEffect } from "react";
import type {
  AgentContextItem,
  IterationExhaustedFiles,
  IterationExhaustedLayer,
  SSEWorkflowStoppedData,
  ToolCall,
} from "../types";
import type { fileApi } from "../lib/api";
import {
  generateUniqueId,
  type EditProgress,
  type MatchedSkill,
  type MessageSegment,
  type StreamCompletionMeta,
  type UseChatStreamingDependencies,
  type UseChatStreamingOptions,
  type UseChatStreamingReturn,
} from "./streaming/types";
import { useThrottledStreamItems } from "./streaming/useThrottledStreamItems";
import {
  appendContextItem,
  appendIterationExhausted,
  appendSegmentStart,
  appendSteeringReceived,
  appendThinkingContent,
  appendThinkingItem,
  appendWorkflowStopped,
  applyHistoryCompacted,
  applySegmentContentUpdate,
  applySegmentToolCallsUpdate,
} from "./streaming/streamRenderReducer";
import { createStreamFileSyncHandlers } from "./streaming/createStreamFileSync";

export { STREAM_UPDATE_THROTTLE_MS, generateUniqueId } from "./streaming/types";
export type {
  StreamRenderItem,
  EditProgress,
  MatchedSkill,
  UseChatStreamingOptions,
  UseChatStreamingDependencies,
  UseChatStreamingReturn,
} from "./streaming/types";

/**
 * React hook for managing chat streaming UI state with throttled updates.
 */
export function useChatStreaming(): UseChatStreamingReturn {
  const {
    streamRenderItems,
    getStreamItemsSnapshot,
    updateStreamItems,
    clearStreamItems,
    forceFlushStreamItems,
  } = useThrottledStreamItems();

  const [editProgress, setEditProgress] = useState<EditProgress | null>(null);
  const [matchedSkills, setMatchedSkills] = useState<MatchedSkill[]>([]);

  const fileContentEndByFileIdRef = useRef<Set<string>>(new Set());
  const fileEditMetaByIdRef = useRef<Map<string, { title: string; fileType?: string }>>(
    new Map(),
  );
  const aiEditingTargetRef = useRef<string | null>(null);
  const pendingFileTreeRefreshRef = useRef(false);
  const roundWroteFilesRef = useRef(false);
  const roundMayDeleteFilesRef = useRef(false);
  const deletionTreeCheckRef = useRef<ReturnType<typeof fileApi.getTree> | null>(null);
  const fileTreeRefreshTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Clear pending debounced file-tree refresh timer on unmount so it cannot
  // fire after the consumer has unmounted.
  useEffect(() => {
    return () => {
      if (fileTreeRefreshTimerRef.current) {
        clearTimeout(fileTreeRefreshTimerRef.current);
        fileTreeRefreshTimerRef.current = null;
      }
    };
  }, []);

  const getStreamCallbacks = useCallback(
    (deps: UseChatStreamingDependencies): Required<UseChatStreamingOptions> => {
      const fileSync = createStreamFileSyncHandlers(
        {
          fileContentEndByFileIdRef,
          fileEditMetaByIdRef,
          aiEditingTargetRef,
          pendingFileTreeRefreshRef,
          roundWroteFilesRef,
          roundMayDeleteFilesRef,
          deletionTreeCheckRef,
          fileTreeRefreshTimerRef,
        },
        deps,
        setEditProgress,
      );

      return {
        onStart: () => {
          setEditProgress(null);
          clearStreamItems();
          setMatchedSkills([]);
          fileSync.resetFileSyncState();
        },

        onContext: (items: AgentContextItem[]) => {
          if (items && items.length > 0) {
            updateStreamItems((prev) => appendContextItem(prev, items));
          }
        },

        onThinking: (message: string, step?: string) => {
          updateStreamItems((prev) => appendThinkingItem(prev, message, step));
        },

        onHistoryCompacted: (summary: string) => {
          updateStreamItems((prev) => applyHistoryCompacted(prev, summary));
        },

        onThinkingContent: (content: string) => {
          updateStreamItems((prev) => appendThinkingContent(prev, content));
        },

        onSegmentStart: (segment: {
          id: string;
          type: string;
          content?: string;
          toolCalls?: ToolCall[];
        }) => {
          if (segment.type === "content" || (segment.type === "tool_calls" && segment.toolCalls)) {
            updateStreamItems((prev) => appendSegmentStart(prev, segment));
          }
        },

        onSegmentUpdate: (segmentId: string, content: string) => {
          updateStreamItems((prev) => applySegmentContentUpdate(prev, segmentId, content));
        },

        onSegmentUpdateToolCalls: (segmentId: string, toolCalls: ToolCall[]) => {
          updateStreamItems((prev) => applySegmentToolCallsUpdate(prev, segmentId, toolCalls));
        },

        onIterationExhausted: (
          layer: IterationExhaustedLayer,
          iterationsUsed: number,
          maxIterations: number,
          reason: string,
          lastAgent?: string,
          limit?: string,
          files?: IterationExhaustedFiles,
        ) => {
          updateStreamItems((prev) =>
            appendIterationExhausted(
              prev,
              layer,
              iterationsUsed,
              maxIterations,
              reason,
              lastAgent,
              limit,
              files,
            ),
          );
        },

        onWorkflowStopped: (data: SSEWorkflowStoppedData) => {
          updateStreamItems((prev) => appendWorkflowStopped(prev, data));
        },

        onToolResult: fileSync.onToolResult,
        onFileCreated: fileSync.onFileCreated,
        onFileContent: fileSync.onFileContent,
        onFileContentEnd: fileSync.onFileContentEnd,
        onFileEditStart: fileSync.onFileEditStart,
        onFileEditApplied: fileSync.onFileEditApplied,
        onFileEditEnd: fileSync.onFileEditEnd,

        onSkillMatched: (
          skillId: string,
          skillName: string,
          matchedTrigger: string,
        ) => {
          // 一轮里可能先后收到多个技能（显式选择的 + 模型 load_skill 的），追加而不是覆盖；
          // 同一技能（按 id，没有 id 时按名称）只保留第一次。
          setMatchedSkills((prev) => {
            const isSame = (skill: MatchedSkill) =>
              skillId ? skill.id === skillId : skill.name === skillName;
            if (prev.some(isSame)) {
              return prev;
            }
            return [
              ...prev,
              { ...(skillId ? { id: skillId } : {}), name: skillName, trigger: matchedTrigger },
            ];
          });
        },

        onSessionStarted: (_sessionId: string) => {
          // No-op: this event is too noisy in the timeline.
          // Session state is tracked internally via useAgentStream.
        },

        onSteeringReceived: (_messageId: string, preview: string) => {
          updateStreamItems((prev) =>
            appendSteeringReceived(prev, deps.t("chat:workflow.steeringReceived", { preview })),
          );
        },

        onComplete: async (
          _segments: MessageSegment[],
          completionMeta?: StreamCompletionMeta,
        ) => {
          // Clear stream render items (both ref and state).
          // Terminal workflow signals (e.g. clarification_needed) are persisted into the final
          // assistant message as "status cards" so they should not remain as standalone
          // streamed items after completion.
          clearStreamItems();
          fileSync.cleanupStreamFileState({
            missingEndReason: "on completion",
            forceRefreshOpenFile: completionMeta?.confirmedFileMutation === true,
          });
        },

        onError: (_message?: string, _code?: string, _retryable?: boolean) => {
          fileSync.cleanupStreamFileState({
            missingEndReason: "after error",
            forceRefreshOpenFile: false,
          });
        },
      };
    },
    [
      clearStreamItems,
      setEditProgress,
      setMatchedSkills,
      updateStreamItems,
    ],
  );

  return {
    streamRenderItems,
    getStreamItemsSnapshot,
    updateStreamItems,
    clearStreamItems,
    forceFlushStreamItems,
    editProgress,
    setEditProgress,
    matchedSkills,
    setMatchedSkills,
    generateUniqueId,
    getStreamCallbacks,
  };
}
