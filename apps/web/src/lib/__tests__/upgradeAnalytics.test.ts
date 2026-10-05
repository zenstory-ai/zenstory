import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const PENDING_STORAGE_KEY = "zenstory_upgrade_funnel_pending_events";
const trackEventMock = vi.fn();

vi.mock("../analytics", () => ({
  trackEvent: trackEventMock,
}));

describe("upgradeAnalytics", () => {
  let analytics: typeof import("../upgradeAnalytics");

  const flushAsync = async () => {
    for (let i = 0; i < 6; i += 1) {
      await Promise.resolve();
    }
  };

  const getPendingEvents = (): unknown[] =>
    JSON.parse(localStorage.getItem(PENDING_STORAGE_KEY) ?? "[]") as unknown[];

  beforeEach(async () => {
    vi.resetModules();
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-03-08T00:00:00.000Z"));
    trackEventMock.mockReset();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
      } as Response)
    );
    analytics = await import("../upgradeAnalytics");
  });

  afterEach(() => {
    vi.useRealTimers();
    delete window.__zenstoryTrackEvent;
    localStorage.removeItem("access_token");
    localStorage.removeItem(PENDING_STORAGE_KEY);
    vi.unstubAllGlobals();
  });

  it("dispatches expose event with source and surface", () => {
    const events: Array<Record<string, unknown>> = [];
    const listener = (event: Event) => {
      events.push((event as CustomEvent<Record<string, unknown>>).detail);
    };
    window.addEventListener(analytics.UPGRADE_FUNNEL_EVENT, listener);

    analytics.trackUpgradeExpose("chat_quota_blocked", "modal");

    window.removeEventListener(analytics.UPGRADE_FUNNEL_EVENT, listener);
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({
      event_name: "upgrade_entry_expose",
      action: "expose",
      source: "chat_quota_blocked",
      surface: "modal",
      occurred_at: "2026-03-08T00:00:00.000Z",
    });
    expect(trackEventMock).toHaveBeenCalledWith(
      "upgrade_entry_expose",
      expect.objectContaining({
        action: "expose",
        source: "chat_quota_blocked",
        surface: "modal",
      })
    );
  });

  it("invokes external tracker on click event", () => {
    const tracker = vi.fn();
    window.__zenstoryTrackEvent = tracker;

    analytics.trackUpgradeClick("material_upload_quota_blocked", "primary", "billing");

    expect(tracker).toHaveBeenCalledTimes(1);
    expect(tracker).toHaveBeenCalledWith(
      "upgrade_entry_click",
      expect.objectContaining({
        action: "click",
        source: "material_upload_quota_blocked",
        cta: "primary",
        destination: "billing",
      })
    );
  });

  it("tracks conversion as page event", () => {
    const events: Array<Record<string, unknown>> = [];
    const listener = (event: Event) => {
      events.push((event as CustomEvent<Record<string, unknown>>).detail);
    };
    window.addEventListener(analytics.UPGRADE_FUNNEL_EVENT, listener);

    analytics.trackUpgradeConversion("export_format_quota_blocked", "pricing");

    window.removeEventListener(analytics.UPGRADE_FUNNEL_EVENT, listener);
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({
      event_name: "upgrade_entry_conversion",
      action: "conversion",
      source: "export_format_quota_blocked",
      destination: "pricing",
      surface: "page",
    });
  });

  it("sends event to backend when auth token exists", async () => {
    localStorage.setItem("access_token", "token-123");

    analytics.trackUpgradeClick("chat_quota_blocked", "primary", "billing");
    await flushAsync();

    const fetchMock = vi.mocked(fetch);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/v1/subscription/upgrade-funnel-events",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({
          Authorization: "Bearer token-123",
        }),
      })
    );
  });

  it("queues events while unauthenticated and flushes after login on focus", async () => {
    analytics.trackUpgradeExpose("chat_quota_blocked", "modal");
    await flushAsync();

    const fetchMock = vi.mocked(fetch);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(getPendingEvents()).toHaveLength(1);

    localStorage.setItem("access_token", "token-123");
    window.dispatchEvent(new Event("focus"));
    await flushAsync();

    expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(1);
    expect(getPendingEvents()).toHaveLength(0);
  });

  it("retries transient failures and drains queue after retry", async () => {
    localStorage.setItem("access_token", "token-123");
    const fetchMock = vi.mocked(fetch);
    fetchMock
      .mockRejectedValueOnce(new Error("network-down"))
      .mockResolvedValue({
        ok: true,
        status: 200,
      } as Response);

    analytics.trackUpgradeClick("chat_quota_blocked", "primary", "billing");
    await flushAsync();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(getPendingEvents()).toHaveLength(1);

    await vi.advanceTimersByTimeAsync(5000);
    await flushAsync();

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(getPendingEvents()).toHaveLength(0);
  });

  it("drops unrecoverable validation failures to prevent queue poison", async () => {
    localStorage.setItem("access_token", "token-123");
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 422,
    } as Response);

    analytics.trackUpgradeClick("chat_quota_blocked", "primary", "billing");
    await flushAsync();
    await vi.advanceTimersByTimeAsync(0);
    await flushAsync();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(getPendingEvents()).toHaveLength(0);

    analytics.trackUpgradeExpose("material_upload_quota_blocked");
    await flushAsync();

    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("stops retrying after a 401 until a different token is stored", async () => {
    localStorage.setItem("access_token", "expired-token");
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValue({ ok: false, status: 401 } as Response);

    analytics.trackUpgradeClick("chat_quota_blocked", "primary", "billing");
    await flushAsync();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    // No 5s retry loop and no retry on focus with the same dead token.
    await vi.advanceTimersByTimeAsync(30_000);
    window.dispatchEvent(new Event("focus"));
    await flushAsync();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(getPendingEvents()).toHaveLength(1);

    // A new login resumes delivery.
    fetchMock.mockResolvedValue({ ok: true, status: 200 } as Response);
    localStorage.setItem("access_token", "fresh-token");
    window.dispatchEvent(new Event("focus"));
    await flushAsync();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(getPendingEvents()).toHaveLength(0);
  });

  it("clears queued events so the next account never sends them", async () => {
    analytics.trackUpgradeExpose("chat_quota_blocked", "modal");
    await flushAsync();
    expect(getPendingEvents()).toHaveLength(1);

    analytics.clearPendingUpgradeFunnelEvents();

    expect(localStorage.getItem(PENDING_STORAGE_KEY)).toBeNull();
    localStorage.setItem("access_token", "other-user-token");
    window.dispatchEvent(new Event("focus"));
    await flushAsync();
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });
});
