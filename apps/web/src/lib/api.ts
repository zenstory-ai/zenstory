/**
 * Core API Client Library for zenstory Novel Writing Workbench
 *
 * This module provides typed API client functions for all main application endpoints:
 * - Authentication (register, login, OAuth, token refresh)
 * - Projects (CRUD operations, templates)
 * - Files (CRUD operations, tree structure, upload)
 * - Versions (file-level version history, comparison, rollback)
 * - Snapshots (project-level snapshots, comparison, rollback)
 * - Export (draft export to various formats)
 *
 * All API functions use the centralized `api` client from apiClient.ts which handles:
 * - Authentication header injection
 * - Token refresh on 401 errors
 * - Error handling and ApiError throwing
 *
 * @module lib/api
 */

import type {
  File,
  FileTreeNode,
  FileVersion,
  FileVersionListResponse,
  PatchProjectRequest,
  Project,
  RollbackResponse,
  SnapshotComparison,
  Snapshot,
  VersionComparison,
  VersionContentResponse,
} from "../types";
import { api, ApiError, apiErrorFromPayload, tryRefreshToken, getAccessToken, getApiBase, resolveOwnedAuthSession } from "./apiClient";
import { resolveApiErrorMessage } from "./errorHandler";
import { getLocale } from "./i18n-helpers";
import { logger } from "./logger";
import { captureException, trackEvent } from "./analytics";

const MATERIAL_UPLOAD_MAX_BYTES = 2_000_000; // 2MB

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
 * Authentication API endpoints.
 *
 * Handles user registration, login, OAuth flows, and email verification.
 * These endpoints do not require authentication (except token refresh).
 *
 * @namespace authApi
 */
export const authApi = {
  /**
   * Initiate Google OAuth login flow.
   *
   * Redirects the browser to the backend's Google OAuth endpoint,
   * which then redirects to Google's consent screen.
   * After successful authentication, Google redirects back to the callback URL.
   */
  googleLogin: () => {
    const url = `${getApiBase()}/api/auth/google`;
    window.location.href = url;
  },

  /**
   * Apple OAuth login - pending backend implementation.
   *
   * @feature_request Sign in with Apple support
   * @backend_needed Add /api/v1/auth/apple endpoint with Apple ID integration
   * @see https://developer.apple.com/sign-in-with-apple/
   * @see apps/server/api/auth.py for OAuth implementation reference
   */
  appleLogin: () => {
    logger.log('Apple OAuth coming soon');
  },

  /**
   * Resolve registration invite-code policy (supports gray rollout experiments).
   */
  getRegistrationPolicy: async (params: { email?: string; username?: string } = {}) => {
    const search = new URLSearchParams();
    const email = params.email?.trim();
    const username = params.username?.trim();
    if (email) search.set("email", email);
    if (username) search.set("username", username);
    const suffix = search.toString();
    const response = await fetch(
      `${getApiBase()}/api/auth/register-policy${suffix ? `?${suffix}` : ""}`,
      { method: "GET" }
    );
    if (!response.ok) {
      const error = await response.json();
      throw new ApiError(
        response.status,
        resolveApiErrorMessage(error, "ERR_AUTH_REGISTRATION_FAILED"),
      );
    }
    return response.json() as Promise<{
      invite_code_optional: boolean;
      variant: string;
      rollout_percent: number;
    }>;
  },

  /**
   * Register a new user account.
   *
   * @param data - Registration data
   * @param data.username - Desired username (unique)
   * @param data.email - User's email address
   * @param data.password - User's password (will be hashed server-side)
   * @param data.invite_code - Optional invite code for closed beta or referrals
   * @returns Promise resolving to the created user object with tokens
   * @throws {ApiError} ERR_AUTH_REGISTRATION_FAILED if registration fails
   *
   * @example
   * ```ts
   * const user = await authApi.register({
   *   username: 'writer',
   *   email: 'writer@example.com',
   *   password: 'securePassword123'
   * });
   * ```
   */
  register: async (data: { username: string; email: string; password: string; invite_code?: string }) => {
    const response = await fetch(`${getApiBase()}/api/auth/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (!response.ok) {
      const error = await response.json();
      throw new ApiError(
        response.status,
        resolveApiErrorMessage(error, 'ERR_AUTH_REGISTRATION_FAILED'),
      );
    }
    return response.json();
  },

  /**
   * Authenticate a user with username and password.
   *
   * Uses form-data format as required by OAuth2 password flow.
   *
   * @param username - User's username or email
   * @param password - User's password
   * @returns Promise resolving to authentication tokens and user info
   * @throws {ApiError} ERR_AUTH_INVALID_CREDENTIALS if credentials are invalid
   *
   * @example
   * ```ts
   * const { access_token, refresh_token } = await authApi.login('writer', 'password');
   * ```
   */
  login: async (username: string, password: string) => {
    const formData = new FormData();
    formData.append('username', username);
    formData.append('password', password);
    const response = await fetch(`${getApiBase()}/api/auth/login`, {
      method: 'POST',
      body: formData,
    });
    if (!response.ok) {
      const error = await response.json();
      throw new ApiError(
        response.status,
        resolveApiErrorMessage(error, 'ERR_AUTH_INVALID_CREDENTIALS'),
      );
    }
    return response.json();
  },

  /**
   * Refresh an expired access token using a valid refresh token.
   *
   * Called automatically by apiClient when a 401 response is received.
   *
   * @param refreshToken - The refresh token obtained during login
   * @returns Promise resolving to new access and refresh tokens
   * @throws {ApiError} ERR_AUTH_TOKEN_INVALID if refresh token is expired or invalid
   */
  refreshToken: async (refreshToken: string) => {
    const response = await fetch(`${getApiBase()}/api/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    if (!response.ok) {
      const error = await response.json();
      throw new ApiError(
        response.status,
        resolveApiErrorMessage(error, 'ERR_AUTH_TOKEN_INVALID'),
      );
    }
    return response.json();
  },

  /**
   * Verify a user's email address with a verification code.
   *
   * Called after the user receives a verification email with a code.
   *
   * @param email - The email address to verify
   * @param code - The verification code from the email
   * @returns Promise resolving to verification status
   * @throws {ApiError} ERR_AUTH_INVALID_VERIFICATION_CODE if code is invalid or expired
   */
  verifyEmail: async (email: string, code: string) => {
    const response = await fetch(`${getApiBase()}/api/auth/verify-email`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, code }),
    });
    if (!response.ok) {
      const error = await response.json();
      throw new ApiError(
        response.status,
        resolveApiErrorMessage(error, 'ERR_AUTH_INVALID_VERIFICATION_CODE'),
      );
    }
    return response.json();
  },

  /**
   * Resend the verification email to a user.
   *
   * Useful if the verification code expired or the email was lost.
   *
   * @param email - The email address to resend verification to
   * @returns Promise resolving to success status
   * @throws {ApiError} ERR_AUTH_RESEND_FAILED if resend fails (rate limiting, etc.)
   */
  resendVerification: async (email: string) => {
    const response = await fetch(`${getApiBase()}/api/auth/resend-verification`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email }),
    });
    if (!response.ok) {
      const error = await response.json();
      throw new ApiError(
        response.status,
        resolveApiErrorMessage(error, 'ERR_AUTH_RESEND_FAILED'),
      );
    }
    return response.json();
  },

  /**
   * Check if an email address has been verified.
   *
   * Used to show verification status in the UI.
   *
   * @param email - The email address to check
   * @returns Promise resolving to verification status object
   * @throws {ApiError} ERR_VALIDATION_ERROR if email format is invalid
   */
  checkVerification: async (email: string) => {
    const response = await fetch(`${getApiBase()}/api/auth/check-verification?email=${encodeURIComponent(email)}`);
    if (!response.ok) {
      const error = await response.json();
      throw new ApiError(
        response.status,
        resolveApiErrorMessage(error, 'ERR_VALIDATION_ERROR'),
      );
    }
    return response.json();
  },
};

