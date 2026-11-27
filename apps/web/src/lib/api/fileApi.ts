import type {
  File,
  FileTreeNode,
  FileVersionListResponse,
  RollbackResponse,
  VersionComparison,
  VersionContentResponse,
} from "../../types";
import { api, ApiError, authenticatedFetch, readApiError } from "../apiClient";
import { isFileTitleTooLong } from "../fileTitle";
import { captureException, trackEvent } from "../analytics";

const MATERIAL_UPLOAD_MAX_BYTES = 2_000_000; // 2MB
const MATERIAL_UPLOAD_EXTENSIONS = [".txt", ".md"] as const;

// Autosave fires after every typing pause; one file_saved per file per
// 5 minutes is enough to see who writes, without paying per keystroke burst.
const FILE_SAVED_TRACK_INTERVAL_MS = 5 * 60 * 1000;
const fileSavedTrackedAt = new Map<string, number>();

function shouldTrackFileSaved(fileId: string, now: number = Date.now()): boolean {
  const last = fileSavedTrackedAt.get(fileId);
  if (last !== undefined && now - last < FILE_SAVED_TRACK_INTERVAL_MS) return false;
  fileSavedTrackedAt.set(fileId, now);
  return true;
}

/**
 * File Version API endpoints.
 *
 * Provides file-level version history for tracking changes to individual files.
 * Every file update creates a new version, allowing users to compare versions
 * and rollback to previous states.
 */
export const fileVersionApi = {
  /**
   * Get version history for a file.
   *
   * Returns paginated list of versions with metadata.
   * Auto-save versions are excluded by default unless explicitly requested.
   * The first page (offset 0) also carries `current_version_number`: the
   * version matching the live text, or null when unsaved-to-history edits exist.
   */
  getVersions: (
    fileId: string,
    options?: {
      limit?: number;
      offset?: number;
      includeAutoSave?: boolean;
    },
  ) => {
    const params = new URLSearchParams();
    if (options?.limit) params.append("limit", String(options.limit));
    if (options?.offset) params.append("offset", String(options.offset));
    if (options?.includeAutoSave)
      params.append("include_auto_save", String(options.includeAutoSave));
    const query = params.toString() ? `?${params}` : "";
    return api.get<FileVersionListResponse>(
      `/api/v1/files/${fileId}/versions${query}`,
    );
  },

  /** Get content at a specific version number. */
  getVersionContent: (fileId: string, versionNumber: number) =>
    api.get<VersionContentResponse>(
      `/api/v1/files/${fileId}/versions/${versionNumber}/content`,
    ),

  /** Compare two versions of a file. */
  compare: (fileId: string, v1: number, v2: number) =>
    api.get<VersionComparison>(
      `/api/v1/files/${fileId}/versions/compare?v1=${v1}&v2=${v2}`,
    ),

  /**
   * Rollback a file to a specific version.
   *
   * Restores the file content to its state at the specified version.
   * Creates a new version with change_type 'restore'.
   */
  rollback: (
    fileId: string,
    versionNumber: number,
    expectedUpdatedAt?: string,
    card?: { chatMessageId?: string; toolUseId?: string; expectedAfterContentSha256?: string },
  ) => {
    const path = `/api/v1/files/${fileId}/versions/${versionNumber}/rollback`;
    if (expectedUpdatedAt === undefined) return api.post<RollbackResponse>(path);
    // An undo from a chat edit card names that card, so the server can mark it undone.
    const cardIds = card?.chatMessageId && card.toolUseId
      ? { chat_message_id: card.chatMessageId, tool_use_id: card.toolUseId }
      : {};
    // The text that edit wrote: a later change that was undone again leaves the same text on a
    // newer token, and the server still accepts the undo when the file holds exactly these bytes.
    const contentHash = card?.expectedAfterContentSha256
      ? { expected_after_content_sha256: card.expectedAfterContentSha256 }
      : {};
    return api.post<RollbackResponse>(path, {
      expected_updated_at: expectedUpdatedAt,
      ...cardIds,
      ...contentHash,
    });
  },
};

export type FileChangeType = "create" | "edit" | "ai_edit" | "restore" | "auto_save";
export type FileChangeSource = "user" | "ai" | "system";

export interface FileUpdateVersionIntent {
  change_type?: FileChangeType;
  change_source?: FileChangeSource;
  change_summary?: string;
  skip_version?: boolean;
  /**
   * Optional word count for the updated content.
   *
   * Used by the backend to persist `file_metadata.word_count` without having to
   * re-scan large draft/script content on dashboard reads.
   */
  word_count?: number;
  /**
   * Optimistic-concurrency token: the `updated_at` this client saw when it
   * loaded the content it is now saving.
   *
   * When this field is present and the server-side `updated_at` has moved on,
   * the backend answers 409 (`ERR_RESOURCE_CONFLICT`) with the current content
   * instead of overwriting.
   */
  base_updated_at?: string;
}

/**
 * File API endpoints.
 *
 * Provides CRUD operations for files in the unified file model.
 * Files support multiple types (outline, draft, character, lore, material)
 * and hierarchical organization via parent-child relationships.
 */
