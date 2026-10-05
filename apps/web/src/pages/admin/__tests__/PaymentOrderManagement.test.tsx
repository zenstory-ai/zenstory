import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import PaymentOrderManagement from "../PaymentOrderManagement";
import { adminApi } from "../../../lib/adminApi";

const queryMock = vi.fn();
const refetch = vi.fn();
const invalidateQueries = vi.fn();
const mutation = vi.hoisted(() => ({
  options: null as null | { mutationFn: (id: string) => Promise<unknown>; onSuccess: (r: unknown) => void; onError: (e: unknown) => void },
}));
vi.mock("@tanstack/react-query", () => ({
  useQuery: (...args: unknown[]) => queryMock(...args),
  useQueryClient: () => ({ invalidateQueries }),
  useMutation: (options: NonNullable<typeof mutation.options>) => {
    mutation.options = options;
    return {
      isPending: false,
      mutate: (id: string) => {
        options.mutationFn(id).then(options.onSuccess, options.onError);
      },
    };
  },
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown> | string) =>
      typeof options === "object" && options && "reason" in options ? `${key}:${String(options.reason)}` : key,
    i18n: { language: "en-US" },
  }),
}));
vi.mock("../../../lib/adminApi", () => ({ adminApi: { getPaymentOrders: vi.fn(), syncPaymentOrder: vi.fn() } }));
vi.mock("../../../components/ui/Modal", () => ({
  Modal: ({ open, title, children }: { open: boolean; title: string; children: React.ReactNode }) =>
    open ? <div role="dialog" aria-label={title}>{children}</div> : null,
}));

const order = {
  id: "order-1", out_trade_no: "20261005000000000000000000000001", trade_no: "provider-1",
  user_id: "user-1", username: "writer", email: "writer@example.com",
  plan_name: "pro", plan_display_name: "Pro", cycle: "month", amount_cents: 4900,
  payment_method: "alipay", status: "paid", fulfillment_status: "succeeded",
  created_at: "2026-10-05T00:00:00Z", paid_at: "2026-10-05T00:01:00Z",
  fulfilled_at: "2026-10-05T00:01:00Z", failure_reason: null,
};

