/**
 * Shared types, constants, and ID helpers for chat streaming hooks.
 *
 * @module hooks/streaming/types
 */

import type { Dispatch, SetStateAction } from "react";
import type { RoundStopOutcome, RoundWrittenFile } from "../../lib/chatRoundEnd";
import type { QuotaRefundKind, RemovedPlaceholderFile } from "../../lib/agentApi";
import type {
  AgentContextItem,
  AgentRequest,
  AgentStreamState,
  FileEditOutcome,
  FileType,
  IterationExhaustedFiles,
  IterationExhaustedLayer,
  SSEWorkflowStoppedData,
  ToolCall,
} from "../../types";

/** Throttle delay for stream render updates in milliseconds */
export const STREAM_UPDATE_THROTTLE_MS = 50;

/** How long to wait for the server to wrap up a stopped round before dropping the connection. */
export const STOP_GRACE_MS = 5000;

/**
 * Flush streaming content to React state at most once per interval.
 * Avoids re-rendering on every token/chunk during rapid SSE bursts.
 */
export const STREAM_CONTENT_FLUSH_INTERVAL_MS = 33;

/**
 * Generates a unique identifier with the given prefix.
 * Combines timestamp and random string to avoid collisions
 * when multiple IDs are generated in rapid succession.
 */
export function generateUniqueId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).substring(2, 11)}`;
}

/** Generate unique segment ID */
export function generateSegmentId(): string {
  return `segment-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
}

/** A segment of assistant response (text content or tool calls) */
export interface MessageSegment {
  id: string;
  type: "content" | "tool_calls";
  content?: string;
  toolCalls?: ToolCall[];
  isStreaming?: boolean;
}

export interface StreamCompletionMeta {
  assistantMessageId?: string;
  sessionId?: string;
  /** Server-confirmed indication that this completed turn committed a file mutation. */
  confirmedFileMutation?: boolean;
  /**
   * 本轮是「非正常终止」后的部分提交（用户点停止 / SSE error / 反向代理掐断空闲连接）。
   *
   * 正常路径（SSE done）不会带这个标记。调用方据此可以把已流式渲染的正文
   * 照常落进 messages，而不是让它随下一轮 onStart 的清理一起消失。
   */
  partial?: boolean;
  /** The author stopped this round and the server wrapped it up (done with stop_reason). */
  stoppedByAuthor?: boolean;
  /** Stopped rounds only: whether the server settled this round as charged (false: refunded). */
  charged?: boolean;
  /**
   * Stopped rounds only: whether the server saw real output (prose or a write). False means
   * nothing was written, so follow-ups must not assume prose exists.
   */
  producedOutput?: boolean;
  /**
   * Stopped rounds only: the files this round wrote text into, as the server will record them
   * in `stop_outcome.written_files`. Absent when the server named none (or is older).
   */
  writtenFiles?: RoundWrittenFile[];
  unwrittenFiles?: RoundWrittenFile[];
  deliveryOutcome?: RoundStopOutcome;
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
    | "workflow_stopped"
    | "workflow_complete"
    | "history_compaction";
  /** Unique identifier for this item */
  id: string;
  /**
   * Progress line that only means "working on it" (「正在组装上下文… / 正在思考…」);
   * dropped when the round is saved as a finished message. Handoff lines never set it.
   */
  transient?: boolean;
  /** Text content for thinking, status, or message items (history_compaction: the handoff summary) */
  content?: string;
  /** history_compaction: still summarizing (live only, transient) or done (a persistent marker) */
  compactionState?: "running" | "done";
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
  /** Which layer stopped the round (see IterationExhaustedLayer) */
  layer?: IterationExhaustedLayer;
  /** Stop with a file still waiting for its text: titles of files left unfinished / removed again */
  unfinishedFiles?: string[];
  rolledBackFiles?: string[];
  /** Number of iterations actually used */
  iterationsUsed?: number;
  /** Reason for stopping or completing the workflow */
  reason?: string;
  /** Name of the last agent that ran */
  lastAgent?: string;
  /** What ended a paused run: "wall_clock" means it stopped near the time limit and may already be done */
  limit?: string;
  /** Message from workflow status events */
  message?: string;
  /** Clarification question (optional) */
  question?: string;
  /** Clarification context (optional) */
  context?: string;
  /** Clarification details list (optional) */
  details?: string[];
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

  /** Called when context items are received from the agent */
  onContext?: (items: AgentContextItem[]) => void;

  /** Called when AI is thinking (with status message) */
  onThinking?: (message: string, step?: string) => void;

