import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ApiError } from "../../lib/apiClient";
import { announcementApi } from "../../lib/announcementApi";
import { safeAnnouncementUrl } from "../../lib/announcementLinks";
import { toast } from "../../lib/toast";
import type { AdminAnnouncement, UpdateAnnouncementRequest } from "../../types/announcement";
import { AdminPageState } from "../../components/admin";
import { Button } from "../../components/ui/Button";

type Draft = Pick<AdminAnnouncement, "title" | "description" | "action_label" | "action_url">;
const queryKey = ["admin", "announcement", "current"] as const;

function toDraft(value: AdminAnnouncement): Draft {
  return {
    title: value.title,
    description: value.description,
    action_label: value.action_label,
    action_url: value.action_url,
  };
}

export default function AnnouncementManagement() {
  const { t } = useTranslation(["announcements", "common"]);
  const query = useQuery({ queryKey, queryFn: announcementApi.getCurrentAdmin, retry: false, refetchOnMount: "always", refetchOnWindowFocus: false });
  const [initialAnnouncement, setInitialAnnouncement] = useState<AdminAnnouncement | null>(null);
  // Only a successful request on this route entry may initialize the editor.
  // Once mounted, a failed background refresh must not discard its local draft.
  if (initialAnnouncement === null && query.isSuccess && query.isFetchedAfterMount) {
    setInitialAnnouncement(query.data);
  }

  return (
    <div className="admin-page">
      <header>
        <h1 className="admin-page-title">{t("admin.title", "站内公告")}</h1>
        <p className="admin-page-subtitle">{t("admin.subtitle", "维护工作台首页当前展示的一条轻量公告")}</p>
      </header>

      <AdminPageState
        isLoading={!initialAnnouncement && (query.isPending || query.isFetching)}
        isFetching={query.isFetching}
        isError={!initialAnnouncement && query.isError}
        isEmpty={false}
        loadingText={t("common:loading", "加载中…")}
        errorText={t("admin.loadFailed", "公告配置加载失败")}
        emptyText=""
        retryText={t("common:retry", "重试")}
        onRetry={() => void query.refetch()}
        stateClassName="admin-surface flex items-center justify-center py-12"
      >
        {initialAnnouncement && (
          <>
            {query.isError && (
              <div className="admin-surface mb-4 flex flex-wrap items-center justify-between gap-3 p-4">
                <p role="alert" className="text-sm text-[hsl(var(--error-text))]">
                  {t("admin.refreshFailedDraftKept", "刷新失败，你的输入已保留。可以重试读取，保存时仍会校验原版本。")}
                </p>
                <Button type="button" variant="secondary" size="touch" disabled={query.isFetching} onClick={() => void query.refetch()}>
                  {t("common:retry", "重试")}
                </Button>
              </div>
            )}
            <AnnouncementEditor initial={initialAnnouncement} />
          </>
        )}
      </AdminPageState>
    </div>
  );
}

