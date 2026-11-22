import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import {
  Activity,
  BookOpen,
  CheckCircle2,
  Clock,
  Cpu,
  Hand,
  RefreshCw,
  RotateCcw,
  ThumbsUp,
} from "lucide-react";

import { adminApi } from "../../lib/adminApi";
import { AdminPageState } from "../../components/admin";
import { StatsCard } from "../../components/admin/StatsCard";
import { AdminSegmented } from "../../components/admin/AdminSegmented";
import { Button } from "../../components/ui/Button";
import { formatAdminDateTime } from "../../lib/dateUtils";

const WINDOW_OPTIONS = [7, 14, 30] as const;

const BEIJING_CUTOFF_FORMAT: Intl.DateTimeFormatOptions = {
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
  timeZone: "Asia/Shanghai",
};

function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

function formatMs(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (value >= 1000) {
    return `${(value / 1000).toFixed(1)}s`;
  }
  return `${value}ms`;
}

function formatRateDelta(delta: number | null | undefined): string | null {
  if (delta === null || delta === undefined) return null;
  const pp = delta * 100;
  const sign = pp > 0 ? "+" : "";
  return `${sign}${pp.toFixed(1)}pp`;
}

function formatMsDelta(delta: number | null | undefined): string | null {
  if (delta === null || delta === undefined) return null;
  const sign = delta > 0 ? "+" : "";
  if (Math.abs(delta) >= 1000) {
    return `${sign}${(delta / 1000).toFixed(1)}s`;
  }
  return `${sign}${delta}ms`;
}

function formatNumberDelta(delta: number | null | undefined): string | null {
  if (delta === null || delta === undefined) return null;
  const sign = delta > 0 ? "+" : "";
  return `${sign}${delta.toLocaleString()}`;
}

function formatMinutesDelta(delta: number | null | undefined): string | null {
  if (delta === null || delta === undefined) return null;
  const sign = delta > 0 ? "+" : "";
  return `${sign}${delta.toFixed(1)}m`;
}

function semanticTrend(
  delta: number | null | undefined,
  higherIsBetter: boolean,
): "up" | "down" | "neutral" {
  if (delta === null || delta === undefined || delta === 0) return "neutral";
  const improved = higherIsBetter ? delta > 0 : delta < 0;
  return improved ? "up" : "down";
}