  /** Called when earlier conversation was summarized before this round */
  onHistoryCompacted?: (summary: string) => void;

  /** Called when thinking content arrives */
  onThinkingContent?: (content: string) => void;

  /** Called when a new segment starts */
  onSegmentStart?: (segment: { id: string; type: string; content?: string; toolCalls?: ToolCall[] }) => void;

  /** Called when segment content updates */
  onSegmentUpdate?: (segmentId: string, content: string) => void;

  /** Called when tool calls segment updates */
  onSegmentUpdateToolCalls?: (segmentId: string, toolCalls: ToolCall[]) => void;

  /** Called when iteration limit is exhausted */
  onIterationExhausted?: (
    layer: IterationExhaustedLayer,
    iterationsUsed: number,
    maxIterations: number,
    reason: string,
    lastAgent?: string,
    limit?: string,
    files?: IterationExhaustedFiles
  ) => void;

  /** Called when workflow stops */
  onWorkflowStopped?: (data: SSEWorkflowStoppedData) => void;

  /** Called when a tool execution completes */
  onToolResult?: (
    toolName: string,
    status: string,
    result?: Record<string, unknown>,
    error?: string
  ) => void;

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
    editIndex: number,
    op: string,
  ) => void;

  /** Called when file editing ends */
  onFileEditEnd?: (
    fileId: string,
    editsApplied: number,
    newContent?: string,
    originalContent?: string,
    fileType?: string,
    title?: string,
    outcome?: FileEditOutcome,
  ) => void;

  /** Called when a skill is matched */
  onSkillMatched?: (skillId: string, skillName: string, matchedTrigger: string) => void;

  /** Called when session starts */
  onSessionStarted?: (sessionId: string) => void;

  /** Called when steering message is received */
  onSteeringReceived?: (messageId: string, preview: string) => void;

  /**
   * Called when streaming completes successfully.
   * Signature matches useAgentStream's onComplete callback.
   */
  onComplete?: (
    segments: MessageSegment[],
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
  setSelectedItem: (item: { id: string; type: FileType; title: string } | null) => void;
  /** Returns the latest selected item from project context */
  getCurrentSelectedItem?: () => { id: string; type: FileType; title: string } | null;
  /** Function to append content to a streaming file */
  appendFileContent: (fileId: string, content: string) => void;
  /** Function to mark file streaming as complete */
  finishFileStreaming: (fileId: string) => void;
  /** Function to start file streaming for a file */
  startFileStreaming: (fileId: string) => void;
  /** Returns the latest streaming file ID at callback execution time */
  getCurrentStreamingFileId?: () => string | null;
  /** Active UI project ID; scopes file checks during the stream */
  activeProjectId: string | null;
  /** Current authenticated user ID (used to re-arm background edited files for AI-opened tracking) */
  currentUserId?: string | null;
  /**
   * Called when a successful `update_project` reports that the server renamed
   * the project (`project_name_updated: true`, set when the agent gave a
   * default-named project its work title). The caller re-reads that one
   * project so the header shows the new name.
   */
  onProjectRenamed?: (projectId: string) => void;
  /** Translation function for stream status text */
  t: (key: string, options?: Record<string, unknown>) => string;
}

/**
 * Return type for the useChatStreaming hook.
 */
export interface UseChatStreamingReturn {
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

  /** Current edit progress, if any */
  editProgress: EditProgress | null;

  /** Set edit progress state */
  setEditProgress: Dispatch<SetStateAction<EditProgress | null>>;

  /** Currently matched skills */
  matchedSkills: MatchedSkill[];

  /** Set matched skills */
  setMatchedSkills: Dispatch<SetStateAction<MatchedSkill[]>>;

  /** Generate a unique ID with the given prefix */
  generateUniqueId: (prefix: string) => string;

  /** Get callbacks for useAgentStream options */
  getStreamCallbacks: (deps: UseChatStreamingDependencies) => Required<UseChatStreamingOptions>;
}

