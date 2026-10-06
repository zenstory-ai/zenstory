import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import AdminUserDetail from "../AdminUserDetail";
import { adminApi } from "../../../lib/adminApi";
import { ApiError } from "../../../lib/apiClient";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { language: "zh-CN" },
  }),
}));

vi.mock("../../../lib/toast", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

vi.mock("../../../lib/adminApi", () => ({
  adminApi: {
    getUser: vi.fn(),
    getUserSubscription: vi.fn(),
    getPlans: vi.fn(),
    getUserQuota: vi.fn(),
    getUserPoints: vi.fn(),
    getUserPointsTransactions: vi.fn(),
    getPaymentOrders: vi.fn(),
    updateUserSubscription: vi.fn(),
    adjustUserPoints: vi.fn(),
  },
}));

const api = vi.mocked(adminApi);
const counter = (used: number, limit: number) => ({ used, limit, reset_at: null });

const renderPage = () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/admin/users/user-1"]}>
        <Routes>
          <Route path="/admin/users/:userId" element={<AdminUserDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

describe("AdminUserDetail", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getUser.mockResolvedValue({
      id: "user-1", username: "writer", email: "writer@example.com", email_verified: true,
      is_active: true, is_superuser: false, created_at: "2026-10-01T08:00:00", updated_at: "2026-10-01T08:00:00",
    } as never);
    api.getUserSubscription.mockResolvedValue({
      subscription: { user_id: "user-1", status: "active", current_period_end: "2099-01-01T00:00:00" },
      plan: { name: "pro", display_name: "专业版", display_name_en: "Pro" },
      quota: null,
    } as never);
    api.getPlans.mockResolvedValue([
      { name: "free", display_name: "免费版", is_active: true },
      { name: "pro", display_name: "专业版", is_active: true },
    ] as never);
    api.getUserQuota.mockResolvedValue({
      user_id: "user-1", username: "writer", email: "writer@example.com", plan_name: "pro",
      plan_display_name: "专业版", plan_display_name_en: "Pro",
      ai_conversations: counter(3, -1), material_decompositions: counter(1, 5),
      inspiration_copies: counter(0, 100), custom_skills: counter(2, 20),
    });
    api.getUserPoints.mockResolvedValue({
      user_id: "user-1", username: "writer", email: "writer@example.com",
      available: 120, pending_expiration: 0, total_earned: 150, total_spent: 30,
    });
    api.getUserPointsTransactions.mockResolvedValue({
      items: [{
        id: "tx-1", user_id: "user-1", username: "writer", amount: 10, balance_after: 120,
        transaction_type: "check_in", source_id: null, description: null, expires_at: null,
        is_expired: false, created_at: "2026-10-02T00:00:00",
      }],
      total: 1, page: 1, page_size: 10,
    });
    api.getPaymentOrders.mockResolvedValue({
      items: [{
        id: "order-1", out_trade_no: "20261006000001", user_id: "user-1", plan_display_name: "专业版",
        amount_cents: 2900, status: "paid", fulfillment_status: "succeeded", created_at: "2026-10-03T00:00:00",
      }],
      total: 1, page: 1, page_size: 10,
    } as never);
    api.updateUserSubscription.mockResolvedValue({ success: true });
    api.adjustUserPoints.mockResolvedValue({ message: "ok", new_balance: 170 });
  });

  it("shows account, membership, quota, points and payments for one user", async () => {
    renderPage();

    expect(await screen.findByRole("heading", { name: "writer" })).toBeInTheDocument();
    expect(screen.getByText("writer@example.com")).toBeInTheDocument();
    expect(await screen.findByText("1 / 5")).toBeInTheDocument();
    expect(await screen.findByText("120")).toBeInTheDocument();
    expect(await screen.findByText("20261006000001")).toBeInTheDocument();
    expect(screen.getByText("points.types.check_in")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "userDetail.viewUsage" })).toHaveAttribute(
      "href", "/admin/usage?user=user-1",
    );
    expect(api.getPaymentOrders).toHaveBeenCalledWith({ user_id: "user-1", page: 1, page_size: 10 });
    const membership = screen.getByRole("region", { name: "userDetail.subscription" });
    expect(within(membership).getByText("subscriptions.statusActive")).toBeInTheDocument();
  });

  it("treats a missing subscription row as the default plan and changes the plan", async () => {
    api.getUserSubscription.mockRejectedValue(new ApiError(404, "ERR_NOT_FOUND"));
    renderPage();

    expect(await screen.findByText("subscriptions.statusUninitialized")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "subscriptions.modify" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getAllByRole("combobox")[0], { target: { value: "pro" } });
    fireEvent.change(within(dialog).getByRole("spinbutton"), { target: { value: "30" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "subscriptions.saveChanges" }));

    await waitFor(() =>
      expect(api.updateUserSubscription).toHaveBeenCalledWith("user-1", { plan_name: "pro", duration_days: 30 }),
    );
  });

  it("refuses a plan change without days", async () => {
    const { toast } = await import("../../../lib/toast");
    renderPage();
    await screen.findByText("subscriptions.statusActive");

    fireEvent.click(screen.getByRole("button", { name: "subscriptions.modify" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getAllByRole("combobox")[0], { target: { value: "free" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "subscriptions.saveChanges" }));

    expect(toast.error).toHaveBeenCalledWith("subscriptions.extendDurationRequired");
    expect(api.updateUserSubscription).not.toHaveBeenCalled();
  });

  it("adjusts points with a reason", async () => {
    renderPage();
    await screen.findByText("120");

    const submit = screen.getByRole("button", { name: "points.adjustPoints" });
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByLabelText("points.adjustAmount"), { target: { value: "50" } });
    fireEvent.change(screen.getByLabelText("points.adjustReason"), { target: { value: " bonus " } });
    fireEvent.click(submit);

    await waitFor(() => expect(api.adjustUserPoints).toHaveBeenCalledWith("user-1", { amount: 50, reason: "bonus" }));
  });

  it("says so when the user does not exist", async () => {
    api.getUser.mockRejectedValue(new ApiError(404, "ERR_NOT_FOUND"));
    renderPage();

    expect(await screen.findByText("userDetail.notFound")).toBeInTheDocument();
  });
});
