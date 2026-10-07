import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import UsageCostPage from "../UsageCostPage";
import { adminApi } from "../../../lib/adminApi";

type QueryOptions = { queryKey: unknown[]; queryFn: () => unknown; enabled?: boolean };
const queryMock = vi.fn();
const refetch = vi.fn();

vi.mock("@tanstack/react-query", () => ({
  useQuery: (options: QueryOptions) => queryMock(options),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown> | string) => {
      if (typeof options === "object" && options) {
        if ("count" in options) return `${key}:${String(options.count)}`;
        if ("name" in options) return `${key}:${String(options.name)}`;
        if ("peak" in options) return `${key}:${String(options.peak)}|${String(options.offpeak)}`;
      }
      return key;
    },
    i18n: { language: "en-US" },
  }),
}));
vi.mock("../../../lib/i18n-helpers", () => ({ getLocaleCode: () => "en-US" }));
vi.mock("../../../lib/adminApi", () => ({
  adminApi: { getUsageSummary: vi.fn(), getUsageByUser: vi.fn(), getUserDailyUsage: vi.fn() },
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
      return { data: detail(Number(rest[1])), isLoading: false, isError: false, refetch };
    });
  });

  it("shows KPIs, price line and per-source cost for today", () => {
    renderPage();
    expect(screen.getByText("¥12.35")).toBeInTheDocument();
    expect(screen.getByText("¥10.00 / ¥2.35")).toBeInTheDocument();
    expect(screen.getByText("1,000 / 200 / 50")).toBeInTheDocument();
    expect(screen.getByText(/usage.priceLine:usage.peak 0.04 \/ 2 \/ 8\|usage.offpeak 0.02 \/ 1 \/ 4/)).toBeInTheDocument();
    expect(screen.getByText("usage.notCounted")).toBeInTheDocument();
    expect(screen.getByText("usage.sources.material")).toBeInTheDocument();
    // The daily breakdown is only for the 7-day window.
    expect(screen.queryByText("usage.byDay")).not.toBeInTheDocument();
    expect(lastKey("summary")).toEqual(["admin", "usage", "summary", "today"]);
  });

  it("switches window, shows 7 daily rows and resets paging", () => {
    renderPage();
    fireEvent.click(screen.getAllByRole("button", { name: "common:next" })[0]);
    expect(lastKey("users")).toEqual(["admin", "usage", "users", "today", "cost", "", 2]);

    const windows = within(screen.getByRole("group", { name: "usage.window" }));
    fireEvent.click(windows.getByRole("button", { name: "usage.windows.7d" }));
    expect(windows.getByRole("button", { name: "usage.windows.7d" })).toHaveAttribute("aria-pressed", "true");
    expect(lastKey("summary")).toEqual(["admin", "usage", "summary", "7d"]);
    expect(lastKey("users")).toEqual(["admin", "usage", "users", "7d", "cost", "", 1]);
    expect(screen.getByText("usage.byDay")).toBeInTheDocument();
    for (const date of ["2026-10-01", "2026-10-04", "2026-10-07"]) {
      expect(screen.getByText(date)).toBeInTheDocument();
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
      return { data: { ...period, items: [], total: 0, page: 1, page_size: 20 }, isLoading: false, isFetching: false, isError: false, refetch };
    });
    renderPage();
    expect(screen.getByText("usage.loadError")).toBeInTheDocument();
    expect(screen.getByText("usage.noUsers")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "common:retry" }));
    expect(refetch).toHaveBeenCalled();
  });

  it("wires query functions to the admin API", async () => {
    renderPage("/admin/usage?user=user-1");
    const calls = queryMock.mock.calls.map(([options]) => options as QueryOptions);
    for (const options of calls.slice(-3)) {
      await options.queryFn();
    }
    expect(adminApi.getUsageSummary).toHaveBeenCalledWith("today");
    expect(adminApi.getUsageByUser).toHaveBeenCalledWith({ window: "today", sort: "cost", search: undefined, page: 1, page_size: 20 });
    expect(adminApi.getUserDailyUsage).toHaveBeenCalledWith("user-1", 7);
  });
});
