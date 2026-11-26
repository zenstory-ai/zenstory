import { StrictMode, createContext, useContext } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, clearAuthStorage } from "../lib/apiClient";
import { setLastOpenedFile } from "../lib/lastOpenedFile";
import { createEditorDraftSnapshot, writeEditorDraftSnapshot } from "../lib/editorDraftRecovery";
import {
  AUTH_SESSION_USER_KEY,
  clearWorkbenchSwrCache,
  readAuthSessionUserSnapshot,
  readWorkbenchSwr,
  writeAuthSessionUserSnapshot,
  writeWorkbenchSwr,
} from "../lib/workbenchSwrCache";
import type { ProjectBootstrapResponse } from "../lib/api";
import type { File, FileTreeNode, Project, SelectedItem } from "../types";

const ProjectRevContext = createContext(0);

const mocks = vi.hoisted(() => ({
  getBootstrap: vi.fn(),
  projectGet: vi.fn(),
  projectGetAll: vi.fn(),
  fileGet: vi.fn(),
  fileGetTree: vi.fn(),
  fileUpdate: vi.fn(),
  getRecentMessages: vi.fn(),
  refreshProjects: vi.fn(),
  setCurrentProjectId: vi.fn(),
  setSelectedItem: vi.fn(),
  hydrateProject: vi.fn(),
  user: {
    id: "user-1",
    username: "writer1",
    email: "writer1@example.com",
    email_verified: true,
    is_active: true,
    is_superuser: false,
    created_at: "2026-11-01T00:00:00Z",
    updated_at: "2026-11-01T00:00:00Z",
  },
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
  authApi: {
    login: vi.fn(),
    register: vi.fn(),
    verifyEmail: vi.fn(),
    resendVerification: vi.fn(),
  },
  projectApi: {
    getBootstrap: mocks.getBootstrap,
    get: mocks.projectGet,
    getAll: mocks.projectGetAll,
    getProgress: vi.fn().mockResolvedValue([]),
  },
  fileApi: {
    get: mocks.fileGet,
    getTree: mocks.fileGetTree,
    create: vi.fn(),
    update: mocks.fileUpdate,
    delete: vi.fn(),
    upload: vi.fn(),
  },
  fileVersionApi: {
    getVersions: vi.fn().mockResolvedValue({ total: 0, versions: [] }),
  },
}));

vi.mock("../lib/chatApi", () => ({
  getRecentMessages: mocks.getRecentMessages,
  createNewSession: vi.fn(),
  submitMessageFeedback: vi.fn(),
}));

