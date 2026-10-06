import { StrictMode } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockNavigate = vi.fn();
const mockHandleOAuthCallback = vi.fn();
const mockCaptureException = vi.fn();
const mockUseAuth = vi.fn();

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) =>
      (
        {
          "auth:errors.oauthFailed": "Sign-in did not complete",
          "auth:login.loading": "Loading",
          "auth:login.title": "Login",
          "auth:login.verifying": "Verifying identity...",
          "auth:login.failed": "Login failed",
          "auth:login.redirecting": "Redirecting...",
          "auth:login.oauthLoading": "Completing Google login, please wait...",
          "auth:login.backToLogin": "Back to login",
          "auth:errors.oauthErrorHint": "OAuth hint",
        } as Record<string, string>
      )[key] ?? key,
  }),
}));

vi.mock("../../contexts/AuthContext", () => ({
  useAuth: () => mockUseAuth(),
}));

vi.mock("../../components/PublicHeader", () => ({
  PublicHeader: () => <div>PublicHeader</div>,
}));

vi.mock("../../components/LoadingSpinner", () => ({
  LoadingSpinner: ({ label }: { label?: string }) => <div>{label ?? "Loading"}</div>,
}));

vi.mock("../../components/Logo", () => ({
  LogoMark: () => <div>Logo</div>,
}));

vi.mock("../../lib/ssoRedirect", () => ({
  isValidRedirectUrl: () => true,
}));

vi.mock("../../lib/analytics", () => ({
  captureException: (...args: unknown[]) => mockCaptureException(...args),
}));

vi.mock("../../lib/errorHandler", () => ({
  toUserErrorMessage: (message: string) =>
    message === "ERR_AUTH_TOKEN_INVALID" ? "Invalid authentication token" : message,
}));

import OAuthCallback from "../OAuthCallback";

