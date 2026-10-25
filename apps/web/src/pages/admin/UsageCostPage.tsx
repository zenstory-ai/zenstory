import { useState, type FormEvent, type ReactNode } from "react";
import { useIsFetching, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";
import { RefreshCw } from "lucide-react";
import { AdminPageState, AdminSelect } from "../../components/admin";
import { AdminSegmented } from "../../components/admin/AdminSegmented";
import { Modal } from "../../components/ui/Modal";
import {
  adminApi,
  type ModelMetrics,
  type ModelMetricsRow,
  type UsageDistribution,
  type UsageSource,
  type UsageDailyRow,
  type UsageDetailDays,
  type UsageMetrics,
  type UsageSourceRow,
  type UsageUserSort,
  type UsageWindow,
  type UserDailyUsageResponse,
  type UserUsageRow,
} from "../../lib/adminApi";
import { parseUTCDate } from "../../lib/dateUtils";
import { formatCny } from "../../lib/formatCny";
import { getLocaleCode } from "../../lib/i18n-helpers";

const PAGE_SIZE = 20;
const WINDOWS: UsageWindow[] = ["today", "yesterday", "7d"];
const DETAIL_DAYS: UsageDetailDays[] = [7, 14, 30];
const SORTS: UsageUserSort[] = ["cost", "calls", "tokens"];
const SOURCES: UsageSource[] = ["agent", "router", "suggest", "polish", "material"];
const METRIC_DEFINITIONS = ["attempts", "successRate", "latency", "ttft", "decode", "notRecorded"] as const;
// The model table has nine columns: tighter cells and wrapping headers keep it within a desktop page.
const metricCellClass = "px-3 py-3 text-left text-sm";
const metricNumberCellClass = "px-3 py-3 text-right text-sm tabular-nums";
const metricHeadClass = "px-3 py-3 text-left text-sm font-semibold";
const metricNumberHeadClass = "px-3 py-3 text-right text-sm font-semibold";

const totalTokens = (row: UsageMetrics) => row.cache_hit_tokens + row.cache_miss_tokens + row.output_tokens;

const cellClass = "px-4 py-3 text-left text-sm";
const numberCellClass = "px-4 py-3 text-right text-sm tabular-nums";
const headClass = "whitespace-nowrap px-4 py-3 text-left text-sm font-semibold";
const numberHeadClass = "whitespace-nowrap px-4 py-3 text-right text-sm font-semibold";

function Kpi({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="admin-surface min-w-0 px-4 py-3">
      <div className="text-xs text-[hsl(var(--text-secondary))]">{label}</div>
      <div className="mt-1 break-words text-lg font-semibold tabular-nums text-[hsl(var(--text-primary))] sm:text-xl">
        {children}
      </div>
    </div>
  );
}

function PriceLine() {
  const { t } = useTranslation("admin");
  return (
    <p className="text-xs text-[hsl(var(--text-secondary))]">
      {t("usage.priceLine")}
    </p>
  );
}

function SourceTable({ rows }: { rows: UsageSourceRow[] }) {
  const { t } = useTranslation("admin");
  const locale = getLocaleCode();
  if (rows.length === 0) {
    return <p className="px-4 py-6 text-sm text-[hsl(var(--text-secondary))]">{t("usage.noUsage")}</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[hsl(var(--text-primary))]">
        <thead>
          <tr className="border-b border-[hsl(var(--separator-color))]">
            <th className={headClass}>{t("usage.source")}</th>
            <th className={numberHeadClass}>{t("usage.calls")}</th>
            <th className={numberHeadClass}>{t("usage.tokens")}</th>
            <th className={numberHeadClass}>{t("usage.cost")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.source} className="border-b border-[hsl(var(--separator-color))] last:border-0">
              <td className={cellClass}>{t(`usage.sources.${row.source}`, row.source)}</td>
              <td className={numberCellClass}>{row.calls.toLocaleString(locale)}</td>
              <td className={numberCellClass}>{totalTokens(row).toLocaleString(locale)}</td>
              <td className={numberCellClass}>{formatCny(row.cost_cny, locale)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DailyTable({ rows, showUsers }: { rows: UsageDailyRow[]; showUsers?: boolean }) {
  const { t } = useTranslation("admin");
  const locale = getLocaleCode();
  const maxCost = Math.max(0, ...rows.map((row) => Number(row.cost_cny) || 0));
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[520px] text-[hsl(var(--text-primary))]">
        <thead>
          <tr className="border-b border-[hsl(var(--separator-color))]">
            <th className={headClass}>{t("usage.date")}</th>
            {showUsers && <th className={numberHeadClass}>{t("usage.users")}</th>}
            <th className={numberHeadClass}>{t("usage.calls")}</th>
            <th className={numberHeadClass}>{t("usage.tokens")}</th>
            <th className={`${headClass} w-2/5`}>{t("usage.cost")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const cost = Number(row.cost_cny) || 0;
            const width = maxCost > 0 ? Math.max(2, Math.round((cost / maxCost) * 100)) : 0;
            return (
              <tr key={row.date} className="border-b border-[hsl(var(--separator-color))] last:border-0">
                <td className={`${cellClass} whitespace-nowrap tabular-nums`}>{row.date}</td>
                {showUsers && <td className={numberCellClass}>{(row.users ?? 0).toLocaleString(locale)}</td>}
                <td className={numberCellClass}>{row.calls.toLocaleString(locale)}</td>
                <td className={numberCellClass}>{totalTokens(row).toLocaleString(locale)}</td>
                <td className={cellClass}>
                  <div className="flex items-center gap-2">
                    <div className="h-2 flex-1 rounded-full bg-[hsl(var(--bg-tertiary))]">
                      {width > 0 && (
                        <div
                          className="h-2 rounded-full bg-[hsl(var(--accent-primary))]"
                          style={{ width: `${width}%` }}
                        />
                      )}
                    </div>
                    <span className="w-24 shrink-0 text-right tabular-nums">{formatCny(row.cost_cny, locale)}</span>
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** "850 ms" under a second, "12.3 s" above; N/A without samples (never 0). */
function formatMs(value: number | null, locale: string, na: string): string {
  if (value === null) return na;
  if (value < 1000) return `${value.toLocaleString(locale)} ms`;
  return `${(value / 1000).toLocaleString(locale, { maximumFractionDigits: 1 })} s`;
}

function DistributionCell({ value }: { value: UsageDistribution }) {
  const { t } = useTranslation("admin");
  const locale = getLocaleCode();
  const na = t("usage.models.na");
  return (
    <td className={metricNumberCellClass}>
      <div className="whitespace-nowrap">{formatMs(value.p50, locale, na)} / {formatMs(value.p95, locale, na)}</div>
      <div className="text-xs text-[hsl(var(--text-secondary))]">{t("usage.models.samples", { count: value.samples })}</div>
    </td>
  );
}

function ModelMetricsTableRow({ label, row, emphasis }: { label: string; row: ModelMetrics; emphasis?: boolean }) {
  const { t } = useTranslation("admin");
  const locale = getLocaleCode();
  const na = t("usage.models.na");
  const finished = row.success + row.error + row.timeout;
  const rate = row.success_rate === null
    ? na
    : row.success_rate.toLocaleString(locale, { style: "percent", maximumFractionDigits: 1 });
  return (
    <tr className={`border-b border-[hsl(var(--separator-color))] last:border-0 ${emphasis ? "font-semibold" : ""}`}>
      <td className={`${metricCellClass} whitespace-nowrap`}>{label}</td>
      <td className={metricNumberCellClass}>{row.attempts.toLocaleString(locale)}</td>
      <td className={metricNumberCellClass}>
        <div>{rate}</div>
        <div className="text-xs font-normal text-[hsl(var(--text-secondary))]">
          {t("usage.models.rateDetail", { success: row.success.toLocaleString(locale), finished: finished.toLocaleString(locale) })}
        </div>
      </td>
      <td className={`${metricNumberCellClass} whitespace-nowrap`}>
        {[row.error, row.timeout, row.cancelled, row.not_recorded].map((count) => count.toLocaleString(locale)).join(" / ")}
      </td>
      <DistributionCell value={row.latency_ms} />
      <DistributionCell value={row.ttft_ms} />
      <td className={metricNumberCellClass}>
        <div className="whitespace-nowrap">
          {row.decode_tps.value === null
            ? na
            : t("usage.models.tokensPerSecond", { value: row.decode_tps.value.toLocaleString(locale, { maximumFractionDigits: 1 }) })}
        </div>
        <div className="text-xs font-normal text-[hsl(var(--text-secondary))]">{t("usage.models.samples", { count: row.decode_tps.samples })}</div>
      </td>
      <td className={`${metricNumberCellClass} whitespace-nowrap`}>
        {(row.cache_hit_tokens + row.cache_miss_tokens).toLocaleString(locale)} / {row.output_tokens.toLocaleString(locale)}
      </td>
      <td className={metricNumberCellClass}>{formatCny(row.cost_cny, locale)}</td>
    </tr>
  );
}

function ModelMetricsSection({ usageWindow }: { usageWindow: UsageWindow }) {
  const { t } = useTranslation("admin");
  const [source, setSource] = useState<UsageSource | "">("");
  const models = useQuery({
    queryKey: ["admin", "usage", "models", usageWindow, source],
    queryFn: () => adminApi.getUsageByModel(usageWindow, source || undefined),
  });
  const rows: ModelMetricsRow[] = models.data?.items ?? [];
  return (
    <section className="space-y-3">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="text-base font-semibold text-[hsl(var(--text-primary))]">{t("usage.models.title")}</h2>
          <p className="text-xs text-[hsl(var(--text-secondary))]">{t("usage.models.subtitle")}</p>
        </div>
        <AdminSelect
          wrapperClassName="w-full sm:w-auto"
          className="w-full"
          aria-label={t("usage.models.sourceFilter")}
          value={source}
          onChange={(event) => setSource(event.target.value as UsageSource | "")}
        >
          <option value="">{t("usage.models.allSources")}</option>
          {SOURCES.map((value) => (
            <option key={value} value={value}>{t(`usage.sources.${value}`)}</option>
          ))}
        </AdminSelect>
      </div>
      <AdminPageState
        isLoading={models.isLoading}
        isFetching={models.isFetching}
        isError={models.isError}
        isEmpty={rows.length === 0}
        errorText={t("usage.loadError")}
        emptyText={t("usage.models.empty")}
        onRetry={() => void models.refetch()}
      >
        {models.data && (
          <div className="admin-table-shell overflow-x-auto">
            <table className="w-full min-w-[880px] text-[hsl(var(--text-primary))]">
              <thead>
                <tr className="border-b border-[hsl(var(--separator-color))]">
                  <th className={metricHeadClass}>{t("usage.models.model")}</th>
                  <th className={metricNumberHeadClass}>{t("usage.models.attempts")}</th>
                  <th className={metricNumberHeadClass}>{t("usage.models.successRate")}</th>
                  <th className={metricNumberHeadClass}>{t("usage.models.outcomes")}</th>
                  <th className={metricNumberHeadClass}>{t("usage.models.latency")}</th>
                  <th className={metricNumberHeadClass}>{t("usage.models.ttft")}</th>
                  <th className={metricNumberHeadClass}>{t("usage.models.decode")}</th>
                  <th className={metricNumberHeadClass}>{t("usage.models.tokens")}</th>
                  <th className={metricNumberHeadClass}>{t("usage.cost")}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => <ModelMetricsTableRow key={row.model} label={row.model} row={row} />)}
                {rows.length > 1 && <ModelMetricsTableRow label={t("usage.models.allModels")} row={models.data.totals} emphasis />}
              </tbody>
            </table>
          </div>
        )}
      </AdminPageState>
      <details className="admin-surface px-4 py-3 text-xs text-[hsl(var(--text-secondary))]">
        <summary className="cursor-pointer text-sm font-medium text-[hsl(var(--text-primary))]">{t("usage.models.definitionsTitle")}</summary>
        <ul className="mt-2 list-disc space-y-1 pl-4">
          {METRIC_DEFINITIONS.map((key) => <li key={key}>{t(`usage.models.definitions.${key}`)}</li>)}
        </ul>
      </details>
    </section>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="admin-table-shell">
      <h2 className="border-b border-[hsl(var(--separator-color))] px-4 py-3 text-sm font-semibold text-[hsl(var(--text-primary))]">
        {title}
      </h2>
      {children}
    </section>
  );
}

function UserUsageDetail({
  data, days, onDaysChange, isLoading, isError, onRetry,
}: {
  data: UserDailyUsageResponse | undefined;
  days: UsageDetailDays;
  onDaysChange: (days: UsageDetailDays) => void;
  isLoading: boolean;
  isError: boolean;
  onRetry: () => void;
}) {
  const { t } = useTranslation(["admin", "common"]);
  const locale = getLocaleCode();
  return (
    <div className="space-y-4">
      <AdminSegmented
        label={t("usage.detailRange")}
        options={DETAIL_DAYS}
        value={days}
        onChange={onDaysChange}
        renderLabel={(value) => t("usage.lastDays", { count: value })}
      />
      <AdminPageState isLoading={isLoading} isError={isError} errorText={t("usage.loadError")} onRetry={onRetry}>
        {data && (
          <div className="space-y-4">
            <p className="text-sm text-[hsl(var(--text-secondary))]">{data.email}</p>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <Kpi label={t("usage.totalCost")}>{formatCny(data.totals.cost_cny, locale)}</Kpi>
              <Kpi label={t("usage.peakOffpeak")}>
                {formatCny(data.totals.peak_cost_cny, locale)} / {formatCny(data.totals.offpeak_cost_cny, locale)}
              </Kpi>
              <Kpi label={t("usage.calls")}>{data.totals.calls.toLocaleString(locale)}</Kpi>
              <Kpi label={t("usage.tokens")}>{totalTokens(data.totals).toLocaleString(locale)}</Kpi>
            </div>
            <Section title={t("usage.byDay")}>
              <DailyTable rows={data.daily} />
            </Section>
            <Section title={t("usage.bySource")}>
              <SourceTable rows={data.by_source} />
            </Section>
          </div>
        )}
      </AdminPageState>
    </div>
  );
}

function UserCard({ row, onOpen }: { row: UserUsageRow; onOpen: (row: UserUsageRow) => void }) {
  const { t } = useTranslation("admin");
  const locale = getLocaleCode();
  return (
    <button
      type="button"
      onClick={() => onOpen(row)}
      className="admin-surface w-full px-4 py-3 text-left"
      aria-label={t("usage.openUser", { name: row.username })}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate font-medium text-[hsl(var(--text-primary))]">{row.username}</div>
          <div className="truncate text-xs text-[hsl(var(--text-secondary))]">{row.email}</div>
        </div>
        <div className="shrink-0 text-right font-semibold tabular-nums text-[hsl(var(--text-primary))]">
          {formatCny(row.cost_cny, locale)}
        </div>
      </div>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-[hsl(var(--text-secondary))]">
        <span>{t("usage.calls")} {row.calls.toLocaleString(locale)}</span>
        <span>{t("usage.tokens")} {totalTokens(row).toLocaleString(locale)}</span>
        <span>{t("usage.peak")} {formatCny(row.peak_cost_cny, locale)}</span>
        <span>{t("usage.offpeak")} {formatCny(row.offpeak_cost_cny, locale)}</span>
      </div>
    </button>
  );
}

export default function UsageCostPage() {
  const { t } = useTranslation(["admin", "common"]);
  const queryClient = useQueryClient();
  const modelsFetching = useIsFetching({ queryKey: ["admin", "usage", "models"] }) > 0;
  const [searchParams, setSearchParams] = useSearchParams();
  const [usageWindow, setUsageWindow] = useState<UsageWindow>("today");
  const [sort, setSort] = useState<UsageUserSort>("cost");
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const locale = getLocaleCode();
  const selectedUserId = searchParams.get("user");
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [detailDays, setDetailDays] = useState<UsageDetailDays>(7);

  const summary = useQuery({
    queryKey: ["admin", "usage", "summary", usageWindow],
    queryFn: () => adminApi.getUsageSummary(usageWindow),
  });
  const users = useQuery({
    queryKey: ["admin", "usage", "users", usageWindow, sort, search, page],
    queryFn: () => adminApi.getUsageByUser({
      window: usageWindow, sort, search: search || undefined, page, page_size: PAGE_SIZE,
    }),
  });
  const detail = useQuery({
    queryKey: ["admin", "usage", "user-daily", selectedUserId, detailDays],
    queryFn: () => adminApi.getUserDailyUsage(selectedUserId ?? "", detailDays),
    enabled: selectedUserId !== null,
  });

  const openUser = (row: UserUsageRow) => {
    setSelectedName(row.username);
    setDetailDays(7);
    const next = new URLSearchParams(searchParams);
    next.set("user", row.user_id);
    setSearchParams(next);
  };
  const closeUser = () => {
    setSelectedName(null);
    const next = new URLSearchParams(searchParams);
    next.delete("user");
    setSearchParams(next);
  };
  const changeWindow = (value: UsageWindow) => {
    setUsageWindow(value);
    setPage(1);
  };
  const submitSearch = (event: FormEvent) => {
    event.preventDefault();
    setSearch(searchInput.trim());
    setPage(1);
  };

  const totals = summary.data?.totals;
  const rows = users.data?.items ?? [];
  const totalUsers = users.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(totalUsers / PAGE_SIZE));
  const formatDate = (value: string | null) => (value ? parseUTCDate(value).toLocaleString(locale) : "-");

  return (
    <div className="admin-page">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="admin-page-title">{t("usage.title")}</h1>
          <p className="admin-page-subtitle">{t("usage.subtitle")}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <AdminSegmented
            label={t("usage.window")}
            options={WINDOWS}
            value={usageWindow}
            onChange={changeWindow}
            renderLabel={(value) => t(`usage.windows.${value}`)}
          />
          <button
            type="button"
            className="btn-secondary flex items-center gap-2"
            disabled={summary.isFetching || users.isFetching || modelsFetching}
            onClick={() => {
              void summary.refetch();
              void users.refetch();
              void queryClient.invalidateQueries({ queryKey: ["admin", "usage", "models"] });
            }}
          >
            <RefreshCw className="h-4 w-4" />
            {t("usage.refresh")}
          </button>
        </div>
      </div>

      <AdminPageState
        isLoading={summary.isLoading}
        isError={summary.isError}
        errorText={t("usage.loadError")}
        onRetry={() => void summary.refetch()}
      >
        {summary.data && totals && (
          <div className="space-y-4">
            <div className="space-y-1">
              <PriceLine />
              <p className="text-xs text-[hsl(var(--text-secondary))]">{t("usage.notCounted")}</p>
            </div>
            <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
              <Kpi label={t("usage.totalCost")}>{formatCny(totals.cost_cny, locale)}</Kpi>
              <Kpi label={t("usage.peakOffpeak")}>
                {formatCny(totals.peak_cost_cny, locale)} / {formatCny(totals.offpeak_cost_cny, locale)}
              </Kpi>
              <Kpi label={t("usage.calls")}>{totals.calls.toLocaleString(locale)}</Kpi>
              <Kpi label={t("usage.activeUsers")}>{totals.users.toLocaleString(locale)}</Kpi>
              <Kpi label={t("usage.tokenSplit")}>
                <span className="text-sm sm:text-base">
                  {totals.cache_hit_tokens.toLocaleString(locale)} / {totals.cache_miss_tokens.toLocaleString(locale)} / {totals.output_tokens.toLocaleString(locale)}
                </span>
              </Kpi>
            </div>
            {usageWindow === "7d" && (
              <Section title={t("usage.byDay")}>
                <DailyTable rows={summary.data.daily} showUsers />
              </Section>
            )}
            <Section title={t("usage.bySource")}>
              <SourceTable rows={summary.data.by_source} />
            </Section>
          </div>
        )}
      </AdminPageState>

      <ModelMetricsSection usageWindow={usageWindow} />

      <section className="space-y-3">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <h2 className="text-base font-semibold text-[hsl(var(--text-primary))]">{t("usage.byUser")}</h2>
          <div className="flex flex-col gap-3 sm:flex-row">
            <AdminSelect
              wrapperClassName="w-full sm:w-auto"
              className="w-full"
              aria-label={t("usage.sortBy")}
              value={sort}
              onChange={(event) => { setSort(event.target.value as UsageUserSort); setPage(1); }}
            >
              {SORTS.map((value) => (
                <option key={value} value={value}>{t(`usage.sort.${value}`)}</option>
              ))}
            </AdminSelect>
            <form className="flex gap-2" onSubmit={submitSearch}>
              <input
                className="min-w-0 flex-1 rounded-lg border border-[hsl(var(--separator-color))] bg-[hsl(var(--bg-secondary))] px-3 py-2 text-sm text-[hsl(var(--text-primary))]"
                aria-label={t("usage.search")}
                placeholder={t("usage.searchPlaceholder")}
                value={searchInput}
                onChange={(event) => setSearchInput(event.target.value)}
              />
              <button type="submit" className="btn-secondary">{t("usage.search")}</button>
            </form>
          </div>
        </div>

        <AdminPageState
          isLoading={users.isLoading}
          isFetching={users.isFetching}
          isError={users.isError}
          isEmpty={rows.length === 0}
          errorText={t("usage.loadError")}
          emptyText={t("usage.noUsers")}
          onRetry={() => void users.refetch()}
        >
          <>
            <div className="space-y-3 lg:hidden">
              {rows.map((row) => <UserCard key={row.user_id} row={row} onOpen={openUser} />)}
            </div>
            <div className="hidden lg:block admin-table-shell overflow-x-auto">
              <table className="w-full text-[hsl(var(--text-primary))]">
                <thead>
                  <tr className="border-b border-[hsl(var(--separator-color))]">
                    <th className={headClass}>{t("usage.user")}</th>
                    <th className={numberHeadClass}>{t("usage.calls")}</th>
                    <th className={numberHeadClass}>{t("usage.cacheHit")}</th>
                    <th className={numberHeadClass}>{t("usage.cacheMiss")}</th>
                    <th className={numberHeadClass}>{t("usage.output")}</th>
                    <th className={numberHeadClass}>{t("usage.peak")}</th>
                    <th className={numberHeadClass}>{t("usage.offpeak")}</th>
                    <th className={numberHeadClass}>{t("usage.cost")}</th>
                    <th className={headClass}>{t("usage.lastUsed")}</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.user_id} className="border-b border-[hsl(var(--separator-color))] last:border-0">
                      <td className={cellClass}>
                        <button
                          type="button"
                          className="text-left text-[var(--admin-accent-text)] hover:underline"
                          onClick={() => openUser(row)}
                        >
                          {row.username}
                        </button>
                        <div className="text-xs text-[hsl(var(--text-secondary))]">{row.email}</div>
                      </td>
                      <td className={numberCellClass}>{row.calls.toLocaleString(locale)}</td>
                      <td className={numberCellClass}>{row.cache_hit_tokens.toLocaleString(locale)}</td>
                      <td className={numberCellClass}>{row.cache_miss_tokens.toLocaleString(locale)}</td>
                      <td className={numberCellClass}>{row.output_tokens.toLocaleString(locale)}</td>
                      <td className={numberCellClass}>{formatCny(row.peak_cost_cny, locale)}</td>
                      <td className={numberCellClass}>{formatCny(row.offpeak_cost_cny, locale)}</td>
                      <td className={`${numberCellClass} font-semibold`}>{formatCny(row.cost_cny, locale)}</td>
                      <td className={`${cellClass} whitespace-nowrap`}>{formatDate(row.last_used_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        </AdminPageState>

        {!users.isError && totalUsers > 0 && (
          <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-[hsl(var(--text-secondary))]">
            <span>{t("usage.totalUsers", { count: totalUsers })}</span>
            <div className="flex items-center gap-3">
              <button type="button" className="btn-secondary" disabled={page <= 1 || users.isFetching} onClick={() => setPage(page - 1)}>
                {t("common:previous")}
              </button>
              <span>{page} / {totalPages}</span>
              <button type="button" className="btn-secondary" disabled={page >= totalPages || users.isFetching} onClick={() => setPage(page + 1)}>
                {t("common:next")}
              </button>
            </div>
          </div>
        )}
      </section>

      <Modal
        className="admin-shell"
        open={selectedUserId !== null}
        onClose={closeUser}
        title={detail.data?.username ?? selectedName ?? t("usage.userDetail")}
        size="lg"
      >
        {selectedUserId && (
          <UserUsageDetail
            data={detail.data}
            days={detailDays}
            onDaysChange={setDetailDays}
            isLoading={detail.isLoading}
            isError={detail.isError}
            onRetry={() => void detail.refetch()}
          />
        )}
      </Modal>
    </div>
  );
}
