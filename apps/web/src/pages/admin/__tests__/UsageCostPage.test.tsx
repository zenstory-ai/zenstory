import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import UsageCostPage from "../UsageCostPage";
import { adminApi } from "../../../lib/adminApi";

type QueryOptions = { queryKey: unknown[]; queryFn: () => unknown; enabled?: boolean };
const queryMock = vi.fn();
const refetch = vi.fn();
const invalidateQueries = vi.fn();

vi.mock("@tanstack/react-query", () => ({
  useQuery: (options: QueryOptions) => queryMock(options),
  useQueryClient: () => ({ invalidateQueries }),
  useIsFetching: () => 0,
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown> | string) => {
      if (typeof options === "object" && options) {
        if ("affected" in options && "total" in options) return `${key}:${String(options.affected)}/${String(options.total)}`;
        if ("count" in options) return `${key}:${String(options.count)}`;
        if ("name" in options) return `${key}:${String(options.name)}`;
        if ("peak" in options) return `${key}:${String(options.peak)}|${String(options.offpeak)}`;
        if ("finished" in options) return `${String(options.success)} / ${String(options.finished)}`;
        if ("rate" in options && "slow" in options) return `${key}:${String(options.rate)}(${String(options.slow)}/${String(options.samples)})`;
        if ("survived" in options && "eligible" in options) return `${key}:${String(options.survived)}/${String(options.eligible)}|${String(options.coEditRate)}`;
        if ("value" in options) return `${String(options.value)} tok/s`;
      }
      return key;
    },
    i18n: { language: "en-US" },
  }),
}));
vi.mock("../../../lib/i18n-helpers", () => ({ getLocaleCode: () => "en-US" }));
vi.mock("../../../lib/adminApi", () => ({
  adminApi: {
    getUsageSummary: vi.fn(),
    getUsageByUser: vi.fn(),
    getUserDailyUsage: vi.fn(),
    getUsageByModel: vi.fn(),
    getUsageStageLatency: vi.fn(),
  },
}));
vi.mock("../../../components/ui/Modal", () => ({
  Modal: ({ open, title, children, onClose }: { open: boolean; title: string; children: React.ReactNode; onClose: () => void }) =>
    open ? (
      <div role="dialog" aria-label={title}>
        <button type="button" onClick={onClose}>close</button>
        {children}
      </div>
    ) : null,
}));

const metrics = (cost: string, peak = "0.0000", offpeak = cost) => ({
  calls: 3,
  cache_hit_tokens: 1000,
  cache_miss_tokens: 200,
  output_tokens: 50,
  cost_cny: cost,
  peak_cost_cny: peak,
  offpeak_cost_cny: offpeak,
});

const period = {
  timezone: "Asia/Shanghai",
  pricing_version: "deepseek-flash-2026-10",
  prices: {
    peak: { cache_hit: "0.04", cache_miss: "2", output: "8" },
    offpeak: { cache_hit: "0.02", cache_miss: "1", output: "4" },
  },
};

const dailyRow = (date: string, cost: string) => ({ date, users: 1, ...metrics(cost) });

function summaryFor(window: string) {
  const days = window === "7d"
    ? ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05", "2026-10-06", "2026-10-07"]
    : ["2026-10-07"];
  return {
    window,
    ...period,
    period_start: days[0],
    period_end: days[days.length - 1],
    totals: { users: 2, ...metrics("12.3456", "10.0000", "2.3456") },
    by_source: [{ source: "material", ...metrics("12.0000") }, { source: "agent", ...metrics("0.0440") }],
    daily: days.map((date, index) => dailyRow(date, index === 0 ? "1.5000" : "0.0000")),
  };
}

const userRow = {
  user_id: "user-1",
  username: "writer",
  email: "writer@example.com",
  last_used_at: "2026-10-07T01:00:00Z",
  ...metrics("0.0440", "0.0400", "0.0040"),
};

const detail = (days: number) => ({
  user_id: "user-1",
  username: "writer",
  email: "writer@example.com",
  days,
  ...period,
  period_start: "2026-10-01",
  period_end: "2026-10-07",
  totals: metrics("0.0440"),
  by_source: [{ source: "polish", ...metrics("0.0440") }],
  daily: Array.from({ length: days }, (_, index) => dailyRow(`d-${index}`, "0.0000")),
});

