import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Mock error handler so ApiError messages are deterministic.
vi.mock("../errorHandler", () => ({
  resolveApiErrorMessage: vi.fn((payload: unknown, fallback: string) => {
    if (!payload || typeof payload !== "object") return fallback;
    const data = payload as Record<string, unknown>;
    return (typeof data.error_code === "string" && data.error_code) || fallback;
  }),
  toUserErrorMessage: vi.fn((msg: string) => msg),
}));

describe("naturalPolishApi", () => {
  beforeEach(() => {
    vi.clearAllMocks();

    const localStorageMock = {
      store: {} as Record<string, string>,
      getItem: vi.fn((key: string) => localStorageMock.store[key] || null),
      setItem: vi.fn((key: string, value: string) => {
        localStorageMock.store[key] = value;
      }),
      removeItem: vi.fn((key: string) => {
        delete localStorageMock.store[key];
      }),
      clear: vi.fn(() => {
        localStorageMock.store = {};
      }),
      length: 0,
      key: vi.fn(),
    };
    Object.defineProperty(global, "localStorage", {
      value: localStorageMock,
      writable: true,
      configurable: true,
    });

    localStorage.setItem("access_token", "test-token");
    localStorage.setItem("zenstory-language", "zh");
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("posts to /api/v1/editor/natural-polish and returns text", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ text: "rewritten" }),
    });
    vi.stubGlobal("fetch", mockFetch);

    const { naturalPolishApi } = await import("../naturalPolishApi");
    const result = await naturalPolishApi.naturalPolish({
      projectId: "p1",
      fileId: "f1",
      fileType: "draft",
      selectedText: "hello",
    });

    // 旧服务端不带 unchanged：按「有改动」处理，照常进审阅。
    expect(result).toEqual({ text: "rewritten", unchanged: false });
    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [url, init] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/v1/editor/natural-polish");
    expect(init.method).toBe("POST");
    expect(typeof init.body).toBe("string");
    expect(JSON.parse(init.body as string)).toMatchObject({
      project_id: "p1",
      selected_text: "hello",
      metadata: {
        current_file_id: "f1",
        current_file_type: "draft",
        source: "editor_natural_polish",
      },
    });
  });

  it("passes through the server's unchanged flag", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ text: "原文", unchanged: true }),
      }),
    );

    const { naturalPolishApi } = await import("../naturalPolishApi");
    const result = await naturalPolishApi.naturalPolish({
      projectId: "p1",
      fileId: "f1",
      selectedText: "原文",
    });

    expect(result).toEqual({ text: "原文", unchanged: true });
  });

  it("throws ApiError when backend returns non-2xx", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({ error_code: "ERR_BAD_REQUEST" }),
    });
    vi.stubGlobal("fetch", mockFetch);

    const { naturalPolishApi } = await import("../naturalPolishApi");
    await expect(
      naturalPolishApi.naturalPolish({
        projectId: "p1",
        fileId: "f1",
        selectedText: "hello",
      }),
    ).rejects.toMatchObject({
      name: "ApiError",
      status: 400,
      rawMessage: "ERR_BAD_REQUEST",
    });
  });

  it("supports AbortSignal cancellation", async () => {
    const controller = new AbortController();

    const mockFetch = vi.fn((_url: string, init?: RequestInit) => {
      return new Promise((_resolve, reject) => {
        const signal = init?.signal;
        if (signal) {
          if (signal.aborted) {
            reject(new DOMException("Aborted", "AbortError"));
            return;
          }
          signal.addEventListener("abort", () => {
            reject(new DOMException("Aborted", "AbortError"));
          });
        }
      });
    });
    vi.stubGlobal("fetch", mockFetch);

    const { naturalPolishApi } = await import("../naturalPolishApi");
    const pending = naturalPolishApi.naturalPolish(
      {
        projectId: "p1",
        fileId: "f1",
        selectedText: "hello",
      },
      { signal: controller.signal },
    );

    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  });
});
