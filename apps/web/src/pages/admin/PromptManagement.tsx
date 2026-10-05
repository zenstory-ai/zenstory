import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Plus, FileText, Edit, Trash2, RefreshCw, AlertTriangle } from "lucide-react";
import { AdminPageState } from "../../components/admin";
import { adminApi } from "../../lib/adminApi";
import { getLocaleCode } from "../../lib/i18n-helpers";
import { toast } from "../../lib/toast";

export const PromptManagement: React.FC = () => {
  const { t } = useTranslation(["admin", "common"]);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [isReloading, setIsReloading] = useState(false);
  const [deletingPrompt, setDeletingPrompt] = useState<{ project_type: string } | null>(null);

  // 获取所有 Prompt 配置
  const { data, isLoading, isFetching, isError, error, refetch } = useQuery({
    queryKey: ["admin", "prompts"],
    queryFn: () => adminApi.getPrompts(),
    staleTime: 30 * 1000,
  });

  // 重载配置 mutation
  const reloadMutation = useMutation({
    mutationFn: () => adminApi.reloadPrompts(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "prompts"] });
      toast.success(t("prompts.reloadSuccess"));
      setIsReloading(false);
    },
    onError: () => {
      toast.error(t("prompts.reloadFailed"));
      setIsReloading(false);
    },
  });

  const prompts = data ?? [];
  const queryErrorText = error instanceof Error && error.message
    ? error.message
    : t("common:error");

  const formatDate = (dateStr?: string) => {
    if (!dateStr) return "-";
    const date = new Date(dateStr);
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

  const handleCardClick = (projectType: string) => {
    navigate(`/admin/prompts/${encodeURIComponent(projectType)}`);
  };

  const handleCreateClick = () => {
    navigate("/admin/prompts/new");
  };

  const handleReload = () => {
    setIsReloading(true);
    reloadMutation.mutate();
  };

  // 删除 mutation
  const deleteMutation = useMutation({
    mutationFn: (projectType: string) => adminApi.deletePrompt(projectType),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "prompts"] });
      toast.success(t("prompts.deleteSuccess"));
      setDeletingPrompt(null);
    },
    onError: () => {
      toast.error(t("prompts.deleteFailed"));
      setDeletingPrompt(null);
    },
  });

  const handleDeletePrompt = (projectType: string) => {
    setDeletingPrompt({ project_type: projectType });
  };

  const handleDeleteConfirm = () => {
    if (!deletingPrompt) return;
    deleteMutation.mutate(deletingPrompt.project_type);
  };

  return (
    <div className="admin-page">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="admin-page-title">
            {t("prompts.title")}
          </h1>
          <p className="admin-page-subtitle">
            {t("prompts.subtitle")}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleReload}
            disabled={isReloading || reloadMutation.isPending}
            className="flex items-center gap-2 px-4 py-2 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg hover:bg-[hsl(var(--bg-tertiary))] transition-colors disabled:opacity-50"
          >
            <RefreshCw size={18} className={isReloading || reloadMutation.isPending ? "animate-spin" : ""} />
            {t("prompts.reload")}
          </button>
          <button
            onClick={handleCreateClick}
            className="flex items-center gap-2 px-4 py-2 bg-[hsl(var(--accent-primary))] text-white rounded-lg hover:opacity-90 transition-opacity"
          >
            <Plus size={18} />
            {t("prompts.create")}
          </button>
        </div>
      </div>

      {/* Prompt 配置卡片网格 */}
      <AdminPageState
        isLoading={isLoading}
        isFetching={isFetching}
        isError={isError}
        isEmpty={prompts.length === 0}
        loadingText={t("common:loading")}
        errorText={queryErrorText}
        emptyText={t("common:noData")}
        retryText={t("common:retry")}
        onRetry={() => {
          void refetch();
        }}
      >
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {prompts.map((prompt) => (
            <div
              key={prompt.project_type}
              onClick={() => handleCardClick(prompt.project_type)}
              className="admin-surface min-w-0 p-4 cursor-pointer hover:border-[hsl(var(--accent-primary))] transition-colors"
            >
              <div className="flex items-start justify-between mb-3">
                <div className="min-w-0 flex-1">
                  <h3 className="text-lg font-semibold text-[hsl(var(--text-primary))] mb-1 break-words">
                    {prompt.project_type}
                  </h3>
                  <span
                    className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${
                      prompt.is_active
                        ? "bg-[hsl(var(--success)/0.15)] text-[hsl(var(--success))]"
                        : "bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-secondary))]"
                    }`}
                  >
                    {prompt.is_active ? t("prompts.active") : t("prompts.inactive")}
                  </span>
                </div>
                <FileText size={20} className="shrink-0 text-[hsl(var(--text-secondary))]" />
              </div>

              <div className="space-y-2 text-sm">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-[hsl(var(--text-secondary))]">
                    {t("prompts.version")}:
                  </span>
                  <span className="min-w-0 break-words text-right font-medium text-[hsl(var(--text-primary))]">
                    {prompt.version}
                  </span>
                </div>
                <div className="flex items-center justify-between gap-3">
                  <span className="text-[hsl(var(--text-secondary))]">
                    {t("prompts.updatedAt")}:
                  </span>
                  <span className="min-w-0 break-words text-right text-[hsl(var(--text-primary))]">
                    {prompt.updated_at ? formatDate(prompt.updated_at) : "-"}
                  </span>
                </div>
              </div>

              <div className="mt-4 pt-3 border-t border-[hsl(var(--separator-color))] flex items-center justify-end gap-2">
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    handleCardClick(prompt.project_type);
                  }}
                  className="p-1.5 hover:bg-[hsl(var(--bg-tertiary))] rounded transition-colors"
                  title={t("prompts.edit")}
                >
                  <Edit size={16} className="text-[hsl(var(--text-primary))]" />
                </button>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    handleDeletePrompt(prompt.project_type);
                  }}
                  disabled={deleteMutation.isPending}
                  className="p-1.5 hover:bg-[hsl(var(--bg-tertiary))] rounded transition-colors disabled:opacity-50"
                  title={t("prompts.delete")}
                >
                  <Trash2 size={16} className="text-red-500" />
                </button>
              </div>
            </div>
          ))}
        </div>
      </AdminPageState>

      {/* Delete Confirmation Modal */}
      {deletingPrompt && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50">
          <div className="bg-[hsl(var(--bg-primary))] border border-[hsl(var(--separator-color))] rounded-lg shadow-xl w-full max-w-md">
            <div className="flex items-center gap-3 px-4 sm:px-6 py-4 border-b border-[hsl(var(--separator-color))]">
              <AlertTriangle size={24} className="text-red-500" />
              <h2 className="text-lg font-semibold text-[hsl(var(--text-primary))]">
                {t("prompts.delete")}
              </h2>
            </div>

            <div className="px-4 sm:px-6 py-4">
              <p className="text-[hsl(var(--text-secondary))]">
                {t("prompts.deleteConfirm")}
              </p>
              <p className="mt-2 text-sm text-[hsl(var(--text-primary))] font-medium">
                {deletingPrompt.project_type}
              </p>
            </div>

            <div className="flex flex-col-reverse sm:flex-row items-center justify-end gap-2 px-4 sm:px-6 py-4 border-t border-[hsl(var(--separator-color))]">
              <button
                onClick={() => setDeletingPrompt(null)}
                disabled={deleteMutation.isPending}
                className="w-full sm:w-auto px-4 py-2.5 min-h-11 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg hover:bg-[hsl(var(--bg-tertiary))] active:scale-95 transition-all text-sm text-[hsl(var(--text-primary))] disabled:opacity-50"
              >
                {t("common:cancel")}
              </button>
              <button
                onClick={handleDeleteConfirm}
                disabled={deleteMutation.isPending}
                className="w-full sm:w-auto px-4 py-2.5 min-h-11 bg-[hsl(var(--error))] text-white rounded-lg hover:bg-[hsl(var(--error)/0.9)] active:scale-95 transition-all text-sm disabled:opacity-50"
              >
                {deleteMutation.isPending ? t("common:loading") : t("common:confirm")}
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
};

export default PromptManagement;
