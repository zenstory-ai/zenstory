import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  auth: {
    user: null as { id: string; email_verified?: boolean; email?: string } | null,
    loading: false,
  },
  requireOnboarding: false,
  shouldRequireCalls: [] as unknown[],
  initialPath: "/",
  inspirationsEnabled: false,
  project: {
    currentProject: null as { id: string; name?: string } | null,
    projects: [] as Array<{ id: string; name?: string }>,
    setCurrentProjectId: vi.fn(),
    refreshProjects: vi.fn(),
    setSelectedItem: vi.fn(),
  },
  fileGet: vi.fn(),
  ssoRedirect: vi.fn(),
  personaGetState: vi.fn(),
}));

vi.mock("../config/inspirations", () => ({
  inspirationsConfig: {
    get enabled() { return state.inspirationsEnabled; },
  },
}));

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return {
    ...actual,
    BrowserRouter: ({ children }: { children: ReactNode }) => (
      <actual.MemoryRouter initialEntries={[state.initialPath]}>{children}</actual.MemoryRouter>
    ),
  };
});

vi.mock("react-helmet-async", () => ({
  HelmetProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
}));

vi.mock("../components/Toast", () => ({
  ToastContainer: () => <div data-testid="toast-container" />,
}));

vi.mock("../components/PageLoader", () => ({
  PageLoader: () => <div>Page Loader</div>,
}));

vi.mock("../components/Layout", () => ({
  Layout: () => <div>Layout</div>,
}));

vi.mock("../components/sidebar/Sidebar", () => ({
  Sidebar: () => <div>Sidebar</div>,
}));

vi.mock("../components/Editor", () => ({
  Editor: () => <div>Editor</div>,
}));

vi.mock("../components/ChatPanel", () => ({
  ChatPanel: () => <div>Chat Panel</div>,
}));

vi.mock("../components/Helmet", () => ({
  SEOHelmet: () => null,
}));

vi.mock("../providers/SEOProvider", () => ({
  SEOProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
}));

vi.mock("../providers/CommonProviders", () => ({
  CommonProviders: ({ children }: { children: ReactNode }) => <>{children}</>,
}));

vi.mock("../providers/ProtectedProviders", () => ({
  ProtectedProviders: ({ children }: { children: ReactNode }) => <>{children}</>,
}));

vi.mock("../contexts/ThemeContext", () => ({
  ThemeProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
}));

vi.mock("../contexts/FileSearchContext", () => ({
  FileSearchProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
}));

vi.mock("../contexts/AuthContext", () => ({
  AuthProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
  AuthIdentityQueryBoundary: ({ children }: { children: ReactNode }) => <>{children}</>,
  useAuth: () => state.auth,
}));

vi.mock("../contexts/ProjectContext", () => ({
  useProject: () => ({
    setCurrentProjectId: state.project.setCurrentProjectId,
    currentProject: state.project.currentProject,
    loading: false,
    error: null,
    refreshProjects: state.project.refreshProjects,
    projects: state.project.projects,
    setSelectedItem: state.project.setSelectedItem,
  }),
}));

vi.mock("../lib/onboardingPersona", () => ({
  shouldRequirePersonaOnboarding: (user: unknown) => {
    state.shouldRequireCalls.push(user);
    return state.requireOnboarding;
  },
}));

vi.mock("../lib/onboardingPersonaApi", async () => {
  const actual = await vi.importActual<typeof import("../lib/onboardingPersonaApi")>("../lib/onboardingPersonaApi");
  return {
    ...actual,
    onboardingPersonaApi: {
      ...actual.onboardingPersonaApi,
      getState: (...args: unknown[]) => state.personaGetState(...args),
    },
  };
});

vi.mock("../lib/ssoRedirect", () => ({
  handleSsoRedirect: (...args: unknown[]) => state.ssoRedirect(...args),
}));

vi.mock("../lib/logger", () => ({
  logger: {
    log: vi.fn(),
    warn: vi.fn(),
  },
}));

vi.mock("../lib/api", () => ({
  fileApi: {
    get: state.fileGet,
  },
}));

vi.mock("../pages/HomePage", () => ({
  default: () => <div>Home Page</div>,
}));

vi.mock("../pages/Login", () => ({
  default: () => <div>Login Page</div>,
}));

vi.mock("../pages/Register", () => ({
  default: () => <div>Register Page</div>,
}));

