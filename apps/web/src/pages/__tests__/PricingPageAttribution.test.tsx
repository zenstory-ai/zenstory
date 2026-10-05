import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGetCatalog = vi.fn();
const mockNavigate = vi.fn();
const mockTrackEvent = vi.fn();

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, defaultValue?: string) => defaultValue ?? key,
    i18n: { language: "zh-CN" },
  }),
}));

vi.mock("../../contexts/AuthContext", () => ({
  useAuth: () => ({
    user: { id: "user-1" },
    loading: false,
  }),
}));

vi.mock("../../hooks/useMediaQuery", () => ({
  useIsMobile: () => false,
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
  PaymentCheckoutModal: ({ isOpen }: { isOpen: boolean }) => isOpen ? <div>Payment checkout</div> : null,
}));

vi.mock("../../lib/analytics", () => ({
  trackEvent: (...args: unknown[]) => mockTrackEvent(...args),
}));

vi.mock("../../lib/subscriptionApi", () => ({
  subscriptionApi: {
    getCatalog: () => mockGetCatalog(),
  },
}));

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

import PricingPage from "../PricingPage";

function createWrapper(initialEntries: string[]) {
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
        <MemoryRouter initialEntries={initialEntries}>{children}</MemoryRouter>
      </QueryClientProvider>
    );
  };
}

describe("PricingPage attribution", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetCatalog.mockResolvedValue({
      version: "2026-02",
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
          entitlements: {
            writing_credits_monthly: 120000,
            agent_runs_monthly: 20,
            active_projects_limit: 1,
            context_tokens_limit: 4096,
            material_uploads_monthly: 5,
            material_decompositions_monthly: 5,
            custom_skills_limit: 3,
            inspiration_copies_monthly: 10,
            export_formats: ["txt"],
            priority_queue_level: "standard",
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
            context_tokens_limit: 16384,
            material_uploads_monthly: 50,
            material_decompositions_monthly: 50,
            custom_skills_limit: 20,
            inspiration_copies_monthly: 100,
            export_formats: ["txt"],
            priority_queue_level: "priority",
          },
        },
      ],
    });
  });

  it("preserves attribution when opening checkout for an authenticated user", async () => {
    render(<PricingPage />, { wrapper: createWrapper(["/pricing?source=chat_quota_blocked"]) });

    await waitFor(() => {
      expect(screen.getAllByRole("button", { name: /升级专业版|Upgrade Pro|Upgrade to Pro/i }).length).toBeGreaterThan(0);
    });

    fireEvent.click(screen.getAllByRole("button", { name: /升级专业版|Upgrade Pro|Upgrade to Pro/i })[0]);

    expect(screen.getByText("Payment checkout")).toBeInTheDocument();
    expect(mockNavigate).not.toHaveBeenCalled();
    expect(mockTrackEvent).toHaveBeenCalledWith(
      "pricing_upgrade_cta_clicked",
      expect.objectContaining({ attribution_source: "chat_quota_blocked", is_authenticated: true })
    );
  });
});
