import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { AlertCircle, Image as ImageIcon, Search } from "lucide-react";

import { adminApi } from "../../lib/adminApi";
import type { AdminFeedbackItem, AdminFeedbackStatus } from "../../types/admin";
import { AdminPageState, AdminSelect } from "../../components/admin";
import { toast } from "../../lib/toast";
import Modal from "../../components/ui/Modal";
import { useMediaQuery } from "../../hooks/useMediaQuery";
import { AdminUserLink } from "../../components/admin/AdminUserLink";
import { formatAdminDateTime } from "../../lib/dateUtils";

type ScreenshotFilter = "all" | "with" | "without";

const PAGE_SIZE = 20;

const statusOrder: AdminFeedbackStatus[] = ["open", "processing", "resolved"];

export default function FeedbackManagement() {
  const { t } = useTranslation(["admin", "common"]);
  const queryClient = useQueryClient();
  const isCompact = useMediaQuery("(max-width: 1023px)");
  const [page, setPage] = useState(0);
  const [statusFilter, setStatusFilter] = useState<AdminFeedbackStatus | "">("");
  const [sourceFilter, setSourceFilter] = useState<"dashboard" | "editor" | "">("");
  const [screenshotFilter, setScreenshotFilter] = useState<ScreenshotFilter>("all");
  const [searchInput, setSearchInput] = useState("");
  const [searchKeyword, setSearchKeyword] = useState("");
  const [previewFeedback, setPreviewFeedback] = useState<AdminFeedbackItem | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const previewRequestRef = useRef(0);
  const ownedPreviewUrlRef = useRef<string | null>(null);
  const mountedRef = useRef(true);

  const hasScreenshotFilter = useMemo(() => {
    if (screenshotFilter === "with") return true;
    if (screenshotFilter === "without") return false;
    return undefined;
  }, [screenshotFilter]);

  const { data, isLoading, isFetching, isError, error, refetch } = useQuery({
    queryKey: [
      "admin",
      "feedback",
      page,
      statusFilter,
      sourceFilter,
      screenshotFilter,
      searchKeyword,
    ],
    queryFn: () =>
      adminApi.getFeedbackList({
        skip: page * PAGE_SIZE,
        limit: PAGE_SIZE,
        status: statusFilter || undefined,
        source_page: sourceFilter || undefined,
        has_screenshot: hasScreenshotFilter,
        search: searchKeyword || undefined,
      }),
    staleTime: 30_000,
  });

  const updateStatusMutation = useMutation({
    mutationFn: ({ id, status }: { id: string; status: AdminFeedbackStatus }) =>
      adminApi.updateFeedbackStatus(id, status),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "feedback"] });
      toast.success(t("feedback.statusUpdated", "反馈状态已更新"));
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : t("feedback.statusUpdateFailed", "状态更新失败，请重试"));
    },
  });

  const revokeOwnedPreviewUrl = useCallback(() => {
    if (ownedPreviewUrlRef.current) {
      URL.revokeObjectURL(ownedPreviewUrlRef.current);
      ownedPreviewUrlRef.current = null;
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      previewRequestRef.current += 1;
      revokeOwnedPreviewUrl();
    };
  }, [revokeOwnedPreviewUrl]);

  const feedbackItems = data?.items ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const queryErrorText = error instanceof Error && error.message
    ? error.message
    : t("common:error");

  const formatDate = (value: string) => formatAdminDateTime(value);

  const formatFileSize = (sizeBytes: number | null) => {
    if (!sizeBytes) return "-";
    return `${(sizeBytes / 1024 / 1024).toFixed(2)} MB`;
  };

  const renderDebugMeta = (feedback: AdminFeedbackItem) => {
    const lines: Array<{ label: string; value: string | null }> = [
      { label: "req", value: feedback.request_id },
      { label: "trace", value: feedback.trace_id },
      { label: "run", value: feedback.agent_run_id },
      { label: "project", value: feedback.project_id },
      { label: "session", value: feedback.agent_session_id },
    ];

    const visible = lines.filter((line) => Boolean(line.value));
    if (!visible.length) return null;

    return (
      <div className="mt-2 space-y-1 text-[10px] font-mono text-[hsl(var(--text-secondary))]">
        {visible.map((line) => (
          <div key={line.label} className="max-w-[360px] truncate">
            {line.label}: {line.value}
          </div>
        ))}
      </div>
    );
  };

  const handleSearchSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setPage(0);
    setSearchKeyword(searchInput.trim());
  };

  const handleStatusChange = (feedback: AdminFeedbackItem, nextStatus: AdminFeedbackStatus) => {
    if (feedback.status === nextStatus) return;
    updateStatusMutation.mutate({ id: feedback.id, status: nextStatus });
  };

  const handleOpenScreenshot = async (feedback: AdminFeedbackItem) => {
    if (!feedback.has_screenshot) return;
    const request = ++previewRequestRef.current;
    setPreviewFeedback(feedback);
    setPreviewLoading(true);
    setPreviewError(null);
    revokeOwnedPreviewUrl();
    setPreviewUrl(null);
    try {
      const blob = await adminApi.getFeedbackScreenshotBlob(feedback.id);
      if (!mountedRef.current || previewRequestRef.current !== request) return;
      const nextUrl = URL.createObjectURL(blob);
      if (!mountedRef.current || previewRequestRef.current !== request) {
        URL.revokeObjectURL(nextUrl);
        return;
      }
      ownedPreviewUrlRef.current = nextUrl;
      setPreviewUrl(nextUrl);
    } catch (err) {
      if (!mountedRef.current || previewRequestRef.current !== request) return;
      setPreviewError(err instanceof Error ? err.message : t("feedback.screenshotLoadFailed"));
    } finally {
      if (mountedRef.current && previewRequestRef.current === request) setPreviewLoading(false);
    }
  };

  const closePreview = () => {
    previewRequestRef.current += 1;
    revokeOwnedPreviewUrl();
    setPreviewUrl(null);
    setPreviewFeedback(null);
    setPreviewLoading(false);
    setPreviewError(null);
  };

  return (
    <div className="admin-page admin-page-fluid">
      <div>
        <h1 className="admin-page-title">
          {t("feedback.title", "问题反馈管理")}
        </h1>
        <p className="admin-page-subtitle">
          {t("feedback.subtitle", "查看用户反馈和截图，跟进处理状态")}
        </p>
      </div>

      <form
        onSubmit={handleSearchSubmit}
        className="grid grid-cols-1 gap-2 md:grid-cols-[1fr_repeat(3,minmax(0,180px))_auto]"
      >
        <div className="relative">
          <Search
            size={16}
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[hsl(var(--text-secondary))]"
          />
          <input
            value={searchInput}
            onChange={(event) => setSearchInput(event.target.value)}
            placeholder={t("feedback.searchPlaceholder", "搜索用户名、邮箱或问题描述")}
            className="input h-11 pl-9"
          />
        </div>

        <AdminSelect
          value={statusFilter}
          onChange={(event) => {
            setStatusFilter(event.target.value as AdminFeedbackStatus | "");
            setPage(0);
          }}
          className="h-11"
        >
          <option value="">{t("feedback.filterAllStatus", "全部状态")}</option>
          {statusOrder.map((status) => (
            <option key={status} value={status}>
              {t(`feedback.status.${status}`)}
            </option>
          ))}
        </AdminSelect>

        <AdminSelect
          value={sourceFilter}
          onChange={(event) => {
            setSourceFilter(event.target.value as "dashboard" | "editor" | "");
            setPage(0);
          }}
          className="h-11"
        >
          <option value="">{t("feedback.filterAllSources", "全部来源")}</option>
          <option value="dashboard">{t("feedback.source.dashboard", "Dashboard")}</option>
          <option value="editor">{t("feedback.source.editor", "写作页")}</option>
        </AdminSelect>

        <AdminSelect
          value={screenshotFilter}
          onChange={(event) => {
            setScreenshotFilter(event.target.value as ScreenshotFilter);
            setPage(0);
          }}
          className="h-11"
        >
          <option value="all">{t("feedback.filterAllScreenshots", "全部截图")}</option>
          <option value="with">{t("feedback.filterWithScreenshot", "仅有截图")}</option>
          <option value="without">{t("feedback.filterWithoutScreenshot", "仅无截图")}</option>
        </AdminSelect>

        <button type="submit" className="btn-primary h-11 px-4">
          {t("common:search", "搜索")}
        </button>
      </form>

      <AdminPageState
        isLoading={isLoading}
        isFetching={isFetching}
        isError={isError}
        isEmpty={!feedbackItems.length}
        loadingText={t("common:loading")}
        errorText={queryErrorText}
        emptyText={t("feedback.empty", "暂无反馈记录")}
        retryText={t("common:retry")}
        onRetry={() => {
          void refetch();
        }}
      >
        {isCompact ? (
          <div className="space-y-3">
            {feedbackItems.map((feedback) => (
              <div
                key={feedback.id}
                className="rounded-xl border border-[hsl(var(--separator-color))] bg-[hsl(var(--bg-secondary))] p-4 shadow-sm"
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium text-[hsl(var(--text-primary))]">
                      <AdminUserLink userId={feedback.user_id}>{feedback.username}</AdminUserLink> · {feedback.email}
                    </div>
                    <div className="mt-1 text-xs text-[hsl(var(--text-secondary))]">
                      {formatDate(feedback.created_at)}
                    </div>
                  </div>
                  <AdminSelect
                    value={feedback.status}
                    onChange={(event) =>
                      handleStatusChange(feedback, event.target.value as AdminFeedbackStatus)
                    }
                    className="h-9 text-xs"
                  >
                    {statusOrder.map((status) => (
                      <option key={status} value={status}>
                        {t(`feedback.status.${status}`)}
                      </option>
                    ))}
                  </AdminSelect>
                </div>

                <div className="mt-3 text-xs text-[hsl(var(--text-secondary))]">
                  {t(`feedback.source.${feedback.source_page}`)}
                  {feedback.source_route ? ` · ${feedback.source_route}` : ""}
                </div>

                <p className="mt-2 line-clamp-4 text-sm text-[hsl(var(--text-primary))]">
                  {feedback.issue_text}
                </p>

                {renderDebugMeta(feedback)}

                <div className="mt-3 flex items-center justify-between">
                  <div className="text-xs text-[hsl(var(--text-secondary))]">
                    {feedback.has_screenshot
                      ? t("feedback.hasScreenshotMeta", "{{name}} ({{size}})", {
                          name: feedback.screenshot_original_name || "screenshot",
                          size: formatFileSize(feedback.screenshot_size_bytes),
                        })
                      : t("feedback.noScreenshot", "无截图")}
                  </div>
                  {feedback.has_screenshot && (
                    <button
                      type="button"
                      className="btn-ghost h-9 px-3 text-xs"
                      onClick={() => {
                        void handleOpenScreenshot(feedback);
                      }}
                    >
                      <ImageIcon size={14} />
                      {t("feedback.viewScreenshot", "查看截图")}
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="admin-table-shell overflow-x-auto">
            <table className="min-w-[900px] text-left text-sm">
              <thead className="bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-secondary))]">
                <tr>
                  <th className="px-4 py-3 font-medium">{t("feedback.columns.user", "用户")}</th>
                  <th className="px-4 py-3 font-medium">{t("feedback.columns.source", "来源")}</th>
                  <th className="px-4 py-3 font-medium">{t("feedback.columns.issue", "问题描述")}</th>
                  <th className="px-4 py-3 font-medium">{t("feedback.columns.debug", "调试信息")}</th>
                  <th className="px-4 py-3 font-medium">{t("feedback.columns.screenshot", "截图")}</th>
                  <th className="px-4 py-3 font-medium">{t("feedback.columns.status", "状态")}</th>
                  <th className="px-4 py-3 font-medium">{t("feedback.columns.createdAt", "提交时间")}</th>
                </tr>
              </thead>
              <tbody>
                {feedbackItems.map((feedback) => (
                  <tr key={feedback.id} className="border-t border-[hsl(var(--separator-color))] align-top">
                    <td className="px-4 py-3">
                      <div className="font-medium text-[hsl(var(--text-primary))]"><AdminUserLink userId={feedback.user_id}>{feedback.username}</AdminUserLink></div>
                      <div className="text-xs text-[hsl(var(--text-secondary))]">{feedback.email}</div>
                    </td>
                    <td className="px-4 py-3 text-[hsl(var(--text-secondary))]">
                      <div>{t(`feedback.source.${feedback.source_page}`)}</div>
                      {feedback.source_route && (
                        <div className="mt-1 max-w-[220px] truncate text-xs">{feedback.source_route}</div>
                      )}
                    </td>
                    <td className="max-w-[420px] px-4 py-3">
                      <p className="line-clamp-3 text-[hsl(var(--text-primary))]">{feedback.issue_text}</p>
                    </td>
                    <td className="px-4 py-3 text-xs text-[hsl(var(--text-secondary))]">
                      {renderDebugMeta(feedback) ? (
                        <div className="space-y-1 font-mono">
                          {feedback.request_id && (
                            <div className="max-w-[220px] truncate">req: {feedback.request_id}</div>
                          )}
                          {feedback.trace_id && (
                            <div className="max-w-[220px] truncate">trace: {feedback.trace_id}</div>
                          )}
                          {feedback.agent_run_id && (
                            <div className="max-w-[220px] truncate">run: {feedback.agent_run_id}</div>
                          )}
                          {feedback.project_id && (
                            <div className="max-w-[220px] truncate">project: {feedback.project_id}</div>
                          )}
                          {feedback.agent_session_id && (
                            <div className="max-w-[220px] truncate">session: {feedback.agent_session_id}</div>
                          )}
                        </div>
                      ) : (
                        <span className="text-xs text-[hsl(var(--text-secondary))]">-</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      {feedback.has_screenshot ? (
                        <button
                          type="button"
                          className="btn-ghost h-9 px-3 text-xs"
                          onClick={() => {
                            void handleOpenScreenshot(feedback);
                          }}
                        >
                          <ImageIcon size={14} />
                          {t("feedback.viewScreenshot", "查看截图")}
                        </button>
                      ) : (
                        <span className="text-xs text-[hsl(var(--text-secondary))]">
                          {t("feedback.noScreenshot", "无截图")}
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <AdminSelect
                        value={feedback.status}
                        onChange={(event) =>
                          handleStatusChange(feedback, event.target.value as AdminFeedbackStatus)
                        }
                        className="h-10 min-w-[130px]"
                      >
                        {statusOrder.map((status) => (
                          <option key={status} value={status}>
                            {t(`feedback.status.${status}`)}
                          </option>
                        ))}
                      </AdminSelect>
                    </td>
                    <td className="px-4 py-3 text-xs text-[hsl(var(--text-secondary))]">
                      {formatDate(feedback.created_at)}
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
            from: total === 0 ? 0 : page * PAGE_SIZE + 1,
            to: Math.min((page + 1) * PAGE_SIZE, total),
            total,
          })}
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            className="btn-ghost h-10 px-3"
            disabled={page === 0}
            onClick={() => setPage((prev) => Math.max(0, prev - 1))}
          >
            {t("common:previous", "上一页")}
          </button>
          <span className="text-xs text-[hsl(var(--text-secondary))]">
            {t("feedback.pageInfo", "第 {{page}} / {{total}} 页", {
              page: page + 1,
              total: totalPages,
            })}
          </span>
          <button
            type="button"
            className="btn-ghost h-10 px-3"
            disabled={page + 1 >= totalPages}
            onClick={() => setPage((prev) => Math.min(totalPages - 1, prev + 1))}
          >
            {t("common:next", "下一页")}
          </button>
        </div>
      </div>

      <Modal
        open={Boolean(previewFeedback)}
        onClose={closePreview}
        title={t("feedback.screenshotPreviewTitle", "反馈截图预览")}
        size="xl"
        footer={
          <button type="button" className="btn-primary h-10 px-4" onClick={closePreview}>
            {t("common:close", "关闭")}
          </button>
        }
      >
        <div className="space-y-3">
          {previewFeedback && (
            <div className="rounded-lg border border-[hsl(var(--separator-color))] bg-[hsl(var(--bg-tertiary)/0.4)] p-3 text-xs text-[hsl(var(--text-secondary))]">
              <div><AdminUserLink userId={previewFeedback.user_id}>{previewFeedback.username}</AdminUserLink> · {previewFeedback.email}</div>
              <div className="mt-1">{formatDate(previewFeedback.created_at)}</div>
              <div className="mt-1">
                {previewFeedback.screenshot_original_name || "-"} ·{" "}
                {formatFileSize(previewFeedback.screenshot_size_bytes)}
              </div>
              {(previewFeedback.request_id
                || previewFeedback.trace_id
                || previewFeedback.agent_run_id
                || previewFeedback.project_id
                || previewFeedback.agent_session_id) && (
                <div className="mt-2 space-y-1 font-mono text-[10px]">
                  {previewFeedback.request_id && <div>req: {previewFeedback.request_id}</div>}
                  {previewFeedback.trace_id && <div>trace: {previewFeedback.trace_id}</div>}
                  {previewFeedback.agent_run_id && <div>run: {previewFeedback.agent_run_id}</div>}
                  {previewFeedback.project_id && <div>project: {previewFeedback.project_id}</div>}
                  {previewFeedback.agent_session_id && <div>session: {previewFeedback.agent_session_id}</div>}
                </div>
              )}
            </div>
          )}

          {previewLoading && (
            <div className="flex h-80 items-center justify-center text-[hsl(var(--text-secondary))]">
              {t("common:loading", "加载中...")}
            </div>
          )}

          {!previewLoading && previewError && (
            <div className="flex items-center gap-2 rounded-lg border border-[hsl(var(--error)/0.25)] bg-[hsl(var(--error)/0.1)] p-3 text-sm text-[hsl(var(--error))]">
              <AlertCircle size={16} />
              <span>{previewError}</span>
            </div>
          )}

          {!previewLoading && !previewError && previewUrl && (
            <img
              src={previewUrl}
              alt={t("feedback.screenshotPreviewAlt", "反馈截图")}
              className="max-h-[70vh] w-full rounded-lg border border-[hsl(var(--separator-color))] bg-[hsl(var(--bg-primary))] object-contain"
            />
          )}
        </div>
      </Modal>
    </div>
  );
}
