/**
 * React hook for managing chat streaming UI state.
 *
 * Provides state management for:
 * - Stream render items (real-time display during streaming)
 * - Edit progress tracking
 * - Throttled state updates for performance
 *
 * This hook extracts the streaming UI logic from ChatPanel.tsx,
 * making it reusable and testable in isolation.
 *
 * @module hooks/useChatStreaming
 */

import { useState, useRef, useCallback, useMemo, useEffect } from "react";
import type { Dispatch, SetStateAction } from "react";
import type {
  AgentContextItem,
  ApplyAction,
  FileEditOutcome,
  FileType,
  SSEHandoffData,
  SSERoutingMetadata,
  SSEWorkflowCompleteData,
  SSEWorkflowStoppedData,
  ToolCall,
} from "../types";
import { logger } from "../lib/logger";
import { toast } from "../lib/toast";
import { stripThinkTags } from "../lib/utils";
import { dispatchProjectStatusUpdated } from "../lib/projectStatusEvents";
import { formatHandoffMessage } from "../lib/agentDisplayName";
import type { MessageSegment, StreamCompletionMeta } from "./useAgentStream";

/** Throttle delay for stream render updates in milliseconds */
export const STREAM_UPDATE_THROTTLE_MS = 50;
const FILE_TREE_REFRESH_DEBOUNCE_MS = 180;
const AI_MEMORY_STATUS_FIELDS = new Set([
  "summary",
  "current_phase",
  "writing_style",
  "notes",
]);

/**
 * Creates a throttled version of a function that limits execution rate.
 * Ensures the latest call is always eventually executed.
 *
 * @template T - Function type to throttle
 * @param fn - The function to throttle
 * @param delay - Minimum time in milliseconds between executions
 * @returns Throttled function with a cancel method to clear pending execution
 *
 * @example
 * ```tsx
 * const throttledUpdate = throttle((value: string) => {
 *   console.log('Updated:', value);
 * }, 100);
 *
 * throttledUpdate('a'); // Executes immediately
 * throttledUpdate('b'); // Scheduled for 100ms later
 * throttledUpdate('c'); // Replaces 'b', only 'c' executes
 *
 * // To cancel pending execution:
 * throttledUpdate.cancel();
 * ```
 */
export function throttle<T extends (...args: unknown[]) => void>(
  fn: T,
  delay: number
): T & { cancel: () => void } {
  let lastCall = 0;
  let timeoutId: ReturnType<typeof setTimeout> | null = null;
  let pendingArgs: Parameters<T> | null = null;

  const throttled = ((...args: Parameters<T>) => {
    const now = Date.now();
    const timeSinceLastCall = now - lastCall;

    if (timeSinceLastCall >= delay) {
      lastCall = now;
      fn(...args);
    } else {
      // Store the latest args and schedule a call
      pendingArgs = args;
      if (!timeoutId) {
        timeoutId = setTimeout(() => {
          timeoutId = null;
          if (pendingArgs) {
            lastCall = Date.now();
            fn(...pendingArgs);
            pendingArgs = null;
          }
        }, delay - timeSinceLastCall);
      }
    }
  }) as T & { cancel: () => void };

  throttled.cancel = () => {
    if (timeoutId) {
      clearTimeout(timeoutId);
      timeoutId = null;
    }
    pendingArgs = null;
  };

  return throttled;
}

/**
 * Generates a unique identifier with the given prefix.
 * Combines timestamp and random string to avoid collisions
 * when multiple IDs are generated in rapid succession.
 *
 * @param prefix - Prefix for the ID (e.g., 'context', 'tool-calls')
 * @returns Unique identifier string
 *
 * @example
 * ```tsx
 * const id1 = generateUniqueId('msg'); // 'msg-1708051200000-abc123def'
 * const id2 = generateUniqueId('msg'); // Different ID even if called at same ms
 * ```
 */
