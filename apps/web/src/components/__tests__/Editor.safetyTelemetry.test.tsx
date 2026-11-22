import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Editor } from "../Editor";
import type { SaveResult, SaveSubmission } from "../SimpleEditor";
import * as api from "../../lib/api";
import { ApiError } from "../../lib/apiClient";
import {
  createEditorDraftSnapshot,
  writeEditorDraftSnapshot,
} from "../../lib/editorDraftRecovery";

const { trackEventMock, captureExceptionMock } = vi.hoisted(() => ({
  trackEventMock: vi.fn(),
  captureExceptionMock: vi.fn(),
}));

vi.mock("../../lib/analytics", () => ({
  trackEvent: trackEventMock,
  captureException: captureExceptionMock,
}));

vi.mock("../SimpleEditor", () => ({
  SimpleEditor: ({
    fileId,
    fileTitle,
    baseUpdatedAt,
    content,
    onTitleChange,
    onContentChange,
    onSave,
    onFinishReview,
  }: {
    fileId: string;
    fileTitle: string;
    baseUpdatedAt?: string;
    content: string;
    onTitleChange?: (value: string) => void;
    onContentChange?: (value: string) => void;
    onSave?: (submission: SaveSubmission) => Promise<SaveResult>;
    onFinishReview?: () => void;
  }) => {
    const draftRef = React.useRef({
      fileId,
      title: fileTitle,
      content,
      baseUpdatedAt,
      previousTitle: fileTitle,
    });
    draftRef.current = {
      ...draftRef.current,
      fileId,
      title: fileTitle,
      content,
      baseUpdatedAt,
    };
    return (
      <div data-testid="simple-editor">
        <input
          data-testid="title-input"
          value={fileTitle}
          onChange={(e) => {
            draftRef.current.title = e.target.value;
            onTitleChange?.(e.target.value);
          }}
        />
        <textarea
          data-testid="content-input"
          value={content}
          onChange={(e) => {
            draftRef.current.content = e.target.value;
            onContentChange?.(e.target.value);
          }}
        />
        <button
          data-testid="manual-save-button"
          onClick={() =>
            void onSave?.({
              ...draftRef.current,
              versionIntent: { change_type: "edit", change_source: "user" },
            })
          }
        >
          Manual Save
        </button>
        <button
          data-testid="auto-save-button"
          onClick={() =>
            void onSave?.({
              ...draftRef.current,
              versionIntent: { change_type: "auto_save", change_source: "user" },
            })
          }
        >
          Auto Save
        </button>
        <button data-testid="finish-review-button" onClick={() => onFinishReview?.()}>
          Finish Review
        </button>
      </div>
    );
  },
}));

vi.mock("../subscription/UpgradePromptModal", () => ({
  UpgradePromptModal: () => null,
}));

vi.mock("../../lib/api", () => ({
  fileApi: {
    get: vi.fn(),
    getTree: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    uploadDraft: vi.fn(),
  },
  fileVersionApi: {
    getVersions: vi.fn(),
  },
}));

vi.mock("../../contexts/MaterialLibraryContext", () => ({
  useMaterialLibraryContext: () => ({
    preview: null,
    isPreviewLoading: false,
    libraries: [],
    clearPreview: vi.fn(),
  }),
}));

vi.mock("../../contexts/MaterialAttachmentContext", () => ({
  useMaterialAttachment: () => ({
    addMaterial: vi.fn(),
    removeMaterial: vi.fn(),
  }),
}));

const viewport = vi.hoisted(() => ({
  isMobile: false,
  switchToChat: vi.fn(),
  focusChatInput: vi.fn(),
}));

vi.mock("../../contexts/MobileLayoutContext", () => ({
  useMobileLayout: () => viewport,
}));

let mockProjectContext: Record<string, unknown> | null = null;
vi.mock("../../contexts/ProjectContext", () => ({
  useProject: () => mockProjectContext,
}));

vi.mock("../../contexts/AuthContext", () => ({
  useAuth: () => ({ user: { id: "user-1" } }),
}));

vi.mock("../../lib/activationEvents", () => ({
  reportActivationEvent: vi.fn(),
  rearmAiFileOpened: vi.fn(),
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}));

