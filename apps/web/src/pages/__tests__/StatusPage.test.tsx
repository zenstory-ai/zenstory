import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import zhCommon from "../../../public/locales/zh/common.json";
import enCommon from "../../../public/locales/en/common.json";
import type {
  DailyStatusObservation,
  ObservationStatus,
  ServiceStatusResponse,
} from "../../lib/statusApi";

const { mockFetchServiceStatus, mockLanguage } = vi.hoisted(() => ({
  mockFetchServiceStatus: vi.fn<(signal?: AbortSignal) => Promise<ServiceStatusResponse>>(),
  mockLanguage: { value: "zh-CN" },
}));

vi.mock("../../lib/statusApi", async () => {
  const actual = await vi.importActual<typeof import("../../lib/statusApi")>("../../lib/statusApi");
  return {
    ...actual,
    fetchServiceStatus: (...args: Parameters<typeof actual.fetchServiceStatus>) =>
      mockFetchServiceStatus(...args),
  };
});

function lookupNestedKey(source: Record<string, unknown>, dottedKey: string): string | undefined {
  const parts = dottedKey.split(".");
  let current: unknown = source;
  for (const part of parts) {
    if (!current || typeof current !== "object" || !(part in (current as Record<string, unknown>))) {
      return undefined;
    }
    current = (current as Record<string, unknown>)[part];
  }
  return typeof current === "string" ? current : undefined;
}

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, optionsOrFallback?: unknown) => {
      const normalizedKey = key.startsWith("common:") ? key.slice("common:".length) : key;
      const bundle = (mockLanguage.value.startsWith("en") ? enCommon : zhCommon) as Record<
        string,
        unknown
      >;
      const fallback =
        typeof optionsOrFallback === "string"
          ? optionsOrFallback
          : optionsOrFallback &&
              typeof optionsOrFallback === "object" &&
              "defaultValue" in optionsOrFallback &&
              typeof (optionsOrFallback as { defaultValue?: unknown }).defaultValue === "string"
            ? (optionsOrFallback as { defaultValue: string }).defaultValue
            : undefined;
      let template = lookupNestedKey(bundle, normalizedKey) ?? fallback ?? key;
      if (optionsOrFallback && typeof optionsOrFallback === "object") {
        for (const [varKey, varVal] of Object.entries(optionsOrFallback as Record<string, unknown>)) {
          if (varKey === "defaultValue") continue;
          template = template.replaceAll(`{{${varKey}}}`, String(varVal));
        }
      }
      return template;
    },
    i18n: {
      language: mockLanguage.value,
      resolvedLanguage: mockLanguage.value,
    },
  }),
}));

vi.mock("../../components/PublicHeader", () => ({
  PublicHeader: () => <div data-testid="public-header">PublicHeader</div>,
}));

import StatusPage from "../StatusPage";

function buildThirtyDayHistory(
  overrides: Record<string, { status: ObservationStatus; success_rate: number | null }> = {},
  defaultStatus: ObservationStatus = "operational",
  defaultRate: number | null = 1,
): DailyStatusObservation[] {
  const start = new Date("2026-10-09T00:00:00Z");
  return Array.from({ length: 30 }, (_, index) => {
    const day = new Date(start.getTime() + index * 86_400_000);
    const date = day.toISOString().slice(0, 10);
    const custom = overrides[date];
    return {
      date,
      status: custom ? custom.status : defaultStatus,
      success_rate: custom ? custom.success_rate : defaultRate,
    };
  });
}

function buildSnapshot(overrides: Partial<ServiceStatusResponse> = {}): ServiceStatusResponse {
  return {
    generated_at: "2026-11-07T11:00:00+00:00",
    expires_at: "2026-11-07T11:02:00+00:00",
    window_start: "2026-11-07T10:00:00+00:00",
    window_minutes: 60,
    timezone: "Asia/Shanghai",
    status: "operational",
    success_rate: 1,
    history: buildThirtyDayHistory({
      "2026-10-15": { status: "degraded", success_rate: 0.98 },
      "2026-10-20": { status: "unknown", success_rate: null },
      "2026-11-07": { status: "operational", success_rate: 1 },
    }),
    ...overrides,
  };
}

