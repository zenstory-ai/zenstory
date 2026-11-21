import { Modal } from "../../components/ui/Modal";
import { Button } from "../../components/ui/Button";
import { IconButton } from "../../components/ui/IconButton";
import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Plus, FileText, Edit, Trash2, RefreshCw, AlertTriangle } from "lucide-react";
import { AdminPageState } from "../../components/admin";
import { adminApi } from "../../lib/adminApi";
import { toast } from "../../lib/toast";
import { formatAdminDateTime } from "../../lib/dateUtils";

export const PromptManagement: React.FC = () => {
  const { t } = useTranslation(["admin", "common"]);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
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
    },
    onError: () => {
      toast.error(t("prompts.reloadFailed"));
    },
  });

  const prompts = data ?? [];
  const queryErrorText = error instanceof Error && error.message
    ? error.message
    : t("common:error");

  const formatDate = (dateStr?: string) => formatAdminDateTime(dateStr);

  const handleCardClick = (projectType: string) => {
    navigate(`/admin/prompts/${encodeURIComponent(projectType)}`);
  };

  const handleCreateClick = () => {
    navigate("/admin/prompts/new");
  };

  const handleReload = () => {
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
      <div className="flex flex-col items-start justify-between gap-4 lg:flex-row lg:items-center">
        <h1 className="admin-page-title">
          {t("prompts.title")}
        </h1>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            onClick={handleReload}
            disabled={reloadMutation.isPending}
            variant="secondary"
            size="touch"
            leftIcon={<RefreshCw size={16} className={reloadMutation.isPending ? "animate-spin" : ""} />}
          >
            {t("prompts.reload")}
          </Button>
          <Button
            onClick={handleCreateClick}
            size="touch"
            leftIcon={<Plus size={16} />}
          >
            {t("prompts.create")}
          </Button>
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
                        ? "bg-[hsl(var(--success)/0.15)] text-[hsl(var(--success-text))]"
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
                <IconButton
                  onClick={(e) => {
                    e.stopPropagation();
                    handleCardClick(prompt.project_type);
                  }}
                  tone="strong"
                  label={t("prompts.edit")}
                  icon={<Edit size={16} />}
                />
                <IconButton
                  onClick={(e) => {
                    e.stopPropagation();
                    handleDeletePrompt(prompt.project_type);
                  }}
                  disabled={deleteMutation.isPending}
                  tone="danger"
                  label={t("prompts.delete")}
                  icon={<Trash2 size={16} />}
                />
              </div>
            </div>
          ))}
        </div>
      </AdminPageState>

      {/* Delete Confirmation Modal */}
      {deletingPrompt && (
        <Modal
          open
          ariaLabel={t("prompts.delete")}
          onClose={() => setDeletingPrompt(null)}
          closeOnEscape={!(deleteMutation.isPending)}
          closeOnBackdropClick={false}
          showCloseButton={false}
          bodyClassName="p-0"
          className="admin-shell rounded-xl!"
          size="md"
        >
          <div className="flex items-center gap-3 px-4 sm:px-6 py-4 border-b border-[hsl(var(--separator-color))]">
            <AlertTriangle size={24} className="text-[hsl(var(--error-text))]" />
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
            <Button
              type="button"
              variant="secondary"
              size="touch"
              onClick={() => setDeletingPrompt(null)}
              disabled={deleteMutation.isPending}
              className="w-full sm:w-auto"
            >
              {t("common:cancel")}
            </Button>
            <Button
              type="button"
              variant="danger"
              size="touch"
              onClick={handleDeleteConfirm}
              disabled={deleteMutation.isPending}
              className="w-full sm:w-auto"
            >
              {deleteMutation.isPending ? t("common:loading") : t("common:confirm")}
            </Button>
          </div>
        </Modal>
      )}

    </div>
  );
};

export default PromptManagement;
