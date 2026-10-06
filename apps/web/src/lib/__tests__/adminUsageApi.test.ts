import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../apiClient";
import { adminApi } from "../adminApi";
import { formatCny } from "../formatCny";

vi.mock("../apiClient", () => ({ api: { get: vi.fn() } }));

describe("admin usage API", () => {
  beforeEach(() => vi.clearAllMocks());

  it("requests the summary for a window", async () => {
    const summary = { window: "7d" };
    vi.mocked(api.get).mockResolvedValue(summary);
    expect(await adminApi.getUsageSummary("7d")).toBe(summary);
    expect(api.get).toHaveBeenCalledWith("/api/admin/usage/summary?window=7d");
  });

  it("sends sort, encoded search and paging for the user list", async () => {
    await adminApi.getUsageByUser({ window: "today", sort: "calls", search: "a+b@example.com", page: 2, page_size: 20 });
    expect(api.get).toHaveBeenCalledWith(
      "/api/admin/usage/users?window=today&sort=calls&search=a%2Bb%40example.com&page=2&page_size=20",
    );
  });

  it("omits absent user-list filters", async () => {
    await adminApi.getUsageByUser({ window: "yesterday" });
    expect(api.get).toHaveBeenCalledWith("/api/admin/usage/users?window=yesterday");
  });

  it("requests one user's daily usage with an encoded id", async () => {
    await adminApi.getUserDailyUsage("user/1", 30);
    expect(api.get).toHaveBeenCalledWith("/api/admin/usage/users/user%2F1/daily?days=30");
    await adminApi.getUserDailyUsage("u2");
    expect(api.get).toHaveBeenLastCalledWith("/api/admin/usage/users/u2/daily?days=7");
  });
});

describe("formatCny", () => {
  it("keeps 4 decimals below one yuan and 2 from one yuan up", () => {
    expect(formatCny("0.0440", "en-US")).toBe("¥0.044");
    expect(formatCny("0.0001", "en-US")).toBe("¥0.0001");
    expect(formatCny("0.0000", "en-US")).toBe("¥0.00");
    expect(formatCny("12.3456", "en-US")).toBe("¥12.35");
    expect(formatCny(1234.5, "en-US")).toBe("¥1,234.50");
    expect(formatCny("not a number", "en-US")).toBe("¥0.00");
  });
});
