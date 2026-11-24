import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ThumbsDown, ThumbsUp, MessageSquare, Percent } from "lucide-react";

import { adminApi } from "../../lib/adminApi";
import type { ChatVoteFilter } from "../../types/admin";
import { AdminPageState, AdminSelect } from "./index";
import { AdminSegmented } from "./AdminSegmented";
import { StatsCard } from "./StatsCard";
import { AdminUserLink } from "./AdminUserLink";
import { Button } from "../ui/Button";
import { formatAdminDateTime } from "../../lib/dateUtils";

const WINDOW_OPTIONS = [7, 14, 30] as const;
const PAGE_SIZE = 20;

function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

export function ChatVotesPanel({ isCompact }: { isCompact: boolean }) {
  const { t } = useTranslation(["admin", "common"]);
  const [days, setDays] = useState<(typeof WINDOW_OPTIONS)[number]>(7);
  const [voteFilter, setVoteFilter] = useState<ChatVoteFilter>("all");
  const [page, setPage] = useState(1);

  const { data, isLoading, isFetching, isError, error, refetch } = useQuery({
    queryKey: ["admin", "feedback", "chat-votes", days, voteFilter, page],
    queryFn: () =>
      adminApi.getChatVotes({
        days,
        vote: voteFilter,
        page,
        page_size: PAGE_SIZE,
      }),
    staleTime: 30_000,
    retry: false,
  });

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const summary = data?.summary;
  const queryErrorText =
    error instanceof Error && error.message ? error.message : t("common:error");

  const presetLabel = (preset: string) =>
    t(`feedback.chatVotes.presets.${preset}`, { defaultValue: preset });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <AdminSegmented
          label={t("dashboard.windowLabel", "时间窗口")}
          options={[...WINDOW_OPTIONS]}
          value={days}
          onChange={(nextDays) => {
            setDays(nextDays);
            setPage(1);
          }}
          renderLabel={(option) => t(`dashboard.window${option}`, `${option}天`)}
        />

        <AdminSelect
          aria-label={t("feedback.chatVotes.voteFilterLabel", "评价筛选")}
          value={voteFilter}
          onChange={(event) => {
            setVoteFilter(event.target.value as ChatVoteFilter);
            setPage(1);
          }}
          wrapperClassName="w-40"
          className="h-11"
        >
          <option value="all">{t("feedback.chatVotes.filterAll", "全部评价")}</option>
          <option value="up">{t("feedback.chatVotes.filterUp", "仅点赞")}</option>
          <option value="down">{t("feedback.chatVotes.filterDown", "仅点踩")}</option>
        </AdminSelect>
      </div>

      {summary && (
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <StatsCard
            icon={<MessageSquare className="h-5 w-5" />}
            title={t("feedback.chatVotes.totalVotes", "评价总数")}
            value={summary.total_votes}
          />
          <StatsCard
            icon={<Percent className="h-5 w-5" />}
            title={t("feedback.chatVotes.upRate", "好评率")}
            value={formatPercent(summary.up_rate)}
          />
          <StatsCard
            icon={<ThumbsUp className="h-5 w-5" />}
            title={t("feedback.chatVotes.upCount", "点赞")}
            value={summary.up}
          />
          <StatsCard
            icon={<ThumbsDown className="h-5 w-5" />}
            title={t("feedback.chatVotes.downCount", "点踩")}
            value={summary.down}
          />
        </div>
      )}

      {summary && summary.preset_counts.length > 0 && (
        <div className="admin-surface p-4">
          <h2 className="mb-2 text-sm font-semibold text-[hsl(var(--text-primary))]">
            {t("feedback.chatVotes.topPresets", "高频点踩原因")}
          </h2>
          <ul
            className="flex flex-wrap gap-2"
            aria-label={t("feedback.chatVotes.topPresets", "高频点踩原因")}
          >
            {summary.preset_counts.map((chip) => (
              <li
                key={chip.preset}
                className="inline-flex items-center gap-1.5 rounded-full border border-[hsl(var(--separator-color))] bg-[hsl(var(--bg-tertiary)/0.6)] px-3 py-1 text-xs text-[hsl(var(--text-primary))]"
              >
                <span>{presetLabel(chip.preset)}</span>
                <span className="font-semibold tabular-nums text-[hsl(var(--text-secondary))]">
                  {chip.count}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <AdminPageState
        isLoading={isLoading}
        isFetching={isFetching}
        isError={isError}
        isEmpty={!items.length}
        loadingText={t("common:loading")}
        errorText={queryErrorText}
        emptyText={t("feedback.chatVotes.empty", "本期暂无对话评价记录")}
        retryText={t("common:retry")}
        onRetry={() => {
          void refetch();
        }}
      >
        {isCompact ? (
          <div className="space-y-3">
            {items.map((item) => (
              <div
                key={item.message_id}
                className="rounded-xl border border-[hsl(var(--separator-color))] bg-[hsl(var(--bg-secondary))] p-4 shadow-sm"
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium text-[hsl(var(--text-primary))]">
                      <AdminUserLink userId={item.user_id}>{item.username}</AdminUserLink>
                    </div>
                    <div className="mt-1 text-xs text-[hsl(var(--text-secondary))]">
                      {formatAdminDateTime(item.created_at)}
                    </div>
                  </div>
                  <span
                    className={`inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full px-2.5 py-1 text-xs font-medium ${
                      item.vote === "up"
                        ? "bg-[hsl(var(--success)/0.12)] text-[hsl(var(--success-text))]"
                        : "bg-[hsl(var(--error)/0.12)] text-[hsl(var(--error-text))]"
                    }`}
                  >
                    {item.vote === "up"
                      ? t("feedback.chatVotes.voteUp", "点赞")
                      : t("feedback.chatVotes.voteDown", "点踩")}
                  </span>
                </div>

                {item.preset && (
                  <div className="mt-2 text-xs text-[hsl(var(--text-secondary))]">
                    {t("feedback.chatVotes.presetPrefix", "原因：")}
                    {presetLabel(item.preset)}
                  </div>
                )}

                {item.comment && (
                  <p className="mt-2 break-words text-sm text-[hsl(var(--text-primary))]">
                    {item.comment}
                  </p>
                )}
              </div>
            ))}
          </div>
        ) : (
          <div
            className="admin-table-shell overflow-x-auto"
            tabIndex={0}
            role="region"
            aria-label={t("feedback.chatVotes.tableLabel", "对话评价列表")}
          >
            <table className="w-full min-w-[680px] text-left text-sm">
              <thead className="bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-secondary))]">
                <tr>
                  <th scope="col" className="whitespace-nowrap px-4 py-3 font-medium">
                    {t("feedback.columns.user", "用户")}
                  </th>
                  <th scope="col" className="whitespace-nowrap px-4 py-3 font-medium">
                    {t("feedback.chatVotes.columns.vote", "评价")}
                  </th>
                  <th scope="col" className="whitespace-nowrap px-4 py-3 font-medium">
                    {t("feedback.chatVotes.columns.preset", "原因标签")}
                  </th>
                  <th scope="col" className="whitespace-nowrap px-4 py-3 font-medium">
                    {t("feedback.chatVotes.columns.comment", "补充说明")}
                  </th>
                  <th scope="col" className="whitespace-nowrap px-4 py-3 font-medium">
                    {t("feedback.columns.createdAt", "提交时间")}
                  </th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr
                    key={item.message_id}
                    className="border-t border-[hsl(var(--separator-color))] align-top"
                  >
                    <td className="px-4 py-3 font-medium text-[hsl(var(--text-primary))]">
                      <AdminUserLink userId={item.user_id}>{item.username}</AdminUserLink>
                    </td>
                    <td className="px-4 py-3">
                      <span
                        className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-medium ${
                          item.vote === "up"
                            ? "bg-[hsl(var(--success)/0.12)] text-[hsl(var(--success-text))]"
                            : "bg-[hsl(var(--error)/0.12)] text-[hsl(var(--error-text))]"
                        }`}
                      >
                        {item.vote === "up"
                          ? t("feedback.chatVotes.voteUp", "点赞")
                          : t("feedback.chatVotes.voteDown", "点踩")}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-[hsl(var(--text-secondary))]">
                      {item.preset ? presetLabel(item.preset) : "—"}
                    </td>
                    <td className="max-w-[420px] break-words px-4 py-3 text-[hsl(var(--text-primary))]">
                      {item.comment || "—"}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-xs text-[hsl(var(--text-secondary))]">
                      {formatAdminDateTime(item.created_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </AdminPageState>

      <div className="flex items-center justify-between text-sm">
        <div className="text-[hsl(var(--text-secondary))]">
          {t("common:showing", {
            from: total === 0 ? 0 : (page - 1) * PAGE_SIZE + 1,
            to: Math.min(page * PAGE_SIZE, total),
            total,
          })}
        </div>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="ghost"
            size="touch"
            disabled={page <= 1}
            onClick={() => setPage((prev) => Math.max(1, prev - 1))}
          >
            {t("common:previous", "上一页")}
          </Button>
          <span className="text-xs text-[hsl(var(--text-secondary))]">
            {t("feedback.pageInfo", "第 {{page}} / {{total}} 页", {
              page,
              total: totalPages,
            })}
          </span>
          <Button
            type="button"
            variant="ghost"
            size="touch"
            disabled={page >= totalPages}
            onClick={() => setPage((prev) => Math.min(totalPages, prev + 1))}
          >
            {t("common:next", "下一页")}
          </Button>
        </div>
      </div>
    </div>
  );
}