export const fileApi = {
  /** Get a single file by ID. */
  get: (fileId: string) => api.get<File>(`/api/v1/files/${fileId}`),

  /** Create a new file. */
  create: async (
    projectId: string,
    file: {
      title: string;
      file_type?: string;
      content?: string;
      parent_id?: string;
      order?: number;
      metadata?: Record<string, unknown>;
    },
  ) => {
    if (isFileTitleTooLong(file.title)) throw new ApiError(422, "ERR_FILE_TITLE_TOO_LONG");
    const created = await api.post<File>(`/api/v1/projects/${projectId}/files`, file);
    trackEvent("file_created", {
      project_id: projectId,
      file_id: created.id,
      file_type: created.file_type ?? file.file_type ?? undefined,
      parent_present: Boolean(file.parent_id),
    });
    return created;
  },

  /** Update an existing file. */
  update: async (
    fileId: string,
    file: FileUpdateVersionIntent & {
      title?: string;
      content?: string;
      parent_id?: string;
      order?: number;
      metadata?: Record<string, unknown>;
    },
  ) => {
    if (file.title !== undefined && isFileTitleTooLong(file.title)) {
      throw new ApiError(422, "ERR_FILE_TITLE_TOO_LONG");
    }
    const updated = await api.put<File>(`/api/v1/files/${fileId}`, file);
    if (Object.prototype.hasOwnProperty.call(file, "content") && shouldTrackFileSaved(fileId)) {
      trackEvent("file_saved", {
        file_id: fileId,
        project_id: updated.project_id,
        file_type: updated.file_type,
        change_type: file.change_type ?? "edit",
        change_source: file.change_source ?? "user",
        skip_version: Boolean(file.skip_version),
        word_count: file.word_count,
      });
    }
    return updated;
  },

  /** Delete a file (for folders, pass recursive=true to delete children). */
  delete: (fileId: string, recursive = false) => {
    const params = recursive ? "?recursive=true" : "";
    return api.delete(`/api/v1/files/${fileId}${params}`);
  },

  restore: (
    fileId: string,
    options?: { chatMessageId?: string; toolUseId?: string },
  ) =>
    api.post<{
      id: string;
      title: string;
      restored: boolean;
      already_restored: boolean;
      restored_count: number;
      file_ids?: string[];
    }>(`/api/v1/files/${fileId}/restore`, {
      chat_message_id: options?.chatMessageId,
      tool_use_id: options?.toolUseId,
    }),

  /** Get the complete file tree for a project. */
  getTree: (projectId: string, options?: RequestInit) => {
    const url = `/api/v1/projects/${projectId}/file-tree`;
    return options
      ? api.get<{ tree: FileTreeNode[] }>(url, options)
      : api.get<{ tree: FileTreeNode[] }>(url);
  },

  /**
   * Upload a .txt / .md file as a material (snippet), read as plain text.
   * Constraints: .txt / .md only, max 2MB and 200,000 characters.
   */
  upload: async (projectId: string, file: globalThis.File): Promise<File> => {
    const normalizedName = file.name.trim().toLowerCase();
    const fileExtension = MATERIAL_UPLOAD_EXTENSIONS.find((ext) => normalizedName.endsWith(ext));
    if (!fileExtension) {
      trackEvent("material_upload_failed", {
        project_id: projectId,
        reason: "invalid_file_type",
        file_size_bytes: file.size,
      });
      throw new ApiError(400, "ERR_FILE_TYPE_INVALID");
    }
    if (file.size > MATERIAL_UPLOAD_MAX_BYTES) {
      trackEvent("material_upload_failed", {
        project_id: projectId,
        reason: "file_too_large",
        file_size_bytes: file.size,
      });
      throw new ApiError(400, "ERR_FILE_TOO_LARGE");
    }

    const formData = new FormData();
    formData.append("file", file);

    const response = await authenticatedFetch(
      `/api/v1/projects/${projectId}/files/upload`,
      { method: "POST", body: formData },
    );

    if (!response.ok) {
      const uploadError = await readApiError(response);
      trackEvent("material_upload_failed", {
        project_id: projectId,
        reason: "request_failed",
        status: response.status,
        file_size_bytes: file.size,
      });
      captureException(uploadError, {
        feature_area: "file_upload",
        action: "material_upload",
        project_id: projectId,
        status: response.status,
      });
      throw uploadError;
    }

    const uploaded = (await response.json()) as File;
    trackEvent("material_uploaded", {
      project_id: projectId,
      file_id: uploaded.id,
      file_type: uploaded.file_type,
      file_size_bytes: file.size,
      file_extension: fileExtension.slice(1),
    });
    return uploaded;
  },

  /**
   * Upload a .txt/.md file as draft(s) under the draft folder.
   * Auto-splits by chapter headings. No length-based splitting.
   */
  uploadDraft: async (
    projectId: string,
    file: globalThis.File,
    parentId?: string,
  ): Promise<{ files: File[]; total: number; errors: string[] }> => {
    const formData = new FormData();
    formData.append("files", file);
    if (parentId) {
      formData.append("parent_id", parentId);
    }

    const response = await authenticatedFetch(
      `/api/v1/projects/${projectId}/files/upload-drafts`,
      { method: "POST", body: formData },
    );

    if (!response.ok) {
      throw await readApiError(response);
    }

    return (await response.json()) as {
      files: File[];
      total: number;
      errors: string[];
    };
  },
};
