import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { trackEventMock, captureExceptionMock } = vi.hoisted(() => ({
  trackEventMock: vi.fn(),
  captureExceptionMock: vi.fn(),
}));

vi.mock("../analytics", () => ({
  trackEvent: trackEventMock,
  captureException: captureExceptionMock,
}));

vi.mock("../apiClient", () => ({
  tryRefreshToken: vi.fn(),
  getAccessToken: vi.fn(() => "test-access-token"),
  clearAuthStorage: vi.fn(),
  getApiBase: vi.fn(() => "http://localhost:8000"),
}));

import { streamAgentRequest } from "../agentApi";
import { AgentStreamError, createAgentStreamTelemetry } from "../agentStreamTelemetry";

const PROMPT = "第三章写主角在雨夜发现秘密";

function sseResponse(chunks: string[], status = 200) {
  let index = 0;
  const body = new ReadableStream<Uint8Array>({
    pull(controller) {
      if (index < chunks.length) {
        controller.enqueue(new TextEncoder().encode(chunks[index]));
        index += 1;
      } else {
        controller.close();
      }
    },
  });
  return {
    ok: status < 400,
    status,
    body,
    headers: new Headers({ "X-Request-ID": "req-1", "X-Agent-Run-ID": "run-1" }),
    json: async () => ({ error_code: "ERR_INTERNAL_SERVER_ERROR" }),
  };
}

async function runStream(response: unknown) {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));
  const onDone = vi.fn();
  const onError = vi.fn();
  streamAgentRequest({ project_id: "project-1", message: PROMPT }, { onDone, onError });
  await new Promise((resolve) => setTimeout(resolve, 50));
  return { onDone, onError };
}

describe("agent stream telemetry", () => {
  beforeEach(() => {
    trackEventMock.mockReset();
    captureExceptionMock.mockReset();
  });

  it("reports time to first visible output and an author stop as its own outcome", async () => {
    await runStream(
      sseResponse([
        'event: thinking_content\ndata: {"content":"先读大纲"}\n\n',
        'event: content\ndata: {"text":"开头"}\n\n',
        'event: workflow_stopped\ndata: {"reason":"user_stopped","agent_type":"","message":"已停止生成。"}\n\n',
        "event: done\ndata: {}\n\n",
      ]),
    );

    expect(trackEventMock).toHaveBeenCalledTimes(1);
    const [eventName, properties] = trackEventMock.mock.calls[0];
    expect(eventName).toBe("ai_chat_stopped");
    expect(typeof properties.first_output_ms).toBe("number");
  });

  it("leaves time to first output empty when nothing visible arrived", async () => {
    await runStream(sseResponse(['event: thinking_content\ndata: {"content":"先读大纲"}\n\n', "event: done\ndata: {}\n\n"]));

    const [eventName, properties] = trackEventMock.mock.calls[0];
    expect(eventName).toBe("ai_chat_completed");
    expect(properties.first_output_ms).toBeUndefined();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("reports ai_chat_completed with timing and tool counts, never content", async () => {
    const { onDone } = await runStream(
      sseResponse([
        'event: tool_call\ndata: {"tool_name":"edit_file","arguments":{}}\n\n',
        'event: content\ndata: {"text":"雨夜里，她推开了门"}\n\n',
        'event: done\ndata: {"apply_action":"insert"}\n\n',
      ]),
    );

    expect(onDone).toHaveBeenCalled();
    expect(trackEventMock).toHaveBeenCalledTimes(1);
    const [eventName, properties] = trackEventMock.mock.calls[0] as [string, Record<string, unknown>];
    expect(eventName).toBe("ai_chat_completed");
    expect(properties).toMatchObject({
      project_id: "project-1",
      http_status: 200,
      tool_calls: 1,
      events_received: 3,
      request_id: "req-1",
      agent_run_id: "run-1",
    });
    expect(typeof properties.duration_ms).toBe("number");
    const serialized = JSON.stringify(trackEventMock.mock.calls);
    expect(serialized).not.toContain("雨夜");
    expect(captureExceptionMock).not.toHaveBeenCalled();
  });

  it("reports server error events as ai_chat_failed without capturing an exception", async () => {
    const { onError } = await runStream(
      sseResponse([
        'event: error\ndata: {"message":"quota exceeded for 第三章","code":"ERR_QUOTA_EXCEEDED","retryable":false}\n\n',
      ]),
    );

    expect(onError).toHaveBeenCalled();
    expect(trackEventMock).toHaveBeenCalledWith(
      "ai_chat_failed",
      expect.objectContaining({ error_code: "ERR_QUOTA_EXCEEDED", retryable: false }),
    );
    expect(JSON.stringify(trackEventMock.mock.calls)).not.toContain("第三章");
    expect(captureExceptionMock).not.toHaveBeenCalled();
  });

  it("captures an exception when the stream closes without a terminal event", async () => {
    await runStream(sseResponse(['event: content\ndata: {"text":"partial"}\n\n']));

    expect(trackEventMock).toHaveBeenCalledWith(
      "ai_chat_failed",
      expect.objectContaining({ error_code: "STREAM_CLOSED", retryable: true, events_received: 1 }),
    );
    expect(captureExceptionMock).toHaveBeenCalledWith(
      expect.any(AgentStreamError),
      expect.objectContaining({ feature_area: "agent_stream", error_code: "STREAM_CLOSED" }),
    );
  });

  it("captures an exception for HTTP 5xx responses", async () => {
    await runStream(sseResponse([], 502));

    expect(trackEventMock).toHaveBeenCalledWith(
      "ai_chat_failed",
      expect.objectContaining({ http_status: 502 }),
    );
    expect(captureExceptionMock).toHaveBeenCalledTimes(1);
    const [error] = captureExceptionMock.mock.calls[0] as [Error];
    expect(error.message).not.toContain(PROMPT);
  });

  it("settles only once per stream", () => {
    const telemetry = createAgentStreamTelemetry("p", () => 0);
    telemetry.completed();
    telemetry.failed("STREAM_ERROR", true);
    telemetry.cancelled();

    expect(trackEventMock).toHaveBeenCalledTimes(1);
    expect(trackEventMock).toHaveBeenCalledWith("ai_chat_completed", expect.any(Object));
  });
});
