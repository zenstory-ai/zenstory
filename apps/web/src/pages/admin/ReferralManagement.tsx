import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Gift, Link2, Users, Award, Clock, Coins, Plus } from "lucide-react";
import { adminApi } from "../../lib/adminApi";
import { AdminPageState } from "../../components/admin";
import { StatsCard } from "../../components/admin/StatsCard";
import { toast } from "../../lib/toast";
import { formatAdminDateTime } from "../../lib/dateUtils";
import { AdminUserLink } from "../../components/admin/AdminUserLink";

export const ReferralManagement: React.FC = () => {
  const { t } = useTranslation(["admin", "common"]);
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState<"codes" | "rewards">("codes");
  const [codesPage, setCodesPage] = useState(1);
  const [rewardsPage, setRewardsPage] = useState(1);
  const [isActiveFilter, setIsActiveFilter] = useState<boolean | undefined>(undefined);
  const pageSize = 20;

  // Get referral stats
  const {
    data: stats,
    isLoading: statsLoading,
    isFetching: statsFetching,
    isError: statsError,
    error: statsQueryError,
    refetch: refetchStats,
  } = useQuery({
    queryKey: ["admin", "referrals", "stats"],
    queryFn: adminApi.getReferralStats,
  });

  // Get invite codes
  const {
    data: codes,
    isLoading: codesLoading,
    isFetching: codesFetching,
    isError: codesError,
    error: codesQueryError,
    refetch: refetchCodes,
  } = useQuery({
    queryKey: ["admin", "invites", codesPage, isActiveFilter],
    queryFn: () => adminApi.getInviteCodes({ page: codesPage, page_size: pageSize, is_active: isActiveFilter }),
  });

  // Get rewards
  const {
    data: rewards,
    isLoading: rewardsLoading,
    isFetching: rewardsFetching,
    isError: rewardsError,
    error: rewardsQueryError,
    refetch: refetchRewards,
  } = useQuery({
    queryKey: ["admin", "referrals", "rewards", rewardsPage],
    queryFn: () => adminApi.getReferralRewards({ page: rewardsPage, page_size: pageSize }),
  });

  const formatDate = (dateStr: string | null | undefined) => formatAdminDateTime(dateStr);

  const getRewardTypeLabel = (type: string) => {
    return t(`referrals.types.${type}`, type);
  };

  const getRewardTypeClass = (type: string) => {
    if (type === "points") return "bg-blue-500/15 text-blue-500";
    if (type === "pro_trial") return "bg-purple-500/15 text-purple-500";
    return "bg-emerald-500/15 text-emerald-500";
  };

  const statsErrorText = statsQueryError instanceof Error && statsQueryError.message
    ? statsQueryError.message
    : t("common:error");
  const codesErrorText = codesQueryError instanceof Error && codesQueryError.message
    ? codesQueryError.message
    : t("common:error");
  const rewardsErrorText = rewardsQueryError instanceof Error && rewardsQueryError.message
    ? rewardsQueryError.message
    : t("common:error");

  const createInviteCodeMutation = useMutation({
    mutationFn: adminApi.createAdminInviteCode,
    onSuccess: (createdCode) => {
      toast.success(
        t("referrals.generateSuccess", { code: createdCode.code })
      );
      void queryClient.invalidateQueries({ queryKey: ["admin", "invites"] });
      void queryClient.invalidateQueries({ queryKey: ["admin", "referrals", "stats"] });
    },
    onError: (error) => {
      const message = error instanceof Error && error.message
        ? error.message
        : t("referrals.generateError");
      toast.error(message);
    },
  });

  return (
    <div className="admin-page admin-page-fluid">
      {/* Header */}
      <div>
        <h1 className="admin-page-title">
          {t("referrals.title")}
        </h1>
        <p className="admin-page-subtitle">
          {t("referrals.subtitle")}
        </p>
      </div>

      {/* Stats Grid */}
      <div>
        <h2 className="text-lg font-semibold mb-3 text-[hsl(var(--text-primary))]">
          {t("referrals.stats")}
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
          <div className="grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-6">
            <StatsCard
              icon={<Link2 className="h-5 w-5" />}
              title={t("referrals.totalCodes")}
              value={stats?.total_codes ?? 0}
            />
            <StatsCard
              icon={<Gift className="h-5 w-5" />}
              title={t("referrals.activeCodes")}
              value={stats?.active_codes ?? 0}
            />
            <StatsCard
              icon={<Users className="h-5 w-5" />}
              title={t("referrals.totalReferrals")}
              value={stats?.total_referrals ?? 0}
            />
            <StatsCard
              icon={<Award className="h-5 w-5" />}
              title={t("referrals.successfulReferrals")}
              value={stats?.successful_referrals ?? 0}
            />
            <StatsCard
              icon={<Clock className="h-5 w-5" />}
              title={t("referrals.pendingRewards")}
              value={stats?.pending_rewards ?? 0}
            />
            <StatsCard
              icon={<Coins className="h-5 w-5" />}
              title={t("referrals.totalPointsAwarded")}
              value={stats?.total_points_awarded ?? 0}
            />
          </div>
        </AdminPageState>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-[hsl(var(--separator-color))]">
        <button
          onClick={() => setActiveTab("codes")}
          className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
            activeTab === "codes"
              ? "border-[hsl(var(--accent-primary))] text-[hsl(var(--accent-primary))]"
              : "border-transparent text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))]"
          }`}
        >
          {t("referrals.inviteCodes")}
        </button>
        <button
          onClick={() => setActiveTab("rewards")}
          className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
            activeTab === "rewards"
              ? "border-[hsl(var(--accent-primary))] text-[hsl(var(--accent-primary))]"
              : "border-transparent text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))]"
          }`}
        >
          {t("referrals.rewards")}
        </button>
      </div>

      {/* Invite Codes Tab */}
      {activeTab === "codes" && (
        <div className="space-y-4">
          {/* Filters + Actions */}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap gap-2">
              <button
                onClick={() => { setIsActiveFilter(undefined); setCodesPage(1); }}
                className={`px-3 py-1.5 rounded-lg text-sm transition-colors ${
                  isActiveFilter === undefined
                    ? "bg-[hsl(var(--accent-primary))] text-white"
                    : "bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-primary))]"
                }`}
              >
                {t("referrals.allStatus")}
              </button>
              <button
                onClick={() => { setIsActiveFilter(true); setCodesPage(1); }}
                className={`px-3 py-1.5 rounded-lg text-sm transition-colors ${
                  isActiveFilter === true
                    ? "bg-[hsl(var(--accent-primary))] text-white"
                    : "bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-primary))]"
                }`}
              >
                {t("referrals.activeOnly")}
              </button>
              <button
                onClick={() => { setIsActiveFilter(false); setCodesPage(1); }}
                className={`px-3 py-1.5 rounded-lg text-sm transition-colors ${
                  isActiveFilter === false
                    ? "bg-[hsl(var(--accent-primary))] text-white"
                    : "bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-primary))]"
                }`}
              >
                {t("referrals.inactiveOnly")}
              </button>
            </div>

            <button
              onClick={() => createInviteCodeMutation.mutate()}
              disabled={createInviteCodeMutation.isPending}
              className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm font-medium bg-[hsl(var(--accent-primary))] text-white hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed transition-all"
            >
              <Plus className="h-4 w-4" />
              {createInviteCodeMutation.isPending
                ? t("referrals.generating")
                : t("referrals.generateButton")}
            </button>
          </div>

          {/* Codes Table */}
          <div className="admin-table-shell">
            <AdminPageState
              isLoading={codesLoading}
              isFetching={codesFetching}
              isError={codesError}
              isEmpty={(codes?.items ?? []).length === 0}
              loadingText={t("common:loading")}
              errorText={codesErrorText}
              emptyText={t("common:noData")}
              retryText={t("common:retry")}
              onRetry={() => {
                void refetchCodes();
              }}
              stateClassName="flex items-center justify-center py-12"
            >
              <>
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[760px]">
                    <thead>
                      <tr className="border-b border-[hsl(var(--separator-color))]">
                        <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                          {t("referrals.code")}
                        </th>
                        <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                          {t("referrals.owner")}
                        </th>
                        <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                          {t("referrals.currentUses")}
                        </th>
                        <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                          {t("referrals.status")}
                        </th>
                        <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                          {t("referrals.expiresAt")}
                        </th>
                        <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                          {t("referrals.createdAt")}
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {codes?.items.map((code) => (
                        <tr
                          key={code.id}
                          className="border-b border-[hsl(var(--separator-color))] hover:bg-[hsl(var(--bg-tertiary))]"
                        >
                          <td className="px-4 py-3 text-sm font-mono text-[hsl(var(--text-primary))]">
                            {code.code}
                          </td>
                          <td className="px-4 py-3 text-sm text-[hsl(var(--text-primary))]">
                            <AdminUserLink userId={code.owner_id}>{code.owner_name}</AdminUserLink>
                          </td>
                          <td className="px-4 py-3 text-sm text-[hsl(var(--text-primary))]">
                            <span className={code.current_uses >= code.max_uses ? "text-red-500" : ""}>
                              {code.current_uses}
                            </span>
                            <span className="text-[hsl(var(--text-secondary))]"> / {code.max_uses}</span>
                          </td>
                          <td className="px-4 py-3 text-sm">
                            <span className={`px-2 py-1 rounded text-xs font-medium ${
                              code.is_active
                                ? "bg-green-500/15 text-green-500"
                                : "bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-secondary))]"
                            }`}>
                              {code.is_active ? t("referrals.active") : t("referrals.inactive")}
                            </span>
                          </td>
                          <td className="px-4 py-3 text-sm text-[hsl(var(--text-secondary))]">
                            {formatDate(code.expires_at)}
                          </td>
                          <td className="px-4 py-3 text-sm text-[hsl(var(--text-secondary))]">
                            {formatDate(code.created_at)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                {/* Pagination */}
                {(codes?.total ?? 0) > pageSize && (
                  <div className="flex items-center justify-between px-4 py-3 border-t border-[hsl(var(--separator-color))]">
                    <div className="text-sm text-[hsl(var(--text-secondary))]">
                      {t("common:showing", {
                        from: (codesPage - 1) * pageSize + 1,
                        to: Math.min(codesPage * pageSize, codes?.total ?? 0),
                        total: codes?.total ?? 0,
                      })}
                    </div>
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => setCodesPage((p) => Math.max(1, p - 1))}
                        disabled={codesPage === 1}
                        className="px-3 py-1.5 min-h-9 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg hover:bg-[hsl(var(--bg-tertiary))] disabled:opacity-50 disabled:cursor-not-allowed transition-all text-sm"
                      >
                        {t("common:previous")}
                      </button>
                      <span className="text-sm text-[hsl(var(--text-primary))]">
                        {codesPage} / {Math.ceil((codes?.total ?? 0) / pageSize)}
                      </span>
                      <button
                        onClick={() => setCodesPage((p) => Math.min(Math.ceil((codes?.total ?? 0) / pageSize), p + 1))}
                        disabled={codesPage >= Math.ceil((codes?.total ?? 0) / pageSize)}
                        className="px-3 py-1.5 min-h-9 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg hover:bg-[hsl(var(--bg-tertiary))] disabled:opacity-50 disabled:cursor-not-allowed transition-all text-sm"
                      >
                        {t("common:next")}
                      </button>
                    </div>
                  </div>
                )}
              </>
            </AdminPageState>
          </div>
        </div>
      )}

      {/* Rewards Tab */}
      {activeTab === "rewards" && (
        <div className="admin-table-shell">
          <AdminPageState
            isLoading={rewardsLoading}
            isFetching={rewardsFetching}
            isError={rewardsError}
            isEmpty={(rewards?.items ?? []).length === 0}
            loadingText={t("common:loading")}
            errorText={rewardsErrorText}
            emptyText={t("common:noData")}
            retryText={t("common:retry")}
            onRetry={() => {
              void refetchRewards();
            }}
            stateClassName="flex items-center justify-center py-12"
          >
            <>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[820px]">
                  <thead>
                    <tr className="border-b border-[hsl(var(--separator-color))]">
                      <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                        {t("referrals.username")}
                      </th>
                      <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                        {t("referrals.rewardType")}
                      </th>
                      <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                        {t("referrals.amount")}
                      </th>
                      <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                        {t("referrals.source")}
                      </th>
                      <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                        {t("referrals.isUsed")}
                      </th>
                      <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                        {t("referrals.expiresAt")}
                      </th>
                      <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                        {t("referrals.createdAt")}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {rewards?.items.map((reward) => (
                      <tr
                        key={reward.id}
                        className="border-b border-[hsl(var(--separator-color))] hover:bg-[hsl(var(--bg-tertiary))]"
                      >
                        <td className="px-4 py-3 text-sm text-[hsl(var(--text-primary))]">
                          <AdminUserLink userId={reward.user_id}>{reward.username}</AdminUserLink>
                        </td>
                        <td className="px-4 py-3 text-sm">
                          <span className={`px-2 py-1 rounded text-xs font-medium ${getRewardTypeClass(reward.reward_type)}`}>
                            {getRewardTypeLabel(reward.reward_type)}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-sm font-medium text-green-500">
                          +{reward.amount}
                        </td>
                        <td className="px-4 py-3 text-sm text-[hsl(var(--text-secondary))]">
                          {reward.source}
                        </td>
                        <td className="px-4 py-3 text-sm">
                          <span className={`px-2 py-1 rounded text-xs font-medium ${
                            reward.is_used
                              ? "bg-green-500/15 text-green-500"
                              : "bg-yellow-500/15 text-yellow-500"
                          }`}>
                            {reward.is_used ? t("common:yes") : t("common:no")}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-sm text-[hsl(var(--text-secondary))]">
                          {formatDate(reward.expires_at)}
                        </td>
                        <td className="px-4 py-3 text-sm text-[hsl(var(--text-secondary))]">
                          {formatDate(reward.created_at)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Pagination */}
              {(rewards?.total ?? 0) > pageSize && (
                <div className="flex items-center justify-between px-4 py-3 border-t border-[hsl(var(--separator-color))]">
                  <div className="text-sm text-[hsl(var(--text-secondary))]">
                    {t("common:showing", {
                      from: (rewardsPage - 1) * pageSize + 1,
                      to: Math.min(rewardsPage * pageSize, rewards?.total ?? 0),
                      total: rewards?.total ?? 0,
                    })}
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => setRewardsPage((p) => Math.max(1, p - 1))}
                      disabled={rewardsPage === 1}
                      className="px-3 py-1.5 min-h-9 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg hover:bg-[hsl(var(--bg-tertiary))] disabled:opacity-50 disabled:cursor-not-allowed transition-all text-sm"
                    >
                      {t("common:previous")}
                    </button>
                    <span className="text-sm text-[hsl(var(--text-primary))]">
                      {rewardsPage} / {Math.ceil((rewards?.total ?? 0) / pageSize)}
                    </span>
                    <button
                      onClick={() => setRewardsPage((p) => Math.min(Math.ceil((rewards?.total ?? 0) / pageSize), p + 1))}
                      disabled={rewardsPage >= Math.ceil((rewards?.total ?? 0) / pageSize)}
                      className="px-3 py-1.5 min-h-9 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg hover:bg-[hsl(var(--bg-tertiary))] disabled:opacity-50 disabled:cursor-not-allowed transition-all text-sm"
                    >
                      {t("common:next")}
                    </button>
                  </div>
                </div>
              )}
            </>
          </AdminPageState>
        </div>
      )}
    </div>
  );
};

export default ReferralManagement;
