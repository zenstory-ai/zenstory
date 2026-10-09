import { useTranslation } from "react-i18next";
import type { ComponentType } from "react";
import { InspirationGrid } from "../components/inspirations";
import { DashboardPageHeader } from "../components/dashboard/DashboardPageHeader";
import { useMyInspirationSubmissions } from "../hooks/useInspirations";
import { getLocaleCode } from "../lib/i18n-helpers";
import { CheckCircle, Clock, Copy, X } from "../components/icons";

export default function InspirationsPage() {
  const { t } = useTranslation("inspirations");
  const { items, total, isLoading, isFetching, error, refetch } = useMyInspirationSubmissions({ pageSize: 5 });
  const isSubmissionsLoading = isLoading || (isFetching && items.length === 0);

  const statusClassMap: Record<string, string> = {
    pending:
      "border border-yellow-500/30 bg-yellow-500/10 text-yellow-500 dark:text-yellow-400",
    approved:
      "border border-[hsl(var(--success)/0.35)] bg-[hsl(var(--success)/0.15)] text-[hsl(var(--success))]",
    rejected: "border border-red-500/30 bg-red-500/10 text-red-500 dark:text-red-400",
  };

  const statusIconMap: Record<string, ComponentType<{ className?: string }>> = {
    pending: Clock,
    approved: CheckCircle,
    rejected: X,
  };

  return (
    <div className="flex flex-col space-y-8">
      {/* Header */}
      <DashboardPageHeader
        title={t("title")}
        subtitle={t("subtitle")}
      />

      {/* My submissions */}
      <div className="card rounded-2xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] p-5 md:p-6 shadow-[0_12px_40px_hsl(0_0%_0%_/_0.25)]">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold text-[hsl(var(--text-primary))]">
            {t("mySubmissions.title")}
          </h2>
          <span className="rounded-full border border-[hsl(var(--accent-primary)/0.35)] bg-[hsl(var(--accent-primary)/0.12)] px-2.5 py-1 text-xs font-medium text-[hsl(var(--accent-primary))]">
            {t("mySubmissions.count", { count: total })}
          </span>
        </div>

        {isSubmissionsLoading ? (
          <p className="text-sm text-[hsl(var(--text-secondary))]">{t("mySubmissions.loading")}</p>
        ) : error ? (
          <div className="rounded-xl border border-[hsl(var(--error)/0.3)] bg-[hsl(var(--error)/0.08)] px-4 py-4 text-sm text-[hsl(var(--text-secondary))]">
            <p>{t("mySubmissions.loadError")}</p>
            <button className="btn-secondary mt-3 h-9 px-3" onClick={() => void refetch()}>
              {t("retry")}
            </button>
          </div>
        ) : items.length === 0 ? (
          <p className="rounded-xl border border-[hsl(var(--separator-color))] bg-[hsl(var(--bg-tertiary)/0.35)] px-4 py-6 text-sm text-[hsl(var(--text-secondary))]">
            {t("mySubmissions.empty")}
          </p>
        ) : (
          <div className="space-y-3">
            {items.map((item) => (
              <div
                key={item.id}
                className="flex flex-col gap-3 rounded-xl border border-[hsl(var(--separator-color))] bg-[hsl(var(--bg-tertiary)/0.45)] p-4 transition-colors hover:border-[hsl(var(--accent-primary)/0.35)] sm:flex-row sm:items-center sm:justify-between"
              >
                <div className="min-w-0 space-y-1">
                  <p className="truncate text-sm font-semibold text-[hsl(var(--text-primary))]">
                    {item.name}
                  </p>
                  <p className="text-xs text-[hsl(var(--text-secondary))]">
                    {t("mySubmissions.submittedAt", {
                      date: new Date(item.created_at).toLocaleDateString(getLocaleCode()),
                    })}
                  </p>
                  {item.status === "rejected" && item.rejection_reason && (
                    <p className="mt-1 line-clamp-2 text-xs text-red-500">
                      {t("mySubmissions.rejectionReason", { reason: item.rejection_reason })}
                    </p>
                  )}
                </div>

                <div className="flex items-center gap-2">
                  <span className="inline-flex items-center gap-1.5 rounded-full border border-[hsl(var(--separator-color))] bg-[hsl(var(--bg-secondary))] px-2.5 py-1 text-xs text-[hsl(var(--text-secondary))]">
                    <Copy className="h-3.5 w-3.5" />
                    {t("copyCount", { count: item.copy_count })}
                  </span>
                  <span
                    className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-semibold tracking-wide ${statusClassMap[item.status] ?? statusClassMap.pending}`}
                  >
                    {(() => {
                      const StatusIcon = statusIconMap[item.status] ?? Clock;
                      return <StatusIcon className="h-3.5 w-3.5" />;
                    })()}
                    {t(`mySubmissions.status.${item.status}`)}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Inspiration Grid */}
      <InspirationGrid pageSize={12} />
    </div>
  );
}
