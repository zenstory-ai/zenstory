import type {
  File,
  FileTreeNode,
  PatchProjectRequest,
  Project,
  ProjectProgress,
} from "../../types";
import type { ChatMessage, ChatSession } from "../chatApi";
import { api } from "../apiClient";

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

export interface ProjectBootstrapResponse {
  project: Project;
  files: FileTreeNode[];
  initial_file: File | null;
  chat_session: ChatSession | null;
  recent_messages: ChatMessage[];
  has_more_messages: boolean;
}

/**
 * Project API endpoints.
 *
 * Provides CRUD operations for projects, which are the top-level containers
 * for all files (outlines, drafts, characters, lore) in the workbench.
 */
export const projectApi = {
  /** Get all projects for the current user. */
  getAll: () => api.get<Project[]>("/api/v1/projects"),

  /** Get a single project by ID. */
  get: (projectId: string) => api.get<Project>(`/api/v1/projects/${projectId}`),

  /**
   * Aggregated single-round-trip workbench boot payload: project metadata,
   * file tree (without bodies), active initial file, and recent chat messages.
   */
  getBootstrap: (
    projectId: string,
    options?: { fileId?: string | null; chatLimit?: number; signal?: AbortSignal },
  ) => {
    const params = new URLSearchParams();
    if (options?.fileId) params.set("file_id", options.fileId);
    if (options?.chatLimit !== undefined) params.set("chat_limit", String(options.chatLimit));
    const query = params.toString() ? `?${params.toString()}` : "";
    return api.get<ProjectBootstrapResponse>(
      `/api/v1/projects/${projectId}/bootstrap${query}`,
      options?.signal ? { signal: options.signal } : undefined,
    );
  },

  /**
   * Writing progress of the author's projects (cards), or of one project when given
   * (the chat's "write chapter 1" offer when its framework is ready).
   */
  getProgress: (projectId?: string) =>
    api.get<ProjectProgress[]>(
      projectId
        ? `/api/v1/projects/progress?project_id=${encodeURIComponent(projectId)}`
        : "/api/v1/projects/progress",
    ),

  /** Create a new project. */
  create: (project: Partial<Project>) => api.post<Project>("/api/v1/projects", project),

  /** Update an existing project. */
  update: (projectId: string, project: Partial<Project>) =>
    api.put<Project>(`/api/v1/projects/${projectId}`, project),

  /** Delete a project and all its files. */
  delete: (projectId: string) => api.delete(`/api/v1/projects/${projectId}`),

  /**
   * Partially update project status fields (summary, current_phase, writing_style, notes).
   */
  patch: (projectId: string, data: PatchProjectRequest) =>
    api.patch<Project>(`/api/v1/projects/${projectId}`, data),

  /** Get all available project templates. */
  getTemplates: () => api.get<Record<string, ProjectTemplate>>("/api/v1/project-templates"),
};
