import type { File, FileTreeNode, Project } from "../types";

export const WORKBENCH_SWR_SCHEMA = 1 as const;
export const WORKBENCH_SWR_PREFIX = "zenstory:workbench-swr:v1:";
export const WORKBENCH_SWR_TTL_MS = 15 * 60 * 1000;
export const MAX_WORKBENCH_SWR_ENTRY_CHARS = 350_000;
export const MAX_WORKBENCH_SWR_PROJECTS = 4;

export const AUTH_SESSION_USER_SCHEMA = 1 as const;
export const AUTH_SESSION_USER_KEY = "zenstory:auth-session-user:v1";
export const AUTH_SESSION_USER_MAX_AGE_MS = 24 * 60 * 60 * 1000;

export interface WorkbenchSwrPayload {
  project: Project;
  files: FileTreeNode[];
  initial_file: File | null;
}

export interface WorkbenchSwrEntry extends WorkbenchSwrPayload {
  schema: typeof WORKBENCH_SWR_SCHEMA;
  userId: string;
  projectId: string;
  cachedAt: number;
}

interface AuthSessionUserEnvelope<TUser> {
  schema: typeof AUTH_SESSION_USER_SCHEMA;
  savedAt: number;
  user: TUser;
}

function getSessionStorage(): Storage | null {
  try {
    return typeof window !== "undefined" ? window.sessionStorage : null;
  } catch {
    return null;
  }
}

export function getWorkbenchSwrKey(userId: string, projectId: string): string {
  return `${WORKBENCH_SWR_PREFIX}${encodeURIComponent(userId)}:${encodeURIComponent(projectId)}`;
}

function isValidProject(value: unknown, projectId: string): value is Project {
  return Boolean(
    value &&
    typeof value === "object" &&
    (value as Project).id === projectId &&
    typeof (value as Project).name === "string",
  );
}

function isValidInitialFile(value: unknown, projectId: string): value is File | null {
  if (value === null) return true;
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<File>;
  return (
    typeof candidate.id === "string" &&
    candidate.id.length > 0 &&
    candidate.project_id === projectId &&
    typeof candidate.title === "string" &&
    typeof candidate.content === "string" &&
    typeof candidate.updated_at === "string" &&
    candidate.updated_at.length > 0
  );
}

export function readWorkbenchSwr(
  userId: string | null | undefined,
  projectId: string | null | undefined,
  now: number = Date.now(),
): WorkbenchSwrEntry | null {
  if (!userId || !projectId) return null;
  const storage = getSessionStorage();
  if (!storage) return null;
  const key = getWorkbenchSwrKey(userId, projectId);
  let raw: string | null = null;
  try {
    raw = storage.getItem(key);
  } catch {
    return null;
  }
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<WorkbenchSwrEntry>;
    if (
      parsed.schema !== WORKBENCH_SWR_SCHEMA ||
      parsed.userId !== userId ||
      parsed.projectId !== projectId ||
      typeof parsed.cachedAt !== "number" ||
      !Number.isFinite(parsed.cachedAt) ||
      now - parsed.cachedAt > WORKBENCH_SWR_TTL_MS ||
      !isValidProject(parsed.project, projectId) ||
      !Array.isArray(parsed.files) ||
      !isValidInitialFile(parsed.initial_file ?? null, projectId)
    ) {
      try {
        storage.removeItem(key);
      } catch {
        // Ignore storage errors.
      }
      return null;
    }
    return {
      schema: WORKBENCH_SWR_SCHEMA,
      userId,
      projectId,
      cachedAt: parsed.cachedAt,
      project: parsed.project,
      files: parsed.files,
      initial_file: parsed.initial_file ?? null,
    };
  } catch {
    try {
      storage.removeItem(key);
    } catch {
      // Ignore storage errors.
    }
    return null;
  }
}

function pruneWorkbenchSwrEntries(storage: Storage, keepKey: string): void {
  try {
    const entries: Array<{ key: string; cachedAt: number }> = [];
    for (let index = 0; index < storage.length; index += 1) {
      const key = storage.key(index);
      if (!key || !key.startsWith(WORKBENCH_SWR_PREFIX) || key === keepKey) continue;
      let cachedAt = 0;
      try {
        const parsed = JSON.parse(storage.getItem(key) || "{}") as { cachedAt?: unknown };
        if (typeof parsed.cachedAt === "number" && Number.isFinite(parsed.cachedAt)) {
          cachedAt = parsed.cachedAt;
        }
      } catch {
        cachedAt = 0;
      }
      entries.push({ key, cachedAt });
    }
    entries.sort((a, b) => a.cachedAt - b.cachedAt);
    const excess = entries.length + 1 - MAX_WORKBENCH_SWR_PROJECTS;
    for (let i = 0; i < excess; i += 1) {
      storage.removeItem(entries[i].key);
    }
  } catch {
    // Ignore storage errors.
  }
}

