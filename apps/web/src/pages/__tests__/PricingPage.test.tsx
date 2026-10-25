import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGetCatalog = vi.fn();
const mockGetStatus = vi.fn();
const { trackEventMock } = vi.hoisted(() => ({
  trackEventMock: vi.fn(),
}));
let mockUser: { id: string } | null = null;
let mockLoading = false;
let mockLanguage = "zh-CN";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (
      key: string,
      defaultValueOrOptions?: string | Record<string, unknown>,
      maybeOptions?: Record<string, unknown>
    ) => {
      if (typeof defaultValueOrOptions === "string") {
        if (!maybeOptions) return defaultValueOrOptions;
        let output = defaultValueOrOptions;
        for (const [optionKey, optionValue] of Object.entries(maybeOptions)) {
          output = output.replace(`{{${optionKey}}}`, String(optionValue));
        }
        return output;
      }
      return key;
    },
    i18n: { language: mockLanguage },
  }),
}));

vi.mock("../../contexts/AuthContext", () => ({
  useAuth: () => ({
    user: mockUser,
    loading: mockLoading,
  }),
}));

vi.mock("../../config/auth", () => ({
  authConfig: {
    registrationEnabled: true,
  },
}));

vi.mock("../../components/PublicHeader", () => ({
  PublicHeader: () => <div data-testid="public-header" />,
}));

vi.mock("../../components/subscription/PaymentCheckoutModal", () => ({
  PaymentCheckoutModal: ({ initialCycle, isRenewal }: { initialCycle: "month" | "year"; isRenewal?: boolean }) => (
    <div data-testid="payment-checkout-cycle" data-renewal={String(Boolean(isRenewal))}>{initialCycle}</div>
  ),
}));

vi.mock("../../lib/subscriptionApi", () => ({
  subscriptionApi: {
    getCatalog: () => mockGetCatalog(),
    getStatus: () => mockGetStatus(),
  },
  subscriptionQueryKeys: {
    status: () => ["subscription-status", "test-user"],
  },
}));

vi.mock("../../lib/analytics", () => ({
  trackEvent: trackEventMock,
}));

import PricingPage from "../PricingPage";

function createWrapper(entry = "/pricing") {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[entry]}>{children}</MemoryRouter>
      </QueryClientProvider>
    );
  };
}

function createCatalog(
  tiers: Array<Record<string, unknown>>
): Record<string, unknown> {
  return {
    version: "2026-03",
    comparison_mode: "task_outcome",
    pricing_anchor_monthly_cents: 4900,
    tiers,
  };
}

function createDefaultTiers() {
  return [
    {
      id: "plan-free",
      name: "free",
      display_name: "免费版",
      price_monthly_cents: 0,
      price_yearly_cents: 0,
      recommended: false,
      summary_key: "starter",
      target_user_key: "explorer",
      entitlements: {
        ai_conversations_per_day: 3,
        writing_credits_monthly: 0,
        agent_runs_monthly: 0,
        active_projects_limit: 3,
        materials_library_access: false,
        material_uploads_monthly: 0,
        material_decompositions_monthly: 0,
        custom_skills_limit: 3,
        inspiration_copies_monthly: 10,
        export_formats: ["txt"],
      },
    },
    {
      id: "plan-pro",
      name: "pro",
      display_name: "Pro",
      price_monthly_cents: 4900,
      price_yearly_cents: 39900,
      recommended: true,
      summary_key: "creator",
      target_user_key: "daily_writer",
      entitlements: {
        ai_conversations_per_day: -1,
        writing_credits_monthly: 0,
        agent_runs_monthly: 0,
        active_projects_limit: -1,
        materials_library_access: true,
        material_uploads_monthly: 5,
        material_decompositions_monthly: 5,
        custom_skills_limit: 20,
        inspiration_copies_monthly: 100,
        export_formats: ["txt"],
      },
    },
  ];
}

function getControlByName(label: RegExp): HTMLElement | null {
  return (
    screen.queryByRole("button", { name: label }) ??
    screen.queryByRole("switch", { name: label }) ??
    screen.queryByRole("radio", { name: label }) ??
    screen.queryByRole("tab", { name: label }) ??
    screen.queryByLabelText(label)
  );
}

