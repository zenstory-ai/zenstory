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
  IterationExhaustedFiles,
  IterationExhaustedLayer,
  SSEEvent,
  SSEEventType,
  SSEIterationExhaustedData,
  SSEWorkflowStoppedData,
} from "../types";
import { api, authenticatedFetch } from "./apiClient";
import { debugContext } from "./debugContext";
import { resolveApiErrorMessage, toUserErrorMessage, translateError } from "./errorHandler";
import { logger } from "./logger";
import { formatRoundDelivery, parseIterationExhaustedFiles, parseWrittenFiles } from "./chatRoundEnd";
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

const detectMobileViewport = (): boolean | undefined => {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return undefined;
  }
  try {
    const mediaQuery = window.matchMedia("(max-width: 767px)");
    return typeof mediaQuery?.matches === "boolean" ? mediaQuery.matches : undefined;
  } catch {
    return undefined;
  }
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
 *   - metadata: Additional request metadata
 * @param callbacks - Event callback handlers for each SSE event type
 * @returns AbortController for cancelling the stream request
 */
type AgentStreamCallbacks = Parameters<typeof streamAgentRequest>[1];

type StreamErrorEventData = {
  message?: string;
  code?: string;
  retryable?: boolean;
  reason?: string;
  written_files?: unknown;
  unwritten_files?: unknown;
};

/**
 * quota_refunded 帧的说法：失控停止未扣额（历史兼容） / 去AI味零改动未扣额 /
 * 模型服务或预检出错未扣额 / 模型开始处理前作者点了停止未扣额。
 */
export type QuotaRefundKind = "no_progress" | "no_change" | "error" | "stopped";

/** 会话仍在收尾、账号任务名额已满或当前网络额度已用完：服务端没有扣额，也没有开始运行。 */
const MANUALLY_RETRYABLE_REFUSALS = new Set([
  "ERR_SESSION_BUSY",
  "ERR_ACCOUNT_RUNS_BUSY",
  "ERR_QUOTA_FREE_NETWORK_DAILY_EXCEEDED",
]);

/** 作者停止、这一轮被退还时，服务端移除的「这一轮新建、还是空白」的文件。 */
export interface RemovedPlaceholderFile {
  id: string;
  title: string;
}

/**
 * done 帧。作者停止的一轮额外带 stop_reason / charged（真实结算后是否计入今日额度） / produced_output，
 * 写进了正文的文件不为空时还带 written_files（与刷新后 stop_outcome.written_files 同一份）。
 */
export interface StreamDoneData {
  assistant_message_id?: string;
  session_id?: string;
  file_mutated?: boolean;
  stop_reason?: string;
  charged?: boolean;
  produced_output?: boolean;
  written_files?: { id?: string; title?: string; words?: number; partial?: boolean }[];
  unwritten_files?: { id?: string; title?: string }[];
  /** Server-confirmed request-level delivery remained incomplete. */
  delivery_incomplete?: boolean;
  /** Server-confirmed successful saved output, including writes that cannot be named. */
  saved_output?: boolean;
  /** Completed round: the author asked for body text and no file was written. */
  no_file_changes?: boolean;
  /** Incomplete round: a step was only written out as text and never ran. */
  unexecuted_tool_text?: boolean;
}

