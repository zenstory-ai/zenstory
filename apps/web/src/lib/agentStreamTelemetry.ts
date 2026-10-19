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
  /** Count one parsed SSE event. */
  noteEvent(): void;
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
  now: () => number = () => Date.now(),
  /** Where the message came from (request `metadata.entry`): starter_example, first_message, … or typed. */
  entry = "typed",
): AgentStreamTelemetry {
  const startedAt = now();
  let httpStatus: number | undefined;
  let requestId: string | undefined;
  let agentRunId: string | undefined;
  let eventCount = 0;
  let toolCallCount = 0;
  let settled = false;
  let failurePhase: AgentStreamFailurePhase | undefined;
  let exceptionType: string | undefined;

  const baseProperties = () => ({
    project_id: projectId,
    entry,
    duration_ms: Math.max(0, now() - startedAt),
    http_status: httpStatus,
    events_received: eventCount,
    tool_calls: toolCallCount,
    request_id: requestId,
    agent_run_id: agentRunId,
    failure_phase: failurePhase,
    exception_type: exceptionType,
  });

  const settle = (): boolean => {
    if (settled) return false;
    settled = true;
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
    noteEvent() {
      eventCount += 1;
    },
    noteToolCall() {
      toolCallCount += 1;
    },

    completed() {
      if (!settle()) return;
      trackEvent("ai_chat_completed", baseProperties());
    },
    failed(code, retryable) {
      if (!settle()) return;
      const errorCode = code || "UNKNOWN";
      const properties = {
        ...baseProperties(),
        error_code: errorCode,
        retryable: Boolean(retryable),
      };
      trackEvent("ai_chat_failed", properties);

      const isServerError = httpStatus !== undefined && httpStatus >= 500;
      if (UNEXPECTED_TRANSPORT_CODES.has(errorCode) || isServerError) {
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