vi.mock("../lib/analytics", () => ({
  identifyUser: vi.fn(),
  resetAnalytics: vi.fn(),
  trackEvent: vi.fn(),
  captureException: vi.fn(),
}));
vi.mock("../lib/upgradeAnalytics", () => ({
  trackUpgradeExpose: vi.fn(),
  trackUpgradeClick: vi.fn(),
  clearPendingUpgradeFunnelEvents: vi.fn(),
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
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: "zh" } }),
}));
vi.mock("../components/FirstWorkGuidance", () => ({
  FirstWorkOpenTracker: () => null,
  FirstWorkGuidance: () => null,
}));
vi.mock("../components/PageLoader", () => ({
  PageLoader: () => <div data-testid="page-loader">Loading</div>,
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
vi.mock("../contexts/AuthContext", async () => {
  const actual = await vi.importActual<typeof import("../contexts/AuthContext")>("../contexts/AuthContext");
  return {
    ...actual,
    useAuth: () => ({ user: mocks.user, loading: false }),
    useOptionalAuth: () => ({ user: mocks.user, loading: false }),
  };
});
vi.mock("../contexts/ProjectContext", () => ({
  useProject: () => {
    useContext(ProjectRevContext);
    return {
      ...mocks.project,
      setCurrentProjectId: mocks.setCurrentProjectId,
      setSelectedItem: mocks.setSelectedItem,
      refreshProjects: mocks.refreshProjects,
      hydrateProject: mocks.hydrateProject,
      fileTreeVersion: 0,
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

// Lightweight Layout & Chat consumer that mounts useFileTree + real Editor + bootstrap chat probe.
function FileTreeProbe() {
  const { tree, isInitialLoad } = useFileTree();
  return (
    <div data-testid="file-tree-probe" data-initial-load={String(isInitialLoad)}>
      {tree.map((node) => (
        <span key={node.id}>{node.title}</span>
      ))}
    </div>
  );
}

vi.mock("../components/ChatPanel", () => ({
  ChatPanel: function ChatBootstrapProbe() {
    const queryClient = useContext(QueryClientContext);
    const [messages, setMessages] = React.useState<Array<{ id: string; content: string }>>([]);
    const [hasMore, setHasMore] = React.useState(false);

    React.useEffect(() => {
      let cancelled = false;
      const pending = peekBootstrappedChat(queryClient, mocks.user.id, "project-1");
      if (!pending) return;
      void pending.promise.then((res) => {
        if (cancelled || !res?.chatReady) return;
        markBootstrappedChatConsumed(queryClient, mocks.user.id, "project-1", pending);
        setMessages(res.data.recent_messages.map((m) => ({ id: m.id, content: m.content })));
        setHasMore(res.data.has_more_messages);
      });
      return () => {
        cancelled = true;
      };
    }, [queryClient]);

    return (
      <div data-testid="chat-panel" data-has-more={String(hasMore)}>
        {messages.map((m) => (
          <span key={m.id}>{m.content}</span>
        ))}
      </div>
    );
  },
}));

vi.mock("../components/Layout", () => ({
  Layout: ({ middle, right }: { middle: React.ReactNode; right: React.ReactNode }) => (
    <div data-testid="workbench-layout">
      <FileTreeProbe />
      {middle}
      {right}
    </div>
  ),
}));

import React from "react";
import { QueryClientContext } from "@tanstack/react-query";
import ProjectEditor from "../pages/ProjectEditor";
import { useFileTree } from "../hooks/useFileTree";
import {
  markBootstrappedChatConsumed,
  peekBootstrappedChat,
} from "../lib/workbenchBootstrap";

const sampleProject: Project = {
  id: "project-1",
  name: "加速测试小说",
  project_type: "novel",
  created_at: "2026-11-01T00:00:00Z",
  updated_at: "2026-11-26T00:00:00Z",
};

const sampleTree: FileTreeNode[] = [
  {
    id: "folder-drafts",
    title: "正文",
    file_type: "folder",
    parent_id: null,
    order: 1,
    metadata: null,
    children: [
      {
        id: "file-7",
        title: "第七章",
        file_type: "draft",
        parent_id: "folder-drafts",
        order: 1,
        content: "",
        metadata: null,
        children: [],
      },
    ],
  },
];

const sampleFile: File = {
  id: "file-7",
  project_id: "project-1",
  title: "第七章",
  content: "服务端第七章正文。",
  file_type: "draft",
  parent_id: "folder-drafts",
  order: 1,
  file_metadata: null,
  created_at: "2026-11-01T00:00:00Z",
  updated_at: "2026-11-26T08:00:00Z",
};

const sampleBootstrap: ProjectBootstrapResponse = {
  project: sampleProject,
  files: sampleTree,
  initial_file: sampleFile,
  chat_session: {
    id: "session-1",
    user_id: "user-1",
    project_id: "project-1",
    title: "AI 助手对话",
    is_active: true,
    message_count: 20,
    created_at: "2026-11-01T00:00:00Z",
    updated_at: "2026-11-26T08:00:00Z",
  },
  recent_messages: [
    {
      id: "msg-1",
      session_id: "session-1",
      role: "assistant",
      content: "上次我们写到第七章转折点。",
      tool_calls: null,
      metadata: null,
      created_at: "2026-11-26T07:59:00Z",
    },
  ],
  has_more_messages: true,
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("Workbench /bootstrap single-call boot, 404 fallback, and SWR cache", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    sessionStorage.clear();
    mocks.project.projects = [];
    mocks.project.currentProject = null;
    mocks.project.currentProjectId = null;
    mocks.project.loading = true;
    mocks.project.error = null;
    mocks.project.selectedItem = null;
    mocks.getBootstrap.mockReset();
    mocks.projectGet.mockReset();
    mocks.fileGet.mockReset();
    mocks.fileGetTree.mockReset();
    mocks.getRecentMessages.mockReset();
  });

  afterEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });

  function mountEditor(queryClient: QueryClient, options: { strict?: boolean; path?: string } = {}) {
    const listeners = new Set<() => void>();
    const notify = () => {
      listeners.forEach((listener) => listener());
    };

    function StatefulHost() {
      const [rev, setRev] = React.useState(0);
      React.useLayoutEffect(() => {
        const listener = () => setRev((value) => value + 1);
        listeners.add(listener);
        return () => {
          listeners.delete(listener);
        };
      }, []);
      const inner = (
        <QueryClientProvider client={queryClient}>
          <ProjectRevContext.Provider value={rev}>
            <MemoryRouter initialEntries={[options.path ?? "/project/project-1"]}>
              <Routes>
                <Route path="/project/:projectId" element={<ProjectEditor />} />
              </Routes>
            </MemoryRouter>
          </ProjectRevContext.Provider>
        </QueryClientProvider>
      );
      return options.strict ? <StrictMode>{inner}</StrictMode> : inner;
    }

    mocks.setCurrentProjectId.mockImplementation((id: string | null) => {
      mocks.project.currentProjectId = id;
      mocks.project.currentProject = mocks.project.projects.find((p) => p.id === id) ?? null;
      notify();
    });
    mocks.hydrateProject.mockImplementation((project: Project) => {
      mocks.project.projects = [project];
      mocks.project.currentProjectId = project.id ?? null;
      mocks.project.currentProject = project;
      mocks.project.loading = false;
      notify();
    });
    mocks.setSelectedItem.mockImplementation((item: SelectedItem | null) => {
      mocks.project.selectedItem = item;
      notify();
    });

    const view = render(<StatefulHost />);
    return { view };
  }

  it("loads project, file tree, initial file, and chat in ONE /bootstrap call with zero duplicate GETs (StrictMode safe)", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    setLastOpenedFile("user-1", "project-1", sampleFile.id);
    mocks.getBootstrap.mockResolvedValue(sampleBootstrap);

    mountEditor(queryClient, { strict: true });

    await waitFor(() => {
      expect(screen.getByTestId("workbench-layout")).toBeInTheDocument();
      expect(screen.getByPlaceholderText("editor:placeholder.contentPlaceholder")).toHaveValue(
        sampleFile.content,
      );
      expect(screen.getByText("正文")).toBeInTheDocument();
      expect(screen.getByText("上次我们写到第七章转折点。")).toBeInTheDocument();
      expect(screen.getByTestId("chat-panel")).toHaveAttribute("data-has-more", "true");
    });

    // Exactly ONE /bootstrap request, ZERO duplicate legacy GETs!
    expect(mocks.getBootstrap).toHaveBeenCalledTimes(1);
    expect(mocks.getBootstrap).toHaveBeenCalledWith("project-1", {
      fileId: sampleFile.id,
      chatLimit: 15,
    });
    expect(mocks.projectGet).not.toHaveBeenCalled();
    expect(mocks.fileGetTree).not.toHaveBeenCalled();
    expect(mocks.fileGet).not.toHaveBeenCalled();
    expect(mocks.getRecentMessages).not.toHaveBeenCalled();

    // User-scoped SWR cache was populated
    const swr = readWorkbenchSwr("user-1", "project-1");
    expect(swr?.project.id).toBe("project-1");
    expect(swr?.initial_file?.updated_at).toBe(sampleFile.updated_at);
  });

  it("gracefully falls back to parallel legacy endpoints when /bootstrap returns 404 (old Railway backend)", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    setLastOpenedFile("user-1", "project-1", sampleFile.id);

    mocks.getBootstrap.mockRejectedValue(new ApiError(404, "Not Found"));
    mocks.projectGet.mockResolvedValue(sampleProject);
    mocks.fileGetTree.mockResolvedValue({ tree: sampleTree });
    mocks.fileGet.mockResolvedValue(sampleFile);
    mocks.getRecentMessages.mockResolvedValue(sampleBootstrap.recent_messages);

    mountEditor(queryClient);

    await waitFor(() => {
      expect(screen.getByTestId("workbench-layout")).toBeInTheDocument();
      expect(screen.getByPlaceholderText("editor:placeholder.contentPlaceholder")).toHaveValue(
        sampleFile.content,
      );
      expect(screen.getByText("正文")).toBeInTheDocument();
      expect(screen.getByText("上次我们写到第七章转折点。")).toBeInTheDocument();
    });

    expect(mocks.getBootstrap).toHaveBeenCalledTimes(1);
    expect(mocks.projectGet).toHaveBeenCalledWith("project-1");
    expect(mocks.fileGetTree).toHaveBeenCalledWith("project-1");
    expect(mocks.fileGet).toHaveBeenCalledWith(sampleFile.id);
    expect(mocks.getRecentMessages).toHaveBeenCalledWith("project-1", 16);
  });

  it("paints cached SWR project/tree/file immediately and silently reconciles fresh server version without extra file GET", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    setLastOpenedFile("user-1", "project-1", sampleFile.id);

    const staleFile: File = {
      ...sampleFile,
      content: "缓存里的旧版第七章。",
      updated_at: "2026-11-26T07:00:00Z",
    };
    writeWorkbenchSwr("user-1", "project-1", {
      project: sampleProject,
      files: sampleTree,
      initial_file: staleFile,
    });

    const pendingBootstrap = deferred<ProjectBootstrapResponse>();
    mocks.getBootstrap.mockReturnValue(pendingBootstrap.promise);

    mountEditor(queryClient);

    // Instant SWR paint BEFORE /bootstrap resolves!
    await waitFor(() => {
      expect(screen.getByTestId("workbench-layout")).toBeInTheDocument();
      expect(screen.getByText("正文")).toBeInTheDocument();
      expect(screen.getByPlaceholderText("editor:placeholder.contentPlaceholder")).toHaveValue(
        "缓存里的旧版第七章。",
      );
    });

    // Fresh /bootstrap arrives with a newer server version -> silent reconciliation, zero fileApi.get calls!
    await act(async () => {
      pendingBootstrap.resolve(sampleBootstrap);
      await pendingBootstrap.promise;
    });

    await waitFor(() => {
      expect(screen.getByPlaceholderText("editor:placeholder.contentPlaceholder")).toHaveValue(
        sampleFile.content,
      );
    });
    expect(mocks.fileGet).not.toHaveBeenCalled();
  });

  it("NEVER resolves an unsaved draft against stale SWR data; waits for authoritative /bootstrap and shows conflict recovery", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    setLastOpenedFile("user-1", "project-1", sampleFile.id);

    // SWR has older version T1 (matching draft's baseUpdatedAt), while server has moved to T2!
    const staleFile: File = {
      ...sampleFile,
      content: "旧版正文 T1",
      updated_at: "2026-11-26T07:00:00Z",
    };
    writeWorkbenchSwr("user-1", "project-1", {
      project: sampleProject,
      files: sampleTree,
      initial_file: staleFile,
    });
    writeEditorDraftSnapshot(
      localStorage,
      createEditorDraftSnapshot({
        userId: "user-1",
        projectId: "project-1",
        fileId: sampleFile.id,
        title: sampleFile.title,
        content: "作者在 T1 上未保存的草稿。",
        baseUpdatedAt: staleFile.updated_at,
        reason: "page-exit",
      }),
    );

    const pendingBootstrap = deferred<ProjectBootstrapResponse>();
    mocks.getBootstrap.mockReturnValue(pendingBootstrap.promise);

    mountEditor(queryClient);

    // Project shell + file tree paint from SWR, but editor must NOT prematurely load stale T1
    // and auto-overwrite/auto-recover before authoritative T2 arrives.
    await waitFor(() => {
      expect(screen.getByTestId("workbench-layout")).toBeInTheDocument();
    });
    expect(screen.queryByPlaceholderText("editor:placeholder.contentPlaceholder")).not.toBeInTheDocument();

    // Fresh /bootstrap arrives with newer server version T2
    await act(async () => {
      pendingBootstrap.resolve(sampleBootstrap);
      await pendingBootstrap.promise;
    });

    // Explicit conflict recovery choice is shown; server T2 text is preserved (no silent overwrite).
    await waitFor(() => {
      expect(screen.getByPlaceholderText("editor:placeholder.contentPlaceholder")).toHaveValue(
        sampleFile.content,
      );
      expect(screen.getByRole("button", { name: "editor:draftRecovery.restore" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "editor:draftRecovery.discard" })).toBeInTheDocument();
    });
    expect(mocks.fileGet).not.toHaveBeenCalled();
  });

  it("scopes SWR cache by userId, tolerates storage quota errors, and evicts all entries on logout", () => {
    writeWorkbenchSwr("user-a", "project-1", {
      project: sampleProject,
      files: sampleTree,
      initial_file: sampleFile,
    });
    writeAuthSessionUserSnapshot(mocks.user);

    // User isolation: user-b cannot read user-a's cached entry
    expect(readWorkbenchSwr("user-b", "project-1")).toBeNull();
    expect(readWorkbenchSwr("user-a", "project-1")?.project.id).toBe("project-1");
    expect(readAuthSessionUserSnapshot()?.id).toBe("user-1");

    // clearAuthStorage evicts both SWR cache and auth session snapshot
    clearAuthStorage("user_initiated");
    expect(readWorkbenchSwr("user-a", "project-1")).toBeNull();
    expect(readAuthSessionUserSnapshot()).toBeNull();
    expect(sessionStorage.getItem(AUTH_SESSION_USER_KEY)).toBeNull();

    // Storage quota error degrades cleanly without throwing
    const setItemSpy = vi.spyOn(window.sessionStorage, "setItem").mockImplementation(() => {
      throw new DOMException("QuotaExceededError", "QuotaExceededError");
    });
    try {
      expect(
        writeWorkbenchSwr("user-a", "project-1", {
          project: sampleProject,
          files: sampleTree,
          initial_file: sampleFile,
        }),
      ).toBe(false);
    } finally {
      setItemSpy.mockRestore();
      clearWorkbenchSwrCache();
    }
  });

  it("never rolls back a newer saved edit when a slower /bootstrap returns older T1 (P1-1)", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    setLastOpenedFile("user-1", "project-1", sampleFile.id);

    const t1File: File = {
      ...sampleFile,
      content: "旧版正文 T1。",
      updated_at: "2026-11-26T07:00:00Z",
    };
    const t3SavedFile: File = {
      ...sampleFile,
      content: "作者刚刚保存的新版正文 T3。",
      updated_at: "2026-11-26T09:00:00Z",
    };
    writeWorkbenchSwr("user-1", "project-1", {
      project: sampleProject,
      files: sampleTree,
      initial_file: t1File,
    });

    const slowBootstrap = deferred<ProjectBootstrapResponse>();
    mocks.getBootstrap.mockReturnValue(slowBootstrap.promise);
    mocks.fileUpdate.mockResolvedValue(t3SavedFile);

    mountEditor(queryClient);

    const editorInput = await screen.findByPlaceholderText("editor:placeholder.contentPlaceholder");
    expect(editorInput).toHaveValue("旧版正文 T1。");

    // Author edits and saves via the Save button BEFORE slow /bootstrap returns!
    fireEvent.change(editorInput, { target: { value: "作者刚刚保存的新版正文 T3。" } });
    fireEvent.click(screen.getByRole("button", { name: "editor:save" }));

    await waitFor(() => {
      expect(mocks.fileUpdate).toHaveBeenCalledTimes(1);
    });

    // Slow /bootstrap finally resolves with pre-edit T1 -> MUST NOT roll back T3!
    await act(async () => {
      slowBootstrap.resolve({
        ...sampleBootstrap,
        initial_file: t1File,
      });
      await slowBootstrap.promise;
    });
    await act(async () => Promise.resolve());

    expect(screen.getByPlaceholderText("editor:placeholder.contentPlaceholder")).toHaveValue(
      "作者刚刚保存的新版正文 T3。",
    );
    expect(screen.queryByRole("button", { name: "editor:draftRecovery.restore" })).not.toBeInTheDocument();
  });

  it("delivers fresh V2 when /bootstrap resolves before Editor finishes mounting SWR V1 (P1-2)", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    setLastOpenedFile("user-1", "project-1", sampleFile.id);

    const v1File: File = {
      ...sampleFile,
      content: "SWR 旧版 V1。",
      updated_at: "2026-11-26T07:00:00Z",
    };
    writeWorkbenchSwr("user-1", "project-1", {
      project: sampleProject,
      files: sampleTree,
      initial_file: v1File,
    });

    // /bootstrap resolves immediately (microtask) with fresher V2 before Editor mounts/commits V1.
    mocks.getBootstrap.mockResolvedValue(sampleBootstrap);

    mountEditor(queryClient);

    await waitFor(() => {
      expect(screen.getByPlaceholderText("editor:placeholder.contentPlaceholder")).toHaveValue(
        sampleFile.content,
      );
    });
    expect(mocks.fileGet).not.toHaveBeenCalled();
  });

  it("refetches fresh /bootstrap on A→B→A project revisit and clears stale selection on switch (P1-3)", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const projectB: Project = {
      id: "project-2",
      name: "第二个作品",
      project_type: "short",
      created_at: "2026-11-02T00:00:00Z",
      updated_at: "2026-11-26T08:00:00Z",
    };
    const fileB: File = {
      id: "file-b1",
      project_id: "project-2",
      title: "B 第一章",
      content: "作品 B 正文。",
      file_type: "draft",
      parent_id: null,
      order: 1,
      file_metadata: null,
      created_at: "2026-11-02T00:00:00Z",
      updated_at: "2026-11-26T08:00:00Z",
    };
    const updatedFileA: File = {
      ...sampleFile,
      content: "回到作品 A 后拉到的最新正文 V2。",
      updated_at: "2026-11-26T10:00:00Z",
    };

    setLastOpenedFile("user-1", "project-1", sampleFile.id);
    setLastOpenedFile("user-1", "project-2", fileB.id);

    mocks.getBootstrap
      .mockResolvedValueOnce(sampleBootstrap)
      .mockResolvedValueOnce({
        project: projectB,
        files: [],
        initial_file: fileB,
        chat_session: null,
        recent_messages: [],
        has_more_messages: false,
      })
      .mockResolvedValueOnce({
        ...sampleBootstrap,
        initial_file: updatedFileA,
      });

    const listeners = new Set<() => void>();
    const notify = () => listeners.forEach((l) => l());
    let navigateFn: ((to: string) => void) | null = null;

    function NavHarness() {
      const nav = useNavigate();
      React.useLayoutEffect(() => {
        navigateFn = (to: string) => nav(to);
      }, [nav]);
      return null;
    }

    function SwitchHost() {
      const [rev, setRev] = React.useState(0);
      React.useLayoutEffect(() => {
        const l = () => setRev((v) => v + 1);
        listeners.add(l);
        return () => {
          listeners.delete(l);
        };
      }, []);
      return (
        <QueryClientProvider client={queryClient}>
          <ProjectRevContext.Provider value={rev}>
            <MemoryRouter initialEntries={["/project/project-1"]}>
              <NavHarness />
              <Routes>
                <Route path="/project/:projectId" element={<ProjectEditor />} />
              </Routes>
            </MemoryRouter>
          </ProjectRevContext.Provider>
        </QueryClientProvider>
      );
    }

    mocks.setCurrentProjectId.mockImplementation((id: string | null) => {
      const prev = mocks.project.currentProjectId;
      mocks.project.currentProjectId = id;
      mocks.project.currentProject = mocks.project.projects.find((p) => p.id === id) ?? null;
      if (prev !== id) {
        mocks.project.selectedItem = null;
      }
      notify();
    });
    mocks.hydrateProject.mockImplementation((project: Project) => {
      const rest = mocks.project.projects.filter((p) => p.id !== project.id);
      mocks.project.projects = [...rest, project];
      mocks.project.currentProjectId = project.id ?? null;
      mocks.project.currentProject = project;
      mocks.project.loading = false;
      notify();
    });
    mocks.setSelectedItem.mockImplementation((item: SelectedItem | null) => {
      mocks.project.selectedItem = item;
      notify();
    });

    render(<SwitchHost />);

    // 1) Initial load of Project A -> V1
    await waitFor(() => {
      expect(screen.getByPlaceholderText("editor:placeholder.contentPlaceholder")).toHaveValue(
        sampleFile.content,
      );
    });
    expect(mocks.getBootstrap).toHaveBeenCalledTimes(1);

    // 2) Switch A -> B: even with A's selection active before switch, B loads its own file!
    await act(async () => {
      navigateFn?.("/project/project-2");
    });
    await waitFor(() => {
      expect(screen.getByPlaceholderText("editor:placeholder.contentPlaceholder")).toHaveValue(
        fileB.content,
      );
    });
    expect(mocks.getBootstrap).toHaveBeenCalledTimes(2);

    // 3) Switch B -> A within 15s: MUST refetch fresh /bootstrap (NOT reuse stale consumed V1)!
    await act(async () => {
      navigateFn?.("/project/project-1");
    });
    await waitFor(() => {
      expect(screen.getByPlaceholderText("editor:placeholder.contentPlaceholder")).toHaveValue(
        updatedFileA.content,
      );
    });
    expect(mocks.getBootstrap).toHaveBeenCalledTimes(3);
  });
});