function streamErrorForAuthor(data: StreamErrorEventData): string {
  const message = toUserErrorMessage(selectStreamErrorMessage(data));
  const delivery = formatRoundDelivery(i18n.t, parseWrittenFiles(data.written_files),
    parseWrittenFiles(data.unwritten_files), i18n.language);
  return delivery ? `${message}\n${delivery}` : message;
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
    payload.kind === "no_progress" || payload.kind === "no_change" || payload.kind === "stopped"
      ? payload.kind
      : "error";
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

function resolveTelemetryEventType(event: SSEEvent): SSEEventType | undefined {
  const payload = event.data && typeof event.data === "object"
    ? (event.data as Record<string, unknown>)
    : null;
  switch (event.type) {
    case "thinking_content":
      return typeof payload?.content === "string" && payload.content.length > 0
        ? "thinking_content"
        : undefined;
    case "content":
      return typeof payload?.text === "string" && payload.text.length > 0
        ? "content"
        : undefined;
    case "file_content":
      return typeof payload?.chunk === "string" && payload.chunk.length > 0
        ? "file_content"
        : undefined;
    default:
      return event.type;
  }
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
      editIndex: number,
      op: string,
    ) => void;
    onFileEditEnd?: (
      fileId: string,
      editsApplied: number,
      newContent?: string,
      originalContent?: string,
      fileType?: string,
      title?: string,
      outcome?: FileEditOutcome,
    ) => void;
    onSkillMatched?: (skillId: string, skillName: string, matchedTrigger: string) => void;
    onIterationExhausted?: (
      layer: IterationExhaustedLayer,
      iterationsUsed: number,
      maxIterations: number,
      reason: string,
      lastAgent?: string,
      limit?: string,
      files?: IterationExhaustedFiles
    ) => void;
    onWorkflowStopped?: (data: SSEWorkflowStoppedData) => void;
    onSessionStarted?: (sessionId: string) => void;
    /** 响应头到达：本次运行的 X-Agent-Run-ID，停止生成时要用。 */
    onRunStarted?: (agentRunId: string) => void;
    onSteeringReceived?: (messageId: string, preview: string) => void;
    /** Earlier conversation was summarized before this round; `summary` is what the AI now works from. */
    onHistoryCompacted?: (summary: string) => void;
    onDone?: (data: StreamDoneData) => void;
    onError?: (message: string, code?: string, retryable?: boolean) => void;
    /** 后端确实退还了这一轮的 AI 消息（在终止帧之后到达）；附带被移除的空白文件。 */
    onQuotaRefunded?: (kind: QuotaRefundKind, removedFiles: RemovedPlaceholderFile[]) => void;
    /**
     * 这次请求的连接已经结束：服务端发完最后一帧（done 之后还可能有 quota_refunded）并关闭，
     * 或请求被中止 / 失败。每次请求只调用一次。
     */
    onClosed?: () => void;
  },
): AbortController {
  const abortController = new AbortController();
  const traceId = generateTraceId();
  const entry = request.metadata?.entry;
  const telemetry = createAgentStreamTelemetry(
    request.project_id,
    undefined,
    typeof entry === "string" && entry ? entry : "typed",
  );
  const callbacks = withOutcomeTelemetry(rawCallbacks, telemetry);

  const fetchStream = async () => {
    let failurePhase: import("./agentStreamTelemetry").AgentStreamFailurePhase = "fetch";
    // Track whether we received a terminal SSE event ("done" or "error").
    // If the server/proxy closes the connection without a terminal event,
    // the frontend would otherwise remain stuck in "processing" state.
    let receivedTerminalEvent = false;
    let lastAttempt = 0;
    let responseRequestId: string | undefined;
    let responseTraceId = traceId;
    let responseAgentRunId: string | undefined;
    const rawMetadata = request.metadata || {};
    const detectedMobile =
      typeof rawMetadata.is_mobile === "boolean" ? undefined : detectMobileViewport();
    const requestMetadata =
      detectedMobile === undefined
        ? rawMetadata
        : { ...rawMetadata, is_mobile: detectedMobile };
    try {
      const response = await authenticatedFetch(
        "/api/v1/agent/stream",
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Accept: "text/event-stream",
            [TRACE_ID_HEADER]: traceId,
            [CLIENT_STOP_CONTRACT_HEADER]: "1",
          },
          body: JSON.stringify({
            project_id: request.project_id,
            message: request.message,
            session_id: request.session_id,
            selected_text: request.selected_text,
            ui_action: request.ui_action,
            metadata: requestMetadata,
            selected_skill_ids: request.selected_skill_ids?.length
              ? request.selected_skill_ids
              : undefined,
          }),
          signal: abortController.signal,
        },
        {
          includeLanguage: true,
          clearOnRefreshFailureReason: "agent_auth_failed",
          onResponse: (res, attempt) => {
            lastAttempt = attempt;
            failurePhase = "response";
            responseRequestId = res.headers?.get?.("X-Request-ID") ?? undefined;
            responseTraceId = res.headers?.get?.(TRACE_ID_HEADER) ?? traceId;
            responseAgentRunId = res.headers?.get?.("X-Agent-Run-ID") ?? undefined;
            telemetry.noteResponse(res.status, {
              requestId: responseRequestId,
              agentRunId: responseAgentRunId,
            });
            debugContext.set({
              trace_id: responseTraceId,
              request_id: responseRequestId,
              agent_run_id: responseAgentRunId,
              project_id: request.project_id,
              agent_session_id: request.session_id ?? null,
              route: window.location.href,
              created_at: new Date().toISOString(),
            });
          },
        },
      );

      // Initial 401 that could not be recovered via token refresh
      if (response.status === 401 && lastAttempt === 0) {
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
          // 这些拒绝都发生在开始运行之前，之后可能恢复（比如换了网络）：可以手动重试（这里从不自动重发）。
          MANUALLY_RETRYABLE_REFUSALS.has(errorCode),
        );
        return;
      }

      if (responseAgentRunId) callbacks.onRunStarted?.(responseAgentRunId);

      failurePhase = "read";
      const reader = response.body?.getReader();
      if (!reader) {
        callbacks.onError?.("No response body", "NO_BODY", false);
        return;
      }

      const decoder = new TextDecoder();
      let buffer = "";

      const dispatchSSEEvent = (event: SSEEvent) => {
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
              edit_index: number;
              op: string;
            };
            callbacks.onFileEditApplied?.(data.edit_index, data.op);
            break;
          }
          case "file_edit_end": {
            const data = event.data as {
              file_id: string;
              edits_applied: number;
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
          case "iteration_exhausted": {
            const data = event.data as SSEIterationExhaustedData;
            const parsed = parseIterationExhaustedFiles(data.unfinished_files, data.rolled_back_files);
            const files = data.layer === "empty_file_correction" || Object.keys(parsed).length > 0
              ? parsed
              : undefined;
            callbacks.onIterationExhausted?.(
              data.layer,
              data.iterations_used,
              data.max_iterations,
              data.reason,
              data.last_agent,
              data.limit,
              ...(files ? [files] : []),
            );
            break;
          }
          case "workflow_stopped": {
            const data = event.data as SSEWorkflowStoppedData;
            callbacks.onWorkflowStopped?.(data);
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
              route: existing.route ?? window.location.href,
              created_at: existing.created_at ?? new Date().toISOString(),
            });
            callbacks.onSessionStarted?.(data.session_id);
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
          case "history_compacted": {
            const data = event.data as { summary?: unknown };
            callbacks.onHistoryCompacted?.(typeof data.summary === "string" ? data.summary : "");
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
              streamErrorForAuthor(data),
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
      };

      let streamDone = false;
      while (!streamDone) {
        failurePhase = "read";
        const { done, value } = await reader.read();

        if (done) {
          // Flush decoder buffer and parse any trailing event when upstream closes without final "\n\n".
          buffer += decoder.decode();
          streamDone = true;
        } else {
          buffer += decoder.decode(value, { stream: true });
        }

        // SSE events are separated by double newlines
        const events = buffer.split("\n\n");
        buffer = streamDone ? "" : (events.pop() || "");

        for (const eventString of events) {
          if (!eventString.trim()) continue;
          failurePhase = "parse";
          const event = parseSSEEvent(eventString);
          if (!event) continue;
          telemetry.noteEvent(resolveTelemetryEventType(event));
          failurePhase = "callback";
          dispatchSSEEvent(event);
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
      if (abortController.signal.aborted || err.name === "AbortError") {
        // Request was cancelled, don't call onError
        telemetry.cancelled();
        return;
      }
      if (receivedTerminalEvent) {
        // 完成、作者停止或服务端错误已经交给界面。之后读 quota_refunded 等尾帧时
        // 连接断开，或业务回调自己抛错，都不能把这一轮改成失败、覆盖原错误码或
        // 露出重发入口；单独记诊断，不当作网络读取失败。
        telemetry.noteFailureAfterTerminal(failurePhase, error);
        if (failurePhase === "callback") {
          logger.error("[AgentAPI] Stream callback threw after the terminal event:", error);
        } else {
          logger.warn("[AgentAPI] Stream tail failed after the terminal event:", failurePhase);
        }
        return;
      }
      telemetry.noteFailure(failurePhase, error);
      const transportFailed = failurePhase !== "callback";
      callbacks.onError?.(
        // Browser network messages ("network error", "Load failed") mean nothing to authors.
        transportFailed ? i18n.t('chat:stream.connectionInterrupted') : toUserErrorMessage(err.message || "Stream error"),
        "STREAM_ERROR",
        transportFailed,
      );
    }
  };

  void fetchStream().finally(() => callbacks.onClosed?.());

  return abortController;
}

