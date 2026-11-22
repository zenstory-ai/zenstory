import { captureException, trackEvent } from "./analytics";

/**
 * Outcome telemetry for one writing-agent stream. Events carry codes, timing
 * and correlation ids only: never the prompt, the reply or error text, which
 * can echo user content.
 */
export type AgentStreamFailurePhase = "fetch" | "response" | "read" | "parse" | "callback";

export interface AgentStreamTelemetry {
  noteFailure(phase: AgentStreamFailurePhase, error: unknown): void;
  /**
   * A read, parse or callback failure after the terminal frame. The round's
   * outcome is already settled; this is a separate diagnostic, never a failure.
   */
  noteFailureAfterTerminal(phase: AgentStreamFailurePhase, error: unknown): void;
  /** Record the HTTP response (status and correlation ids). */
  noteResponse(status: number, ids: { requestId?: string; agentRunId?: string }): void;
  /** Count one parsed SSE event, recording first visible/prose frame latencies when applicable. */
  noteEvent(eventType?: string): void;
  /** Count one tool call announced by the stream. */
  noteToolCall(): void;
  completed(): void;
  /** The author pressed stop and the server ended the run on this stream. */
  stopped(): void;
  failed(code: string | undefined, retryable: boolean | undefined): void;
  cancelled(): void;
}

/** Client-side codes for transport failures nobody else will ever report. */
const UNEXPECTED_TRANSPORT_CODES = new Set(["STREAM_CLOSED", "STREAM_ERROR", "NO_BODY"]);

/** SSE event types that deliver visible model/tool/thinking output to the browser. */
const FIRST_CONTENT_EVENT_TYPES = new Set([
  "thinking_content",
  "content",
  "file_content",
  "tool_call",
  "file_created",
]);

/** SSE event types that deliver visible prose text to chat or an open file. */
const FIRST_PROSE_EVENT_TYPES = new Set([
  "content",
  "file_content",
]);

const monotonicNow = (): number =>
  typeof performance !== "undefined" && typeof performance.now === "function"
    ? performance.now()
    : Date.now();

export class AgentStreamError extends Error {
  constructor(code: string) {
    super(`Agent stream failed: ${code}`);
    this.name = "AgentStreamError";
  }
}

/** A UI callback threw after the round had already ended; not a transport failure. */
export class AgentStreamCallbackError extends Error {
  constructor() {
    super("Agent stream callback threw after the terminal event");
    this.name = "AgentStreamCallbackError";
  }
}

export function createAgentStreamTelemetry(
  projectId: string,
  now: () => number = monotonicNow,
  /** Where the message came from (request `metadata.entry`): starter_example, first_message, … or typed. */
  entry = "typed",
): AgentStreamTelemetry {
  const startedAt = now();
  let httpStatus: number | undefined;
  let requestId: string | undefined;
  let agentRunId: string | undefined;
  let eventCount = 0;
  let toolCallCount = 0;
  let firstContentMs: number | undefined;
  let firstProseMs: number | undefined;
  let settled = false;
  let failurePhase: AgentStreamFailurePhase | undefined;
  let exceptionType: string | undefined;

  const elapsedMs = () => Math.round(Math.max(0, now() - startedAt));

  // Browsers suspend or drop the connections of hidden tabs (phones above all)
  // and of offline devices. A transport failure then is expected, not a defect.
  let hiddenMs = 0;
  let hiddenAt = document.visibilityState === "hidden" ? startedAt : undefined;
  const onVisibilityChange = () => {
    if (document.visibilityState === "hidden") {
      hiddenAt ??= now();
    } else if (hiddenAt !== undefined) {
      hiddenMs += now() - hiddenAt;
      hiddenAt = undefined;
    }
  };
  document.addEventListener("visibilitychange", onVisibilityChange);
  const hiddenTotalMs = () => Math.round(hiddenMs + (hiddenAt === undefined ? 0 : Math.max(0, now() - hiddenAt)));

  const baseProperties = () => ({
    project_id: projectId,
    entry,
    duration_ms: elapsedMs(),
    ...(firstContentMs !== undefined ? { first_content_ms: firstContentMs } : {}),
    ...(firstProseMs !== undefined ? { first_prose_ms: firstProseMs } : {}),
    http_status: httpStatus,
    events_received: eventCount,
    tool_calls: toolCallCount,
    request_id: requestId,
    agent_run_id: agentRunId,
    failure_phase: failurePhase,
    exception_type: exceptionType,
    hidden_ms: hiddenTotalMs(),
  });

  const settle = (): boolean => {
    if (settled) return false;
    settled = true;
    document.removeEventListener("visibilitychange", onVisibilityChange);
    return true;
  };

  const safeExceptionType = (error: unknown): string => {
    const safeNames = new Set(["Error", "TypeError", "SyntaxError", "AbortError", "NotFoundError", "NetworkError", "TimeoutError"]);
    const name = error instanceof Error || error instanceof DOMException ? error.name : undefined;
    return name && safeNames.has(name) ? name : "OtherError";
  };

  return {
    noteFailure(phase, error) {
      failurePhase = phase;
      exceptionType = safeExceptionType(error);
    },
    noteFailureAfterTerminal(phase, error) {
      const properties = {
        ...baseProperties(),
        failure_phase: phase,
        exception_type: safeExceptionType(error),
      };
      trackEvent("ai_chat_tail_failed", properties);
      if (phase === "callback") {
        captureException(new AgentStreamCallbackError(), {
          feature_area: "agent_stream",
          ...properties,
        });
      }
    },
    noteResponse(status, ids) {
      httpStatus = status;
      requestId = ids.requestId;
      agentRunId = ids.agentRunId;
    },
    noteEvent(eventType?: string) {
      eventCount += 1;
      if (!eventType) return;
      const isProse = FIRST_PROSE_EVENT_TYPES.has(eventType);
      const isVisible = isProse || FIRST_CONTENT_EVENT_TYPES.has(eventType);
      if (!isVisible) return;
      const elapsed = elapsedMs();
      if (firstContentMs === undefined) firstContentMs = elapsed;
      if (isProse && firstProseMs === undefined) firstProseMs = elapsed;
    },
    noteToolCall() {
      toolCallCount += 1;
      if (firstContentMs === undefined) firstContentMs = elapsedMs();
    },

    completed() {
      if (!settle()) return;
      trackEvent("ai_chat_completed", baseProperties());
    },
    failed(code, retryable) {
      const errorCode = code || "UNKNOWN";
      const properties = {
        ...baseProperties(),
        error_code: errorCode,
        retryable: Boolean(retryable),
        online: navigator.onLine,
      };
      if (!settle()) return;
      trackEvent("ai_chat_failed", properties);

      const isServerError = httpStatus !== undefined && httpStatus >= 500;
      const deviceInterrupted = properties.hidden_ms > 0 || properties.online === false;
      if (isServerError || (UNEXPECTED_TRANSPORT_CODES.has(errorCode) && !deviceInterrupted)) {
        captureException(new AgentStreamError(errorCode), {
          feature_area: "agent_stream",
          ...properties,
        });
      }
    },
    stopped() {
      if (!settle()) return;
      trackEvent("ai_chat_stopped", baseProperties());
    },
    cancelled() {
      if (!settle()) return;
      trackEvent("ai_chat_cancelled", baseProperties());
    },
  };
}