const distribution = (p50: number | null, p95: number | null, samples: number) => ({ p50, p95, samples });
const modelMetrics = (overrides: Record<string, unknown> = {}) => ({
  attempts: 10,
  success: 4,
  error: 3,
  timeout: 1,
  cancelled: 1,
  not_recorded: 1,
  success_rate: 0.5,
  latency_ms: distribution(5_000, 20_000, 4),
  ttft_ms: distribution(850, 4_000, 3),
  decode_tps: { value: 100, samples: 2 },
  cache_hit_tokens: 0,
  cache_miss_tokens: 400,
  output_tokens: 3_000,
  cost_cny: "0.0120",
  ...overrides,
});
const modelsFor = (source: string) => ({
  window: "today",
  ...period,
  period_start: "2026-10-07",
  period_end: "2026-10-07",
  source: source || null,
  items: [
    { model: "deepseek-flash", ...modelMetrics() },
    {
      model: "MiniMax-M3.1-Flash",
      ...modelMetrics({
        attempts: 1, success: 1, error: 0, timeout: 0, cancelled: 0, not_recorded: 0, success_rate: 1,
        latency_ms: distribution(900, 900, 1), ttft_ms: distribution(null, null, 0), decode_tps: { value: null, samples: 0 },
      }),
    },
  ],
  totals: modelMetrics({ attempts: 11, success: 5, success_rate: 0.5556 }),
});

const stageLatencyFor = (days: number) => ({
  days,
  timezone: "Asia/Shanghai",
  period_start: "2026-10-01",
  period_end: "2026-10-07",
  slow_threshold_ms: 10_000,
  total_rounds: 20,
  prepare_context_ms: distribution(180, 620, 20),
  first_output_ms: { p50: 3_200, p95: 12_500, samples: 18, slow_count: 3, slow_rate: 0.1667 },
  chapter_first_prose_ms: { p50: 4_500, p90: 11_000, p95: 14_000, samples: 10, slow_count: 2, slow_rate: 0.2 },
  first_body_persisted_ms: distribution(9_000, 25_000, 10),
  file_body_pending_rounds: 2,
  file_body_pending_rate: 0.1,
  leaked_tool_call_rounds: 1,
  leaked_tool_call_rate: 0.05,
  user_stopped_rounds: 3,
  user_stopped_rate: 0.15,
  client_disconnected_rounds: 1,
  client_disconnected_rate: 0.05,
  desktop_vs_mobile: {
    desktop: {
      rounds: 15,
      first_output_ms: { p50: 2_800, p95: 11_000, samples: 14, slow_count: 2, slow_rate: 0.1429 },
      chapter_first_prose_ms: { p50: 4_000, p90: 10_000, p95: 13_000, samples: 8, slow_count: 1, slow_rate: 0.125 },
      client_disconnected_rounds: 0,
      client_disconnected_rate: 0,
    },
    mobile: {
      rounds: 5,
      first_output_ms: { p50: 5_000, p95: 14_000, samples: 4, slow_count: 1, slow_rate: 0.25 },
      chapter_first_prose_ms: { p50: 6_000, p90: 14_000, p95: 14_000, samples: 2, slow_count: 1, slow_rate: 0.5 },
      client_disconnected_rounds: 1,
      client_disconnected_rate: 0.2,
    },
    unknown: {
      rounds: 0,
      first_output_ms: { p50: null, p95: null, samples: 0, slow_count: 0, slow_rate: null },
      chapter_first_prose_ms: { p50: null, p90: null, p95: null, samples: 0, slow_count: 0, slow_rate: null },
      client_disconnected_rounds: 0,
      client_disconnected_rate: null,
    },
  },
  ai_draft_survival: {
    eligible_drafts: 8,
    survived_drafts: 6,
    co_edited_drafts: 4,
    deleted_or_reverted_drafts: 2,
    survival_rate: 0.75,
    co_edit_rate: 0.5,
    observation_hours: 24,
  },
  daily: [
    {
      date: "2026-10-07",
      rounds: 6,
      prepare_context_p50_ms: 190,
      first_output_p50_ms: 3_100,
      first_output_p95_ms: 12_000,
      first_output_slow_rate: 0.1667,
      chapter_first_prose_p50_ms: 4_200,
      chapter_first_prose_p95_ms: 13_500,
      chapter_first_prose_slow_rate: 0.25,
      file_body_pending_rate: 0.1667,
      leaked_tool_call_rate: 0,
      user_stopped_rate: 0.1667,
      client_disconnected_rate: 0,
    },
  ],
});

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.search}</div>;
}

