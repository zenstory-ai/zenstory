import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearEditorDraftSnapshot,
  createEditorDraftSnapshot,
  getEditorDraftRecoveryKey,
  readEditorDraftSnapshot,
  resolveEditorDraftRecovery,
  writeEditorDraftSnapshot,
} from "../editorDraftRecovery";

const scope = { userId: "user/a", projectId: "project:b", fileId: "file c" };

describe("editorDraftRecovery", () => {
  beforeEach(() => localStorage.clear());

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
});
