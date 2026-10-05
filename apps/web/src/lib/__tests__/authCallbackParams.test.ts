import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

describe("authCallbackParams", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    window.history.replaceState(null, "", "/");
  });

  it("captures hash credentials on /auth/callback and removes them from the URL", async () => {
    window.history.replaceState(
      { usr: null, key: "default", idx: 0 },
      "",
      "/auth/callback#access_token=acc.jwt&refresh_token=ref.jwt&new_user=1",
    );
    const { captureAuthCallbackParams, takeAuthCallbackParams } = await import("../authCallbackParams");

    captureAuthCallbackParams();

    expect(window.location.pathname).toBe("/auth/callback");
    expect(window.location.hash).toBe("");
    expect(window.location.href).not.toContain("acc.jwt");
    // Router history state survives the scrub.
    expect(window.history.state).toEqual({ usr: null, key: "default", idx: 0 });

    const params = takeAuthCallbackParams();
    expect(params?.get("access_token")).toBe("acc.jwt");
    expect(params?.get("refresh_token")).toBe("ref.jwt");
    expect(params?.get("new_user")).toBe("1");
    // Handed out once.
    expect(takeAuthCallbackParams()).toBeNull();
  });

  it("prefers query parameters over hash parameters and scrubs both", async () => {
    window.history.replaceState(null, "", "/auth/callback?error_code=ERR_X#error_code=ERR_Y&redirect=https%3A%2F%2Fa.example");
    const { captureAuthCallbackParams, takeAuthCallbackParams } = await import("../authCallbackParams");

    captureAuthCallbackParams();

    expect(window.location.search).toBe("");
    expect(window.location.hash).toBe("");
    const params = takeAuthCallbackParams();
    expect(params?.get("error_code")).toBe("ERR_X");
    expect(params?.get("redirect")).toBe("https://a.example");
  });

  it("leaves other routes alone", async () => {
    window.history.replaceState(null, "", "/dashboard?tab=files#section");
    const { captureAuthCallbackParams, takeAuthCallbackParams } = await import("../authCallbackParams");

    captureAuthCallbackParams();

    expect(window.location.search).toBe("?tab=files");
    expect(window.location.hash).toBe("#section");
    expect(takeAuthCallbackParams()).toBeNull();
  });
});
