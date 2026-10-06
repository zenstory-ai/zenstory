import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { Search, ChartBar, Upload, Zap, Lightbulb, MessageSquare } from "lucide-react";
import { adminApi } from "../../lib/adminApi";
import { AdminPageState } from "../../components/admin";
import { StatsCard } from "../../components/admin/StatsCard";
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

  const formatPercentage = (used: number, limit: number) => {
    if (limit === -1) return 0;
    if (limit === 0) return 100;
    return Math.round((used / limit) * 100);
  };

  const getQuotaBgColor = (percentage: number) => {
    if (percentage >= 90) return "bg-red-500";
    if (percentage >= 70) return "bg-yellow-500";
    return "bg-green-500";
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
        <h2 className="text-lg font-semibold mb-3 text-[hsl(var(--text-primary))]">
          {t("quota.stats")}
        </h2>
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
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <StatsCard
              icon={<Upload className="h-5 w-5" />}
              title={t("quota.materialUploads")}
              value={stats?.material_uploads ?? 0}
            />
            <StatsCard
              icon={<ChartBar className="h-5 w-5" />}
              title={t("quota.materialDecomposes")}
              value={stats?.material_decomposes ?? 0}
            />
            <StatsCard
              icon={<Zap className="h-5 w-5" />}
              title={t("quota.skillCreates")}
              value={stats?.skill_creates ?? 0}
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
            {/* User Info Card */}
            <div className="admin-surface p-4">
              <div className="flex items-center gap-2 mb-2">
                <span className="font-medium text-[hsl(var(--text-primary))]">
                  {userQuota?.username ?? "-"}
                </span>
                <span className="px-2 py-0.5 rounded text-xs font-medium bg-[hsl(var(--accent-primary)/0.15)] text-[hsl(var(--accent-primary))]">
                  {userQuota?.plan_name ?? "-"}
                </span>
              </div>
            </div>

            {/* Quota Cards */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {/* AI Conversations */}
              <div className="admin-surface p-4">
                <div className="flex items-center gap-3 mb-3">
                  <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-[hsl(var(--accent-primary)/0.15)] text-[hsl(var(--accent-primary))]">
                    <MessageSquare className="h-5 w-5" />
                  </div>
                  <div>
                    <h3 className="font-medium text-[hsl(var(--text-primary))]">
                      {t("quota.aiConversations")}
                    </h3>
                    <p className="text-sm text-[hsl(var(--text-secondary))]">
                      {(userQuota?.ai_conversations_limit ?? 0) === -1
                        ? t("quota.unlimited")
                        : `${userQuota?.ai_conversations_used ?? 0} / ${userQuota?.ai_conversations_limit ?? 0}`}
                    </p>
                  </div>
                </div>
                {(userQuota?.ai_conversations_limit ?? 0) !== -1 && (
                  <div className="space-y-1">
                    <div className="flex justify-between text-xs text-[hsl(var(--text-secondary))]">
                      <span>{t("quota.used")}</span>
                      <span>
                        {formatPercentage(userQuota?.ai_conversations_used ?? 0, userQuota?.ai_conversations_limit ?? 0)}%
                      </span>
                    </div>
                    <div className="h-2 bg-[hsl(var(--bg-tertiary))] rounded-full overflow-hidden">
                      <div
                        className={`h-full ${getQuotaBgColor(formatPercentage(userQuota?.ai_conversations_used ?? 0, userQuota?.ai_conversations_limit ?? 0))} transition-all`}
                        style={{ width: `${Math.min(100, formatPercentage(userQuota?.ai_conversations_used ?? 0, userQuota?.ai_conversations_limit ?? 0))}%` }}
                      />
                    </div>
                  </div>
                )}
              </div>

              {/* Material Decomposition */}
              <div className="admin-surface p-4">
                <div className="flex items-center gap-3 mb-3">
                  <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-[hsl(var(--accent-primary)/0.15)] text-[hsl(var(--accent-primary))]">
                    <ChartBar className="h-5 w-5" />
                  </div>
                  <div>
                    <h3 className="font-medium text-[hsl(var(--text-primary))]">
                      {t("quota.materialDecomposes")}
                    </h3>
                    <p className="text-sm text-[hsl(var(--text-secondary))]">
                      {(userQuota?.material_decompose_limit ?? 0) === -1
                        ? t("quota.unlimited")
                        : `${userQuota?.material_decompose_used ?? 0} / ${userQuota?.material_decompose_limit ?? 0}`}
                    </p>
                  </div>
                </div>
                {(userQuota?.material_decompose_limit ?? 0) !== -1 && (
                  <div className="space-y-1">
                    <div className="flex justify-between text-xs text-[hsl(var(--text-secondary))]">
                      <span>{t("quota.used")}</span>
                      <span>{formatPercentage(userQuota?.material_decompose_used ?? 0, userQuota?.material_decompose_limit ?? 0)}%</span>
                    </div>
                    <div className="h-2 bg-[hsl(var(--bg-tertiary))] rounded-full overflow-hidden">
                      <div
                        className={`h-full ${getQuotaBgColor(formatPercentage(userQuota?.material_decompose_used ?? 0, userQuota?.material_decompose_limit ?? 0))} transition-all`}
                        style={{ width: `${Math.min(100, formatPercentage(userQuota?.material_decompose_used ?? 0, userQuota?.material_decompose_limit ?? 0))}%` }}
                      />
                    </div>
                  </div>
                )}
              </div>

              {/* Skill Create */}
              <div className="admin-surface p-4">
                <div className="flex items-center gap-3 mb-3">
                  <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-[hsl(var(--accent-primary)/0.15)] text-[hsl(var(--accent-primary))]">
                    <Zap className="h-5 w-5" />
                  </div>
                  <div>
                    <h3 className="font-medium text-[hsl(var(--text-primary))]">
                      {t("quota.skillCreate")}
                    </h3>
                    <p className="text-sm text-[hsl(var(--text-secondary))]">
                      {(userQuota?.skill_create_limit ?? 0) === -1
                        ? t("quota.unlimited")
                        : `${userQuota?.skill_create_used ?? 0} / ${userQuota?.skill_create_limit ?? 0}`}
                    </p>
                  </div>
                </div>
                {(userQuota?.skill_create_limit ?? 0) !== -1 && (
                  <div className="space-y-1">
                    <div className="flex justify-between text-xs text-[hsl(var(--text-secondary))]">
                      <span>{t("quota.used")}</span>
                      <span>{formatPercentage(userQuota?.skill_create_used ?? 0, userQuota?.skill_create_limit ?? 0)}%</span>
                    </div>
                    <div className="h-2 bg-[hsl(var(--bg-tertiary))] rounded-full overflow-hidden">
                      <div
                        className={`h-full ${getQuotaBgColor(formatPercentage(userQuota?.skill_create_used ?? 0, userQuota?.skill_create_limit ?? 0))} transition-all`}
                        style={{ width: `${Math.min(100, formatPercentage(userQuota?.skill_create_used ?? 0, userQuota?.skill_create_limit ?? 0))}%` }}
                      />
                    </div>
                  </div>
                )}
              </div>

              {/* Inspiration Copy */}
              {inspirationsConfig.enabled && <div className="admin-surface p-4">
                <div className="flex items-center gap-3 mb-3">
                  <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-[hsl(var(--accent-primary)/0.15)] text-[hsl(var(--accent-primary))]">
                    <Lightbulb className="h-5 w-5" />
                  </div>
                  <div>
                    <h3 className="font-medium text-[hsl(var(--text-primary))]">
                      {t("quota.inspirationCopy")}
                    </h3>
                    <p className="text-sm text-[hsl(var(--text-secondary))]">
                      {(userQuota?.inspiration_copy_limit ?? 0) === -1
                        ? t("quota.unlimited")
                        : `${userQuota?.inspiration_copy_used ?? 0} / ${userQuota?.inspiration_copy_limit ?? 0}`}
                    </p>
                  </div>
                </div>
                {(userQuota?.inspiration_copy_limit ?? 0) !== -1 && (
                  <div className="space-y-1">
                    <div className="flex justify-between text-xs text-[hsl(var(--text-secondary))]">
                      <span>{t("quota.used")}</span>
                      <span>{formatPercentage(userQuota?.inspiration_copy_used ?? 0, userQuota?.inspiration_copy_limit ?? 0)}%</span>
                    </div>
                    <div className="h-2 bg-[hsl(var(--bg-tertiary))] rounded-full overflow-hidden">
                      <div
                        className={`h-full ${getQuotaBgColor(formatPercentage(userQuota?.inspiration_copy_used ?? 0, userQuota?.inspiration_copy_limit ?? 0))} transition-all`}
                        style={{ width: `${Math.min(100, formatPercentage(userQuota?.inspiration_copy_used ?? 0, userQuota?.inspiration_copy_limit ?? 0))}%` }}
                      />
                    </div>
                  </div>
                )}
              </div>}
            </div>
          </div>
        </AdminPageState>
      ) : null}
    </div>
  );
};

export default QuotaManagement;
