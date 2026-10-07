import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import AuditLogPage from "../AuditLogPage";

const useQueryMock = vi.fn();

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: { defaultValue?: string }) =>
      ({
        "auditLogs.actionLabels.update_subscription": "修改订阅",
        "auditLogs.resourceLabels.payment_order": "支付订单",
      } as Record<string, string>)[key] ?? options?.defaultValue ?? key,
  }),
}));

vi.mock("@tanstack/react-query", () => ({
  useQuery: (...args: unknown[]) => useQueryMock(...args),
}));

describe("AuditLogPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows loading state", () => {
    useQueryMock.mockReturnValue({
      data: undefined,
      isLoading: true,
      isFetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<AuditLogPage />, { wrapper: MemoryRouter });
    expect(screen.getByText("common:loading")).toBeInTheDocument();
  });

  it("shows error state and supports retry", () => {
    const refetchMock = vi.fn();
    useQueryMock.mockReturnValue({
      data: undefined,
      isLoading: false,
      isFetching: false,
      isError: true,
      error: new Error("load audit logs failed"),
      refetch: refetchMock,
    });

    render(<AuditLogPage />, { wrapper: MemoryRouter });
    expect(screen.getByText("load audit logs failed")).toBeInTheDocument();

    fireEvent.click(screen.getByText("common:retry"));
    expect(refetchMock).toHaveBeenCalledTimes(1);
  });

  it("shows empty state", () => {
    useQueryMock.mockReturnValue({
      data: { items: [], total: 0, page: 1, page_size: 20 },
      isLoading: false,
      isFetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<AuditLogPage />, { wrapper: MemoryRouter });
    expect(screen.getByText("common:noData")).toBeInTheDocument();
  });

  it("uses the exact total rather than offering a nonexistent next page", () => {
    const items = Array.from({ length: 20 }, (_, index) => ({
      id: `log-${index}`, admin_id: "admin-1", admin_name: "review_admin",
      action: "update_user", resource_type: "user", resource_id: `user-${index}`,
      old_value: null, new_value: null, ip_address: null, user_agent: null,
      created_at: "2026-10-04T00:00:00Z",
    }));
    useQueryMock.mockReturnValue({ data: { items, total: 20, page: 1, page_size: 20 }, isLoading: false, isFetching: false, isError: false, error: null, refetch: vi.fn() });
    render(<AuditLogPage />, { wrapper: MemoryRouter });
    expect(screen.getAllByText("review_admin").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "common:next" })).toBeDisabled();
  });

  it("opens and closes detail modal for a log row", () => {
    useQueryMock.mockReturnValue({
      data: {
        items: [
          {
            id: "log-1",
            admin_id: "admin-1",
            admin_name: "admin_user",
            action: "update_subscription",
            resource_type: "subscription",
            resource_id: "sub-1",
            details: "changed plan",
            old_value: null,
            new_value: null,
            ip_address: "127.0.0.1",
            user_agent: "Playwright",
            created_at: "2026-03-08T00:00:00Z",
          },
        ],
        total: 1,
        page: 1,
        page_size: 20,
      },
      isLoading: false,
      isFetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<AuditLogPage />, { wrapper: MemoryRouter });

    expect(screen.getAllByText("admin_user").length).toBeGreaterThan(0);
    fireEvent.click(screen.getAllByText("auditLogs.viewDetails")[0]);

    expect(screen.getByText("auditLogs.detailTitle")).toBeInTheDocument();
    expect(screen.getByText("127.0.0.1")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "common:close" }));
    expect(screen.queryByText("auditLogs.detailTitle")).not.toBeInTheDocument();
  });

  it("labels every action and links user resources", () => {
    useQueryMock.mockReturnValue({
      data: {
        items: [
          {
            id: "log-1", admin_id: "admin-1", admin_name: "ops", action: "update_subscription",
            resource_type: "subscription", resource_id: "user-9", old_value: null, new_value: null,
            ip_address: null, user_agent: null, created_at: "2026-03-08T00:00:00",
          },
          {
            id: "log-2", admin_id: "admin-1", admin_name: "ops", action: "brand_new_action",
            resource_type: "payment_order", resource_id: "order-1", old_value: null, new_value: null,
            ip_address: null, user_agent: null, created_at: "2026-03-08T00:00:00",
          },
        ],
        total: 2, page: 1, page_size: 20,
      },
      isLoading: false, isFetching: false, isError: false, error: null, refetch: vi.fn(),
    });

    render(<AuditLogPage />, { wrapper: MemoryRouter });

    expect(screen.getAllByText("修改订阅").length).toBeGreaterThan(0);
    expect(screen.queryByText("update_subscription")).not.toBeInTheDocument();
    expect(screen.getAllByText("Brand new action").length).toBeGreaterThan(0);
    expect(screen.getAllByText("支付订单").length).toBeGreaterThan(0);
    screen.getAllByRole("link", { name: "user-9" }).forEach((link) =>
      expect(link).toHaveAttribute("href", "/admin/users/user-9"),
    );
    expect(screen.queryByRole("link", { name: "order-1" })).not.toBeInTheDocument();
  });

  it("offers every logged resource type and narrows actions to it", () => {
    useQueryMock.mockReturnValue({
      data: { items: [], total: 0, page: 1, page_size: 20 },
      isLoading: false, isFetching: false, isError: false, error: null, refetch: vi.fn(),
    });

    render(<AuditLogPage />, { wrapper: MemoryRouter });

    const [resourceSelect, actionSelect] = screen.getAllByRole("combobox");
    const resourceValues = Array.from((resourceSelect as HTMLSelectElement).options).map((o) => o.value);
    expect(resourceValues).toEqual(expect.arrayContaining([
      "user", "code", "subscription", "inspiration", "plan", "points", "skill",
      "invite_code", "feedback", "system_prompt", "payment_order",
    ]));

    fireEvent.change(actionSelect, { target: { value: "adjust_points" } });
    fireEvent.change(resourceSelect, { target: { value: "skill" } });
    const actionValues = Array.from((actionSelect as HTMLSelectElement).options).map((o) => o.value);
    expect(actionValues).toEqual(["", "approve_skill", "reject_skill", "unpublish_skill"]);
    expect((actionSelect as HTMLSelectElement).value).toBe("");
  });
});