vi.mock("../pages/ForgotPassword", () => ({
  default: () => <div>Forgot Password Page</div>,
}));

vi.mock("../pages/Dashboard", async () => {
  const { Outlet, useLocation } = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return {
    default: function DashboardMock() {
      const location = useLocation();
      return <><div>Dashboard Page</div><div data-testid="dashboard-path">{location.pathname}</div><Outlet /></>;
    },
  };
});

vi.mock("../pages/DashboardHome", () => ({
  default: () => <div>Dashboard Home</div>,
}));

vi.mock("../pages/BillingPage", () => ({
  default: () => <div>Billing Page</div>,
}));

vi.mock("../pages/OnboardingPersonaPage", () => ({
  default: () => <div>Onboarding Persona Page</div>,
}));

vi.mock("../pages/VerifyEmail", () => ({
  default: () => <div>Verify Email Page</div>,
}));

vi.mock("../components/AdminRoute", () => ({
  default: ({ children }: { children: ReactNode }) => <>{children}</>,
}));

vi.mock("../components/admin/AdminLayout", async () => {
  const { Outlet, useLocation } = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return {
    default: function AdminLayoutMock() {
      const location = useLocation();
      return <><div>Admin Layout</div><div data-testid="admin-path">{location.pathname}</div><Outlet /></>;
    },
  };
});

vi.mock("../pages/InspirationsPage", () => ({
  default: () => <div>Inspiration List Page</div>,
}));

vi.mock("../pages/InspirationDetailPage", () => ({
  default: () => <div>Inspiration Detail Page</div>,
}));

vi.mock("../pages/admin/InspirationManagement", () => ({
  default: () => <div>Inspiration Admin Page</div>,
}));

vi.mock("../pages/admin/AdminDashboard", () => ({
  default: () => <div>Admin Dashboard</div>,
}));

import App from "../App";

const renderAppAt = (path: string) => {
  state.initialPath = path;
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>,
  );
};

