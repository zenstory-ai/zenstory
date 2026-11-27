/**
 * Pure reducers for StreamRenderItem timelines during live chat streaming.
 *
 * @module hooks/streaming/streamRenderReducer
 */

import type {
  AgentContextItem,
  IterationExhaustedFiles,
  IterationExhaustedLayer,
  SSEWorkflowStoppedData,
  ToolCall,
} from "../../types";
import { stripThinkTags } from "../../lib/utils";
import {
  HISTORY_COMPACTION_SKIPPED_STEP,
  HISTORY_COMPACTION_STEP,
} from "../../lib/historyCompaction";
import { generateUniqueId, type StreamRenderItem } from "./types";

/** The live 「正在压缩对话…」 step (not yet the persistent marker). */
function isRunningCompaction(item: StreamRenderItem): boolean {
  return item.type === "history_compaction" && item.compactionState === "running";
}

export function appendContextItem(
  prev: StreamRenderItem[],
  items: AgentContextItem[],
): StreamRenderItem[] {
  return [
    ...prev,
    {
      type: "context",
      id: generateUniqueId("context"),
      items,
      timestamp: new Date(),
    },
  ];
}

export function appendThinkingItem(
  prev: StreamRenderItem[],
  message: string,
  step?: string,
): StreamRenderItem[] {
  if (step === HISTORY_COMPACTION_STEP) {
    // A visible step where the reply will appear; becomes the persistent marker on success.
    return [
      ...prev,
      {
        type: "history_compaction",
        id: generateUniqueId("history-compaction"),
        compactionState: "running",
        transient: true,
        timestamp: new Date(),
      },
    ];
  }
  return [
    // Not compacted: the running step just goes away (the quiet note below is transient too).
    ...(step === HISTORY_COMPACTION_SKIPPED_STEP
      ? prev.filter((item) => !isRunningCompaction(item))
      : prev),
    {
      type: "thinking_status",
      id: generateUniqueId("thinking-status"),
      content: message,
      transient: true,
      timestamp: new Date(),
    },
  ];
}

export function applyHistoryCompacted(
  prev: StreamRenderItem[],
  summary: string,
): StreamRenderItem[] {
  const done: StreamRenderItem = {
    type: "history_compaction",
    id: generateUniqueId("history-compaction"),
    compactionState: "done",
    content: summary,
    timestamp: new Date(),
  };
  const index = prev.findIndex(isRunningCompaction);
  if (index < 0) return [...prev, done];
  const updated = prev.slice();
  updated[index] = {
    ...done,
    id: prev[index].id,
    timestamp: prev[index].timestamp,
  };
  return updated;
}

export function appendThinkingContent(
  prev: StreamRenderItem[],
  content: string,
): StreamRenderItem[] {
  const lastItem = prev[prev.length - 1];

  // Only accumulate when last item is thinking_content
  if (lastItem && lastItem.type === "thinking_content") {
    const updated = prev.slice();
    updated[updated.length - 1] = {
      ...lastItem,
      content: (lastItem.content || "") + content,
    };
    return updated;
  }

  // Create new thinking_content
  return [
    ...prev,
    {
      type: "thinking_content",
      id: generateUniqueId("thinking-content"),
      content,
      timestamp: new Date(),
    },
  ];
}

export function appendSegmentStart(
  prev: StreamRenderItem[],
  segment: { id: string; type: string; content?: string; toolCalls?: ToolCall[] },
): StreamRenderItem[] {
  if (segment.type === "content") {
    return [
      ...prev,
      {
        type: "content",
        id: segment.id,
        content: "",
        timestamp: new Date(),
      },
    ];
  }
  if (segment.type === "tool_calls" && segment.toolCalls) {
    return [
      ...prev,
      {
        type: "tool_calls",
        id: segment.id,
        toolCalls: segment.toolCalls,
        timestamp: new Date(),
      },
    ];
  }
  return prev;
}

export function applySegmentContentUpdate(
  prev: StreamRenderItem[],
  segmentId: string,
  content: string,
): StreamRenderItem[] {
  const cleanContent = stripThinkTags(content);
  const contentIndex = prev.findIndex((item) => item.id === segmentId);

  if (contentIndex >= 0) {
    const updated = prev.slice();
    const currentItem = updated[contentIndex];
    updated[contentIndex] = { ...currentItem, content: cleanContent };
    return updated;
  }

  if (!cleanContent.trim()) return prev;

  // If segment not found, create new one
  return [
    ...prev,
    {
      type: "content",
      id: segmentId,
      content: cleanContent,
      timestamp: new Date(),
    },
  ];
}

export function applySegmentToolCallsUpdate(
  prev: StreamRenderItem[],
  segmentId: string,
  toolCalls: ToolCall[],
): StreamRenderItem[] {
  const toolCallsIndex = prev.findIndex((item) => item.id === segmentId);
  if (toolCallsIndex >= 0) {
    const updated = prev.slice();
    const currentItem = updated[toolCallsIndex];
    if (currentItem.type === "tool_calls") {
      updated[toolCallsIndex] = { ...currentItem, toolCalls };
    }
    return updated;
  }
  return prev;
}

export function appendIterationExhausted(
  prev: StreamRenderItem[],
  layer: IterationExhaustedLayer,
  iterationsUsed: number,
  maxIterations: number,
  reason: string,
  lastAgent?: string,
  limit?: string,
  files?: IterationExhaustedFiles,
): StreamRenderItem[] {
  return [
    ...prev,
    {
      type: "iteration_exhausted",
      id: generateUniqueId("iteration-exhausted"),
      layer,
      iterationsUsed,
      maxIterations,
      reason,
      lastAgent,
      limit,
      ...files,
      timestamp: new Date(),
    },
  ];
}

export function appendWorkflowStopped(
  prev: StreamRenderItem[],
  data: SSEWorkflowStoppedData,
): StreamRenderItem[] {
  return [
    ...prev,
    {
      type: "workflow_stopped",
      id: generateUniqueId("workflow-stopped"),
      reason: data.reason,
      agentType: data.agent_type,
      message: data.message,
      question: data.question,
      context: data.context,
      details: data.details,
      timestamp: new Date(),
    },
  ];
}

export function appendSteeringReceived(
  prev: StreamRenderItem[],
  content: string,
): StreamRenderItem[] {
  return [
    ...prev,
    {
      type: "thinking_status",
      id: generateUniqueId("steering-received"),
      content,
      timestamp: new Date(),
    },
  ];
}
