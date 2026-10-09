/**
 * Agent API client with SSE (Server-Sent Events) streaming support.
 *
 * This module provides the frontend interface to the AI Agent backend,
 * handling real-time streaming communication for chat interactions.
 *
 * Key features:
 * - SSE streaming for real-time AI responses
 * - Tool calling support (CRUD operations on files)
 * - Multi-agent workflow coordination
 * - Intelligent suggestion generation
 * - Automatic token refresh on authentication errors
 *
 * Main exports:
 * - streamAgentRequest: Primary streaming endpoint for AI conversations
 * - fetchSuggestions: Get AI-generated next-step suggestions
 */

import type {
  AgentRequest,
  AgentContextItem,
  FileEditOutcome,
  SSEEvent,
  SSEEventType,
  SSEHandoffData,
  SSERouterDecidedData,
  SSERoutingMetadata,
  SSEWorkflowCompleteData,
  SSEWorkflowStoppedData,
} from "../types";
import { api, tryRefreshToken, getAccessToken, clearAuthStorage, getApiBase, resolveOwnedAuthSession } from "./apiClient";
import { debugContext } from "./debugContext";
import { resolveApiErrorMessage, toUserErrorMessage, translateError } from "./errorHandler";
import { logger } from "./logger";
import i18n from "./i18n";
import { createAgentStreamTelemetry, type AgentStreamTelemetry } from "./agentStreamTelemetry";

const TRACE_ID_HEADER = "X-Trace-ID";
// 告诉后端这个前端认识停止约定（/agent/stop、quota_refunded.removed_files、断线终态）。
// 发布前打开的旧标签页不带它，后端对它的断线不移除空白文件、不写空的助手消息。
const CLIENT_STOP_CONTRACT_HEADER = "X-Client-Stop-Contract";

const generateTraceId = (): string => {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `${Date.now().toString(16)}-${Math.random().toString(16).slice(2)}`;
};

/**
 * Parse a single SSE (Server-Sent Event) from a raw string.
 *
 * Extracts the event type and data payload from the SSE format:
 * - "event:" line specifies the event type (e.g., "content", "tool_call")
 * - "data:" line contains the JSON payload
 *
 * @param eventString - Raw SSE event string to parse
 * @returns Parsed SSEEvent object with type and data, or null if invalid
 */
function parseSSEEvent(eventString: string): SSEEvent | null {
  const lines = eventString.split("\n");
  let eventType: SSEEventType = "content";
  let dataString = "";

  for (const line of lines) {
    if (line.startsWith("event:")) {
      eventType = line.slice(6).trim() as SSEEventType;
    } else if (line.startsWith("data:")) {
      dataString = line.slice(5).trim();
    }
  }

  if (!dataString) {
    return null;
  }

  try {
    const data = JSON.parse(dataString);
    return { type: eventType, data };
  } catch {
    // If data is not JSON, treat as plain text content
    return { type: eventType, data: { text: dataString } };
  }
}

/**
 * Stream an agent request with SSE (Server-Sent Events) and Function Calling.
 *
 * Establishes a real-time streaming connection to the AI agent backend,
 * allowing the AI to call tools (CRUD operations on files) and respond
 * with structured events for thinking, content, tool calls, and more.
 *
 * @param request - Agent request parameters containing:
 *   - project_id: Current project context
 *   - message: User's input message
 *   - selected_text: Optional text selected in editor
 *   - context_before/after: Optional surrounding context
 *   - outline_id: Optional outline reference
 *   - metadata: Additional request metadata
 * @param callbacks - Event callback handlers for each SSE event type:
 *   - onThinking: Agent internal reasoning updates
 *   - onThinkingContent: Streaming thinking content chunks
 *   - onContext: Context items being used for response
 *   - onContentStart/Content/ContentEnd: Main response text streaming
 *   - onToolCall/ToolResult: Tool execution lifecycle
 *   - onConflict: Conflict detection notifications
 *   - onFileCreated/Content/ContentEnd: File creation streaming
 *   - onFileEditStart/EditApplied/EditEnd: File edit operations
 *   - onSkillMatched/SkillsMatched: Skill detection events
 *   - onAgentSelected: Agent selection in multi-agent workflow
 *   - onIterationExhausted: Iteration limit reached warning
 *   - onRouterThinking/Decided: Router agent decision process
 *   - onWorkflowStopped/Complete: Workflow termination events
 *   - onDone: Stream completion with optional action refs
 *   - onError: Error handling with retry capability
 * @returns AbortController for cancelling the stream request
 */
