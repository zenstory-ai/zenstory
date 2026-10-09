import { render, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../lib/apiClient";
import { getLastOpenedFile, setLastOpenedFile } from "../lib/lastOpenedFile";
import type { SelectedItem } from "../types";

const state = vi.hoisted(() => ({
  user: { id: "user-1" } as { id: string } | null,
  initialPath: "/project/project-1",
  project: {
    currentProject: { id: "project-1" } as { id: string } | null,
    projects: [{ id: "project-1" }, { id: "project-2" }] as Array<{ id: string }>,
    selectedItem: null as SelectedItem | null,
    setCurrentProjectId: vi.fn(),
    refreshProjects: vi.fn(),
    setSelectedItem: vi.fn(),
  },
  fileGet: vi.fn(),
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
vi.mock("../components/Toast", () => ({ ToastContainer: () => null }));
vi.mock("../components/PageLoader", () => ({ PageLoader: () => <div>Page Loader</div> }));
vi.mock("../components/Layout", () => ({ Layout: () => <div>Layout</div> }));
vi.mock("../components/sidebar/Sidebar", () => ({ Sidebar: () => null }));
vi.mock("../components/Editor", () => ({ Editor: () => null }));
vi.mock("../components/ChatPanel", () => ({ ChatPanel: () => null }));
vi.mock("../components/Helmet", () => ({ SEOHelmet: () => null }));
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
  useAuth: () => ({ user: state.user, loading: false }),
}));
vi.mock("../contexts/ProjectContext", () => ({
  useProject: () => ({
    setCurrentProjectId: state.project.setCurrentProjectId,
    currentProject: state.project.currentProject,
    loading: false,
    error: null,
    refreshProjects: state.project.refreshProjects,
    projects: state.project.projects,
    selectedItem: state.project.selectedItem,
    setSelectedItem: state.project.setSelectedItem,
  }),
}));
vi.mock("../lib/onboardingPersona", () => ({
  shouldRequirePersonaOnboarding: () => false,
}));
vi.mock("../lib/onboardingPersonaApi", async () => {
  const actual = await vi.importActual<typeof import("../lib/onboardingPersonaApi")>("../lib/onboardingPersonaApi");
  return {
    ...actual,
    onboardingPersonaApi: {
      ...actual.onboardingPersonaApi,
      getState: () => Promise.resolve({ required: false, profile: null }),
    },
  };
});
vi.mock("../lib/ssoRedirect", () => ({ handleSsoRedirect: vi.fn() }));
vi.mock("../lib/logger", () => ({ logger: { log: vi.fn(), warn: vi.fn(), error: vi.fn() } }));
vi.mock("../lib/api", () => ({ fileApi: { get: state.fileGet } }));

import App from "../App";

const renderAppAt = (path: string) => {
  state.initialPath = path;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>,
  );
};

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("ProjectEditor restores the last opened file", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    state.user = { id: "user-1" };
    state.project.currentProject = { id: "project-1" };
    state.project.selectedItem = null;
    state.fileGet.mockReset();
  });

  it("reopens the file the author last had open in this project", async () => {
    setLastOpenedFile("user-1", "project-1", "file-7");
    state.fileGet.mockResolvedValue({
      id: "file-7",
      title: "第三章",
      file_type: "draft",
      project_id: "project-1",
    });

    renderAppAt("/project/project-1");

    await waitFor(() => {
      expect(state.project.setSelectedItem).toHaveBeenCalledWith({
        id: "file-7",
        title: "第三章",
        type: "draft",
      });
    });
    expect(state.fileGet).toHaveBeenCalledTimes(1);
    expect(state.fileGet).toHaveBeenCalledWith("file-7");
  });

  it("lets a ?file= deep link win over the remembered file", async () => {
    setLastOpenedFile("user-1", "project-1", "file-old");
    state.fileGet.mockResolvedValue({
      id: "file-new",
      title: "Linked",
      file_type: "outline",
      project_id: "project-1",
    });

    renderAppAt("/project/project-1?file=file-new");

    await waitFor(() => {
      expect(state.project.setSelectedItem).toHaveBeenCalledWith({
        id: "file-new",
        title: "Linked",
        type: "outline",
      });
    });
    await flush();
    expect(state.fileGet).toHaveBeenCalledTimes(1);
    expect(state.fileGet).not.toHaveBeenCalledWith("file-old");
  });

  it("forgets a remembered file that now belongs to another project", async () => {
    setLastOpenedFile("user-1", "project-1", "file-moved");
    state.fileGet.mockResolvedValue({
      id: "file-moved",
      title: "Moved",
      file_type: "draft",
      project_id: "project-2",
    });

    renderAppAt("/project/project-1");

    await waitFor(() => expect(getLastOpenedFile("user-1", "project-1")).toBeNull());
    expect(state.project.setSelectedItem).not.toHaveBeenCalled();
  });

  it("forgets a remembered file that was deleted (404)", async () => {
    setLastOpenedFile("user-1", "project-1", "file-gone");
    state.fileGet.mockRejectedValue(new ApiError(404, "ERR_FILE_NOT_FOUND"));

    renderAppAt("/project/project-1");

    await waitFor(() => expect(getLastOpenedFile("user-1", "project-1")).toBeNull());
    expect(state.project.setSelectedItem).not.toHaveBeenCalled();
  });

  it("keeps the record when the lookup fails for a transient reason", async () => {
    setLastOpenedFile("user-1", "project-1", "file-7");
    state.fileGet.mockRejectedValue(new TypeError("Failed to fetch"));

    renderAppAt("/project/project-1");

    await waitFor(() => expect(state.fileGet).toHaveBeenCalledWith("file-7"));
    await flush();
    expect(getLastOpenedFile("user-1", "project-1")).toBe("file-7");
    expect(state.project.setSelectedItem).not.toHaveBeenCalled();
  });

  it("does not replace a file the author already has open", async () => {
    setLastOpenedFile("user-1", "project-1", "file-7");
    state.project.selectedItem = { id: "file-current", title: "Current", type: "draft" };

    renderAppAt("/project/project-1");
    await flush();

    expect(state.fileGet).not.toHaveBeenCalled();
    expect(state.project.setSelectedItem).not.toHaveBeenCalled();
  });

  it("remembers the open file per user and project, but not folders", async () => {
    state.project.selectedItem = { id: "file-9", title: "Notes", type: "lore" };
    const view = renderAppAt("/project/project-1");

    await waitFor(() => expect(getLastOpenedFile("user-1", "project-1")).toBe("file-9"));
    expect(getLastOpenedFile("user-2", "project-1")).toBeNull();
    expect(getLastOpenedFile("user-1", "project-2")).toBeNull();

    state.project.selectedItem = { id: "folder-1", title: "Folder", type: "folder" };
    view.rerender(
      <QueryClientProvider client={new QueryClient()}>
        <App />
      </QueryClientProvider>,
    );
    await flush();
    expect(getLastOpenedFile("user-1", "project-1")).toBe("file-9");
  });
});