export function generateUniqueId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).substring(2, 11)}`;
}

/**
 * Represents a single renderable item in the streaming message display.
 * Items are stored in order received from the server and rendered sequentially.
 */
export interface StreamRenderItem {
  /** Type of the render item determining how it's displayed */
  type:
    | "thinking_status"
    | "thinking_content"
    | "context"
    | "tool_calls"
    | "content"
    | "agent_selected"
    | "iteration_exhausted"
    | "router_thinking"
    | "router_decided"
    | "workflow_stopped"
    | "workflow_complete";
  /** Unique identifier for this item */
  id: string;
  /** Text content for thinking, status, or message items */
  content?: string;
  /** Context items assembled for the AI request */
  items?: AgentContextItem[];
  /** Tool calls to display as result cards */
  toolCalls?: ToolCall[];
  /** Type of the agent handling this workflow */
  agentType?: string;
  /** Human-readable name of the agent */
  agentName?: string;
  /** Current iteration number in multi-step workflows */
  iteration?: number;
  /** Maximum allowed iterations */
  maxIterations?: number;
  /** Number of remaining iterations available */
  remaining?: number;
  /** Layer of the multi-agent workflow (collaboration or tool_call) */
  layer?: "collaboration" | "tool_call";
  /** Number of iterations actually used */
  iterationsUsed?: number;
  /** Reason for stopping or completing the workflow */
  reason?: string;
  /** Name of the last agent that ran */
  lastAgent?: string;
  /** First agent selected by the router */
  initialAgent?: string;
  /** Planned workflow execution path */
  workflowPlan?: string;
  /** List of agents in the planned workflow */
  workflowAgents?: string[];
  /** Structured router metadata */
  routingMetadata?: SSERoutingMetadata;
  /** Message from workflow status events */
  message?: string;
  /** Clarification question (optional) */
  question?: string;
  /** Clarification context (optional) */
  context?: string;
  /** Clarification details list (optional) */
  details?: string[];
  /** Confidence score for router decisions (0-1) */
  confidence?: number;
  /** Structured evaluation payload from workflow stop/complete events */
  evaluation?: {
    complete_score: number;
    clarification_score: number;
    consistency_score: number;
    decision_reason: string;
  };
  /** Timestamp when this item was created */
  timestamp: Date;
}

/**
 * Represents the progress of an AI file edit operation.
 * Used to display real-time progress during multi-step edits.
 */
export interface EditProgress {
  /** ID of the file being edited */
  fileId: string;
  /** Display title of the file */
  title: string;
  /** Total number of edit operations to apply */
  totalEdits: number;
  /** Number of edit operations completed so far */
  completedEdits: number;
  /** Description of the current operation being performed */
  currentOp?: string;
}

/**
 * Represents a matched skill during streaming.
 * Used to display which skills were activated by user input.
 */
export interface MatchedSkill {
  /** Skill id (when the server provides one); used to dedupe repeated matches */
  id?: string;
  /** Display name of the matched skill */
  name: string;
  /** The trigger phrase that activated this skill */
  trigger: string;
}

/**
 * Options for the useChatStreaming hook.
 * All callbacks are optional and will be called at appropriate times during streaming.
 */
export interface UseChatStreamingOptions {
  /** Called when streaming starts - useful for clearing previous state */
  onStart?: () => void;

  // ========================================
  // Context and Thinking callbacks
  // ========================================

  /** Called when context items are received from the agent */
  onContext?: (items: AgentContextItem[]) => void;

  /** Called when AI is thinking (with status message) */
  onThinking?: (message: string) => void;

  /** Called when thinking content arrives */
  onThinkingContent?: (content: string) => void;

  // ========================================
  // Segment callbacks (for stream render items)
  // ========================================

  /** Called when a new segment starts */
  onSegmentStart?: (segment: { id: string; type: string; content?: string; toolCalls?: ToolCall[] }) => void;

  /** Called when segment content updates */
  onSegmentUpdate?: (segmentId: string, content: string) => void;

  /** Called when tool calls segment updates */
  onSegmentUpdateToolCalls?: (segmentId: string, toolCalls: ToolCall[]) => void;

  /** Called when a segment completes */
  onSegmentEnd?: (segmentId: string) => void;

  // ========================================
  // Multi-agent workflow callbacks
  // ========================================

  /** Called when an agent is selected by the router */
  onAgentSelected?: (
    agentType: string,
    agentName: string,
    iteration?: number,
    maxIterations?: number,
    remaining?: number
  ) => void;

  /** Called when iteration limit is exhausted */
  onIterationExhausted?: (
    layer: "collaboration" | "tool_call",
    iterationsUsed: number,
    maxIterations: number,
    reason: string,
    lastAgent?: string
  ) => void;

  /** Called when router starts thinking */
  onRouterThinking?: (message: string) => void;

  /** Called when router makes a decision */
  onRouterDecided?: (
    initialAgent: string,
    workflowPlan: string,
    workflowAgents: string[],
    routingMetadata?: SSERoutingMetadata
  ) => void;

  /** Called when workflow hands off to another agent */
  onHandoff?: (data: SSEHandoffData) => void;

  /** Called when workflow stops for clarification */
  onWorkflowStopped?: (data: SSEWorkflowStoppedData) => void;

  /** Called when workflow completes successfully */
  onWorkflowComplete?: (data: SSEWorkflowCompleteData) => void;

  // ========================================
  // Tool result callbacks
  // ========================================

  /** Called when a tool execution completes */
  onToolResult?: (
    toolName: string,
    status: string,
    result?: Record<string, unknown>,
    error?: string
  ) => void;

  // ========================================
  // File operation callbacks
  // ========================================

  /** Called when a file is created (for auto-select) */
  onFileCreated?: (fileId: string, fileType: string, title: string) => void;

  /** Called when file content chunk is received (streaming) */
  onFileContent?: (fileId: string, chunk: string) => void;

  /** Called when file content streaming ends */
  onFileContentEnd?: (fileId: string) => void;

  /** Called when file editing starts */
  onFileEditStart?: (
    fileId: string,
    title: string,
    totalEdits: number,
    fileType?: string,
  ) => void;

  /** Called when a single edit is applied */
  onFileEditApplied?: (
    fileId: string,
    editIndex: number,
    op: string,
    oldPreview?: string,
    newPreview?: string,
    success?: boolean,
    error?: string
  ) => void;

  /** Called when file editing ends */
  onFileEditEnd?: (
    fileId: string,
    editsApplied: number,
    newLength: number,
    newContent?: string,
    originalContent?: string,
    fileType?: string,
    title?: string,
    outcome?: FileEditOutcome,
  ) => void;

  // ========================================
  // Skill matching callbacks
  // ========================================

  /** Called when a skill is matched */
  onSkillMatched?: (skillId: string, skillName: string, matchedTrigger: string) => void;

  /** Called when multiple skills are matched (multi-skill activation) */
  onSkillsMatched?: (skills: Array<{ id: string; name: string; trigger: string; confidence: number }>) => void;

  /** Called when session starts */
  onSessionStarted?: (sessionId: string) => void;

  /** Called when parallel execution starts */
  onParallelStart?: (
    executionId: string,
    taskCount: number,
    descriptions: string[],
    droppedCount?: number,
  ) => void;

  /** Called when a parallel task starts */
  onParallelTaskStart?: (executionId: string, taskId: string, taskType: string, description: string) => void;

  /** Called when a parallel task completes */
  onParallelTaskEnd?: (executionId: string, taskId: string, status: string, result?: unknown, error?: string) => void;

  /** Called when all parallel tasks complete */
  onParallelEnd?: (executionId: string, total: number, completed: number, failed: number, durationMs: number) => void;

  /** Called when steering message is received */
  onSteeringReceived?: (messageId: string, preview: string) => void;

  // ========================================
  // Error and completion callbacks
  // ========================================

  /**
   * Called when streaming completes successfully.
   * Signature matches useAgentStream's onComplete callback.
   */
  onComplete?: (
    segments: MessageSegment[],
    applyAction: ApplyAction | null,
    completionMeta?: StreamCompletionMeta,
  ) => void | Promise<void>;

  /** Called when an error occurs during streaming */
  onError?: (message?: string, code?: string, retryable?: boolean) => void;
}

/**
 * Dependencies needed by the useChatStreaming hook for side effects.
 * These are passed to avoid tight coupling with specific implementations.
 */
export interface UseChatStreamingDependencies {
  /** Function to trigger file tree refresh */
  triggerFileTreeRefresh: () => void;
  /** Function to trigger editor refresh for a specific file */
  triggerEditorRefresh: (fileId: string) => void;
  /**
   * 标记/清除「AI 正在编辑某个文件」。
   * 编辑器据此在 file_edit_start ~ file_edit_end 之间挂起防抖自动保存，
   * 否则编辑前拍下的整篇快照会在 AI 写完之后发出，把它的改动整篇覆盖掉。
   */
  setAiEditingFileId?: (fileId: string | null) => void;
  /** Function to set the selected item in project context */
  setSelectedItem: (item: { id: string; type: FileType; title: string }) => void;
  /** Returns the latest selected item from project context */
  getCurrentSelectedItem?: () => { id: string; type: FileType; title: string } | null;
  /** Function to append content to a streaming file */
  appendFileContent: (fileId: string, content: string) => void;
  /** Function to mark file streaming as complete */
  finishFileStreaming: (fileId: string) => void;
  /** Function to start file streaming for a file */
  startFileStreaming: (fileId: string) => void;
  /** Current streaming file ID (snapshot, may be stale in long-lived callbacks) */
  streamingFileId?: string | null;
  /** Returns the latest streaming file ID at callback execution time */
  getCurrentStreamingFileId?: () => string | null;
  /** Function to enter diff review mode */
  enterDiffReview: (fileId: string, originalContent: string, newContent: string) => void;
  /** Active UI project ID for snapshot creation */
  activeProjectId: string | null;
  /** Function to create a project snapshot */
  createSnapshot: (
    projectId: string,
    options?: {
      fileId?: string;
      description?: string;
      snapshotType?: string;
    }
  ) => Promise<unknown>;
  /**
   * Returns the author's own wording for the current round (the message that
   * started it). Used to describe the automatic snapshot taken after AI edits.
   */
  getLatestUserRequest?: () => string | null | undefined;
  /** Translation function for generating snapshot descriptions */
  t: (key: string, options?: Record<string, unknown>) => string;
}

/** Maximum characters of the author's request quoted in a snapshot description. */
const SNAPSHOT_REQUEST_PREVIEW_CHARS = 24;

/**
 * One-line preview of the author's request for a snapshot description:
 * line breaks removed, first 24 characters, "…" when cut.
 */
export const buildSnapshotRequestPreview = (request: string | null | undefined): string => {
  // Drop line breaks; keep a space only between two Latin letters/digits so English words don't merge.
  const singleLine = (request ?? "")
    .trim()
    .replace(/([A-Za-z0-9]?)\s*[\r\n]+\s*([A-Za-z0-9]?)/g, (_m, before: string, after: string) =>
      before && after ? `${before} ${after}` : `${before}${after}`,
    );
  const chars = Array.from(singleLine);
  if (chars.length <= SNAPSHOT_REQUEST_PREVIEW_CHARS) return singleLine;
  return `${chars.slice(0, SNAPSHOT_REQUEST_PREVIEW_CHARS).join("")}…`;
};

/**
 * Return type for the useChatStreaming hook.
 */
export interface UseChatStreamingReturn {
  // ========================================
  // Stream render items state
  // ========================================

  /** Current stream render items for display */
  streamRenderItems: StreamRenderItem[];

  /** Read the current unthrottled sequence before completion clears it. */
  getStreamItemsSnapshot: () => StreamRenderItem[];

  /** Add or update stream render items */
  updateStreamItems: (updater: (prev: StreamRenderItem[]) => StreamRenderItem[]) => void;

  /** Clear all stream render items */
  clearStreamItems: () => void;

  /** Force immediate flush of pending state updates */
  forceFlushStreamItems: () => void;

  // ========================================
  // Edit progress state
  // ========================================

  /** Current edit progress, if any */
  editProgress: EditProgress | null;

  /** Set edit progress state */
  setEditProgress: Dispatch<SetStateAction<EditProgress | null>>;

  // ========================================
  // Skill matching state
  // ========================================

  /** Currently matched skills */
  matchedSkills: MatchedSkill[];

  /** Set matched skills */
  setMatchedSkills: Dispatch<SetStateAction<MatchedSkill[]>>;

  // ========================================
  // Utility functions
  // ========================================

  /** Generate a unique ID with the given prefix */
  generateUniqueId: (prefix: string) => string;

  // ========================================
  // Callback creators
  // ========================================

  /** Get callbacks for useAgentStream options */
  getStreamCallbacks: (deps: UseChatStreamingDependencies) => Required<UseChatStreamingOptions>;
}

/**
 * Type alias for UseChatStreamingReturn for convenience.
 * Represents the return type of the useChatStreaming hook.
 */
export type { UseChatStreamingReturn as UseChatStreaming };

/**
 * React hook for managing chat streaming UI state with throttled updates.
 *
 * This hook provides:
 * - Throttled stream render items state management for performance
 * - Edit progress tracking for AI file modifications
 * - Skill matching state
 * - Utility functions for unique ID generation
 * - Callback creators for useAgentStream integration
 *
 * The throttling mechanism uses a ref to accumulate items and a throttled
 * flush to update React state, reducing re-renders during rapid streaming events.
 *
 * @returns Object containing state values and updater functions
 *
 * @example
 * ```tsx
 * const {
 *   streamRenderItems,
 *   updateStreamItems,
 *   clearStreamItems,
 *   forceFlushStreamItems,
 *   editProgress,
 *   setEditProgress,
 *   matchedSkills,
 *   setMatchedSkills,
 *   generateUniqueId,
 *   getStreamCallbacks,
 * } = useChatStreaming();
 * ```
 */
export function useChatStreaming(): UseChatStreamingReturn {
  // ========================================
  // Stream render items state with throttling
  // ========================================

  /**
   * React state for stream render items - triggers re-renders for display.
   */
  const [streamRenderItems, setStreamRenderItems] = useState<StreamRenderItem[]>([]);

  /**
   * Ref for accumulating stream items without triggering re-renders.
   * Used as the source of truth during rapid streaming events.
   */
  const streamItemsRef = useRef<StreamRenderItem[]>([]);
  const getStreamItemsSnapshot = useCallback(() => streamItemsRef.current.slice(), []);

  /**
   * Refs for throttling - these persist across renders without triggering re-renders.
   */
  const throttleTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastFlushTimeRef = useRef(0);

  /**
   * Maps a parallel_execute sub-task id to its description, captured on
   * task-start so task-end (which omits the description) can render a
   * meaningful per-task completion line.
   */
  const parallelTaskDescRef = useRef<Map<string, string>>(new Map());

  /**
   * Throttled flush function to update state from ref.
   * Limits state updates to at most once per STREAM_UPDATE_THROTTLE_MS,
   * reducing re-renders while ensuring the latest state is eventually flushed.
   */
  const flushStreamItems = useMemo(() => {
    const flush = () => {
      const now = Date.now();
      if (now - lastFlushTimeRef.current >= STREAM_UPDATE_THROTTLE_MS) {
        // Enough time has passed, flush immediately
        lastFlushTimeRef.current = now;
        setStreamRenderItems([...streamItemsRef.current]);
      } else if (!throttleTimeoutRef.current) {
        // Schedule a flush for when throttle period ends
        throttleTimeoutRef.current = setTimeout(() => {
          throttleTimeoutRef.current = null;
          lastFlushTimeRef.current = Date.now();
          setStreamRenderItems([...streamItemsRef.current]);
        }, STREAM_UPDATE_THROTTLE_MS - (now - lastFlushTimeRef.current));
      }
    };

    return {
      flush,
      cancel: () => {
        if (throttleTimeoutRef.current) {
          clearTimeout(throttleTimeoutRef.current);
          throttleTimeoutRef.current = null;
        }
      },
      forceFlush: () => {
        if (throttleTimeoutRef.current) {
          clearTimeout(throttleTimeoutRef.current);
          throttleTimeoutRef.current = null;
        }
        lastFlushTimeRef.current = Date.now();
        setStreamRenderItems([...streamItemsRef.current]);
      },
    };
  }, []);

  /**
   * Updates stream render items via ref with throttled state updates.
   * Modifies the ref immediately and triggers a throttled state update
   * for rendering, reducing re-renders during rapid streaming events.
   *
   * @param updater - Function that receives current items and returns updated array
   */
  const updateStreamItems = useCallback(
    (updater: (prev: StreamRenderItem[]) => StreamRenderItem[]) => {
      streamItemsRef.current = updater(streamItemsRef.current);
      flushStreamItems.flush();
    },
    [flushStreamItems]
  );

  /**
   * Force immediate flush of pending state updates.
   * Useful when you need to ensure the UI is updated immediately,
   * such as before navigation or when streaming completes.
   */
  const forceFlushStreamItems = useCallback(() => {
    flushStreamItems.forceFlush();
  }, [flushStreamItems]);

  /**
   * Clears all stream render items from both ref and state.
   * Also cancels any pending throttled updates.
   */
  const clearStreamItems = useCallback(() => {
    streamItemsRef.current = [];
    flushStreamItems.cancel();
    setStreamRenderItems([]);
  }, [flushStreamItems]);

  // ========================================
  // Edit progress state
  // ========================================

  /**
   * Current edit progress for AI file modifications.
   * Null when no edit is in progress.
   */
  const [editProgress, setEditProgress] = useState<EditProgress | null>(null);

  // ========================================
  // Skill matching state
  // ========================================

  /**
   * Currently matched skills during streaming.
   * Used to display which skills were activated by user input.
   */
  const [matchedSkills, setMatchedSkills] = useState<MatchedSkill[]>([]);
  const fileContentEndByFileIdRef = useRef<Set<string>>(new Set());
  const fileEditMetaByIdRef = useRef<Map<string, { title: string; fileType?: string }>>(
    new Map(),
  );
  const pendingFileTreeRefreshRef = useRef(false);
  const fileTreeRefreshTimerRef = useRef<ReturnType<typeof setTimeout> | null>(
    null
  );

  // Clear pending timers on unmount so a queued throttle flush or debounced
  // file-tree refresh can't fire (and touch state / trigger a refetch) after
  // the consumer has unmounted.
  useEffect(() => {
    return () => {
      if (throttleTimeoutRef.current) {
        clearTimeout(throttleTimeoutRef.current);
        throttleTimeoutRef.current = null;
      }
      if (fileTreeRefreshTimerRef.current) {
        clearTimeout(fileTreeRefreshTimerRef.current);
        fileTreeRefreshTimerRef.current = null;
      }
    };
  }, []);

  // ========================================
  // Utility functions
  // ========================================

  // Note: generateUniqueId is now a standalone exported function above
  // We reference it directly in getStreamCallbacks without useCallback wrapper

  // ========================================
  // Callback creators
  // ========================================

  /**
   * Creates callback handlers for SSE events based on provided dependencies.
   * These callbacks integrate with useAgentStream to handle all streaming events.
   *
   * The returned callbacks handle:
   * - Stream render items management (context, thinking, segments, workflow)
   * - Edit progress tracking
   * - Skill matching
   * - File operations (create, content, edit)
   * - Tool results
   * - Error handling
   *
   * @param deps - External dependencies for side effects
   * @returns Object containing all required callbacks for useAgentStream
   */
  const getStreamCallbacks = useCallback(
    (deps: UseChatStreamingDependencies): Required<UseChatStreamingOptions> => {
      const {
        triggerFileTreeRefresh,
        triggerEditorRefresh,
        setAiEditingFileId,
        setSelectedItem,
        getCurrentSelectedItem,
        appendFileContent,
        finishFileStreaming,
        startFileStreaming,
        streamingFileId,
        getCurrentStreamingFileId,
        enterDiffReview,
        activeProjectId,
        createSnapshot,
        getLatestUserRequest,
        t,
      } = deps;

      const scheduleFileTreeRefresh = (delayMs = FILE_TREE_REFRESH_DEBOUNCE_MS) => {
        pendingFileTreeRefreshRef.current = true;
        if (fileTreeRefreshTimerRef.current) {
          clearTimeout(fileTreeRefreshTimerRef.current);
        }
        fileTreeRefreshTimerRef.current = setTimeout(() => {
          fileTreeRefreshTimerRef.current = null;
          if (!pendingFileTreeRefreshRef.current) return;
          pendingFileTreeRefreshRef.current = false;
          triggerFileTreeRefresh();
        }, delayMs);
      };

      const flushFileTreeRefresh = () => {
        if (fileTreeRefreshTimerRef.current) {
          clearTimeout(fileTreeRefreshTimerRef.current);
          fileTreeRefreshTimerRef.current = null;
        }
        if (!pendingFileTreeRefreshRef.current) return;
        pendingFileTreeRefreshRef.current = false;
        triggerFileTreeRefresh();
      };

      return {
        // ========================================
        // Lifecycle callbacks
        // ========================================

        /**
         * Called when streaming starts - clears previous state
         */
        onStart: () => {
          setEditProgress(null);
          clearStreamItems();
          setMatchedSkills([]);
          fileContentEndByFileIdRef.current.clear();
          fileEditMetaByIdRef.current.clear();
          pendingFileTreeRefreshRef.current = false;
          if (fileTreeRefreshTimerRef.current) {
            clearTimeout(fileTreeRefreshTimerRef.current);
            fileTreeRefreshTimerRef.current = null;
          }
        },

        // ========================================
        // Context and Thinking callbacks
        // ========================================

        /**
         * Called when context items are received from the agent
         */
        onContext: (items: AgentContextItem[]) => {
          if (items && items.length > 0) {
            updateStreamItems((prev) => [
              ...prev,
              {
                type: "context",
                id: generateUniqueId("context"),
                items: items,
                timestamp: new Date(),
              },
            ]);
          }
        },

        /**
         * Called when AI is thinking (with status message)
         */
        onThinking: (message: string) => {
          updateStreamItems((prev) => [
            ...prev,
            {
              type: "thinking_status",
              id: generateUniqueId("thinking-status"),
              content: message,
              timestamp: new Date(),
            },
          ]);
        },

        /**
         * Called when thinking content arrives - accumulates to previous item if same type
         */
        onThinkingContent: (content: string) => {
          updateStreamItems((prev) => {
            const lastItem = prev[prev.length - 1];

            // Only accumulate when last item is thinking_content
            if (lastItem && lastItem.type === "thinking_content") {
              const updated = prev.slice();
              updated[updated.length - 1] = {
                ...lastItem,
                content: (lastItem.content || "") + content,
              };
              return updated;
            } else {
              // Create new thinking_content
              return [
                ...prev,
                {
                  type: "thinking_content",
                  id: generateUniqueId("thinking-content"),
                  content: content,
                  timestamp: new Date(),
                },
              ];
            }
          });
        },

        // ========================================
        // Segment callbacks (for stream render items)
        // ========================================

        /**
         * Called when a new segment starts
         */
        onSegmentStart: (segment: {
          id: string;
          type: string;
          content?: string;
          toolCalls?: ToolCall[];
        }) => {
          if (segment.type === "content") {
            updateStreamItems((prev) => [
              ...prev,
              {
                type: "content",
                id: segment.id,
                content: "",
                timestamp: new Date(),
              },
            ]);
          } else if (segment.type === "tool_calls" && segment.toolCalls) {
            updateStreamItems((prev) => [
              ...prev,
              {
                type: "tool_calls",
                id: segment.id,
                toolCalls: segment.toolCalls,
                timestamp: new Date(),
              },
            ]);
          }
        },

        /**
         * Called when segment content updates - strips think tags before updating
         */
        onSegmentUpdate: (segmentId: string, content: string) => {
          const cleanContent = stripThinkTags(content);
          if (!cleanContent.trim()) return;

          updateStreamItems((prev) => {
            const contentIndex = prev.findIndex((item) => item.id === segmentId);

            if (contentIndex >= 0) {
              const updated = prev.slice();
              const currentItem = updated[contentIndex];
              updated[contentIndex] = { ...currentItem, content: cleanContent };
              return updated;
            }

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
          });
        },

        /**
         * Called when tool calls segment updates
         */
        onSegmentUpdateToolCalls: (segmentId: string, toolCalls: ToolCall[]) => {
          updateStreamItems((prev) => {
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
          });
        },

        /**
         * Called when a segment completes - currently no action needed
         */
        onSegmentEnd: (_segmentId: string) => {
          // Segment is complete, nothing special to do
        },

        // ========================================
        // Multi-agent workflow callbacks
        // ========================================

        /**
         * Called when an agent is selected by the router
         */
        onAgentSelected: (
          agentType: string,
          agentName: string,
          iteration?: number,
          maxIterations?: number,
          remaining?: number
        ) => {
          updateStreamItems((prev) => [
            ...prev,
            {
              type: "agent_selected",
              id: generateUniqueId("agent-selected"),
              agentType,
              agentName,
              iteration,
              maxIterations,
              remaining,
              timestamp: new Date(),
            },
          ]);
        },

        /**
         * Called when iteration limit is exhausted
         */
        onIterationExhausted: (
          layer: "collaboration" | "tool_call",
          iterationsUsed: number,
          maxIterations: number,
          reason: string,
          lastAgent?: string
        ) => {
          updateStreamItems((prev) => [
            ...prev,
            {
              type: "iteration_exhausted",
              id: generateUniqueId("iteration-exhausted"),
              layer,
              iterationsUsed,
              maxIterations,
              reason,
              lastAgent,
              timestamp: new Date(),
            },
          ]);
        },

        /**
         * Called when router starts thinking
         */
        onRouterThinking: (message: string) => {
          updateStreamItems((prev) => [
            ...prev,
            {
              type: "router_thinking",
              id: generateUniqueId("router-thinking"),
              content: message,
              timestamp: new Date(),
            },
          ]);
        },

        /**
         * Called when router makes a decision
         */
        onRouterDecided: (
          initialAgent: string,
          workflowPlan: string,
          workflowAgents: string[],
          routingMetadata?: SSERoutingMetadata
        ) => {
          updateStreamItems((prev) => [
            ...prev,
            {
              type: "router_decided",
              id: generateUniqueId("router-decided"),
              initialAgent,
              workflowPlan,
              workflowAgents,
              routingMetadata,
              timestamp: new Date(),
            },
          ]);
        },

        /**
         * Called when workflow handoff happens between agents
         */
        onHandoff: (data: SSEHandoffData) => {
          const content = formatHandoffMessage(data, t);
          if (!content) return;
          updateStreamItems((prev) => [
            ...prev,
            {
              type: "thinking_status",
              id: generateUniqueId("handoff"),
              content,
              timestamp: new Date(),
            },
          ]);
        },

        /**
         * Called when workflow stops for clarification
         */
        onWorkflowStopped: (data: SSEWorkflowStoppedData) => {
          updateStreamItems((prev) => [
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
              confidence: data.confidence,
              evaluation: data.evaluation,
              timestamp: new Date(),
            },
          ]);
        },

        /**
         * Called when workflow completes successfully
         */
        onWorkflowComplete: (data: SSEWorkflowCompleteData) => {
          updateStreamItems((prev) => [
            ...prev,
            {
              type: "workflow_complete",
              id: generateUniqueId("workflow-complete"),
              reason: data.reason,
              agentType: data.agent_type,
              message: data.message,
              confidence: data.confidence,
              evaluation: data.evaluation,
              timestamp: new Date(),
            },
          ]);
        },

        // ========================================
        // Tool result callbacks
        // ========================================

        /**
         * Called when a tool execution completes
         */
        onToolResult: (
          toolName: string,
          status: string,
          result?: Record<string, unknown>,
          _error?: string
        ) => {
          if (
            status === "success" &&
            [
              "create_file",
              "update_file",
              "delete_file",
              "edit_file",
              // parallel_execute may create/edit/delete files across its
              // sub-tasks, so its completion must also refresh the tree.
              "parallel_execute",
            ].includes(toolName)
          ) {
            // Coalesce multiple tool/file events into one refresh.
            scheduleFileTreeRefresh();
          }

          if (status === "success" && toolName === "update_project") {
            const payload = (result?.data ?? result) as
              | Record<string, unknown>
              | undefined;
            const projectStatus = payload?.project_status as
              | Record<string, unknown>
              | undefined;
            const rawUpdatedFields =
              payload?.updated_fields ?? projectStatus?.updated_fields;
            const updatedFields = Array.isArray(rawUpdatedFields)
              ? rawUpdatedFields.filter(
                  (field): field is string => typeof field === "string"
                )
              : [];
            const hasAiMemoryFieldUpdates = updatedFields.some((field) =>
              AI_MEMORY_STATUS_FIELDS.has(field)
            );

            if (!hasAiMemoryFieldUpdates) {
              return;
            }

            const payloadProjectIdRaw =
              payload?.project_id ?? projectStatus?.project_id;
            const payloadProjectId =
              typeof payloadProjectIdRaw === "string" &&
              payloadProjectIdRaw.trim().length > 0
                ? payloadProjectIdRaw
                : null;
            const targetProjectId = payloadProjectId ?? activeProjectId;

            if (!targetProjectId) {
              return;
            }

            dispatchProjectStatusUpdated({
              projectId: targetProjectId,
              updatedFields,
            });
          }
        },

        // ========================================
        // File operation callbacks
        // ========================================

        /**
         * Called when a file is created (for auto-select)
         */
        onFileCreated: (fileId: string, fileType: string, title: string) => {
          // Auto-select newly created file so the editor can show streaming output
          scheduleFileTreeRefresh();
          startFileStreaming(fileId);
          fileContentEndByFileIdRef.current.delete(fileId);
          setSelectedItem({ id: fileId, type: fileType as FileType, title });
        },

        /**
         * Called when file content chunk is received (streaming)
         */
        onFileContent: (fileId: string, chunk: string) => {
          // Append content chunk to streaming state
          appendFileContent(fileId, chunk);
        },

        /**
         * Called when file content streaming ends
         */
        onFileContentEnd: (fileId: string) => {
          fileContentEndByFileIdRef.current.add(fileId);
          // Always ask ProjectContext to finish the stream for this file.
          // ProjectContext guards mismatched file IDs internally.
          finishFileStreaming(fileId);
          scheduleFileTreeRefresh();
        },

        /**
         * Called when file editing starts
         */
        onFileEditStart: (fileId: string, title: string, totalEdits: number, fileType?: string) => {
          fileEditMetaByIdRef.current.set(fileId, { title, fileType });
          // 挂起该文件的自动保存，直到 file_edit_end
          setAiEditingFileId?.(fileId);
          setEditProgress({
            fileId,
            title,
            totalEdits,
            completedEdits: 0,
          });
        },

        /**
         * Called when a single edit is applied
         */
        onFileEditApplied: (
          _fileId: string,
          editIndex: number,
          op: string,
          _oldPreview?: string,
          _newPreview?: string,
          _success?: boolean,
          _error?: string
        ) => {
          setEditProgress((prev) =>
            prev
              ? {
                  ...prev,
                  completedEdits: editIndex + 1,
                  currentOp: op,
                }
              : null
          );
        },

        /**
         * Called when file editing ends
         */
        onFileEditEnd: (
          fileId: string,
          _editsApplied: number,
          _newLength: number,
          newContent?: string,
          originalContent?: string,
          fileType?: string,
          title?: string,
          outcome?: FileEditOutcome,
        ) => {
          // Clear edit progress and mark tree refresh once.
          setEditProgress(null);
          setAiEditingFileId?.(null);
          scheduleFileTreeRefresh();

          // 批量编辑可以部分失败：不提示的话用户只看到"编辑完成"，
          // 实际上有若干条 edit 根本没落地（失败原因只藏在 data 里）。
          if (outcome && (outcome.failedCount > 0 || outcome.allFailed)) {
            const displayTitle = title || fileEditMetaByIdRef.current.get(fileId)?.title || "";
            toast.error(
              outcome.allFailed
                ? t("chat:fileEdit.allFailed", {
                    title: displayTitle,
                    count: outcome.failedCount,
                  })
                : t("chat:fileEdit.partialFailed", {
                    title: displayTitle,
                    count: outcome.failedCount,
                  }),
            );
          }
          for (const warning of outcome?.warnings ?? []) {
            if (warning) {
              logger.warn(`[FileEdit] ${fileId}: ${warning}`);
            }
          }

          // If we have both original and new content, enter diff review mode
          if (newContent && originalContent && newContent !== originalContent) {
            enterDiffReview(fileId, originalContent, newContent);

            const currentSelected = getCurrentSelectedItem?.() ?? null;
            const trackedMeta = fileEditMetaByIdRef.current.get(fileId);
            const resolvedType =
              (fileType as FileType | undefined)
              || (trackedMeta?.fileType as FileType | undefined)
              || (currentSelected?.id === fileId ? currentSelected.type : undefined)
              || "draft";
            const resolvedTitle =
              title
              || trackedMeta?.title
              || (currentSelected?.id === fileId ? currentSelected.title : "")
              || "";
            setSelectedItem({
              id: fileId,
              type: resolvedType,
              title: resolvedTitle,
            });
          } else {
            // Fallback: trigger editor refresh to show updated content
            triggerEditorRefresh(fileId);
          }
          fileEditMetaByIdRef.current.delete(fileId);
        },

        // ========================================
        // Skill matching callbacks
        // ========================================

        /**
         * Called when a skill is matched
         */
        onSkillMatched: (
          skillId: string,
          skillName: string,
          matchedTrigger: string
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

        /**
         * Called when multiple skills are matched (multi-skill activation)
         */
        onSkillsMatched: (
          skills: Array<{
            id: string;
            name: string;
            trigger: string;
            confidence: number;
          }>
        ) => {
          setMatchedSkills(
            skills.map((s) => ({ name: s.name, trigger: s.trigger }))
          );
        },

        onSessionStarted: (_sessionId: string) => {
          // No-op: this event is too noisy in the timeline.
          // Session state is tracked internally via useAgentStream.
        },

        onParallelStart: (
          _executionId: string,
          taskCount: number,
          _descriptions: string[],
          droppedCount?: number,
        ) => {
          // 后端单次并行有上限，超出的任务会被截断。只报"执行 N 个"会让用户
          // 以为模型请求的任务全跑了，而实际上有几个根本没执行。
          const dropped = droppedCount ?? 0;
          const content =
            dropped > 0
              ? t('chat:workflow.parallelStartTruncated', {
                  count: taskCount,
                  dropped,
                })
              : t('chat:workflow.parallelStart', { count: taskCount });
          updateStreamItems((prev) => [
            ...prev,
            {
              type: "thinking_status",
              id: generateUniqueId("parallel-start"),
              content,
              timestamp: new Date(),
            },
          ]);
        },

        onParallelTaskStart: (
          _executionId: string,
          taskId: string,
          _taskType: string,
          description: string
        ) => {
          // Remember the description so task-end can render a labeled line.
          parallelTaskDescRef.current.set(taskId, description);
        },

        onParallelTaskEnd: (
          _executionId: string,
          taskId: string,
          status: string,
          _result?: unknown,
          error?: string
        ) => {
          const description =
            parallelTaskDescRef.current.get(taskId) || taskId;
          parallelTaskDescRef.current.delete(taskId);
          const content =
            status === "failed"
              ? t('chat:workflow.parallelTaskFailed', {
                  description,
                  error: error || '',
                })
              : t('chat:workflow.parallelTaskCompleted', { description });
          updateStreamItems((prev) => [
            ...prev,
            {
              type: "thinking_status",
              id: generateUniqueId("parallel-task-end"),
              content,
              timestamp: new Date(),
            },
          ]);
        },

        onParallelEnd: (_executionId: string, total: number, completed: number, failed: number, _durationMs: number) => {
          updateStreamItems((prev) => [
            ...prev,
            {
              type: "thinking_status",
              id: generateUniqueId("parallel-end"),
              content: t('chat:workflow.parallelEnd', { completed, total, failed }),
              timestamp: new Date(),
            },
          ]);
        },

        onSteeringReceived: (_messageId: string, preview: string) => {
          updateStreamItems((prev) => [
            ...prev,
            {
              type: "thinking_status",
              id: generateUniqueId("steering-received"),
              content: t('chat:workflow.steeringReceived', { preview }),
              timestamp: new Date(),
            },
          ]);
        },

        // ========================================
        // Error and completion callbacks
        // ========================================

        /**
         * Called when streaming completes successfully.
         * Handles cleanup, snapshot creation, and external callback invocation.
         */
        onComplete: async (
          segments: MessageSegment[],
          _applyAction: ApplyAction | null,
          completionMeta?: StreamCompletionMeta,
        ) => {
          setEditProgress(null);

          // Clear stream render items (both ref and state).
          //
          // Terminal workflow signals (e.g. clarification_needed) are persisted into the final
          // assistant message as "status cards" so they should not remain as standalone
          // streamed items after completion.
          clearStreamItems();

          // 流结束兜底：file_edit_end 可能因取消/报错永远不到，
          // 不清掉标记会让编辑器的自动保存被永久挂起。
          setAiEditingFileId?.(null);

          // Clear any remaining file streaming state
          const latestStreamingFileId =
            getCurrentStreamingFileId?.() ?? streamingFileId ?? null;
          if (latestStreamingFileId) {
            if (!fileContentEndByFileIdRef.current.has(latestStreamingFileId)) {
              logger.warn(
                `[Stream] Missing file_content_end for ${latestStreamingFileId}, forcing final refresh on completion`
              );
              pendingFileTreeRefreshRef.current = true;
            }
            finishFileStreaming(latestStreamingFileId);
            fileContentEndByFileIdRef.current.delete(latestStreamingFileId);
          }
          flushFileTreeRefresh();

          if (
            completionMeta?.partial !== true &&
            completionMeta?.confirmedFileMutation === true &&
            activeProjectId
          ) {
            // 快照列表里一排「AI 对话完成」分不出哪次是哪次，用作者这一轮的原话来区分。
            const requestPreview = buildSnapshotRequestPreview(getLatestUserRequest?.());
            try {
              await createSnapshot(activeProjectId, {
                description: requestPreview
                  ? t("chat:message.snapshotAfterAiEdit", { request: requestPreview })
                  : t("chat:message.aiDone"),
                snapshotType: "auto",
              });
            } catch (err) {
              // Snapshot creation is non-critical, log but don't throw
              logger.error("Failed to create snapshot:", err);
            }
          }
        },

        /**
         * Called when an error occurs during streaming
         */
        onError: (_message?: string, _code?: string, _retryable?: boolean) => {
          setEditProgress(null);
          setAiEditingFileId?.(null);

          // Clear any remaining file streaming state on error
          const latestStreamingFileId =
            getCurrentStreamingFileId?.() ?? streamingFileId ?? null;
          if (latestStreamingFileId) {
            if (!fileContentEndByFileIdRef.current.has(latestStreamingFileId)) {
              logger.warn(
                `[Stream] Missing file_content_end for ${latestStreamingFileId} after error, forcing final refresh`
              );
              pendingFileTreeRefreshRef.current = true;
            }
            finishFileStreaming(latestStreamingFileId);
            fileContentEndByFileIdRef.current.delete(latestStreamingFileId);
          }
          flushFileTreeRefresh();
        },
      };
    },
    [
      clearStreamItems,
      setEditProgress,
      setMatchedSkills,
      updateStreamItems,
    ]
  );

  // ========================================
  // Return value
  // ========================================

  return {
    // Stream render items state
    streamRenderItems,
    getStreamItemsSnapshot,
    updateStreamItems,
    clearStreamItems,
    forceFlushStreamItems,

    // Edit progress state
    editProgress,
    setEditProgress,

    // Skill matching state
    matchedSkills,
    setMatchedSkills,

    // Utility functions
    generateUniqueId,

    // Callback creators
    getStreamCallbacks,
  };
}
