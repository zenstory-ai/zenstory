import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../apiClient";
import { adminApi } from "../adminApi";

vi.mock("../apiClient", () => ({ api: { get: vi.fn(), post: vi.fn() } }));

describe("admin payment orders API", () => {
  beforeEach(() => vi.clearAllMocks());

  it("sends encoded pagination, status and search filters without credentials", async () => {
    const result = { items: [], total: 0, page: 2, page_size: 20 };
    vi.mocked(api.get).mockResolvedValue(result);
    expect(await adminApi.getPaymentOrders({ page: 2, page_size: 20, status: "paid", search: "user+name@example.com" })).toBe(result);
    expect(api.get).toHaveBeenCalledWith("/api/admin/payment-orders?page=2&page_size=20&status=paid&search=user%2Bname%40example.com");
  });

  it("omits absent filters", async () => {
    await adminApi.getPaymentOrders();
    expect(api.get).toHaveBeenCalledWith("/api/admin/payment-orders?");
  });

  it("sends fulfillment and needs-attention filters", async () => {
    await adminApi.getPaymentOrders({ fulfillment_status: "failed", needs_attention: true });
    expect(api.get).toHaveBeenCalledWith("/api/admin/payment-orders?fulfillment_status=failed&needs_attention=true");
  });

  it("posts an order sync by internal id", async () => {
    await adminApi.syncPaymentOrder("order/1");
    expect(api.post).toHaveBeenCalledWith("/api/admin/payment-orders/order%2F1/sync");
  });
});