function AnnouncementEditor({ initial }: { initial: AdminAnnouncement }) {
  const { t } = useTranslation(["announcements", "common"]);
  const queryClient = useQueryClient();
  const [published, setPublished] = useState(initial);
  const [draft, setDraft] = useState<Draft>(() => toDraft(initial));
  const [hasConflict, setHasConflict] = useState(false);
  const [isReloading, setIsReloading] = useState(false);
  const mutation = useMutation({
    mutationFn: (request: UpdateAnnouncementRequest) => announcementApi.updateCurrent(request),
    onSuccess: (next, request) => {
      queryClient.setQueryData(queryKey, next);
      void queryClient.invalidateQueries({ queryKey: ["announcement", "current"] });
      setPublished(next);
      setHasConflict(false);
      if (request.enabled) setDraft(toDraft(next));
      toast.success(next.enabled
        ? t("admin.saved", "公告已保存并展示")
        : t("admin.stopped", "公告已停止展示"));
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409) setHasConflict(true);
      toast.error(error instanceof ApiError && error.status === 409
        ? t("admin.conflict", "公告已被其他管理员更新，请刷新后重试")
        : t("admin.saveFailed", "保存失败，请重试"));
    },
  });

  const isBusy = mutation.isPending || isReloading;
  const requestFrom = (source: Draft, enabled: boolean): UpdateAnnouncementRequest => ({
    title: source.title.trim(),
    description: source.description.trim(),
    action_label: source.action_label?.trim() || null,
    action_url: source.action_url?.trim() || null,
    enabled,
    expected_version: published.version,
    expected_updated_at: published.updated_at,
  });

  const actionLabel = draft.action_label?.trim() ?? "";
  const actionUrl = draft.action_url?.trim() ?? "";
  const actionIsValid = (!actionLabel && !actionUrl) || Boolean(actionLabel && safeAnnouncementUrl(actionUrl));
  const canSave = Boolean(draft.title.trim() && draft.description.trim() && actionIsValid) && !isBusy;
  const save = () => {
    if (canSave) mutation.mutate(requestFrom(draft, true));
  };
  const stop = () => {
    if (published.enabled && !isBusy) mutation.mutate(requestFrom(toDraft(published), false));
  };
  const reloadLatest = async () => {
    if (isBusy) return;
    setIsReloading(true);
    try {
      const latest = await announcementApi.getCurrentAdmin();
      queryClient.setQueryData(queryKey, latest);
      setPublished(latest);
      setDraft(toDraft(latest));
      setHasConflict(false);
    } catch {
      toast.error(t("admin.loadFailed", "公告配置加载失败"));
    } finally {
      setIsReloading(false);
    }
  };

  return (
    <div className="admin-surface max-w-3xl p-5 sm:p-6">
      <div className="mb-5 flex flex-wrap items-center justify-between gap-2 border-b border-[hsl(var(--separator-color))] pb-4">
        <div>
          <h2 className="text-base font-semibold text-[hsl(var(--text-primary))]">{t("admin.current", "当前公告")}</h2>
          <p className="mt-1 text-xs text-[hsl(var(--text-secondary))]">
            {published.enabled ? t("admin.visible", "正在展示") : t("admin.hidden", "未展示")} · {t("admin.version", "版本 {{version}}", { version: published.version })}
          </p>
        </div>
      </div>

      <form className="space-y-5" onSubmit={(event) => { event.preventDefault(); save(); }}>
        <label className="block text-sm font-medium text-[hsl(var(--text-primary))]">
          {t("admin.fields.title", "标题")}
          <input
            value={draft.title}
            disabled={isBusy}
            maxLength={80}
            onChange={(event) => setDraft((value) => ({ ...value, title: event.target.value }))}
            className="mt-2 min-h-11 w-full rounded-xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] px-3 text-base text-[hsl(var(--text-primary))] focus:outline-none focus:ring-2 focus:ring-[hsl(var(--accent-primary))]"
          />
        </label>
        <label className="block text-sm font-medium text-[hsl(var(--text-primary))]">
          {t("admin.fields.description", "说明")}
          <textarea
            value={draft.description}
            disabled={isBusy}
            maxLength={240}
            rows={3}
            onChange={(event) => setDraft((value) => ({ ...value, description: event.target.value }))}
            className="mt-2 w-full resize-y rounded-xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] px-3 py-2.5 text-base text-[hsl(var(--text-primary))] focus:outline-none focus:ring-2 focus:ring-[hsl(var(--accent-primary))]"
          />
        </label>
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="block text-sm font-medium text-[hsl(var(--text-primary))]">
            {t("admin.fields.actionLabel", "按钮文案（可选）")}
            <input
              value={draft.action_label ?? ""}
              disabled={isBusy}
              maxLength={24}
              onChange={(event) => setDraft((value) => ({ ...value, action_label: event.target.value }))}
              className="mt-2 min-h-11 w-full rounded-xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] px-3 text-base text-[hsl(var(--text-primary))] focus:outline-none focus:ring-2 focus:ring-[hsl(var(--accent-primary))]"
            />
          </label>
          <label className="block text-sm font-medium text-[hsl(var(--text-primary))]">
            {t("admin.fields.actionUrl", "按钮链接（可选）")}
            <input
              value={draft.action_url ?? ""}
              disabled={isBusy}
              maxLength={2048}
              placeholder={t("admin.fields.actionUrlPlaceholder", "/docs 或 https://example.com")}
              onChange={(event) => setDraft((value) => ({ ...value, action_url: event.target.value }))}
              className="mt-2 min-h-11 w-full rounded-xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] px-3 text-base text-[hsl(var(--text-primary))] focus:outline-none focus:ring-2 focus:ring-[hsl(var(--accent-primary))]"
            />
          </label>
        </div>
        <p className="text-xs leading-5 text-[hsl(var(--text-secondary))]">{t("admin.linkHint", "按钮文案和链接都填写时才会展示；链接仅支持站内根路径或 HTTPS 地址")}</p>
        {hasConflict && (
          <div role="alert" className="rounded-xl border border-[hsl(var(--warning)/0.35)] bg-[hsl(var(--warning)/0.08)] p-3 text-sm leading-6 text-[hsl(var(--text-primary))]">
            <p>{t("admin.conflictDraftKept", "你的输入已保留。加载最新配置会替换当前草稿，再继续编辑。")}</p>
            <Button type="button" variant="secondary" size="touch" className="mt-2" disabled={isBusy} onClick={() => void reloadLatest()}>
              {isReloading ? t("admin.reloading", "加载中…") : t("admin.reloadLatest", "加载最新配置")}
            </Button>
          </div>
        )}
        <div className="flex flex-col-reverse gap-3 border-t border-[hsl(var(--separator-color))] pt-5 sm:flex-row sm:justify-end">
          <Button type="button" variant="secondary" size="touch" disabled={!published.enabled || isBusy} onClick={stop}>
            {t("admin.stop", "停止展示")}
          </Button>
          <Button type="submit" size="touch" disabled={!canSave}>
            {mutation.isPending ? t("admin.saving", "保存中…") : t("admin.saveAndShow", "保存并展示")}
          </Button>
        </div>
      </form>
    </div>
  );
}
