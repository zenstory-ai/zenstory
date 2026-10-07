import { StrictMode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen } from "@testing-library/react";
import { createInstance } from "i18next";
import { I18nextProvider } from "react-i18next";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterAll, afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const importFence = vi.hoisted(() => {
  const original = globalThis.fetch;
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
    const path = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url,
                         "http://test").pathname;
    if (path.startsWith("/locales/")) return new Response("{}", { headers: { "Content-Type": "application/json" } });
    throw new Error("Unexpected import-time fetch: " + path);
  });
  return { original };
});

import InspirationDetailPage from "../InspirationDetailPage";

let client: QueryClient | undefined;
let originalOverflow: string;
beforeEach(() => {
  originalOverflow = document.body.style.overflow;
  vi.stubGlobal("localStorage", new window.Storage());
  vi.stubGlobal("sessionStorage", new window.Storage());
});
afterEach(() => {
  cleanup();
  client?.clear();
  localStorage.clear();
  sessionStorage.clear();
  vi.unstubAllGlobals();
  expect(document.body.style.overflow).toBe(originalOverflow);
  expect(document.querySelector('[role="dialog"]')).toBeNull();
});
afterAll(() => { globalThis.fetch = importFence.original; });

describe.each([false, true])("actual detail-only query ownership rootStrict=%s", (strict) => {
  it("loads and renders detail without unused list/featured requests", async () => {
    const requests: string[] = [];
    const detail = { id: "template-1", name: "Metadata detail", description: "Detail description",
      cover_image: null, project_type: "novel", tags: [], source: "official", author_id: null,
      original_project_id: null, copy_count: 0, is_featured: false, created_at: "2026-01-01T00:00:00Z",
      file_preview: [{ title: "Chapter", file_type: "draft", has_content: true }] };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const path = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url,
                           "http://test").pathname;
      let body: unknown;
      if (path.startsWith("/locales/")) body = {};
      else {
        requests.push(path);
        if (path === "/api/v1/inspirations/template-1") body = detail;
        else if (path === "/api/v1/inspirations") body = { inspirations: [], total: 0, page: 1, page_size: 12 };
        else if (path === "/api/v1/inspirations/featured") body = [];
        else throw new Error("Unexpected runtime fetch: " + path);
      }
      return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
    }));
    const locale = createInstance();
    await locale.init({ lng: "en", fallbackLng: "en", resources: { en: { inspirations: {}, common: {} } },
                        interpolation: { escapeValue: false } });
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
    const tree = <I18nextProvider i18n={locale}><QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/inspirations/template-1"]}><Routes>
        <Route path="/inspirations/:inspirationId" element={<InspirationDetailPage />} />
      </Routes></MemoryRouter>
    </QueryClientProvider></I18nextProvider>;
    render(strict ? <StrictMode>{tree}</StrictMode> : tree);
    expect(await screen.findByText("Metadata detail")).toBeInTheDocument();
    expect(screen.getByText("Chapter")).toBeInTheDocument();
    await act(async () => { await Promise.resolve(); });
    expect(requests.filter(path => path.endsWith("/template-1")).length).toBeGreaterThanOrEqual(1);
    // Actual Page/hook/QueryClient and network boundary, not a mocked hook-argument assertion.
    expect(requests.filter(path => path === "/api/v1/inspirations" || path.endsWith("/featured"))).toEqual([]);
  });
});
