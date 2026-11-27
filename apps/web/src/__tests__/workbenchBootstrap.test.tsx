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
  MAX_WORKBENCH_SWR_ENTRY_CHARS,
  MAX_WORKBENCH_SWR_PROJECTS,
  WORKBENCH_SWR_TTL_MS,
  clearAuthSessionUserSnapshot,
  clearWorkbenchSwrCache,
  getWorkbenchSwrKey,
  invalidateWorkbenchSwrInitialFile,
  readAuthSessionUserSnapshot,
  readWorkbenchSwr,
  removeWorkbenchSwr,
  updateWorkbenchSwrInitialFile,
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
  BOOTSTRAP_HANDOFF_MAX_AGE_MS,
  WORKBENCH_BOOTSTRAP_FAILED_EVENT,
  WORKBENCH_FRESH_FILE_EVENT,
  clearWorkbenchSwrSyncRequired,
  consumeWorkbenchSwrSyncRequired,
  evictProjectBootstrapHandoff,
  fetchProjectBootstrapWithFallback,
  isBootstrapEndpointMissingError,
  isValidProjectBootstrapResponse,
  markBootstrappedChatConsumed,
  markBootstrappedFileTreeConsumed,
  markWorkbenchSwrSyncRequired,
  peekBootstrappedChat,
  peekBootstrappedFileTree,
  startProjectBootstrap,
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
    return { view, notify };
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

  it("gracefully falls back to parallel legacy endpoints when /bootstrap returns an invalid payload shape", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    setLastOpenedFile("user-1", "project-1", sampleFile.id);

    // Simulate a legacy proxy or catch-all route returning a raw project object without { project, files, recent_messages }.
    mocks.getBootstrap.mockResolvedValue(sampleProject as unknown as ProjectBootstrapResponse);
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
    const originalSessionStorage = window.sessionStorage;
    const quotaFailingStorage: Storage = {
      get length() {
        return 0;
      },
      clear: vi.fn(),
      getItem: vi.fn(() => null),
      key: vi.fn(() => null),
      removeItem: vi.fn(),
      setItem: vi.fn(() => {
        throw new DOMException("QuotaExceededError", "QuotaExceededError");
      }),
    };
    vi.stubGlobal("sessionStorage", quotaFailingStorage);
    try {
      expect(
        writeWorkbenchSwr("user-a", "project-1", {
          project: sampleProject,
          files: sampleTree,
          initial_file: sampleFile,
        }),
      ).toBe(false);
    } finally {
      vi.stubGlobal("sessionStorage", originalSessionStorage);
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

  it("writes through saved edits to workbenchSwrCache so reload within 15m never flashes pre-save text (P0-3a)", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    setLastOpenedFile("user-1", "project-1", sampleFile.id);

    const v1File: File = {
      ...sampleFile,
      content: "旧版正文 V1。",
      updated_at: "2026-11-26T07:00:00Z",
    };
    const v2SavedFile: File = {
      ...sampleFile,
      content: "保存后的最新正文 V2。",
      updated_at: "2026-11-26T09:30:00Z",
    };

    mocks.getBootstrap.mockResolvedValueOnce({
      ...sampleBootstrap,
      initial_file: v1File,
    });
    mocks.fileUpdate.mockResolvedValueOnce(v2SavedFile);

    const firstMount = mountEditor(queryClient);

    const editorInput = await screen.findByPlaceholderText("editor:placeholder.contentPlaceholder");
    expect(editorInput).toHaveValue("旧版正文 V1。");

    // Edit and save V2
    fireEvent.change(editorInput, { target: { value: "保存后的最新正文 V2。" } });
    fireEvent.click(screen.getByRole("button", { name: "editor:save" }));

    await waitFor(() => {
      expect(mocks.fileUpdate).toHaveBeenCalledTimes(1);
    });

    // SWR cache immediately reflects V2!
    const cachedAfterSave = readWorkbenchSwr("user-1", "project-1");
    expect(cachedAfterSave?.initial_file?.content).toBe("保存后的最新正文 V2。");
    expect(cachedAfterSave?.initial_file?.updated_at).toBe(v2SavedFile.updated_at);

    // Simulate reload within 15 minutes while /bootstrap is still in flight
    firstMount.view.unmount();
    const reloadQueryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    mocks.project.projects = [];
    mocks.project.currentProject = null;
    mocks.project.currentProjectId = null;
    mocks.project.loading = true;
    mocks.project.selectedItem = null;

    const pendingReloadBootstrap = deferred<ProjectBootstrapResponse>();
    mocks.getBootstrap.mockReturnValueOnce(pendingReloadBootstrap.promise);

    mountEditor(reloadQueryClient);

    // Instant SWR paint shows V2 — NEVER flashes stale pre-save V1!
    await waitFor(() => {
      expect(screen.getByPlaceholderText("editor:placeholder.contentPlaceholder")).toHaveValue(
        "保存后的最新正文 V2。",
      );
    });
  });

  it("triggers fresh server sync and surfaces serverSyncIssue banner when /bootstrap fails transiently after SWR paint (P0-3b)", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    setLastOpenedFile("user-1", "project-1", sampleFile.id);

    const staleFile: File = {
      ...sampleFile,
      content: "缓存里的旧版正文。",
      updated_at: "2026-11-26T07:00:00Z",
    };
    writeWorkbenchSwr("user-1", "project-1", {
      project: sampleProject,
      files: sampleTree,
      initial_file: staleFile,
    });

    // Case 1: /bootstrap fails with 502 after SWR paints -> Editor triggers fileApi.get and reconciles fresh V2!
    const pendingBootstrap = deferred<ProjectBootstrapResponse>();
    mocks.getBootstrap.mockReturnValueOnce(pendingBootstrap.promise);
    mocks.fileGet.mockResolvedValueOnce(sampleFile);

    const firstView = mountEditor(queryClient);

    await waitFor(() => {
      expect(screen.getByPlaceholderText("editor:placeholder.contentPlaceholder")).toHaveValue(
        "缓存里的旧版正文。",
      );
    });

    await act(async () => {
      pendingBootstrap.reject(new ApiError(502, "Bad Gateway"));
      await Promise.resolve();
    });

    await waitFor(() => {
      expect(mocks.fileGet).toHaveBeenCalledWith(sampleFile.id);
      expect(screen.getByPlaceholderText("editor:placeholder.contentPlaceholder")).toHaveValue(
        sampleFile.content,
      );
    });
    expect(readWorkbenchSwr("user-1", "project-1")?.initial_file?.content).toBe(sampleFile.content);
    firstView.view.unmount();

    // Case 2: Both /bootstrap AND fallback fileApi.get fail transiently -> surfaces serverSyncIssue retry banner!
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const retryQueryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      mocks.project.projects = [];
      mocks.project.currentProject = null;
      mocks.project.currentProjectId = null;
      mocks.project.loading = true;
      mocks.project.selectedItem = null;
      writeWorkbenchSwr("user-1", "project-1", {
        project: sampleProject,
        files: sampleTree,
        initial_file: staleFile,
      });

      mocks.getBootstrap.mockRejectedValueOnce(new ApiError(503, "Service Unavailable"));
      mocks.fileGet.mockRejectedValue(new ApiError(503, "Service Unavailable"));

      const retryView = mountEditor(retryQueryClient);
      try {
        await waitFor(() => {
          expect(screen.getByPlaceholderText("editor:placeholder.contentPlaceholder")).toHaveValue(
            "缓存里的旧版正文。",
          );
        });

        // Advance past SERVER_SYNC_RETRY_DELAYS_MS ([1000, 3000])
        await act(async () => {
          await vi.advanceTimersByTimeAsync(5000);
        });

        await waitFor(() => {
          expect(screen.getByText("editor:serverSyncFailed.message")).toBeInTheDocument();
          expect(screen.getByRole("button", { name: "editor:serverSyncFailed.retry" })).toBeInTheDocument();
        });
      } finally {
        retryView.view.unmount();
      }
    } finally {
      vi.useRealTimers();
    }
  });

  it("evicts SWR cache and renders projectNotFound even when SWR hydrated currentProject on /bootstrap 404/403 (P1-1)", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    setLastOpenedFile("user-1", "project-1", sampleFile.id);

    writeWorkbenchSwr("user-1", "project-1", {
      project: sampleProject,
      files: sampleTree,
      initial_file: sampleFile,
    });

    const pendingBootstrap = deferred<ProjectBootstrapResponse>();
    mocks.getBootstrap.mockReturnValueOnce(pendingBootstrap.promise);

    mountEditor(queryClient);

    // SWR hydrates currentProject immediately
    await waitFor(() => {
      expect(screen.getByTestId("workbench-layout")).toBeInTheDocument();
    });

    // Server definitively reports project deleted (404 ERR_PROJECT_NOT_FOUND)
    await act(async () => {
      pendingBootstrap.reject(new ApiError(404, "ERR_PROJECT_NOT_FOUND"));
      await Promise.resolve();
    });

    // SWR entry is evicted AND projectNotFound screen is rendered (not hidden behind SWR currentProject)!
    await waitFor(() => {
      expect(screen.getByText("projectNotFound")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("workbench-layout")).not.toBeInTheDocument();
    expect(readWorkbenchSwr("user-1", "project-1")).toBeNull();
  });

  it("clears auth storage and SWR cache when /bootstrap returns 400 ERR_AUTH_INACTIVE_USER (P0-1)", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    localStorage.setItem("access_token", "deactivated-token");
    writeAuthSessionUserSnapshot(mocks.user);
    writeWorkbenchSwr("user-1", "project-1", {
      project: sampleProject,
      files: sampleTree,
      initial_file: sampleFile,
    });
    const logoutListener = vi.fn();
    window.addEventListener("auth:logout", logoutListener);

    try {
      mocks.getBootstrap.mockRejectedValueOnce(new ApiError(400, "ERR_AUTH_INACTIVE_USER"));

      mountEditor(queryClient);

      await waitFor(() => {
        expect(logoutListener).toHaveBeenCalledTimes(1);
      });
      expect(localStorage.getItem("access_token")).toBeNull();
      expect(readAuthSessionUserSnapshot()).toBeNull();
      expect(readWorkbenchSwr("user-1", "project-1")).toBeNull();
    } finally {
      window.removeEventListener("auth:logout", logoutListener);
    }
  });

  it("registers bootstrap handoff before child useEffects when navigating from Dashboard to an already-active project (P1-4)", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    setLastOpenedFile("user-1", "project-1", sampleFile.id);

    // Simulate Dashboard -> previously-active /project/project-1 where ProjectContext ALREADY has
    // currentProject & currentProjectId set on frame 1 so FileTreeProbe & ChatPanel mount immediately!
    mocks.project.projects = [sampleProject];
    mocks.project.currentProject = sampleProject;
    mocks.project.currentProjectId = sampleProject.id;
    mocks.project.loading = false;
    mocks.project.selectedItem = null;

    mocks.getBootstrap.mockResolvedValueOnce(sampleBootstrap);

    mountEditor(queryClient, { strict: true });

    await waitFor(() => {
      expect(screen.getByTestId("workbench-layout")).toBeInTheDocument();
      expect(screen.getByText("正文")).toBeInTheDocument();
      expect(screen.getByText("上次我们写到第七章转折点。")).toBeInTheDocument();
      expect(screen.getByPlaceholderText("editor:placeholder.contentPlaceholder")).toHaveValue(
        sampleFile.content,
      );
    });

    // Exactly ONE /bootstrap call — ZERO duplicate GET /file-tree, GET /recent, or GET /files/:id!
    expect(mocks.getBootstrap).toHaveBeenCalledTimes(1);
    expect(mocks.fileGetTree).not.toHaveBeenCalled();
    expect(mocks.getRecentMessages).not.toHaveBeenCalled();
    expect(mocks.fileGet).not.toHaveBeenCalled();
  });

  it("consumes preloadedFileCache on fresh-file reconciliation and save so file-1 -> file-2 -> file-1 within 10s never rolls back T3", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const file1T1: File = {
      ...sampleFile,
      id: "file-1",
      title: "第一章",
      content: "SWR 缓存里的旧正文 T1。",
      updated_at: "2026-11-26T07:00:00Z",
    };
    const file1T2: File = {
      ...file1T1,
      content: "Bootstrap 返回的权威正文 T2。",
      updated_at: "2026-11-26T08:00:00Z",
    };
    const file1T3: File = {
      ...file1T1,
      content: "作者新保存的最新正文 T3。",
      updated_at: "2026-11-26T09:00:00Z",
    };
    const file2: File = {
      ...sampleFile,
      id: "file-2",
      title: "第二章",
      content: "第二章正文。",
      updated_at: "2026-11-26T08:30:00Z",
    };

    setLastOpenedFile("user-1", "project-1", file1T1.id);
    writeWorkbenchSwr("user-1", "project-1", {
      project: sampleProject,
      files: sampleTree,
      initial_file: file1T1,
    });

    const pendingBootstrap = deferred<ProjectBootstrapResponse>();
    mocks.getBootstrap.mockReturnValueOnce(pendingBootstrap.promise);
    mocks.fileUpdate.mockResolvedValueOnce(file1T3);
    mocks.fileGet.mockImplementation(async (id: string) => {
      if (id === file2.id) return file2;
      if (id === file1T1.id) return file1T3;
      throw new ApiError(404, "ERR_FILE_NOT_FOUND");
    });

    mountEditor(queryClient);

    // 1) SWR paints T1 immediately
    await waitFor(() => {
      expect(screen.getByPlaceholderText("editor:placeholder.contentPlaceholder")).toHaveValue(
        file1T1.content,
      );
    });

    // 2) /bootstrap resolves with T2 -> Editor reconciles to T2 via WORKBENCH_FRESH_FILE_EVENT
    await act(async () => {
      pendingBootstrap.resolve({
        ...sampleBootstrap,
        initial_file: file1T2,
      });
      await pendingBootstrap.promise;
    });
    await waitFor(() => {
      expect(screen.getByPlaceholderText("editor:placeholder.contentPlaceholder")).toHaveValue(
        file1T2.content,
      );
    });

    // 3) Author edits and saves T3
    const editorInput = screen.getByPlaceholderText("editor:placeholder.contentPlaceholder");
    fireEvent.change(editorInput, { target: { value: file1T3.content } });
    fireEvent.click(screen.getByRole("button", { name: "editor:save" }));
    await waitFor(() => {
      expect(mocks.fileUpdate).toHaveBeenCalledTimes(1);
    });

    // 4) Switch file-1 -> file-2 -> file-1 within 10s TTL
    act(() => {
      mocks.setSelectedItem({ id: file2.id, title: file2.title, type: "draft" });
    });
    await waitFor(() => {
      expect(screen.getByPlaceholderText("editor:placeholder.contentPlaceholder")).toHaveValue(
        file2.content,
      );
    });

    act(() => {
      mocks.setSelectedItem({ id: file1T1.id, title: file1T1.title, type: "draft" });
    });
    await waitFor(() => {
      expect(screen.getByPlaceholderText("editor:placeholder.contentPlaceholder")).toHaveValue(
        file1T3.content,
      );
    });

    // Must fetch live T3 from server rather than rolling back to dangling preloadedFileCache T2!
    expect(mocks.fileGet).toHaveBeenCalledWith(file1T1.id);
  });

  it("keeps SWR fresh-file reconciliation and error recovery alive when /me refreshes user object while /bootstrap is in flight (A3)", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const file1T1: File = {
      ...sampleFile,
      id: "file-1",
      title: "第一章",
      content: "SWR 缓存里的旧正文 T1。",
      updated_at: "2026-11-26T07:00:00Z",
    };
    const file1T2: File = {
      ...file1T1,
      content: "Bootstrap 返回的新鲜正文 T2。",
      updated_at: "2026-11-26T08:00:00Z",
    };

    const originalUser = mocks.user;
    const originalSetCurrentProjectId = mocks.setCurrentProjectId;
    const freshFileEvents: Array<CustomEvent> = [];
    const failedEvents: Array<CustomEvent> = [];
    const onFreshFile = (event: Event) => freshFileEvents.push(event as CustomEvent);
    const onBootstrapFailed = (event: Event) => failedEvents.push(event as CustomEvent);
    window.addEventListener(WORKBENCH_FRESH_FILE_EVENT, onFreshFile);
    window.addEventListener(WORKBENCH_BOOTSTRAP_FAILED_EVENT, onBootstrapFailed);

    try {
      // Case 1: /bootstrap resolves with T2 after background /me updates user object reference (same id)
      setLastOpenedFile("user-1", "project-1", file1T1.id);
      writeWorkbenchSwr("user-1", "project-1", {
        project: sampleProject,
        files: sampleTree,
        initial_file: file1T1,
      });

      const pendingBootstrap = deferred<ProjectBootstrapResponse>();
      mocks.getBootstrap.mockReturnValueOnce(pendingBootstrap.promise);

      const { view, notify } = mountEditor(queryClient);

      await waitFor(() => {
        expect(screen.getByPlaceholderText("editor:placeholder.contentPlaceholder")).toHaveValue(
          file1T1.content,
        );
      });

      // Simulate background /api/auth/me resolving with a new user object (same id) and a new callback ref
      // while /bootstrap is still in flight.
      const prevImpl = mocks.setCurrentProjectId.getMockImplementation();
      mocks.user = { ...originalUser, updated_at: "2026-11-27T12:00:00Z" };
      mocks.setCurrentProjectId = vi.fn((id: string | null) => prevImpl?.(id));
      act(() => {
        notify();
      });

      await act(async () => {
        pendingBootstrap.resolve({
          ...sampleBootstrap,
          initial_file: file1T2,
        });
        await pendingBootstrap.promise;
      });

      await waitFor(() => {
        expect(freshFileEvents.length).toBe(1);
        expect(screen.getByPlaceholderText("editor:placeholder.contentPlaceholder")).toHaveValue(
          file1T2.content,
        );
      });
      view.unmount();

      // Case 2: /bootstrap rejects with 503 after mid-flight /me refresh -> error recovery still fires!
      const queryClient2 = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      mocks.project.projects = [];
      mocks.project.currentProject = null;
      mocks.project.currentProjectId = null;
      mocks.project.loading = true;
      mocks.project.selectedItem = null;
      mocks.setCurrentProjectId = originalSetCurrentProjectId;
      mocks.user = originalUser;

      writeWorkbenchSwr("user-1", "project-1", {
        project: sampleProject,
        files: sampleTree,
        initial_file: file1T1,
      });

      const failingBootstrap = deferred<ProjectBootstrapResponse>();
      mocks.getBootstrap.mockReturnValueOnce(failingBootstrap.promise);
      mocks.fileGet.mockResolvedValueOnce(file1T2);

      const { view: view2, notify: notify2 } = mountEditor(queryClient2);

      await waitFor(() => {
        expect(screen.getByPlaceholderText("editor:placeholder.contentPlaceholder")).toHaveValue(
          file1T1.content,
        );
      });

      const prevImpl2 = mocks.setCurrentProjectId.getMockImplementation();
      mocks.user = { ...originalUser, updated_at: "2026-11-27T12:05:00Z" };
      mocks.setCurrentProjectId = vi.fn((id: string | null) => prevImpl2?.(id));
      act(() => {
        notify2();
      });

      await act(async () => {
        failingBootstrap.reject(new ApiError(503, "Service Unavailable"));
        await failingBootstrap.promise.catch(() => {});
      });

      await waitFor(() => {
        expect(failedEvents.length).toBe(1);
        expect(screen.getByPlaceholderText("editor:placeholder.contentPlaceholder")).toHaveValue(
          file1T2.content,
        );
      });
      view2.unmount();
    } finally {
      window.removeEventListener(WORKBENCH_FRESH_FILE_EVENT, onFreshFile);
      window.removeEventListener(WORKBENCH_BOOTSTRAP_FAILED_EVENT, onBootstrapFailed);
      mocks.user = originalUser;
      mocks.setCurrentProjectId = originalSetCurrentProjectId;
    }
  });

  it("propagates fresher SWR initial_file from startProjectBootstrap and clears preloaded cache when slow /bootstrap returns T1 after T3 save", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const file1T1: File = {
      ...sampleFile,
      id: "file-1",
      title: "第一章",
      content: "旧正文 T1。",
      updated_at: "2026-11-26T07:00:00Z",
    };
    const file1T3: File = {
      ...file1T1,
      content: "慢速 bootstrap 返回前已保存的正文 T3。",
      updated_at: "2026-11-26T09:00:00Z",
    };
    const file2: File = {
      ...sampleFile,
      id: "file-2",
      title: "第二章",
      content: "第二章正文。",
      updated_at: "2026-11-26T08:30:00Z",
    };

    setLastOpenedFile("user-1", "project-1", file1T1.id);
    writeWorkbenchSwr("user-1", "project-1", {
      project: sampleProject,
      files: sampleTree,
      initial_file: file1T1,
    });

    const slowBootstrap = deferred<ProjectBootstrapResponse>();
    mocks.getBootstrap.mockReturnValueOnce(slowBootstrap.promise);
    mocks.fileUpdate.mockResolvedValueOnce(file1T3);
    mocks.fileGet.mockImplementation(async (id: string) => {
      if (id === file2.id) return file2;
      if (id === file1T1.id) return file1T3;
      throw new ApiError(404, "ERR_FILE_NOT_FOUND");
    });

    mountEditor(queryClient);

    const editorInput = await screen.findByPlaceholderText("editor:placeholder.contentPlaceholder");
    expect(editorInput).toHaveValue(file1T1.content);

    // Save T3 while /bootstrap is still in flight
    fireEvent.change(editorInput, { target: { value: file1T3.content } });
    fireEvent.click(screen.getByRole("button", { name: "editor:save" }));
    await waitFor(() => {
      expect(mocks.fileUpdate).toHaveBeenCalledTimes(1);
    });

    // Slow /bootstrap resolves with older T1
    await act(async () => {
      slowBootstrap.resolve({
        ...sampleBootstrap,
        initial_file: file1T1,
      });
      await slowBootstrap.promise;
    });
    await act(async () => Promise.resolve());

    // Switch file-1 -> file-2 -> file-1 within 10s TTL: must NEVER roll back to T1!
    act(() => {
      mocks.setSelectedItem({ id: file2.id, title: file2.title, type: "draft" });
    });
    await waitFor(() => {
      expect(screen.getByPlaceholderText("editor:placeholder.contentPlaceholder")).toHaveValue(
        file2.content,
      );
    });

    act(() => {
      mocks.setSelectedItem({ id: file1T1.id, title: file1T1.title, type: "draft" });
    });
    await waitFor(() => {
      expect(screen.getByPlaceholderText("editor:placeholder.contentPlaceholder")).toHaveValue(
        file1T3.content,
      );
    });
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

  it("evicts deactivated account (400 ERR_AUTH_INACTIVE_USER) during 24h non-blocking auth boot and cache-miss init (P0-1)", async () => {
    const actualAuth = await vi.importActual<typeof import("../contexts/AuthContext")>(
      "../contexts/AuthContext",
    );
    const { AuthProvider, useAuth } = actualAuth;

    // 1) 24h non-blocking sessionStorage snapshot boot -> background /api/auth/me returns 400 ERR_AUTH_INACTIVE_USER
    const expiredTs = Date.now() - 10 * 60 * 1000;
    localStorage.setItem("access_token", "deactivated-user-token");
    localStorage.setItem("refresh_token", "deactivated-user-refresh");
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

    const { result, unmount } = renderHook(() => useAuth(), {
      wrapper: ({ children }: { children: React.ReactNode }) => (
        <AuthProvider>{children}</AuthProvider>
      ),
    });

    // Non-blocking boot initially painted cached user
    expect(result.current.loading).toBe(false);
    expect(result.current.user?.id).toBe(mocks.user.id);

    await act(async () => {
      meResponse.resolve(
        new Response(
          JSON.stringify({
            detail: "ERR_AUTH_INACTIVE_USER",
            error_code: "ERR_AUTH_INACTIVE_USER",
            error_detail: "ERR_AUTH_INACTIVE_USER",
          }),
          { status: 400, headers: { "Content-Type": "application/json" } },
        ),
      );
      await meResponse.promise;
    });

    await waitFor(() => {
      expect(result.current.user).toBeNull();
    });
    expect(localStorage.getItem("access_token")).toBeNull();
    expect(localStorage.getItem("refresh_token")).toBeNull();
    expect(localStorage.getItem("user")).toBeNull();
    expect(readAuthSessionUserSnapshot()).toBeNull();
    expect(readWorkbenchSwr(mocks.user.id, "project-1")).toBeNull();
    unmount();

    // 2) Cache-miss init validation -> 400 ERR_AUTH_INACTIVE_USER clears tokens instead of preserving them
    localStorage.clear();
    sessionStorage.clear();
    localStorage.setItem("access_token", "deactivated-user-token-2");
    localStorage.setItem("refresh_token", "deactivated-user-refresh-2");
    global.fetch = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: "ERR_AUTH_INACTIVE_USER",
          error_code: "ERR_AUTH_INACTIVE_USER",
        }),
        { status: 400, headers: { "Content-Type": "application/json" } },
      ),
    ) as unknown as typeof fetch;

    const coldInit = renderHook(() => useAuth(), {
      wrapper: ({ children }: { children: React.ReactNode }) => (
        <AuthProvider>{children}</AuthProvider>
      ),
    });

    await waitFor(() => {
      expect(coldInit.result.current.loading).toBe(false);
    });
    expect(coldInit.result.current.user).toBeNull();
    expect(localStorage.getItem("access_token")).toBeNull();
    expect(localStorage.getItem("refresh_token")).toBeNull();
    coldInit.unmount();
  });
});

