import { describe, expect, it } from "vitest";
import {
  DEFAULT_PRODUCTION_API_ORIGIN,
  resolveApiPreconnectOrigin,
  transformApiPreconnectHtml,
} from "../../vite.config";

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

describe("transformApiPreconnectHtml", () => {
  const baseHtml = `<!doctype html>
<html>
    <head>
        <link rel="preconnect" href="https://api.zenstory.ai" crossorigin />
        <link rel="dns-prefetch" href="https://api.zenstory.ai" />
        <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
    </head>
</html>`;

  it("keeps static production preconnect and dns-prefetch without duplication", () => {
    const output = transformApiPreconnectHtml(baseHtml, DEFAULT_PRODUCTION_API_ORIGIN);
    expect(output.match(/rel="preconnect"/g)).toHaveLength(1);
    expect(output.match(/rel="dns-prefetch"/g)).toHaveLength(1);
    expect(output).toContain('<link rel="preconnect" href="https://api.zenstory.ai" crossorigin />');
    expect(output).toContain('<link rel="dns-prefetch" href="https://api.zenstory.ai" />');
  });

  it("rewrites static production hints to explicit staging API origin", () => {
    const stagingOrigin = "https://staging-api-staging-a289.up.railway.app";
    const output = transformApiPreconnectHtml(baseHtml, stagingOrigin);
    expect(output).not.toContain("https://api.zenstory.ai");
    expect(output).toContain(`<link rel="preconnect" href="${stagingOrigin}" crossorigin />`);
    expect(output).toContain(`<link rel="dns-prefetch" href="${stagingOrigin}" />`);
  });

  it("strips static production hints when preconnect is disabled for dev or isolated preview", () => {
    const output = transformApiPreconnectHtml(baseHtml, null);
    expect(output).not.toContain('rel="preconnect"');
    expect(output).not.toContain('rel="dns-prefetch"');
    expect(output).toContain('<link rel="icon"');
  });
});