export function writeWorkbenchSwr(
  userId: string | null | undefined,
  projectId: string | null | undefined,
  payload: WorkbenchSwrPayload,
  now: number = Date.now(),
): boolean {
  if (!userId || !projectId) return false;
  if (!isValidProject(payload.project, projectId) || !Array.isArray(payload.files)) return false;
  const storage = getSessionStorage();
  if (!storage) return false;

  const key = getWorkbenchSwrKey(userId, projectId);
  const fullEntry: WorkbenchSwrEntry = {
    schema: WORKBENCH_SWR_SCHEMA,
    userId,
    projectId,
    cachedAt: now,
    project: payload.project,
    files: payload.files,
    initial_file: isValidInitialFile(payload.initial_file, projectId) ? payload.initial_file : null,
  };

  let serialized = JSON.stringify(fullEntry);
  if (serialized.length > MAX_WORKBENCH_SWR_ENTRY_CHARS && fullEntry.initial_file !== null) {
    // Drop a huge manuscript body from the SWR entry so project + file tree still paint instantly.
    serialized = JSON.stringify({ ...fullEntry, initial_file: null });
  }
  if (serialized.length > MAX_WORKBENCH_SWR_ENTRY_CHARS) {
    return false;
  }

  pruneWorkbenchSwrEntries(storage, key);
  try {
    storage.setItem(key, serialized);
    return true;
  } catch {
    // Storage quota full or unavailable; never throw or claim draft protection.
    return false;
  }
}

export function clearWorkbenchSwrCache(): void {
  const storage = getSessionStorage();
  if (!storage) return;
  try {
    const keysToRemove: string[] = [];
    for (let index = 0; index < storage.length; index += 1) {
      const key = storage.key(index);
      if (key && key.startsWith(WORKBENCH_SWR_PREFIX)) {
        keysToRemove.push(key);
      }
    }
    for (const key of keysToRemove) {
      storage.removeItem(key);
    }
  } catch {
    // Ignore storage access errors.
  }
}

export function writeAuthSessionUserSnapshot<TUser extends { id: string }>(
  user: TUser | null | undefined,
  now: number = Date.now(),
): boolean {
  if (!user || typeof user.id !== "string" || !user.id) return false;
  const storage = getSessionStorage();
  if (!storage) return false;
  try {
    const envelope: AuthSessionUserEnvelope<TUser> = {
      schema: AUTH_SESSION_USER_SCHEMA,
      savedAt: now,
      user,
    };
    storage.setItem(AUTH_SESSION_USER_KEY, JSON.stringify(envelope));
    return true;
  } catch {
    return false;
  }
}

export function readAuthSessionUserSnapshot<TUser extends { id: string; username: string; email: string }>(
  now: number = Date.now(),
): TUser | null {
  const storage = getSessionStorage();
  if (!storage) return null;
  let raw: string | null = null;
  try {
    raw = storage.getItem(AUTH_SESSION_USER_KEY);
  } catch {
    return null;
  }
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<AuthSessionUserEnvelope<Partial<TUser>>>;
    const candidate = parsed.user;
    if (
      parsed.schema !== AUTH_SESSION_USER_SCHEMA ||
      typeof parsed.savedAt !== "number" ||
      !Number.isFinite(parsed.savedAt) ||
      now - parsed.savedAt > AUTH_SESSION_USER_MAX_AGE_MS ||
      !candidate ||
      typeof candidate !== "object" ||
      typeof candidate.id !== "string" ||
      !candidate.id ||
      typeof candidate.username !== "string" ||
      typeof candidate.email !== "string"
    ) {
      try {
        storage.removeItem(AUTH_SESSION_USER_KEY);
      } catch {
        // Ignore storage errors.
      }
      return null;
    }
    return candidate as TUser;
  } catch {
    try {
      storage.removeItem(AUTH_SESSION_USER_KEY);
    } catch {
      // Ignore storage errors.
    }
    return null;
  }
}

export function clearAuthSessionUserSnapshot(): void {
  const storage = getSessionStorage();
  if (!storage) return;
  try {
    storage.removeItem(AUTH_SESSION_USER_KEY);
  } catch {
    // Ignore storage access errors.
  }
}
