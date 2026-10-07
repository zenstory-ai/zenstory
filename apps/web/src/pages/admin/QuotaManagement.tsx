import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Search, ChartBar, Zap, Lightbulb } from "lucide-react";
import { adminApi } from "../../lib/adminApi";
import { formatBeijingPeriodDate } from "../../lib/dateUtils";
import { AdminPageState } from "../../components/admin";
import { StatsCard } from "../../components/admin/StatsCard";
import { UserQuotaCards } from "../../components/admin/UserQuotaCards";
import { adminUserPath } from "../../lib/adminRoutes";
import { inspirationsConfig } from "../../config/inspirations";

export const QuotaManagement: React.FC = () => {
  const { t } = useTranslation(["admin", "common"]);
  const [searchUserId, setSearchUserId] = useState("");
  const [searchInput, setSearchInput] = useState("");

  // Get quota usage stats
  const {
    data: stats,
    isLoading: statsLoading,
    isFetching: statsFetching,
    isError: statsError,
    error: statsQueryError,
    refetch: refetchStats,
  } = useQuery({
    queryKey: ["admin", "quota", "stats"],
    queryFn: adminApi.getQuotaUsageStats,
  });

  // Get user quota details
  const {
    data: userQuota,
    isLoading: userLoading,
    isFetching: userFetching,
    isError: userError,
    error: userQueryError,
    refetch: refetchUserQuota,
  } = useQuery({
    queryKey: ["admin", "quota", "user", searchUserId],
    queryFn: () => adminApi.getUserQuota(searchUserId),
    enabled: !!searchUserId,
  });

  const handleSearch = () => {
    if (searchInput.trim()) {
      setSearchUserId(searchInput.trim());
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      handleSearch();
    }
  };

  const statsErrorText = statsQueryError instanceof Error && statsQueryError.message
    ? statsQueryError.message
    : t("common:error");
  const userErrorText = userQueryError instanceof Error && userQueryError.message
    ? userQueryError.message
    : t("common:error");

  return (
    <div className="admin-page">
      {/* Header */}
      <div>
        <h1 className="admin-page-title">
          {t("quota.title")}
        </h1>
        <p className="admin-page-subtitle">
          {t("quota.subtitle")}
        </p>
      </div>

      {/* Stats Grid */}
      <div>
        <h2 className="text-lg font-semibold text-[hsl(var(--text-primary))]">
          {t("quota.stats")}
        </h2>
        {stats?.period_start && (
          <p className="mb-3 text-sm text-[hsl(var(--text-secondary))]">
            {t("quota.period", {
              start: formatBeijingPeriodDate(stats.period_start),
              end: formatBeijingPeriodDate(stats.period_end, { exclusiveEnd: true }),
            })}
          </p>
        )}
        <AdminPageState
          isLoading={statsLoading}
          isFetching={statsFetching}
          isError={statsError}
          isEmpty={!stats}
          loadingText={t("common:loading")}
          errorText={statsErrorText}
          emptyText={t("common:noData")}
          retryText={t("common:retry")}
          onRetry={() => {
            void refetchStats();
          }}
          stateClassName="admin-surface flex items-center justify-center py-12"
        >
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-3">
            <StatsCard
              icon={<ChartBar className="h-5 w-5" />}
              title={t("quota.materialDecompositions")}
              value={stats?.material_decompositions ?? 0}
            />
            <StatsCard
              icon={<Zap className="h-5 w-5" />}
              title={t("quota.skillsCreated")}
              value={stats?.skills_created ?? 0}
            />
            {inspirationsConfig.enabled && (
              <StatsCard
                icon={<Lightbulb className="h-5 w-5" />}
                title={t("quota.inspirationCopies")}
                value={stats?.inspiration_copies ?? 0}
              />
            )}
          </div>
        </AdminPageState>
      </div>

      {/* User Search */}
      <div className="admin-surface p-4">
        <h2 className="text-lg font-semibold mb-3 text-[hsl(var(--text-primary))]">
          {t("quota.userDetail")}
        </h2>
        <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2">
          <div className="flex-1 relative">
            <Search
              className="absolute left-3 top-1/2 -translate-y-1/2 text-[hsl(var(--text-secondary))]"
              size={18}
            />
            <input
              type="text"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={t("quota.searchUser")}
              className="w-full pl-10 pr-4 py-2.5 min-h-11 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg focus:outline-none focus:ring-2 focus:ring-[hsl(var(--accent-primary))] text-[hsl(var(--text-primary))] placeholder-[hsl(var(--text-secondary))]"
            />
          </div>
          <button
            onClick={handleSearch}
            className="w-full sm:w-auto px-4 py-2.5 min-h-11 bg-[hsl(var(--accent-primary))] text-white rounded-lg hover:opacity-90 active:scale-95 transition-all"
          >
            {t("common:search")}
          </button>
        </div>
      </div>

      {/* User Quota Details */}
      {searchUserId ? (
        <AdminPageState
          isLoading={userLoading}
          isFetching={userFetching}
          isError={userError}
          isEmpty={!userQuota || !userQuota.user_id}
          loadingText={t("common:loading")}
          errorText={userErrorText}
          emptyText={t("quota.noUser")}
          retryText={t("common:retry")}
          onRetry={() => {
            void refetchUserQuota();
          }}
          stateClassName="admin-surface flex items-center justify-center py-12"
        >
          <div className="space-y-4">
            <div className="admin-surface flex flex-wrap items-center gap-2 p-4">
              <span className="font-medium text-[hsl(var(--text-primary))]">
                {userQuota?.username ?? "-"}
              </span>
              <span className="px-2 py-0.5 rounded text-xs font-medium bg-[hsl(var(--accent-primary)/0.15)] text-[hsl(var(--accent-primary))]">
                {userQuota?.plan_display_name || userQuota?.plan_name || "-"}
              </span>
              {userQuota?.user_id && (
                <Link
                  to={adminUserPath(userQuota.user_id)}
                  className="ml-auto text-sm text-[hsl(var(--accent-primary))] hover:underline"
                >
                  {t("users.viewDetails")}
                </Link>
              )}
            </div>
            {userQuota && <UserQuotaCards quota={userQuota} />}
          </div>
        </AdminPageState>
      ) : null}
    </div>
  );
};

export default QuotaManagement;
