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
import { AgentStreamCallbackError, AgentStreamError, createAgentStreamTelemetry } from "../agentStreamTelemetry";

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

async function runStream(response: unknown, metadata?: Record<string, unknown>) {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));
  const onDone = vi.fn();
  const onError = vi.fn();
  streamAgentRequest({ project_id: "project-1", message: PROMPT, metadata }, { onDone, onError });
  await new Promise((resolve) => setTimeout(resolve, 50));
  return { onDone, onError };
}

describe("agent stream telemetry", () => {
  beforeEach(() => {
    trackEventMock.mockReset();
    captureExceptionMock.mockReset();
  });

  it("reports an author stop as its own outcome", async () => {
    await runStream(
      sseResponse([
        'event: thinking_content\ndata: {"content":"先读大纲"}\n\n',
        'event: content\ndata: {"text":"开头"}\n\n',
        'event: workflow_stopped\ndata: {"reason":"user_stopped","agent_type":"","message":"已停止生成。"}\n\n',
        "event: done\ndata: {}\n\n",
      ]),
    );

    expect(trackEventMock).toHaveBeenCalledTimes(1);
    expect(trackEventMock.mock.calls[0][0]).toBe("ai_chat_stopped");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("tags the outcome with where the message came from", async () => {
    await runStream(sseResponse(['event: done\ndata: {}\n\n']), { entry: "starter_example" });

    expect(trackEventMock).toHaveBeenCalledWith("ai_chat_completed", expect.objectContaining({ entry: "starter_example" }));
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
      entry: "typed",
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

  it("distinguishes fetch, reader and callback failures without error text", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError(PROMPT)));
    const closed = vi.fn();
    streamAgentRequest({ project_id: "project-1", message: PROMPT }, { onClosed: closed });
    await vi.waitFor(() => expect(closed).toHaveBeenCalled());
    expect(captureExceptionMock).toHaveBeenLastCalledWith(expect.any(Error), expect.objectContaining({ failure_phase: "fetch", exception_type: "TypeError" }));
    captureExceptionMock.mockClear();
    await runStream({ ...sseResponse([]), body: { getReader: () => ({ read: () => Promise.reject(new TypeError(PROMPT)) }) } });
    expect(captureExceptionMock).toHaveBeenLastCalledWith(expect.any(Error), expect.objectContaining({ failure_phase: "read", exception_type: "TypeError" }));
    captureExceptionMock.mockClear();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse(['event: content\ndata: {"text":"body"}\n\n'])));
    closed.mockClear();
    streamAgentRequest({ project_id: "project-1", message: PROMPT }, { onContent: () => { throw new DOMException(PROMPT, "NotFoundError"); }, onClosed: closed });
    await vi.waitFor(() => expect(closed).toHaveBeenCalled());
    expect(captureExceptionMock).toHaveBeenLastCalledWith(expect.any(Error), expect.objectContaining({ failure_phase: "callback", exception_type: "NotFoundError", retryable: false }));
    expect(JSON.stringify(captureExceptionMock.mock.calls)).not.toContain(PROMPT);
  });

  it("recognizes a non-AbortError rejection after the signal was cancelled", async () => {
    let rejectFetch!: (error: Error) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise((_, reject) => { rejectFetch = reject; })));
    const closed = vi.fn();
    const onError = vi.fn();
    const controller = streamAgentRequest({ project_id: "project-1", message: PROMPT }, { onClosed: closed, onError });
    controller.abort();
    rejectFetch(new TypeError(PROMPT));
    await vi.waitFor(() => expect(closed).toHaveBeenCalled());
    expect(trackEventMock).toHaveBeenCalledWith("ai_chat_cancelled", expect.any(Object));
    expect(onError).not.toHaveBeenCalled();
    expect(captureExceptionMock).not.toHaveBeenCalled();
  });

  describe("after the terminal frame", () => {
    /** Deliver the frames, then reject the next read like a connection reset. */
    function framesThenReadRejects(frames: string[]) {
      let index = 0;
      const read = vi.fn(async () => {
        if (index < frames.length) {
          return { done: false, value: new TextEncoder().encode(frames[index++]) };
        }
        throw new TypeError(PROMPT);
      });
      return { ...sseResponse([]), body: { getReader: () => ({ read }) } };
    }

    async function run(response: unknown, extra: Parameters<typeof streamAgentRequest>[1] = {}) {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));
      const callbacks = { onDone: vi.fn(), onError: vi.fn(), onClosed: vi.fn(), ...extra };
      streamAgentRequest({ project_id: "project-1", message: PROMPT }, callbacks);
      await vi.waitFor(() => expect(callbacks.onClosed).toHaveBeenCalledTimes(1));
      return callbacks;
    }

    const eventNames = () => trackEventMock.mock.calls.map(([name]) => name);

    it("still fails a round whose read rejects before any terminal frame", async () => {
      const { onError } = await run(framesThenReadRejects(['event: content\ndata: {"text":"partial"}\n\n']));

      expect(onError).toHaveBeenCalledWith(expect.any(String), "STREAM_ERROR", true);
      expect(eventNames()).toEqual(["ai_chat_failed"]);
      expect(captureExceptionMock).toHaveBeenCalledWith(expect.any(AgentStreamError), expect.objectContaining({ failure_phase: "read" }));
    });

    it("keeps a completed round when the tail read rejects", async () => {
      const { onDone, onError } = await run(framesThenReadRejects(['event: done\ndata: {"assistant_message_id":"m-1"}\n\n']));

      expect(onDone).toHaveBeenCalledTimes(1);
      expect(onError).not.toHaveBeenCalled();
      expect(eventNames()).toEqual(["ai_chat_completed", "ai_chat_tail_failed"]);
      expect(trackEventMock).toHaveBeenLastCalledWith("ai_chat_tail_failed", expect.objectContaining({ failure_phase: "read", exception_type: "TypeError" }));
      expect(captureExceptionMock).not.toHaveBeenCalled();
    });

    it("keeps an author stop and its delivered output when the tail read rejects", async () => {
      const onWorkflowStopped = vi.fn();
      const { onDone, onError } = await run(framesThenReadRejects([
        'event: workflow_stopped\ndata: {"reason":"user_stopped","message":"stopped"}\n\n',
        'event: done\ndata: {"stop_reason":"user_stopped","produced_output":true}\n\n',
      ]), { onWorkflowStopped });

      expect(onWorkflowStopped).toHaveBeenCalledTimes(1);
      expect(onDone).toHaveBeenCalledWith(expect.objectContaining({ stop_reason: "user_stopped", produced_output: true }));
      expect(onError).not.toHaveBeenCalled();
      expect(eventNames()).toEqual(["ai_chat_stopped", "ai_chat_tail_failed"]);
    });

    it("keeps the server error code and retry rule when the tail read rejects", async () => {
      const { onError } = await run(framesThenReadRejects([
        'event: error\ndata: {"message":"x","code":"ERR_AGENT_RUN_TIMEOUT","retryable":false}\n\n',
      ]));

      expect(onError).toHaveBeenCalledTimes(1);
      expect(onError).toHaveBeenCalledWith(expect.any(String), "ERR_AGENT_RUN_TIMEOUT", false);
      expect(trackEventMock).toHaveBeenCalledWith("ai_chat_failed", expect.objectContaining({ error_code: "ERR_AGENT_RUN_TIMEOUT" }));
      expect(eventNames()).not.toContain("ai_chat_cancelled");
    });

    it("still delivers a refund that arrived before the tail read rejected", async () => {
      const onQuotaRefunded = vi.fn();
      const { onError } = await run(framesThenReadRejects([
        'event: done\ndata: {"stop_reason":"user_stopped","produced_output":false}\n\n',
        'event: quota_refunded\ndata: {"refunded":true,"kind":"stopped"}\n\n',
      ]), { onQuotaRefunded });

      expect(onQuotaRefunded).toHaveBeenCalledWith("stopped", []);
      expect(onError).not.toHaveBeenCalled();
    });

    it("reports a throwing onDone as a callback error, not a network failure", async () => {
      const { onError } = await run(sseResponse(['event: done\ndata: {}\n\n']), {
        onDone: vi.fn(() => { throw new TypeError(PROMPT); }),
      });

      expect(onError).not.toHaveBeenCalled();
      expect(eventNames()).toEqual(["ai_chat_completed", "ai_chat_tail_failed"]);
      expect(captureExceptionMock).toHaveBeenCalledTimes(1);
      const [error, properties] = captureExceptionMock.mock.calls[0] as [Error, Record<string, unknown>];
      expect(error).toBeInstanceOf(AgentStreamCallbackError);
      expect(properties).toEqual(expect.objectContaining({ failure_phase: "callback", exception_type: "TypeError" }));
      expect(JSON.stringify(captureExceptionMock.mock.calls)).not.toContain(PROMPT);
    });
  });

  describe("device interruptions", () => {
    let visibility: DocumentVisibilityState = "visible";

    beforeEach(() => {
      visibility = "visible";
      Object.defineProperty(document, "visibilityState", { configurable: true, get: () => visibility });
    });

    afterEach(() => {
      Reflect.deleteProperty(document, "visibilityState");
      vi.unstubAllGlobals();
    });

    it("records a transport failure after the tab was hidden without reporting an exception", () => {
      let clock = 1_000;
      const telemetry = createAgentStreamTelemetry("project-1", () => clock);
      visibility = "hidden";
      document.dispatchEvent(new Event("visibilitychange"));
      clock += 30_000;
      visibility = "visible";
      document.dispatchEvent(new Event("visibilitychange"));

      telemetry.failed("STREAM_ERROR", true);

      expect(trackEventMock).toHaveBeenCalledWith("ai_chat_failed", expect.objectContaining({ hidden_ms: 30_000 }));
      expect(captureExceptionMock).not.toHaveBeenCalled();
    });

    it("records a transport failure while offline without reporting an exception", () => {
      vi.stubGlobal("navigator", { ...navigator, onLine: false });
      createAgentStreamTelemetry("project-1").failed("STREAM_ERROR", true);

      expect(trackEventMock).toHaveBeenCalledWith("ai_chat_failed", expect.objectContaining({ online: false }));
      expect(captureExceptionMock).not.toHaveBeenCalled();
    });

    it("still reports a transport failure of a visible, online tab", () => {
      createAgentStreamTelemetry("project-1").failed("STREAM_ERROR", true);

      expect(captureExceptionMock).toHaveBeenCalledWith(
        expect.any(AgentStreamError),
        expect.objectContaining({ hidden_ms: 0, online: true }),
      );
    });
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