describe("OAuthCallback", () => {
  const originalLocation = window.location;
  const replaceStateSpy = vi.spyOn(window.history, "replaceState");

  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    sessionStorage.clear();
    mockUseAuth.mockReturnValue({
      handleOAuthCallback: mockHandleOAuthCallback,
      user: null,
    });

    Object.defineProperty(window, "location", {
      configurable: true,
      value: {
        ...originalLocation,
        pathname: "/auth/callback",
        search: "",
        hash: "",
        href: "http://localhost:5173/auth/callback",
      },
    });
  });

  it("handles token callback from hash and redirects to dashboard", async () => {
    mockHandleOAuthCallback.mockResolvedValue(undefined);
    Object.defineProperty(window, "location", {
      configurable: true,
      value: {
        ...originalLocation,
        pathname: "/auth/callback",
        search: "",
        hash: "#access_token=access&refresh_token=refresh",
        href: "http://localhost:5173/auth/callback#access_token=access&refresh_token=refresh",
      },
    });

    render(
      <MemoryRouter>
        <OAuthCallback />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(mockHandleOAuthCallback).toHaveBeenCalledWith("access", "refresh", { isNewUser: false });
      expect(mockNavigate).toHaveBeenCalledWith("/dashboard", { replace: true });
    });
    expect(replaceStateSpy).toHaveBeenCalled();
    expect(mockCaptureException).not.toHaveBeenCalled();
  });

  it("passes the new-user flag from the callback to the auth context", async () => {
    mockHandleOAuthCallback.mockResolvedValue(undefined);
    Object.assign(window.location, { hash: "#access_token=access&refresh_token=refresh&new_user=1" });

    render(
      <MemoryRouter>
        <OAuthCallback />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(mockHandleOAuthCallback).toHaveBeenCalledWith("access", "refresh", { isNewUser: true });
    });
  });

  it("uses credentials captured at boot after the URL was already scrubbed", async () => {
    mockHandleOAuthCallback.mockResolvedValue(undefined);
    const { captureAuthCallbackParams } = await import("../../lib/authCallbackParams");
    const realLocation = new URL("http://localhost:5173/auth/callback#access_token=boot-access&refresh_token=boot-refresh");
    Object.assign(window.location, {
      pathname: realLocation.pathname,
      search: "",
      hash: realLocation.hash,
    });
    captureAuthCallbackParams();
    // What the page sees once boot capture has replaced the URL.
    Object.assign(window.location, { hash: "" });

    render(
      <MemoryRouter>
        <OAuthCallback />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(mockHandleOAuthCallback).toHaveBeenCalledWith("boot-access", "boot-refresh", { isNewUser: false });
      expect(mockNavigate).toHaveBeenCalledWith("/dashboard", { replace: true });
    });
  });

  it("consumes normalized OAuth plan intent once and redirects to billing", async () => {
    mockHandleOAuthCallback.mockResolvedValue(undefined);
    sessionStorage.setItem("oauth_plan_intent", "pro");
    Object.assign(window.location, { hash: "#access_token=access&refresh_token=refresh" });

    render(<MemoryRouter><OAuthCallback /></MemoryRouter>);

    await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith(
      "/dashboard/billing?plan=pro", { replace: true },
    ));
    expect(sessionStorage.getItem("oauth_plan_intent")).toBeNull();
  });

  it("redirects silently to dashboard when callback tokens are missing but auth context already has a user", async () => {
    mockUseAuth.mockReturnValue({
      handleOAuthCallback: mockHandleOAuthCallback,
      user: { id: "user-1" },
    });

    render(
      <MemoryRouter>
        <OAuthCallback />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith("/dashboard", { replace: true });
    });
    expect(mockHandleOAuthCallback).not.toHaveBeenCalled();
    expect(mockCaptureException).not.toHaveBeenCalled();
    expect(screen.queryByText("Missing tokens in callback")).not.toBeInTheDocument();
  });

  it("redirects back to login when callback is opened without OAuth params or cached session", async () => {
    render(
      <MemoryRouter>
        <OAuthCallback />
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith("/login", { replace: true });
    });
    expect(mockHandleOAuthCallback).not.toHaveBeenCalled();
    expect(mockCaptureException).not.toHaveBeenCalled();
  });

  it("displays a user-facing message for backend OAuth error codes", async () => {
    Object.defineProperty(window, "location", {
      configurable: true,
      value: {
        ...originalLocation,
        pathname: "/auth/callback",
        search: "?error_code=ERR_AUTH_TOKEN_INVALID",
        hash: "",
        href: "http://localhost:5173/auth/callback?error_code=ERR_AUTH_TOKEN_INVALID",
      },
    });

    render(
      <MemoryRouter>
        <OAuthCallback />
      </MemoryRouter>
    );

    expect(await screen.findByText("Invalid authentication token")).toBeInTheDocument();
    expect(screen.queryByText("ERR_AUTH_TOKEN_INVALID")).not.toBeInTheDocument();
    expect(mockHandleOAuthCallback).not.toHaveBeenCalled();
    expect(mockNavigate).not.toHaveBeenCalled();
    expect(mockCaptureException).toHaveBeenCalled();
    expect(replaceStateSpy).toHaveBeenCalledWith(window.history.state, document.title, "/auth/callback");
  });

  it("scrubs callback credentials before a failed exchange and processes StrictMode only once", async () => {
    Object.assign(window.location, {
      search: "?access_token=access&refresh_token=refresh",
      hash: "#redirect=https%3A%2F%2Fmanga.zenstory.ai",
    });
    mockHandleOAuthCallback.mockImplementation(async () => {
      expect(replaceStateSpy).toHaveBeenCalledWith(window.history.state, document.title, "/auth/callback");
      throw new Error("ERR_AUTH_TOKEN_INVALID");
    });
    render(<MemoryRouter><StrictMode><OAuthCallback /></StrictMode></MemoryRouter>);
    expect(await screen.findByText("Invalid authentication token")).toBeInTheDocument();
    expect(mockHandleOAuthCallback).toHaveBeenCalledTimes(1);
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it("scrubs a provider error fragment while retaining its handled error UI", async () => {
    Object.assign(window.location, { hash: "#error=access_denied" });
    render(<MemoryRouter><OAuthCallback /></MemoryRouter>);
    expect(await screen.findByText("Sign-in did not complete")).toBeInTheDocument();
    expect(screen.queryByText("access_denied")).not.toBeInTheDocument();
    expect(replaceStateSpy).toHaveBeenCalledWith(window.history.state, document.title, "/auth/callback");
    expect(mockHandleOAuthCallback).not.toHaveBeenCalled();
    expect(mockNavigate).not.toHaveBeenCalled();
  });
});