describe("App route guards", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    state.auth.user = null;
    state.auth.loading = false;
    state.requireOnboarding = false;
    state.shouldRequireCalls = [];
    state.initialPath = "/";
    state.inspirationsEnabled = false;
    state.project.currentProject = null;
    state.project.projects = [];
    state.project.setCurrentProjectId.mockReset();
    state.project.refreshProjects.mockReset();
    state.project.setSelectedItem.mockReset();
    state.fileGet.mockReset();
    state.ssoRedirect.mockReset();
    state.personaGetState.mockReset();
    state.personaGetState.mockResolvedValue({ required: false, profile: null });
  });

  it("redirects unauthenticated users from protected routes to login", async () => {
    renderAppAt("/dashboard");

    await waitFor(() => {
      expect(screen.getByText("Login Page")).toBeInTheDocument();
    });
  });

  it.each(["/dashboard/inspirations", "/dashboard/inspirations/template-1"])(
    "redirects disabled library deep link %s without rendering the feature",
    async (path) => {
      state.auth.user = { id: "user-auth" };
      renderAppAt(path);
      await waitFor(() => expect(screen.getByTestId("dashboard-path")).toHaveTextContent(/^\/dashboard$/));
      expect(screen.queryByText("Inspiration List Page")).not.toBeInTheDocument();
      expect(screen.queryByText("Inspiration Detail Page")).not.toBeInTheDocument();
    },
  );

  it("redirects disabled admin library deep links", async () => {
    state.auth.user = { id: "admin-user" };
    renderAppAt("/admin/inspirations");
    await waitFor(() => expect(screen.getByTestId("admin-path")).toHaveTextContent(/^\/admin$/));
    expect(screen.queryByText("Inspiration Admin Page")).not.toBeInTheDocument();
  });

  it.each([
    ["/dashboard/inspirations", "Inspiration List Page"],
    ["/dashboard/inspirations/template-1", "Inspiration Detail Page"],
    ["/admin/inspirations", "Inspiration Admin Page"],
  ])("preserves enabled feature route %s", async (path, page) => {
    state.auth.user = { id: "user-auth" };
    state.inspirationsEnabled = true;
    renderAppAt(path);
    expect(await screen.findByText(page)).toBeInTheDocument();
  });

  it("redirects authenticated users away from login to dashboard", async () => {
    state.auth.user = { id: "user-auth" };

    renderAppAt("/login");

    await waitFor(() => {
      expect(screen.getByText("Dashboard Page")).toBeInTheDocument();
    });
  });

  it("redirects authenticated paid-plan intent to billing", async () => {
    state.auth.user = { id: "user-auth" };
    renderAppAt("/login?plan=PRO");
    await waitFor(() => expect(screen.getByTestId("dashboard-path")).toHaveTextContent(/^\/dashboard\/billing$/));
  });

  it("preserves auth and exits the loader for an invalid SSO redirect", async () => {
    state.auth.user = { id: "user-auth" };
    localStorage.setItem("access_token", "healthy-token");
    localStorage.setItem("refresh_token", "healthy-refresh");
    state.ssoRedirect.mockResolvedValue({
      success: false, clearAuth: false, shouldShowLogin: true, reason: "invalid_redirect", error: "invalid",
    });
    renderAppAt("/login?redirect=https%3A%2F%2Fevil.example");

    await waitFor(() => expect(screen.getByText("Dashboard Page")).toBeInTheDocument());
    expect(localStorage.getItem("access_token")).toBe("healthy-token");
    expect(localStorage.getItem("refresh_token")).toBe("healthy-refresh");
    expect(screen.queryByText("Page Loader")).not.toBeInTheDocument();
  });

  it("clears auth only for a definitive SSO credential failure", async () => {
    state.auth.user = { id: "user-auth" };
    localStorage.setItem("access_token", "expired-token");
    localStorage.setItem("refresh_token", "expired-refresh");
    state.ssoRedirect.mockResolvedValue({
      success: false, clearAuth: true, shouldShowLogin: true, reason: "session_expired",
    });
    renderAppAt("/login?redirect=https%3A%2F%2Fzenstory.ai");

    await waitFor(() => expect(screen.getByText("Login Page")).toBeInTheDocument());
    expect(localStorage.getItem("access_token")).toBeNull();
    expect(localStorage.getItem("refresh_token")).toBeNull();
  });

  it("preserves credentials and exits the loader for a thrown SSO failure", async () => {
    state.auth.user = { id: "user-auth" };
    localStorage.setItem("access_token", "healthy-token");
    localStorage.setItem("refresh_token", "healthy-refresh");
    state.ssoRedirect.mockRejectedValue(new Error("chunk failed"));
    renderAppAt("/login?redirect=https%3A%2F%2Fzenstory.ai");

    await waitFor(() => expect(screen.getByText("Dashboard Page")).toBeInTheDocument());
    expect(localStorage.getItem("access_token")).toBe("healthy-token");
    expect(screen.queryByText("Page Loader")).not.toBeInTheDocument();
  });

  it("preserves the authenticated session on retryable SSO failure", async () => {
    state.auth.user = { id: "user-auth" };
    localStorage.setItem("access_token", "healthy-token");
    localStorage.setItem("refresh_token", "healthy-refresh");
    const logoutListener = vi.fn();
    window.addEventListener("auth:logout", logoutListener);
    state.ssoRedirect.mockResolvedValue({
      success: false, clearAuth: false, shouldShowLogin: true, reason: "network_error", error: "offline",
    });
    renderAppAt("/login?redirect=https%3A%2F%2Fzenstory.ai");

    await waitFor(() => expect(screen.getByText("Dashboard Page")).toBeInTheDocument());
    expect(localStorage.getItem("access_token")).toBe("healthy-token");
    expect(logoutListener).not.toHaveBeenCalled();
    window.removeEventListener("auth:logout", logoutListener);
  });

  it("shows loading indicator while auth guard is resolving", () => {
    state.auth.loading = true;

    renderAppAt("/dashboard");

    expect(screen.getByText("Page Loader")).toBeInTheDocument();
  });

  it("redirects authenticated users to onboarding when required", async () => {
    state.auth.user = { id: "user-1" };
    state.requireOnboarding = false;
    state.personaGetState.mockResolvedValue({ required: true, profile: null });

    renderAppAt("/dashboard");

    await waitFor(() => {
      expect(screen.getByText("Onboarding Persona Page")).toBeInTheDocument();
    });
    expect(state.personaGetState).toHaveBeenCalledTimes(1);
  });

  it("does not loop-redirect when already on onboarding route", async () => {
    state.auth.user = { id: "user-2" };
    state.requireOnboarding = true;

    renderAppAt("/onboarding/persona");

    await waitFor(() => {
      expect(screen.getByText("Onboarding Persona Page")).toBeInTheDocument();
    });
    expect(state.shouldRequireCalls).toEqual([]);
  });

  it("skips onboarding redirects on admin routes", async () => {
    state.auth.user = { id: "admin-user" };
    state.requireOnboarding = true;

    renderAppAt("/admin");

    await waitFor(() => {
      expect(screen.getByText("Admin Layout")).toBeInTheDocument();
    });
    expect(state.shouldRequireCalls).toEqual([]);
  });

  it("allows authenticated users through when onboarding is not required", async () => {
    state.auth.user = { id: "user-3" };
    state.requireOnboarding = true;
    state.personaGetState.mockResolvedValue({ required: false, profile: { version: 1 } });

    renderAppAt("/dashboard");

    await waitFor(() => {
      expect(screen.getByText("Dashboard Page")).toBeInTheDocument();
    });
    expect(state.personaGetState).toHaveBeenCalledTimes(1);
  });

  it("waits for late server persona state instead of redirecting from missing local data", async () => {
    state.auth.user = { id: "late-user" };
    state.requireOnboarding = true;
    let resolveState!: (value: { required: boolean; profile: null }) => void;
    state.personaGetState.mockReturnValue(new Promise((resolve) => { resolveState = resolve; }));

    renderAppAt("/dashboard");
    expect(screen.getByText("Page Loader")).toBeInTheDocument();
    expect(screen.queryByText("Onboarding Persona Page")).not.toBeInTheDocument();

    resolveState({ required: false, profile: null });
    await waitFor(() => expect(screen.getByText("Dashboard Page")).toBeInTheDocument());
  });

  it("does not let forged local persona data bypass a server-required state", async () => {
    state.auth.user = { id: "forged-user" };
    state.requireOnboarding = false;
    state.personaGetState.mockResolvedValue({ required: true, profile: null });

    renderAppAt("/dashboard?tab=recent#draft");

    await waitFor(() => expect(screen.getByText("Onboarding Persona Page")).toBeInTheDocument());
  });

  it("ignores a late persona response from the previously authenticated user", async () => {
    state.auth.user = { id: "old-user" };
    let resolveOld!: (value: { required: boolean; profile: null }) => void;
    state.personaGetState
      .mockReturnValueOnce(new Promise((resolve) => { resolveOld = resolve; }))
      .mockResolvedValueOnce({ required: true, profile: null });
    state.initialPath = "/dashboard";
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const view = render(
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(state.personaGetState).toHaveBeenCalledTimes(1));

    state.auth.user = { id: "new-user" };
    view.rerender(
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByText("Onboarding Persona Page")).toBeInTheDocument());
    resolveOld({ required: false, profile: null });
    await waitFor(() => expect(screen.getByText("Onboarding Persona Page")).toBeInTheDocument());
    expect(screen.queryByText("Dashboard Page")).not.toBeInTheDocument();
  });

  it("keeps a failed persona gate closed and lets the user retry", async () => {
    state.auth.user = { id: "retry-user" };
    state.personaGetState
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce({ required: false, profile: null });

    renderAppAt("/dashboard");

    const retry = await screen.findByRole("button", { name: /retry|重试/i });
    expect(screen.queryByText("Dashboard Page")).not.toBeInTheDocument();
    fireEvent.click(retry);
    await waitFor(() => expect(screen.getByText("Dashboard Page")).toBeInTheDocument());
  });

  it("waits for the route project before selecting a deep-linked file", async () => {
    state.auth.user = { id: "user-4" };
    state.project.currentProject = { id: "previous-project" };
    state.project.projects = [{ id: "target-project" }];
    state.fileGet.mockResolvedValue({
      id: "file-1",
      title: "Deep-linked file",
      file_type: "draft",
      project_id: "target-project",
    });

    const view = renderAppAt("/project/target-project?file=file-1");

    await waitFor(() => {
      expect(state.project.setCurrentProjectId).toHaveBeenCalledWith("target-project");
    });
    expect(state.fileGet).not.toHaveBeenCalled();

    state.project.currentProject = { id: "target-project" };
    view.rerender(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <App />
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(state.fileGet).toHaveBeenCalledWith("file-1");
      expect(state.project.setSelectedItem).toHaveBeenCalledWith({
        id: "file-1",
        title: "Deep-linked file",
        type: "draft",
      });
    });
  });
});
