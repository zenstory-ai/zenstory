/**
 * Shared types, constants, and pure helpers for the Editor workbench shell.
 *
 * @module components/editor/editorFileHelpers
 */

import { fileApi } from "../../lib/api";
import { FOLDER_TYPE_MAP } from "../../lib/folderTypeMap";
import { logger } from "../../lib/logger";
import type { EditorDraftSnapshot } from "../../lib/editorDraftRecovery";
import type { DiffReviewState, FileTreeNode } from "../../types";

/** Retry delays for re-reading the open file after an external write. */
export const SERVER_SYNC_RETRY_DELAYS_MS = [1000, 3000];

export type EmptyStateFileType = "draft" | "outline" | "character" | "lore" | "snippet" | "script";

/**
 * The comparison Editor opened for "the author wrote on an older copy": the
 * author's own text on the left, the server copy merged with the author's
 * edits on the right. Every listed change is the other side's (the AI's).
 */
export interface ConflictReview {
  fileId: string;
  /** The author's text (left side). */
  originalContent: string;
  /** The server copy plus every author edit that applies cleanly (right side). */
  modifiedContent: string;
  /** The server copy the comparison was opened against (the save token's copy). */
  serverContent: string;
  /** The copy the author's text was written on. */
  base: string;
  /**
   * Places where the author and the AI rewrote the same words. They start
   * rejected, which keeps the author's words there; until the author decides
   * one of them, a draft kept on leaving carries no save token, so reopening
   * asks instead of silently writing over the AI's copy.
   */
  undecidedConflictIds: Set<string>;
}

/**
 * What opening the comparison found: the author's text is already in the
 * server copy ("server"), the server copy adds nothing to the author's text
 * ("local"), or there are changes from the other side to review ("review").
 */
export type ConflictReviewOutcome = "server" | "local" | "review";

export interface ServerBaselineState {
  version: number;
  fileId: string;
  title: string;
  content: string;
  updatedAt?: string;
}

export interface ServerSyncIssueState {
  fileId: string;
  retrying: boolean;
}

export interface RemovedDraftEntry {
  snapshot: EditorDraftSnapshot;
  persisted: boolean;
}

export interface DraftRecoveryState {
  snapshot: EditorDraftSnapshot;
  serverTitle: string;
  serverContent: string;
  kind: "recover" | "conflict";
  applied: boolean;
}

export const isSameReview = (
  review: DiffReviewState | null,
  conflict: ConflictReview | null,
): boolean =>
  review !== null &&
  conflict !== null &&
  review.fileId === conflict.fileId &&
  review.originalContent === conflict.originalContent &&
  review.modifiedContent === conflict.modifiedContent;

/** Apple keyboards label the search shortcut ⌘ K; everything else uses Ctrl K. */
export const isApplePlatform = (): boolean => {
  const uaDataPlatform = (navigator as Navigator & { userAgentData?: { platform?: string } })
    .userAgentData?.platform;
  return /mac|iphone|ipad|ipod/i.test(uaDataPlatform || navigator.platform || "");
};

/**
 * Resolve the best parent folder for a new file based on file type.
 *
 * Priority:
 * 1) Canonical deterministic folder ID (<projectId>-<type>-folder)
 * 2) Folder title mapping fallback (for legacy/migrated projects)
 */
export async function resolveCreateParentId(
  currentProjectId: string | null,
  fileType: EmptyStateFileType,
): Promise<string | undefined> {
  if (!currentProjectId) return undefined;

  try {
    const { tree } = await fileApi.getTree(currentProjectId);

    const rootFolders = (tree || []).filter((node) => node.file_type === "folder");
    const rootFolderIds = new Set(rootFolders.map((node) => node.id));

    const canonicalSuffixByType: Record<EmptyStateFileType, string> = {
      draft: "draft-folder",
      outline: "outline-folder",
      character: "character-folder",
      lore: "lore-folder",
      snippet: "material-folder",
      script: "script-folder",
    };

    const canonicalFolderId = `${currentProjectId}-${canonicalSuffixByType[fileType]}`;
    if (rootFolderIds.has(canonicalFolderId)) {
      return canonicalFolderId;
    }

    const queue: FileTreeNode[] = [...(tree || [])];
    while (queue.length > 0) {
      const node = queue.shift();
      if (!node) continue;

      if (node.file_type === "folder" && FOLDER_TYPE_MAP[node.title] === fileType) {
        return node.id;
      }

      if (node.children?.length) {
        queue.push(...node.children);
      }
    }
  } catch (err) {
    logger.warn("Failed to resolve create parent folder, falling back to root create", err);
  }

  return undefined;
}
