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
  reason: "chunk-reload" | "page-exit";
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
    && typeof snapshot.capturedAt === "string"
    && (snapshot.reason === "chunk-reload" || snapshot.reason === "page-exit");
}

export function writeEditorDraftSnapshot(
  storage: DraftStorage,
  snapshot: EditorDraftSnapshot,
): boolean {
  try {
    storage.setItem(getEditorDraftRecoveryKey(snapshot), JSON.stringify(snapshot));
    return true;
  } catch {
    return false;
  }
}

export function readEditorDraftSnapshot(
  storage: DraftStorage,
  scope: EditorDraftScope,
): EditorDraftSnapshot | null {
  const key = getEditorDraftRecoveryKey(scope);
  try {
    const raw = storage.getItem(key);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (isSnapshotForScope(parsed, scope)) return parsed;
    storage.removeItem(key);
  } catch {
    try {
      storage.removeItem(key);
    } catch {
      // Storage is unavailable; editor loading must continue.
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
