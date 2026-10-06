import { StrictMode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createInstance } from "i18next";
import { I18nextProvider } from "react-i18next";
import { afterAll, afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const importFence = vi.hoisted(() => {
  const original = globalThis.fetch;
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
    const path = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url,
                         "http://test").pathname;
    if (path.startsWith("/locales/")) return new Response("{}", { headers: { "Content-Type": "application/json" } });
    throw new Error("Unexpected import fetch: " + path);
  });
  return { original };
});

import { InspirationGrid } from "../inspirations/InspirationGrid";

let client: QueryClient | undefined;
beforeEach(() => {
  vi.stubGlobal("localStorage", new window.Storage());
  vi.stubGlobal("sessionStorage", new window.Storage());
});
afterEach(async () => {
  cleanup();
  await client?.cancelQueries();
  client?.clear();
  localStorage.clear();
  sessionStorage.clear();
  vi.unstubAllGlobals();
});
afterAll(() => { globalThis.fetch = importFence.original; });

describe.each([false, true])("actual automatic search rootStrict=%s", (strict) => {
  it("starts a new search on page one after paging, without requiring form submit", async () => {
    const queries: Array<{ page: number; search: string }> = [];
    const item = (id: string, name: string) => ({ id, name, description: null, cover_image: null,
      project_type: "novel", tags: [], source: "official", author_id: null, original_project_id: null,
      copy_count: 0, is_featured: false, created_at: "2026-01-01T00:00:00Z" });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url,
                          "http://test");
      let body: unknown;
      if (url.pathname.startsWith("/locales/")) body = {};
      else if (url.pathname.endsWith("/featured")) body = [];
      else if (url.pathname === "/api/v1/inspirations") {
        const page = Number(url.searchParams.get("page") || 1);
        const search = url.searchParams.get("search") || "";
        queries.push({ page, search });
        body = { inspirations: search ? (page === 1 ? [item("match", "Narrow match")] : [])
                                     : [item(`page-${page}`, `Page ${page} item`)],
                 total: search ? 1 : 2, page, page_size: 1 };
      } else throw new Error("Unexpected runtime fetch: " + url.pathname);
      return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
    }));
    const locale = createInstance();
    await locale.init({ lng: "en", fallbackLng: "en", resources: { en: { inspirations: {}, common: {} } },
                        interpolation: { escapeValue: false } });
    client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    const tree = <I18nextProvider i18n={locale}><QueryClientProvider client={client}>
      <InspirationGrid pageSize={1} />
    </QueryClientProvider></I18nextProvider>;
    render(strict ? <StrictMode>{tree}</StrictMode> : tree);
    await screen.findByText("Page 1 item");
    fireEvent.click(screen.getByRole("button", { name: "pagination.next" }));
    await screen.findByText("Page 2 item");
    expect(queries).toContainEqual({ page: 2, search: "" });
    const firstPageRequests = queries.filter(query => query.page === 1 && query.search === "").length;
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "narrow" } });
    await waitFor(() => expect(queries.some(query => query.search === "narrow")).toBe(true));
    await act(async () => { await Promise.resolve(); });
    expect(queries.filter(query => query.search === "narrow").at(-1)?.page).toBe(1);
    expect(await screen.findByText("Narrow match")).toBeInTheDocument();
    expect(queries.filter(query => query.page === 1 && query.search === "")).toHaveLength(firstPageRequests);
    // Further input on page one retains the same debounce and query cache contract.
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "narrower" } });
    await waitFor(() => expect(queries.filter(query => query.search === "narrower")).toEqual([
      { page: 1, search: "narrower" },
    ]));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "" } });
    await screen.findByText("Page 1 item");
    expect(queries.filter(query => query.page === 1 && query.search === "")).toHaveLength(firstPageRequests);
    const countBeforeSubmit = queries.length;
    fireEvent.submit(screen.getByRole("textbox").closest("form")!);
    await act(async () => { await Promise.resolve(); });
    expect(queries).toHaveLength(countBeforeSubmit);
  });
});
