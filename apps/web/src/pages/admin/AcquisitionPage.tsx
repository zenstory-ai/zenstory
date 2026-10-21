import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { Compass, UserPlus } from "lucide-react";
import { adminApi } from "../../lib/adminApi";
import { AdminPageState } from "../../components/admin";
import { StatsCard } from "../../components/admin/StatsCard";
import { formatAdminDateTime } from "../../lib/dateUtils";
import type { AcquisitionChannel, AcquisitionCounts } from "../../types/admin";

const WINDOW_OPTIONS = [7, 14, 30, 90] as const;

const CHANNEL_FALLBACKS: Record<AcquisitionChannel, string> = {
  search: "搜索引擎",
  ai: "AI 助手",
  social: "社区与社交",
  org_site: "ZenStory 官网",
  referral: "其他网站",
  campaign: "推广活动",
  paid: "付费广告",
  direct: "直接访问",
  unknown: "未记录",
};

/** New accounts of a window by first-touch channel and source (signups only, no visits). */
export default function AcquisitionPage() {
  const { t } = useTranslation(["admin", "common"]);
  const [days, setDays] = useState<(typeof WINDOW_OPTIONS)[number]>(30);
  const { data, isLoading, isFetching, isError, error, refetch } = useQuery({
    queryKey: ["admin", "dashboard", "acquisition", days],
    queryFn: () => adminApi.getAcquisitionStats(days),
  });

  const channelLabel = (channel: AcquisitionChannel) =>
    t(`acquisition.channels.${channel}`, CHANNEL_FALLBACKS[channel] ?? channel);
  const countWithRate = (count: number, rate: number | null) =>
    rate === null ? String(count) : `${count}（${Math.round(rate * 1000) / 10}%）`;
  const countCells = (row: AcquisitionCounts) => (
    <>
      <td className="px-4 py-3 text-right tabular-nums">{row.signups}</td>
      <td className="px-4 py-3 text-right tabular-nums">{countWithRate(row.activated_users, row.activation_rate)}</td>
      <td className="px-4 py-3 text-right tabular-nums">{countWithRate(row.paid_users, row.paid_rate)}</td>
    </>
  );
  const countHeaders = (
    <>
      <th scope="col" className="px-4 py-3 text-right font-medium whitespace-nowrap">{t("acquisition.signups", "注册")}</th>
      <th scope="col" className="px-4 py-3 text-right font-medium whitespace-nowrap">{t("acquisition.activated", "用过 AI")}</th>
      <th scope="col" className="px-4 py-3 text-right font-medium whitespace-nowrap">{t("acquisition.paid", "付费")}</th>
    </>
  );
  const errorText = error instanceof Error && error.message ? error.message : t("common:error");

  return (
    <div className="admin-page admin-page-fluid">
      <div>
        <h1 className="admin-page-title">{t("acquisition.title", "注册来源")}</h1>
        <p className="admin-page-subtitle">
          {t(
            "acquisition.subtitle",
            "按首次来源统计这段时间新注册的账号，以及其中用过 AI、付费的人数。只统计注册，不统计访问。",
          )}
        </p>
      </div>

      <div className="admin-surface p-3">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-sm text-[hsl(var(--text-secondary))]">{t("dashboard.windowLabel", "时间窗口")}</span>
          {WINDOW_OPTIONS.map((option) => (
            <button
              key={option}
              type="button"
              onClick={() => setDays(option)}
              aria-pressed={days === option}
              className={`min-h-11 px-3 rounded-md text-sm transition-colors ${
                days === option
                  ? "bg-[hsl(var(--accent-primary)/0.15)] font-medium text-[var(--admin-accent-text)]"
                  : "bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))]"
              }`}
            >
              {t(`acquisition.window${option}`, `${option}天`)}
            </button>
          ))}
        </div>
      </div>

      <AdminPageState
        isLoading={isLoading}
        isFetching={isFetching}
        isError={isError}
        isEmpty={!data}
        loadingText={t("common:loading")}
        errorText={errorText}
        emptyText={t("common:noData")}
        retryText={t("common:retry")}
        onRetry={() => {
          void refetch();
        }}
        stateClassName="admin-surface flex items-center justify-center py-12"
      >
        {data && (
          <div className="space-y-6">
            <p className="text-xs text-[hsl(var(--text-secondary))]">
              {t("acquisition.period", "北京时间 {{start}} 至 {{end}}", {
                start: formatAdminDateTime(data.period_start),
                end: formatAdminDateTime(data.period_end),
              })}
            </p>
            <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
              <StatsCard
                icon={<UserPlus className="h-5 w-5" />}
                title={t("acquisition.totalSignups", "新注册")}
                value={data.total_signups}
              />
              <StatsCard
                icon={<Compass className="h-5 w-5" />}
                title={t("acquisition.attributedSignups", "有来源记录")}
                value={data.attributed_signups}
              />
            </div>

            <section className="admin-table-shell overflow-x-auto">
              <h2 className="px-4 py-3 border-b border-[hsl(var(--separator-color))] font-semibold text-[hsl(var(--text-primary))]">
                {t("acquisition.byChannel", "按渠道")}
              </h2>
              {data.channels.length === 0 ? (
                <p className="px-4 py-6 text-sm text-[hsl(var(--text-secondary))]">{t("common:noData")}</p>
              ) : (
                <table className="w-full min-w-[480px] text-sm text-[hsl(var(--text-primary))]">
                  <thead className="text-[hsl(var(--text-secondary))]">
                    <tr>
                      <th scope="col" className="px-4 py-3 text-left font-medium whitespace-nowrap">{t("acquisition.channel", "渠道")}</th>
                      {countHeaders}
                    </tr>
                  </thead>
                  <tbody>
                    {data.channels.map((row) => (
                      <tr key={row.channel} className="border-t border-[hsl(var(--separator-color))]">
                        <td className="px-4 py-3 whitespace-nowrap">{channelLabel(row.channel)}</td>
                        {countCells(row)}
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </section>

            <section className="admin-table-shell overflow-x-auto">
              <h2 className="px-4 py-3 border-b border-[hsl(var(--separator-color))] font-semibold text-[hsl(var(--text-primary))]">
                {t("acquisition.bySource", "按来源")}
              </h2>
              {data.sources.length === 0 ? (
                <p className="px-4 py-6 text-sm text-[hsl(var(--text-secondary))]">{t("common:noData")}</p>
              ) : (
                <table className="w-full min-w-[560px] text-sm text-[hsl(var(--text-primary))]">
                  <thead className="text-[hsl(var(--text-secondary))]">
                    <tr>
                      <th scope="col" className="px-4 py-3 text-left font-medium whitespace-nowrap">{t("acquisition.channel", "渠道")}</th>
                      <th scope="col" className="px-4 py-3 text-left font-medium whitespace-nowrap">{t("acquisition.source", "来源")}</th>
                      {countHeaders}
                    </tr>
                  </thead>
                  <tbody>
                    {data.sources.map((row) => (
                      <tr key={`${row.channel}:${row.source}`} className="border-t border-[hsl(var(--separator-color))]">
                        <td className="px-4 py-3 whitespace-nowrap">{channelLabel(row.channel)}</td>
                        <td className="px-4 py-3 break-all">{row.source || "—"}</td>
                        {countCells(row)}
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </section>

            <p className="text-xs text-[hsl(var(--text-secondary))]">
              {t(
                "acquisition.note",
                "来源取自账号注册前首次带来源信息的访问（30 天内）。「未记录」是上线来源统计之前注册、或浏览器禁用了本地存储的账号。用过 AI、付费按注册后至今统计。",
              )}
            </p>
          </div>
        )}
      </AdminPageState>
    </div>
  );
}
