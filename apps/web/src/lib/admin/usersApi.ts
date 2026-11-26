import { api } from "../apiClient";
import type {
  AuditLog,
  QuotaUsageStats,
  User,
  UserQuotaDetail,
  UserUpdateRequest,
  UsersListResponse,
} from "../../types/admin";
import { ADMIN_BASE } from "./shared";
import type { AuditLogsResponse } from "./types";

// ==================== 用户管理 API ====================

async function getUsers(
  skip: number = 0,
  limit: number = 20,
  search?: string
): Promise<UsersListResponse> {
  const params = new URLSearchParams({
    skip: skip.toString(),
    limit: limit.toString(),
  });

  if (search) {
    params.append("search", search);
  }

  const payload = await api.get<{ items: User[]; total: number }>(
    `${ADMIN_BASE}/users?${params.toString()}`
  );
  return { users: payload.items, total: payload.total };
}

async function getUser(id: string): Promise<User> {
  return api.get<User>(`${ADMIN_BASE}/users/${encodeURIComponent(id)}`);
}

async function updateUser(id: string, data: UserUpdateRequest): Promise<User> {
  return api.put<User>(`${ADMIN_BASE}/users/${id}`, data);
}

async function deleteUser(id: string): Promise<User | { message: string }> {
  return api.delete<User | { message: string }>(`${ADMIN_BASE}/users/${id}`);
}

// ==================== 审计日志 API ====================

type RawAuditLog = Omit<AuditLog, "admin_name"> & { admin_name: string | null };

async function getAuditLogs(params?: {
  page?: number;
  page_size?: number;
  resource_type?: string;
  action?: string;
  include_total?: boolean;
}): Promise<AuditLogsResponse> {
  const searchParams = new URLSearchParams();
  if (params?.page) searchParams.set("page", String(params.page));
  if (params?.page_size) searchParams.set("page_size", String(params.page_size));
  if (params?.resource_type) searchParams.set("resource_type", params.resource_type);
  if (params?.action) searchParams.set("action", params.action);
  if (params?.include_total === false) searchParams.set("include_total", "false");

  const payload = await api.get<Omit<AuditLogsResponse, "items"> & { items: RawAuditLog[] }>(
    `${ADMIN_BASE}/audit-logs?${searchParams.toString()}`
  );
  return {
    ...payload,
    items: payload.items.map((log) => ({
      ...log,
      admin_name: log.admin_name || "-",
    })),
  };
}

// ==================== 配额使用 API ====================

async function getQuotaUsageStats(): Promise<QuotaUsageStats> {
  return api.get<QuotaUsageStats>(`${ADMIN_BASE}/quota/usage`);
}

async function getUserQuota(userId: string): Promise<UserQuotaDetail> {
  return api.get<UserQuotaDetail>(`${ADMIN_BASE}/quota/${userId}`);
}

export const usersAdminApi = {
  getUsers,
  getUser,
  updateUser,
  deleteUser,
  getAuditLogs,
  getQuotaUsageStats,
  getUserQuota,
};