describe("workbenchBootstrap and workbenchSwrCache edge branches", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    sessionStorage.clear();
    mocks.getBootstrap.mockReset();
    mocks.projectGet.mockReset();
    mocks.fileGet.mockReset();
    mocks.fileGetTree.mockReset();
    mocks.getRecentMessages.mockReset();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
    sessionStorage.clear();
  });

  it("classifies bootstrap missing errors and validates bootstrap payload shapes", () => {
    expect(isBootstrapEndpointMissingError(new Error("plain"))).toBe(false);
    expect(isBootstrapEndpointMissingError(new ApiError(405, "Method Not Allowed"))).toBe(true);
    expect(isBootstrapEndpointMissingError(new ApiError(404, "Not Found"))).toBe(true);
    expect(isBootstrapEndpointMissingError(new ApiError(404, "ERR_PROJECT_NOT_FOUND"))).toBe(false);
    expect(isBootstrapEndpointMissingError(new ApiError(500, "Internal"))).toBe(false);

    expect(isValidProjectBootstrapResponse(null)).toBe(false);
    expect(isValidProjectBootstrapResponse({ project: { id: "" }, files: [], recent_messages: [] })).toBe(false);
    expect(isValidProjectBootstrapResponse({ project: { id: "p-1" }, files: [], recent_messages: [] }, "p-2")).toBe(false);
    expect(isValidProjectBootstrapResponse({ project: { id: "p-1" }, files: "nope", recent_messages: [] })).toBe(false);
    expect(isValidProjectBootstrapResponse({ project: { id: "p-1" }, files: [], recent_messages: "nope" })).toBe(false);
    expect(
      isValidProjectBootstrapResponse({
        project: { id: "p-1" },
        files: [],
        recent_messages: [],
        initial_file: "invalid" as unknown as File,
      }),
    ).toBe(false);
    expect(isValidProjectBootstrapResponse(sampleBootstrap, "project-1")).toBe(true);
  });

  it("handles legacy fallback partial failures, pagination trim, invalid file types, and 5xx rethrow", async () => {
    // 5xx from /bootstrap must rethrow rather than falling back
    mocks.getBootstrap.mockRejectedValueOnce(new ApiError(502, "Bad Gateway"));
    await expect(fetchProjectBootstrapWithFallback("project-1")).rejects.toThrow("Bad Gateway");

    // 405 triggers fallback; test tree/chat failures, deleted/folder/cross-project/404/503 file cases, and chatLimit+1 trim
    mocks.getBootstrap.mockRejectedValueOnce(new ApiError(405, "Method Not Allowed"));
    mocks.projectGet.mockResolvedValueOnce(sampleProject);
    mocks.fileGetTree.mockRejectedValueOnce(new Error("tree down"));
    mocks.getRecentMessages.mockRejectedValueOnce(new Error("chat down"));
    mocks.fileGet.mockResolvedValueOnce({ ...sampleFile, file_type: "folder" });

    const res1 = await fetchProjectBootstrapWithFallback("project-1", { fileId: sampleFile.id, chatLimit: 2 });
    expect(res1.usedFallback).toBe(true);
    expect(res1.fileTreeReady).toBe(false);
    expect(res1.chatReady).toBe(false);
    expect(res1.initialFileStatus).toBe("missing");
    expect(res1.data.initial_file).toBeNull();

    // 404 file -> missing; 3 messages for chatLimit=2 -> trimmed to last 2 with has_more_messages=true
    mocks.getBootstrap.mockRejectedValueOnce(new ApiError(404, "Not Found"));
    mocks.projectGet.mockResolvedValueOnce(sampleProject);
    mocks.fileGetTree.mockResolvedValueOnce({ tree: sampleTree });
    mocks.getRecentMessages.mockResolvedValueOnce([
      { ...sampleBootstrap.recent_messages[0], id: "m1", content: "1" },
      { ...sampleBootstrap.recent_messages[0], id: "m2", content: "2" },
      { ...sampleBootstrap.recent_messages[0], id: "m3", content: "3" },
    ]);
    mocks.fileGet.mockRejectedValueOnce(new ApiError(404, "ERR_FILE_NOT_FOUND"));

    const res2 = await fetchProjectBootstrapWithFallback("project-1", { fileId: "gone-file", chatLimit: 2 });
    expect(res2.data.has_more_messages).toBe(true);
    expect(res2.data.recent_messages.map((m) => m.id)).toEqual(["m2", "m3"]);
    expect(res2.initialFileStatus).toBe("missing");

    // 503 file -> transient_error; omitted fileId -> missing without calling fileApi.get
    mocks.getBootstrap.mockRejectedValueOnce(new ApiError(404, "Not Found"));
    mocks.projectGet.mockResolvedValueOnce(sampleProject);
    mocks.fileGetTree.mockResolvedValueOnce({ tree: sampleTree });
    mocks.getRecentMessages.mockResolvedValueOnce([]);
    mocks.fileGet.mockRejectedValueOnce(new ApiError(503, "Unavailable"));

    const res3 = await fetchProjectBootstrapWithFallback("project-1", { fileId: "flaky-file" });
    expect(res3.initialFileStatus).toBe("transient_error");

    mocks.getBootstrap.mockRejectedValueOnce(new ApiError(404, "Not Found"));
    mocks.projectGet.mockResolvedValueOnce(sampleProject);
    mocks.fileGetTree.mockResolvedValueOnce({ tree: sampleTree });
    mocks.getRecentMessages.mockResolvedValueOnce([]);

    const res4 = await fetchProjectBootstrapWithFallback("project-1");
    expect(res4.initialFileStatus).toBe("missing");
    expect(res4.data.initial_file).toBeNull();
  });

  it("handles handoff expiry, sync-required flags, consumption guards, and fresher SWR propagation", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    // Null/missing guards are safe no-ops
    expect(consumeWorkbenchSwrSyncRequired(undefined, "u", "p", "f")).toBe(false);
    expect(consumeWorkbenchSwrSyncRequired(queryClient, null, "p", "f")).toBe(false);
    markWorkbenchSwrSyncRequired(undefined, "u", "p", "f");
    clearWorkbenchSwrSyncRequired(undefined, "u", "p", "f");
    evictProjectBootstrapHandoff(undefined, "u", "p");
    expect(peekBootstrappedFileTree(undefined, "u", "p")).toBeNull();
    expect(peekBootstrappedChat(queryClient, "u", "p")).toBeNull();

    // Sync-required mark -> consume -> empty
    markWorkbenchSwrSyncRequired(queryClient, "user-1", "project-1", "file-1");
    expect(consumeWorkbenchSwrSyncRequired(queryClient, "user-1", "project-1", "file-1")).toBe(true);
    expect(consumeWorkbenchSwrSyncRequired(queryClient, "user-1", "project-1", "file-1")).toBe(false);

    // Expired handoff is automatically evicted by peek
    mocks.getBootstrap.mockResolvedValueOnce(sampleBootstrap);
    const oldStart = Date.now() - BOOTSTRAP_HANDOFF_MAX_AGE_MS - 1_000;
    await startProjectBootstrap(queryClient, {
      userId: "user-1",
      projectId: "project-1",
      now: oldStart,
    });
    expect(peekBootstrappedFileTree(queryClient, "user-1", "project-1")).toBeNull();

    // Active handoff can be peeked and marked consumed; fresher SWR wins in returned result.data.initial_file
    const newerSwrFile: File = {
      ...sampleFile,
      content: "更晚保存的 SWR 内容",
      updated_at: "2026-11-26T12:00:00Z",
    };
    writeWorkbenchSwr("user-1", "project-1", {
      project: sampleProject,
      files: sampleTree,
      initial_file: newerSwrFile,
    });
    mocks.getBootstrap.mockResolvedValueOnce(sampleBootstrap);
    const bootPromise = startProjectBootstrap(queryClient, {
      userId: "user-1",
      projectId: "project-1",
      fileId: sampleFile.id,
    });
    const entry = peekBootstrappedFileTree(queryClient, "user-1", "project-1");
    expect(entry).not.toBeNull();
    markBootstrappedFileTreeConsumed(queryClient, "user-1", "project-1", entry!);
    expect(peekBootstrappedFileTree(queryClient, "user-1", "project-1")).toBeNull();
    markBootstrappedChatConsumed(queryClient, "user-1", "project-1", entry!);
    expect(peekBootstrappedChat(queryClient, "user-1", "project-1")).toBeNull();

    const result = await bootPromise;
    expect(result?.data.initial_file?.content).toBe("更晚保存的 SWR 内容");
    expect(result?.data.initial_file?.updated_at).toBe(newerSwrFile.updated_at);
  });

  it("handles workbenchSwrCache corruption, expiry, oversized bodies, LRU pruning, title updates, invalidation, and auth snapshot edge cases", () => {
    // Missing args
    expect(readWorkbenchSwr(null, "project-1")).toBeNull();
    expect(writeWorkbenchSwr(null, "project-1", { project: sampleProject, files: sampleTree, initial_file: null })).toBe(false);
    expect(writeWorkbenchSwr("user-1", "other-project", { project: sampleProject, files: sampleTree, initial_file: null })).toBe(false);
    removeWorkbenchSwr(null, "project-1");
    updateWorkbenchSwrInitialFile(null, "project-1", sampleFile);
    invalidateWorkbenchSwrInitialFile(null, "project-1");

    // Corrupted JSON and expired/invalid schema entries are removed on read
    const key = getWorkbenchSwrKey("user-1", "project-1");
    sessionStorage.setItem(key, "{not-json");
    expect(readWorkbenchSwr("user-1", "project-1")).toBeNull();
    expect(sessionStorage.getItem(key)).toBeNull();

    const baseNow = Date.now();
    writeWorkbenchSwr("user-1", "project-1", {
      project: sampleProject,
      files: sampleTree,
      initial_file: sampleFile,
    }, baseNow);
    expect(readWorkbenchSwr("user-1", "project-1", baseNow + WORKBENCH_SWR_TTL_MS + 1)).toBeNull();
    expect(sessionStorage.getItem(key)).toBeNull();

    // Oversized initial_file is dropped while keeping project + tree; oversized tree returns false
    const hugeFile: File = {
      ...sampleFile,
      content: "x".repeat(MAX_WORKBENCH_SWR_ENTRY_CHARS + 100),
    };
    expect(
      writeWorkbenchSwr("user-1", "project-1", {
        project: sampleProject,
        files: sampleTree,
        initial_file: hugeFile,
      }, baseNow),
    ).toBe(true);
    expect(readWorkbenchSwr("user-1", "project-1", baseNow)?.initial_file).toBeNull();

    const hugeTree: FileTreeNode[] = [
      {
        ...sampleTree[0],
        title: "t".repeat(MAX_WORKBENCH_SWR_ENTRY_CHARS + 100),
      },
    ];
    expect(
      writeWorkbenchSwr("user-1", "project-1", {
        project: sampleProject,
        files: hugeTree,
        initial_file: null,
      }, baseNow),
    ).toBe(false);

    // updateWorkbenchSwrInitialFile updates nested tree node title iteratively and invalidate respects fileId filter
    writeWorkbenchSwr("user-1", "project-1", {
      project: sampleProject,
      files: sampleTree,
      initial_file: sampleFile,
    }, baseNow);
    updateWorkbenchSwrInitialFile("user-1", "project-1", { ...sampleFile, title: "第七章（修订）" });
    const renamed = readWorkbenchSwr("user-1", "project-1", baseNow);
    expect(renamed?.files[0]?.children?.[0]?.title).toBe("第七章（修订）");

    invalidateWorkbenchSwrInitialFile("user-1", "project-1", "other-file");
    expect(readWorkbenchSwr("user-1", "project-1", baseNow)?.initial_file?.id).toBe(sampleFile.id);
    invalidateWorkbenchSwrInitialFile("user-1", "project-1", sampleFile.id);
    expect(readWorkbenchSwr("user-1", "project-1", baseNow)?.initial_file).toBeNull();

    // LRU pruning evicts oldest entries beyond MAX_WORKBENCH_SWR_PROJECTS (including corrupted entries)
    clearWorkbenchSwrCache();
    sessionStorage.setItem(getWorkbenchSwrKey("user-1", "corrupt-proj"), "{bad");
    for (let i = 0; i < MAX_WORKBENCH_SWR_PROJECTS + 1; i += 1) {
      const pid = `project-lru-${i}`;
      writeWorkbenchSwr(
        "user-1",
        pid,
        {
          project: { ...sampleProject, id: pid },
          files: [],
          initial_file: null,
        },
        baseNow + i * 1000,
      );
    }
    expect(sessionStorage.getItem(getWorkbenchSwrKey("user-1", "corrupt-proj"))).toBeNull();
    expect(readWorkbenchSwr("user-1", "project-lru-0", baseNow + 10_000)).toBeNull();
    expect(readWorkbenchSwr("user-1", `project-lru-${MAX_WORKBENCH_SWR_PROJECTS}`, baseNow + 10_000)?.project.id).toBe(
      `project-lru-${MAX_WORKBENCH_SWR_PROJECTS}`,
    );

    // Auth session user snapshot edge cases: invalid user, corrupted/expired JSON, explicit clear
    expect(writeAuthSessionUserSnapshot(null)).toBe(false);
    expect(writeAuthSessionUserSnapshot({ id: "" })).toBe(false);
    sessionStorage.setItem(AUTH_SESSION_USER_KEY, "{bad-json");
    expect(readAuthSessionUserSnapshot()).toBeNull();
    expect(sessionStorage.getItem(AUTH_SESSION_USER_KEY)).toBeNull();

    writeAuthSessionUserSnapshot(mocks.user, baseNow);
    expect(readAuthSessionUserSnapshot(baseNow + 25 * 60 * 60 * 1000)).toBeNull();
    writeAuthSessionUserSnapshot(mocks.user, baseNow);
    clearAuthSessionUserSnapshot();
    expect(readAuthSessionUserSnapshot(baseNow)).toBeNull();
  });
});
