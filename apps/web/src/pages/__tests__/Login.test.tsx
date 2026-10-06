import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const {
  mockNavigate,
  mockLogin,
  mockGoogleLogin,
  mockAppleLogin,
  mockGetAllProjects,
  mockOAuthEnabled,
  mockHandleSsoRedirect,
} = vi.hoisted(() => ({
  mockNavigate: vi.fn(),
  mockLogin: vi.fn(),
  mockGoogleLogin: vi.fn(),
  mockAppleLogin: vi.fn(),
  mockGetAllProjects: vi.fn(),
  mockOAuthEnabled: { google: false },
  mockHandleSsoRedirect: vi.fn(),
}));

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown> | string) => {
      if (typeof options === "string") {
        // Inline zh fallback, as shown when the key is missing from the bundle.
        return options;
      }
      if (options && Object.keys(options).length > 0) {
        return `${key}:${JSON.stringify(options)}`;
      }
      return key;
    },
  }),
}));

vi.mock("../../contexts/AuthContext", () => ({
  useAuth: () => ({
    login: mockLogin,
    googleLogin: mockGoogleLogin,
    appleLogin: mockAppleLogin,
    user: null,
    loading: false,
  }),
}));

vi.mock("../../lib/api", () => ({
  projectApi: {
    getAll: mockGetAllProjects,
  },
}));

vi.mock("../../config/auth", () => ({
  authConfig: {
    registrationEnabled: true,
    forgotPasswordEnabled: true,
    oauthProviders: {
      google: { get enabled() { return mockOAuthEnabled.google; } },
      apple: { enabled: false },
    },
  },
  hasOAuthProviders: () => mockOAuthEnabled.google,
}));

vi.mock("../../lib/ssoRedirect", () => ({
  handleSsoRedirect: mockHandleSsoRedirect,
}));

vi.mock("../../components/PublicHeader", () => ({
  PublicHeader: () => <div data-testid="public-header" />,
}));

vi.mock("../../components/LoadingSpinner", () => ({
  LoadingSpinner: () => <div data-testid="loading-spinner" />,
}));

vi.mock("../../components/Logo", () => ({
  LogoMark: () => <div data-testid="logo-mark" />,
}));

import Login from "../Login";
import zhAuth from "../../../public/locales/zh/auth.json";

