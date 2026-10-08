import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGetCatalog = vi.fn();
const mockGetOptions = vi.fn();

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, defaultValue?: string | Record<string, unknown>, options?: Record<string, unknown>) => {
      if (typeof defaultValue !== "string") return key;
      return defaultValue.replace(/\{\{(\w+)\}\}/g, (_match, name: string) => String(options?.[name] ?? ""));
    },
    i18n: { language: "zh-CN" },
  }),
}));

vi.mock("../../contexts/AuthContext", () => ({
  useAuth: () => ({ user: { id: "user-1" }, loading: false }),
}));

vi.mock("../../config/auth", () => ({
  authConfig: { registrationEnabled: true },
}));

vi.mock("../../components/PublicHeader", () => ({
  PublicHeader: () => <div data-testid="public-header" />,
}));

vi.mock("../../lib/analytics", () => ({ trackEvent: vi.fn() }));

vi.mock("../../lib/subscriptionApi", () => ({
  subscriptionApi: {
    getCatalog: () => mockGetCatalog(),
    getStatus: () => Promise.resolve({ tier: "free", status: "active" }),
  },
  subscriptionQueryKeys: {
    status: () => ["subscription-status", "test-user"],
  },
}));

vi.mock("../../lib/paymentApi", async () => {
  const actual = await vi.importActual<typeof import("../../lib/paymentApi")>("../../lib/paymentApi");
  return {
    ...actual,
    paymentApi: {
      getOptions: () => mockGetOptions(),
      createOrder: vi.fn(),
    },
  };
});

import PricingPage from "../PricingPage";

function renderPricingPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/pricing"]}>
        <PricingPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

const entitlements = {
  ai_conversations_per_day: 10,
  active_projects_limit: 3,
  material_decompositions_monthly: 5,
  custom_skills_limit: 3,
};

describe("PricingPage checkout when online payment is off", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetOptions.mockResolvedValue({ enabled: false, payment_methods: [] });
    mockGetCatalog.mockResolvedValue({
      version: "2026-03",
      comparison_mode: "task_outcome",
      pricing_anchor_monthly_cents: 4900,
      tiers: [
        {
          id: "plan-free",
          name: "free",
          display_name: "免费版",
          price_monthly_cents: 0,
          price_yearly_cents: 0,
          recommended: false,
          summary_key: "starter",
          target_user_key: "explorer",
          entitlements,
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
          entitlements: { ...entitlements, ai_conversations_per_day: -1 },
        },
      ],
    });
  });

  it("sends the buyer to the billing page for a redeem code, since this page has no 兑换码 button", async () => {
    renderPricingPage();

    const [upgradeButton] = await screen.findAllByRole("button", { name: /开通 Pro/ });
    fireEvent.click(upgradeButton);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("暂时无法在线支付。有兑换码的话，可以在「订阅权益」页点「兑换码」开通。");
    expect(alert).not.toHaveTextContent("点页面上的「兑换码」");
    expect(screen.queryByRole("button", { name: "兑换码" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "去订阅权益页" })).toHaveAttribute("href", "/dashboard/billing?plan=pro");
  });
});