/**
 * Export API endpoints.
 *
 * Handles project export functionality for downloading content in various formats.
 * Exports are downloaded directly to the user's browser.
 *
 * @namespace exportApi
 */
export const exportApi = {
  /**
   * Export all drafts from a project as a TXT file.
   *
   * Fetches the export from the backend and triggers a browser download.
   * The filename is extracted from the Content-Disposition header or uses
   * a localized default ("Export.txt" / "导出.txt").
   *
   * Handles authentication automatically with token refresh on 401 errors.
   *
   * @param projectId - The UUID of the project to export
   * @returns Promise that resolves when download is initiated
   * @throws {ApiError} ERR_EXPORT_NO_DRAFTS if project has no drafts
   *
   * @example
   * ```ts
   * // Export all drafts from a project
   * await exportApi.exportDrafts('project-uuid-123');
   * // Browser will download a file named "{ProjectName}_drafts.txt"
   * ```
   */
  exportDrafts: async (projectId: string): Promise<void> => {
    const doFetch = async (isRetry = false): Promise<Response> => {
      const accessToken = getAccessToken();
      const response = await fetch(
        `${getApiBase()}/api/v1/projects/${projectId}/export/drafts`,
        {
          method: "GET",
          headers: accessToken
            ? { Authorization: `Bearer ${accessToken}` }
            : undefined,
        }
      );

      // Handle 401 - try to refresh token and retry once
      if (response.status === 401 && !isRetry) {
        const refreshed = await tryRefreshToken();
        if (refreshed) {
          return doFetch(true);
        }
      }

      return response;
    };

    const response = await doFetch();

    if (!response.ok) {
      let errorMessage = "ERR_EXPORT_NO_DRAFTS";
      try {
        const errorData = await response.json();
        errorMessage = resolveApiErrorMessage(errorData, errorMessage);
      } catch {
        // Could not parse error response
      }
      throw new ApiError(response.status, errorMessage);
    }

    // Extract filename from Content-Disposition header
    const disposition = response.headers.get("Content-Disposition");
    const locale = getLocale();
    const exportFilename = locale === 'en' ? 'Export' : '导出';
    let filename = `${exportFilename}.txt`;
    if (disposition) {
      // Try RFC 5987 format: filename*=UTF-8''encoded_name
      const rfc5987Match = disposition.match(/filename\*=UTF-8''(.+)/);
      if (rfc5987Match) {
        filename = decodeURIComponent(rfc5987Match[1]);
      } else {
        // Try standard format: filename="name"
        const standardMatch = disposition.match(/filename="?([^"]+)"?/);
        if (standardMatch) {
          filename = standardMatch[1];
        }
      }
    }

    // Trigger browser download
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  },
};

/**
 * Project template configuration.
 *
 * Defines the structure and default content for a new project based on its type.
 * Templates specify default folders, file type mappings, and naming conventions.
 */
export interface ProjectTemplate {
  /** Display name of the template */
  name: string;
  /** Human-readable description of the template's purpose */
  description: string;
  /** Icon identifier for UI display */
  icon: string;
  /** Default folder structure to create */
  folders: Array<{ id: string; title: string; file_type: string; order: number }>;
  /** Mapping of generic file categories to specific file types */
  file_type_mapping: Record<string, string>;
  /** Default name for new projects using this template */
  default_project_name: string;
}

/**
 * Project API endpoints.
 *
 * Provides CRUD operations for projects, which are the top-level containers
 * for all files (outlines, drafts, characters, lore) in the workbench.
 *
 * @namespace projectApi
 */
export const projectApi = {
  /**
   * Get all projects for the current user.
   *
   * @returns Promise resolving to array of Project objects
   *
   * @example
   * ```ts
   * const projects = await projectApi.getAll();
   * ```
   */
  getAll: () => api.get<Project[]>("/api/v1/projects"),

  /**
   * Get a single project by ID.
   *
   * @param projectId - The UUID of the project to retrieve
   * @returns Promise resolving to the Project object
   *
   * @example
   * ```ts
   * const project = await projectApi.get('project-uuid-123');
   * ```
   */
  get: (projectId: string) => api.get<Project>(`/api/v1/projects/${projectId}`),

  /**
   * Create a new project.
   *
   * @param project - Partial project data (name is required)
   * @returns Promise resolving to the created Project object
   *
   * @example
   * ```ts
   * const newProject = await projectApi.create({
   *   name: 'My Novel',
   *   project_type: 'novel'
   * });
   * ```
   */
  create: (project: Partial<Project>) =>
    api.post<Project>("/api/v1/projects", project),

  /**
   * Update an existing project.
   *
   * @param projectId - The UUID of the project to update
   * @param project - Partial project data to update
   * @returns Promise resolving to the updated Project object
   *
   * @example
   * ```ts
   * const updated = await projectApi.update('project-uuid', { name: 'New Name' });
   * ```
   */
  update: (projectId: string, project: Partial<Project>) =>
    api.put<Project>(`/api/v1/projects/${projectId}`, project),

  /**
   * Delete a project and all its files.
   *
   * @param projectId - The UUID of the project to delete
   * @returns Promise resolving when deletion is complete
   *
   * @example
   * ```ts
   * await projectApi.delete('project-uuid');
   * ```
   */
  delete: (projectId: string) => api.delete(`/api/v1/projects/${projectId}`),

  /**
   * Partially update project status fields.
   *
   * Used for AI context awareness to track project progress without
   * modifying core project settings. Updates fields like summary,
   * current_phase, writing_style, and notes.
   *
   * @param projectId - The UUID of the project to patch
   * @param data - Partial project data with allowed fields
   * @returns Promise resolving to the updated Project object
   *
   * @example
   * ```ts
   * await projectApi.patch('project-uuid', {
   *   current_phase: 'drafting',
   *   summary: 'A tale of two cities...'
   * });
   * ```
   */
  patch: (projectId: string, data: PatchProjectRequest) =>
    api.patch<Project>(`/api/v1/projects/${projectId}`, data),

  /**
   * Get all available project templates.
   *
   * Templates define default structures for different project types
   * (novel, short_story, script, etc.).
   *
   * @returns Promise resolving to a map of project types to templates
   *
   * @example
   * ```ts
   * const templates = await projectApi.getTemplates();
   * const novelTemplate = templates['novel'];
   * ```
   */
  getTemplates: () => api.get<Record<string, ProjectTemplate>>("/api/v1/project-templates"),
};

