import { beforeEach, describe, expect, it, vi } from "vitest";

const { trackEventMock } = vi.hoisted(() => ({
  trackEventMock: vi.fn(),
}));

vi.mock("../analytics", () => ({
  trackEvent: trackEventMock,
}));

import {
  clearEditorDraftSnapshot,
  createEditorDraftSnapshot,
  getEditorDraftRecoveryKey,
  readEditorDraftSnapshot,
  readDeletedEditorDraftSnapshots,
  resolveEditorDraftRecovery,
  writeEditorDraftSnapshot,
} from "../editorDraftRecovery";

const scope = { userId: "user/a", projectId: "project:b", fileId: "file c" };

describe("editorDraftRecovery", () => {
  beforeEach(() => {
    localStorage.clear();
    trackEventMock.mockReset();
  });

  it("isolates snapshots by user, project, and file", () => {
    const snapshot = createEditorDraftSnapshot({
      ...scope,
      title: "Local title",
      content: "Latest local body",
      baseUpdatedAt: "server-v1",
      capturedAt: "2026-10-07T08:00:00.000Z",
    });

    expect(writeEditorDraftSnapshot(localStorage, snapshot)).toBe(true);
    expect(readEditorDraftSnapshot(localStorage, scope)).toEqual(snapshot);
    expect(readEditorDraftSnapshot(localStorage, { ...scope, userId: "other-user" })).toBeNull();
    expect(getEditorDraftRecoveryKey(scope)).not.toContain("user/a");
  });

  it("accepts ordinary page-exit snapshots", () => {
    const snapshot = createEditorDraftSnapshot({
      ...scope,
      title: "Local title",
      content: "Latest local body",
      baseUpdatedAt: "server-v1",
      capturedAt: "2026-10-07T08:00:00.000Z",
      reason: "page-exit",
    });

    expect(writeEditorDraftSnapshot(localStorage, snapshot)).toBe(true);
    expect(readEditorDraftSnapshot(localStorage, scope)).toEqual(snapshot);
  });

  it("cleans identical server content without offering recovery", () => {
    const snapshot = createEditorDraftSnapshot({
      ...scope,
      title: "Same",
      content: "Same body",
      baseUpdatedAt: "server-v1",
      capturedAt: "2026-10-07T08:00:00.000Z",
    });

    expect(resolveEditorDraftRecovery(snapshot, {
      title: "Same",
      content: "Same body",
      updatedAt: "server-v2",
    })).toBe("identical");
  });

  it("recovers only when both concurrency tokens exist and match exactly", () => {
    const snapshot = createEditorDraftSnapshot({
      ...scope,
      title: "Local title",
      content: "Local body",
      baseUpdatedAt: "server-v1",
      capturedAt: "2026-10-07T08:00:00.000Z",
    });

    expect(resolveEditorDraftRecovery(snapshot, {
      title: "Server title",
      content: "Server body",
      updatedAt: "server-v1",
    })).toBe("recover");
    expect(resolveEditorDraftRecovery(snapshot, {
      title: "Server title",
      content: "Server body",
      updatedAt: "server-v2",
    })).toBe("conflict");
    expect(resolveEditorDraftRecovery({ ...snapshot, baseUpdatedAt: undefined }, {
      title: "Server title",
      content: "Server body",
      updatedAt: undefined,
    })).toBe("conflict");
    expect(resolveEditorDraftRecovery({ ...snapshot, baseUpdatedAt: "" }, {
      title: "Server title",
      content: "Server body",
      updatedAt: "",
    })).toBe("conflict");
  });

  it("ignores malformed storage and storage failures", () => {
    localStorage.setItem(getEditorDraftRecoveryKey(scope), "not-json");
    expect(readEditorDraftSnapshot(localStorage, scope)).toBeNull();
    expect(localStorage.getItem(getEditorDraftRecoveryKey(scope))).toBeNull();

    const unavailableStorage = {
      getItem: vi.fn(() => { throw new Error("blocked"); }),
      setItem: vi.fn(() => { throw new Error("full"); }),
      removeItem: vi.fn(() => { throw new Error("blocked"); }),
    };
    const snapshot = createEditorDraftSnapshot({
      ...scope,
      title: "Local title",
      content: "Local body",
      capturedAt: "2026-10-07T08:00:00.000Z",
    });

    expect(writeEditorDraftSnapshot(unavailableStorage, snapshot)).toBe(false);
    expect(readEditorDraftSnapshot(unavailableStorage, scope)).toBeNull();
    expect(clearEditorDraftSnapshot(unavailableStorage, scope)).toBe(false);
  });
  it('rejects a corrupt additional recovery copy and tolerates unavailable project storage', () => {
    const snapshot = createEditorDraftSnapshot({ ...scope, title: 'Latest', content: 'Latest body', reason: 'file-deleted' });
    localStorage.setItem(getEditorDraftRecoveryKey(scope), JSON.stringify({ ...snapshot, recoveryDraft: { title: 'Other', content: 42 } }));
    expect(readEditorDraftSnapshot(localStorage, scope)).toBeNull();
    const unavailable = { get length() { throw new Error('storage unavailable'); } } as Storage;
    expect(readDeletedEditorDraftSnapshots(unavailable, scope)).toEqual([]);
  });

  it('lists deleted drafts only for the signed-in user and active project', () => {
    const own = createEditorDraftSnapshot({ ...scope, title: 'Deleted', content: 'Keep', reason: 'file-deleted' });
    [own, { ...own, userId: 'another-user' }, { ...own, projectId: 'another-project' }, { ...own, fileId: 'ordinary', reason: 'page-exit' as const }]
      .forEach(snapshot => writeEditorDraftSnapshot(localStorage, snapshot));
    expect(readDeletedEditorDraftSnapshots(localStorage, scope)).toEqual([own]);
    expect(trackEventMock).not.toHaveBeenCalled();
  });

  it("emits privacy-clean editor_draft_storage_failed events when storage operations throw", () => {
    const secretTitle = "绝密草稿标题";
    const secretContent = "主角在雨夜写下的绝密遗书";
    const unavailableStorage = {
      getItem: vi.fn(() => { throw new Error("SecurityError: blocked"); }),
      setItem: vi.fn(() => { throw new Error("QuotaExceededError: full"); }),
      removeItem: vi.fn(() => { throw new Error("SecurityError: blocked"); }),
    };

    const reloadSnapshot = createEditorDraftSnapshot({
      ...scope,
      title: secretTitle,
      content: secretContent,
      reason: "chunk-reload",
    });
    expect(writeEditorDraftSnapshot(unavailableStorage, reloadSnapshot)).toBe(false);
    expect(trackEventMock).toHaveBeenLastCalledWith("editor_draft_storage_failed", {
      surface: "draft_recovery",
      is_mobile: false,
      reason: "chunk-reload",
      storage_available: false,
    });

    const deletedSnapshot = createEditorDraftSnapshot({
      ...scope,
      title: secretTitle,
      content: secretContent,
      reason: "file-deleted",
    });
    expect(writeEditorDraftSnapshot(unavailableStorage, deletedSnapshot)).toBe(false);
    expect(trackEventMock).toHaveBeenLastCalledWith("editor_draft_storage_failed", {
      surface: "deleted_file_recovery",
      is_mobile: false,
      reason: "file-deleted",
      storage_available: false,
    });

    expect(readEditorDraftSnapshot(unavailableStorage, scope)).toBeNull();
    expect(trackEventMock).toHaveBeenLastCalledWith("editor_draft_storage_failed", {
      surface: "draft_recovery",
      is_mobile: false,
      reason: "read_failed",
      storage_available: false,
    });

    expect(clearEditorDraftSnapshot(unavailableStorage, scope)).toBe(false);
    expect(trackEventMock).toHaveBeenLastCalledWith("editor_draft_storage_failed", {
      surface: "draft_recovery",
      is_mobile: false,
      reason: "clear_failed",
      storage_available: false,
    });

    const unavailableEnum = { get length() { throw new Error("storage unavailable"); } } as Storage;
    expect(readDeletedEditorDraftSnapshots(unavailableEnum, scope)).toEqual([]);
    expect(trackEventMock).toHaveBeenLastCalledWith("editor_draft_storage_failed", {
      surface: "deleted_file_recovery",
      is_mobile: false,
      reason: "file-deleted",
      storage_available: false,
    });

    // Zero manuscript text, title, or identifiers leaked into telemetry.
    const serialized = JSON.stringify(trackEventMock.mock.calls);
    expect(serialized).not.toContain(secretTitle);
    expect(serialized).not.toContain(secretContent);
    expect(serialized).not.toContain(scope.userId);
    expect(serialized).not.toContain(scope.projectId);
    expect(serialized).not.toContain(scope.fileId);
  });

  it("does not emit editor_draft_storage_failed for malformed JSON when storage is working", () => {
    localStorage.setItem(getEditorDraftRecoveryKey(scope), "{not-valid-json");
    expect(readEditorDraftSnapshot(localStorage, scope)).toBeNull();
    expect(trackEventMock).not.toHaveBeenCalled();
  });

});
