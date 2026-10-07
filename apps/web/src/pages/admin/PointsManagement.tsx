import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Search, Coins, TrendingUp, TrendingDown, Clock, Users, X } from "lucide-react";
import { adminApi } from "../../lib/adminApi";
import { AdminPageState } from "../../components/admin";
import { StatsCard } from "../../components/admin/StatsCard";
import type { PointsStats, AdminPointsBalance, PointsAdjustRequest } from "../../types/admin";
import { toast } from "../../lib/toast";
import { formatAdminDateTime } from "../../lib/dateUtils";
import { AdminUserLink } from "../../components/admin/AdminUserLink";

export const PointsManagement: React.FC = () => {
  const { t } = useTranslation(["admin", "common"]);
  const queryClient = useQueryClient();
  const [searchUserId, setSearchUserId] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [adjustDialogOpen, setAdjustDialogOpen] = useState(false);
  const [adjustForm, setAdjustForm] = useState<PointsAdjustRequest>({
    amount: 0,
    reason: "",
  });
  const [transactionPage, setTransactionPage] = useState(1);

  const pageSize = 20;

  // Get points system stats
  const {
    data: stats,
    isLoading: statsLoading,
    isFetching: statsFetching,
    isError: statsError,
    error: statsQueryError,
    refetch: refetchStats,
  } = useQuery<PointsStats>({
    queryKey: ["admin", "points", "stats"],
    queryFn: adminApi.getPointsStats,
  });

  // Get user points details
  const {
    data: userPoints,
    isLoading: userLoading,
    isFetching: userFetching,
    isError: userError,
    error: userQueryError,
    refetch: refetchUserPoints,
  } = useQuery<AdminPointsBalance>({
    queryKey: ["admin", "points", "user", searchUserId],
    queryFn: () => adminApi.getUserPoints(searchUserId),
    enabled: !!searchUserId,
  });

  // Get user transaction history
  const {
    data: transactions,
    isLoading: transactionsLoading,
    isFetching: transactionsFetching,
    isError: transactionsError,
    error: transactionsQueryError,
    refetch: refetchTransactions,
  } = useQuery({
    queryKey: ["admin", "points", "user", searchUserId, "transactions", transactionPage],
    queryFn: () => adminApi.getUserPointsTransactions(searchUserId, { page: transactionPage, page_size: pageSize }),
    enabled: !!searchUserId,
  });

  // Adjust points mutation
  const adjustMutation = useMutation({
    mutationFn: ({ userId, data }: { userId: string; data: PointsAdjustRequest }) =>
      adminApi.adjustUserPoints(userId, data),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["admin", "points"] });
      setAdjustDialogOpen(false);
      setAdjustForm({ amount: 0, reason: "" });
      toast.success(t("points.adjustSuccess", { balance: result.new_balance }));
    },
    onError: () => {
      toast.error(t("points.adjustFailed"));
    },
  });

  const handleSearch = () => {
    if (searchInput.trim()) {
      setSearchUserId(searchInput.trim());
      setTransactionPage(1);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      handleSearch();
    }
  };

  const handleAdjustPoints = () => {
    if (!searchUserId || !adjustForm.reason.trim()) return;
    adjustMutation.mutate({ userId: searchUserId, data: adjustForm });
  };

  const formatDate = (dateStr: string | null | undefined) => formatAdminDateTime(dateStr);

  const getTransactionTypeLabel = (type: string) => {
    return t(`points.types.${type}`, type);
  };

  const getTransactionDescription = (tx: { transaction_type: string; description: string | null }) => {
    if (!tx.description) return "-";
    if (["check_in", "check_in_streak", "redeem_pro"].includes(tx.transaction_type)) return "-";
    return tx.description;
  };

  const statsErrorText = statsQueryError instanceof Error && statsQueryError.message
    ? statsQueryError.message
    : t("common:error");
  const userErrorText = userQueryError instanceof Error && userQueryError.message
    ? userQueryError.message
    : t("common:error");
  const transactionsErrorText = transactionsQueryError instanceof Error && transactionsQueryError.message
    ? transactionsQueryError.message
    : t("common:error");

  return (
    <div className="admin-page admin-page-fluid">
      {/* Header */}
      <div>
        <h1 className="admin-page-title">
          {t("points.title")}
        </h1>
        <p className="admin-page-subtitle">
          {t("points.subtitle")}
        </p>
      </div>

      {/* Stats Grid */}
      <div>
        <h2 className="text-lg font-semibold mb-3 text-[hsl(var(--text-primary))]">
          {t("points.stats")}
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
              icon={<TrendingUp className="h-5 w-5" />}
              title={t("points.totalIssued")}
              value={stats?.total_points_issued ?? 0}
            />
            <StatsCard
              icon={<TrendingDown className="h-5 w-5" />}
              title={t("points.totalSpent")}
              value={stats?.total_points_spent ?? 0}
            />
            <StatsCard
              icon={<Clock className="h-5 w-5" />}
              title={t("points.totalExpired")}
              value={stats?.total_points_expired ?? 0}
            />
            <StatsCard
              icon={<Users className="h-5 w-5" />}
              title={t("points.activeUsers")}
              value={stats?.active_users_with_points ?? 0}
            />
          </div>
        </AdminPageState>
      </div>

      {/* User Search */}
      <div className="admin-surface p-4">
        <h2 className="text-lg font-semibold mb-3 text-[hsl(var(--text-primary))]">
          {t("points.userDetails")}
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
              placeholder={t("points.searchUser")}
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

      {/* User Points Details */}
      {searchUserId ? (
        <AdminPageState
          isLoading={userLoading}
          isFetching={userFetching}
          isError={userError}
          isEmpty={!userPoints || !userPoints.user_id}
          loadingText={t("common:loading")}
          errorText={userErrorText}
          emptyText={t("points.noUser")}
          retryText={t("common:retry")}
          onRetry={() => {
            void refetchUserPoints();
          }}
          stateClassName="admin-surface flex items-center justify-center py-12"
        >
          <div className="space-y-4">
            {/* User Balance Card */}
            <div className="admin-surface p-4">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                <div className="space-y-2">
                  <div className="flex items-center gap-2">
                    <Coins className="h-5 w-5 text-[hsl(var(--accent-primary))]" />
                    <span className="font-medium text-[hsl(var(--text-primary))]">
                      <AdminUserLink userId={userPoints?.user_id}>{userPoints?.username ?? "-"}</AdminUserLink>
                    </span>
                    <span className="text-sm text-[hsl(var(--text-secondary))]">
                      ({userPoints?.email ?? "-"})
                    </span>
                  </div>
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-sm">
                    <div>
                      <span className="text-[hsl(var(--text-secondary))]">{t("points.available")}:</span>
                      <span className="ml-2 font-semibold text-[hsl(var(--text-primary))]">
                        {(userPoints?.available ?? 0).toLocaleString()}
                      </span>
                    </div>
                    <div>
                      <span className="text-[hsl(var(--text-secondary))]">{t("points.pendingExpiration")}:</span>
                      <span className="ml-2 font-semibold text-[hsl(var(--text-primary))]">
                        {(userPoints?.pending_expiration ?? 0).toLocaleString()}
                      </span>
                    </div>
                    <div>
                      <span className="text-[hsl(var(--text-secondary))]">{t("points.totalEarned")}:</span>
                      <span className="ml-2 font-semibold text-green-500">
                        {(userPoints?.total_earned ?? 0).toLocaleString()}
                      </span>
                    </div>
                    <div>
                      <span className="text-[hsl(var(--text-secondary))]">{t("points.totalSpent")}:</span>
                      <span className="ml-2 font-semibold text-red-500">
                        {(userPoints?.total_spent ?? 0).toLocaleString()}
                      </span>
                    </div>
                  </div>
                </div>
                <button
                  onClick={() => setAdjustDialogOpen(true)}
                  className="w-full sm:w-auto px-4 py-2.5 min-h-11 bg-[hsl(var(--accent-primary))] text-white rounded-lg hover:opacity-90 active:scale-95 transition-all text-sm"
                >
                  {t("points.adjustPoints")}
                </button>
              </div>
            </div>

            {/* Transaction History */}
            <div className="admin-table-shell">
              <div className="px-4 py-3 border-b border-[hsl(var(--separator-color))]">
                <h3 className="font-semibold text-[hsl(var(--text-primary))]">
                  {t("points.transactions")}
                </h3>
              </div>
              <AdminPageState
                isLoading={transactionsLoading}
                isFetching={transactionsFetching}
                isError={transactionsError}
                isEmpty={(transactions?.items ?? []).length === 0}
                loadingText={t("common:loading")}
                errorText={transactionsErrorText}
                emptyText={t("common:noData")}
                retryText={t("common:retry")}
                onRetry={() => {
                  void refetchTransactions();
                }}
                stateClassName="flex items-center justify-center py-12"
              >
                <>
                  <div className="overflow-x-auto">
                    <table className="w-full min-w-[720px]">
                      <thead>
                        <tr className="border-b border-[hsl(var(--separator-color))]">
                          <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                            {t("points.transactionType")}
                          </th>
                          <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                            {t("points.amount")}
                          </th>
                          <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                            {t("points.balanceAfter")}
                          </th>
                          <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                            {t("points.description")}
                          </th>
                          <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                            {t("points.expiresAt")}
                          </th>
                          <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                            {t("points.createdAt")}
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {transactions?.items.map((tx) => (
                          <tr
                            key={tx.id}
                            className="border-b border-[hsl(var(--separator-color))] hover:bg-[hsl(var(--bg-tertiary))]"
                          >
                            <td className="px-4 py-3 text-sm">
                              <span className={`px-2 py-1 rounded text-xs font-medium ${
                                tx.amount > 0
                                  ? "bg-green-500/15 text-green-500"
                                  : "bg-red-500/15 text-red-500"
                              }`}>
                                {getTransactionTypeLabel(tx.transaction_type)}
                              </span>
                            </td>
                            <td className={`px-4 py-3 text-sm font-medium ${
                              tx.amount > 0 ? "text-green-500" : "text-red-500"
                            }`}>
                              {tx.amount > 0 ? "+" : ""}{tx.amount.toLocaleString()}
                            </td>
                            <td className="px-4 py-3 text-sm text-[hsl(var(--text-primary))]">
                              {tx.balance_after.toLocaleString()}
                            </td>
                            <td className="px-4 py-3 text-sm text-[hsl(var(--text-secondary))]">
                              {getTransactionDescription(tx)}
                            </td>
                            <td className="px-4 py-3 text-sm">
                              {tx.expires_at ? (
                                <span className={tx.is_expired ? "text-red-500 line-through" : "text-[hsl(var(--text-secondary))]"}>
                                  {formatDate(tx.expires_at)}
                                </span>
                              ) : (
                                <span className="text-[hsl(var(--text-secondary))]">-</span>
                              )}
                            </td>
                            <td className="px-4 py-3 text-sm text-[hsl(var(--text-secondary))]">
                              {formatDate(tx.created_at)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>

                  {/* Pagination */}
                  {(transactions?.total ?? 0) > pageSize && (
                    <div className="flex items-center justify-between px-4 py-3 border-t border-[hsl(var(--separator-color))]">
                      <div className="text-sm text-[hsl(var(--text-secondary))]">
                        {t("common:showing", {
                          from: (transactionPage - 1) * pageSize + 1,
                          to: Math.min(transactionPage * pageSize, transactions?.total ?? 0),
                          total: transactions?.total ?? 0,
                        })}
                      </div>
                      <div className="flex items-center gap-2">
                        <button
                          onClick={() => setTransactionPage((p) => Math.max(1, p - 1))}
                          disabled={transactionPage === 1}
                          className="px-3 py-1.5 min-h-9 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg hover:bg-[hsl(var(--bg-tertiary))] disabled:opacity-50 disabled:cursor-not-allowed transition-all text-sm"
                        >
                          {t("common:previous")}
                        </button>
                        <span className="text-sm text-[hsl(var(--text-primary))]">
                          {transactionPage} / {Math.ceil((transactions?.total ?? 0) / pageSize)}
                        </span>
                        <button
                          onClick={() => setTransactionPage((p) => Math.min(Math.ceil((transactions?.total ?? 0) / pageSize), p + 1))}
                          disabled={transactionPage >= Math.ceil((transactions?.total ?? 0) / pageSize)}
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
        </AdminPageState>
      ) : null}

      {/* Adjust Points Dialog */}
      {adjustDialogOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50">
          <div className="bg-[hsl(var(--bg-primary))] border border-[hsl(var(--separator-color))] rounded-lg shadow-xl w-full max-w-md">
            <div className="flex items-center justify-between px-4 sm:px-6 py-4 border-b border-[hsl(var(--separator-color))]">
              <h2 className="text-lg font-semibold text-[hsl(var(--text-primary))]">
                {t("points.adjustPoints")}
              </h2>
              <button
                onClick={() => setAdjustDialogOpen(false)}
                disabled={adjustMutation.isPending}
                aria-label={t("common:close")}
                className="p-2.5 touch-target hover:bg-[hsl(var(--bg-tertiary))] rounded transition-colors"
              >
                <X size={20} className="text-[hsl(var(--text-secondary))]" />
              </button>
            </div>

            <div className="px-4 sm:px-6 py-4 space-y-4">
              <div>
                <label className="block text-sm font-medium text-[hsl(var(--text-primary))] mb-1">
                  {t("points.adjustAmount")}
                </label>
                <input
                  type="number"
                  value={adjustForm.amount}
                  onChange={(e) => setAdjustForm({ ...adjustForm, amount: Number(e.target.value) })}
                  placeholder={t("points.adjustAmountPlaceholder")}
                  className="w-full px-3 py-2.5 min-h-11 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg focus:outline-none focus:ring-2 focus:ring-[hsl(var(--accent-primary))] text-[hsl(var(--text-primary))]"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-[hsl(var(--text-primary))] mb-1">
                  {t("points.adjustReason")}
                </label>
                <textarea
                  value={adjustForm.reason}
                  onChange={(e) => setAdjustForm({ ...adjustForm, reason: e.target.value })}
                  placeholder={t("points.adjustReasonPlaceholder")}
                  rows={3}
                  className="w-full px-3 py-2.5 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg focus:outline-none focus:ring-2 focus:ring-[hsl(var(--accent-primary))] text-[hsl(var(--text-primary))] resize-none"
                />
              </div>
            </div>

            <div className="flex flex-col-reverse sm:flex-row items-center justify-end gap-2 px-4 sm:px-6 py-4 border-t border-[hsl(var(--separator-color))]">
              <button
                onClick={() => setAdjustDialogOpen(false)}
                disabled={adjustMutation.isPending}
                className="w-full sm:w-auto px-4 py-2.5 min-h-11 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg hover:bg-[hsl(var(--bg-tertiary))] active:scale-95 transition-all text-sm text-[hsl(var(--text-primary))] disabled:opacity-50"
              >
                {t("common:cancel")}
              </button>
              <button
                onClick={handleAdjustPoints}
                disabled={adjustMutation.isPending || !adjustForm.reason.trim()}
                className="w-full sm:w-auto px-4 py-2.5 min-h-11 bg-[hsl(var(--accent-primary))] text-white rounded-lg hover:opacity-90 active:scale-95 transition-all text-sm disabled:opacity-50"
              >
                {adjustMutation.isPending ? t("common:loading") : t("common:confirm")}
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
};

export default PointsManagement;