type AgentStreamCallbacks = Parameters<typeof streamAgentRequest>[1];

type StreamErrorEventData = {
  message?: string;
  code?: string;
  retryable?: boolean;
  reason?: string;
};

/**
 * quota_refunded 帧的三种说法：失控停止且没改文件 / 平台出错且没有产出 /
 * 作者点了停止且这一轮还没有任何产出。
 */
export type QuotaRefundKind = "no_progress" | "error" | "stopped";

/** 作者停止、这一轮被退还时，服务端移除的「这一轮新建、还是空白」的文件。 */
export interface RemovedPlaceholderFile {
  id: string;
  title: string;
}

/** done 帧。作者停止的一轮额外带 stop_reason / produced_output（是否已有实质产出）。 */
export interface StreamDoneData {
  apply_action?: string;
  refs?: number[];
  assistant_message_id?: string;
  session_id?: string;
  file_mutated?: boolean;
  stop_reason?: string;
  produced_output?: boolean;
}


/**
 * 服务端为这几类停止写了具体说明（停在哪、能不能接着来）；只有中文版本。
 * 其余错误一律按错误码走 i18n，避免把内部异常文本展示给作者。
 */
const SERVER_WORDED_STOP_CODES = new Set([
  "ERR_AGENT_TOOL_FAILURE_LIMIT",
  "ERR_AGENT_NO_PROGRESS",
]);

/**
 * 选出 error 帧要展示的原始文案（之后仍交给 toUserErrorMessage 翻译错误码）。
 *
 * 帧带 reason（无进展、熔断等停止原因），或错误码属于 SERVER_WORDED_STOP_CODES 且
 * message 非空时，优先用服务端的具体说明；这些说明只有中文，英文界面仍用错误码文案。
 */
export function selectStreamErrorMessage(
  data: StreamErrorEventData,
  language: string = i18n.language ?? "",
): string {
  const code = typeof data.code === "string" && data.code.trim() ? data.code.trim() : undefined;
  const message = typeof data.message === "string" ? data.message.trim() : "";
  const hasReason = typeof data.reason === "string" && data.reason.trim().length > 0;
  const serverWorded = hasReason || (code !== undefined && SERVER_WORDED_STOP_CODES.has(code));
  if (serverWorded && message && language.toLowerCase().startsWith("zh")) {
    return message;
  }
  return (code?.startsWith("ERR_") ? code : undefined)
    || message
    || code
    || "ERR_INTERNAL_SERVER_ERROR";
}

function notifyQuotaRefunded(data: unknown, callbacks: AgentStreamCallbacks): void {
  const payload = (data && typeof data === "object" ? data : {}) as {
    refunded?: unknown;
    kind?: unknown;
    removed_files?: unknown;
  };
  if (payload.refunded !== true) return;
  const kind: QuotaRefundKind =
    payload.kind === "no_progress" || payload.kind === "stopped" ? payload.kind : "error";
  const removedFiles: RemovedPlaceholderFile[] = Array.isArray(payload.removed_files)
    ? payload.removed_files.flatMap((item) => {
      const file = item as { id?: unknown; title?: unknown } | null;
      return file && typeof file.id === "string"
        ? [{ id: file.id, title: typeof file.title === "string" ? file.title : "" }]
        : [];
    })
    : [];
  callbacks.onQuotaRefunded?.(kind, removedFiles);
}

