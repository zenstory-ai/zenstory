import { beforeEach, describe, expect, it, vi } from "vitest";

const post = vi.fn();
vi.mock("../apiClient", () => ({ api: { post: (...args: unknown[]) => post(...args) } }));

import { rearmAiFileOpened, reportActivationEvent, resetActivationEventState } from "../activationEvents";

const URL = "/api/v1/activation/events";

describe("reportActivationEvent", () => {
  beforeEach(() => {
    post.mockReset();
    localStorage.clear();
    resetActivationEventState();
  });

  it("sends an event once the server confirmed it, also after a reload", async () => {
    post.mockResolvedValue({ recorded: true });

    await reportActivationEvent("u1", { event_name: "dashboard_viewed", is_mobile: false });
    await reportActivationEvent("u1", { event_name: "dashboard_viewed", is_mobile: false });
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith(URL, { event_name: "dashboard_viewed", is_mobile: false });

    resetActivationEventState(); // a new page load keeps the per-account confirmation
    await reportActivationEvent("u1", { event_name: "dashboard_viewed" });
    expect(post).toHaveBeenCalledTimes(1);

    await reportActivationEvent("u2", { event_name: "dashboard_viewed" });
    expect(post).toHaveBeenCalledTimes(2);
  });

  it("does not send twice while a request is in flight", async () => {
    let settle: (value: { recorded: boolean }) => void = () => {};
    post.mockReturnValue(new Promise((resolve) => { settle = resolve; }));

    const first = reportActivationEvent("u1", { event_name: "ai_send_blocked_quota", surface: "chat" });
    const second = reportActivationEvent("u1", { event_name: "ai_send_blocked_quota", surface: "dashboard" });
    settle({ recorded: true });
    await Promise.all([first, second]);

    expect(post).toHaveBeenCalledTimes(1);
  });

  it("checks each opened file at most once until an AI-written file is confirmed", async () => {
    post.mockResolvedValueOnce({ recorded: false }).mockResolvedValueOnce({ recorded: true });

    await reportActivationEvent("u1", { event_name: "ai_file_opened", file_id: "own" });
    await reportActivationEvent("u1", { event_name: "ai_file_opened", file_id: "own" });
    await reportActivationEvent("u1", { event_name: "ai_file_opened", file_id: "ai" });
    await reportActivationEvent("u1", { event_name: "ai_file_opened", file_id: "another" });

    expect(post.mock.calls.map(([, body]) => body.file_id)).toEqual(["own", "ai"]);
  });

  it("checks a file again only after the AI has written into it", async () => {
    post.mockResolvedValueOnce({ recorded: false }).mockResolvedValueOnce({ recorded: true });

    await reportActivationEvent("u1", { event_name: "ai_file_opened", file_id: "ch1" });
    await reportActivationEvent("u1", { event_name: "ai_file_opened", file_id: "ch1" });
    expect(post).toHaveBeenCalledTimes(1);

    rearmAiFileOpened("u2", "ch1"); // another account's write does not re-arm this one
    await reportActivationEvent("u1", { event_name: "ai_file_opened", file_id: "ch1" });
    expect(post).toHaveBeenCalledTimes(1);

    rearmAiFileOpened("u1", "ch1");
    await reportActivationEvent("u1", { event_name: "ai_file_opened", file_id: "ch1", while_ai_writing: true });
    expect(post).toHaveBeenCalledTimes(2);
  });

  it("swallows failures and allows a later retry", async () => {
    post.mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce({ recorded: true });

    await expect(reportActivationEvent("u1", { event_name: "dashboard_viewed" })).resolves.toBeUndefined();
    await reportActivationEvent("u1", { event_name: "dashboard_viewed" });

    expect(post).toHaveBeenCalledTimes(2);
  });

  it("does nothing without an account", async () => {
    await reportActivationEvent(null, { event_name: "dashboard_viewed" });
    expect(post).not.toHaveBeenCalled();
  });
});