describe("AuthContext non-blocking session snapshot boot and 401 eviction", () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });

  afterEach(() => {
    global.fetch = originalFetch;
    localStorage.clear();
    sessionStorage.clear();
  });

  it("unblocks immediately from sessionStorage snapshot even past 5m TTL, and evicts session + SWR cache on 401", async () => {
    const actualAuth = await vi.importActual<typeof import("../contexts/AuthContext")>(
      "../contexts/AuthContext",
    );
    const { AuthProvider, useAuth } = actualAuth;

    const expiredTs = Date.now() - 10 * 60 * 1000;
    localStorage.setItem("access_token", "expired-access-token");
    localStorage.setItem("user", JSON.stringify(mocks.user));
    localStorage.setItem("auth_validated_at", String(expiredTs));
    writeAuthSessionUserSnapshot(mocks.user);
    writeWorkbenchSwr(mocks.user.id, "project-1", {
      project: sampleProject,
      files: sampleTree,
      initial_file: sampleFile,
    });

    const meResponse = deferred<Response>();
    global.fetch = vi.fn().mockReturnValue(meResponse.promise) as unknown as typeof fetch;

    const { result } = renderHook(() => useAuth(), {
      wrapper: ({ children }: { children: React.ReactNode }) => (
        <AuthProvider>{children}</AuthProvider>
      ),
    });

    // Non-blocking boot: loading is false and user is immediately available while /api/auth/me is in flight!
    expect(result.current.loading).toBe(false);
    expect(result.current.user?.id).toBe(mocks.user.id);

    // /api/auth/me returns 401 -> immediately evict user, tokens, session snapshot, and SWR cache!
    await act(async () => {
      meResponse.resolve(new Response(null, { status: 401 }));
      await meResponse.promise;
    });

    await waitFor(() => {
      expect(result.current.user).toBeNull();
    });
    expect(localStorage.getItem("access_token")).toBeNull();
    expect(readAuthSessionUserSnapshot()).toBeNull();
    expect(readWorkbenchSwr(mocks.user.id, "project-1")).toBeNull();
  });

  it("clears mismatched session snapshot + SWR cache on cross-tab switch and rejects orphan session snapshot (P2-1)", async () => {
    const actualAuth = await vi.importActual<typeof import("../contexts/AuthContext")>(
      "../contexts/AuthContext",
    );
    const { AuthProvider, useAuth } = actualAuth;

    const userB = { ...mocks.user, id: "user-b", username: "writerB", email: "b@example.com" };
    // Tab had User A's session snapshot + SWR cache, while another tab logged in as User B within 5m TTL.
    writeAuthSessionUserSnapshot(mocks.user);
    writeWorkbenchSwr(mocks.user.id, "project-1", {
      project: sampleProject,
      files: sampleTree,
      initial_file: sampleFile,
    });
    localStorage.setItem("access_token", "user-b-token");
    localStorage.setItem("user", JSON.stringify(userB));
    localStorage.setItem("auth_validated_at", String(Date.now()));

    global.fetch = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify(userB), { status: 200 })) as unknown as typeof fetch;

    const { result, unmount } = renderHook(() => useAuth(), {
      wrapper: ({ children }: { children: React.ReactNode }) => (
        <AuthProvider>{children}</AuthProvider>
      ),
    });

    expect(result.current.user?.id).toBe("user-b");
    expect(readWorkbenchSwr(mocks.user.id, "project-1")).toBeNull();
    unmount();

    // Orphan sessionStorage snapshot with missing localStorage.user must NOT bypass /api/auth/me!
    localStorage.clear();
    sessionStorage.clear();
    localStorage.setItem("access_token", "orphan-token");
    writeAuthSessionUserSnapshot(mocks.user);
    const pendingMe = deferred<Response>();
    global.fetch = vi.fn().mockReturnValue(pendingMe.promise) as unknown as typeof fetch;

    const orphanView = renderHook(() => useAuth(), {
      wrapper: ({ children }: { children: React.ReactNode }) => (
        <AuthProvider>{children}</AuthProvider>
      ),
    });

    expect(orphanView.result.current.loading).toBe(true);
    expect(orphanView.result.current.user).toBeNull();
    expect(readAuthSessionUserSnapshot()).toBeNull();

    await act(async () => {
      pendingMe.resolve(new Response(JSON.stringify(mocks.user), { status: 200 }));
      await pendingMe.promise;
    });
    await waitFor(() => expect(orphanView.result.current.loading).toBe(false));
    orphanView.unmount();
  });
});
