import { beforeEach, describe, expect, it, vi } from "vitest";

const initMock = vi.fn();
const captureMock = vi.fn();
const identifyMock = vi.fn();
const resetMock = vi.fn();
const captureExceptionMock = vi.fn();
const startExceptionAutocaptureMock = vi.fn();
const optOutMock = vi.fn();
const optInMock = vi.fn();

vi.mock("posthog-js", () => ({
  default: {
    init: initMock,
    capture: captureMock,
    identify: identifyMock,
    reset: resetMock,
    captureException: captureExceptionMock,
    startExceptionAutocapture: startExceptionAutocaptureMock,
    opt_out_capturing: optOutMock,
    opt_in_capturing: optInMock,
  },
}));

const ENABLED_ENV = {
  DEV: false,
  MODE: "test",
  VITE_POSTHOG_ENABLED: "true",
  VITE_POSTHOG_KEY: "phc_test",
};

const ACCESS_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1MSJ9.sig";


describe("analytics", () => {
  beforeEach(() => {
    vi.resetModules();
    initMock.mockReset();
    captureMock.mockReset();
    identifyMock.mockReset();
    resetMock.mockReset();
    captureExceptionMock.mockReset();
    startExceptionAutocaptureMock.mockReset();
    optOutMock.mockReset();
    optInMock.mockReset();
    localStorage.clear();
    window.history.replaceState(null, "", "/");
    document.title = "Dashboard";
  });

  it("does not initialize when disabled", async () => {
    const analytics = await import("../analytics");

    expect(
      analytics.initAnalytics({
        DEV: false,
        MODE: "test",
        VITE_POSTHOG_ENABLED: "false",
        VITE_POSTHOG_KEY: "phc_test",
      })
    ).toBe(false);
    expect(initMock).not.toHaveBeenCalled();
  });

  it("initializes PostHog and captures events when enabled", async () => {
    const analytics = await import("../analytics");

    expect(
      analytics.initAnalytics({
        DEV: false,
        MODE: "test",
        VITE_POSTHOG_ENABLED: " TRUE \n",
        VITE_POSTHOG_KEY: "phc_test",
        VITE_POSTHOG_HOST: "https://us.i.posthog.com",
      })
    ).toBe(true);

    expect(initMock).toHaveBeenCalledWith(
      "phc_test",
      expect.objectContaining({
        api_host: "https://us.i.posthog.com",
        person_profiles: "identified_only",
        disable_session_recording: true,
      })
    );
    expect(startExceptionAutocaptureMock).toHaveBeenCalledTimes(1);

    analytics.trackEvent("dashboard_view", { source: "test" });
    expect(captureMock).toHaveBeenCalledWith(
      "dashboard_view",
      expect.objectContaining({
        source: "test",
        page_path: window.location.pathname,
        page_title: "Dashboard",
      })
    );
  });

  it("identifies, resets, captures exceptions, and dedupes page views", async () => {
    const analytics = await import("../analytics");

    analytics.initAnalytics({
      DEV: false,
      MODE: "test",
      VITE_POSTHOG_ENABLED: "true",
      VITE_POSTHOG_KEY: "phc_test",
    });

    analytics.identifyUser({
      id: "user-1",
      email: "user@example.com",
      username: "writer",
      is_superuser: false,
    });
    expect(identifyMock).toHaveBeenCalledWith(
      "user-1",
      expect.objectContaining({
        is_superuser: false,
      })
    );
    const identifyPayload = identifyMock.mock.calls[0]?.[1] as Record<string, unknown> | undefined;
    expect(identifyPayload).toBeDefined();
    expect(identifyPayload).not.toHaveProperty("email");
    expect(identifyPayload).not.toHaveProperty("username");

    analytics.trackPageView();
    analytics.trackPageView();
    expect(captureMock).toHaveBeenCalledTimes(1);
    expect(captureMock).toHaveBeenCalledWith(
      "page_view",
      expect.objectContaining({
        path: window.location.pathname,
        search: window.location.search || undefined,
      })
    );

    analytics.captureException(new Error("boom"), { feature_area: "test" });
    expect(captureExceptionMock).toHaveBeenCalledWith(
      expect.any(Error),
      expect.objectContaining({
        feature_area: "test",
        page_path: window.location.pathname,
      })
    );

    analytics.resetAnalytics();
    expect(resetMock).toHaveBeenCalledWith(true);
  });

  it("pins DOM-derived capture off and installs the URL scrubber", async () => {
    const analytics = await import("../analytics");

    analytics.initAnalytics(ENABLED_ENV);

    const config = initMock.mock.calls[0]?.[1] as Record<string, unknown>;
    expect(config).toMatchObject({
      autocapture: false,
      capture_dead_clicks: false,
      capture_heatmaps: false,
      rageclick: false,
      mask_all_text: true,
      mask_all_element_attributes: true,
      before_send: analytics.sanitizeCaptureResult,
    });
  });

  it("never sends a page view for the OAuth callback URL", async () => {
    const analytics = await import("../analytics");
    analytics.initAnalytics(ENABLED_ENV);
    window.history.replaceState(null, "", `/auth/callback#access_token=${ACCESS_JWT}&refresh_token=${ACCESS_JWT}`);

    analytics.trackPageView();

    expect(captureMock).not.toHaveBeenCalled();
  });

  it("drops hashes and non-allow-listed query params from page views", async () => {
    const analytics = await import("../analytics");
    analytics.initAnalytics(ENABLED_ENV);
    window.history.replaceState(
      null,
      "",
      `/verify-email?email=writer%40example.com&plan=pro&token=${ACCESS_JWT}&code=INVITE1&utm_source=x#frag`,
    );

    analytics.trackPageView();

    expect(captureMock).toHaveBeenCalledTimes(1);
    const [, properties] = captureMock.mock.calls[0] as [string, Record<string, unknown>];
    expect(properties.search).toBe("?plan=pro&utm_source=x");
    expect(properties).not.toHaveProperty("hash");
    expect(properties.page_url).toBe(`${window.location.origin}/verify-email?plan=pro&utm_source=x`);
    const serialized = JSON.stringify(properties);
    expect(serialized).not.toContain("writer");
    expect(serialized).not.toContain(ACCESS_JWT);
    expect(serialized).not.toContain("INVITE1");
  });

  it("scrubs SDK-added URL properties in before_send", async () => {
    const analytics = await import("../analytics");
    const tokenUrl = `https://app.zenstory.ai/auth/callback?token=${ACCESS_JWT}#access_token=${ACCESS_JWT}`;

    const result = analytics.sanitizeCaptureResult({
      uuid: "1",
      event: "$exception",
      properties: {
        $current_url: tokenUrl,
        $referrer: "https://accounts.google.com/o/oauth2/auth?code=4%2F0abc&state=xyz",
        $pathname: "/auth/callback",
        $session_entry_url: tokenUrl,
        $exception_list: [{ type: "Error", value: "boom" }],
      },
      $set_once: { $initial_current_url: tokenUrl, $initial_pathname: "/auth/callback" },
      $set: { $current_url: tokenUrl },
    });

    expect(result?.properties.$current_url).toBe("https://app.zenstory.ai/auth/callback");
    expect(result?.properties.$referrer).toBe("https://accounts.google.com/o/oauth2/auth");
    expect(result?.properties.$session_entry_url).toBe("https://app.zenstory.ai/auth/callback");
    expect(result?.properties.$exception_list).toEqual([{ type: "Error", value: "boom" }]);
    expect(result?.$set_once?.$initial_current_url).toBe("https://app.zenstory.ai/auth/callback");
    expect(result?.$set?.$current_url).toBe("https://app.zenstory.ai/auth/callback");
    expect(JSON.stringify(result)).not.toContain(ACCESS_JWT);
    expect(analytics.sanitizeCaptureResult(null)).toBeNull();
  });

  it("keeps opted-out browsers out of PostHog and can opt back in", async () => {
    const analytics = await import("../analytics");
    analytics.setAnalyticsOptOut(true);

    expect(analytics.initAnalytics(ENABLED_ENV)).toBe(false);
    expect(initMock).not.toHaveBeenCalled();
    expect(analytics.isAnalyticsOptedOut()).toBe(true);

    analytics.setAnalyticsOptOut(false);

    expect(analytics.isAnalyticsOptedOut()).toBe(false);
    expect(initMock).toHaveBeenCalledTimes(1);

    analytics.setAnalyticsOptOut(true);
    expect(optOutMock).toHaveBeenCalledTimes(1);
    analytics.setAnalyticsOptOut(false);
    expect(optInMock).toHaveBeenCalledTimes(1);
  });
});
