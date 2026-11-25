import { StrictMode, createContext, useContext } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { setLastOpenedFile } from "../lib/lastOpenedFile";
import { createEditorDraftSnapshot, writeEditorDraftSnapshot } from "../lib/editorDraftRecovery";
import { peekPreloadedFile, seedPreloadedFile } from "../lib/preloadedFileCache";
import type { File, Project, SelectedItem } from "../types";

const ProjectRevContext = createContext(0);

const mocks = vi.hoisted(() => ({
  fileGet: vi.fn(),
  fileGetTree: vi.fn(),
  fileUpdate: vi.fn(),
  refreshProjects: vi.fn(),
  setCurrentProjectId: vi.fn(),
  setSelectedItem: vi.fn(),
  project: {
    projects: [] as Project[],
    currentProject: null as Project | null,
    currentProjectId: null as string | null,
    loading: true,
    error: null as string | null,
    selectedItem: null as SelectedItem | null,
  },
}));

vi.mock("../lib/api", () => ({
  fileApi: {
    get: mocks.fileGet,
    getTree: mocks.fileGetTree,
    create: vi.fn(),
    update: mocks.fileUpdate,
  },
  fileVersionApi: {
    getVersions: vi.fn().mockResolvedValue({ total: 0, versions: [] }),
  },
}));