/**
 * Version/Snapshot API endpoints.
 *
 * Provides project-level snapshot functionality for saving and restoring
 * project state at specific points in time. Unlike file-level versions,
 * snapshots capture the entire project state.
 *
 * @namespace versionApi
 */
export const versionApi = {
  /**
   * Get all snapshots for a project.
   *
   * @param projectId - The UUID of the project
   * @param options - Optional filters
   * @param options.fileId - Filter snapshots to a specific file
   * @param options.limit - Maximum number of snapshots to return
   * @returns Promise resolving to array of Snapshot objects
   *
   * @example
   * ```ts
   * // Get all snapshots for a project
   * const snapshots = await versionApi.getSnapshots('project-uuid');
   *
   * // Get last 10 snapshots for a specific file
   * const fileSnapshots = await versionApi.getSnapshots('project-uuid', {
   *   fileId: 'file-uuid',
   *   limit: 10
   * });
   * ```
   */
  getSnapshots: (
    projectId: string,
    options?: {
      fileId?: string;
      limit?: number;
      offset?: number;
    },
  ) => {
    const params = new URLSearchParams();
    if (options?.fileId)
      params.append("file_id", String(options.fileId));
    if (options?.limit !== undefined) params.append("limit", String(options.limit));
    if (options?.offset !== undefined) params.append("offset", String(options.offset));
    const query = params.toString() ? `?${params}` : "";
    return api.get<Snapshot[]>(
      `/api/v1/projects/${projectId}/snapshots${query}`,
    );
  },

  /**
   * Get a single snapshot by ID.
   *
   * @param snapshotId - The UUID of the snapshot
   * @returns Promise resolving to the Snapshot object
   *
   * @example
   * ```ts
   * const snapshot = await versionApi.getSnapshot('snapshot-uuid');
   * ```
   */
  getSnapshot: (snapshotId: string) =>
    api.get<Snapshot>(`/api/v1/snapshots/${snapshotId}`),

  /**
   * Create a new project snapshot.
   *
   * Captures the current state of all files in the project.
   *
   * @param projectId - The UUID of the project
   * @param options - Optional snapshot configuration
   * @param options.fileId - Limit snapshot to a specific file (partial snapshot)
   * @param options.description - Human-readable description of the snapshot
   * @param options.snapshotType - Type classification (e.g., 'milestone', 'backup')
   * @returns Promise resolving to the created Snapshot object
   *
   * @example
   * ```ts
   * const snapshot = await versionApi.createSnapshot('project-uuid', {
   *   description: 'Before major revision',
   *   snapshotType: 'milestone'
   * });
   * ```
   */
  createSnapshot: (
    projectId: string,
    options?: {
      fileId?: string;
      description?: string;
      snapshotType?: string;
    },
  ) => {
    const payload = {
      file_id: options?.fileId,
      description: options?.description,
      snapshot_type: options?.snapshotType,
    };
    return api.post<Snapshot>(`/api/v1/projects/${projectId}/snapshots`, payload);
  },

  /**
   * Compare two snapshots to see differences.
   *
   * @param snapshotId1 - The UUID of the first (older) snapshot
   * @param snapshotId2 - The UUID of the second (newer) snapshot
   * @returns Promise resolving to SnapshotComparison with diff details
   *
   * @example
   * ```ts
   * const comparison = await versionApi.compare('old-snapshot', 'new-snapshot');
   * console.log(comparison.changes); // Array of file changes
   * ```
   */
  compare: (snapshotId1: string, snapshotId2: string) =>
    api.get<SnapshotComparison>(
      `/api/v1/snapshots/${snapshotId1}/compare/${snapshotId2}`,
    ),

  /**
   * Rollback a project to a previous snapshot state.
   *
   * This restores all files to their state at the time of the snapshot.
   * Use with caution as this operation cannot be easily undone.
   *
   * @param snapshotId - The UUID of the snapshot to rollback to
   * @returns Promise resolving to success message
   *
   * @example
   * ```ts
   * await versionApi.rollback('milestone-snapshot');
   * ```
   */
  rollback: (snapshotId: string) =>
    api.post<{ message: string }>(`/api/v1/snapshots/${snapshotId}/rollback`),

  /**
   * Update a snapshot's metadata.
   *
   * Currently only supports updating the description field.
   *
   * @param snapshotId - The UUID of the snapshot to update
   * @param updates - Fields to update
   * @param updates.description - New description for the snapshot
   * @returns Promise resolving to the updated Snapshot object
   *
   * @example
   * ```ts
   * await versionApi.updateSnapshot('snapshot-uuid', {
   *   description: 'Updated description'
   * });
   * ```
   */
  updateSnapshot: (snapshotId: string, updates: { description?: string }) =>
    api.put<Snapshot>(`/api/v1/snapshots/${snapshotId}`, {
      description: updates.description,
    }),
};

/**
 * File Version API endpoints.
 *
 * Provides file-level version history for tracking changes to individual files.
 * Every file update creates a new version, allowing users to compare versions
 * and rollback to previous states.
 *
 * Unlike project-level snapshots, file versions track changes on a per-file basis.
 *
 * @namespace fileVersionApi
 */