/** Report stream outcomes (completed/failed) before handing off to the caller. */
function withOutcomeTelemetry(
  callbacks: AgentStreamCallbacks,
  telemetry: AgentStreamTelemetry,
): AgentStreamCallbacks {
  return {
    ...callbacks,
    onToolCall: (...args) => {
      telemetry.noteToolCall();
      callbacks.onToolCall?.(...args);
    },
    onWorkflowStopped: (data) => {
      if (data.reason === "user_stopped") telemetry.stopped();
      callbacks.onWorkflowStopped?.(data);
    },
    onDone: (data) => {
      telemetry.completed();
      callbacks.onDone?.(data);
    },
    onError: (message, code, retryable) => {
      telemetry.failed(code, retryable);
      callbacks.onError?.(message, code, retryable);
    },
  };
}

export function streamAgentRequest(
  request: AgentRequest,
  rawCallbacks: {
    onThinking?: (message: string, step?: string) => void;
    onThinkingContent?: (content: string, isComplete?: boolean) => void;
    onContext?: (items: AgentContextItem[], tokenCount?: number) => void;
    onContentStart?: () => void;
    onContent?: (text: string, index?: number) => void;
    onContentEnd?: () => void;
    onToolCall?: (
      toolName: string,
      args: Record<string, unknown>,
      toolUseId?: string,
    ) => void;
    onToolResult?: (
      toolName: string,
      status: string,
      data?: Record<string, unknown>,
      error?: string,
      toolUseId?: string,
    ) => void;
    onConflict?: (conflict: {
      type: string;
      severity: string;
      title: string;
      description: string;
      suggestions: string[];
    }) => void;
    onFileCreated?: (fileId: string, fileType: string, title: string) => void;
    onFileContent?: (fileId: string, chunk: string) => void;
    onFileContentEnd?: (fileId: string) => void;
    onFileEditStart?: (
      fileId: string,
      title: string,
      totalEdits: number,
      fileType?: string,
    ) => void;
    onFileEditApplied?: (
      fileId: string,
      editIndex: number,
      op: string,
      oldPreview?: string,
      newPreview?: string,
      success?: boolean,
      error?: string,
    ) => void;
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
    onSkillMatched?: (skillId: string, skillName: string, matchedTrigger: string) => void;
    onSkillsMatched?: (skills: Array<{ id: string; name: string; trigger: string; confidence: number }>) => void;
    onAgentSelected?: (
      agentType: string,
      agentName: string,
      iteration?: number,
      maxIterations?: number,
      remaining?: number
    ) => void;
    onIterationExhausted?: (
      layer: "collaboration" | "tool_call",
      iterationsUsed: number,
      maxIterations: number,
      reason: string,
      lastAgent?: string
    ) => void;
    onRouterThinking?: (message: string) => void;
    onRouterDecided?: (
      initialAgent: string,
      workflowPlan: string,
      workflowAgents: string[],
      routingMetadata?: SSERoutingMetadata
    ) => void;
    onHandoff?: (data: SSEHandoffData) => void;
    onWorkflowStopped?: (data: SSEWorkflowStoppedData) => void;
    onWorkflowComplete?: (data: SSEWorkflowCompleteData) => void;
    onSessionStarted?: (sessionId: string) => void;
    /** 响应头到达：本次运行的 X-Agent-Run-ID，停止生成时要用。 */
    onRunStarted?: (agentRunId: string) => void;
    onParallelStart?: (
      executionId: string,
      taskCount: number,
      descriptions: string[],
      droppedCount?: number,
    ) => void;
    onParallelTaskStart?: (executionId: string, taskId: string, taskType: string, description: string) => void;
    onParallelTaskEnd?: (executionId: string, taskId: string, status: string, resultPreview?: string, error?: string) => void;
    onParallelEnd?: (executionId: string, total: number, completed: number, failed: number, durationMs: number) => void;
    onSteeringReceived?: (messageId: string, preview: string) => void;
    onDone?: (data: StreamDoneData) => void;
    onError?: (message: string, code?: string, retryable?: boolean) => void;
    /** 后端确实退还了这一轮的 AI 消息（在终止帧之后到达）；附带被移除的空白文件。 */
    onQuotaRefunded?: (kind: QuotaRefundKind, removedFiles: RemovedPlaceholderFile[]) => void;
  },
): AbortController {
  const entryAccess = getAccessToken();
  const entryRefresh = localStorage.getItem("refresh_token");
  const abortController = new AbortController();
  const traceId = generateTraceId();
  const telemetry = createAgentStreamTelemetry(request.project_id);
  const callbacks = withOutcomeTelemetry(rawCallbacks, telemetry);

  const fetchStream = async (accessToken: string | null = entryAccess, isRetry = false) => {
    try {
      // Track whether we received a terminal SSE event ("done" or "error").
      // If the server/proxy closes the connection without a terminal event,
      // the frontend would otherwise remain stuck in "processing" state.
      let receivedTerminalEvent = false;
      const language = localStorage.getItem("zenstory-language") || "zh";

      const response = await fetch(`${getApiBase()}/api/v1/agent/stream`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Accept-Language": language,
          Accept: "text/event-stream",
          [TRACE_ID_HEADER]: traceId,
          [CLIENT_STOP_CONTRACT_HEADER]: "1",
          ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
        },
        body: JSON.stringify({
          project_id: request.project_id,
          message: request.message,
          session_id: request.session_id,
          selected_text: request.selected_text,
          context_before: request.context_before,
          context_after: request.context_after,
          outline_id: request.outline_id,
          metadata: request.metadata || {},
          selected_skill_ids: request.selected_skill_ids?.length
            ? request.selected_skill_ids
            : undefined,
        }),
        signal: abortController.signal,
      });

      const responseRequestId = response.headers?.get?.("X-Request-ID") ?? undefined;
      const responseTraceId = response.headers?.get?.(TRACE_ID_HEADER) ?? traceId;
      const responseAgentRunId = response.headers?.get?.("X-Agent-Run-ID") ?? undefined;
      telemetry.noteResponse(response.status, {
        requestId: responseRequestId,
        agentRunId: responseAgentRunId,
      });

      debugContext.set({
        trace_id: responseTraceId,
        request_id: responseRequestId,
        agent_run_id: responseAgentRunId,
        project_id: request.project_id,
        agent_session_id: request.session_id ?? null,
        route: typeof window !== "undefined" ? window.location.href : undefined,
        created_at: new Date().toISOString(),
      });

      // Handle 401 - try to refresh token and retry once
      if (response.status === 401 && !isRetry) {
        logger.log("[AgentAPI] Got 401, attempting token refresh...");
        let ownedSession = entryAccess && entryRefresh
          ? resolveOwnedAuthSession(entryAccess, entryRefresh) : null;
        if (ownedSession?.accessToken && ownedSession.refreshToken) {
          if (ownedSession.accessToken === entryAccess && ownedSession.refreshToken === entryRefresh) {
            const refreshed = await tryRefreshToken();
            ownedSession = refreshed ? resolveOwnedAuthSession(entryAccess, entryRefresh) : null;
          }
          if (ownedSession?.accessToken && ownedSession.refreshToken) {
            logger.log("[AgentAPI] Token refreshed, retrying request...");
            return fetchStream(ownedSession.accessToken, true);
          }
        }
        if (resolveOwnedAuthSession(entryAccess, entryRefresh)) clearAuthStorage("agent_auth_failed");
        callbacks.onError?.(
          translateError("ERR_AUTH_TOKEN_INVALID"),
          "AUTH_ERROR",
          false,
        );
        return;
      }

      if (!response.ok) {
        const error = await response
          .json()
          .catch(() => ({ detail: "Unknown error" }));
        const errorMessage = resolveApiErrorMessage(error, `HTTP ${response.status}`);
        const errorCode = (
          error &&
          typeof error === "object" &&
          "error_code" in error &&
          typeof (error as { error_code?: unknown }).error_code === "string" &&
          (error as { error_code: string }).error_code.trim()
        )
          ? (error as { error_code: string }).error_code
          : "HTTP_ERROR";
        callbacks.onError?.(
          toUserErrorMessage(errorMessage),
          errorCode,
          // 会话仍在收尾（409 ERR_SESSION_BUSY）通常几秒后就会释放，提供重试。
          errorCode === "ERR_SESSION_BUSY",
        );
        return;
      }

      if (responseAgentRunId) callbacks.onRunStarted?.(responseAgentRunId);

      const reader = response.body?.getReader();
      if (!reader) {
        callbacks.onError?.("No response body", "NO_BODY", false);
        return;
      }

      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();

        if (done) {
          // Flush decoder buffer to avoid losing split UTF-8 characters at stream end.
          buffer += decoder.decode();
          break;
        }

        buffer += decoder.decode(value, { stream: true });

        // SSE events are separated by double newlines
        const events = buffer.split("\n\n");
        // Keep the last incomplete event in buffer
        buffer = events.pop() || "";

        for (const eventString of events) {
          if (!eventString.trim()) continue;

          const event = parseSSEEvent(eventString);
          if (!event) continue;
          telemetry.noteEvent();

          switch (event.type) {
            case "thinking": {
              const data = event.data as { message: string; step?: string };
              callbacks.onThinking?.(data.message, data.step);
              break;
            }
            case "thinking_content": {
              const data = event.data as { content: string; is_complete?: boolean };
              callbacks.onThinkingContent?.(data.content, data.is_complete);
              break;
            }
            case "context": {
              const data = event.data as {
                items: AgentContextItem[];
                token_count?: number;
              };
              callbacks.onContext?.(data.items || [], data.token_count);
              break;
            }
            case "content_start": {
              callbacks.onContentStart?.();
              break;
            }
            case "content": {
              const data = event.data as { text: string; index?: number };
              callbacks.onContent?.(data.text, data.index);
              break;
            }
            case "content_end": {
              callbacks.onContentEnd?.();
              break;
            }
            case "tool_call": {
              const data = event.data as {
                tool_use_id?: string;
                tool_name: string;
                arguments: Record<string, unknown>;
              };
              if (data.tool_use_id) {
                callbacks.onToolCall?.(data.tool_name, data.arguments, data.tool_use_id);
              } else {
                callbacks.onToolCall?.(data.tool_name, data.arguments);
              }
              break;
            }
            case "tool_result": {
              const data = event.data as {
                tool_use_id?: string;
                tool_name: string;
                status: string;
                data?: Record<string, unknown>;
                error?: string;
              };
              if (data.tool_use_id) {
                callbacks.onToolResult?.(
                  data.tool_name,
                  data.status,
                  data.data,
                  data.error,
                  data.tool_use_id,
                );
              } else {
                callbacks.onToolResult?.(
                  data.tool_name,
                  data.status,
                  data.data,
                  data.error,
                );
              }
              break;
            }
            case "conflict": {
              const data = event.data as {
                type: string;
                severity: string;
                title: string;
                description: string;
                suggestions: string[];
              };
              callbacks.onConflict?.(data);
              break;
            }
            case "file_created": {
              const data = event.data as {
                file_id: string;
                file_type: string;
                title: string;
              };
              callbacks.onFileCreated?.(data.file_id, data.file_type, data.title);
              break;
            }
            case "file_content": {
              const data = event.data as {
                file_id: string;
                chunk: string;
              };
              callbacks.onFileContent?.(data.file_id, data.chunk);
              break;
            }
            case "file_content_end": {
              const data = event.data as {
                file_id: string;
              };
              callbacks.onFileContentEnd?.(data.file_id);
              break;
            }
            case "file_edit_start": {
              const data = event.data as {
                file_id: string;
                title: string;
                total_edits: number;
                file_type?: string;
              };
              callbacks.onFileEditStart?.(data.file_id, data.title, data.total_edits, data.file_type);
              break;
            }
            case "file_edit_applied": {
              const data = event.data as {
                file_id: string;
                edit_index: number;
                op: string;
                old_preview?: string;
                new_preview?: string;
                success: boolean;
                error?: string;
              };
              callbacks.onFileEditApplied?.(
                data.file_id,
                data.edit_index,
                data.op,
                data.old_preview,
                data.new_preview,
                data.success,
                data.error,
              );
              break;
            }
            case "file_edit_end": {
              const data = event.data as {
                file_id: string;
                edits_applied: number;
                new_length: number;
                new_content?: string;
                original_content?: string;
                file_type?: string;
                title?: string;
                failed_count?: number;
                partial_success?: boolean;
                all_failed?: boolean;
                warnings?: string[];
              };
              callbacks.onFileEditEnd?.(
                data.file_id,
                data.edits_applied,
                data.new_length,
                data.new_content,
                data.original_content,
                data.file_type,
                data.title,
                {
                  failedCount: data.failed_count ?? 0,
                  partialSuccess: Boolean(data.partial_success),
                  allFailed: Boolean(data.all_failed),
                  warnings: Array.isArray(data.warnings) ? data.warnings : [],
                },
              );
              break;
            }
            case "skill_matched": {
              const data = event.data as {
                skill_id: string;
                skill_name: string;
                matched_trigger: string;
              };
              callbacks.onSkillMatched?.(data.skill_id, data.skill_name, data.matched_trigger);
              break;
            }
            case "skills_matched": {
              const data = event.data as {
                skills: Array<{ id: string; name: string; trigger: string; confidence: number }>;
                total_count: number;
              };
              callbacks.onSkillsMatched?.(data.skills);
              break;
            }
            case "agent_selected": {
              const data = event.data as {
                agent_type: string;
                agent_name: string;
                iteration?: number;
                max_iterations?: number;
                remaining?: number;
              };
              callbacks.onAgentSelected?.(
                data.agent_type,
                data.agent_name,
                data.iteration,
                data.max_iterations,
                data.remaining
              );
              break;
            }
            case "iteration_exhausted": {
              const data = event.data as {
                layer: "collaboration" | "tool_call";
                iterations_used: number;
                max_iterations: number;
                reason: string;
                last_agent?: string;
              };
              callbacks.onIterationExhausted?.(
                data.layer,
                data.iterations_used,
                data.max_iterations,
                data.reason,
                data.last_agent
              );
              break;
            }
            case "router_thinking": {
              const data = event.data as { message: string };
              callbacks.onRouterThinking?.(data.message);
              break;
            }
            case "router_decided": {
              const data = event.data as SSERouterDecidedData;
              callbacks.onRouterDecided?.(
                data.initial_agent,
                data.workflow_plan,
                data.workflow_agents,
                data.routing_metadata
              );
              break;
            }
            case "handoff": {
              const data = event.data as SSEHandoffData;
              callbacks.onHandoff?.(data);
              break;
            }
            case "workflow_stopped": {
              const data = event.data as SSEWorkflowStoppedData;
              callbacks.onWorkflowStopped?.(data);
              break;
            }
            case "workflow_complete": {
              const data = event.data as SSEWorkflowCompleteData;
              callbacks.onWorkflowComplete?.(data);
              break;
            }
            case "session_started": {
              const data = event.data as { session_id: string };
              const existing = debugContext.get() ?? {};
              debugContext.set({
                ...existing,
                trace_id: existing.trace_id ?? responseTraceId,
                request_id: existing.request_id ?? responseRequestId,
                agent_run_id: existing.agent_run_id ?? responseAgentRunId,
                project_id: existing.project_id ?? request.project_id,
                agent_session_id: data.session_id,
                route:
                  existing.route
                  ?? (typeof window !== "undefined" ? window.location.href : undefined),
                created_at: existing.created_at ?? new Date().toISOString(),
              });
              callbacks.onSessionStarted?.(data.session_id);
              break;
            }
            case "parallel_start": {
              const data = event.data as {
                execution_id: string;
                task_count: number;
                task_descriptions: string[];
                dropped_count?: number;
              };
              callbacks.onParallelStart?.(
                data.execution_id,
                data.task_count,
                data.task_descriptions,
                data.dropped_count ?? 0,
              );
              break;
            }
            case "parallel_task_start": {
              const data = event.data as {
                execution_id: string;
                task_id: string;
                task_type: string;
                description: string;
              };
              callbacks.onParallelTaskStart?.(data.execution_id, data.task_id, data.task_type, data.description);
              break;
            }
            case "parallel_task_end": {
              const data = event.data as {
                execution_id: string;
                task_id: string;
                status: string;
                result_preview?: string;
                error?: string;
              };
              callbacks.onParallelTaskEnd?.(data.execution_id, data.task_id, data.status, data.result_preview, data.error);
              break;
            }
            case "parallel_end": {
              const data = event.data as {
                execution_id: string;
                total_tasks: number;
                completed: number;
                failed: number;
                duration_ms: number;
              };
              callbacks.onParallelEnd?.(data.execution_id, data.total_tasks, data.completed, data.failed, data.duration_ms);
              break;
            }
            case "steering_received": {
              const data = event.data as {
                message_id: string;
                preview: string;
              };
              callbacks.onSteeringReceived?.(data.message_id, data.preview);
              break;
            }
            case "done": {
              const data = event.data as StreamDoneData;
              receivedTerminalEvent = true;
              callbacks.onDone?.(data);
              break;
            }
            case "error": {
              const data = event.data as StreamErrorEventData;
              receivedTerminalEvent = true;
              callbacks.onError?.(
                toUserErrorMessage(selectStreamErrorMessage(data)),
                data.code,
                data.retryable,
              );
              break;
            }
            case "quota_refunded": {
              notifyQuotaRefunded(event.data, callbacks);
              break;
            }
          }
        }
      }

      // Parse any trailing buffered event when upstream closes without final "\n\n".
      if (buffer.trim()) {
        const trailingEvents = buffer.split("\n\n").filter((item) => item.trim());
        for (const eventString of trailingEvents) {
          const event = parseSSEEvent(eventString);
          if (!event) continue;

          switch (event.type) {
            case "done": {
              const data = event.data as StreamDoneData;
              receivedTerminalEvent = true;
              callbacks.onDone?.(data);
              break;
            }
            case "error": {
              const data = event.data as StreamErrorEventData;
              receivedTerminalEvent = true;
              callbacks.onError?.(
                toUserErrorMessage(selectStreamErrorMessage(data)),
                data.code,
                data.retryable,
              );
              break;
            }
            case "quota_refunded": {
              notifyQuotaRefunded(event.data, callbacks);
              break;
            }
            default:
              break;
          }
        }
      }

      // If the stream ends without a terminal event, treat it as a retryable error.
      // This commonly happens when upstream proxies close idle SSE connections.
      if (!receivedTerminalEvent && !abortController.signal.aborted) {
        const fallbackMessage = i18n.t('chat:stream.connectionInterrupted');
        callbacks.onError?.(fallbackMessage, "STREAM_CLOSED", true);
      } else if (!receivedTerminalEvent) {
        telemetry.cancelled();
      }
    } catch (error: unknown) {
      const err = error as { name?: string; message?: string };
      if (err.name === "AbortError") {
        // Request was cancelled, don't call onError
        telemetry.cancelled();
        return;
      }
      callbacks.onError?.(
        toUserErrorMessage(err.message || "Stream error"),
        "STREAM_ERROR",
        true,
      );
    }
  };

  fetchStream();

  return abortController;
}