/**
 * Delay before performing a single read-only GET reconcile after an unexpected SSE
 * disconnect so the backend has time to commit any in-flight partial/final reply.
 */
export const SSE_DISCONNECT_RECONCILE_DELAY_MS = 1500;

/**
 * True when an SSE error represents an unexpected transport drop (closed/errored stream)
 * rather than an explicit server error, quota refusal, or author-triggered abort.
 * Callers may run a single read-only GET reconcile; NEVER automatically retry POST.
 */
export function isUnexpectedStreamDisconnect(
  code: string | null | undefined,
  retryable: boolean | undefined,
): boolean {
  return retryable === true && (code === "STREAM_CLOSED" || code === "STREAM_ERROR");
}



/**
 * 输入建议被限流（429）后，到 Retry-After 之前不再请求：每日上限要等到北京时间零点，
 * 期间每一轮都去撞一次 429 没有意义。按登录会话记（refresh token 尾段），换账号不受影响。
 */
const SUGGEST_BLOCKED_KEY = "zenstory_suggest_blocked_until";
const SUGGEST_BLOCK_FALLBACK_SECONDS = 10 * 60;

function suggestSessionTag(refreshToken: string | null): string {
  return (refreshToken || "").slice(-16);
}

function suggestionsBlocked(refreshToken: string | null): boolean {
  try {
    const raw = localStorage.getItem(SUGGEST_BLOCKED_KEY);
    if (!raw) return false;
    const { until, session } = JSON.parse(raw) as { until?: unknown; session?: unknown };
    if (typeof until === "number" && Date.now() < until && session === suggestSessionTag(refreshToken)) {
      return true;
    }
    localStorage.removeItem(SUGGEST_BLOCKED_KEY);
  } catch {
    // 存储不可用或内容损坏：照常请求
  }
  return false;
}