export const fileVersionApi = {
  /**
   * Get version history for a file.
   *
   * Returns paginated list of versions with metadata.
   * Auto-save versions are excluded by default unless explicitly requested.
   *
   * @param fileId - The UUID of the file
   * @param options - Optional query parameters
   * @param options.limit - Maximum number of versions to return (default: 20)
   * @param options.offset - Number of versions to skip (for pagination)
   * @param options.includeAutoSave - Include auto-save versions in results
   * @returns Promise resolving to paginated version list with total count
   *
   * @example
   * ```ts
   * const { versions, total } = await fileVersionApi.getVersions('file-uuid', {
   *   limit: 10,
   *   offset: 0
   * });
   * ```
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

  /**
   * Create a new version for a file.
   *
   * Explicitly creates a version with the provided content.
   * Note: Versions are also created automatically on file updates.
   *
   * @param fileId - The UUID of the file
   * @param content - The content to save in this version
   * @param options - Optional version metadata
   * @param options.changeType - Type of change ('create', 'edit', 'ai_edit', 'restore', 'auto_save')
   * @param options.changeSource - Who made the change ('user', 'ai', 'system')
   * @param options.changeSummary - Human-readable summary of changes
   * @returns Promise resolving to the created FileVersion object
   *
   * @example
   * ```ts
   * const version = await fileVersionApi.createVersion('file-uuid', content, {
   *   changeType: 'edit',
   *   changeSource: 'user',
   *   changeSummary: 'Fixed typo in chapter 3'
   * });
   * ```
   */
  createVersion: (
    fileId: string,
    content: string,
    options?: {
      changeType?: "create" | "edit" | "ai_edit" | "restore" | "auto_save";
      changeSource?: "user" | "ai" | "system";
      changeSummary?: string;
    },
  ) =>
    api.post<FileVersion>(`/api/v1/files/${fileId}/versions`, {
      content,
      change_type: options?.changeType || "edit",
      change_source: options?.changeSource || "user",
      change_summary: options?.changeSummary,
    }),

  /**
   * Get a specific version by ID.
   *
   * @param versionId - The UUID of the version
   * @returns Promise resolving to the FileVersion object
   *
   * @example
   * ```ts
   * const version = await fileVersionApi.getVersion('version-uuid');
   * ```
   */
  getVersion: (versionId: string) =>
    api.get<FileVersion>(`/api/v1/versions/${versionId}`),

  /**
   * Get the latest version for a file.
   *
   * @param fileId - The UUID of the file
   * @returns Promise resolving to the latest FileVersion object
   *
   * @example
   * ```ts
   * const latestVersion = await fileVersionApi.getLatestVersion('file-uuid');
   * ```
   */
  getLatestVersion: (fileId: string) =>
    api.get<FileVersion>(`/api/v1/files/${fileId}/versions/latest`),

  /**
   * Get content at a specific version number.
   *
   * Retrieves the actual file content (not just metadata) at a specific version.
   *
   * @param fileId - The UUID of the file
   * @param versionNumber - The sequential version number (not UUID)
   * @returns Promise resolving to version content response
   *
   * @example
   * ```ts
   * const { content } = await fileVersionApi.getVersionContent('file-uuid', 5);
   * ```
   */
  getVersionContent: (fileId: string, versionNumber: number) =>
    api.get<VersionContentResponse>(
      `/api/v1/files/${fileId}/versions/${versionNumber}/content`,
    ),

  /**
   * Compare two versions of a file.
   *
   * Returns a diff showing additions, deletions, and modifications
   * between the two versions.
   *
   * @param fileId - The UUID of the file
   * @param v1 - The older version number
   * @param v2 - The newer version number
   * @returns Promise resolving to VersionComparison with diff details
   *
   * @example
   * ```ts
   * const diff = await fileVersionApi.compare('file-uuid', 3, 5);
   * console.log(diff.additions, diff.deletions);
   * ```
   */
  compare: (fileId: string, v1: number, v2: number) =>
    api.get<VersionComparison>(
      `/api/v1/files/${fileId}/versions/compare?v1=${v1}&v2=${v2}`,
    ),

  /**
   * Rollback a file to a specific version.
   *
   * Restores the file content to its state at the specified version.
   * Creates a new version with change_type 'restore'.
   *
   * @param fileId - The UUID of the file
   * @param versionNumber - The version number to rollback to
   * @param expectedUpdatedAt - Optional exact file timestamp for conditional rollback
   * @returns Promise resolving to RollbackResponse with new version info
   *
   * @example
   * ```ts
   * const result = await fileVersionApi.rollback('file-uuid', 3);
   * console.log(`Restored to version 3, created new version ${result.new_version}`);
   * ```
   */
  rollback: (
    fileId: string,
    versionNumber: number,
    expectedUpdatedAt?: string,
  ) => {
    const path = `/api/v1/files/${fileId}/versions/${versionNumber}/rollback`;
    return expectedUpdatedAt === undefined
      ? api.post<RollbackResponse>(path)
      : api.post<RollbackResponse>(path, { expected_updated_at: expectedUpdatedAt });
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
   * The editor submits a whole-document snapshot, so a lock alone cannot stop
   * lost updates — a stale snapshot will happily overwrite whatever the agent
   * wrote while the 3s debounce was pending. When this field is present and the
   * server-side `updated_at` has moved on, the backend answers 409
   * (`ERR_RESOURCE_CONFLICT`) with the current content instead of overwriting.
   * Omitting the field keeps the old (unchecked) behaviour for compatibility.
   */
  base_updated_at?: string;
}

/**
 * File API endpoints.
 *
 * Provides CRUD operations for files in the unified file model.
 * Files support multiple types (outline, draft, character, lore, material)
 * and hierarchical organization via parent-child relationships.
 *
 * @namespace fileApi
 */
export const fileApi = {
  /**
   * Get all files for a project, optionally filtered.
   *
   * @param projectId - The UUID of the project
   * @param options - Optional filters
   * @param options.fileType - Filter by file type ('outline', 'draft', 'character', 'lore', 'material')
   * @param options.parentId - Filter by parent folder ID
   * @returns Promise resolving to array of File objects
   *
   * @example
   * ```ts
   * // Get all draft files
   * const drafts = await fileApi.getAll('project-uuid', { fileType: 'draft' });
   *
   * // Get files in a specific folder
   * const folderFiles = await fileApi.getAll('project-uuid', { parentId: 'folder-uuid' });
   * ```
   */
  getAll: (
    projectId: string,
    options?: { fileType?: string; parentId?: string },
  ) => {
    const params = new URLSearchParams();
    if (options?.fileType) params.append("file_type", options.fileType);
    if (options?.parentId !== undefined)
      params.append("parent_id", options.parentId);
    const query = params.toString() ? `?${params}` : "";
    return api.get<File[]>(`/api/v1/projects/${projectId}/files${query}`);
  },

  /**
   * Get a single file by ID.
   *
   * @param fileId - The UUID of the file
   * @returns Promise resolving to the File object
   *
   * @example
   * ```ts
   * const file = await fileApi.get('file-uuid');
   * ```
   */
  get: (fileId: string) => api.get<File>(`/api/v1/files/${fileId}`),

  /**
   * Create a new file.
   *
   * @param projectId - The UUID of the project
   * @param file - File creation data
   * @param file.title - Required title for the file
   * @param file.file_type - Type of file ('outline', 'draft', 'character', 'lore', 'material')
   * @param file.content - Initial content (optional)
   * @param file.parent_id - Parent folder UUID (optional, null for root)
   * @param file.order - Display order within siblings (optional)
   * @param file.metadata - Additional metadata (optional)
   * @returns Promise resolving to the created File object
   *
   * @example
   * ```ts
   * const file = await fileApi.create('project-uuid', {
   *   title: 'Chapter 1',
   *   file_type: 'draft',
   *   content: 'Once upon a time...'
   * });
   * ```
   */
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
    const created = await api.post<File>(`/api/v1/projects/${projectId}/files`, file);
    trackEvent("file_created", {
      project_id: projectId,
      file_id: created.id,
      file_type: created.file_type ?? file.file_type ?? undefined,
      parent_present: Boolean(file.parent_id),
    });
    return created;
  },

  /**
   * Update an existing file.
   *
   * Only provided fields will be updated. Updating content automatically
   * creates a new version in the version history.
   *
   * @param fileId - The UUID of the file to update
   * @param file - Partial file data to update
   * @returns Promise resolving to the updated File object
   *
   * @example
   * ```ts
   * const updated = await fileApi.update('file-uuid', {
   *   title: 'New Title',
   *   content: 'Updated content...'
   * });
   * ```
   */
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

  /**
   * Delete a file.
   *
   * For folders, use recursive=true to delete all children.
   * Otherwise, deleting a folder with children will fail.
   *
   * @param fileId - The UUID of the file to delete
   * @param recursive - If true, delete all child files (default: false)
   * @returns Promise resolving when deletion is complete
   *
   * @example
   * ```ts
   * // Delete a single file
   * await fileApi.delete('file-uuid');
   *
   * // Delete a folder and all its contents
   * await fileApi.delete('folder-uuid', true);
   * ```
   */
  delete: (fileId: string, recursive = false) => {
    const params = recursive ? "?recursive=true" : "";
    return api.delete(`/api/v1/files/${fileId}${params}`);
  },

  /**
   * Get the complete file tree for a project.
   *
   * Returns hierarchical structure of all files and folders.
   *
   * @param projectId - The UUID of the project
   * @returns Promise resolving to tree structure with nested FileTreeNode objects
   *
   * @example
   * ```ts
   * const { tree } = await fileApi.getTree('project-uuid');
   * // tree is an array of root-level nodes with children
   * ```
   */
  getTree: (projectId: string, options?: RequestInit) => {
    const url = `/api/v1/projects/${projectId}/file-tree`;
    return options
      ? api.get<{ tree: FileTreeNode[] }>(url, options)
      : api.get<{ tree: FileTreeNode[] }>(url);
  },

  /**
   * Upload a .txt file as a material (snippet).
   *
   * Used for importing external text files into the project.
   * Handles authentication with automatic token refresh on 401 errors.
   *
   * Constraints:
   * - Only .txt files are allowed
   * - Maximum 2MB and 200,000 characters (约 20万字)
   *
   * @param projectId - The UUID of the project
   * @param file - The File object from an input element or drag-drop
   * @returns Promise resolving to the created File object
   * @throws {ApiError} ERR_FILE_TYPE_INVALID if file is not .txt
   *
   * @example
   * ```ts
   * const input = document.querySelector('input[type="file"]');
   * const file = input.files[0];
   * const uploaded = await fileApi.upload('project-uuid', file);
   * ```
   */
  upload: async (projectId: string, file: globalThis.File): Promise<File> => {
    const normalizedName = file.name.trim().toLowerCase();
    if (!normalizedName.endsWith(".txt")) {
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

    const doFetch = async (isRetry = false): Promise<Response> => {
      const accessToken = getAccessToken();
      const response = await fetch(
        `${getApiBase()}/api/v1/projects/${projectId}/files/upload`,
        {
          method: "POST",
          headers: accessToken
            ? { Authorization: `Bearer ${accessToken}` }
            : undefined,
          body: formData,
        }
      );

      // Handle 401 - try to refresh token and retry once
      if (response.status === 401 && !isRetry) {
        const refreshed = await tryRefreshToken();
        if (refreshed) {
          return doFetch(true);
        }
      }

      return response;
    };

    const response = await doFetch();

    if (!response.ok) {
      let errorMessage = "ERR_FILE_TYPE_INVALID";
      try {
        const errorData = await response.json();
        errorMessage = resolveApiErrorMessage(errorData, errorMessage);
      } catch {
        // Could not parse error response
      }
      const uploadError = new ApiError(response.status, errorMessage);
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
      file_extension: "txt",
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
    parentId?: string
  ): Promise<{ files: File[]; total: number; errors: string[] }> => {
    const formData = new FormData();
    formData.append("files", file);
    if (parentId) {
      formData.append("parent_id", parentId);
    }

    const doFetch = async (isRetry = false): Promise<Response> => {
      const accessToken = getAccessToken();
      const response = await fetch(
        `${getApiBase()}/api/v1/projects/${projectId}/files/upload-drafts`,
        {
          method: "POST",
          headers: accessToken
            ? { Authorization: `Bearer ${accessToken}` }
            : undefined,
          body: formData,
        }
      );

      if (response.status === 401 && !isRetry) {
        const refreshed = await tryRefreshToken();
        if (refreshed) {
          return doFetch(true);
        }
      }

      return response;
    };

    const response = await doFetch();

    if (!response.ok) {
      let errorMessage = getLocale() === 'en' ? "Upload failed" : "上传失败";
      try {
        const errorData = await response.json();
        errorMessage = resolveApiErrorMessage(errorData, errorMessage);
      } catch {
        // Could not parse error response
      }
      throw new ApiError(response.status, errorMessage);
    }

    return (await response.json()) as {
      files: File[];
      total: number;
      errors: string[];
    };
  },

  /**
   * Move a file to a new parent folder.
   *
   * @param fileId - The UUID of the file to move
   * @param targetParentId - The UUID of the target parent folder (null for root)
   * @returns Promise resolving to the updated File object
   *
   * @example
   * ```ts
   * // Move file to a folder
   * await fileApi.move('file-uuid', 'folder-uuid');
   *
   * // Move file to root
   * await fileApi.move('file-uuid', null);
   * ```
   */
  move: async (fileId: string, targetParentId: string | null): Promise<File> => {
    return api.post<File>(`/api/v1/files/${fileId}/move`, {
      target_parent_id: targetParentId,
    });
  },

  /**
   * Reorder files within a parent folder.
   *
   * @param projectId - The UUID of the project
   * @param orderedIds - Array of file IDs in the desired order
   * @returns Promise resolving to success message
   *
   * @example
   * ```ts
   * await fileApi.reorder('project-uuid', ['file1', 'file2', 'file3']);
   * ```
   */
  reorder: (projectId: string, orderedIds: string[]) =>
    api.post<{ message: string; count: number }>(
      `/api/v1/projects/${projectId}/files/reorder`,
      { ordered_ids: orderedIds }
    ),
};

/**
 * Authenticated raw fetch for skill package endpoints that cannot go through
 * the JSON-only `api` client (multipart upload, zip download).
 * Retries once after a token refresh on 401 and throws ApiError on failure.
 */
async function fetchSkillPackage(endpoint: string, init: RequestInit = {}): Promise<Response> {
  const entryAccess = getAccessToken();
  const entryRefresh = localStorage.getItem("refresh_token");
  const fetchOnce = async (accessToken: string | null): Promise<Response> => {
    return fetch(`${getApiBase()}${endpoint}`, {
      ...init,
      headers: {
        "Accept-Language": localStorage.getItem("zenstory-language") || "zh",
        ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      },
    });
  };

  let response = await fetchOnce(entryAccess);
  if (response.status === 401 && entryAccess && entryRefresh) {
    let ownedSession = resolveOwnedAuthSession(entryAccess, entryRefresh);
    if (ownedSession?.accessToken && ownedSession.refreshToken) {
      if (ownedSession.accessToken === entryAccess && ownedSession.refreshToken === entryRefresh) {
        const refreshed = await tryRefreshToken();
        ownedSession = refreshed ? resolveOwnedAuthSession(entryAccess, entryRefresh) : null;
      }
      if (ownedSession?.accessToken && ownedSession.refreshToken) {
        response = await fetchOnce(ownedSession.accessToken);
      }
    }
  }
  if (!response.ok) {
    let errorData: unknown = null;
    try {
      errorData = await response.json();
    } catch {
      // Could not parse error response
    }
    throw apiErrorFromPayload(response.status, errorData);
  }
  return response;
}

/**
 * Skills API endpoints.
 *
 * Provides CRUD operations for user-defined AI prompt templates (skills).
 * Skills are reusable prompt patterns that enhance AI interactions,
 * allowing users to customize AI behavior for specific writing tasks.
 *
 * Skills can be personal (private) or shared publicly to the community.
 *
 * @namespace skillsApi
 */
export const skillsApi = {
  /**
   * Get all skills available to the current user.
   *
   * Returns both personal skills and public skills the user has added.
   *
   * @returns Promise resolving to skills list with total count
   *
   * @example
   * ```ts
   * const { skills, total } = await skillsApi.list();
   * ```
   */
  list: async () => {
    return api.get<{ skills: import("../types").Skill[]; total: number }>("/api/v1/skills");
  },

  /**
   * Get the current user's personal skills with optional search.
   *
   * Returns only skills created by the current user, excluding public skills
   * added from the community.
   *
   * @param params - Optional query parameters
   * @param params.search - Search term to filter skills by name or content
   * @returns Promise resolving to user's skills with pagination info
   *
   * @example
   * ```ts
   * // Get all my skills
   * const mySkills = await skillsApi.mySkills();
   *
   * // Search for specific skills
   * const results = await skillsApi.mySkills({ search: 'character' });
   * ```
   */
  mySkills: async (params?: { search?: string }) => {
    const searchParams = new URLSearchParams();
    if (params?.search) {
      searchParams.set("search", params.search);
    }
    const query = searchParams.toString();
    return api.get<import("../types").MySkillsResponse>(`/api/v1/skills/my-skills${query ? `?${query}` : ""}`);
  },

  /**
   * Create a new personal skill.
   *
   * @param data - Skill creation data
   * @param data.name - Display name for the skill
   * @param data.description - Human-readable description
   * @param data.prompt_template - The prompt template with variables
   * @param data.variables - Variable definitions for the template
   * @returns Promise resolving to the created Skill object
   *
   * @example
   * ```ts
   * const skill = await skillsApi.create({
   *   name: 'Character Dialog Generator',
   *   description: 'Generate realistic dialog for characters',
   *   prompt_template: 'Write dialog for {{character}} about {{topic}}',
   *   variables: [
   *     { name: 'character', type: 'string', description: 'Character name' },
   *     { name: 'topic', type: 'string', description: 'Topic of conversation' }
   *   ]
   * });
   * ```
   */
  create: async (data: import("../types").CreateSkillRequest) => {
    return api.post<import("../types").Skill>("/api/v1/skills", data);
  },

  /**
   * Update an existing skill.
   *
   * Only the skill's creator can update it.
   *
   * @param id - The UUID of the skill to update
   * @param data - Partial skill data to update
   * @returns Promise resolving to the updated Skill object
   *
   * @example
   * ```ts
   * const updated = await skillsApi.update('skill-uuid', {
   *   name: 'Updated Name',
   *   description: 'Updated description'
   * });
   * ```
   */
  update: async (id: string, data: import("../types").UpdateSkillRequest) => {
    return api.put<import("../types").Skill>(`/api/v1/skills/${id}`, data);
  },

  /**
   * Delete a personal skill.
   *
   * Only the skill's creator can delete it. Shared public copies
   * of the skill will remain in the community.
   *
   * @param id - The UUID of the skill to delete
   * @returns Promise resolving to deletion confirmation
   *
   * @example
   * ```ts
   * await skillsApi.delete('skill-uuid');
   * ```
   */
  delete: async (id: string) => {
    return api.delete<{ success: boolean; message: string }>(`/api/v1/skills/${id}`);
  },

  /**
   * Share a personal skill to the public community.
   *
   * Creates a public copy of the skill that other users can discover
   * and add to their own skill collection.
   *
   * @param id - The UUID of the skill to share
   * @param category - Category for organization (default: 'writing')
   * @returns Promise resolving to share confirmation with public skill ID
   *
   * @example
   * ```ts
   * const result = await skillsApi.share('skill-uuid', 'character');
   * if (result.public_skill_id) {
   *   console.log('Shared publicly with ID:', result.public_skill_id);
   * }
   * ```
   */
  share: async (id: string, category: string = "writing") => {
    return api.post<{ success: boolean; message: string; public_skill_id?: string }>(
      `/api/v1/skills/${id}/share`,
      { category }
    );
  },

  /**
   * Get skill usage statistics for a project.
   *
   * Returns analytics on how often skills have been used in AI
   * conversations within the specified project.
   *
   * @param projectId - The UUID of the project
   * @param days - Number of days to include in stats (default: 30)
   * @returns Promise resolving to usage statistics by skill
   *
   * @example
   * ```ts
   * // Get last 30 days of skill usage
   * const stats = await skillsApi.getStats('project-uuid');
   *
   * // Get last 7 days
   * const weeklyStats = await skillsApi.getStats('project-uuid', 7);
   * ```
   */
  getStats: async (projectId: string, days: number = 30) => {
    return api.get<import("../types").SkillUsageStats>(`/api/v1/skills/stats/${projectId}?days=${days}`);
  },

  /**
   * Perform a batch operation on multiple skills.
   *
   * Enables efficient management of multiple skills at once.
   *
   * @param skillIds - Array of skill UUIDs to operate on
   * @param action - The action to perform: 'enable', 'disable', or 'delete'
   * @returns Promise resolving to operation result with count of updated skills
   *
   * @example
   * ```ts
   * // Enable multiple skills
   * const result = await skillsApi.batchUpdate(['id1', 'id2'], 'enable');
   * console.log(`Updated ${result.updated_count} skills`);
   *
   * // Delete multiple skills
   * await skillsApi.batchUpdate(['id1', 'id2', 'id3'], 'delete');
   * ```
   */
  batchUpdate: async (skillIds: string[], action: "enable" | "disable" | "delete") => {
    return api.post<{ success: boolean; updated_count: number; message: string }>(
      "/api/v1/skills/batch-update",
      { skill_ids: skillIds, action }
    );
  },

  /**
   * Import a skill package (.zip with SKILL.md + references/assets, or a bare .md).
   *
   * Scripts and unsupported files are dropped by the server and reported in `warnings`.
   *
   * @param file - The .zip or .md file selected by the user
   * @returns Promise resolving to the created skill and import warnings
   */
  importSkill: async (file: globalThis.File) => {
    const formData = new FormData();
    formData.append("file", file);
    const response = await fetchSkillPackage("/api/v1/skills/import", {
      method: "POST",
      body: formData,
    });
    return response.json() as Promise<import("../types").ImportSkillResponse>;
  },

  /**
   * Export a skill (own or added) as a standard skill zip and trigger a browser download.
   *
   * @param id - Skill id as returned by GET /skills (UserSkill.id or UserAddedSkill.id)
   * @param skillName - Used for the fallback filename `<name>.zip`
   */
  exportSkill: async (id: string, skillName: string): Promise<void> => {
    const response = await fetchSkillPackage(`/api/v1/skills/${id}/export`);

    let filename = `${skillName}.zip`;
    const disposition = response.headers.get("Content-Disposition");
    if (disposition) {
      const rfc5987Match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
      const standardMatch = disposition.match(/filename="?([^";]+)"?/i);
      if (rfc5987Match) {
        filename = decodeURIComponent(rfc5987Match[1]);
      } else if (standardMatch) {
        filename = standardMatch[1];
      }
    }

    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  },

  /**
   * List the resource files bundled with a skill (own or added).
   */
  listResources: async (id: string) => {
    return api.get<{ resources: import("../types").SkillResource[] }>(
      `/api/v1/skills/${id}/resources`
    );
  },

  /**
   * Read one resource file's text content.
   */
  getResourceContent: async (id: string, path: string) => {
    return api.get<import("../types").SkillResourceContent>(
      `/api/v1/skills/${id}/resources/content?path=${encodeURIComponent(path)}`
    );
  },

  /**
   * Create or replace a resource file (own skills only).
   */
  upsertResource: async (id: string, path: string, content: string) => {
    return api.put<unknown>(`/api/v1/skills/${id}/resources`, {
      path,
      content,
    });
  },

  /**
   * Delete a resource file (own skills only).
   */
  deleteResource: async (id: string, path: string) => {
    return api.delete<unknown>(
      `/api/v1/skills/${id}/resources?path=${encodeURIComponent(path)}`
    );
  },
};

/**
 * Public Skills API endpoints.
 *
 * Provides access to the community skills marketplace where users can
 * discover, browse, and add skills shared by other users. Skills are
 * organized by categories and can be searched by various criteria.
 *
 * Once added, public skills become available in the user's personal
 * skill collection for use in AI conversations.
 *
 * @namespace publicSkillsApi
 */
export const publicSkillsApi = {
  /**
   * Browse public skills with optional filtering and pagination.
   *
   * Supports searching by keyword, filtering by category/source,
   * and paginating through results.
   *
   * @param params - Optional query parameters
   * @param params.category - Filter by skill category (e.g., 'writing', 'character')
   * @param params.source - Filter by skill source/author
   * @param params.search - Search term to find skills by name or description
   * @param params.page - Page number for pagination (1-indexed)
   * @param params.page_size - Number of results per page
   * @returns Promise resolving to paginated list of public skills
   *
   * @example
   * ```ts
   * // Browse all public skills
   * const { skills, total } = await publicSkillsApi.list();
   *
   * // Search for character-related skills
   * const results = await publicSkillsApi.list({ search: 'character', page_size: 20 });
   *
   * // Filter by category
   * const writingSkills = await publicSkillsApi.list({ category: 'writing' });
   * ```
   */
  list: async (params?: {
    category?: string;
    source?: string;
    search?: string;
    page?: number;
    page_size?: number;
  }) => {
    const searchParams = new URLSearchParams();
    if (params?.category) searchParams.set("category", params.category);
    if (params?.source) searchParams.set("source", params.source);
    if (params?.search) searchParams.set("search", params.search);
    if (params?.page) searchParams.set("page", params.page.toString());
    if (params?.page_size) searchParams.set("page_size", params.page_size.toString());
    const query = searchParams.toString();
    return api.get<import("../types").PublicSkillListResponse>(
      `/api/v1/public-skills${query ? `?${query}` : ""}`
    );
  },

  /**
   * Get details of a specific public skill.
   *
   * Returns full skill information including the prompt template
   * and variable definitions.
   *
   * @param id - The UUID of the public skill
   * @returns Promise resolving to the PublicSkill object
   *
   * @example
   * ```ts
   * const skill = await publicSkillsApi.get('public-skill-uuid');
   * console.log(skill.prompt_template);
   * ```
   */
  get: async (id: string) => {
    return api.get<import("../types").PublicSkill>(`/api/v1/public-skills/${id}`);
  },

  /**
   * Get all available skill categories.
   *
   * Returns the list of categories that can be used to filter
   * public skills.
   *
   * @returns Promise resolving to array of SkillCategory objects
   *
   * @example
   * ```ts
   * const { categories } = await publicSkillsApi.getCategories();
   * // categories: [{ name: 'writing', count: 42 }, { name: 'character', count: 15 }]
   * ```
   */
  getCategories: async () => {
    return api.get<{ categories: import("../types").SkillCategory[] }>("/api/v1/public-skills/categories");
  },

  /**
   * Add a public skill to the user's personal collection.
   *
   * Creates a copy of the public skill that the user can use
   * in their AI conversations. The copy maintains a reference
   * to the original for update notifications.
   *
   * @param id - The UUID of the public skill to add
   * @returns Promise resolving to confirmation with the new skill ID
   *
   * @example
   * ```ts
   * const result = await publicSkillsApi.add('public-skill-uuid');
   * if (result.added_skill_id) {
   *   console.log('Added to collection:', result.added_skill_id);
   * }
   * ```
   */
  add: async (id: string) => {
    return api.post<{ success: boolean; message: string; added_skill_id?: string }>(
      `/api/v1/public-skills/${id}/add`
    );
  },

  /**
   * Remove a previously added public skill from the user's collection.
   *
   * This removes the copy from the user's personal skills but does
   * not affect the original public skill.
   *
   * @param id - The UUID of the public skill to remove
   * @returns Promise resolving to removal confirmation
   *
   * @example
   * ```ts
   * await publicSkillsApi.remove('public-skill-uuid');
   * ```
   */
  remove: async (id: string) => {
    return api.delete<{ success: boolean; message: string }>(`/api/v1/public-skills/${id}/remove`);
  },
};

// Agent API Keys APIs
export const agentApiKeysApi = {
  /**
   * List all API keys for the current user
   */
  list: async () => {
    return api.get<import("../types").AgentApiKeyListResponse>("/api/v1/agent-api-keys");
  },

  /**
   * Get a single API key by ID
   */
  get: async (id: string) => {
    return api.get<import("../types").AgentApiKey>(`/api/v1/agent-api-keys/${id}`);
  },

  /**
   * Create a new API key
   */
  create: async (data: import("../types").CreateAgentApiKeyRequest) => {
    return api.post<import("../types").CreateAgentApiKeyResponse>("/api/v1/agent-api-keys", data);
  },

  /**
   * Update an existing API key
   */
  update: async (id: string, data: { name?: string; description?: string; scopes?: string[]; project_ids?: string[]; is_active?: boolean }) => {
    return api.put<import("../types").AgentApiKey>(`/api/v1/agent-api-keys/${id}`, data);
  },

  /**
   * Delete (revoke) an API key
   */
  delete: async (id: string) => {
    return api.delete<{ success: boolean; message: string }>(`/api/v1/agent-api-keys/${id}`);
  },

  /**
   * Regenerate an API key
   */
  regenerate: async (id: string) => {
    return api.post<import("../types").RegenerateAgentApiKeyResponse>(`/api/v1/agent-api-keys/${id}/regenerate`);
  },
};

/**
 * Inspirations API endpoints.
 *
 * Provides access to creative writing prompts and story templates that
 * help users start new projects. Inspirations are curated starting points
 * with pre-defined structure, characters, or themes.
 *
 * Users can browse inspirations by project type, search by keywords,
 * and copy inspirations to create new projects.
 *
 * @namespace inspirationsApi
 */
export const inspirationsApi = {
  /**
   * Submit an existing user project to the inspiration library.
   *
   * Community submissions enter admin review by default. Submissions from
   * admin users are auto-approved by backend policy.
   */
  submit: async (data: import("../types").SubmitInspirationRequest) => {
    return api.post<import("../types").SubmitInspirationResponse>(
      "/api/v1/inspirations",
      data
    );
  },

  /**
   * Get the current user's inspiration submissions and review status.
   */
  getMySubmissions: async (params?: { page?: number; page_size?: number }) => {
    const searchParams = new URLSearchParams();
    if (params?.page) searchParams.set("page", String(params.page));
    if (params?.page_size) searchParams.set("page_size", String(params.page_size));
    const query = searchParams.toString();
    return api.get<import("../types").MyInspirationSubmissionsResponse>(
      `/api/v1/inspirations/my-submissions${query ? `?${query}` : ""}`
    );
  },

  /**
   * Browse inspirations with optional filtering and pagination.
   *
   * Supports filtering by project type, searching by keyword or tags,
   * and restricting to featured content only.
   *
   * @param params - Optional query parameters
   * @param params.project_type - Filter by project type (e.g., 'novel', 'short_story')
   * @param params.search - Search term to find inspirations by title or description
   * @param params.tags - Comma-separated list of tags to filter by
   * @param params.page - Page number for pagination (1-indexed)
   * @param params.page_size - Number of results per page
   * @param params.featured_only - Only return featured/promoted inspirations
   * @returns Promise resolving to paginated list of inspirations
   *
   * @example
   * ```ts
   * // Browse all inspirations
   * const { inspirations, total } = await inspirationsApi.list();
   *
   * // Get featured novel inspirations
   * const featured = await inspirationsApi.list({
   *   project_type: 'novel',
   *   featured_only: true
   * });
   *
   * // Search by keyword
   * const results = await inspirationsApi.list({ search: 'fantasy' });
   * ```
   */
  list: async (params?: {
    project_type?: string;
    search?: string;
    tags?: string;
    page?: number;
    page_size?: number;
    featured_only?: boolean;
  }) => {
    const searchParams = new URLSearchParams();
    if (params?.project_type) searchParams.set("project_type", params.project_type);
    if (params?.search) searchParams.set("search", params.search);
    if (params?.tags) searchParams.set("tags", params.tags);
    if (params?.page) searchParams.set("page", params.page.toString());
    if (params?.page_size) searchParams.set("page_size", params.page_size.toString());
    if (params?.featured_only) searchParams.set("featured_only", "true");
    const query = searchParams.toString();
    return api.get<import("../types").InspirationListResponse>(
      `/api/v1/inspirations${query ? `?${query}` : ""}`
    );
  },

  /**
   * Get featured inspirations for display on homepage or landing.
   *
   * Returns a curated selection of top inspirations promoted by the
   * platform. Useful for showcasing popular starting points.
   *
   * @param limit - Maximum number of featured inspirations to return
   * @returns Promise resolving to array of featured Inspiration objects
   *
   * @example
   * ```ts
   * // Get 5 featured inspirations for homepage
   * const featured = await inspirationsApi.getFeatured(5);
   * ```
   */
  getFeatured: async (limit?: number) => {
    const searchParams = new URLSearchParams();
    if (limit) searchParams.set("limit", limit.toString());
    const query = searchParams.toString();
    return api.get<import("../types").Inspiration[]>(
      `/api/v1/inspirations/featured${query ? `?${query}` : ""}`
    );
  },

  /**
   * Get detailed information about a specific inspiration.
   *
   * Returns the full inspiration content including preview text,
   * structure, and any associated metadata.
   *
   * @param id - The UUID of the inspiration
   * @returns Promise resolving to detailed InspirationDetail object
   *
   * @example
   * ```ts
   * const inspiration = await inspirationsApi.get('inspiration-uuid');
   * console.log(inspiration.title, inspiration.preview);
   * ```
   */
  get: async (id: string) => {
    return api.get<import("../types").InspirationDetail>(`/api/v1/inspirations/${id}`);
  },

  /**
   * Create a new project from an inspiration template.
   *
   * Copies the inspiration's structure and content into a new project
   * that the user can then customize and develop.
   *
   * @param id - The UUID of the inspiration to copy
   * @param projectName - Optional custom name for the new project
   *                      (defaults to inspiration title)
   * @returns Promise resolving to copy confirmation with new project ID
   *
   * @example
   * ```ts
   * // Copy with default name
   * const result = await inspirationsApi.copy('inspiration-uuid');
   *
   * // Copy with custom name
   * const result = await inspirationsApi.copy('inspiration-uuid', 'My Fantasy Novel');
   * console.log('Created project:', result.project_id);
   * ```
   */
  copy: async (id: string, projectName?: string) => {
    return api.post<import("../types").CopyInspirationResponse>(
      `/api/v1/inspirations/${id}/copy`,
      { project_name: projectName } as import("../types").CopyInspirationRequest
    );
  },
};