/**
 * Fetch intelligent next-step suggestions.
 *
 * Generates multiple short suggestions (~15 characters each) based on
 * project context and recent conversation history.
 *
 * @param projectId - Project ID
 * @param recentMessages - Optional recent conversation messages
 * @param count - Number of suggestions to generate (default 3, max 5)
 * @returns Promise with array of suggestion strings
 */
export async function fetchSuggestions(
  projectId: string,
  recentMessages?: Array<{ role: string; content: string }>,
  count: number = 3,
): Promise<string[]> {
  const entryAccess = getAccessToken();
  const entryRefresh = localStorage.getItem("refresh_token");
  const doFetch = async (accessToken: string | null = entryAccess, isRetry = false): Promise<string[]> => {
    try {
      const language = localStorage.getItem("zenstory-language") || "zh";

      const response = await fetch(`${getApiBase()}/api/v1/agent/suggest`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Accept-Language": language,
          ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
        },
        body: JSON.stringify({
          project_id: projectId,
          recent_messages: recentMessages?.slice(-5),
          count: Math.min(count, 5),
        }),
      });

      // Handle 401 - try to refresh token and retry once
      if (response.status === 401 && !isRetry) {
        let ownedSession = entryAccess && entryRefresh
          ? resolveOwnedAuthSession(entryAccess, entryRefresh) : null;
        if (ownedSession?.accessToken && ownedSession.refreshToken) {
          if (ownedSession.accessToken === entryAccess && ownedSession.refreshToken === entryRefresh) {
            const refreshed = await tryRefreshToken();
            ownedSession = refreshed ? resolveOwnedAuthSession(entryAccess, entryRefresh) : null;
          }
          if (ownedSession?.accessToken && ownedSession.refreshToken) {
            return doFetch(ownedSession.accessToken, true);
          }
        }
        return [];
      }

      // 429：小时限流或每日上限已满。建议只是锦上添花，静默退回空列表，
      // 不记错误、不打扰用户（调用方会显示本地兜底建议）。
      if (response.status === 429) {
        return [];
      }

      if (!response.ok) {
        logger.error("Failed to fetch suggestions:", response.status);
        return [];
      }

      const data = await response.json();
      return data.suggestions || [];
    } catch (error) {
      logger.error("Failed to fetch suggestions:", error);
      return [];
    }
  };

  return doFetch();
}

