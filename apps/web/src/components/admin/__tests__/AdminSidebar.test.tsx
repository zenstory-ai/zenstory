import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AdminSidebar from "../AdminSidebar";

const inspirationFeature = vi.hoisted(() => ({ enabled: true }));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (_key: string, fallback: string) => fallback,
  }),
}));

vi.mock("../../../config/inspirations", () => ({
  inspirationsConfig: inspirationFeature,
}));

describe("AdminSidebar", () => {
  beforeEach(() => {
    inspirationFeature.enabled = true;
  });

  it("shows inspiration management when enabled", () => {
    render(<AdminSidebar />, { wrapper: MemoryRouter });

    expect(screen.getByRole("button", { name: "灵感管理" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "支付订单" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "兑换码管理" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "用量与成本" })).toBeInTheDocument();
  });

  it("hides inspiration management when disabled", () => {
    inspirationFeature.enabled = false;
    render(<AdminSidebar />, { wrapper: MemoryRouter });

    expect(screen.queryByRole("button", { name: "灵感管理" })).not.toBeInTheDocument();
  });

  it("groups pages into labeled sections in task order", () => {
    render(<AdminSidebar />, { wrapper: MemoryRouter });

    const groups = screen.getAllByRole("region");
    expect(groups.map((group) => within(group).getByRole("heading").textContent)).toEqual([
      "概览", "用户与用量", "营收", "增长", "内容与运营", "系统",
    ]);
    const usersGroup = screen.getByRole("region", { name: "用户与用量" });
    expect(within(usersGroup).getAllByRole("button").map((button) => button.textContent)).toEqual([
      "用户管理", "用量与成本",
    ]);
    const revenue = screen.getByRole("region", { name: "营收" });
    expect(within(revenue).getAllByRole("button").map((button) => button.textContent)).toEqual([
      "支付订单", "订阅管理", "订阅计划", "兑换码管理",
    ]);
  });

  it("marks the section of a nested page as current and closes after navigating", () => {
    const onClose = vi.fn();
    render(
      <MemoryRouter initialEntries={["/admin/users/user-1"]}>
        <AdminSidebar onClose={onClose} />
      </MemoryRouter>,
    );

    expect(screen.getByRole("button", { name: "用户管理" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("button", { name: "用量与成本" })).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("button", { name: "仪表盘" })).not.toHaveAttribute("aria-current");

    fireEvent.click(screen.getByRole("button", { name: "用量与成本" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
