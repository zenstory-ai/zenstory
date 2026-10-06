import { render, screen } from "@testing-library/react";
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
});