/**
 * Send a steering message to an active agent session.
 *
 * Uses the same API base/auth token/refresh flow as other agent APIs.
 *
 * @param sessionId - Active agent session ID
 * @param message - Steering content
 * @returns Promise with steering response payload
 */
export async function sendSteeringRequest(
  sessionId: string,
  message: string,
): Promise<{ message_id: string; queued: boolean }> {
  const entryAccess = getAccessToken();
  const entryRefresh = localStorage.getItem("refresh_token");
  const doSend = async (
    accessToken: string | null = entryAccess,
    isRetry = false,
  ): Promise<{ message_id: string; queued: boolean }> => {
    const language = localStorage.getItem("zenstory-language") || "zh";

    const response = await fetch(`${getApiBase()}/api/v1/agent/steer`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Accept-Language": language,
        ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      },
      body: JSON.stringify({
        session_id: sessionId,
        message,
      }),
    });

    if (response.status === 401 && !isRetry) {
      let ownedSession = entryAccess && entryRefresh
        ? resolveOwnedAuthSession(entryAccess, entryRefresh) : null;
      if (ownedSession?.accessToken && ownedSession.refreshToken) {
        if (ownedSession.accessToken === entryAccess && ownedSession.refreshToken === entryRefresh) {
          const refreshed = await tryRefreshToken();
          ownedSession = refreshed ? resolveOwnedAuthSession(entryAccess, entryRefresh) : null;
        }
        if (ownedSession?.accessToken && ownedSession.refreshToken) {
          return doSend(ownedSession.accessToken, true);
        }
      }
      if (resolveOwnedAuthSession(entryAccess, entryRefresh)) clearAuthStorage("agent_auth_failed");
      throw new Error(translateError("ERR_AUTH_TOKEN_INVALID"));
    }

    if (!response.ok) {
      const error = await response
        .json()
        .catch(() => ({ detail: `HTTP ${response.status}` }));
      const errorMessage = resolveApiErrorMessage(error, `HTTP ${response.status}`);
      throw new Error(toUserErrorMessage(errorMessage));
    }

    return response.json();
  };

  return doSend();
}

/**
 * 作者点了「停止生成」：请服务端结束这次运行。
 *
 * 服务端会在原 /stream 连接上发 workflow_stopped(user_stopped) + done，没有任何
 * 产出时再发 quota_refunded(kind=stopped)；调用方应继续读流，只有这里失败或
 * 迟迟等不到收尾时才直接断开连接。
 *
 * @returns 停止请求是否已被服务端记录
 */
export async function stopAgentRun(agentRunId: string): Promise<boolean> {
  try {
    const result = await api.post<{ stop_requested: boolean }>("/api/v1/agent/stop", {
      agent_run_id: agentRunId,
    });
    return result?.stop_requested === true;
  } catch (error) {
    logger.warn("[AgentAPI] stop request failed", error);
    return false;
  }
}