const createProjectContext = (overrides = {}) => ({
  currentProjectId: "project-1",
  selectedItem: { id: "file-1", type: "draft", title: "绝密章节标题" },
  setSelectedItem: vi.fn(),
  streamingFileId: null,
  streamingContent: "",
  triggerFileTreeRefresh: vi.fn(),
  editorRefreshVersion: 0,
  lastEditedFileId: null,
  aiEditingFileId: null,
  diffReviewState: null,
  enterDiffReview: vi.fn(),
  acceptEdit: vi.fn(),
  rejectEdit: vi.fn(),
  resetEdit: vi.fn(),
  acceptAllEdits: vi.fn(),
  rejectAllEdits: vi.fn(),
  exitDiffReview: vi.fn(),
  applyDiffReviewChanges: vi.fn(),
  ...overrides,
});

const SECRET_TITLE = "绝密章节标题";
const SECRET_LOCAL_BODY = "作者尚未保存的绝密手稿内容";
const SECRET_SERVER_BODY = "服务端最新AI修改正文";

const mockFile = {
  id: "file-1",
  project_id: "project-1",
  title: SECRET_TITLE,
  content: "初始正文",
  file_type: "draft",
  parent_id: null,
  updated_at: "server-v1",
};

describe("Editor safety telemetry", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    viewport.isMobile = false;
    mockProjectContext = createProjectContext();
    vi.mocked(api.fileApi.get).mockResolvedValue(mockFile as never);
    vi.mocked(api.fileApi.update).mockResolvedValue({ ...mockFile, updated_at: "server-v2" } as never);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("emits editor_save_conflict_409 distinguishing manual vs auto_save and is_mobile without leaking text", async () => {
    vi.mocked(api.fileApi.update).mockRejectedValue(
      new ApiError(409, "ERR_RESOURCE_CONFLICT", {
        reason: "stale_write",
        current_content: SECRET_SERVER_BODY,
        current_updated_at: "server-v2",
      }),
    );

    const { unmount } = render(<Editor />);
    await waitFor(() => expect(screen.getByTestId("content-input")).toHaveValue("初始正文"));

    fireEvent.change(screen.getByTestId("content-input"), { target: { value: SECRET_LOCAL_BODY } });
    fireEvent.click(screen.getByTestId("manual-save-button"));

    await waitFor(() =>
      expect(trackEventMock).toHaveBeenCalledWith("editor_save_conflict_409", {
        surface: "manual",
        is_mobile: false,
        reason: "stale_write",
      }),
    );
    expect(trackEventMock).toHaveBeenCalledWith("editor_draft_recovery_shown", {
      surface: "conflict_review",
      is_mobile: false,
      reason: "stale_write",
    });

    unmount();
    trackEventMock.mockClear();
    viewport.isMobile = true;

    render(<Editor />);
    await waitFor(() => expect(screen.getByTestId("content-input")).toHaveValue("初始正文"));

    fireEvent.change(screen.getByTestId("content-input"), { target: { value: SECRET_LOCAL_BODY } });
    fireEvent.click(screen.getByTestId("auto-save-button"));

    await waitFor(() =>
      expect(trackEventMock).toHaveBeenCalledWith("editor_save_conflict_409", {
        surface: "auto_save",
        is_mobile: true,
        reason: "stale_write",
      }),
    );

    const serialized = JSON.stringify(trackEventMock.mock.calls);
    expect(serialized).not.toContain(SECRET_TITLE);
    expect(serialized).not.toContain(SECRET_LOCAL_BODY);
    expect(serialized).not.toContain(SECRET_SERVER_BODY);
  });

  it("emits editor_draft_recovery_shown and resolved for conflict restore_local, discard, and use_server", async () => {
    const scope = { userId: "user-1", projectId: "project-1", fileId: "file-1" };
    writeEditorDraftSnapshot(
      localStorage,
      createEditorDraftSnapshot({
        ...scope,
        title: SECRET_TITLE,
        content: SECRET_LOCAL_BODY,
        baseUpdatedAt: "server-v0",
        capturedAt: "2026-10-07T08:00:00.000Z",
      }),
    );

    const { unmount } = render(<Editor />);
    await screen.findByRole("alert");

    expect(trackEventMock).toHaveBeenCalledWith("editor_draft_recovery_shown", {
      surface: "draft_recovery",
      is_mobile: false,
      reason: "conflict",
      storage_available: true,
    });

    // Click Restore local draft -> resolution: "restore_local"
    fireEvent.click(screen.getByRole("button", { name: "editor:draftRecovery.restore" }));
    expect(trackEventMock).toHaveBeenCalledWith("editor_draft_recovery_resolved", {
      surface: "draft_recovery",
      is_mobile: false,
      reason: "conflict",
      resolution: "restore_local",
    });

    // Click Discard after applying -> resolution: "discard"
    fireEvent.click(screen.getByRole("button", { name: "editor:draftRecovery.discard" }));
    expect(trackEventMock).toHaveBeenCalledWith("editor_draft_recovery_resolved", {
      surface: "draft_recovery",
      is_mobile: false,
      reason: "conflict",
      resolution: "discard",
    });

    unmount();
    trackEventMock.mockClear();

    // Conflict banner where user immediately chooses server version -> resolution: "use_server"
    writeEditorDraftSnapshot(
      localStorage,
      createEditorDraftSnapshot({
        ...scope,
        title: SECRET_TITLE,
        content: SECRET_LOCAL_BODY,
        baseUpdatedAt: "server-v0",
        capturedAt: "2026-10-07T08:05:00.000Z",
      }),
    );
    viewport.isMobile = true;
    render(<Editor />);
    await screen.findByRole("alert");

    fireEvent.click(screen.getByRole("button", { name: "editor:draftRecovery.discard" }));
    expect(trackEventMock).toHaveBeenCalledWith("editor_draft_recovery_resolved", {
      surface: "draft_recovery",
      is_mobile: true,
      reason: "conflict",
      resolution: "use_server",
    });

    const serialized = JSON.stringify(trackEventMock.mock.calls);
    expect(serialized).not.toContain(SECRET_TITLE);
    expect(serialized).not.toContain(SECRET_LOCAL_BODY);
  });

  it("emits editor_draft_recovery_resolved with merge when conflict diff review finishes with mixed decisions", async () => {
    const UnmemoizedEditor = (Editor as unknown as { type: React.ComponentType }).type;
    mockProjectContext = createProjectContext({
      applyDiffReviewChanges: vi.fn().mockReturnValue("合并后的正文"),
    });
    const { rerender } = render(<UnmemoizedEditor />);
    await waitFor(() => expect(screen.getByTestId("content-input")).toHaveValue("初始正文"));

    mockProjectContext = createProjectContext({
      applyDiffReviewChanges: vi.fn().mockReturnValue("合并后的正文"),
      diffReviewState: {
        isReviewing: true,
        fileId: "file-1",
        originalContent: SECRET_LOCAL_BODY,
        modifiedContent: SECRET_SERVER_BODY,
        origin: "conflict",
        pendingEdits: [
          { id: "e-1", op: "replace", oldText: "a", newText: "b", status: "accepted" },
          { id: "e-2", op: "replace", oldText: "c", newText: "d", status: "rejected" },
        ],
      },
    });
    rerender(<UnmemoizedEditor />);

    fireEvent.click(screen.getByTestId("finish-review-button"));

    await waitFor(() =>
      expect(trackEventMock).toHaveBeenCalledWith("editor_draft_recovery_resolved", {
        surface: "conflict_review",
        is_mobile: false,
        reason: "stale_write",
        resolution: "merge",
      }),
    );
  });

  it("emits editor_draft_recovery_shown and restore_local when an auto-recovered draft is saved", async () => {
    const scope = { userId: "user-1", projectId: "project-1", fileId: "file-1" };
    writeEditorDraftSnapshot(
      localStorage,
      createEditorDraftSnapshot({
        ...scope,
        title: SECRET_TITLE,
        content: SECRET_LOCAL_BODY,
        baseUpdatedAt: "server-v1",
        capturedAt: "2026-10-07T08:00:00.000Z",
      }),
    );

    render(<Editor />);
    await waitFor(() => expect(screen.getByTestId("content-input")).toHaveValue(SECRET_LOCAL_BODY));
    expect(trackEventMock).toHaveBeenCalledWith("editor_draft_recovery_shown", {
      surface: "draft_recovery",
      is_mobile: false,
      reason: "recover",
      storage_available: true,
    });

    fireEvent.click(screen.getByTestId("manual-save-button"));

    await waitFor(() =>
      expect(trackEventMock).toHaveBeenCalledWith("editor_draft_recovery_resolved", {
        surface: "draft_recovery",
        is_mobile: false,
        reason: "recover",
        resolution: "restore_local",
      }),
    );
  });
});
