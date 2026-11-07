import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { ChevronLeft, ChevronRight, RefreshCw } from "lucide-react";
import { PublicHeader } from "../components/PublicHeader";
import { Button } from "../components/ui/Button";
import {
  fetchServiceStatus,
  formatSuccessRate,
  isStatusSnapshotExpired,
  type ComponentStatusObservation,
  type DailyStatusObservation,
  type ObservationStatus,
  type ServiceStatusResponse,
  type StatusComponentId,
} from "../lib/statusApi";

const POLL_INTERVAL_MS = 60_000;
const STATUS_REQUEST_TIMEOUT_MS = 10_000;
const COMPONENT_ORDER: readonly StatusComponentId[] = ["writing", "polish"];

type DisplayTone = ObservationStatus | "stale" | "unavailable";

function formatBeijingTimestamp(isoString: string | null | undefined, locale: string): string | null {
  if (!isoString) return null;
  const parsed = new Date(isoString);
  if (!Number.isFinite(parsed.getTime())) return null;
  try {
    return new Intl.DateTimeFormat(locale.startsWith("en") ? "en-US" : "zh-CN", {
      timeZone: "Asia/Shanghai",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(parsed);
  } catch {
    return parsed.toISOString().slice(5, 16).replace("T", " ");
  }
}

function getToneClasses(tone: DisplayTone): {
  dot: string;
  banner: string;
} {
  switch (tone) {
    case "operational":
      return {
        dot: "bg-[hsl(var(--success))]",
        banner:
          "border-[hsl(var(--success)/0.3)] bg-[hsl(var(--success)/0.08)] text-[hsl(var(--text-primary))]",
      };
    case "degraded":
      return {
        dot: "bg-[hsl(var(--warning))]",
        banner:
          "border-[hsl(var(--warning)/0.35)] bg-[hsl(var(--warning)/0.1)] text-[hsl(var(--text-primary))]",
      };
    case "unavailable":
      return {
        dot: "bg-[hsl(var(--error))]",
        banner:
          "border-[hsl(var(--error)/0.35)] bg-[hsl(var(--error)/0.08)] text-[hsl(var(--text-primary))]",
      };
    case "stale":
    case "unknown":
    default:
      return {
        dot: "bg-[hsl(var(--text-secondary))]",
        banner:
          "border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] text-[hsl(var(--text-primary))]",
      };
  }
}

function getDayBarClasses(status: ObservationStatus, isSelected: boolean): string {
  const colorClass =
    status === "operational"
      ? "bg-[hsl(var(--success))]"
      : status === "degraded"
        ? "bg-[hsl(var(--warning))]"
        : "bg-[hsl(var(--bg-tertiary))] border border-[hsl(var(--border-color))]";
  const selectionClass = isSelected
    ? "h-8 ring-2 ring-[hsl(var(--text-primary))] ring-offset-1 ring-offset-[hsl(var(--bg-secondary))]"
    : "h-6 opacity-80";
  return `w-full rounded-[2px] transition-all ${colorClass} ${selectionClass}`;
}

export function StatusPage() {
  const { t, i18n } = useTranslation("common");
  const [snapshot, setSnapshot] = useState<ServiceStatusResponse | null>(null);
  const [initialLoading, setInitialLoading] = useState<boolean>(true);
  const [refreshing, setRefreshing] = useState<boolean>(false);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [nowMs, setNowMs] = useState<number>(() => Date.now());
  const [selectedDates, setSelectedDates] = useState<Partial<Record<StatusComponentId, string>>>({});
  const abortControllerRef = useRef<AbortController | null>(null);
  const requestTimeoutRef = useRef<number | null>(null);
  const mountedRef = useRef<boolean>(true);

  const loadStatus = useCallback(async (isInitial = false) => {
    if (requestTimeoutRef.current !== null) {
      window.clearTimeout(requestTimeoutRef.current);
      requestTimeoutRef.current = null;
    }
    abortControllerRef.current?.abort();
    const controller = new AbortController();
    abortControllerRef.current = controller;
    let timedOut = false;

    const requestTimer = window.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, STATUS_REQUEST_TIMEOUT_MS);
    requestTimeoutRef.current = requestTimer;

    if (isInitial) {
      setInitialLoading(true);
    } else {
      setRefreshing(true);
    }

    try {
      const data = await fetchServiceStatus(controller.signal);
      if (!mountedRef.current || controller.signal.aborted) return;
      setSnapshot(data);
      setFetchError(null);
      setNowMs(Date.now());
    } catch (error) {
      if (!mountedRef.current || abortControllerRef.current !== controller) return;
      if (controller.signal.aborted && !timedOut) return;
      setFetchError(
        timedOut
          ? "status_request_timeout"
          : error instanceof Error
            ? error.message
            : "status_fetch_failed",
      );
      setNowMs(Date.now());
    } finally {
      window.clearTimeout(requestTimer);
      if (requestTimeoutRef.current === requestTimer) {
        requestTimeoutRef.current = null;
      }
      if (mountedRef.current && abortControllerRef.current === controller && (timedOut || !controller.signal.aborted)) {
        setInitialLoading(false);
        setRefreshing(false);
      }
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    void loadStatus(true);

    const pollTimer = window.setInterval(() => {
      void loadStatus(false);
    }, POLL_INTERVAL_MS);

    const handleVisibilityOrFocus = () => {
      if (typeof document !== "undefined" && document.visibilityState === "hidden") {
        return;
      }
      setNowMs(Date.now());
      void loadStatus(false);
    };

    document.addEventListener("visibilitychange", handleVisibilityOrFocus);
    window.addEventListener("focus", handleVisibilityOrFocus);

    return () => {
      mountedRef.current = false;
      window.clearInterval(pollTimer);
      if (requestTimeoutRef.current !== null) {
        window.clearTimeout(requestTimeoutRef.current);
        requestTimeoutRef.current = null;
      }
      document.removeEventListener("visibilitychange", handleVisibilityOrFocus);
      window.removeEventListener("focus", handleVisibilityOrFocus);
      abortControllerRef.current?.abort();
    };
  }, [loadStatus]);

  useEffect(() => {
    if (!snapshot) return;
    const expiresMs = Date.parse(snapshot.expires_at);
    if (!Number.isFinite(expiresMs)) return;
    const remainingMs = expiresMs - Date.now();
    if (remainingMs <= 0) return;
    const expiryTimer = window.setTimeout(() => {
      if (mountedRef.current) {
        setNowMs(Date.now());
      }
    }, remainingMs + 20);

    return () => {
      window.clearTimeout(expiryTimer);
    };
  }, [snapshot]);

  const isExpired = useMemo(() => {
    if (!snapshot) return false;
    return isStatusSnapshotExpired(snapshot, nowMs);
  }, [snapshot, nowMs]);

  const isStaleOrFailed = Boolean(fetchError) || isExpired;

  // Once expired or failed to refresh, current status is stale regardless of prior operational/degraded state.
  const overallTone: DisplayTone = useMemo(() => {
    if (!snapshot) {
      return fetchError ? "unavailable" : "unknown";
    }
    if (isStaleOrFailed) {
      return "stale";
    }
    return snapshot.status;
  }, [snapshot, fetchError, isStaleOrFailed]);

  const orderedComponents = useMemo<ComponentStatusObservation[]>(() => {
    if (!snapshot) return [];
    const byId = new Map(snapshot.components.map((item) => [item.id, item]));
    return COMPONENT_ORDER.map((id) => byId.get(id)).filter(
      (item): item is ComponentStatusObservation => Boolean(item),
    );
  }, [snapshot]);

  const windowLabel = useMemo(() => {
    const minutes = snapshot?.window_minutes ?? 60;
    if (minutes === 60) {
      return t("statusPage.windowHour", { defaultValue: "最近 1 小时" });
    }
    return t("statusPage.windowMinutes", {
      minutes,
      defaultValue: "最近 {{minutes}} 分钟",
    });
  }, [snapshot?.window_minutes, t]);

  const formattedGeneratedAt = useMemo(
    () => formatBeijingTimestamp(snapshot?.generated_at, i18n.language),
    [snapshot?.generated_at, i18n.language],
  );

  const overallHeadline = useMemo(() => {
    if (!snapshot && fetchError) {
      return t("statusPage.overall.unavailable", { defaultValue: "状态数据获取失败" });
    }
    if (isStaleOrFailed) {
      return fetchError
        ? t("statusPage.overall.unavailable", { defaultValue: "状态数据获取失败" })
        : t("statusPage.overall.stale", { defaultValue: "状态数据已过期" });
    }
    switch (overallTone) {
      case "operational":
        return t("statusPage.overall.operational", { defaultValue: "近期调用正常" });
      case "degraded":
        return t("statusPage.overall.degraded", { defaultValue: "近期有异常调用" });
      default:
        return t("statusPage.overall.unknown", { defaultValue: "暂无近期数据" });
    }
  }, [snapshot, fetchError, isStaleOrFailed, overallTone, t]);

  const statusLabelFor = useCallback(
    (tone: DisplayTone): string => {
      switch (tone) {
        case "operational":
          return t("statusPage.status.operational", { defaultValue: "正常" });
        case "degraded":
          return t("statusPage.status.degraded", { defaultValue: "有异常调用" });
        case "stale":
        case "unavailable":
          return t("statusPage.status.stale", { defaultValue: "待更新" });
        case "unknown":
        default:
          return t("statusPage.status.unknown", { defaultValue: "无数据" });
      }
    },
    [t],
  );

  const handleSelectDate = useCallback((componentId: StatusComponentId, date: string) => {
    setSelectedDates((prev) => ({ ...prev, [componentId]: date }));
  }, []);

  const overallClasses = getToneClasses(overallTone);

  return (
    <div className="public-page min-h-screen bg-[hsl(var(--bg-primary))] text-[hsl(var(--text-primary))]">
      <PublicHeader variant="home" maxWidth="max-w-5xl" />

      <main className="mx-auto max-w-2xl px-4 py-8 sm:px-6 sm:py-12">
        <h1 className="mb-6 text-2xl font-bold tracking-tight sm:text-3xl">
          {t("statusPage.title", { defaultValue: "服务状态" })}
        </h1>

        {initialLoading && !snapshot && !fetchError ? (
          <div
            role="status"
            aria-busy="true"
            aria-label={t("statusPage.loadingAria", { defaultValue: "正在加载服务状态…" })}
            className="space-y-5 animate-pulse motion-reduce:animate-none"
          >
            <div className="h-20 rounded-xl bg-[hsl(var(--bg-secondary))]" />
            <div className="h-36 rounded-xl bg-[hsl(var(--bg-secondary))]" />
          </div>
        ) : (
          <div className="space-y-5">
            <section
              role={fetchError ? "alert" : undefined}
              data-testid="status-overview-banner"
              data-status-tone={overallTone}
              className={`flex items-center justify-between gap-3 rounded-xl border px-4 py-3 ${overallClasses.banner}`}
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span aria-hidden="true" className={`h-2 w-2 shrink-0 rounded-full ${overallClasses.dot}`} />
                  <p className="font-medium">{overallHeadline}</p>
                </div>
                {formattedGeneratedAt && (
                  <p className="mt-1 text-xs tabular-nums text-[hsl(var(--text-secondary))]">
                    {isStaleOrFailed
                      ? t("statusPage.lastUpdatedAt", { time: formattedGeneratedAt, defaultValue: "上次更新 {{time}}" })
                      : t("statusPage.updatedAt", { time: formattedGeneratedAt, defaultValue: "更新于 {{time}}" })}
                  </p>
                )}
              </div>
              <Button
                type="button"
                variant="ghost"
                size="touch"
                onClick={() => void loadStatus(false)}
                disabled={refreshing}
                leftIcon={<RefreshCw aria-hidden="true" className={`h-4 w-4 ${refreshing ? "animate-spin motion-reduce:animate-none" : ""}`} />}
                className="min-h-11 shrink-0 px-3 text-sm"
              >
                {refreshing
                  ? t("statusPage.refreshing", { defaultValue: "刷新中…" })
                  : fetchError
                    ? t("statusPage.retry", { defaultValue: "重试" })
                    : t("statusPage.refresh", { defaultValue: "刷新" })}
              </Button>
            </section>

            {orderedComponents.length > 0 && (
              <>
                <section aria-labelledby="status-current-window">
                  <div className="flex items-center justify-between gap-3 px-1 pb-2 text-xs text-[hsl(var(--text-secondary))]">
                    <span id="status-current-window">{windowLabel}</span>
                    <span>{t("statusPage.metrics.successRate", { defaultValue: "调用成功率" })}</span>
                  </div>
                  <div className="divide-y divide-[hsl(var(--separator-color))] rounded-xl border border-[hsl(var(--border-color))]">
                    {orderedComponents.map((component) => {
                      const tone = isStaleOrFailed ? "stale" : component.status;
                      return (
                        <div
                          key={component.id}
                          data-testid={`status-component-${component.id}`}
                          data-status-tone={tone}
                          className="flex items-center justify-between gap-4 px-4 py-4"
                        >
                          <div className="min-w-0">
                            <h2 className="text-sm font-medium">{t(`statusPage.components.${component.id}.title`)}</h2>
                            <p className="mt-1 flex items-center gap-1.5 text-xs text-[hsl(var(--text-secondary))]">
                              <span aria-hidden="true" className={`h-1.5 w-1.5 rounded-full ${getToneClasses(tone).dot}`} />
                              {statusLabelFor(tone)}
                            </p>
                          </div>
                          <span className="text-sm tabular-nums">
                            {isStaleOrFailed ? "—" : formatSuccessRate(component.success_rate) ?? "—"}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                </section>

                <details className="group border-b border-[hsl(var(--border-color))]">
                  <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-3 rounded text-sm text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] [&::-webkit-details-marker]:hidden">
                    {t("statusPage.history.title", { defaultValue: "近 30 天调用记录" })}
                    <ChevronRight aria-hidden="true" className="h-4 w-4 group-open:rotate-90" />
                  </summary>
                  <div className="space-y-6 pb-4 pt-3">
                    {orderedComponents.map((component) => (
                      <ComponentHistory
                        key={component.id}
                        component={component}
                        selectedDate={selectedDates[component.id]}
                        onSelectDate={(date) => handleSelectDate(component.id, date)}
                        statusLabelFor={statusLabelFor}
                      />
                    ))}
                  </div>
                </details>
              </>
            )}

            <p className="text-xs leading-relaxed text-[hsl(var(--text-secondary))]">
              {t("statusPage.scopeNote", {
                defaultValue: "按模型调用统计，一次回复可能包含多次调用。时间为北京时间（UTC+8）。",
              })}
            </p>
          </div>
        )}
      </main>
    </div>
  );
}

interface ComponentHistoryProps {
  component: ComponentStatusObservation;
  selectedDate: string | undefined;
  onSelectDate: (date: string) => void;
  statusLabelFor: (tone: DisplayTone) => string;
}

function ComponentHistory({
  component,
  selectedDate,
  onSelectDate,
  statusLabelFor,
}: ComponentHistoryProps) {
  const { t } = useTranslation("common");
  const serviceName = t(`statusPage.components.${component.id}.title`);
  const history = component.history;
  const maxIndex = Math.max(0, history.length - 1);

  const selectedIndex = useMemo(() => {
    if (history.length === 0) return 0;
    if (selectedDate) {
      const found = history.findIndex((item) => item.date === selectedDate);
      if (found >= 0) return found;
    }
    return history.length - 1;
  }, [history, selectedDate]);

  const activeDay: DailyStatusObservation | undefined = history[selectedIndex];
  const isTodaySelected = history.length > 0 && selectedIndex === history.length - 1;
  const activeDayRate = formatSuccessRate(activeDay?.success_rate);
  const activeDayStatusLabel = activeDay ? statusLabelFor(activeDay.status) : statusLabelFor("unknown");

  const ariaValueText = useMemo(() => {
    if (!activeDay) return "";
    const parts = [activeDay.date, activeDayStatusLabel];
    if (activeDayRate !== null) {
      parts.push(
        `${t("statusPage.metrics.successRate", { defaultValue: "调用成功率" })} ${activeDayRate}`,
      );
    }
    return parts.join(" · ");
  }, [activeDay, activeDayStatusLabel, activeDayRate, t]);

  const selectByIndex = (nextIndex: number) => {
    if (history.length === 0) return;
    const clamped = Math.max(0, Math.min(maxIndex, nextIndex));
    const target = history[clamped];
    if (target) {
      onSelectDate(target.date);
    }
  };

  const handleRangeKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (history.length === 0) return;
    if (event.key === "ArrowLeft" || event.key === "ArrowDown") {
      event.preventDefault();
      selectByIndex(selectedIndex - 1);
    } else if (event.key === "ArrowRight" || event.key === "ArrowUp") {
      event.preventDefault();
      selectByIndex(selectedIndex + 1);
    } else if (event.key === "Home") {
      event.preventDefault();
      selectByIndex(0);
    } else if (event.key === "End") {
      event.preventDefault();
      selectByIndex(maxIndex);
    }
  };

  return (
    <div>
      <h3 className="mb-1 text-sm font-medium">{serviceName}</h3>
      {history.length > 0 && (
        <div>
          <div className="relative flex min-h-11 items-center">
            {/* Read-only 30-day visual bars (no narrow buttons, no 30 tab stops) */}
            <div
              data-testid={`status-history-strip-${component.id}`}
              aria-hidden="true"
              className="pointer-events-none flex w-full items-center gap-[3px] sm:gap-1"
            >
              {history.map((day, index) => (
                <div
                  key={day.date}
                  data-day-bar={day.date}
                  data-day-status={day.status}
                  data-day-selected={index === selectedIndex ? "true" : "false"}
                  className="flex flex-1 min-w-0 items-center"
                >
                  <div className={getDayBarClasses(day.status, index === selectedIndex)} />
                </div>
              ))}
            </div>

            {/* Full-width >=44px native range input for touch, mouse, and keyboard */}
            <input
              type="range"
              data-testid={`status-history-range-${component.id}`}
              min={0}
              max={maxIndex}
              step={1}
              value={selectedIndex}
              aria-label={t("statusPage.history.rangeAria", {
                service: serviceName,
                defaultValue: "{{service}}近 30 天日期选择",
              })}
              aria-valuetext={ariaValueText}
              onChange={(event) => selectByIndex(Number(event.target.value))}
              onKeyDown={handleRangeKeyDown}
              className="absolute inset-0 w-full min-h-11 cursor-pointer opacity-0 focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] rounded"
            />
          </div>

          {/* Selected date details + 44px prev/next step controls */}
          {activeDay && (
            <div
              data-testid={`status-history-detail-${component.id}`}
              className="flex items-center justify-between gap-2 text-xs"
            >
              <button
                type="button"
                data-testid={`status-history-prev-${component.id}`}
                onClick={() => selectByIndex(selectedIndex - 1)}
                disabled={selectedIndex <= 0}
                aria-label={t("statusPage.history.prevDay", { defaultValue: "前一天" })}
                title={t("statusPage.history.prevDay", { defaultValue: "前一天" })}
                className="inline-flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-lg text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--bg-tertiary))] hover:text-[hsl(var(--text-primary))] disabled:opacity-40 disabled:pointer-events-none transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)]"
              >
                <ChevronLeft className="h-4 w-4" aria-hidden="true" />
              </button>

              <div className="flex flex-1 flex-wrap items-center justify-center gap-x-2 gap-y-1 text-center tabular-nums">
                <span className="font-medium text-[hsl(var(--text-primary))]">
                  {activeDay.date}
                </span>
                {isTodaySelected && (
                  <span className="text-[hsl(var(--text-secondary))]">
                    {t("statusPage.history.todayPartial", { defaultValue: "截至更新" })}
                  </span>
                )}
                <span className="text-[hsl(var(--text-secondary))]">·</span>
                <span className="inline-flex items-center gap-1.5 font-medium text-[hsl(var(--text-primary))]">
                  <span
                    aria-hidden="true"
                    className={`h-2 w-2 rounded-full ${getToneClasses(activeDay.status).dot}`}
                  />
                  <span>{activeDayStatusLabel}</span>
                </span>
                <span className="text-[hsl(var(--text-secondary))]">·</span>
                <span className="text-[hsl(var(--text-secondary))]">
                  <strong className="font-medium text-[hsl(var(--text-primary))]">
                    {activeDayRate ?? t("statusPage.status.unknown", { defaultValue: "无数据" })}
                  </strong>
                </span>
              </div>

              <button
                type="button"
                data-testid={`status-history-next-${component.id}`}
                onClick={() => selectByIndex(selectedIndex + 1)}
                disabled={selectedIndex >= maxIndex}
                aria-label={t("statusPage.history.nextDay", { defaultValue: "后一天" })}
                title={t("statusPage.history.nextDay", { defaultValue: "后一天" })}
                className="inline-flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-lg text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--bg-tertiary))] hover:text-[hsl(var(--text-primary))] disabled:opacity-40 disabled:pointer-events-none transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)]"
              >
                <ChevronRight className="h-4 w-4" aria-hidden="true" />
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default StatusPage;
