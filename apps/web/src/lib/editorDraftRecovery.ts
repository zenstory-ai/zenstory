import { trackEditorDraftStorageFailed } from "./editorSafetyTelemetry";

export const EDITOR_DRAFT_RECOVERY_SCHEMA = 1 as const;
export const BEFORE_CHUNK_RELOAD_EVENT = "zenstory:before-chunk-reload";

const EDITOR_DRAFT_RECOVERY_PREFIX = "zenstory:editor-draft-recovery:v1";

export interface EditorDraftScope {
  userId: string;
  projectId: string;
  fileId: string;
}

export interface EditorDraftSnapshot extends EditorDraftScope {
  schema: typeof EDITOR_DRAFT_RECOVERY_SCHEMA;
  title: string;
  content: string;
  baseUpdatedAt?: string;
  capturedAt: string;
  reason: "chunk-reload" | "page-exit" | "file-deleted";
  /** An unanswered conflict remains a separate copy beside newer edits. */
  recoveryDraft?: Pick<EditorDraftSnapshot, "title" | "content" | "baseUpdatedAt" | "capturedAt">;
}

interface DraftStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

interface ServerDraftState {
  title: string;
  content: string;
  updatedAt?: string;
}

export type EditorDraftRecoveryResolution = "identical" | "recover" | "conflict";

const encodeKeyPart = (value: string) => encodeURIComponent(value);

export function getEditorDraftRecoveryKey(scope: EditorDraftScope): string {
  return [
    EDITOR_DRAFT_RECOVERY_PREFIX,
    encodeKeyPart(scope.userId),
    encodeKeyPart(scope.projectId),
    encodeKeyPart(scope.fileId),
  ].join(":");
}

export function createEditorDraftSnapshot(
  draft: EditorDraftScope & {
    title: string;
    content: string;
    baseUpdatedAt?: string;
    capturedAt?: string;
    reason?: EditorDraftSnapshot["reason"];
    recoveryDraft?: EditorDraftSnapshot["recoveryDraft"];
  },
): EditorDraftSnapshot {
  return {
    schema: EDITOR_DRAFT_RECOVERY_SCHEMA,
    userId: draft.userId,
    projectId: draft.projectId,
    fileId: draft.fileId,
    title: draft.title,
    content: draft.content,
    baseUpdatedAt: draft.baseUpdatedAt,
    capturedAt: draft.capturedAt ?? new Date().toISOString(),
    reason: draft.reason ?? "chunk-reload",
    ...(draft.recoveryDraft ? { recoveryDraft: draft.recoveryDraft } : {}),
  };
}

function isSnapshotForScope(value: unknown, scope: EditorDraftScope): value is EditorDraftSnapshot {
  if (!value || typeof value !== "object") return false;
  const snapshot = value as Partial<EditorDraftSnapshot>;
  return snapshot.schema === EDITOR_DRAFT_RECOVERY_SCHEMA
    && snapshot.userId === scope.userId
    && snapshot.projectId === scope.projectId
    && snapshot.fileId === scope.fileId
    && typeof snapshot.title === "string"
    && typeof snapshot.content === "string"
    && (snapshot.baseUpdatedAt === undefined || typeof snapshot.baseUpdatedAt === "string")
    && (snapshot.recoveryDraft === undefined || (
      snapshot.recoveryDraft !== null && typeof snapshot.recoveryDraft === "object"
      && typeof snapshot.recoveryDraft.title === "string"
      && typeof snapshot.recoveryDraft.content === "string"
      && typeof snapshot.recoveryDraft.capturedAt === "string"
      && (snapshot.recoveryDraft.baseUpdatedAt === undefined || typeof snapshot.recoveryDraft.baseUpdatedAt === "string")
    ))
    && typeof snapshot.capturedAt === "string"
    && (snapshot.reason === "chunk-reload" || snapshot.reason === "page-exit" || snapshot.reason === "file-deleted");
}

export function writeEditorDraftSnapshot(
  storage: DraftStorage,
  snapshot: EditorDraftSnapshot,
): boolean {
  try {
    storage.setItem(getEditorDraftRecoveryKey(snapshot), JSON.stringify(snapshot));
    return true;
  } catch {
    trackEditorDraftStorageFailed({
      surface: snapshot.reason === "file-deleted" ? "deleted_file_recovery" : "draft_recovery",
      reason: snapshot.reason,
      storageAvailable: false,
    });
    return false;
  }
}

export function readEditorDraftSnapshot(
  storage: DraftStorage,
  scope: EditorDraftScope,
): EditorDraftSnapshot | null {
  const key = getEditorDraftRecoveryKey(scope);
  let raw: string | null;
  try {
    raw = storage.getItem(key);
  } catch {
    trackEditorDraftStorageFailed({
      surface: "draft_recovery",
      reason: "read_failed",
      storageAvailable: false,
    });
    return null;
  }
  if (!raw) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (isSnapshotForScope(parsed, scope)) return parsed;
    storage.removeItem(key);
  } catch {
    try {
      storage.removeItem(key);
    } catch {
      trackEditorDraftStorageFailed({
        surface: "draft_recovery",
        reason: "clear_failed",
        storageAvailable: false,
      });
    }
  }
  return null;
}

export function clearEditorDraftSnapshot(
  storage: DraftStorage,
  scope: EditorDraftScope,
): boolean {
  try {
    storage.removeItem(getEditorDraftRecoveryKey(scope));
    return true;
  } catch {
    trackEditorDraftStorageFailed({
      surface: "draft_recovery",
      reason: "clear_failed",
      storageAvailable: false,
    });
    return false;
  }
}

export function resolveEditorDraftRecovery(
  snapshot: EditorDraftSnapshot,
  server: ServerDraftState,
): EditorDraftRecoveryResolution {
  if (snapshot.title === server.title && snapshot.content === server.content) {
    return "identical";
  }
  if (
    typeof snapshot.baseUpdatedAt === "string"
    && snapshot.baseUpdatedAt.length > 0
    && typeof server.updatedAt === "string"
    && server.updatedAt.length > 0
    && snapshot.baseUpdatedAt === server.updatedAt
  ) {
    return "recover";
  }
  return "conflict";
}

/** Deleted files cannot be reopened from the tree, so their drafts have a project recovery entry. */
export function readDeletedEditorDraftSnapshots(
  storage: Storage, scope: Pick<EditorDraftScope, "userId" | "projectId">,
): EditorDraftSnapshot[] {
  const drafts: EditorDraftSnapshot[] = [];
  const prefix = `${EDITOR_DRAFT_RECOVERY_PREFIX}:${encodeKeyPart(scope.userId)}:${encodeKeyPart(scope.projectId)}:`;
  try {
    const keys = Array.from({ length: storage.length }, (_, index) => storage.key(index));
    for (const key of keys) {
      if (!key?.startsWith(prefix)) continue;
      const fileId = decodeURIComponent(key.slice(prefix.length));
      const snapshot = readEditorDraftSnapshot(storage, { ...scope, fileId });
      if (snapshot?.reason === "file-deleted") drafts.push(snapshot);
    }
  } catch {
    trackEditorDraftStorageFailed({
      surface: "deleted_file_recovery",
      reason: "file-deleted",
      storageAvailable: false,
    });
  }
  return drafts;
}
