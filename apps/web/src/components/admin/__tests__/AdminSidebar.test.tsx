import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import AdminSidebar from "../AdminSidebar";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (_key: string, fallback: string) => fallback,
  }),
}));

describe("AdminSidebar", () => {
  it("renders the 6 flat top-level items without group headers or redundant desktop header", () => {
    render(<AdminSidebar />, { wrapper: MemoryRouter });

    expect(screen.queryByRole("heading")).not.toBeInTheDocument();
    expect(screen.queryAllByRole("region")).toHaveLength(0);

    const links = screen.getAllByRole("link");
    expect(links.map((link) => link.textContent)).toEqual([
      "概览",
      "用户",
      "用量与成本",
      "营收",
      "运营",
      "系统",
    ]);
    expect(links.map((link) => link.getAttribute("href"))).toEqual([
      "/admin",
      "/admin/users",
      "/admin/usage",
      "/admin/payment-orders",
      "/admin/feedback",
      "/admin/prompts",
    ]);
  });

  it("renders an accessible title and close control in the mobile drawer", () => {
    const onClose = vi.fn();
    render(
      <MemoryRouter initialEntries={["/admin"]}>
        <AdminSidebar onClose={onClose} />
      </MemoryRouter>,
    );

    expect(screen.getByRole("heading", { name: "管理后台" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "关闭管理菜单" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "返回应用" })).toHaveAttribute("href", "/dashboard");
  });

  it.each([
    ["/admin", "概览"],
    ["/admin/", "概览"],
    ["/admin/ux-health", "概览"],
    ["/admin/acquisition", "概览"],
    ["/admin/users", "用户"],
    ["/admin/users/user-1", "用户"],
    ["/admin/usage", "用量与成本"],
    ["/admin/payment-orders", "营收"],
    ["/admin/subscriptions", "营收"],
    ["/admin/codes", "营收"],
    ["/admin/plans", "营收"],
    ["/admin/feedback", "运营"],
    ["/admin/skills", "运营"],
    ["/admin/announcements", "运营"],
    ["/admin/prompts", "系统"],
    ["/admin/prompts/novel", "系统"],
    ["/admin/audit-logs", "系统"],
  ])("highlights %s under %s", (route, activeLabel) => {
    render(
      <MemoryRouter initialEntries={[route]}>
        <AdminSidebar />
      </MemoryRouter>,
    );

    for (const label of ["概览", "用户", "用量与成本", "营收", "运营", "系统"]) {
      const link = screen.getByRole("link", { name: label });
      if (label === activeLabel) {
        expect(link).toHaveAttribute("aria-current", "page");
      } else {
        expect(link).not.toHaveAttribute("aria-current");
      }
    }
  });

  it("closes the mobile drawer after navigating", () => {
    const onClose = vi.fn();
    render(
      <MemoryRouter initialEntries={["/admin/users/user-1"]}>
        <AdminSidebar onClose={onClose} />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("link", { name: "用量与成本" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
