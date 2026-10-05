import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGetCatalog = vi.fn();
const { trackEventMock } = vi.hoisted(() => ({
  trackEventMock: vi.fn(),
}));
let mockUser: { id: string } | null = null;
let mockLoading = false;

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
    i18n: { language: "zh-CN" },
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
  PaymentCheckoutModal: ({ initialCycle }: { initialCycle: "month" | "year" }) => (
    <div data-testid="payment-checkout-cycle">{initialCycle}</div>
  ),
}));

vi.mock("../../lib/subscriptionApi", () => ({
  subscriptionApi: {
    getCatalog: () => mockGetCatalog(),
  },
}));

vi.mock("../../lib/analytics", () => ({
  trackEvent: trackEventMock,
}));

import PricingPage from "../PricingPage";

function createWrapper() {
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
        <MemoryRouter>{children}</MemoryRouter>
      </QueryClientProvider>
    );
  };
}

function createCatalog(
  tiers: Array<Record<string, unknown>>
): Record<string, unknown> {
  return {
    version: "2026-02",
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
        writing_credits_monthly: 120000,
        agent_runs_monthly: 20,
        active_projects_limit: 1,
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
        writing_credits_monthly: 600000,
        agent_runs_monthly: 120,
        active_projects_limit: 5,
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
    screen.queryByRole("radio", { name: label }) ??
    screen.queryByRole("tab", { name: label }) ??
    screen.queryByLabelText(label)
  );
}

describe("PricingPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUser = null;
    mockLoading = false;
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
      expect(screen.getByText("套餐权益对比")).toBeInTheDocument();
      expect(screen.getByText("推荐")).toBeInTheDocument();
      expect(screen.getAllByText("可创作体量").length).toBeGreaterThan(0);
    });
  });

  it("covers monthly/yearly switch with annual saving hint", async () => {
    mockGetCatalog.mockResolvedValue(createCatalog(createDefaultTiers()));

    render(<PricingPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText("套餐权益对比")).toBeInTheDocument();
    });

    const monthlyControl = getControlByName(/月付|Monthly/i);
    const yearlyControl = getControlByName(/年付|Yearly/i);

    expect(monthlyControl).not.toBeNull();
    expect(yearlyControl).not.toBeNull();
    expect(monthlyControl).toHaveAttribute("role", "radio");
    expect(monthlyControl).toHaveAttribute("aria-checked", "true");
    expect(yearlyControl).toHaveAttribute("aria-checked", "false");

    if (yearlyControl) {
      fireEvent.click(yearlyControl);
    }

    await waitFor(() => {
      expect(screen.getByText(/¥399\/年|¥399\/year/i)).toBeInTheDocument();
      expect(screen.getByText(/省|节省|save/i)).toBeInTheDocument();
      expect(yearlyControl).toHaveAttribute("aria-checked", "true");
    });
  });

  it("covers difference-only filter behavior", async () => {
    mockGetCatalog.mockResolvedValue(createCatalog(createDefaultTiers()));

    render(<PricingPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText("套餐权益对比")).toBeInTheDocument();
    });

    const diffControl = getControlByName(/仅看差异|Only differences?/i);
    expect(diffControl).not.toBeNull();

    const beforeCount = screen.queryAllByText("TXT").length;
    expect(beforeCount).toBeGreaterThan(0);

    if (diffControl) {
      fireEvent.click(diffControl);
    }

    await waitFor(() => {
      expect(screen.queryAllByText("TXT").length).toBeLessThan(beforeCount);
    });
  });

  it("covers distinct CTA copy for free-start and pro-upgrade", async () => {
    mockGetCatalog.mockResolvedValue(createCatalog(createDefaultTiers()));

    render(<PricingPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getAllByRole("button", { name: /免费开始|Start Free|Create Project Free/i }).length).toBeGreaterThan(0);
      expect(screen.getAllByRole("button", { name: /升级专业版|Upgrade Pro|Upgrade to Pro/i }).length).toBeGreaterThan(0);
    });
  });

  it("opens checkout with the selected yearly cycle for an authenticated user", async () => {
    mockUser = { id: "user-1" };
    mockGetCatalog.mockResolvedValue(createCatalog(createDefaultTiers()));

    render(<PricingPage />, { wrapper: createWrapper() });
    await waitFor(() => expect(getControlByName(/年付|Yearly/i)).not.toBeNull());

    fireEvent.click(getControlByName(/年付|Yearly/i)!);
    fireEvent.click(screen.getAllByRole("button", { name: /升级专业版|Upgrade Pro|Upgrade to Pro/i })[0]);

    expect(screen.getByTestId("payment-checkout-cycle")).toHaveTextContent("year");
  });

  it("handles empty export format arrays safely", async () => {
    const [, proTier] = createDefaultTiers();
    mockGetCatalog.mockResolvedValue(
      createCatalog([
        {
          ...proTier,
          entitlements: {
            ...(proTier as { entitlements: Record<string, unknown> }).entitlements,
            export_formats: [],
          },
        },
      ])
    );

    render(<PricingPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(screen.getByText("导出格式")).toBeInTheDocument();
      expect(screen.getAllByRole("button", { name: /免费开始|Start Free/i }).length).toBeGreaterThan(0);
    });
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
      expect(screen.getByText("套餐权益对比")).toBeInTheDocument();
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