function renderPage(entry = "/admin/usage") {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <UsageCostPage />
      <LocationProbe />
    </MemoryRouter>,
  );
}

const lastKey = (name: string) =>
  queryMock.mock.calls.map(([options]) => (options as QueryOptions).queryKey).filter((key) => key[2] === name).at(-1);

describe("UsageCostPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    queryMock.mockImplementation((options: QueryOptions) => {
      const [, , name, ...rest] = options.queryKey as string[];
      if (options.enabled === false) return { data: undefined, isLoading: false, isError: false, refetch };
      if (name === "summary") return { data: summaryFor(rest[0]), isLoading: false, isFetching: false, isError: false, refetch };
      if (name === "users") {
        return { data: { ...period, items: [userRow], total: 41, page: 1, page_size: 20 }, isLoading: false, isFetching: false, isError: false, refetch };
      }
      if (name === "models") return { data: modelsFor(rest[1]), isLoading: false, isFetching: false, isError: false, refetch };
      if (name === "stage-latency") return { data: stageLatencyFor(Number(rest[0])), isLoading: false, isFetching: false, isError: false, refetch };
      return { data: detail(Number(rest[1])), isLoading: false, isError: false, refetch };
    });
  });

  it("shows per-model success rate with its counts, latency, first token, decode speed and N/A", () => {
    renderPage();
    expect(screen.getByText("usage.models.title")).toBeInTheDocument();
    const deepseek = within(screen.getByText("deepseek-flash").closest("tr")!);
    expect(deepseek.getByText("50%")).toBeInTheDocument();
    // Numerator and denominator next to the rate: 4 / (4 + 3 + 1).
    expect(deepseek.getByText("4 / 8")).toBeInTheDocument();
    expect(deepseek.getByText("3 / 1 / 1 / 1")).toBeInTheDocument();
    expect(deepseek.getByText("5 s / 20 s")).toBeInTheDocument();
    expect(deepseek.getByText("850 ms / 4 s")).toBeInTheDocument();
    expect(deepseek.getByText("100 tok/s")).toBeInTheDocument();
    expect(deepseek.getByText("usage.models.samples:2")).toBeInTheDocument();
    expect(deepseek.getByText("400 / 3,000")).toBeInTheDocument();

    // Not streamed: no first token and no decode speed, shown as N/A rather than 0.
    const minimax = within(screen.getByText("MiniMax-M3.1-Flash").closest("tr")!);
    expect(minimax.getByText("usage.models.na / usage.models.na")).toBeInTheDocument();
    expect(minimax.getByText("usage.models.na")).toBeInTheDocument();
    expect(minimax.getAllByText("usage.models.samples:0")).toHaveLength(2);

    // The pooled row comes from the server, not an average of the two rows above.
    const totals = within(screen.getByText("usage.models.allModels").closest("tr")!);
    expect(totals.getByText("55.6%")).toBeInTheDocument();

    for (const key of ["attempts", "successRate", "latency", "ttft", "decode", "notRecorded"]) {
      expect(screen.getByText(`usage.models.definitions.${key}`)).toBeInTheDocument();
    }
  });

  it("filters the model metrics by feature within the same window, and refresh reloads them", () => {
    renderPage();
    expect(lastKey("models")).toEqual(["admin", "usage", "models", "today", ""]);
    fireEvent.change(screen.getByRole("combobox", { name: "usage.models.sourceFilter" }), { target: { value: "agent" } });
    expect(lastKey("models")).toEqual(["admin", "usage", "models", "today", "agent"]);
    fireEvent.click(within(screen.getByRole("group", { name: "usage.window" })).getByRole("button", { name: "usage.windows.7d" }));
    expect(lastKey("models")).toEqual(["admin", "usage", "models", "7d", "agent"]);
    fireEvent.click(screen.getByRole("button", { name: "usage.refresh" }));
    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["admin", "usage", "models"] });
  });

  it("shows KPIs, price line and per-source cost for today", () => {
    renderPage();
    expect(screen.getByText("¥12.35")).toBeInTheDocument();
    expect(screen.getByText("¥10.00 / ¥2.35")).toBeInTheDocument();
    expect(screen.getByText("1,000 / 200 / 50")).toBeInTheDocument();
    expect(screen.getByText("usage.priceLine")).toBeInTheDocument();
    expect(screen.getByText("usage.notCounted")).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "usage.sources.material" })).toBeInTheDocument();
    // The daily breakdown is only for the 7-day window.
    expect(screen.queryByText("usage.byDay")).not.toBeInTheDocument();
    expect(lastKey("summary")).toEqual(["admin", "usage", "summary", "today"]);
  });

  it("switches window, shows 7 daily rows and resets paging", () => {
    renderPage();
    fireEvent.click(screen.getAllByRole("button", { name: "common:next" })[0]);
    expect(lastKey("users")).toEqual(["admin", "usage", "users", "today", "cost", "", 2]);
    fireEvent.click(screen.getAllByRole("button", { name: "common:previous" })[0]);
    expect(lastKey("users")).toEqual(["admin", "usage", "users", "today", "cost", "", 1]);
    fireEvent.click(screen.getAllByRole("button", { name: "common:next" })[0]);

    const windows = within(screen.getByRole("group", { name: "usage.window" }));
    fireEvent.click(windows.getByRole("button", { name: "usage.windows.7d" }));
    expect(windows.getByRole("button", { name: "usage.windows.7d" })).toHaveAttribute("aria-pressed", "true");
    expect(lastKey("summary")).toEqual(["admin", "usage", "summary", "7d"]);
    expect(lastKey("users")).toEqual(["admin", "usage", "users", "7d", "cost", "", 1]);
    expect(screen.getByText("usage.byDay")).toBeInTheDocument();
    const byDayTable = within(screen.getByRole("region", { name: "usage.byDay" }));
    for (const date of ["2026-10-01", "2026-10-04", "2026-10-07"]) {
      expect(byDayTable.getByText(date)).toBeInTheDocument();
    }
    expect(screen.getByText("¥1.50")).toBeInTheDocument();
  });

  it("sorts and searches the user list", () => {
    renderPage();
    fireEvent.change(screen.getByRole("combobox", { name: "usage.sortBy" }), { target: { value: "tokens" } });
    expect(lastKey("users")).toEqual(["admin", "usage", "users", "today", "tokens", "", 1]);
    fireEvent.change(screen.getByRole("textbox", { name: "usage.search" }), { target: { value: "  writer@  " } });
    fireEvent.click(screen.getByRole("button", { name: "usage.search" }));
    expect(lastKey("users")).toEqual(["admin", "usage", "users", "today", "tokens", "writer@", 1]);
    expect(screen.getByText("usage.totalUsers:41")).toBeInTheDocument();
    expect(screen.getByText("1 / 3")).toBeInTheDocument();
  });

  it("opens a user's detail on click and puts it in the URL", () => {
    renderPage();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "writer" }));
    const dialog = within(screen.getByRole("dialog", { name: "writer" }));
    expect(screen.getByTestId("location")).toHaveTextContent("?user=user-1");
    expect(dialog.getByText("usage.sources.polish")).toBeInTheDocument();
    expect(dialog.getByText("d-6")).toBeInTheDocument();

    fireEvent.click(dialog.getByRole("button", { name: "usage.lastDays:30" }));
    expect(lastKey("user-daily")).toEqual(["admin", "usage", "user-daily", "user-1", 30]);
    expect(dialog.getByText("d-29")).toBeInTheDocument();

    fireEvent.click(dialog.getByRole("button", { name: "close" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent("");
  });

  it("opens the detail from a ?user= deep link", () => {
    renderPage("/admin/usage?user=user-1");
    expect(screen.getByRole("dialog", { name: "writer" })).toBeInTheDocument();
    expect(lastKey("user-daily")).toEqual(["admin", "usage", "user-daily", "user-1", 7]);
  });

  it("opens the detail from a mobile card", () => {
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "usage.openUser:writer" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("shows the empty and error states", () => {
    queryMock.mockImplementation((options: QueryOptions) => {
      const name = options.queryKey[2];
      if (name === "summary") return { data: undefined, isLoading: false, isFetching: false, isError: true, refetch };
      if (name === "models") return { data: { ...modelsFor(""), items: [], totals: modelMetrics({ attempts: 0, success_rate: null }) }, isLoading: false, isFetching: false, isError: false, refetch };
      if (name === "stage-latency") {
        return {
          data: {
            ...stageLatencyFor(7),
            total_rounds: 0,
            ai_draft_survival: { ...stageLatencyFor(7).ai_draft_survival, eligible_drafts: 0, survival_rate: null, co_edit_rate: null },
          },
          isLoading: false,
          isFetching: false,
          isError: false,
          refetch,
        };
      }
      return { data: { ...period, items: [], total: 0, page: 1, page_size: 20 }, isLoading: false, isFetching: false, isError: false, refetch };
    });
    renderPage();
    expect(screen.getByText("usage.loadError")).toBeInTheDocument();
    expect(screen.getByText("usage.noUsers")).toBeInTheDocument();
    expect(screen.getByText("usage.models.empty")).toBeInTheDocument();
    expect(screen.getByText("usage.stageLatency.empty")).toBeInTheDocument();
    expect(screen.queryByText("usage.models.allModels")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "common:retry" }));
    expect(refetch).toHaveBeenCalled();
  });

  it("shows stage latency cards, slow tails, pending-body retries, 24h AI draft survival, and switches days", () => {
    renderPage();
    expect(screen.getByText("usage.stageLatency.title")).toBeInTheDocument();
    expect(screen.getByText("180 ms / 620 ms")).toBeInTheDocument();
    expect(screen.getByText("3.2 s / 12.5 s")).toBeInTheDocument();
    expect(screen.getByText("4.5 s / 11 s / 14 s")).toBeInTheDocument();
    expect(screen.getByText("usage.stageLatency.slowTailDetail:16.7%(3/18)")).toBeInTheDocument();
    expect(screen.getByText("usage.stageLatency.slowTailDetail:20%(2/10)")).toBeInTheDocument();
    expect(screen.getByText("10%")).toBeInTheDocument();
    expect(screen.getByText("usage.stageLatency.roundsDetail:2/20")).toBeInTheDocument();
    expect(screen.getByText("75%")).toBeInTheDocument();
    expect(screen.getByText("usage.stageLatency.draftSurvivalDetail:6/8|50%")).toBeInTheDocument();

    const rangeGroup = within(screen.getByRole("group", { name: "usage.stageLatency.range" }));
    fireEvent.click(rangeGroup.getByRole("button", { name: "usage.lastDays:30" }));
    expect(lastKey("stage-latency")).toEqual(["admin", "usage", "stage-latency", 30]);

    fireEvent.click(screen.getByRole("button", { name: "usage.refresh" }));
    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["admin", "usage", "stage-latency"] });
  });

  it("wires query functions to the admin API", async () => {
    renderPage("/admin/usage?user=user-1");
    const calls = queryMock.mock.calls.map(([options]) => options as QueryOptions);
    for (const options of calls.slice(-5)) {
      await options.queryFn();
    }
    expect(adminApi.getUsageByModel).toHaveBeenCalledWith("today", undefined);
    expect(adminApi.getUsageStageLatency).toHaveBeenCalledWith(7);
    expect(adminApi.getUsageSummary).toHaveBeenCalledWith("today");
    expect(adminApi.getUsageByUser).toHaveBeenCalledWith({ window: "today", sort: "cost", search: undefined, page: 1, page_size: 20 });
    expect(adminApi.getUserDailyUsage).toHaveBeenCalledWith("user-1", 7);
  });

  it("exposes keyboard-focusable scroll regions on usage tables", () => {
    renderPage();

    for (const label of [
      "usage.bySource",
      "usage.models.title",
      "usage.stageLatency.deviceTitle",
      "usage.stageLatency.dailyTitle",
      "usage.byUser",
    ]) {
      const region = screen.getByRole("region", { name: label });
      expect(region).toHaveAttribute("tabindex", "0");
    }

    fireEvent.click(within(screen.getByRole("group", { name: "usage.window" })).getByRole("button", { name: "usage.windows.7d" }));
    expect(screen.getByRole("region", { name: "usage.byDay" })).toHaveAttribute("tabindex", "0");
  });
});