function formatCompactTokens(tokens: number): string {
  if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(1)}M`;
  if (tokens >= 1_000) return `${(tokens / 1_000).toFixed(1)}K`;
  return String(tokens);
}

export default function UxHealthPage() {
  const { t } = useTranslation(["admin", "common"]);
  const [days, setDays] = useState<(typeof WINDOW_OPTIONS)[number]>(7);

  const { data, isLoading, isFetching, isError, error, refetch } = useQuery({
    queryKey: ["admin", "dashboard", "ux-health", days],
    queryFn: () => adminApi.getUxHealthDashboard(days),
  });

  const errorText = error instanceof Error && error.message ? error.message : t("common:error");
  const metrics = data?.current.metrics;
  const previous = data?.previous.metrics;
  const delta = data?.delta;

  const periodEnd = data
    ? formatAdminDateTime(data.current.period_end, BEIJING_CUTOFF_FORMAT)
    : "—";

  const reasonLabel = (reason: string) =>
    t(`uxHealth.stopReasons.${reason}`, { defaultValue: reason });

  const presetLabel = (preset: string) =>
    t(`feedback.chatVotes.presets.${preset}`, { defaultValue: preset });

  const priorWithDelta = (priorValue: string, deltaText: string | null) =>
    deltaText
      ? t("uxHealth.priorWithDelta", "上期 {{prior}}（{{delta}}）", {
          prior: priorValue,
          delta: deltaText,
        })
      : t("uxHealth.priorOnly", "上期 {{prior}}", { prior: priorValue });

  const ttftValue = metrics
    ? metrics.ttft_p50_ms === null && metrics.ttft_p95_ms === null
      ? "—"
      : `${formatMs(metrics.ttft_p50_ms)} / ${formatMs(metrics.ttft_p95_ms)}`
    : "—";

  const totalInputTokens = (metrics?.cache_hit_tokens ?? 0) + (metrics?.cache_miss_tokens ?? 0);
  const desktopShare =
    metrics && metrics.mobile_share !== null ? Math.max(0, 1 - metrics.mobile_share) : null;

  return (
    <div className="admin-page admin-page-fluid">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="admin-page-title">{t("uxHealth.title", "体验健康")}</h1>
        <Button
          type="button"
          variant="ghost"
          size="touch"
          leftIcon={<RefreshCw className={`h-4 w-4 ${isFetching ? "animate-spin" : ""}`} />}
          onClick={() => {
            void refetch();
          }}
          disabled={isFetching}
        >
          {t("dashboard.refresh", "刷新")}
        </Button>
      </div>

      <AdminSegmented
        label={t("dashboard.windowLabel", "时间窗口")}
        options={[...WINDOW_OPTIONS]}
        value={days}
        onChange={setDays}
        renderLabel={(option) => t(`dashboard.window${option}`, `${option}天`)}
      />

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
        {data && metrics && previous && delta && (
          <div className="space-y-6">
            <p className="text-xs text-[hsl(var(--text-secondary))]">
              {t("uxHealth.period", "北京时间 · 截至 {{end}}，今日数据未完结", { end: periodEnd })}
            </p>

            {/* Core UX Health KPI Scorecard */}
            <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
              <StatsCard
                icon={<Clock className="h-5 w-5" />}
                title={t("uxHealth.cards.ttft", "供应商首字延迟 (p50 / p95)")}
                value={ttftValue}
                trend={semanticTrend(delta.ttft_p95_ms, false)}
                trendValue={priorWithDelta(
                  formatMs(previous.ttft_p95_ms),
                  formatMsDelta(delta.ttft_p95_ms),
                )}
                description={t(
                  "uxHealth.cards.ttftDetail",
                  "首字 ≥10s 慢尾 {{ttftSlow}} · 总耗时 ≥10s {{durSlow}}",
                  {
                    ttftSlow: formatPercent(metrics.ttft_slow_tail_rate),
                    durSlow: formatPercent(metrics.duration_slow_tail_rate),
                  },
                )}
              />

              <StatsCard
                icon={<Cpu className="h-5 w-5" />}
                title={t("uxHealth.cards.cacheHit", "Prompt 缓存命中率")}
                value={formatPercent(metrics.prompt_cache_hit_rate)}
                trend={semanticTrend(delta.prompt_cache_hit_rate, true)}
                trendValue={priorWithDelta(
                  formatPercent(previous.prompt_cache_hit_rate),
                  formatRateDelta(delta.prompt_cache_hit_rate),
                )}
                description={t(
                  "uxHealth.cards.cacheDetail",
                  "命中 {{hit}} / 输入 {{total}}",
                  {
                    hit: formatCompactTokens(metrics.cache_hit_tokens),
                    total: formatCompactTokens(totalInputTokens),
                  },
                )}
              />

              <StatsCard
                icon={<CheckCircle2 className="h-5 w-5" />}
                title={t("uxHealth.cards.healthyRounds", "健康轮次完成率")}
                value={formatPercent(metrics.healthy_round_rate)}
                trend={semanticTrend(delta.healthy_round_rate, true)}
                trendValue={priorWithDelta(
                  formatPercent(previous.healthy_round_rate),
                  formatRateDelta(delta.healthy_round_rate),
                )}
                description={t(
                  "uxHealth.cards.healthyDetail",
                  "健康 {{healthy}} / 总轮次 {{total}}",
                  { healthy: metrics.healthy_rounds, total: metrics.total_rounds },
                )}
              />

              <StatsCard
                icon={<Hand className="h-5 w-5" />}
                title={t("uxHealth.cards.userStopped", "中途主动停止率")}
                value={formatPercent(metrics.user_stopped_rate)}
                trend={semanticTrend(delta.user_stopped_rate, false)}
                trendValue={priorWithDelta(
                  formatPercent(previous.user_stopped_rate),
                  formatRateDelta(delta.user_stopped_rate),
                )}
                description={t(
                  "uxHealth.cards.stopDetail",
                  "主动停止 {{stopped}} · 断线 {{disconnected}} ({{disconnectedRate}})",
                  {
                    stopped: metrics.user_stopped_rounds,
                    disconnected: metrics.client_disconnected_rounds,
                    disconnectedRate: formatPercent(metrics.client_disconnected_rate),
                  },
                )}
              />

              <StatsCard
                icon={<RotateCcw className="h-5 w-5" />}
                title={t("uxHealth.cards.editUndo", "AI 修改撤销率")}
                value={formatPercent(metrics.ai_edit_undo_rate)}
                trend={semanticTrend(delta.ai_edit_undo_rate, false)}
                trendValue={priorWithDelta(
                  formatPercent(previous.ai_edit_undo_rate),
                  formatRateDelta(delta.ai_edit_undo_rate),
                )}
                description={t(
                  "uxHealth.cards.undoRestoreDetail",
                  "修改撤销 {{undone}}/{{edits}} · 删除恢复 {{restored}}/{{deletes}} ({{restoreRate}})",
                  {
                    undone: metrics.ai_edit_undone,
                    edits: metrics.ai_edit_cards,
                    restored: metrics.ai_delete_restored,
                    deletes: metrics.ai_delete_cards,
                    restoreRate: formatPercent(metrics.ai_delete_restore_rate),
                  },
                )}
              />

              <StatsCard
                icon={<ThumbsUp className="h-5 w-5" />}
                title={t("uxHealth.cards.thumbsUp", "对话好评率")}
                value={formatPercent(metrics.thumbs_up_rate)}
                trend={semanticTrend(delta.thumbs_up_rate, true)}
                trendValue={priorWithDelta(
                  formatPercent(previous.thumbs_up_rate),
                  formatRateDelta(delta.thumbs_up_rate),
                )}
                description={t(
                  "uxHealth.cards.thumbsDetail",
                  "点赞 {{up}} / 点踩 {{down}}（共 {{total}} 票）",
                  {
                    up: metrics.feedback_up,
                    down: metrics.feedback_down,
                    total: metrics.feedback_total,
                  },
                )}
              />

              <StatsCard
                icon={<Activity className="h-5 w-5" />}
                title={t("uxHealth.cards.ttfv", "首次价值耗时 (TTFV p50)")}
                value={
                  metrics.ttfv_p50_minutes === null
                    ? "—"
                    : t("uxHealth.minutesValue", "{{value}} 分钟", {
                        value: metrics.ttfv_p50_minutes.toFixed(1),
                      })
                }
                trend={semanticTrend(delta.ttfv_p50_minutes, false)}
                trendValue={priorWithDelta(
                  previous.ttfv_p50_minutes === null
                    ? "—"
                    : `${previous.ttfv_p50_minutes.toFixed(1)}m`,
                  formatMinutesDelta(delta.ttfv_p50_minutes),
                )}
                description={t(
                  "uxHealth.cards.retentionDetail",
                  "次周写作留存 {{rate}} ({{returned}}/{{eligible}})",
                  {
                    rate: formatPercent(metrics.w1_retention_rate),
                    returned: metrics.w1_returning_writers,
                    eligible: metrics.w1_eligible_writers,
                  },
                )}
              />

              <StatsCard
                icon={<BookOpen className="h-5 w-5" />}
                title={t("uxHealth.cards.output", "成稿章节产出")}
                value={t("uxHealth.chaptersValue", "{{count}} 章", {
                  count: metrics.chapters_written,
                })}
                trend={semanticTrend(delta.net_words_written, true)}
                trendValue={priorWithDelta(
                  t("uxHealth.wordsShort", "{{value}} 字", {
                    value: previous.net_words_written.toLocaleString(),
                  }),
                  formatNumberDelta(delta.net_words_written),
                )}
                description={t(
                  "uxHealth.cards.outputDetail",
                  "净增 {{words}} 字 · 移动端 {{mobile}} / 桌面端 {{desktop}}",
                  {
                    words: metrics.net_words_written.toLocaleString(),
                    mobile: formatPercent(metrics.mobile_share),
                    desktop: formatPercent(desktopShare),
                  },
                )}
              />
            </div>

            {/* Abnormal stops + Top dislike presets */}
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <section className="admin-surface p-4">
                <div className="mb-3 flex items-center justify-between gap-2">
                  <h2 className="text-base font-semibold text-[hsl(var(--text-primary))]">
                    {t("uxHealth.abnormalStops.title", "异常终止分布")}
                  </h2>
                  <span className="text-xs tabular-nums text-[hsl(var(--text-secondary))]">
                    {t("uxHealth.abnormalStops.summary", "共 {{count}} 轮（{{rate}}）", {
                      count: metrics.abnormal_stop_rounds,
                      rate: formatPercent(metrics.abnormal_stop_rate),
                    })}
                  </span>
                </div>

                {metrics.abnormal_stops.length === 0 ? (
                  <p className="text-sm text-[hsl(var(--text-secondary))]">
                    {t("uxHealth.abnormalStops.empty", "本期无异常终止轮次")}
                  </p>
                ) : (
                  <div className="space-y-2">
                    {metrics.abnormal_stops.map((item) => (
                      <div
                        key={item.reason}
                        className="flex items-center justify-between gap-3 border-b border-[hsl(var(--separator-color)/0.45)] py-2 text-sm last:border-0"
                      >
                        <span className="break-words text-[hsl(var(--text-primary))]">
                          {reasonLabel(item.reason)}
                        </span>
                        <span className="shrink-0 tabular-nums text-[hsl(var(--text-secondary))]">
                          {item.count} ({formatPercent(item.rate)})
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </section>

              <section className="admin-surface p-4">
                <div className="mb-3 flex items-center justify-between gap-2">
                  <h2 className="text-base font-semibold text-[hsl(var(--text-primary))]">
                    {t("uxHealth.dislikePresets.title", "高频点踩原因")}
                  </h2>
                  <Link
                    to="/admin/feedback"
                    className="text-xs font-medium text-[var(--admin-accent-text)] hover:underline"
                  >
                    {t("uxHealth.dislikePresets.viewAll", "前往对话反馈")}
                  </Link>
                </div>

                {metrics.top_dislike_presets.length === 0 ? (
                  <p className="text-sm text-[hsl(var(--text-secondary))]">
                    {t("uxHealth.dislikePresets.empty", "本期暂无带标签的点踩反馈")}
                  </p>
                ) : (
                  <ul
                    className="flex flex-wrap gap-2"
                    aria-label={t("uxHealth.dislikePresets.title", "高频点踩原因")}
                  >
                    {metrics.top_dislike_presets.map((item) => (
                      <li
                        key={item.preset}
                        className="inline-flex items-center gap-1.5 rounded-full border border-[hsl(var(--separator-color))] bg-[hsl(var(--bg-tertiary)/0.6)] px-3 py-1 text-xs text-[hsl(var(--text-primary))]"
                      >
                        <span>{presetLabel(item.preset)}</span>
                        <span className="font-semibold tabular-nums text-[hsl(var(--text-secondary))]">
                          {item.count}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            </div>

            {/* Daily Trend Table */}
            <section className="admin-table-shell">
              <h2 className="border-b border-[hsl(var(--separator-color))] px-4 py-3 text-base font-semibold text-[hsl(var(--text-primary))]">
                {t("uxHealth.daily.title", "每日趋势")}
              </h2>
              {data.daily.length === 0 ? (
                <p className="px-4 py-6 text-sm text-[hsl(var(--text-secondary))]">
                  {t("common:noData")}
                </p>
              ) : (
                <div
                  className="overflow-x-auto"
                  tabIndex={0}
                  role="region"
                  aria-label={t("uxHealth.daily.title", "每日趋势")}
                >
                  <table className="w-full min-w-[600px] text-sm text-[hsl(var(--text-primary))]">
                    <thead className="bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-secondary))]">
                      <tr>
                        <th scope="col" className="whitespace-nowrap px-4 py-3 text-left font-medium">
                          {t("uxHealth.daily.date", "日期")}
                        </th>
                        <th scope="col" className="whitespace-nowrap px-4 py-3 text-right font-medium">
                          {t("uxHealth.daily.rounds", "轮次")}
                        </th>
                        <th scope="col" className="whitespace-nowrap px-4 py-3 text-right font-medium">
                          {t("uxHealth.daily.healthyRate", "健康完成率")}
                        </th>
                        <th scope="col" className="whitespace-nowrap px-4 py-3 text-right font-medium">
                          {t("uxHealth.daily.undoRate", "修改撤销率")}
                        </th>
                        <th scope="col" className="whitespace-nowrap px-4 py-3 text-right font-medium">
                          {t("uxHealth.daily.ttftP95", "首字 p95")}
                        </th>
                        <th scope="col" className="whitespace-nowrap px-4 py-3 text-right font-medium">
                          {t("uxHealth.daily.thumbsUpRate", "好评率")}
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.daily.map((row) => (
                        <tr
                          key={row.date}
                          className="border-t border-[hsl(var(--separator-color))]"
                        >
                          <th scope="row" className="whitespace-nowrap px-4 py-3 text-left font-normal">
                            {row.date}
                          </th>
                          <td className="px-4 py-3 text-right tabular-nums">{row.rounds}</td>
                          <td className="px-4 py-3 text-right tabular-nums">
                            {formatPercent(row.healthy_rate)}
                          </td>
                          <td className="px-4 py-3 text-right tabular-nums">
                            {formatPercent(row.undo_rate)}
                          </td>
                          <td className="px-4 py-3 text-right tabular-nums">
                            {formatMs(row.ttft_p95_ms)}
                          </td>
                          <td className="px-4 py-3 text-right tabular-nums">
                            {formatPercent(row.thumbs_up_rate)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            {/* Single collapsible methodology disclosure */}
            <details className="admin-surface px-4 py-3 text-xs text-[hsl(var(--text-secondary))]">
              <summary className="cursor-pointer text-sm font-medium text-[hsl(var(--text-primary))]">
                {t("methodology", "统计口径")}
              </summary>
              <ul className="mt-2 list-disc space-y-1 pl-4">
                <li>{t("uxHealth.methodology.scope", "仅统计非管理员客户，排除历史回填记录；时间窗口按北京时间自然日划分，与上一等长时段（含同等当日进度）比较。")}</li>
                <li>{t("uxHealth.methodology.speed", "供应商首字延迟（TTFT）与 ≥10s 慢尾率按成功流式调用计算，总耗时 ≥10s 慢尾按全部成功调用计算；Prompt 缓存命中率按输入 tokens 加权。")}</li>
                <li>{t("uxHealth.methodology.quality", "健康轮次指 stop_reason 为 end_turn 且无未执行工具文本、未落盘空文件或失控截停；主动停止（user_stopped）与客户端断线（client_disconnected）分开统计。")}</li>
                <li>{t("uxHealth.methodology.undo", "修改撤销率与删除恢复率来自助手消息工具卡的实际回写状态，对话好评率按消息产生时间窗口统计。")}</li>
                <li>{t("uxHealth.methodology.value", "TTFV 为本期新注册用户到首次 AI 写稿或首次保存文件的中位分钟数；次周写作留存比较前 7 天与近 7 天活跃写作者；成稿产出统计未删除草稿与剧本相对期前版本的净增字数；设备占比取自激活事件中的 is_mobile 字段。")}</li>
              </ul>
            </details>
          </div>
        )}
      </AdminPageState>
    </div>
  );
}
