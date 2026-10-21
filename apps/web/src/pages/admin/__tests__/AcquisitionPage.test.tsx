import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const { getAcquisitionStats } = vi.hoisted(() => ({ getAcquisitionStats: vi.fn() }));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string | Record<string, unknown>, options?: Record<string, unknown>) =>
      typeof fallback === "string"
        ? fallback.replace(/\{\{(\w+)\}\}/g, (_match, name: string) => String(options?.[name] ?? ""))
        : key,
  }),
}));

vi.mock("../../../lib/adminApi", () => ({ adminApi: { getAcquisitionStats } }));

import AcquisitionPage from "../AcquisitionPage";

const stats = {
  window_days: 30,
  period_start: "2026-09-21T16:00:00+00:00",
  period_end: "2026-10-21T08:00:00+00:00",
  total_signups: 5,
  attributed_signups: 4,
  channels: [
    { channel: "search", signups: 3, activated_users: 2, activation_rate: 0.6667, paid_users: 1, paid_rate: 0.3333 },
    { channel: "unknown", signups: 1, activated_users: 0, activation_rate: 0, paid_users: 0, paid_rate: 0 },
  ],
  sources: [
    { channel: "search", source: "www.google.com", signups: 3, activated_users: 2, activation_rate: 0.6667, paid_users: 1, paid_rate: 0.3333 },
    { channel: "unknown", source: "", signups: 1, activated_users: 0, activation_rate: 0, paid_users: 0, paid_rate: 0 },
  ],
};

const renderPage = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AcquisitionPage />
    </QueryClientProvider>,
  );

describe("AcquisitionPage", () => {
  beforeEach(() => {
    getAcquisitionStats.mockReset();
    getAcquisitionStats.mockResolvedValue(stats);
  });

  it("lists sign-ups by channel and source with activation and paid rates", async () => {
    renderPage();

    const [channelTable] = await screen.findAllByRole("table");
    const searchRow = within(channelTable).getByText("搜索引擎").closest("tr")!;
    expect(within(searchRow).getByText("3")).toBeInTheDocument();
    expect(within(searchRow).getByText("2（66.7%）")).toBeInTheDocument();
    expect(within(searchRow).getByText("1（33.3%）")).toBeInTheDocument();
    expect(screen.getByText("www.google.com")).toBeInTheDocument();
    expect(screen.getAllByText("未记录")).toHaveLength(2);
    expect(getAcquisitionStats).toHaveBeenCalledWith(30);
  });

  it("reloads for another window", async () => {
    renderPage();
    await screen.findByText("www.google.com");

    fireEvent.click(screen.getByRole("button", { name: "90天" }));

    await waitFor(() => expect(getAcquisitionStats).toHaveBeenLastCalledWith(90));
    expect(screen.getByRole("button", { name: "90天" })).toHaveAttribute("aria-pressed", "true");
  });
});
