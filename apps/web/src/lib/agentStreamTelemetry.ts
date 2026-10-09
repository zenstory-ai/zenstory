import { captureException, trackEvent } from "./analytics";

/**
 * Outcome telemetry for one writing-agent stream. Events carry codes, timing
 * and correlation ids only: never the prompt, the reply or error text, which
 * can echo user content.
 */
export interface AgentStreamTelemetry {
  /** Record the HTTP response (status and correlation ids). */
  noteResponse(status: number, ids: { requestId?: string; agentRunId?: string }): void;
  /** Count one parsed SSE event. */
  noteEvent(): void;
  /** Count one tool call announced by the stream. */
  noteToolCall(): void;
  /** First visible output (reply text or file text) reached the client. */
  noteFirstOutput(): void;
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

export function createAgentStreamTelemetry(
  projectId: string,
  now: () => number = () => Date.now(),
): AgentStreamTelemetry {
  const startedAt = now();
  let httpStatus: number | undefined;
  let requestId: string | undefined;
  let agentRunId: string | undefined;
  let eventCount = 0;
  let toolCallCount = 0;
  let firstOutputMs: number | undefined;
  let settled = false;

  const baseProperties = () => ({
    project_id: projectId,
    duration_ms: Math.max(0, now() - startedAt),
    http_status: httpStatus,
    events_received: eventCount,
    tool_calls: toolCallCount,
    // Time from send to the first reply/file text; undefined when none arrived.
    first_output_ms: firstOutputMs,
    request_id: requestId,
    agent_run_id: agentRunId,
  });

  const settle = (): boolean => {
    if (settled) return false;
    settled = true;
    return true;
  };

  return {
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
    noteFirstOutput() {
      if (firstOutputMs === undefined) firstOutputMs = Math.max(0, now() - startedAt);
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
