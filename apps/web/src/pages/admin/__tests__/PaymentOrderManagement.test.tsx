import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import PaymentOrderManagement from "../PaymentOrderManagement";
import { adminApi } from "../../../lib/adminApi";

const queryMock = vi.fn();
const refetch = vi.fn();
vi.mock("@tanstack/react-query", () => ({ useQuery: (...args: unknown[]) => queryMock(...args) }));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: "en-US" } }),
}));
vi.mock("../../../lib/adminApi", () => ({ adminApi: { getPaymentOrders: vi.fn() } }));
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
    expect(screen.getByText("paymentOrders.fulfillment.succeeded")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "paymentOrders.details" }));
    const dialog = within(screen.getByRole("dialog"));
    expect(dialog.getByText("provider-1")).toBeInTheDocument();
    expect(dialog.getByText("paymentOrders.paidAt")).toBeInTheDocument();
    expect(dialog.getByText("paymentOrders.fulfilledAt")).toBeInTheDocument();
    expect(dialog.queryByRole("button", { name: /refund|activate|grant/i })).not.toBeInTheDocument();
  });

  it("paginates, submits search explicitly, and resets page when filters change", async () => {
    render(<PaymentOrderManagement />);
    fireEvent.click(screen.getByRole("button", { name: "paymentOrders.nextPage" }));
    expect(queryMock.mock.lastCall?.[0].queryKey).toEqual(["admin", "payment-orders", 2, "", ""]);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "paid" } });
    expect(queryMock.mock.lastCall?.[0].queryKey).toEqual(["admin", "payment-orders", 1, "paid", ""]);
    const input = screen.getByRole("textbox", { name: "paymentOrders.search" });
    fireEvent.change(input, { target: { value: "  writer@example.com  " } });
    expect(queryMock.mock.lastCall?.[0].queryKey).toEqual(["admin", "payment-orders", 1, "paid", ""]);
    fireEvent.submit(input.closest("form")!);
    await queryMock.mock.lastCall?.[0].queryFn();
    expect(adminApi.getPaymentOrders).toHaveBeenCalledWith({
      page: 1, page_size: 20, status: "paid", search: "writer@example.com",
    });
  });

  it("shows activation failures in details without claiming activation succeeded", () => {
    queryMock.mockReturnValue({
      data: { items: [{ ...order, status: "pending", fulfillment_status: "failed", failure_reason: "Activation could not be completed" }], total: 1 },
      isLoading: false, isFetching: false, isError: false, refetch,
    });
    render(<PaymentOrderManagement />);
    expect(screen.getByText("paymentOrders.fulfillment.failed")).toBeInTheDocument();
    expect(screen.queryByText("paymentOrders.fulfillment.succeeded")).not.toBeInTheDocument();
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
});
