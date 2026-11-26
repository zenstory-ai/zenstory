import { api, authenticatedFetch, readApiError } from "../apiClient";
import type {
  AdminFeedbackItem,
  AdminFeedbackListResponse,
  AdminFeedbackStatus,
  ChatVoteFilter,
  ChatVotesListResponse,
  PendingSkill,
  PromptConfigRequest,
  PromptReloadResult,
  PromptsListResponse,
  SkillReviewResource,
  SkillReviewStatus,
  SystemPromptConfig,
} from "../../types/admin";
import { ADMIN_BASE } from "./shared";

// ==================== Prompt 管理 API ====================

async function getPrompts(): Promise<PromptsListResponse> {
  return api.get<PromptsListResponse>(`${ADMIN_BASE}/prompts`);
}

async function getPrompt(projectType: string): Promise<SystemPromptConfig> {
  return api.get<SystemPromptConfig>(`${ADMIN_BASE}/prompts/${encodeURIComponent(projectType)}`);
}

async function upsertPrompt(
  projectType: string,
  data: PromptConfigRequest
): Promise<SystemPromptConfig> {
  return api.put<SystemPromptConfig>(
    `${ADMIN_BASE}/prompts/${encodeURIComponent(projectType)}`,
    data
  );
}

async function deletePrompt(projectType: string): Promise<{ message: string }> {
  return api.delete<{ message: string }>(
    `${ADMIN_BASE}/prompts/${encodeURIComponent(projectType)}`
  );
}

async function reloadPrompts(): Promise<PromptReloadResult> {
  return api.post<PromptReloadResult>(`${ADMIN_BASE}/prompts/reload`);
}

// ==================== 技能审核 API ====================

async function getPendingSkills(status: SkillReviewStatus = "pending"): Promise<PendingSkill[]> {
  return api.get<PendingSkill[]>(
    `${ADMIN_BASE}/skills/pending?status=${encodeURIComponent(status)}`
  );
}

async function approveSkill(id: string): Promise<{ message: string; skill_id: string }> {
  return api.post<{ message: string; skill_id: string }>(`${ADMIN_BASE}/skills/${id}/approve`);
}

async function rejectSkill(
  id: string,
  reason?: string
): Promise<{ message: string; skill_id: string }> {
  return api.post<{ message: string; skill_id: string }>(`${ADMIN_BASE}/skills/${id}/reject`, {
    rejection_reason: reason,
  });
}

async function unpublishSkill(
  id: string,
  reason?: string
): Promise<{ message: string; skill_id: string }> {
  return api.post<{ message: string; skill_id: string }>(`${ADMIN_BASE}/skills/${id}/unpublish`, {
    rejection_reason: reason,
  });
}

async function getSkillReviewResources(id: string): Promise<SkillReviewResource[]> {
  const payload = await api.get<{ resources?: SkillReviewResource[] }>(
    `${ADMIN_BASE}/skills/${id}/resources`
  );
  return Array.isArray(payload?.resources) ? payload.resources : [];
}

// ==================== Feedback (Admin) API ====================

async function getFeedbackList(params?: {
  status?: AdminFeedbackStatus;
  source_page?: "dashboard" | "editor";
  has_screenshot?: boolean;
  search?: string;
  skip?: number;
  limit?: number;
}): Promise<AdminFeedbackListResponse> {
  const searchParams = new URLSearchParams();
  if (params?.status) searchParams.set("status", params.status);
  if (params?.source_page) searchParams.set("source_page", params.source_page);
  if (params?.has_screenshot !== undefined) {
    searchParams.set("has_screenshot", String(params.has_screenshot));
  }
  if (params?.search) searchParams.set("search", params.search);
  if (params?.skip !== undefined) searchParams.set("skip", String(params.skip));
  if (params?.limit !== undefined) searchParams.set("limit", String(params.limit));

  const queryString = searchParams.toString();
  const url = queryString ? `${ADMIN_BASE}/feedback?${queryString}` : `${ADMIN_BASE}/feedback`;

  return api.get<AdminFeedbackListResponse>(url);
}

async function updateFeedbackStatus(
  feedbackId: string,
  status: AdminFeedbackStatus
): Promise<AdminFeedbackItem> {
  return api.patch<AdminFeedbackItem>(`${ADMIN_BASE}/feedback/${feedbackId}/status`, {
    status,
  });
}

async function getFeedbackScreenshotBlob(feedbackId: string): Promise<Blob> {
  const response = await authenticatedFetch(
    `${ADMIN_BASE}/feedback/${feedbackId}/screenshot`,
    { method: "GET" },
    { includeLanguage: true }
  );

  if (!response.ok) {
    throw await readApiError(response);
  }

  return response.blob();
}

async function getChatVotes(params?: {
  days?: 7 | 14 | 30;
  vote?: ChatVoteFilter;
  page?: number;
  page_size?: number;
}): Promise<ChatVotesListResponse> {
  const searchParams = new URLSearchParams();
  if (params?.days) searchParams.set("days", String(params.days));
  if (params?.vote) searchParams.set("vote", params.vote);
  if (params?.page) searchParams.set("page", String(params.page));
  if (params?.page_size) searchParams.set("page_size", String(params.page_size));

  const queryString = searchParams.toString();
  const url = queryString
    ? `${ADMIN_BASE}/feedback/chat-votes?${queryString}`
    : `${ADMIN_BASE}/feedback/chat-votes`;

  return api.get<ChatVotesListResponse>(url);
}

export const contentAdminApi = {
  getPrompts,
  getPrompt,
  upsertPrompt,
  deletePrompt,
  reloadPrompts,
  getPendingSkills,
  approveSkill,
  rejectSkill,
  unpublishSkill,
  getSkillReviewResources,
  getFeedbackList,
  updateFeedbackStatus,
  getFeedbackScreenshotBlob,
  getChatVotes,
};