describe("PaymentOrderManagement", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    queryMock.mockReturnValue({
      data: { items: [order], total: 21, page: 1, page_size: 20 },
      isLoading: false, isFetching: false, isError: false, refetch,
    });
  });

  it("lists payment and activation separately and opens read-only details", () => {
    render(<PaymentOrderManagement />);
    expect(screen.getByText(order.out_trade_no)).toBeInTheDocument();
    expect(screen.getByText("writer@example.com")).toBeInTheDocument();
    expect(screen.getByText("¥49.00")).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "paymentOrders.status.paid" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "paymentOrders.fulfillment.succeeded" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "paymentOrders.details" }));
    const dialog = within(screen.getByRole("dialog"));
    expect(dialog.getByText("provider-1")).toBeInTheDocument();
    expect(dialog.getByText("paymentOrders.paidAt")).toBeInTheDocument();
    expect(dialog.getByText("paymentOrders.fulfilledAt")).toBeInTheDocument();
    expect(dialog.queryByRole("button", { name: /refund|activate|grant/i })).not.toBeInTheDocument();
    // Fulfilled orders need no compensation.
    expect(dialog.queryByRole("button", { name: "paymentOrders.sync" })).not.toBeInTheDocument();
  });

  it("paginates, submits search explicitly, and resets page when filters change", async () => {
    render(<PaymentOrderManagement />);
    fireEvent.click(screen.getByRole("button", { name: "paymentOrders.nextPage" }));
    expect(queryMock.mock.lastCall?.[0].queryKey).toEqual(["admin", "payment-orders", 2, "", "", "", false]);
    fireEvent.change(screen.getByRole("combobox", { name: "paymentOrders.paymentStatus" }), { target: { value: "paid" } });
    expect(queryMock.mock.lastCall?.[0].queryKey).toEqual(["admin", "payment-orders", 1, "paid", "", "", false]);
    const input = screen.getByRole("textbox", { name: "paymentOrders.search" });
    fireEvent.change(input, { target: { value: "  writer@example.com  " } });
    expect(queryMock.mock.lastCall?.[0].queryKey).toEqual(["admin", "payment-orders", 1, "paid", "", "", false]);
    fireEvent.submit(input.closest("form")!);
    await queryMock.mock.lastCall?.[0].queryFn();
    expect(adminApi.getPaymentOrders).toHaveBeenCalledWith({
      page: 1, page_size: 20, status: "paid", search: "writer@example.com",
      fulfillment_status: undefined, needs_attention: undefined,
    });
  });

  it("shows activation failures in details without claiming activation succeeded", () => {
    queryMock.mockReturnValue({
      data: { items: [{ ...order, status: "pending", fulfillment_status: "failed", failure_reason: "Activation could not be completed" }], total: 1 },
      isLoading: false, isFetching: false, isError: false, refetch,
    });
    render(<PaymentOrderManagement />);
    expect(screen.getByRole("cell", { name: "paymentOrders.fulfillment.failed" })).toBeInTheDocument();
    expect(screen.queryByRole("cell", { name: "paymentOrders.fulfillment.succeeded" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "paymentOrders.details" }));
    expect(screen.getByText("Activation could not be completed")).toBeInTheDocument();
  });

  it.each(["loading", "empty", "error"])("handles the %s state", (state) => {
    queryMock.mockReturnValue({
      data: { items: [], total: 0 }, isLoading: state === "loading",
      isFetching: false, isError: state === "error", refetch,
    });
    render(<PaymentOrderManagement />);
    const message = { loading: "common:loading", empty: "common:noData", error: "paymentOrders.loadError" }[state];
    expect(screen.getByText(message!)).toBeInTheDocument();
    if (state === "error") {
      fireEvent.click(screen.getByRole("button", { name: "common:retry" }));
      expect(refetch).toHaveBeenCalledOnce();
    }
  });

  it("filters by fulfillment status and the paid-but-not-fulfilled quick view", async () => {
    queryMock.mockReturnValue({
      data: { items: [order], total: 1, page: 1, page_size: 20, needs_attention_total: 4 },
      isLoading: false, isFetching: false, isError: false, refetch,
    });
    render(<PaymentOrderManagement />);
    expect(screen.getByText("4")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox", { name: "paymentOrders.fulfillmentStatus" }), { target: { value: "failed" } });
    expect(queryMock.mock.lastCall?.[0].queryKey).toEqual(["admin", "payment-orders", 1, "", "", "failed", false]);
    fireEvent.click(screen.getByRole("button", { name: /paymentOrders.needsAttention/ }));
    expect(queryMock.mock.lastCall?.[0].queryKey).toEqual(["admin", "payment-orders", 1, "", "", "failed", true]);
    await queryMock.mock.lastCall?.[0].queryFn();
    expect(adminApi.getPaymentOrders).toHaveBeenLastCalledWith(expect.objectContaining({
      fulfillment_status: "failed", needs_attention: true,
    }));
  });

  it("queries Zpay for an unfulfilled order and shows the outcome", async () => {
    const stuck = { ...order, fulfillment_status: "failed", failure_reason: "subscription_fulfillment_failed" };
    queryMock.mockReturnValue({
      data: { items: [stuck], total: 1 }, isLoading: false, isFetching: false, isError: false, refetch,
    });
    vi.mocked(adminApi.syncPaymentOrder).mockResolvedValue({
      outcome: "fulfilled", order: { ...stuck, fulfillment_status: "succeeded" },
    } as never);
    render(<PaymentOrderManagement />);
    fireEvent.click(screen.getByRole("button", { name: "paymentOrders.details" }));
    fireEvent.click(screen.getByRole("button", { name: "paymentOrders.sync" }));
    expect(await screen.findByText("paymentOrders.syncOutcome.fulfilled")).toBeInTheDocument();
    expect(adminApi.syncPaymentOrder).toHaveBeenCalledWith("order-1");
    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["admin", "payment-orders"] });
  });

  it("reports why a sync failed", async () => {
    const { ApiError } = await import("../../../lib/apiClient");
    queryMock.mockReturnValue({
      data: { items: [{ ...order, status: "pending", fulfillment_status: "pending" }], total: 1 },
      isLoading: false, isFetching: false, isError: false, refetch,
    });
    vi.mocked(adminApi.syncPaymentOrder).mockRejectedValue(new ApiError(502, "sync_failed:provider_unavailable"));
    render(<PaymentOrderManagement />);
    fireEvent.click(screen.getByRole("button", { name: "paymentOrders.details" }));
    fireEvent.click(screen.getByRole("button", { name: "paymentOrders.sync" }));
    expect(await screen.findByText("paymentOrders.syncFailed:provider_unavailable")).toBeInTheDocument();
  });

  it("renders a hand-edited order status without crashing", () => {
    queryMock.mockReturnValue({
      data: { items: [{ ...order, status: "refunded", cycle: "quarter", payment_method: "wxpay" }], total: 1 },
      isLoading: false, isFetching: false, isError: false, refetch,
    });
    render(<PaymentOrderManagement />);
    expect(screen.getByText("paymentOrders.status.refunded")).toBeInTheDocument();
    expect(screen.getByText("wxpay")).toBeInTheDocument();
  });
});