function blockSuggestions(retryAfter: string | null | undefined, refreshToken: string | null): void {
  const seconds = Number(retryAfter);
  const wait = Number.isFinite(seconds) && seconds > 0 ? seconds : SUGGEST_BLOCK_FALLBACK_SECONDS;
  try {
    localStorage.setItem(
      SUGGEST_BLOCKED_KEY,
      JSON.stringify({ until: Date.now() + wait * 1000, session: suggestSessionTag(refreshToken) }),
    );
  } catch {
    // 存储不可用：下次照常请求
  }
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
 * @returns Suggestion strings (including a valid empty list), or null when unavailable
 */
export async function fetchSuggestions(
  projectId: string,
  recentMessages?: Array<{ role: string; content: string }>,
  count: number = 3,
): Promise<string[] | null> {
  const entryRefresh = localStorage.getItem("refresh_token");
  if (suggestionsBlocked(entryRefresh)) return null;

  try {
    const response = await authenticatedFetch(
      "/api/v1/agent/suggest",
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          project_id: projectId,
          recent_messages: recentMessages?.slice(-5),
          count: Math.min(count, 5),
        }),
      },
      { includeLanguage: true },
    );

    if (response.status === 401) {
      return null;
    }

    // 429：小时限流或每日上限已满。建议只是锦上添花，静默退回失败标记，
    // 不记错误、不打扰用户（调用方会显示本地兜底建议）；到 Retry-After 之前不再请求。
    if (response.status === 429) {
      blockSuggestions(response.headers?.get?.("Retry-After"), entryRefresh);
      return null;
    }

    if (!response.ok) {
      logger.error("Failed to fetch suggestions:", response.status);
      return null;
    }

    const data = await response.json();
    return Array.isArray(data.suggestions) && data.suggestions.every((s: unknown) => typeof s === "string")
      ? data.suggestions.slice(0, 3)
      : null;
  } catch (error) {
    logger.error("Failed to fetch suggestions:", error);
    return null;
  }
}

/**
 * Send a steering message to an active agent session.
 *
 * Uses the shared `api.post` client so auth/refresh and HTTP error status
 * (e.g. 404 when a round already ended) are preserved consistently.
 *
 * @param sessionId - Active agent session ID
 * @param message - Steering content
 * @returns Promise with steering response payload
 */
export async function sendSteeringRequest(
  sessionId: string,
  message: string,
): Promise<{ message_id: string; queued: boolean }> {
  return api.post<{ message_id: string; queued: boolean }>("/api/v1/agent/steer", {
    session_id: sessionId,
    message,
  });
}

/**
 * 作者点了「停止生成」：请服务端结束这次运行。
 *
 * 服务端会在原 /stream 连接上发 workflow_stopped(user_stopped) + done，没有任何
 * 产出时再发 quota_refunded(kind=stopped)；调用方应继续读流，只有这里失败或
 * 迟迟等不到收尾时才直接断开连接。
 *
 * `keepFileIds`：编辑器里作者还有没存下的字的文件（停止前的保存失败或超时）。
 * 这一轮被退还时服务端不把它们当作空白占位文件移除。
 *
 * @returns 停止请求是否已被服务端记录
 */
export async function stopAgentRun(agentRunId: string, keepFileIds: string[] = []): Promise<boolean> {
  try {
    const result = await api.post<{ stop_requested: boolean }>("/api/v1/agent/stop", {
      agent_run_id: agentRunId,
      ...(keepFileIds.length > 0 ? { keep_file_ids: keepFileIds } : {}),
    });
    return result?.stop_requested === true;
  } catch (error) {
    logger.warn("[AgentAPI] stop request failed", error);
    return false;
  }
}