describe("PricingPage", () => {
  it("exposes the differences filter as a keyboard-operable switch", async () => {
    const [free, pro] = createDefaultTiers();
    mockGetCatalog.mockResolvedValue(createCatalog([free, { ...pro, entitlements: { ...pro.entitlements, custom_skills_limit: 3 } }]));
    render(<PricingPage />, { wrapper: createWrapper() });
    const toggle = await screen.findByRole("switch", { name: "仅看差异" });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-checked", "true");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-checked", "false");
  });

  it("omits the differences filter when every listed entitlement differs", async () => {
    mockGetCatalog.mockResolvedValue(createCatalog(createDefaultTiers()));
    render(<PricingPage />, { wrapper: createWrapper() });
    await screen.findByText("推荐");
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
  });

  it("omits comparison controls and paid actions when the catalog fails", async () => {
    mockGetCatalog.mockRejectedValue(new Error("offline"));
    render(<PricingPage />, { wrapper: createWrapper() });
    await screen.findByText("加载失败");
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
    for (const radio of screen.getAllByRole("radio")) expect(radio).toBeDisabled();
    expect(screen.queryByRole("button", { name: "开通 Pro" })).not.toBeInTheDocument();
  });

  beforeEach(() => {
    vi.clearAllMocks();
    mockGetStatus.mockResolvedValue({ tier: "free", status: "active" });
    mockUser = null;
    mockLoading = false;
    mockLanguage = "zh-CN";
  });

  it("renders catalog metrics and recommended badge", async () => {
    const [freeTier, proTier] = createDefaultTiers();
    mockGetCatalog.mockResolvedValue(
      createCatalog([
        {
          ...freeTier,
          entitlements: {
            ...(freeTier as { entitlements: Record<string, unknown> }).entitlements,
            export_formats: ["txt", "md"],
          },
        },
        {
          ...proTier,
          entitlements: {
            ...(proTier as { entitlements: Record<string, unknown> }).entitlements,
            export_formats: ["txt", "md", "docx", "pdf"],
          },
        },
      ])
    );

    render(<PricingPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      // The title stays put while loading; the catalog is ready once the loading status is gone.
      expect(screen.queryByRole("status")).not.toBeInTheDocument();
      expect(screen.getByText("推荐")).toBeInTheDocument();
      expect(screen.getAllByText("每日 AI 消息").length).toBeGreaterThan(0);
    });
    expect(screen.getAllByText("北京时间 00:00 重置").length).toBeGreaterThan(0);
    expect(screen.queryByText("可创作体量")).not.toBeInTheDocument();
    expect(screen.queryByText("Agent 深度任务")).not.toBeInTheDocument();
    expect(screen.getByText("支付宝单次支付，不会自动续费")).toBeInTheDocument();
    expect(screen.queryByText(/服务器/)).not.toBeInTheDocument();
  });

  it("still renders when the API has not shipped a new entitlement yet", async () => {
    // Older API catalog: no ai_conversations_per_day on any tier.
    const tiers = createDefaultTiers().map((tier) => {
      const { ai_conversations_per_day: _omitted, ...entitlements } = tier.entitlements;
      return { ...tier, entitlements };
    });
    mockGetCatalog.mockResolvedValue(createCatalog(tiers));

    render(<PricingPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      // The title stays put while loading; the catalog is ready once the loading status is gone.
      expect(screen.queryByRole("status")).not.toBeInTheDocument();
    });
    expect(screen.queryByText("每日 AI 消息")).not.toBeInTheDocument();
    expect(screen.getAllByText("自定义技能").length).toBeGreaterThan(0);
  });

  it("shows only the selected billing cycle price and keeps the annual saving in the cycle control", async () => {
    mockGetCatalog.mockResolvedValue(createCatalog(createDefaultTiers()));

    render(<PricingPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      // The title stays put while loading; the catalog is ready once the loading status is gone.
      expect(screen.queryByRole("status")).not.toBeInTheDocument();
    });

    const monthlyControl = getControlByName(/月付|Monthly/i);
    const yearlyControl = getControlByName(/年付|Yearly/i);

    expect(monthlyControl).not.toBeNull();
    expect(yearlyControl).not.toBeNull();
    expect(monthlyControl).toHaveAttribute("role", "radio");
    // Monthly is the default offer. The annual saving stays in the cycle
    // control, while each plan card shows only the selected cycle's price.
    expect(monthlyControl).toHaveAttribute("aria-checked", "true");
    expect(yearlyControl).toHaveAttribute("aria-checked", "false");
    expect(screen.getByText(/¥49\/月|¥49\/month/i)).toBeInTheDocument();
    expect(screen.queryByText(/¥399\/年|¥399\/year/i)).not.toBeInTheDocument();
    expect(yearlyControl).toHaveTextContent(/省\s*32%|save\s*32%/i);
    expect(screen.getAllByText(/省\s*32%|save\s*32%/i)).toHaveLength(1);

    if (yearlyControl) {
      fireEvent.click(yearlyControl);
    }

    await waitFor(() => {
      expect(screen.getByText(/¥399\/年|¥399\/year/i)).toBeInTheDocument();
      expect(screen.queryByText(/¥49\/月|¥49\/month/i)).not.toBeInTheDocument();
      expect(yearlyControl).toHaveAttribute("aria-checked", "true");
    });
  });

  it("prices Pro in USD for English readers and takes the annual saving from the USD prices", async () => {
    mockLanguage = "en";
    mockGetCatalog.mockResolvedValue(createCatalog(createDefaultTiers()));

    render(<PricingPage />, { wrapper: createWrapper() });

    expect(await screen.findByText("$9.99/月")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /年付/ })).toHaveTextContent("省 34%");
    expect(screen.queryByText(/¥/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("radio", { name: /年付/ }));
    expect(await screen.findByText("$79/年")).toBeInTheDocument();
  });

  it("covers difference-only filter behavior", async () => {
    const [freeTier, proTier] = createDefaultTiers();
    // Same skills limit on both plans: the row only shows when listing everything.
    mockGetCatalog.mockResolvedValue(
      createCatalog([
        freeTier,
        { ...proTier, entitlements: { ...proTier.entitlements, custom_skills_limit: 3 } },
      ])
    );

    render(<PricingPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      // The title stays put while loading; the catalog is ready once the loading status is gone.
      expect(screen.queryByRole("status")).not.toBeInTheDocument();
    });

    const diffControl = getControlByName(/仅看差异|Only differences?/i);
    expect(diffControl).not.toBeNull();
    expect(screen.getAllByText("自定义技能").length).toBeGreaterThan(0);

    if (diffControl) {
      fireEvent.click(diffControl);
    }

    await waitFor(() => {
      expect(screen.queryAllByText("自定义技能")).toHaveLength(0);
    });
    expect(screen.getAllByText("每日 AI 消息").length).toBeGreaterThan(0);
  });

  it("renders one action per plan without a duplicate page-level action bar", async () => {
    mockGetCatalog.mockResolvedValue(createCatalog(createDefaultTiers()));

    render(<PricingPage />, { wrapper: createWrapper() });

    await screen.findAllByText("每日 AI 消息");
    expect(screen.getAllByRole("button", { name: /免费开始|Start Free|Create Project Free/i })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: /开通 Pro|Get Pro/i })).toHaveLength(1);
  });

  it("defaults the public pricing page and checkout to monthly", async () => {
    mockUser = { id: "user-1" };
    mockGetCatalog.mockResolvedValue(createCatalog(createDefaultTiers()));

    render(<PricingPage />, { wrapper: createWrapper() });
    await waitFor(() => expect(getControlByName(/月付|Monthly/i)).toBeEnabled());

    expect(getControlByName(/月付|Monthly/i)).toHaveAttribute("aria-checked", "true");
    fireEvent.click(screen.getAllByRole("button", { name: /开通 Pro|Get Pro/i })[0]);
    expect(screen.getByTestId("payment-checkout-cycle")).toHaveTextContent("month");
  });

  it("preserves an explicit annual deep link on the public pricing page", async () => {
    mockUser = { id: "user-1" };
    mockGetCatalog.mockResolvedValue(createCatalog(createDefaultTiers()));

    render(<PricingPage />, { wrapper: createWrapper("/pricing?cycle=yearly") });
    await waitFor(() => expect(getControlByName(/年付|Yearly/i)).toBeEnabled());

    expect(getControlByName(/年付|Yearly/i)).toHaveAttribute("aria-checked", "true");
    fireEvent.click(screen.getAllByRole("button", { name: /开通 Pro|Get Pro/i })[0]);
    expect(screen.getByTestId("payment-checkout-cycle")).toHaveTextContent("year");
  });

  it("does not claim unlimited Pro benefits when the catalog supplies finite limits", async () => {
    const [freeTier, proTier] = createDefaultTiers();
    mockGetCatalog.mockResolvedValue(
      createCatalog([
        freeTier,
        {
          ...proTier,
          entitlements: {
            ...proTier.entitlements,
            ai_conversations_per_day: 12,
            active_projects_limit: 8,
          },
        },
      ])
    );

    render(<PricingPage />, { wrapper: createWrapper() });

    expect(await screen.findByText("12 条/天")).toBeInTheDocument();
    expect(screen.getByText("8 个")).toBeInTheDocument();
    expect(screen.queryByText(/不限|unlimited/i)).not.toBeInTheDocument();
  });

  it("opens checkout with the selected yearly cycle for an authenticated user", async () => {
    mockUser = { id: "user-1" };
    mockGetCatalog.mockResolvedValue(createCatalog(createDefaultTiers()));

    render(<PricingPage />, { wrapper: createWrapper() });
    await waitFor(() => expect(getControlByName(/年付|Yearly/i)).toBeEnabled());

    fireEvent.click(getControlByName(/年付|Yearly/i)!);
    fireEvent.click(screen.getAllByRole("button", { name: /开通 Pro|Get Pro/i })[0]);

    expect(screen.getByTestId("payment-checkout-cycle")).toHaveTextContent("year");
  });

  it("compares only what differs by plan, with not-included instead of zero", async () => {
    mockGetCatalog.mockResolvedValue(createCatalog(createDefaultTiers()));

    render(<PricingPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getAllByText("素材拆解").length).toBeGreaterThan(0);
    });
    // TXT export is on every plan, so the comparison does not list it.
    expect(screen.queryByText("导出格式")).not.toBeInTheDocument();
    expect(screen.queryByText("TXT")).not.toBeInTheDocument();
    expect(screen.getByText("不含")).toBeInTheDocument();
    expect(screen.queryByText(/^0 次\/月$/)).not.toBeInTheDocument();
    expect(screen.getByText("3 条/天")).toBeInTheDocument();
    expect(screen.getByText("免费版")).toBeInTheDocument();
  });

  it("offers renewal to a signed-in Pro user", async () => {
    mockUser = { id: "user-1" };
    mockGetStatus.mockResolvedValue({ tier: "pro", status: "active" });
    mockGetCatalog.mockResolvedValue(createCatalog(createDefaultTiers()));

    render(<PricingPage />, { wrapper: createWrapper() });

    const renew = await screen.findAllByRole("button", { name: "续费 Pro" });
    expect(screen.queryByRole("button", { name: "开通 Pro" })).not.toBeInTheDocument();
    fireEvent.click(renew[0]);
    expect(screen.getByTestId("payment-checkout-cycle")).toHaveAttribute("data-renewal", "true");
  });

  it("keeps the page title while the catalog loads and announces loading separately", () => {
    mockGetCatalog.mockReturnValue(new Promise(() => {}));

    render(<PricingPage />, { wrapper: createWrapper() });

    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("选一个适合你的方案");
    expect(screen.getByRole("status")).toHaveTextContent("加载中…");
  });

  it("shows error state when catalog request fails", async () => {
    mockGetCatalog.mockRejectedValue(new Error("network"));

    render(<PricingPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText("加载失败")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
    });
  });

  it("waits for auth hydration before tracking pricing page view", async () => {
    mockGetCatalog.mockResolvedValue(createCatalog(createDefaultTiers()));
    mockLoading = true;

    const { rerender } = render(<PricingPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      // The title stays put while loading; the catalog is ready once the loading status is gone.
      expect(screen.queryByRole("status")).not.toBeInTheDocument();
    });
    expect(trackEventMock).not.toHaveBeenCalledWith(
      "pricing_page_view",
      expect.anything()
    );

    mockLoading = false;
    mockUser = { id: "user-1" };
    rerender(<PricingPage />);

    await waitFor(() => {
      const pricingCalls = trackEventMock.mock.calls.filter(
        ([eventName]) => eventName === "pricing_page_view"
      );
      expect(pricingCalls).toHaveLength(1);
      expect(pricingCalls[0]?.[1]).toMatchObject({
        is_authenticated: true,
      });
    });
  });
});
