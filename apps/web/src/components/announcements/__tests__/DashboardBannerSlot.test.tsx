import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getCurrent = vi.hoisted(() => vi.fn());
const getStatus = vi.hoisted(() => vi.fn());

vi.mock("../../../lib/announcementApi", () => ({
  announcementApi: { getCurrent },
}));

vi.mock("../../../lib/subscriptionApi", () => ({
  subscriptionApi: { getStatus },
  subscriptionQueryKeys: { status: () => ["subscription-status", "test"] },
}));

vi.mock("../../../lib/upgradeAnalytics", () => ({
  trackUpgradeClick: vi.fn(),
  trackUpgradeExpose: vi.fn(),
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (_key: string, fallback: string, options?: Record<string, string>) =>
      fallback.replace(/\{\{(\w+)\}\}/g, (_match, name: string) => options?.[name] ?? ""),
  }),
}));

import { DashboardBannerSlot } from "../DashboardBannerSlot";

const announcement = {
  title: "功能更新",
  description: "现在可以更专注地继续创作。",
  action_label: null,
  action_url: null,
  version: 3,
  updated_at: "2026-10-17T00:00:00Z",
};

function proStatus(daysRemaining: number, overrides: Record<string, unknown> = {}) {
  return {
    tier: "pro",
    status: "active",
    display_name: "专业版",
    current_period_end: "2026-11-04T12:00:00",
    days_remaining: daysRemaining,
    features: {},
    ...overrides,
  };
}

function renderSlot() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnMount: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <DashboardBannerSlot accountId="user-1" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("DashboardBannerSlot", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
    getCurrent.mockResolvedValue(announcement);
  });

  it.each([
    ["8 days left", () => getStatus.mockResolvedValue(proStatus(8)), null],
    ["7 days left", () => getStatus.mockResolvedValue(proStatus(7)), /^你的 Pro .+到期$/],
    ["1 day left", () => getStatus.mockResolvedValue(proStatus(1)), "你的 Pro 快到期了"],
    ["under a day left", () => getStatus.mockResolvedValue(proStatus(0)), "你的 Pro 快到期了"],
    ["free plan", () => getStatus.mockResolvedValue(proStatus(0, { tier: "free", current_period_end: null, days_remaining: null })), null],
    ["cancelled Pro", () => getStatus.mockResolvedValue(proStatus(1, { status: "cancelled" })), null],
    ["status request failed", () => getStatus.mockRejectedValue(new Error("offline")), null],
  ])("with %s shows the matching banner", async (_label, arrange, reminderTitle) => {
    arrange();
    renderSlot();
    if (reminderTitle) {
      expect(await screen.findByText(reminderTitle)).toBeVisible();
      expect(screen.queryByText("功能更新")).not.toBeInTheDocument();
    } else {
      expect(await screen.findByText("功能更新")).toBeVisible();
      expect(screen.queryByRole("region", { name: "Pro 到期提醒" })).not.toBeInTheDocument();
    }
  });

  it("sends renewals to checkout attributed to the reminder", async () => {
    getStatus.mockResolvedValue(proStatus(5));
    renderSlot();
    const renew = await screen.findByRole("link", { name: "续费 Pro" });
    expect(renew).toHaveAttribute("href", "/dashboard/billing?plan=pro&source=renewal_reminder");
  });

  it("keeps a dismissed 7-day reminder closed but still shows the final one", async () => {
    getStatus.mockResolvedValue(proStatus(6));
    const first = renderSlot();
    fireEvent.click(await screen.findByRole("button", { name: "关闭提醒" }));
    expect(await screen.findByText("功能更新")).toBeVisible();
    first.unmount();

    const second = renderSlot();
    expect(await screen.findByText("功能更新")).toBeVisible();
    await waitFor(() => expect(getStatus).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole("region", { name: "Pro 到期提醒" })).not.toBeInTheDocument();
    second.unmount();

    getStatus.mockResolvedValue(proStatus(1));
    renderSlot();
    expect(await screen.findByText("你的 Pro 快到期了")).toBeVisible();
  });
});
