import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockNavigate, mockLogout } = vi.hoisted(() => ({
  mockNavigate: vi.fn(),
  mockLogout: vi.fn(),
}));

let mockIsMobile = false;
let mockUser: { username: string; email: string } | null = null;

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string) => {
      const map: Record<string, string> = {
        "common:productMenu.title": "产品",
        "common:productMenu.aiStory": "AI写作",
        "common:nav.docs": "文档",
        "common:nav.status": "服务状态",
        "home:nav.pricing": "订阅",
        "home:nav.login": "登录",
        "home:nav.getStarted": "开始免费试用",
        "home:nav.goDashboard": "进入工作台",
        "dashboard:nav.logout": "退出登录",
        "common:nav.openMenu": "打开导航菜单",
        "common:nav.closeMenu": "关闭导航菜单",
        "common:nav.mobileMenu": "移动端导航菜单",
      };
      return map[key] ?? fallback ?? key;
    },
  }),
}));

vi.mock("../../contexts/AuthContext", () => ({
  useAuth: () => ({
    user: mockUser,
    logout: mockLogout,
  }),
}));

vi.mock("../../hooks/useMediaQuery", () => ({
  useIsDesktop: () => !mockIsMobile,
}));

vi.mock("../Logo", () => ({
  Logo: () => <div data-testid="logo">Logo</div>,
}));

vi.mock("../LanguageSwitcher", () => ({
  LanguageSwitcher: () => <div data-testid="language-switcher">LanguageSwitcher</div>,
}));

import { PublicHeader } from "../PublicHeader";

const renderHeader = ({
  variant = "home",
  initialEntries = ["/"],
}: {
  variant?: "home" | "dashboard" | "auth";
  initialEntries?: string[];
} = {}) =>
  render(
    <MemoryRouter initialEntries={initialEntries}>
      <PublicHeader variant={variant} />
    </MemoryRouter>
  );

describe("PublicHeader", () => {
  it("exposes the logo as a home link in local development", () => {
    renderHeader();
    expect(screen.getByRole("link", { name: "返回首页" })).toHaveAttribute("href", "/");
  });

  beforeEach(() => {
    vi.clearAllMocks();
    mockIsMobile = false;
    mockUser = null;
    mockLogout.mockResolvedValue(undefined);
    localStorage.clear();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("keeps primary home navigation focused on docs, pricing and auth", () => {
    renderHeader({ variant: "home" });

    expect(screen.getByRole("link", { name: "文档" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "订阅" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "服务状态" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "登录" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始免费试用" })).toBeInTheDocument();
  });

  it("hides docs, pricing and status links on auth variant", () => {
    renderHeader({ variant: "auth" });

    expect(screen.queryByRole("link", { name: "文档" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "订阅" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "服务状态" })).not.toBeInTheDocument();
  });

  it("opens product menu and navigates to AI writing entry", () => {
    renderHeader({ variant: "home" });

    fireEvent.click(screen.getByRole("button", { name: /产品/ }));

    const aiStoryButton = screen.getByRole("menuitem", { name: "AI写作" });
    expect(aiStoryButton).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: /实验室|Lab/i })).not.toBeInTheDocument();

    fireEvent.click(aiStoryButton);
    expect(mockNavigate).toHaveBeenCalledWith("/dashboard");
  });

  it("does not render AI manga entry in product menu before feature readiness", () => {
    renderHeader({ variant: "home" });

    fireEvent.click(screen.getByRole("button", { name: /产品/ }));
    expect(screen.queryByRole("menuitem", { name: "AI漫剧" })).not.toBeInTheDocument();
  });

  it("supports keyboard open/close for product menu", async () => {
    renderHeader({ variant: "home" });

    const productTrigger = screen.getByRole("button", { name: /产品/ });
    productTrigger.focus();
    fireEvent.keyDown(productTrigger, { key: "ArrowDown" });

    const firstMenuItem = await screen.findByRole("menuitem", { name: "AI写作" });
    await waitFor(() => {
      expect(firstMenuItem).toHaveFocus();
    });

    fireEvent.keyDown(document, { key: "Escape" });

    await waitFor(() => {
      expect(screen.queryByRole("menu", { name: "产品" })).not.toBeInTheDocument();
      expect(productTrigger).toHaveFocus();
    });
  });

  it("supports dashboard mobile menu logout flow", async () => {
    mockIsMobile = true;
    mockUser = { username: "demo", email: "demo@example.com" };
    renderHeader({ variant: "dashboard" });

    fireEvent.click(screen.getByRole("button", { name: "打开导航菜单" }));
    fireEvent.click(screen.getByText("退出登录"));

    await waitFor(() => {
      expect(mockLogout).toHaveBeenCalled();
      expect(mockNavigate).toHaveBeenCalledWith("/");
    });
  });

  it("supports escape-to-close for home mobile menu", async () => {
    mockIsMobile = true;
    renderHeader({ variant: "home" });

    const menuToggle = screen.getByRole("button", { name: "打开导航菜单" });
    fireEvent.click(menuToggle);

    expect(screen.getByRole("menu", { name: "移动端导航菜单" })).toBeInTheDocument();

    fireEvent.keyDown(document, { key: "Escape" });

    await waitFor(() => {
      expect(screen.queryByRole("menu", { name: "移动端导航菜单" })).not.toBeInTheDocument();
      expect(menuToggle).toHaveFocus();
    });
  });

  it("adds source attribution and keeps plan query for desktop home navigation actions", () => {
    renderHeader({ variant: "home", initialEntries: ["/?plan=pro"] });

    expect(screen.getByRole("link", { name: "订阅" })).toHaveAttribute(
      "href",
      "/pricing?plan=pro&source=header_nav_pricing"
    );

    fireEvent.click(screen.getByRole("button", { name: "登录" }));
    expect(mockNavigate).toHaveBeenLastCalledWith("/login?plan=pro&source=header_nav_login");

    fireEvent.click(screen.getByRole("button", { name: "开始免费试用" }));
    expect(mockNavigate).toHaveBeenLastCalledWith("/register?plan=pro&source=header_nav_get_started");
  });

  it("adds source attribution and keeps plan query for mobile home menu actions", () => {
    mockIsMobile = true;
    renderHeader({ variant: "home", initialEntries: ["/?plan=team"] });

    fireEvent.click(screen.getByRole("button", { name: "打开导航菜单" }));

    const mobileMenu = screen.getByRole("menu", { name: "移动端导航菜单" });
    expect(within(mobileMenu).queryByRole("menuitem", { name: "服务状态" })).not.toBeInTheDocument();
    expect(within(mobileMenu).getByRole("menuitem", { name: "订阅" })).toHaveAttribute(
      "href",
      "/pricing?plan=team&source=header_nav_pricing"
    );

    fireEvent.click(within(mobileMenu).getByRole("menuitem", { name: "登录" }));
    expect(mockNavigate).toHaveBeenLastCalledWith("/login?plan=team&source=header_nav_login");

    fireEvent.click(screen.getByRole("button", { name: "打开导航菜单" }));
    const reopenedMenu = screen.getByRole("menu", { name: "移动端导航菜单" });
    fireEvent.click(within(reopenedMenu).getByRole("menuitem", { name: "开始免费试用" }));
    expect(mockNavigate).toHaveBeenLastCalledWith("/register?plan=team&source=header_nav_get_started");
  });
});
