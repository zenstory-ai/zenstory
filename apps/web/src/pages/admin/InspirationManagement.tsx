import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Edit,
  Trash2,
  X,
  AlertTriangle,
  Check,
  XCircle,
  Star,
  RotateCcw,
} from "lucide-react";
import { adminApi } from "../../lib/adminApi";
import type { AdminInspiration } from "../../types/admin";
import { AdminPageState, TouchCheckbox, AdminSelect } from "../../components/admin";
import { toast } from "../../lib/toast";
import { formatAdminDate, formatAdminDateTime } from "../../lib/dateUtils";

// Mobile card component for inspirations
const InspirationCard: React.FC<{
  inspiration: AdminInspiration;
  onReview: (inspiration: AdminInspiration, approve: boolean) => void;
  onEdit: (inspiration: AdminInspiration) => void;
  onDelete: (inspiration: AdminInspiration) => void;
  t: (key: string, options?: Record<string, unknown>) => string;
}> = ({ inspiration, onReview, onEdit, onDelete, t }) => {
  const statusColors = {
    pending: "bg-yellow-500/15 text-yellow-600 dark:text-yellow-400",
    approved: "bg-[hsl(var(--success)/0.15)] text-[hsl(var(--success))]",
    rejected: "bg-red-500/15 text-red-600 dark:text-red-400",
  };

  const sourceColors = {
    official: "bg-blue-500/15 text-blue-600 dark:text-blue-400",
    community: "bg-purple-500/15 text-purple-600 dark:text-purple-400",
  };

  return (
    <div className="admin-surface p-4 space-y-3">
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h3 className="font-medium text-[hsl(var(--text-primary))] truncate">
              {inspiration.name}
            </h3>
            {inspiration.is_featured && (
              <Star size={14} className="text-yellow-500 fill-yellow-500" />
            )}
          </div>
          <p className="text-sm text-[hsl(var(--text-secondary))] line-clamp-2 mt-1">
            {inspiration.description}
          </p>
        </div>
      </div>

      <div className="flex flex-wrap gap-1">
        {inspiration.tags.slice(0, 3).map((tag) => (
          <span
            key={tag}
            className="px-2 py-0.5 text-xs bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-secondary))] rounded"
          >
            {tag}
          </span>
        ))}
        {inspiration.tags.length > 3 && (
          <span className="px-2 py-0.5 text-xs bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-secondary))] rounded">
            +{inspiration.tags.length - 3}
          </span>
        )}
      </div>

      <div className="grid grid-cols-2 gap-2 text-sm">
        <div>
          <span className="text-[hsl(var(--text-secondary))]">{t("inspirations.status")}:</span>
          <span className={`ml-1 px-2 py-0.5 rounded text-xs font-medium ${statusColors[inspiration.status]}`}>
            {t(`inspirations.${inspiration.status}`)}
          </span>
        </div>
        <div>
          <span className="text-[hsl(var(--text-secondary))]">{t("inspirations.source")}:</span>
          <span className={`ml-1 px-2 py-0.5 rounded text-xs font-medium ${sourceColors[inspiration.source]}`}>
            {t(`inspirations.${inspiration.source}`)}
          </span>
        </div>
        <div>
          <span className="text-[hsl(var(--text-secondary))]">{t("inspirations.copies")}:</span>
          <span className="ml-1 text-[hsl(var(--text-primary))]">{inspiration.copy_count}</span>
        </div>
        <div>
          <span className="text-[hsl(var(--text-secondary))]">{t("inspirations.featured")}:</span>
          <span className="ml-1 text-[hsl(var(--text-primary))]">
            {inspiration.is_featured ? t("common:yes") : t("common:no")}
          </span>
        </div>
      </div>

      <div className="flex items-center justify-between pt-2 border-t border-[hsl(var(--separator-color))]">
        <span className="text-xs text-[hsl(var(--text-secondary))]">
          {formatAdminDate(inspiration.created_at)}
        </span>
        <div className="flex items-center gap-1">
          {inspiration.status === "pending" && (
            <>
              <button
                onClick={() => onReview(inspiration, true)}
                className="p-2 hover:bg-[hsl(var(--success)/0.1)] rounded transition-colors"
                title={t("inspirations.approve")}
              >
                <Check size={18} className="text-green-500" />
              </button>
              <button
                onClick={() => onReview(inspiration, false)}
                className="p-2 hover:bg-red-500/10 rounded transition-colors"
                title={t("inspirations.reject")}
              >
                <XCircle size={18} className="text-red-500" />
              </button>
            </>
          )}
          <button
            onClick={() => onEdit(inspiration)}
            className="p-2 hover:bg-[hsl(var(--bg-tertiary))] rounded transition-colors"
            title={t("common:edit")}
          >
            <Edit size={16} className="text-[hsl(var(--text-primary))]" />
          </button>
          <button
            onClick={() => onDelete(inspiration)}
            className="p-2 hover:bg-red-500/10 rounded transition-colors"
            title={t("common:delete")}
          >
            <Trash2 size={16} className="text-red-500" />
          </button>
        </div>
      </div>
    </div>
  );
};

