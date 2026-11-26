import { describe, expect, it } from "vitest";
import { DEFAULT_PRODUCTION_API_ORIGIN, resolveApiPreconnectOrigin } from "../../vite.config";

describe("resolveApiPreconnectOrigin", () => {
  it("omits preconnect during local dev serve", () => {
    expect(resolveApiPreconnectOrigin({ command: "serve" })).toBeNull();
    expect(
      resolveApiPreconnectOrigin({
        command: "serve",
        apiBaseUrl: "https://api.zenstory.ai",
      }),
    ).toBeNull();
  });

  it("omits preconnect for HTTP / localhost E2E builds", () => {
    expect(
      resolveApiPreconnectOrigin({
        command: "build",
        apiBaseUrl: "http://localhost:8000",
      }),
    ).toBeNull();
    expect(
      resolveApiPreconnectOrigin({
        command: "build",
        apiBaseUrl: "http://127.0.0.1:8000",
      }),
    ).toBeNull();
  });

  it("omits preconnect for isolated Vercel Preview builds with empty VITE_API_BASE_URL", () => {
    expect(
      resolveApiPreconnectOrigin({
        command: "build",
        apiBaseUrl: "",
        vercelEnv: "preview",
      }),
    ).toBeNull();
  });

  it("emits explicit HTTPS staging or production API origin when VITE_API_BASE_URL is set", () => {
    expect(
      resolveApiPreconnectOrigin({
        command: "build",
        apiBaseUrl: "https://staging-api-staging-a289.up.railway.app/some/path",
        vercelEnv: "preview",
      }),
    ).toBe("https://staging-api-staging-a289.up.railway.app");

    expect(
      resolveApiPreconnectOrigin({
        command: "build",
        apiBaseUrl: "https://api.zenstory.ai",
        vercelEnv: "production",
      }),
    ).toBe("https://api.zenstory.ai");
  });

  it("defaults to production API origin on standard build outside Vercel Preview", () => {
    expect(
      resolveApiPreconnectOrigin({
        command: "build",
      }),
    ).toBe(DEFAULT_PRODUCTION_API_ORIGIN);
    expect(
      resolveApiPreconnectOrigin({
        command: "build",
        vercelEnv: "production",
      }),
    ).toBe(DEFAULT_PRODUCTION_API_ORIGIN);
  });
});
