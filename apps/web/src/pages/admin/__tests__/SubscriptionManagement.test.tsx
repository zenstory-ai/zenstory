import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";

import SubscriptionManagement from "../SubscriptionManagement";
import { toast } from "../../../lib/toast";

const useQueryMock = vi.fn();
const useMutationMock = vi.fn();
const invalidateQueriesMock = vi.fn();
const mutateMock = vi.fn();
// When on, t() returns the component's inline fallback copy, so tests can check the wording users see.
const i18nState = vi.hoisted(() => ({ useFallback: false }));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: unknown) =>
      i18nState.useFallback && typeof fallback === "string" ? fallback : key,
    i18n: { language: "zh-CN" },
  }),
}));

vi.mock("@tanstack/react-query", () => ({
  useQuery: (...args: unknown[]) => useQueryMock(...args),
  useMutation: (...args: unknown[]) => useMutationMock(...args),
  useQueryClient: () => ({
    invalidateQueries: invalidateQueriesMock,
  }),
}));

vi.mock("../../../lib/subscriptionEntitlements", () => ({
  getLocalizedPlanDisplayName: (plan: { display_name?: string; display_name_en?: string; name?: string }) =>
    plan.display_name ?? plan.display_name_en ?? plan.name ?? "",
}));

vi.mock("../../../lib/toast", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

const defaultPlan = [
  {
    id: "plan-pro",
    name: "pro",
    display_name: "Pro",
    display_name_en: "Pro",
    price_monthly_cents: 1999,
    price_yearly_cents: 19999,
    features: {},
    is_active: true,
  },
];

const subscriptionItem = {
  id: "sub-1",
  user_id: "user-1",
  username: "writer",
  email: "writer@example.com",
  plan_name: "pro",
  plan_display_name: "Pro",
  plan_display_name_en: "Pro",
  status: "active",
  current_period_start: "2026-03-01T00:00:00Z",
  current_period_end: "2026-04-01T00:00:00Z",
  created_at: "2026-03-01T00:00:00Z",
  updated_at: "2026-03-01T00:00:00Z",
  has_subscription_record: true,
};

const mockQueries = ({
  subscriptionsData,
  subscriptionsLoading = false,
  subscriptionsError = false,
  subscriptionsErrorMessage,
  plansData = defaultPlan,
}: {
  subscriptionsData?: unknown;
  subscriptionsLoading?: boolean;
  subscriptionsError?: boolean;
  subscriptionsErrorMessage?: string;
  plansData?: unknown;
}) => {
  const refetchMock = vi.fn();

  useQueryMock.mockImplementation(({ queryKey }: { queryKey?: unknown[] }) => {
    if (Array.isArray(queryKey) && queryKey[1] === "subscriptions") {
      return {
        data: subscriptionsData,
        isLoading: subscriptionsLoading,
        isFetching: false,
        isError: subscriptionsError,
        error: subscriptionsErrorMessage ? new Error(subscriptionsErrorMessage) : null,
        refetch: refetchMock,
      };
    }

    if (Array.isArray(queryKey) && queryKey[1] === "plans") {
      return {
        data: plansData,
        isLoading: false,
        isFetching: false,
        isError: false,
        error: null,
        refetch: vi.fn(),
      };
    }

    return {
      data: undefined,
      isLoading: false,
      isFetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    };
  });

  return { refetchMock };
};

describe("SubscriptionManagement", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    i18nState.useFallback = false;
    useMutationMock.mockReturnValue({
      mutate: mutateMock,
      isPending: false,
    });
  });

  it("shows loading state", () => {
    mockQueries({ subscriptionsLoading: true });

    render(<SubscriptionManagement />);
    expect(screen.getByText("common:loading")).toBeInTheDocument();
  });

  it("shows error state and supports retry", () => {
    const { refetchMock } = mockQueries({
      subscriptionsError: true,
      subscriptionsErrorMessage: "load subscriptions failed",
    });

    render(<SubscriptionManagement />);
    expect(screen.getByText("load subscriptions failed")).toBeInTheDocument();

    fireEvent.click(screen.getByText("common:retry"));
    expect(refetchMock).toHaveBeenCalledTimes(1);
  });

  it("shows empty state", () => {
    mockQueries({
      subscriptionsData: { items: [], total: 0, page: 1, page_size: 20 },
    });

    render(<SubscriptionManagement />);
    expect(screen.getByText("common:noData")).toBeInTheDocument();
  });

  it("submits duration update from modify modal", () => {
    mockQueries({
      subscriptionsData: { items: [subscriptionItem], total: 1, page: 1, page_size: 20 },
    });

    render(<SubscriptionManagement />);

    fireEvent.click(screen.getByTitle("subscriptions.modify"));
    fireEvent.change(screen.getByRole("spinbutton"), { target: { value: "30" } });
    fireEvent.click(screen.getByRole("button", { name: "subscriptions.saveChanges" }));

    expect(mutateMock).toHaveBeenCalledWith({
      userId: "user-1",
      data: { plan_name: "pro", duration_days: 30 },
    });
  });

  it("shows no-changes error when submitting unchanged form", () => {
    mockQueries({
      subscriptionsData: { items: [subscriptionItem], total: 1, page: 1, page_size: 20 },
    });

    render(<SubscriptionManagement />);

    fireEvent.click(screen.getByTitle("subscriptions.modify"));
    fireEvent.click(screen.getByRole("button", { name: "subscriptions.saveChanges" }));

    expect(mutateMock).not.toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalledWith("subscriptions.noChanges");
  });

  it("labels a user without a subscription record as the default free plan with no expiry", () => {
    i18nState.useFallback = true;
    const freeUser = {
      ...subscriptionItem,
      id: "virtual-user-2",
      user_id: "user-2",
      username: "newcomer",
      email: "newcomer@example.com",
      plan_name: "free",
      plan_display_name: "Free",
      plan_display_name_en: "Free",
      current_period_start: null,
      current_period_end: null,
      has_subscription_record: false,
    };
    mockQueries({
      subscriptionsData: { items: [subscriptionItem, freeUser], total: 2, page: 1, page_size: 20 },
    });

    render(<SubscriptionManagement />);

    const freeRow = screen
      .getAllByText("newcomer@example.com")
      .map((node) => node.closest("tr"))
      .find((row): row is HTMLTableRowElement => row !== null);
    expect(freeRow).toBeDefined();
    const freeCells = within(freeRow as HTMLTableRowElement);
    expect(freeCells.getByText("免费版（默认）")).toBeInTheDocument();
    expect(freeCells.getByText("长期（免费版）")).toBeInTheDocument();
    expect(freeCells.getByText("无订阅记录")).toBeInTheDocument();

    // A user with a real record keeps the normal status and date, not the free-plan wording.
    const paidRow = screen
      .getAllByText("writer@example.com")
      .map((node) => node.closest("tr"))
      .find((row): row is HTMLTableRowElement => row !== null);
    const paidCells = within(paidRow as HTMLTableRowElement);
    expect(paidCells.getByText("subscriptions.statusActive")).toBeInTheDocument();
    expect(paidCells.queryByText("免费版（默认）")).not.toBeInTheDocument();
    expect(paidCells.queryByText("长期（免费版）")).not.toBeInTheDocument();

    // The details dialog repeats the same status and expiry for the free user.
    fireEvent.click(within(freeRow as HTMLTableRowElement).getByTitle("subscriptions.viewDetails"));
    expect(screen.getByText("subscriptions.detailsTitle")).toBeInTheDocument();
    // Mobile card + table row + details dialog.
    expect(screen.getAllByText("免费版（默认）")).toHaveLength(3);
    expect(screen.getAllByText("长期（免费版）")).toHaveLength(3);
  });

  it("refuses a plan change without days and only submits once days are entered", () => {
    i18nState.useFallback = true;
    mockQueries({
      subscriptionsData: { items: [subscriptionItem], total: 1, page: 1, page_size: 20 },
      plansData: [
        ...defaultPlan,
        {
          id: "plan-max",
          name: "max",
          display_name: "Max",
          display_name_en: "Max",
          price_monthly_cents: 4999,
          price_yearly_cents: 49999,
          features: {},
          is_active: true,
        },
      ],
    });

    render(<SubscriptionManagement />);

    fireEvent.click(screen.getByTitle("subscriptions.modify"));
    fireEvent.change(screen.getByDisplayValue("Pro"), { target: { value: "max" } });
    expect(screen.getByRole("spinbutton")).toHaveValue(0);
    fireEvent.click(screen.getByRole("button", { name: "subscriptions.saveChanges" }));

    expect(toast.error).toHaveBeenCalledWith("更换套餐时请填写大于 0 的天数");
    expect(mutateMock).not.toHaveBeenCalled();
    // The dialog stays open so the admin can fill in the days.
    expect(screen.getByRole("button", { name: "subscriptions.saveChanges" })).toBeInTheDocument();

    fireEvent.change(screen.getByRole("spinbutton"), { target: { value: "30" } });
    fireEvent.click(screen.getByRole("button", { name: "subscriptions.saveChanges" }));

    expect(toast.error).toHaveBeenCalledTimes(1);
    expect(mutateMock).toHaveBeenCalledWith({
      userId: "user-1",
      data: { plan_name: "max", duration_days: 30 },
    });
  });
});