describe("StatusPage", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-11-07T11:00:10Z"));
    mockFetchServiceStatus.mockReset();
    mockLanguage.value = "zh-CN";
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("keeps the replacement request deadline when StrictMode aborts the first request", async () => {
    const signals: AbortSignal[] = [];
    mockFetchServiceStatus.mockImplementation((signal?: AbortSignal) => new Promise((_, reject) => {
      signals.push(signal!);
      signal!.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
    }));
    const { unmount } = render(<StrictMode><MemoryRouter><StatusPage /></MemoryRouter></StrictMode>);
    await act(async () => { await Promise.resolve(); });
    expect(signals).toHaveLength(2);
    expect(signals[0].aborted).toBe(true);
    expect(signals[1].aborted).toBe(false);

    await act(async () => { await vi.advanceTimersByTimeAsync(10_001); });
    expect(signals[1].aborted).toBe(true);
    expect(screen.getByRole("alert")).toHaveTextContent("状态数据获取失败");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("keeps history collapsed until requested and shows only one error message and retry action", async () => {
    mockFetchServiceStatus.mockResolvedValueOnce(buildSnapshot()).mockRejectedValueOnce(new Error("503"));
    render(<MemoryRouter><StatusPage /></MemoryRouter>);
    await act(async () => { await Promise.resolve(); });

    const disclosure = screen.getByText("近 30 天调用记录").closest("details")!;
    expect(disclosure).not.toHaveAttribute("open");
    expect(screen.getByTestId("status-history-strip")).not.toBeVisible();
    fireEvent.click(screen.getByText("近 30 天调用记录"));
    expect(disclosure).toHaveAttribute("open");
    expect(screen.getByTestId("status-history-strip")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "刷新" }));
    await act(async () => { await Promise.resolve(); });
    expect(screen.getAllByText("状态数据获取失败")).toHaveLength(1);
    expect(screen.getByRole("alert")).toBe(screen.getByTestId("status-overview-banner"));
    expect(screen.getAllByRole("button", { name: /重试|刷新/ })).toHaveLength(1);
    expect(disclosure).toHaveAttribute("open");
  });

  it("renders a compact current summary with on-demand history and aggregation scope", async () => {
    mockFetchServiceStatus.mockResolvedValueOnce(buildSnapshot());

    render(
      <MemoryRouter initialEntries={["/status"]}>
        <StatusPage />
      </MemoryRouter>,
    );

    expect(screen.getByRole("status")).toBeInTheDocument();

    await act(async () => {
      await Promise.resolve();
    });

    const banner = screen.getByTestId("status-overview-banner");
    expect(banner).toHaveAttribute("data-status-tone", "operational");
    expect(banner).toHaveTextContent("近期调用正常");
    expect(screen.getByRole("region", { name: "最近 1 小时" })).toBeVisible();

    const currentRate = screen.getByTestId("status-current-rate");
    expect(currentRate).toHaveTextContent("调用成功率 100%");
    expect(screen.getByRole("heading", { level: 1, name: "Agent 状态" })).toBeVisible();
    expect(screen.queryByText("去 AI 味")).not.toBeInTheDocument();
    expect(screen.getAllByTestId("status-history-strip")).toHaveLength(1);

    // 30-day visual strip is read-only (no 30 narrow buttons)
    const writingStrip = screen.getByTestId("status-history-strip");
    expect(writingStrip.querySelectorAll("button")).toHaveLength(0);
    expect(writingStrip.querySelectorAll("[data-day-bar]")).toHaveLength(30);

    expect(screen.getByText(/按模型调用统计，一次回复可能包含多次调用/)).toBeInTheDocument();
    expect(screen.queryByText(/99\.99%|90\s*天|宕机|意图路由/)).not.toBeInTheDocument();
  });

  it("renders unknown status honestly when no recent calls were observed", async () => {
    mockFetchServiceStatus.mockResolvedValueOnce(
      buildSnapshot({
        status: "unknown",
        success_rate: null,
        history: buildThirtyDayHistory({}, "unknown", null),
      }),
    );

    render(
      <MemoryRouter initialEntries={["/status"]}>
        <StatusPage />
      </MemoryRouter>,
    );

    await act(async () => {
      await Promise.resolve();
    });

    const banner = screen.getByTestId("status-overview-banner");
    expect(banner).toHaveAttribute("data-status-tone", "unknown");
    expect(banner).toHaveTextContent("暂无近期数据");

    expect(screen.getByTestId("status-current-rate")).toHaveTextContent("—");
    expect(screen.getByTestId("status-current-rate")).not.toHaveTextContent("100%");
  });

  it("renders degraded status when recent calls had errors without claiming site-wide outage", async () => {
    mockFetchServiceStatus.mockResolvedValueOnce(
      buildSnapshot({
        status: "degraded",
        success_rate: 0.98,
        history: buildThirtyDayHistory({
          "2026-11-07": { status: "degraded", success_rate: 0.98 },
        }),
      }),
    );

    render(
      <MemoryRouter initialEntries={["/status"]}>
        <StatusPage />
      </MemoryRouter>,
    );

    await act(async () => {
      await Promise.resolve();
    });

    const banner = screen.getByTestId("status-overview-banner");
    expect(banner).toHaveAttribute("data-status-tone", "degraded");
    expect(banner).toHaveTextContent("近期有异常调用");

    expect(screen.getByTestId("status-current-rate")).toHaveTextContent("98%");
  });

  it("supports full-width 44px range slider, keyboard navigation, and 44px prev/next buttons for 30-day history", async () => {
    mockFetchServiceStatus.mockResolvedValueOnce(buildSnapshot());

    render(
      <MemoryRouter initialEntries={["/status"]}>
        <StatusPage />
      </MemoryRouter>,
    );

    await act(async () => {
      await Promise.resolve();
    });

    fireEvent.click(screen.getByText("近 30 天调用记录"));
    const writingDetail = screen.getByTestId("status-history-detail");
    const rangeInput = screen.getByTestId("status-history-range") as HTMLInputElement;

    // Full-width and at least 44px high
    expect(rangeInput.className).toContain("w-full");
    expect(rangeInput.className).toContain("min-h-11");
    expect(rangeInput.value).toBe("29");
    expect(rangeInput.getAttribute("aria-valuetext")).toContain("2026-11-07");
    expect(rangeInput.getAttribute("aria-valuetext")).toContain("正常");
    expect(rangeInput.getAttribute("aria-valuetext")).toContain("100%");

    // Defaults to today (2026-11-07), labeled as partial day up to update time
    expect(writingDetail).toHaveTextContent("2026-11-07");
    expect(writingDetail).toHaveTextContent("截至更新");
    expect(writingDetail).toHaveTextContent("100%");

    // Select degraded day 2026-10-15 (index 6) via range input
    fireEvent.change(rangeInput, { target: { value: "6" } });
    expect(rangeInput.value).toBe("6");
    expect(rangeInput.getAttribute("aria-valuetext")).toContain("2026-10-15");
    expect(rangeInput.getAttribute("aria-valuetext")).toContain("有异常调用");
    expect(rangeInput.getAttribute("aria-valuetext")).toContain("98%");
    expect(writingDetail).toHaveTextContent("2026-10-15");
    expect(writingDetail).toHaveTextContent("有异常调用");
    expect(writingDetail).toHaveTextContent("98%");

    // Keyboard navigation on range: ArrowRight -> index 7 (2026-10-16), Home -> index 0 (2026-10-09), End -> index 29 (2026-11-07)
    fireEvent.keyDown(rangeInput, { key: "ArrowRight" });
    expect(rangeInput.value).toBe("7");
    expect(writingDetail).toHaveTextContent("2026-10-16");
    expect(writingDetail).toHaveTextContent("100%");

    fireEvent.keyDown(rangeInput, { key: "Home" });
    expect(rangeInput.value).toBe("0");
    expect(writingDetail).toHaveTextContent("2026-10-09");

    fireEvent.keyDown(rangeInput, { key: "End" });
    expect(rangeInput.value).toBe("29");
    expect(writingDetail).toHaveTextContent("2026-11-07");

    // 44px previous/next buttons also step day by day
    const prevBtn = screen.getByTestId("status-history-prev");
    const nextBtn = screen.getByTestId("status-history-next");
    expect(prevBtn.className).toContain("min-h-11");
    expect(nextBtn.className).toContain("min-h-11");
    expect(nextBtn).toBeDisabled();

    fireEvent.click(prevBtn);
    expect(rangeInput.value).toBe("28");
    expect(writingDetail).toHaveTextContent("2026-11-06");
    expect(nextBtn).not.toBeDisabled();

    fireEvent.click(nextBtn);
    expect(rangeInput.value).toBe("29");
    expect(writingDetail).toHaveTextContent("2026-11-07");
  });

  it("shows fetch failure on initial 503 and recovers on manual retry", async () => {
    mockFetchServiceStatus
      .mockRejectedValueOnce(new Error("HTTP 503"))
      .mockResolvedValueOnce(buildSnapshot());

    render(
      <MemoryRouter initialEntries={["/status"]}>
        <StatusPage />
      </MemoryRouter>,
    );

    await act(async () => {
      await Promise.resolve();
    });

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("状态数据获取失败");
    expect(screen.getByTestId("status-overview-banner")).not.toHaveAttribute(
      "data-status-tone",
      "operational",
    );

    fireEvent.click(within(alert).getByRole("button", { name: "重试" }));

    await act(async () => {
      await Promise.resolve();
    });

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByTestId("status-overview-banner")).toHaveAttribute(
      "data-status-tone",
      "operational",
    );
  });

  it("times out a hanging initial request after 10s so the page does not stay in loading forever", async () => {
    mockFetchServiceStatus
      .mockImplementationOnce(
        (signal) =>
          new Promise((_, reject) => {
            signal?.addEventListener("abort", () => {
              reject(new DOMException("Aborted", "AbortError"));
            });
          }),
      )
      .mockResolvedValueOnce(buildSnapshot());

    render(
      <MemoryRouter initialEntries={["/status"]}>
        <StatusPage />
      </MemoryRouter>,
    );

    expect(screen.getByRole("status")).toBeInTheDocument();

    // Advance 10s to hit the request timeout
    await act(async () => {
      vi.advanceTimersByTime(10_000);
      await Promise.resolve();
    });

    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("状态数据获取失败");

    // Manual retry now works and loads the snapshot
    fireEvent.click(within(alert).getByRole("button", { name: "重试" }));
    await act(async () => {
      await Promise.resolve();
    });

    expect(screen.getByTestId("status-overview-banner")).toHaveAttribute(
      "data-status-tone",
      "operational",
    );
  });

  it("downgrades current status to stale (whether previously operational or degraded) and hides live rate when refresh fails or expires_at passes", async () => {
    mockFetchServiceStatus
      .mockResolvedValueOnce(
        buildSnapshot({
          status: "degraded",
          success_rate: 0.98,
          history: buildThirtyDayHistory({
            "2026-11-07": { status: "degraded", success_rate: 0.98 },
          }),
        }),
      )
      .mockRejectedValueOnce(new Error("HTTP 503"));

    render(
      <MemoryRouter initialEntries={["/status"]}>
        <StatusPage />
      </MemoryRouter>,
    );

    await act(async () => {
      await Promise.resolve();
    });

    expect(screen.getByTestId("status-overview-banner")).toHaveAttribute(
      "data-status-tone",
      "degraded",
    );

    // Advance 60s to trigger background poll which fails with 503
    await act(async () => {
      vi.advanceTimersByTime(60_000);
      await Promise.resolve();
    });

    expect(screen.getByRole("alert")).toHaveTextContent("状态数据获取失败");
    // Both previously degraded and previously operational statuses transition to stale (not kept as current)
    expect(screen.getByTestId("status-overview-banner")).toHaveAttribute(
      "data-status-tone",
      "stale",
    );
    // Live 1-hour rate shows "—" when stale while 30-day history detail retains 98%
    const currentRate = screen.getByTestId("status-current-rate");
    expect(currentRate).toHaveTextContent("—");
    expect(currentRate).not.toHaveTextContent("98%");
    expect(screen.getByTestId("status-history-detail")).toHaveTextContent("98%");
  });

  it("stops showing green operational status as soon as expires_at is reached", async () => {
    mockFetchServiceStatus
      .mockResolvedValueOnce(buildSnapshot())
      .mockImplementation(() => new Promise(() => {}));

    render(
      <MemoryRouter initialEntries={["/status"]}>
        <StatusPage />
      </MemoryRouter>,
    );

    await act(async () => {
      await Promise.resolve();
    });

    expect(screen.getByTestId("status-overview-banner")).toHaveAttribute(
      "data-status-tone",
      "operational",
    );

    // Advance past expires_at (2026-11-07T11:02:00Z, which is +110s from 11:00:10Z)
    await act(async () => {
      vi.advanceTimersByTime(115_000);
      await Promise.resolve();
    });

    expect(screen.getByTestId("status-overview-banner")).toHaveAttribute(
      "data-status-tone",
      "stale",
    );
    expect(screen.getByTestId("status-overview-banner")).toHaveTextContent("上次更新");
  });

  it("refreshes when background tab becomes visible again and renders English Agent copy when locale is en", async () => {
    mockLanguage.value = "en-US";
    mockFetchServiceStatus
      .mockResolvedValueOnce(buildSnapshot())
      .mockResolvedValueOnce(
        buildSnapshot({
          status: "degraded",
          success_rate: 0.9,
          history: buildThirtyDayHistory({
            "2026-11-07": { status: "degraded", success_rate: 0.9 },
          }),
        }),
      );

    const { unmount } = render(
      <MemoryRouter initialEntries={["/status"]}>
        <StatusPage />
      </MemoryRouter>,
    );

    await act(async () => {
      await Promise.resolve();
    });

    expect(screen.getByRole("heading", { level: 1, name: "Agent Status" })).toBeInTheDocument();
    expect(screen.queryByText("Humanize")).not.toBeInTheDocument();

    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => "visible",
    });

    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      await Promise.resolve();
    });

    expect(mockFetchServiceStatus).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId("status-overview-banner")).toHaveAttribute(
      "data-status-tone",
      "degraded",
    );

    unmount();
  });
});
