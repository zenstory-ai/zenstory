import type {
  CreateSkillRequest,
  ImportSkillResponse,
  MySkillsResponse,
  PublicSkill,
  PublicSkillListResponse,
  Skill,
  SkillCategory,
  SkillResource,
  SkillResourceContent,
  UpdateSkillRequest,
} from "../../types";
import { api, authenticatedFetch, readApiError } from "../apiClient";
import { downloadBlob } from "../downloadBlob";
import { parseContentDispositionFilename } from "../contentDisposition";

/**
 * Authenticated raw fetch for skill package endpoints that cannot go through
 * the JSON-only `api` client (multipart upload, zip download).
 * Retries once after a token refresh on 401 and throws ApiError on failure.
 */
async function fetchSkillPackage(endpoint: string, init: RequestInit = {}): Promise<Response> {
  const response = await authenticatedFetch(endpoint, init, { includeLanguage: true });
  if (!response.ok) {
    throw await readApiError(response);
  }
  return response;
}

/**
 * Skills API endpoints.
 *
 * Provides CRUD operations for user-defined AI prompt templates (skills),
 * plus package import/export and bundled resource management.
 */
export const skillsApi = {
  /** Get all skills available to the current user (personal + added public skills). */
  list: async () => api.get<{ skills: Skill[]; total: number }>("/api/v1/skills"),

  /** Get the current user's personal skills with optional search. */
  mySkills: async (params?: { search?: string }) => {
    const searchParams = new URLSearchParams();
    if (params?.search) {
      searchParams.set("search", params.search);
    }
    const query = searchParams.toString();
    return api.get<MySkillsResponse>(`/api/v1/skills/my-skills${query ? `?${query}` : ""}`);
  },

  /** Create a new personal skill. */
  create: async (data: CreateSkillRequest) => api.post<Skill>("/api/v1/skills", data),

  /** Update an existing skill (creator only). */
  update: async (id: string, data: UpdateSkillRequest) =>
    api.put<Skill>(`/api/v1/skills/${id}`, data),

  /** Delete a personal skill (creator only). */
  delete: async (id: string) =>
    api.delete<{ success: boolean; message: string }>(`/api/v1/skills/${id}`),

  /** Share a personal skill to the public community. */
  share: async (id: string, category: string = "writing") =>
    api.post<{ success: boolean; message: string; public_skill_id?: string }>(
      `/api/v1/skills/${id}/share`,
      { category },
    ),

  /** Perform a batch operation on multiple skills ('enable', 'disable', or 'delete'). */
  batchUpdate: async (skillIds: string[], action: "enable" | "disable" | "delete") =>
    api.post<{ success: boolean; updated_count: number; message: string }>(
      "/api/v1/skills/batch-update",
      { skill_ids: skillIds, action },
    ),

  /**
   * Import a skill package (.zip with SKILL.md + references/assets, or a bare .md).
   * Scripts and unsupported files are dropped by the server and reported in `warnings`.
   */
  importSkill: async (file: globalThis.File) => {
    const formData = new FormData();
    formData.append("file", file);
    const response = await fetchSkillPackage("/api/v1/skills/import", {
      method: "POST",
      body: formData,
    });
    return response.json() as Promise<ImportSkillResponse>;
  },

  /** Export a skill (own or added) as a standard skill zip and trigger a browser download. */
  exportSkill: async (id: string, skillName: string): Promise<void> => {
    const response = await fetchSkillPackage(`/api/v1/skills/${id}/export`);
    const filename =
      parseContentDispositionFilename(response.headers.get("Content-Disposition")) ??
      `${skillName}.zip`;
    downloadBlob(await response.blob(), filename);
  },

  /** List the resource files bundled with a skill (own or added). */
  listResources: async (id: string) =>
    api.get<{ resources: SkillResource[] }>(`/api/v1/skills/${id}/resources`),

  /** Read one resource file's text content. */
  getResourceContent: async (id: string, path: string) =>
    api.get<SkillResourceContent>(
      `/api/v1/skills/${id}/resources/content?path=${encodeURIComponent(path)}`,
    ),

  /** Create or replace a resource file (own skills only). */
  upsertResource: async (id: string, path: string, content: string) =>
    api.put<unknown>(`/api/v1/skills/${id}/resources`, { path, content }),

  /** Delete a resource file (own skills only). */
  deleteResource: async (id: string, path: string) =>
    api.delete<unknown>(`/api/v1/skills/${id}/resources?path=${encodeURIComponent(path)}`),
};

/**
 * Public Skills API endpoints.
 *
 * Provides access to the community skills marketplace.
 */
export const publicSkillsApi = {
  /** Browse public skills with optional filtering and pagination. */
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
    return api.get<PublicSkillListResponse>(`/api/v1/public-skills${query ? `?${query}` : ""}`);
  },

  /** Get details of a specific public skill. */
  get: async (id: string) => api.get<PublicSkill>(`/api/v1/public-skills/${id}`),

  /** Get all available skill categories. */
  getCategories: async () =>
    api.get<{ categories: SkillCategory[] }>("/api/v1/public-skills/categories"),

  /** Add a public skill to the user's personal collection. */
  add: async (id: string) =>
    api.post<{ success: boolean; message: string; added_skill_id?: string }>(
      `/api/v1/public-skills/${id}/add`,
    ),

  /** Remove a previously added public skill from the user's collection. */
  remove: async (id: string) =>
    api.delete<{ success: boolean; message: string }>(`/api/v1/public-skills/${id}/remove`),
};