export const InspirationManagement: React.FC = () => {
  const { t } = useTranslation(["admin", "common"]);
  const queryClient = useQueryClient();
  const [page, setPage] = useState(0);
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [sourceFilter, setSourceFilter] = useState<string>("");
  const [editingInspiration, setEditingInspiration] = useState<AdminInspiration | null>(null);
  const [deletingInspiration, setDeletingInspiration] = useState<AdminInspiration | null>(null);
  const [rejectingInspiration, setRejectingInspiration] = useState<AdminInspiration | null>(null);
  const [rejectionReason, setRejectionReason] = useState("");
  const [formData, setFormData] = useState({
    name: "",
    description: "",
    tags: "",
    is_featured: false,
  });
  const pageSize = 20;

  // Fetch inspirations list
  const { data, isLoading, isFetching, isError, error, refetch } = useQuery({
    queryKey: ["admin", "inspirations", page, statusFilter, sourceFilter],
    queryFn: () =>
      adminApi.getInspirations({
        status: statusFilter || undefined,
        source: sourceFilter || undefined,
        skip: page * pageSize,
        limit: pageSize,
      }),
    staleTime: 30 * 1000,
  });

  // Review inspiration mutation
  const reviewMutation = useMutation({
    mutationFn: ({ id, approve, reason }: { id: string; approve: boolean; reason?: string }) =>
      adminApi.reviewInspiration(id, approve, reason),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "inspirations"] });
      queryClient.invalidateQueries({ queryKey: ["admin", "dashboard", "stats"] });
      setRejectingInspiration(null);
      setRejectionReason("");
      toast.success(t("inspirations.reviewSuccess"));
    },
    onError: () => {
      toast.error(t("inspirations.reviewFailed"));
    },
  });

  // Update inspiration mutation
  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: string; data: { name?: string; description?: string; tags?: string[]; is_featured?: boolean } }) =>
      adminApi.updateInspiration(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "inspirations"] });
      setEditingInspiration(null);
      toast.success(t("inspirations.updateSuccess"));
    },
    onError: () => {
      toast.error(t("inspirations.updateFailed"));
    },
  });

  // Delete inspiration mutation
  const deleteMutation = useMutation({
    mutationFn: (id: string) => adminApi.deleteInspiration(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "inspirations"] });
      queryClient.invalidateQueries({ queryKey: ["admin", "dashboard", "stats"] });
      setDeletingInspiration(null);
      toast.success(t("inspirations.deleteSuccess"));
    },
    onError: () => {
      toast.error(t("inspirations.deleteFailed"));
    },
  });

  const inspirations = data?.items ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.ceil(total / pageSize);
  const queryErrorText = error instanceof Error && error.message
    ? error.message
    : t("common:error");

  const handleReview = (inspiration: AdminInspiration, approve: boolean) => {
    if (approve) {
      reviewMutation.mutate({ id: inspiration.id, approve: true });
    } else {
      setRejectingInspiration(inspiration);
    }
  };

  const handleRejectConfirm = () => {
    if (!rejectingInspiration) return;
    const trimmedReason = rejectionReason.trim();
    if (!trimmedReason) {
      toast.error(t("inspirations.rejectReasonRequired"));
      return;
    }
    reviewMutation.mutate({
      id: rejectingInspiration.id,
      approve: false,
      reason: trimmedReason,
    });
  };

  const handleEditClick = (inspiration: AdminInspiration) => {
    setEditingInspiration(inspiration);
    setFormData({
      name: inspiration.name,
      description: inspiration.description ?? "",
      tags: inspiration.tags.join(", "),
      is_featured: inspiration.is_featured,
    });
  };

  const handleDeleteClick = (inspiration: AdminInspiration) => {
    setDeletingInspiration(inspiration);
  };

  const handleSave = () => {
    if (!editingInspiration) return;
    updateMutation.mutate({
      id: editingInspiration.id,
      data: {
        name: formData.name,
        description: formData.description,
        tags: formData.tags.split(",").map((t) => t.trim()).filter(Boolean),
        is_featured: formData.is_featured,
      },
    });
  };

  const handleDeleteConfirm = () => {
    if (!deletingInspiration) return;
    deleteMutation.mutate(deletingInspiration.id);
  };

  const formatDate = (dateStr: string | null | undefined) => formatAdminDateTime(dateStr);

  const statusColors = {
    pending: "bg-yellow-500/15 text-yellow-600 dark:text-yellow-400",
    approved: "bg-[hsl(var(--success)/0.15)] text-[hsl(var(--success))]",
    rejected: "bg-red-500/15 text-red-600 dark:text-red-400",
  };

  const sourceColors = {
    official: "bg-blue-500/15 text-blue-600 dark:text-blue-400",
    community: "bg-purple-500/15 text-purple-600 dark:text-purple-400",
  };

  return (
    <div className="admin-page admin-page-fluid">
      <div>
        <h1 className="admin-page-title">
          {t("inspirations.title")}
        </h1>
        <p className="admin-page-subtitle">
          {t("inspirations.subtitle")}
        </p>
      </div>

      {/* Filters */}
      <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2">
        <AdminSelect
          value={statusFilter}
          onChange={(e) => {
            setStatusFilter(e.target.value);
            setPage(0);
          }}
          className="text-[hsl(var(--text-primary))]"
        >
          <option value="">{t("inspirations.allStatus")}</option>
          <option value="pending">{t("inspirations.pending")}</option>
          <option value="approved">{t("inspirations.approved")}</option>
          <option value="rejected">{t("inspirations.rejected")}</option>
        </AdminSelect>
        <AdminSelect
          value={sourceFilter}
          onChange={(e) => {
            setSourceFilter(e.target.value);
            setPage(0);
          }}
          className="text-[hsl(var(--text-primary))]"
        >
          <option value="">{t("inspirations.allSources")}</option>
          <option value="official">{t("inspirations.official")}</option>
          <option value="community">{t("inspirations.community")}</option>
        </AdminSelect>
        {(statusFilter || sourceFilter) && (
          <button
            onClick={() => {
              setStatusFilter("");
              setSourceFilter("");
              setPage(0);
            }}
            className="w-full sm:w-auto px-4 py-2.5 min-h-11 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg hover:bg-[hsl(var(--bg-tertiary))] active:scale-95 transition-all text-[hsl(var(--text-primary))] flex items-center justify-center gap-2"
          >
            <RotateCcw size={16} />
            {t("common:reset")}
          </button>
        )}
      </div>

      {/* Inspirations table/cards */}
      <AdminPageState
        isLoading={isLoading}
        isFetching={isFetching}
        isError={isError}
        isEmpty={inspirations.length === 0}
        loadingText={t("common:loading")}
        errorText={queryErrorText}
        emptyText={t("common:noData")}
        retryText={t("common:retry")}
        onRetry={() => {
          void refetch();
        }}
      >
        <>
          {/* Mobile card view */}
          <div className="space-y-3 lg:hidden">
            {inspirations.map((inspiration) => (
              <InspirationCard
                key={inspiration.id}
                inspiration={inspiration}
                onReview={handleReview}
                onEdit={handleEditClick}
                onDelete={handleDeleteClick}
                t={t}
              />
            ))}
          </div>

          {/* Desktop table view */}
          <div className="hidden lg:block admin-table-shell">
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-[hsl(var(--separator-color))]">
                    <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                      {t("inspirations.name")}
                    </th>
                    <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                      {t("inspirations.status")}
                    </th>
                    <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                      {t("inspirations.source")}
                    </th>
                    <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                      {t("inspirations.featured")}
                    </th>
                    <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                      {t("inspirations.copies")}
                    </th>
                    <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                      {t("inspirations.createdAt")}
                    </th>
                    <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                      {t("inspirations.actions")}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {inspirations.map((inspiration) => (
                    <tr
                      key={inspiration.id}
                      className="border-b border-[hsl(var(--separator-color))] hover:bg-[hsl(var(--bg-tertiary))]"
                    >
                      <td className="px-4 py-3 text-sm">
                        <div className="flex items-center gap-2">
                          <span className="text-[hsl(var(--text-primary))] font-medium">
                            {inspiration.name}
                          </span>
                          {inspiration.is_featured && (
                            <Star size={14} className="text-yellow-500 fill-yellow-500" />
                          )}
                        </div>
                        <p className="text-xs text-[hsl(var(--text-secondary))] line-clamp-1 mt-0.5">
                          {inspiration.description}
                        </p>
                      </td>
                      <td className="px-4 py-3 text-sm">
                        <span className={`px-2 py-1 rounded text-xs font-medium ${statusColors[inspiration.status]}`}>
                          {t(`inspirations.${inspiration.status}`)}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-sm">
                        <span className={`px-2 py-1 rounded text-xs font-medium ${sourceColors[inspiration.source]}`}>
                          {t(`inspirations.${inspiration.source}`)}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-sm text-[hsl(var(--text-primary))]">
                        {inspiration.is_featured ? t("common:yes") : t("common:no")}
                      </td>
                      <td className="px-4 py-3 text-sm text-[hsl(var(--text-primary))]">
                        {inspiration.copy_count}
                      </td>
                      <td className="px-4 py-3 text-sm text-[hsl(var(--text-secondary))]">
                        {formatDate(inspiration.created_at)}
                      </td>
                      <td className="px-4 py-3 text-sm">
                        <div className="flex items-center gap-1">
                          {inspiration.status === "pending" && (
                            <>
                              <button
                                onClick={() => handleReview(inspiration, true)}
                                className="p-1.5 hover:bg-[hsl(var(--success)/0.1)] rounded transition-colors"
                                title={t("inspirations.approve")}
                              >
                                <Check size={16} className="text-green-500" />
                              </button>
                              <button
                                onClick={() => handleReview(inspiration, false)}
                                className="p-1.5 hover:bg-red-500/10 rounded transition-colors"
                                title={t("inspirations.reject")}
                              >
                                <XCircle size={16} className="text-red-500" />
                              </button>
                            </>
                          )}
                          <button
                            onClick={() => handleEditClick(inspiration)}
                            className="p-1.5 hover:bg-[hsl(var(--bg-tertiary))] rounded transition-colors"
                            title={t("common:edit")}
                          >
                            <Edit size={16} className="text-[hsl(var(--text-primary))]" />
                          </button>
                          <button
                            onClick={() => handleDeleteClick(inspiration)}
                            className="p-1.5 hover:bg-red-500/10 rounded transition-colors"
                            title={t("common:delete")}
                          >
                            <Trash2 size={16} className="text-red-500" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      </AdminPageState>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex flex-col sm:flex-row items-center justify-between gap-4">
          <div className="text-sm text-[hsl(var(--text-secondary))] text-center sm:text-left">
            {t("common:showing", {
              from: page * pageSize + 1,
              to: Math.min((page + 1) * pageSize, total),
              total,
            })}
          </div>
          <div className="flex items-center gap-2 w-full sm:w-auto">
            <button
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0}
              className="flex-1 sm:flex-none px-4 py-2.5 min-h-11 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg hover:bg-[hsl(var(--bg-tertiary))] active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed transition-all text-sm"
            >
              {t("common:previous")}
            </button>
            <span className="text-sm text-[hsl(var(--text-primary))] hidden sm:inline">
              {page + 1} / {totalPages}
            </span>
            <button
              onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
              disabled={page >= totalPages - 1}
              className="flex-1 sm:flex-none px-4 py-2.5 min-h-11 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg hover:bg-[hsl(var(--bg-tertiary))] active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed transition-all text-sm"
            >
              {t("common:next")}
            </button>
          </div>
        </div>
      )}

      {/* Edit Modal */}
      {editingInspiration && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50">
          <div className="bg-[hsl(var(--bg-primary))] border border-[hsl(var(--separator-color))] rounded-lg shadow-xl w-full max-w-md">
            <div className="flex items-center justify-between px-4 sm:px-6 py-4 border-b border-[hsl(var(--separator-color))]">
              <h2 className="text-lg font-semibold text-[hsl(var(--text-primary))]">
                {t("inspirations.editTitle")}
              </h2>
              <button
                onClick={() => setEditingInspiration(null)}
                disabled={updateMutation.isPending}
                aria-label={t("common:close")}
                className="p-2.5 hover:bg-[hsl(var(--bg-tertiary))] rounded transition-colors"
              >
                <X size={20} className="text-[hsl(var(--text-secondary))]" />
              </button>
            </div>

            <div className="px-4 sm:px-6 py-4 space-y-4 max-h-[60vh] overflow-y-auto">
              <div>
                <label className="block text-sm font-medium text-[hsl(var(--text-primary))] mb-1">
                  {t("inspirations.name")}
                </label>
                <input
                  type="text"
                  value={formData.name}
                  onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                  className="w-full px-3 py-2.5 min-h-11 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg focus:outline-none focus:ring-2 focus:ring-[hsl(var(--accent-primary))] text-[hsl(var(--text-primary))]"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-[hsl(var(--text-primary))] mb-1">
                  {t("inspirations.description")}
                </label>
                <textarea
                  value={formData.description}
                  onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                  rows={3}
                  className="w-full px-3 py-2.5 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg focus:outline-none focus:ring-2 focus:ring-[hsl(var(--accent-primary))] text-[hsl(var(--text-primary))] resize-none"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-[hsl(var(--text-primary))] mb-1">
                  {t("inspirations.tags")}
                </label>
                <input
                  type="text"
                  value={formData.tags}
                  onChange={(e) => setFormData({ ...formData, tags: e.target.value })}
                  placeholder={t("inspirations.tagsPlaceholder")}
                  className="w-full px-3 py-2.5 min-h-11 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg focus:outline-none focus:ring-2 focus:ring-[hsl(var(--accent-primary))] text-[hsl(var(--text-primary))] placeholder-[hsl(var(--text-secondary))]"
                />
              </div>

              <TouchCheckbox
                checked={formData.is_featured}
                onChange={(checked) => setFormData({ ...formData, is_featured: checked })}
                label={t("inspirations.setFeatured")}
              />
            </div>

            <div className="flex flex-col-reverse sm:flex-row items-center justify-end gap-2 px-4 sm:px-6 py-4 border-t border-[hsl(var(--separator-color))]">
              <button
                onClick={() => setEditingInspiration(null)}
                disabled={updateMutation.isPending}
                className="w-full sm:w-auto px-4 py-2.5 min-h-11 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg hover:bg-[hsl(var(--bg-tertiary))] active:scale-95 transition-all text-sm text-[hsl(var(--text-primary))] disabled:opacity-50"
              >
                {t("common:cancel")}
              </button>
              <button
                onClick={handleSave}
                disabled={updateMutation.isPending}
                className="w-full sm:w-auto px-4 py-2.5 min-h-11 bg-[hsl(var(--accent-primary))] text-white rounded-lg hover:opacity-90 active:scale-95 transition-all text-sm disabled:opacity-50"
              >
                {updateMutation.isPending ? t("common:loading") : t("common:save")}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete Confirmation Modal */}
      {deletingInspiration && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50">
          <div className="bg-[hsl(var(--bg-primary))] border border-[hsl(var(--separator-color))] rounded-lg shadow-xl w-full max-w-md">
            <div className="flex items-center gap-3 px-4 sm:px-6 py-4 border-b border-[hsl(var(--separator-color))]">
              <AlertTriangle size={24} className="text-red-500" />
              <h2 className="text-lg font-semibold text-[hsl(var(--text-primary))]">
                {t("inspirations.deleteTitle")}
              </h2>
            </div>

            <div className="px-4 sm:px-6 py-4">
              <p className="text-[hsl(var(--text-secondary))]">
                {t("inspirations.deleteConfirm")}
              </p>
              <p className="mt-2 text-sm text-[hsl(var(--text-primary))] font-medium">
                {deletingInspiration.name}
              </p>
            </div>

            <div className="flex flex-col-reverse sm:flex-row items-center justify-end gap-2 px-4 sm:px-6 py-4 border-t border-[hsl(var(--separator-color))]">
              <button
                onClick={() => setDeletingInspiration(null)}
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
                {deleteMutation.isPending ? t("common:loading") : t("inspirations.deleteTitle")}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Reject Reason Modal */}
      {rejectingInspiration && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50">
          <div className="bg-[hsl(var(--bg-primary))] border border-[hsl(var(--separator-color))] rounded-lg shadow-xl w-full max-w-md">
            <div className="flex items-center justify-between px-4 sm:px-6 py-4 border-b border-[hsl(var(--separator-color))]">
              <h2 className="text-lg font-semibold text-[hsl(var(--text-primary))]">
                {t("inspirations.rejectTitle")}
              </h2>
              <button
                onClick={() => {
                  setRejectingInspiration(null);
                  setRejectionReason("");
                }}
                disabled={reviewMutation.isPending}
                aria-label={t("common:close")}
                className="p-2.5 hover:bg-[hsl(var(--bg-tertiary))] rounded transition-colors"
              >
                <X size={20} className="text-[hsl(var(--text-secondary))]" />
              </button>
            </div>

            <div className="px-4 sm:px-6 py-4 space-y-4">
              <p className="text-[hsl(var(--text-secondary))]">
                {t("inspirations.rejecting")}: <span className="text-[hsl(var(--text-primary))] font-medium">{rejectingInspiration.name}</span>
              </p>
              <div>
                <label className="block text-sm font-medium text-[hsl(var(--text-primary))] mb-1">
                  {t("inspirations.rejectReason")}
                </label>
                <textarea
                  value={rejectionReason}
                  onChange={(e) => setRejectionReason(e.target.value)}
                  rows={3}
                  placeholder={t("inspirations.rejectReasonPlaceholder")}
                  className="w-full px-3 py-2.5 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg focus:outline-none focus:ring-2 focus:ring-[hsl(var(--accent-primary))] text-[hsl(var(--text-primary))] placeholder-[hsl(var(--text-secondary))] resize-none"
                />
              </div>
            </div>

            <div className="flex flex-col-reverse sm:flex-row items-center justify-end gap-2 px-4 sm:px-6 py-4 border-t border-[hsl(var(--separator-color))]">
              <button
                onClick={() => {
                  setRejectingInspiration(null);
                  setRejectionReason("");
                }}
                disabled={reviewMutation.isPending}
                className="w-full sm:w-auto px-4 py-2.5 min-h-11 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg hover:bg-[hsl(var(--bg-tertiary))] active:scale-95 transition-all text-sm text-[hsl(var(--text-primary))] disabled:opacity-50"
              >
                {t("common:cancel")}
              </button>
              <button
                onClick={handleRejectConfirm}
                disabled={reviewMutation.isPending}
                className="w-full sm:w-auto px-4 py-2.5 min-h-11 bg-[hsl(var(--error))] text-white rounded-lg hover:bg-[hsl(var(--error)/0.9)] active:scale-95 transition-all text-sm disabled:opacity-50"
              >
                {reviewMutation.isPending ? t("common:loading") : t("inspirations.confirmReject")}
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
};

export default InspirationManagement;
