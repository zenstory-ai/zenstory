import { beforeEach, describe, expect, it, vi } from "vitest";

const initMock = vi.fn();
const captureMock = vi.fn();
const optOutMock = vi.fn();
const optInMock = vi.fn();
const hasOptedOutMock = vi.fn(() => false);
const startExceptionAutocaptureMock = vi.fn();

vi.mock("posthog-js", () => ({
  default: {
    init: initMock,
    capture: captureMock,
    identify: vi.fn(),
    reset: vi.fn(),
    captureException: vi.fn(),
    startExceptionAutocapture: startExceptionAutocaptureMock,
    opt_out_capturing: optOutMock,
    opt_in_capturing: optInMock,
    has_opted_out_capturing: hasOptedOutMock,
  },
}));

const ENABLED_ENV = {
  DEV: false,
  MODE: "test",
  VITE_POSTHOG_ENABLED: "true",
  VITE_POSTHOG_KEY: "phc_test",
};

describe("UX phase 3 telemetry opt-out respect", () => {
  beforeEach(() => {
    vi.resetModules();
    initMock.mockReset();
    captureMock.mockReset();
    optOutMock.mockReset();
    optInMock.mockReset();
    hasOptedOutMock.mockReset();
    hasOptedOutMock.mockReturnValue(false);
    startExceptionAutocaptureMock.mockReset();
    localStorage.clear();
  });

  it("drops agent stream TTFT and editor safety events when zenstory:analytics-opt-out is true", async () => {
    localStorage.setItem("zenstory:analytics-opt-out", "true");

    const analytics = await import("../analytics");
    const { createAgentStreamTelemetry } = await import("../agentStreamTelemetry");
    const {
      trackEditorSaveConflict409,
      trackEditorDraftRecoveryShown,
      trackEditorDraftRecoveryResolved,
      trackEditorDraftStorageFailed,
    } = await import("../editorSafetyTelemetry");

    expect(analytics.initAnalytics(ENABLED_ENV)).toBe(false);

    let clock = 100;
    const stream = createAgentStreamTelemetry("project-1", () => clock);
    clock = 350;
    stream.noteEvent("content");
    clock = 600;
    stream.completed();

    trackEditorSaveConflict409({ surface: "manual", isMobile: false });
    trackEditorDraftRecoveryShown({ surface: "draft_recovery", isMobile: false, reason: "conflict" });
    trackEditorDraftRecoveryResolved({
      surface: "draft_recovery",
      isMobile: false,
      reason: "conflict",
      resolution: "restore_local",
    });
    trackEditorDraftStorageFailed({ surface: "draft_recovery", isMobile: false, reason: "page-exit" });

    expect(initMock).not.toHaveBeenCalled();
    expect(captureMock).not.toHaveBeenCalled();
  });

  it("emits allowlisted fields via PostHog when analytics is opted in", async () => {
    const analytics = await import("../analytics");
    const { createAgentStreamTelemetry } = await import("../agentStreamTelemetry");
    const { trackEditorSaveConflict409 } = await import("../editorSafetyTelemetry");

    expect(analytics.initAnalytics(ENABLED_ENV)).toBe(true);
    await analytics.whenAnalyticsReady();

    let clock = 1_000;
    const stream = createAgentStreamTelemetry("project-1", () => clock);
    clock = 1_250;
    stream.noteEvent("thinking_content");
    clock = 1_600;
    stream.noteEvent("content");
    clock = 2_000;
    stream.completed();

    trackEditorSaveConflict409({ surface: "auto_save", isMobile: true });

    expect(captureMock).toHaveBeenCalledWith(
      "ai_chat_completed",
      expect.objectContaining({
        first_content_ms: 250,
        first_prose_ms: 600,
      }),
    );
    expect(captureMock).toHaveBeenCalledWith(
      "editor_save_conflict_409",
      expect.objectContaining({
        surface: "auto_save",
        is_mobile: true,
        reason: "stale_write",
      }),
    );
  });
});
