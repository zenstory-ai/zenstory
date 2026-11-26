import { api } from "../apiClient";
import type {
  AcquisitionStats,
  ActivationFunnelStats,
  DashboardStats,
  GrowthDashboard,
  UpgradeConversionStats,
  UpgradeFunnelStats,
  UxHealthDashboard,
} from "../../types/admin";
import { ADMIN_BASE, clampWindowDays } from "./shared";
import type {
  ModelMetricsResponse,
  StageLatencyResponse,
  UsageDetailDays,
  UsageSource,
  UsageSummaryResponse,
  UsageUserSort,
  UsageWindow,
  UserDailyUsageResponse,
  UserUsageListResponse,
} from "./types";

// ==================== 用量与成本 API ====================

async function getUsageSummary(
  window: UsageWindow,
  options?: { refresh?: boolean }
): Promise<UsageSummaryResponse> {
  const suffix = options?.refresh ? "&refresh=true" : "";
  return api.get<UsageSummaryResponse>(`${ADMIN_BASE}/usage/summary?window=${window}${suffix}`);
}

async function getUsageByUser(params: {
  window: UsageWindow;
  sort?: UsageUserSort;
  search?: string;
  page?: number;
  page_size?: number;
}): Promise<UserUsageListResponse> {
  const query = new URLSearchParams({ window: params.window });
  if (params.sort) query.set("sort", params.sort);
  if (params.search) query.set("search", params.search);
  if (params.page) query.set("page", String(params.page));
  if (params.page_size) query.set("page_size", String(params.page_size));
  return api.get<UserUsageListResponse>(`${ADMIN_BASE}/usage/users?${query.toString()}`);
}

async function getUsageByModel(
  window: UsageWindow,
  source?: UsageSource,
  options?: { refresh?: boolean }
): Promise<ModelMetricsResponse> {
  const query = new URLSearchParams({ window });
  if (source) query.set("source", source);
  if (options?.refresh) query.set("refresh", "true");
  return api.get<ModelMetricsResponse>(`${ADMIN_BASE}/usage/models?${query.toString()}`);
}

async function getUsageStageLatency(
  days: UsageDetailDays = 7,
  options?: { refresh?: boolean }
): Promise<StageLatencyResponse> {
  const suffix = options?.refresh ? "&refresh=true" : "";
  return api.get<StageLatencyResponse>(`${ADMIN_BASE}/usage/stage-latency?days=${days}${suffix}`);
}

async function getUserDailyUsage(
  userId: string,
  days: UsageDetailDays = 7
): Promise<UserDailyUsageResponse> {
  return api.get<UserDailyUsageResponse>(
    `${ADMIN_BASE}/usage/users/${encodeURIComponent(userId)}/daily?days=${days}`
  );
}

// ==================== Dashboard Stats API ====================

async function getGrowthDashboard(
  days: 7 | 14 | 30 = 7,
  options?: { refresh?: boolean }
): Promise<GrowthDashboard> {
  const suffix = options?.refresh ? "&refresh=true" : "";
  return api.get<GrowthDashboard>(`${ADMIN_BASE}/dashboard/growth?days=${days}${suffix}`);
}

async function getUxHealthDashboard(
  days: 7 | 14 | 30 = 7,
  options?: { refresh?: boolean }
): Promise<UxHealthDashboard> {
  const suffix = options?.refresh ? "&refresh=true" : "";
  return api.get<UxHealthDashboard>(`${ADMIN_BASE}/dashboard/ux-health?days=${days}${suffix}`);
}

async function getAcquisitionStats(
  days: 7 | 14 | 30 | 90 = 30,
  options?: { refresh?: boolean }
): Promise<AcquisitionStats> {
  const suffix = options?.refresh ? "&refresh=true" : "";
  return api.get<AcquisitionStats>(`${ADMIN_BASE}/dashboard/acquisition?days=${days}${suffix}`);
}

async function getDashboardStats(options?: {
  includeCommercialization?: boolean;
  refresh?: boolean;
}): Promise<DashboardStats> {
  const params = new URLSearchParams();
  if (options?.includeCommercialization === false) {
    params.set("include_commercialization", "false");
  }
  if (options?.refresh) {
    params.set("refresh", "true");
  }
  const suffix = params.toString();
  return api.get<DashboardStats>(
    suffix ? `${ADMIN_BASE}/dashboard/stats?${suffix}` : `${ADMIN_BASE}/dashboard/stats`
  );
}

async function getActivationFunnel(
  days = 7,
  options?: { refresh?: boolean }
): Promise<ActivationFunnelStats> {
  const suffix = options?.refresh ? "&refresh=true" : "";
  return api.get<ActivationFunnelStats>(
    `${ADMIN_BASE}/dashboard/activation-funnel?days=${clampWindowDays(days)}${suffix}`
  );
}

async function getUpgradeConversionStats(
  days = 7,
  options?: { refresh?: boolean }
): Promise<UpgradeConversionStats> {
  const suffix = options?.refresh ? "&refresh=true" : "";
  return api.get<UpgradeConversionStats>(
    `${ADMIN_BASE}/dashboard/upgrade-conversion?days=${clampWindowDays(days)}${suffix}`
  );
}

async function getUpgradeFunnelStats(
  days = 7,
  options?: { refresh?: boolean }
): Promise<UpgradeFunnelStats> {
  const suffix = options?.refresh ? "&refresh=true" : "";
  return api.get<UpgradeFunnelStats>(
    `${ADMIN_BASE}/dashboard/upgrade-funnel?days=${clampWindowDays(days)}${suffix}`
  );
}

export const metricsAdminApi = {
  getUsageSummary,
  getUsageByUser,
  getUsageByModel,
  getUsageStageLatency,
  getUserDailyUsage,
  getDashboardStats,
  getGrowthDashboard,
  getUxHealthDashboard,
  getAcquisitionStats,
  getActivationFunnel,
  getUpgradeConversionStats,
  getUpgradeFunnelStats,
};
