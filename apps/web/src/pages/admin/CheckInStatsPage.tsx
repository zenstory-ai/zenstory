import React from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { CalendarCheck, Calendar, TrendingUp, Users } from "lucide-react";
import { adminApi } from "../../lib/adminApi";
import { AdminPageState } from "../../components/admin";
import { StatsCard } from "../../components/admin/StatsCard";
import { getLocaleCode } from "../../lib/i18n-helpers";
import { parseUTCDate } from "../../lib/dateUtils";

export const CheckInStatsPage: React.FC = () => {
  const { t } = useTranslation(["admin", "common"]);
  const [page, setPage] = React.useState(1);
  const pageSize = 20;

  // Get check-in stats
  const {
    data: stats,
    isLoading: statsLoading,
    isFetching: statsFetching,
    isError: statsError,
    error: statsQueryError,
    refetch: refetchStats,
  } = useQuery({
    queryKey: ["admin", "check-in", "stats"],
    queryFn: adminApi.getCheckInStats,
  });

  // Get check-in records
  const {
    data: records,
    isLoading: recordsLoading,
    isFetching: recordsFetching,
    isError: recordsError,
    error: recordsQueryError,
    refetch: refetchRecords,
  } = useQuery({
    queryKey: ["admin", "check-in", "records", page],
    queryFn: () => adminApi.getCheckInRecords({ page, page_size: pageSize }),
  });
  const statsErrorText = statsQueryError instanceof Error && statsQueryError.message
    ? statsQueryError.message
    : t("common:error");
  const recordsErrorText = recordsQueryError instanceof Error && recordsQueryError.message
    ? recordsQueryError.message
    : t("common:error");

  const formatDate = (dateStr: string) => {
    const date = parseUTCDate(dateStr);
    if (Number.isNaN(date.getTime())) {
      return "-";
    }

    return date.toLocaleString(getLocaleCode(), {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  };

  const formatDateOnly = (dateStr: string) => {
    const date = new Date(dateStr);
    if (Number.isNaN(date.getTime())) {
      return "-";
    }

    return date.toLocaleDateString(getLocaleCode(), {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      timeZone: /^\d{4}-\d{2}-\d{2}$/.test(dateStr) ? "UTC" : undefined,
    });
  };

  // Calculate streak distribution summary
  const streakDistribution = stats?.streak_distribution || {};
  const streakEntries = Object.entries(streakDistribution).sort((a, b) => Number(a[0]) - Number(b[0]));
  const recordItems = records?.items ?? [];
  const totalRecords = records?.total ?? 0;

  return (
    <div className="admin-page admin-page-fluid">
      {/* Header */}
      <div>
        <h1 className="admin-page-title">
          {t("checkIn.title")}
        </h1>
        <p className="admin-page-subtitle">
          {t("checkIn.subtitle")}
        </p>
      </div>

      {/* Stats Grid */}
      <div>
        <h2 className="text-lg font-semibold mb-3 text-[hsl(var(--text-primary))]">
          {t("checkIn.stats")}
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
              icon={<CalendarCheck className="h-5 w-5" />}
              title={t("checkIn.todayCount")}
              value={stats?.today_count ?? 0}
            />
            <StatsCard
              icon={<Calendar className="h-5 w-5" />}
              title={t("checkIn.yesterdayCount")}
              value={stats?.yesterday_count ?? 0}
            />
            <StatsCard
              icon={<TrendingUp className="h-5 w-5" />}
              title={t("checkIn.weekTotal")}
              value={stats?.week_total ?? 0}
            />
            <StatsCard
              icon={<Users className="h-5 w-5" />}
              title={t("checkIn.streakDistribution")}
              value={Object.keys(streakDistribution).length}
            />
          </div>
        </AdminPageState>
      </div>

      {/* Streak Distribution */}
      {!statsLoading && !statsError && streakEntries.length > 0 && (
        <div className="admin-surface p-4">
          <h3 className="font-semibold text-[hsl(var(--text-primary))] mb-3">
            {t("checkIn.streakDistribution")}
          </h3>
          <div className="flex flex-wrap gap-2">
            {streakEntries.map(([days, count]) => (
              <div
                key={days}
                className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-[hsl(var(--bg-tertiary))] text-sm"
              >
                <span className="text-[hsl(var(--text-secondary))]">{days}{t("checkIn.streakDays").charAt(0)}:</span>
                <span className="font-semibold text-[hsl(var(--text-primary))]">{count}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Records Table */}
      <div className="admin-table-shell">
        <div className="px-4 py-3 border-b border-[hsl(var(--separator-color))]">
          <h3 className="font-semibold text-[hsl(var(--text-primary))]">
            {t("checkIn.records")}
          </h3>
        </div>

        <AdminPageState
          isLoading={recordsLoading}
          isFetching={recordsFetching}
          isError={recordsError}
          isEmpty={recordItems.length === 0}
          loadingText={t("common:loading")}
          errorText={recordsErrorText}
          emptyText={t("common:noData")}
          retryText={t("common:retry")}
          onRetry={() => {
            void refetchRecords();
          }}
          stateClassName="flex items-center justify-center py-12"
        >
          <>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[680px]">
                <thead>
                  <tr className="border-b border-[hsl(var(--separator-color))]">
                    <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                      {t("checkIn.username")}
                    </th>
                    <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                      {t("checkIn.checkInDate")}
                    </th>
                    <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                      {t("checkIn.streakDays")}
                    </th>
                    <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                      {t("checkIn.pointsEarned")}
                    </th>
                    <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                      {t("checkIn.createdAt")}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {recordItems.map((record) => (
                    <tr
                      key={record.id}
                      className="border-b border-[hsl(var(--separator-color))] hover:bg-[hsl(var(--bg-tertiary))]"
                    >
                      <td className="px-4 py-3 text-sm text-[hsl(var(--text-primary))]">
                        {record.username}
                      </td>
                      <td className="px-4 py-3 text-sm text-[hsl(var(--text-primary))]">
                        {formatDateOnly(record.check_in_date)}
                      </td>
                      <td className="px-4 py-3 text-sm">
                        <span className={`px-2 py-1 rounded-full text-xs font-medium ${
                          record.streak_days >= 7
                            ? "bg-green-500/15 text-green-500"
                            : record.streak_days >= 3
                            ? "bg-yellow-500/15 text-yellow-500"
                            : "bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-secondary))]"
                        }`}>
                          {record.streak_days}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-sm font-medium text-green-500">
                        +{record.points_earned}
                      </td>
                      <td className="px-4 py-3 text-sm text-[hsl(var(--text-secondary))]">
                        {formatDate(record.created_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Pagination */}
            {totalRecords > pageSize && (
              <div className="flex items-center justify-between px-4 py-3 border-t border-[hsl(var(--separator-color))]">
                <div className="text-sm text-[hsl(var(--text-secondary))]">
                  {t("common:showing", {
                    from: (page - 1) * pageSize + 1,
                    to: Math.min(page * pageSize, totalRecords),
                    total: totalRecords,
                  })}
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => setPage((p) => Math.max(1, p - 1))}
                    disabled={page === 1}
                    className="px-3 py-1.5 min-h-9 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg hover:bg-[hsl(var(--bg-tertiary))] disabled:opacity-50 disabled:cursor-not-allowed transition-all text-sm"
                  >
                    {t("common:previous")}
                  </button>
                  <span className="text-sm text-[hsl(var(--text-primary))]">
                    {page} / {Math.ceil(totalRecords / pageSize)}
                  </span>
                  <button
                    onClick={() => setPage((p) => Math.min(Math.ceil(totalRecords / pageSize), p + 1))}
                    disabled={page >= Math.ceil(totalRecords / pageSize)}
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
  );
};

export default CheckInStatsPage;