export interface UseAgentStreamOptions {
  /**
   * Flush interval (ms) for streaming content batching.
   *
   * Default: 33ms.
   * Set to 0 to disable batching (flush every chunk).
   */
  contentFlushIntervalMs?: number;
  /** Called when streaming starts */
  onStart?: () => void;
  /** Called when context items are received */
  onContext?: (items: AgentContextItem[], tokenCount?: number) => void;
  /** Called when AI is thinking (with message) */
  onThinking?: (message: string, step?: string) => void;
  /** Called when thinking content arrives */
  onThinkingContent?: (content: string, isComplete: boolean) => void;
  /** Called when a new message segment starts (for adding to chat) */
  onSegmentStart?: (segment: MessageSegment) => void;
  /** Called when segment content updates (for streaming text) */
  onSegmentUpdate?: (segmentId: string, content: string) => void;
  /** Called when tool calls segment updates */
  onSegmentUpdateToolCalls?: (segmentId: string, toolCalls: ToolCall[]) => void;
  /** Called when a segment completes */
  onSegmentEnd?: (segmentId: string) => void;
  /**
   * Called when content is flushed to React state.
   *
   * Note: To reduce re-renders, streaming content may be batched (e.g. flushed every ~33ms),
   * so `chunk` can contain multiple SSE chunks concatenated together.
   */
  onContent?: (chunk: string, fullContent: string) => void;
  /** Called when streaming completes successfully */
  onComplete?: (
    segments: MessageSegment[],
    completionMeta?: StreamCompletionMeta,
  ) => void;
  /** Called when an error occurs */
  onError?: (error: string, code?: string, retryable?: boolean) => void;
  /** Called when AI calls a tool */
  onToolCall?: (toolName: string, args: Record<string, unknown>, toolUseId?: string) => void;
  /** Called when tool execution completes */
  onToolResult?: (
    toolName: string,
    status: string,
    result?: Record<string, unknown>,
    error?: string,
    toolUseId?: string,
  ) => void;
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
    editIndex: number,
    op: string,
  ) => void;
  /** Called when file editing ends */
  onFileEditEnd?: (
    fileId: string,
    editsApplied: number,
    newContent?: string,
    originalContent?: string,
    fileType?: string,
    title?: string,
    outcome?: FileEditOutcome,
  ) => void;
  /** Called when a skill is matched */
  onSkillMatched?: (skillId: string, skillName: string, matchedTrigger: string) => void;
  /** Called when iteration limit is exhausted */
  onIterationExhausted?: (
    layer: IterationExhaustedLayer,
    iterationsUsed: number,
    maxIterations: number,
    reason: string,
    lastAgent?: string,
    limit?: string,
    files?: IterationExhaustedFiles
  ) => void;
  /** Called when workflow stops */
  onWorkflowStopped?: (data: SSEWorkflowStoppedData) => void;
  /** Called when session starts */
  onSessionStarted?: (sessionId: string) => void;
  /** Called when steering message is received */
  onSteeringReceived?: (messageId: string, preview: string) => void;
  /** Called when earlier conversation was summarized before this round */
  onHistoryCompacted?: (summary: string) => void;
  /** Called after the terminal frame when the backend actually refunded this round */
  onQuotaRefunded?: (kind: QuotaRefundKind, removedFiles: RemovedPlaceholderFile[]) => void;
}

export interface UseAgentStreamReturn {
  /** Current stream state */
  state: AgentStreamState;
  /** Start streaming request */
  startStream: (request: Omit<AgentRequest, "project_id">) => void;
  /**
   * Author pressed stop: ask the server to end the run on this stream so it can
   * settle billing as an author stop; falls back to cancel() if that fails.
   * `keepFileIds`: files with author text the editor could not save first; the
   * server does not remove them as blank placeholders. Resolves once the stop
   * request has settled.
   */
  stop: (options?: { keepFileIds?: string[] }) => Promise<void>;
  /** Stop requested, waiting for the server to close the round */
  isStopping: boolean;
  /** Reset state to initial (drops the connection without the server stop) */
  reset: () => void;
  /**
   * Resolves once this round's connection is over: the server sent its last frame (after
   * done a quota_refunded may still follow) and closed the stream, or the connection was
   * dropped. Resolves at once when no round is open, and after `timeoutMs` at the latest.
   * Used to start a new chat only after a stopped round has been settled as an author stop.
   */
  whenStreamClosed: (timeoutMs?: number) => Promise<void>;
  /** Whether currently streaming */
  isStreaming: boolean;
  /** Whether waiting for thinking step */
  isThinking: boolean;
  /** Error message if any */
  error: string | null;
  /** Backend error code if provided by SSE error event */
  errorCode: string | null;
  /** Clears the current error message and code */
  clearError: () => void;
  /** Whether the backend says the current stream error can be retried */
  retryable: boolean;
  /** Current session ID for steering */
  sessionId: string | null;
  /** Send a steering message to the active session */
  sendSteeringMessage: (message: string) => Promise<void>;
}