describe("Login", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockOAuthEnabled.google = false;
    mockGetAllProjects.mockResolvedValue([]);
  });

  const renderPage = () =>
    render(
      <MemoryRouter initialEntries={["/login"]}>
        <Login />
      </MemoryRouter>
    );

  const renderPageAt = (entry: string, state?: unknown) =>
    render(
      <MemoryRouter initialEntries={[state === undefined ? entry : { pathname: entry, state }]}>
        <Login />
      </MemoryRouter>
    );

  it("disables submit until identifier and password are both provided", async () => {
    renderPage();

    const submitButton = screen.getByTestId("login-submit");
    expect(submitButton).toBeDisabled();

    fireEvent.change(screen.getByTestId("email-input"), {
      target: { value: "writer@example.com" },
    });
    expect(submitButton).toBeDisabled();

    fireEvent.change(screen.getByTestId("password-input"), {
      target: { value: "SecurePass123!" },
    });
    expect(submitButton).toBeEnabled();
  });

  it("does not render redundant inline helper text for login method", () => {
    renderPage();
    expect(screen.queryByText(/auth:login.helper/)).not.toBeInTheDocument();
  });

  it("passes normalized plan intent through Google login", () => {
    mockOAuthEnabled.google = true;
    renderPageAt("/login?plan=PRO");
    fireEvent.click(screen.getByRole("button", { name: "auth:login.googleLogin" }));
    expect(mockGoogleLogin).toHaveBeenCalledWith({ planIntent: "pro" });
  });

  it("shows loading spinner and busy state while login request is pending", async () => {
    mockLogin.mockReturnValue(new Promise<void>(() => {}));
    const user = userEvent.setup();
    renderPage();

    fireEvent.change(screen.getByTestId("email-input"), {
      target: { value: "writer@example.com" },
    });
    fireEvent.change(screen.getByTestId("password-input"), {
      target: { value: "SecurePass123!" },
    });

    const submitButton = screen.getByTestId("login-submit");
    await user.click(submitButton);

    await waitFor(() => {
      expect(mockLogin).toHaveBeenCalledWith("writer@example.com", "SecurePass123!");
    });

    expect(submitButton).toBeDisabled();
    expect(screen.getByTestId("loading-spinner")).toBeInTheDocument();
    expect(screen.getByTestId("login-form")).toHaveAttribute("aria-busy", "true");
  });

  it("trims identifier and navigates to dashboard after successful login", async () => {
    const user = userEvent.setup();
    mockLogin.mockResolvedValue(undefined);
    renderPage();

    fireEvent.change(screen.getByTestId("email-input"), {
      target: { value: "  writer@example.com  " },
    });
    fireEvent.change(screen.getByTestId("password-input"), {
      target: { value: "SecurePass123!" },
    });

    await user.click(screen.getByTestId("login-submit"));

    await waitFor(() => {
      expect(mockLogin).toHaveBeenCalledWith("writer@example.com", "SecurePass123!");
    });
    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith("/dashboard");
    });
  });

  it("restores protected deep-link search, hash, and router state after login", async () => {
    const user = userEvent.setup();
    mockLogin.mockResolvedValue(undefined);
    renderPageAt("/login", {
      from: { pathname: "/project/p1", search: "?file=f1", hash: "#selection", state: { source: "guard" } },
    });
    fireEvent.change(screen.getByTestId("email-input"), { target: { value: "writer@example.com" } });
    fireEvent.change(screen.getByTestId("password-input"), { target: { value: "SecurePass123!" } });

    await user.click(screen.getByTestId("login-submit"));

    await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith(
      "/project/p1?file=f1#selection",
      { replace: true, state: { source: "guard" } },
    ));
  });
  it("asks for both credentials when the form is submitted with a whitespace-only account", () => {
    renderPage();

    fireEvent.change(screen.getByTestId("email-input"), { target: { value: "   " } });
    fireEvent.change(screen.getByTestId("password-input"), { target: { value: "SecurePass123!" } });
    expect(screen.getByTestId("login-submit")).toBeDisabled();

    // Enter-key / programmatic submit bypasses the disabled button.
    fireEvent.submit(screen.getByTestId("login-form"));

    expect(screen.getByRole("alert")).toHaveTextContent("请输入账号和密码");
    expect(screen.getByTestId("email-input")).toHaveAttribute("aria-invalid", "true");
    expect(mockLogin).not.toHaveBeenCalled();
    expect(zhAuth.errors.missingCredentials).toBe("请输入账号和密码");

    // Typing again clears the stale validation message.
    fireEvent.change(screen.getByTestId("email-input"), { target: { value: "writer@example.com" } });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  describe("returning to an external app after login", () => {
    beforeEach(() => {
      window.history.pushState({}, "", "/login?redirect=https%3A%2F%2Fmanga.zenstory.ai%2Fcallback");
    });

    afterEach(() => {
      window.history.pushState({}, "", "/");
    });

    it("explains that login worked but the app could not be reopened when the SSO hand-off fails", async () => {
      const user = userEvent.setup();
      mockLogin.mockResolvedValue(undefined);
      mockHandleSsoRedirect.mockResolvedValue({ success: false, error: "Invalid redirect URL" });
      renderPage();

      fireEvent.change(screen.getByTestId("email-input"), { target: { value: "writer@example.com" } });
      fireEvent.change(screen.getByTestId("password-input"), { target: { value: "SecurePass123!" } });
      await user.click(screen.getByTestId("login-submit"));

      expect(await screen.findByRole("alert")).toHaveTextContent(
        "已登录，但没能返回原来的应用，请重新打开该应用再试"
      );
      expect(mockHandleSsoRedirect).toHaveBeenCalledWith("https://manga.zenstory.ai/callback");
      expect(screen.queryByText("Invalid redirect URL")).not.toBeInTheDocument();
      expect(mockNavigate).not.toHaveBeenCalled();
      expect(mockGetAllProjects).not.toHaveBeenCalled();
      expect(screen.getByTestId("login-submit")).toBeEnabled();
      expect(zhAuth.errors.ssoRedirectFailed).toBe("已登录，但没能返回原来的应用，请重新打开该应用再试");
    });
  });
});