vi.mock("../lib/analytics", () => ({
  trackEvent: vi.fn(),
  captureException: vi.fn(),
}));
vi.mock("../lib/upgradeAnalytics", () => ({
  trackUpgradeExpose: vi.fn(),
  trackUpgradeClick: vi.fn(),
}));
vi.mock("../lib/activationEvents", () => ({
  rearmAiFileOpened: vi.fn(),
  reportActivationEvent: vi.fn().mockResolvedValue(undefined),
}));
vi.mock("../lib/toast", () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() },
}));
vi.mock("../lib/logger", () => ({
  logger: { log: vi.fn(), warn: vi.fn(), error: vi.fn() },
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock("../components/FirstWorkGuidance", () => ({
  FirstWorkOpenTracker: () => null,
  FirstWorkGuidance: () => null,
}));
vi.mock("../components/ChatPanel", () => ({
  ChatPanel: () => <div data-testid="chat-panel" />,
}));
vi.mock("../components/Layout", () => ({
  Layout: ({ middle, right }: { middle: React.ReactNode; right: React.ReactNode }) => (
    <div data-testid="workbench-layout">
      {middle}
      {right}
    </div>
  ),
}));
vi.mock("../components/PageLoader", () => ({
  PageLoader: () => <div data-testid="page-loader">Loading</div>,
}));
vi.mock("../contexts/AuthContext", () => ({
  useAuth: () => ({ user: { id: "user-1" }, loading: false }),
}));
vi.mock("../contexts/MaterialLibraryContext", () => ({
  useMaterialLibraryContext: () => ({ preview: null }),
}));
vi.mock("../contexts/MaterialAttachmentContext", () => ({
  useMaterialAttachment: () => ({
    addMaterial: vi.fn(),
    isMaterialAttached: () => false,
    isAtLimit: false,
    maxMaterials: 5,
  }),
}));
vi.mock("../contexts/TextQuoteContext", () => ({
  useTextQuote: () => ({ addQuote: vi.fn() }),
}));
vi.mock("../contexts/ProjectContext", () => ({
  useProject: () => {
    useContext(ProjectRevContext);
    return {
      ...mocks.project,
      setCurrentProjectId: mocks.setCurrentProjectId,
      setSelectedItem: mocks.setSelectedItem,
      refreshProjects: mocks.refreshProjects,
      triggerFileTreeRefresh: vi.fn(),
      editorRefreshVersion: 0,
      lastEditedFileId: null,
      aiEditingFileId: null,
      isChatStreaming: false,
      requestAgentUiAction: vi.fn(),
      streamingFileId: null,
      streamingContent: "",
      diffReviewState: null,
      enterDiffReview: vi.fn(),
      acceptEdit: vi.fn(),
      rejectEdit: vi.fn(),
      resetEdit: vi.fn(),
      acceptAllEdits: vi.fn(),
      rejectAllEdits: vi.fn(),
      exitDiffReview: vi.fn(),
      applyDiffReviewChanges: () => "",
    };
  },
}));

import ProjectEditor from "../pages/ProjectEditor";
import { Editor } from "../components/Editor";

const sampleProject: Project = {
  id: "project-1",
  name: "Test Novel",
  project_type: "novel",
  created_at: "2026-11-01T00:00:00Z",
  updated_at: "2026-11-01T00:00:00Z",
};

const sampleFile: File = {
  id: "file-7",
  project_id: "project-1",
  title: "第三章",
  content: "山雨欲来风满楼。",
  file_type: "draft",
  order_index: 0,
  created_at: "2026-11-01T00:00:00Z",
  updated_at: "2026-11-25T10:00:00Z",
};

describe("ProjectEditor boot API deduplication", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    mocks.project.projects = [];
    mocks.project.currentProject = null;
    mocks.project.currentProjectId = null;
    mocks.project.loading = true;
    mocks.project.error = null;
    mocks.project.selectedItem = null;
    mocks.fileGet.mockReset();
    mocks.fileGetTree.mockResolvedValue({ tree: [] });
    mocks.refreshProjects.mockResolvedValue(undefined);
  });

  it("does not trigger a duplicate refreshProjects while initial project load is in flight", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    let rev = 0;

    const renderTree = () => (
      <QueryClientProvider client={queryClient}>
        <ProjectRevContext.Provider value={rev++}>
          <MemoryRouter initialEntries={["/project/project-1"]}>
            <Routes>
              <Route path="/project/:projectId" element={<ProjectEditor />} />
            </Routes>
          </MemoryRouter>
        </ProjectRevContext.Provider>
      </QueryClientProvider>
    );

    const view = render(renderTree());

    expect(screen.getByTestId("page-loader")).toBeInTheDocument();
    expect(mocks.setCurrentProjectId).toHaveBeenCalledWith("project-1");
    expect(mocks.refreshProjects).not.toHaveBeenCalled();

    // Initial loadProjects finishes and provides project-1.
    mocks.project.loading = false;
    mocks.project.projects = [sampleProject];
    mocks.project.currentProject = sampleProject;
    mocks.project.currentProjectId = "project-1";
    view.rerender(renderTree());

    await waitFor(() => {
      expect(screen.getByTestId("workbench-layout")).toBeInTheDocument();
    });
    expect(mocks.refreshProjects).not.toHaveBeenCalled();
  });

  it("reuses the file fetched during last-opened restore without a duplicate GET while preserving draft recovery", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    let rev = 0;

    setLastOpenedFile("user-1", "project-1", sampleFile.id);
    // Seed an unsaved local draft on top of the same server version to prove recovery still runs.
    writeEditorDraftSnapshot(
      localStorage,
      createEditorDraftSnapshot({
        userId: "user-1",
        projectId: "project-1",
        fileId: sampleFile.id,
        title: sampleFile.title,
        content: "山雨欲来风满楼（本地未保存草稿）。",
        baseUpdatedAt: sampleFile.updated_at,
        reason: "page-exit",
      }),
    );

    mocks.project.loading = false;
    mocks.project.projects = [sampleProject];
    mocks.project.currentProject = sampleProject;
    mocks.project.currentProjectId = "project-1";
    mocks.fileGet.mockResolvedValue(sampleFile);

    const renderTree = () => (
      <QueryClientProvider client={queryClient}>
        <ProjectRevContext.Provider value={rev++}>
          <MemoryRouter initialEntries={["/project/project-1"]}>
            <Routes>
              <Route path="/project/:projectId" element={<ProjectEditor />} />
            </Routes>
          </MemoryRouter>
        </ProjectRevContext.Provider>
      </QueryClientProvider>
    );

    const holder: { view?: ReturnType<typeof render> } = {};
    mocks.setSelectedItem.mockImplementation((item: SelectedItem | null) => {
      mocks.project.selectedItem = item;
      holder.view?.rerender(renderTree());
    });

    const view = render(renderTree());
    holder.view = view;

    await waitFor(() => {
      expect(screen.getByPlaceholderText("editor:placeholder.contentPlaceholder")).toHaveValue(
        "山雨欲来风满楼（本地未保存草稿）。",
      );
    });

    // Exactly ONE GET /api/v1/files/{id} across ProjectEditor restore + Editor mount!
    expect(mocks.fileGet).toHaveBeenCalledTimes(1);
    expect(mocks.fileGet).toHaveBeenCalledWith(sampleFile.id);

    // One-shot guarantee: switching away and back hits live fileApi.get again.
    const updatedServerFile = {
      ...sampleFile,
      content: "服务端新版本内容。",
      updated_at: "2026-11-25T11:00:00Z",
    };
    mocks.fileGet.mockResolvedValue(updatedServerFile);

    act(() => {
      mocks.project.selectedItem = null;
      view.rerender(renderTree());
    });
    act(() => {
      mocks.project.selectedItem = { id: sampleFile.id, title: sampleFile.title, type: "draft" };
      view.rerender(renderTree());
    });

    await waitFor(() => {
      expect(mocks.fileGet).toHaveBeenCalledTimes(2);
    });
  });

  it("survives React StrictMode double-effect execution in Editor without falling back to fileApi.get", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    seedPreloadedFile(queryClient, "user-1", "project-1", sampleFile);

    mocks.project.loading = false;
    mocks.project.projects = [sampleProject];
    mocks.project.currentProject = sampleProject;
    mocks.project.currentProjectId = "project-1";
    mocks.project.selectedItem = { id: sampleFile.id, title: sampleFile.title, type: "draft" };
    mocks.fileGet.mockResolvedValue(sampleFile);

    render(
      <StrictMode>
        <QueryClientProvider client={queryClient}>
          <Editor />
        </QueryClientProvider>
      </StrictMode>,
    );

    await waitFor(() => {
      expect(screen.getByPlaceholderText("editor:placeholder.contentPlaceholder")).toHaveValue(
        sampleFile.content,
      );
    });
    // StrictMode mounts/unmounts/remounts Editor: the microtask handoff must keep the
    // preloaded entry alive for the surviving effect so Editor makes 0 extra GET calls.
    expect(mocks.fileGet).not.toHaveBeenCalled();
    expect(peekPreloadedFile(queryClient, "user-1", "project-1", sampleFile.id)).toBeNull();
  });

  it("preserves explicit conflict recovery when preloaded server version is newer than local draft", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    let rev = 0;

    setLastOpenedFile("user-1", "project-1", sampleFile.id);
    // Draft was saved against an older updated_at token than sampleFile.updated_at.
    writeEditorDraftSnapshot(
      localStorage,
      createEditorDraftSnapshot({
        userId: "user-1",
        projectId: "project-1",
        fileId: sampleFile.id,
        title: sampleFile.title,
        content: "本地旧版本上的未保存草稿。",
        baseUpdatedAt: "2026-11-25T09:00:00Z",
        reason: "page-exit",
      }),
    );

    mocks.project.loading = false;
    mocks.project.projects = [sampleProject];
    mocks.project.currentProject = sampleProject;
    mocks.project.currentProjectId = "project-1";
    mocks.fileGet.mockResolvedValue(sampleFile);

    const renderTree = () => (
      <QueryClientProvider client={queryClient}>
        <ProjectRevContext.Provider value={rev++}>
          <MemoryRouter initialEntries={["/project/project-1"]}>
            <Routes>
              <Route path="/project/:projectId" element={<ProjectEditor />} />
            </Routes>
          </MemoryRouter>
        </ProjectRevContext.Provider>
      </QueryClientProvider>
    );

    const holder: { view?: ReturnType<typeof render> } = {};
    mocks.setSelectedItem.mockImplementation((item: SelectedItem | null) => {
      mocks.project.selectedItem = item;
      holder.view?.rerender(renderTree());
    });

    const view = render(renderTree());
    holder.view = view;

    // Must keep the newer server content loaded (no automatic overwrite) and show explicit conflict recovery choices.
    await waitFor(() => {
      expect(screen.getByPlaceholderText("editor:placeholder.contentPlaceholder")).toHaveValue(
        sampleFile.content,
      );
      expect(screen.getByRole("button", { name: "editor:draftRecovery.restore" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "editor:draftRecovery.discard" })).toBeInTheDocument();
      expect(screen.getByText("editor:draftRecovery.compare")).toBeInTheDocument();
    });
    expect(mocks.fileGet).toHaveBeenCalledTimes(1);
  });

  it("evicts preloaded target file from cache when leaving a dirty file fails flush", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    let rev = 0;

    const fileB: File = {
      ...sampleFile,
      id: "file-8",
      title: "第四章",
      content: "预加载的第四章内容。",
    };

    mocks.project.loading = false;
    mocks.project.projects = [sampleProject];
    mocks.project.currentProject = sampleProject;
    mocks.project.currentProjectId = "project-1";
    mocks.project.selectedItem = { id: sampleFile.id, title: sampleFile.title, type: "draft" };
    mocks.fileGet.mockResolvedValue(sampleFile);
    mocks.fileUpdate.mockRejectedValue(new Error("save failed"));

    const renderTree = () => (
      <QueryClientProvider client={queryClient}>
        <ProjectRevContext.Provider value={rev++}>
          <MemoryRouter initialEntries={["/project/project-1"]}>
            <Routes>
              <Route path="/project/:projectId" element={<ProjectEditor />} />
            </Routes>
          </MemoryRouter>
        </ProjectRevContext.Provider>
      </QueryClientProvider>
    );

    const holder: { view?: ReturnType<typeof render> } = {};
    mocks.setSelectedItem.mockImplementation((item: SelectedItem | null) => {
      mocks.project.selectedItem = item;
      holder.view?.rerender(renderTree());
    });

    const view = render(renderTree());
    holder.view = view;

    await waitFor(() => {
      expect(screen.getByPlaceholderText("editor:placeholder.contentPlaceholder")).toHaveValue(
        sampleFile.content,
      );
    });

    // Make file A dirty, then seed file B and attempt to switch to file B.
    fireEvent.change(screen.getByPlaceholderText("editor:placeholder.contentPlaceholder"), {
      target: { value: "修改后但保存失败的第三章" },
    });
    seedPreloadedFile(queryClient, "user-1", "project-1", fileB);
    expect(peekPreloadedFile(queryClient, "user-1", "project-1", fileB.id)).not.toBeNull();

    act(() => {
      mocks.project.selectedItem = { id: fileB.id, title: fileB.title, type: "draft" };
      view.rerender(renderTree());
    });

    await waitFor(() => {
      expect(mocks.fileUpdate).toHaveBeenCalledTimes(1);
      expect(peekPreloadedFile(queryClient, "user-1", "project-1", fileB.id)).toBeNull();
    });
  });
});
