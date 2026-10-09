/**
 * Remembers the last file a user opened in each project so reopening the
 * project lands on it instead of an empty editor.
 *
 * Keyed by `userId:projectId`: a shared browser must not hand one account's
 * file id to another, and each project restores independently. The record
 * only holds the id; the caller re-validates it against the server before
 * selecting it (it may have been deleted or moved since).
 */
export const LAST_OPENED_FILE_STORAGE_PREFIX = "zenstory_last_opened_file_v1";
export const LAST_OPENED_FILE_TTL_MS = 30 * 24 * 60 * 60 * 1000;

interface StoredLastOpenedFile {
  id: string;
  ts: number;
}

function buildKey(userId: string, projectId: string): string {
  return `${LAST_OPENED_FILE_STORAGE_PREFIX}:${userId}:${projectId}`;
}

export function setLastOpenedFile(userId: string, projectId: string, fileId: string): void {
  if (!userId || !projectId || !fileId) return;
  try {
    const record: StoredLastOpenedFile = { id: fileId, ts: Date.now() };
    localStorage.setItem(buildKey(userId, projectId), JSON.stringify(record));
  } catch {
    // Restoring the last file is a convenience; losing it is harmless.
  }
}

export function getLastOpenedFile(
  userId: string,
  projectId: string,
  now: number = Date.now(),
): string | null {
  if (!userId || !projectId) return null;
  let raw: string | null;
  try {
    raw = localStorage.getItem(buildKey(userId, projectId));
  } catch {
    return null;
  }
  if (!raw) return null;

  try {
    const parsed = JSON.parse(raw) as Partial<StoredLastOpenedFile> | null;
    if (
      !parsed ||
      typeof parsed.id !== "string" ||
      !parsed.id ||
      typeof parsed.ts !== "number" ||
      !Number.isFinite(parsed.ts) ||
      now - parsed.ts > LAST_OPENED_FILE_TTL_MS
    ) {
      clearLastOpenedFile(userId, projectId);
      return null;
    }
    return parsed.id;
  } catch {
    clearLastOpenedFile(userId, projectId);
    return null;
  }
}

interface TreeNodeLike {
  id: string;
  children?: TreeNodeLike[] | null;
}

/**
 * Ids of the folders that contain `targetId`, outermost first, so a file tree
 * can expand down to a restored (or otherwise selected) file. Returns `null`
 * when the id is not in the tree (yet).
 */
export function findAncestorFolderIds(tree: TreeNodeLike[], targetId: string): string[] | null {
  for (const node of tree) {
    if (node.id === targetId) return [];
    if (node.children && node.children.length > 0) {
      const below = findAncestorFolderIds(node.children, targetId);
      if (below) return [node.id, ...below];
    }
  }
  return null;
}

export function clearLastOpenedFile(userId: string, projectId: string): void {
  if (!userId || !projectId) return;
  try {
    localStorage.removeItem(buildKey(userId, projectId));
  } catch {
    // Nothing to clean up when storage is unavailable.
  }
}
