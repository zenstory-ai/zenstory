import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Plus,
  Copy,
  ToggleLeft,
  ToggleRight,
  X,
  Layers,
  RotateCcw,
} from "lucide-react";
import { AdminPageState, AdminSelect } from "../../components/admin";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { adminApi, type RedemptionCode } from "../../lib/adminApi";
import { getLocaleCode } from "../../lib/i18n-helpers";
import { toast } from "../../lib/toast";

type CodeType = "single_use" | "multi_use";

// Mobile card component for redemption codes
const CodeCard: React.FC<{
  code: RedemptionCode;
  onToggle: (code: RedemptionCode) => void;
  onCopy: (codeStr: string) => void;
  t: (key: string, options?: Record<string, unknown>) => string;
  pending: boolean;
}> = ({ code, onToggle, onCopy, t, pending }) => {
  return (
    <div className="admin-surface p-4 space-y-3">
      <div className="flex items-center justify-between">
        <span className="font-mono text-sm text-[hsl(var(--text-primary))] bg-[hsl(var(--bg-tertiary))] px-2 py-1 rounded">
          {code.code}
        </span>
        <button
          onClick={() => onCopy(code.code)}
          className="p-2 hover:bg-[hsl(var(--bg-tertiary))] rounded transition-colors"
          title={t("codes.copyCode")}
        >
          <Copy size={16} className="text-[hsl(var(--text-secondary))]" />
        </button>
      </div>
      <div className="grid grid-cols-2 gap-2 text-sm">
        <div>
          <span className="text-[hsl(var(--text-secondary))]">{t("codes.tier")}:</span>
          <span className="ml-1 text-[hsl(var(--text-primary))] font-medium">{code.tier}</span>
        </div>
        <div>
          <span className="text-[hsl(var(--text-secondary))]">{t("codes.duration")}:</span>
          <span className="ml-1 text-[hsl(var(--text-primary))]">{code.duration_days}{t("codes.days")}</span>
        </div>
        <div>
          <span className="text-[hsl(var(--text-secondary))]">{t("codes.type")}:</span>
          <span className="ml-1 text-[hsl(var(--text-primary))]">{code.code_type}</span>
        </div>
        <div>
          <span className="text-[hsl(var(--text-secondary))]">{t("codes.uses")}:</span>
          <span className="ml-1 text-[hsl(var(--text-primary))]">
            {code.current_uses}/{code.max_uses ?? "∞"}
          </span>
        </div>
      </div>
      <div className="flex items-center justify-between pt-2 border-t border-[hsl(var(--separator-color))]">
        <span
          className={`px-2 py-1 rounded text-xs font-medium ${
            code.is_active
              ? "bg-[hsl(var(--success)/0.15)] text-[hsl(var(--success))]"
              : "bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-secondary))]"
          }`}
        >
          {code.is_active ? t("codes.active") : t("codes.inactive")}
        </span>
        <button
          onClick={() => onToggle(code)}
          disabled={pending}
          className={`p-2 rounded transition-colors ${
            code.is_active
              ? "hover:bg-[hsl(var(--error)/0.1)]"
              : "hover:bg-[hsl(var(--success)/0.1)]"
          } disabled:opacity-50`}
          title={code.is_active ? t("codes.deactivate") : t("codes.activate")}
        >
          {code.is_active ? (
            <ToggleRight size={20} className="text-red-500" />
          ) : (
            <ToggleLeft size={20} className="text-green-500" />
          )}
        </button>
      </div>
    </div>
  );
};

export const CodeManagement: React.FC = () => {
  const { t } = useTranslation(["admin", "common"]);
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [tierFilter, setTierFilter] = useState<string>("");
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showBatchModal, setShowBatchModal] = useState(false);
  const [codeToDeactivate, setCodeToDeactivate] = useState<RedemptionCode | null>(null);
  const [createFormData, setCreateFormData] = useState<{
    tier: string;
    duration_days: number;
    code_type: CodeType;
    max_uses: number;
    notes: string;
  }>({
    tier: "pro",
    duration_days: 30,
    code_type: "single_use",
    max_uses: 1,
    notes: "",
  });
  const [batchFormData, setBatchFormData] = useState<{
    tier: string;
    duration_days: number;
    count: number;
    code_type: CodeType;
    notes: string;
  }>({
    tier: "pro",
    duration_days: 30,
    count: 10,
    code_type: "single_use",
    notes: "",
  });
  const pageSize = 20;

  // Fetch codes list
  const { data, isLoading, isFetching, isError, error, refetch } = useQuery({
    queryKey: ["admin", "codes", page, tierFilter, statusFilter],
    queryFn: () =>
      adminApi.getCodes({
        page,
        page_size: pageSize,
        tier: tierFilter || undefined,
        is_active: statusFilter === "" ? undefined : statusFilter === "true",
      }),
    staleTime: 30 * 1000,
  });

  // Create single code mutation
  const createMutation = useMutation({
    mutationFn: (data: typeof createFormData) => adminApi.createCode(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "codes"] });
      setShowCreateModal(false);
      setCreateFormData({
        tier: "pro",
        duration_days: 30,
        code_type: "single_use",
        max_uses: 1,
        notes: "",
      });
      toast.success(t("codes.createSuccess"));
    },
    onError: () => {
      toast.error(t("codes.createFailed"));
    },
  });

  // Batch create codes mutation
  const batchCreateMutation = useMutation({
    mutationFn: (data: typeof batchFormData) => adminApi.createCodesBatch(data),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["admin", "codes"] });
      setShowBatchModal(false);
      setBatchFormData({
        tier: "pro",
        duration_days: 30,
        count: 10,
        code_type: "single_use",
        notes: "",
      });
      toast.success(
        t("codes.batchCreateSuccess", { count: result.count ?? result.created ?? batchFormData.count }),
      );
    },
    onError: () => {
      toast.error(t("codes.batchCreateFailed"));
    },
  });

  // Update code mutation (toggle status)
  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: string; data: { is_active: boolean } }) =>
      adminApi.updateCode(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "codes"] });
      toast.success(t("codes.updateSuccess"));
    },
    onError: () => {
      toast.error(t("codes.updateFailed"));
    },
  });

  const codes = data?.items ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.ceil(total / pageSize);
  const queryErrorText = error instanceof Error && error.message
    ? error.message
    : t("common:error");

  const handleToggleStatus = (code: RedemptionCode) => {
    if (code.is_active) {
      setCodeToDeactivate(code);
      return;
    }
    updateMutation.mutate({
      id: code.id,
      data: { is_active: true },
    });
  };

  const confirmDeactivation = () => {
    if (!codeToDeactivate) return;
    updateMutation.mutate(
      { id: codeToDeactivate.id, data: { is_active: false } },
      { onSuccess: () => setCodeToDeactivate(null) },
    );
  };

  const handleCopyCode = (codeStr: string) => {
    navigator.clipboard.writeText(codeStr);
    toast.success(t("codes.copied"));
  };

  const handleCreateSingle = () => {
    createMutation.mutate(createFormData);
  };

  const handleCreateBatch = () => {
    batchCreateMutation.mutate(batchFormData);
  };

  const formatDate = (dateStr: string) => {
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

  return (
    <div className="admin-page admin-page-fluid">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="admin-page-title">
            {t("codes.title")}
          </h1>
          <p className="admin-page-subtitle">
            {t("codes.subtitle")}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowCreateModal(true)}
            className="flex items-center gap-2 px-4 py-2.5 min-h-11 bg-[hsl(var(--accent-primary))] text-white rounded-lg hover:opacity-90 active:scale-95 transition-all"
          >
            <Plus size={18} />
            <span>{t("codes.create")}</span>
          </button>
          <button
            onClick={() => setShowBatchModal(true)}
            className="flex items-center gap-2 px-4 py-2.5 min-h-11 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg hover:bg-[hsl(var(--bg-tertiary))] active:scale-95 transition-all text-[hsl(var(--text-primary))]"
          >
            <Layers size={18} />
            <span>{t("codes.batchCreate")}</span>
          </button>
        </div>
      </div>

      {/* Filters */}
      <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2">
        <AdminSelect
          value={tierFilter}
          onChange={(e) => {
            setTierFilter(e.target.value);
            setPage(1);
          }}
          className="text-[hsl(var(--text-primary))]"
        >
          <option value="">{t("codes.allTiers")}</option>
          <option value="free">{t("codes.tierFree")}</option>
          <option value="pro">{t("codes.tierPro")}</option>
        </AdminSelect>
        <AdminSelect
          value={statusFilter}
          onChange={(e) => {
            setStatusFilter(e.target.value);
            setPage(1);
          }}
          className="text-[hsl(var(--text-primary))]"
        >
          <option value="">{t("codes.allStatus")}</option>
          <option value="true">{t("codes.activeOnly")}</option>
          <option value="false">{t("codes.inactiveOnly")}</option>
        </AdminSelect>
        {(tierFilter || statusFilter) && (
          <button
            onClick={() => {
              setTierFilter("");
              setStatusFilter("");
              setPage(1);
            }}
            className="w-full sm:w-auto px-4 py-2.5 min-h-11 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg hover:bg-[hsl(var(--bg-tertiary))] active:scale-95 transition-all text-[hsl(var(--text-primary))] flex items-center justify-center gap-2"
          >
            <RotateCcw size={16} />
            {t("common:reset")}
          </button>
        )}
      </div>

      {/* Codes table/cards */}
      <AdminPageState
        isLoading={isLoading}
        isFetching={isFetching}
        isError={isError}
        isEmpty={codes.length === 0}
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
            {codes.map((code) => (
              <CodeCard
                key={code.id}
                code={code}
                onToggle={handleToggleStatus}
                onCopy={handleCopyCode}
                t={t}
                pending={Boolean(updateMutation.isPending && updateMutation.variables?.id === code.id)}
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
                      {t("codes.code")}
                    </th>
                    <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                      {t("codes.tier")}
                    </th>
                    <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                      {t("codes.duration")}
                    </th>
                    <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                      {t("codes.type")}
                    </th>
                    <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                      {t("codes.uses")}
                    </th>
                    <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                      {t("codes.status")}
                    </th>
                    <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                      {t("codes.createdAt")}
                    </th>
                    <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                      {t("codes.actions")}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {codes.map((code) => (
                    <tr
                      key={code.id}
                      className="border-b border-[hsl(var(--separator-color))] hover:bg-[hsl(var(--bg-tertiary))]"
                    >
                      <td className="px-4 py-3 text-sm">
                        <div className="flex items-center gap-2">
                          <span className="font-mono text-[hsl(var(--text-primary))] bg-[hsl(var(--bg-tertiary))] px-2 py-0.5 rounded">
                            {code.code}
                          </span>
                          <button
                            onClick={() => handleCopyCode(code.code)}
                            className="p-1 hover:bg-[hsl(var(--bg-tertiary))] rounded transition-colors"
                            title={t("codes.copyCode")}
                          >
                            <Copy size={14} className="text-[hsl(var(--text-secondary))]" />
                          </button>
                        </div>
                      </td>
                      <td className="px-4 py-3 text-sm text-[hsl(var(--text-primary))] font-medium">
                        {code.tier}
                      </td>
                      <td className="px-4 py-3 text-sm text-[hsl(var(--text-primary))]">
                        {code.duration_days}{t("codes.days")}
                      </td>
                      <td className="px-4 py-3 text-sm text-[hsl(var(--text-primary))]">
                        {code.code_type}
                      </td>
                      <td className="px-4 py-3 text-sm text-[hsl(var(--text-primary))]">
                        {code.current_uses}/{code.max_uses ?? "∞"}
                      </td>
                      <td className="px-4 py-3 text-sm">
                        <span
                          className={`px-2 py-1 rounded text-xs font-medium ${
                            code.is_active
                              ? "bg-[hsl(var(--success)/0.15)] text-[hsl(var(--success))]"
                              : "bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-secondary))]"
                          }`}
                        >
                          {code.is_active ? t("codes.active") : t("codes.inactive")}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-sm text-[hsl(var(--text-secondary))]">
                        {formatDate(code.created_at)}
                      </td>
                      <td className="px-4 py-3 text-sm">
                        <button
                          onClick={() => handleToggleStatus(code)}
                          disabled={Boolean(updateMutation.isPending && updateMutation.variables?.id === code.id)}
                          className={`p-1.5 rounded transition-colors ${
                            code.is_active
                              ? "hover:bg-[hsl(var(--error)/0.1)]"
                              : "hover:bg-[hsl(var(--success)/0.1)]"
                          } disabled:opacity-50`}
                          title={code.is_active ? t("codes.deactivate") : t("codes.activate")}
                        >
                          {code.is_active ? (
                            <ToggleRight size={18} className="text-red-500" />
                          ) : (
                            <ToggleLeft size={18} className="text-green-500" />
                          )}
                        </button>
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
              from: (page - 1) * pageSize + 1,
              to: Math.min(page * pageSize, total),
              total,
            })}
          </div>
          <div className="flex items-center gap-2 w-full sm:w-auto">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page === 1}
              className="flex-1 sm:flex-none px-4 py-2.5 min-h-11 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg hover:bg-[hsl(var(--bg-tertiary))] active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed transition-all text-sm"
            >
              {t("common:previous")}
            </button>
            <span className="text-sm text-[hsl(var(--text-primary))] hidden sm:inline">
              {page} / {totalPages}
            </span>
            <button
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page >= totalPages}
              className="flex-1 sm:flex-none px-4 py-2.5 min-h-11 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg hover:bg-[hsl(var(--bg-tertiary))] active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed transition-all text-sm"
            >
              {t("common:next")}
            </button>
          </div>
        </div>
      )}

      <ConfirmDialog
        open={Boolean(codeToDeactivate)}
        onClose={() => setCodeToDeactivate(null)}
        onConfirm={confirmDeactivation}
        title={t("codes.deactivateConfirmTitle", "禁用兑换码")}
        message={`${codeToDeactivate?.code ?? ""}: ${t(
          "codes.deactivateConfirmImpact",
          "禁用后无法再兑换，重新启用即可恢复。",
        )}`}
        confirmLabel={t("codes.confirmDeactivate", "禁用")}
        cancelLabel={t("common:cancel")}
        variant="danger"
        loading={Boolean(updateMutation.isPending && updateMutation.variables?.id === codeToDeactivate?.id)}
      />

      {/* Create Single Code Modal */}
      {showCreateModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50">
          <div className="bg-[hsl(var(--bg-primary))] border border-[hsl(var(--separator-color))] rounded-lg shadow-xl w-full max-w-md">
            <div className="flex items-center justify-between px-4 sm:px-6 py-4 border-b border-[hsl(var(--separator-color))]">
              <h2 className="text-lg font-semibold text-[hsl(var(--text-primary))]">
                {t("codes.createTitle")}
              </h2>
              <button
                onClick={() => setShowCreateModal(false)}
                className="p-2.5 hover:bg-[hsl(var(--bg-tertiary))] rounded transition-colors"
              >
                <X size={20} className="text-[hsl(var(--text-secondary))]" />
              </button>
            </div>

            <div className="px-4 sm:px-6 py-4 space-y-4 max-h-[60vh] overflow-y-auto">
              <div>
                <label className="block text-sm font-medium text-[hsl(var(--text-primary))] mb-1">
                  {t("codes.tier")}
                </label>
                <AdminSelect
                  fullWidth
                  value={createFormData.tier}
                  onChange={(e) => setCreateFormData({ ...createFormData, tier: e.target.value })}
                  className="text-[hsl(var(--text-primary))]"
                >
                  <option value="free">{t("codes.tierFree")}</option>
                  <option value="pro">{t("codes.tierPro")}</option>
                </AdminSelect>
              </div>

              <div>
                <label className="block text-sm font-medium text-[hsl(var(--text-primary))] mb-1">
                  {t("codes.duration")}
                </label>
                <input
                  type="number"
                  min={1}
                  value={createFormData.duration_days}
                  onChange={(e) => setCreateFormData({ ...createFormData, duration_days: parseInt(e.target.value) || 30 })}
                  className="w-full px-3 py-2.5 min-h-11 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg focus:outline-none focus:ring-2 focus:ring-[hsl(var(--accent-primary))] text-[hsl(var(--text-primary))]"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-[hsl(var(--text-primary))] mb-1">
                  {t("codes.type")}
                </label>
                <AdminSelect
                  fullWidth
                  value={createFormData.code_type}
                  onChange={(e) =>
                    setCreateFormData({
                      ...createFormData,
                      code_type: e.target.value as CodeType,
                    })
                  }
                  className="text-[hsl(var(--text-primary))]"
                >
                  <option value="single_use">{t("codes.typeSingle")}</option>
                  <option value="multi_use">{t("codes.typeMulti")}</option>
                </AdminSelect>
              </div>

              <div>
                <label className="block text-sm font-medium text-[hsl(var(--text-primary))] mb-1">
                  {t("codes.maxUses")}
                </label>
                <input
                  type="number"
                  min={1}
                  value={createFormData.max_uses}
                  onChange={(e) => setCreateFormData({ ...createFormData, max_uses: parseInt(e.target.value) || 1 })}
                  className="w-full px-3 py-2.5 min-h-11 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg focus:outline-none focus:ring-2 focus:ring-[hsl(var(--accent-primary))] text-[hsl(var(--text-primary))]"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-[hsl(var(--text-primary))] mb-1">
                  {t("codes.notes")}
                </label>
                <textarea
                  value={createFormData.notes}
                  onChange={(e) => setCreateFormData({ ...createFormData, notes: e.target.value })}
                  rows={2}
                  className="w-full px-3 py-2.5 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg focus:outline-none focus:ring-2 focus:ring-[hsl(var(--accent-primary))] text-[hsl(var(--text-primary))] resize-none"
                  placeholder={t("codes.notesPlaceholder")}
                />
              </div>
            </div>

            <div className="flex flex-col-reverse sm:flex-row items-center justify-end gap-2 px-4 sm:px-6 py-4 border-t border-[hsl(var(--separator-color))]">
              <button
                onClick={() => setShowCreateModal(false)}
                disabled={createMutation.isPending}
                className="w-full sm:w-auto px-4 py-2.5 min-h-11 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg hover:bg-[hsl(var(--bg-tertiary))] active:scale-95 transition-all text-sm text-[hsl(var(--text-primary))] disabled:opacity-50"
              >
                {t("common:cancel")}
              </button>
              <button
                onClick={handleCreateSingle}
                disabled={createMutation.isPending}
                className="w-full sm:w-auto px-4 py-2.5 min-h-11 bg-[hsl(var(--accent-primary))] text-white rounded-lg hover:opacity-90 active:scale-95 transition-all text-sm disabled:opacity-50"
              >
                {createMutation.isPending ? t("common:loading") : t("codes.create")}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Batch Create Modal */}
      {showBatchModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50">
          <div className="bg-[hsl(var(--bg-primary))] border border-[hsl(var(--separator-color))] rounded-lg shadow-xl w-full max-w-md">
            <div className="flex items-center justify-between px-4 sm:px-6 py-4 border-b border-[hsl(var(--separator-color))]">
              <h2 className="text-lg font-semibold text-[hsl(var(--text-primary))]">
                {t("codes.batchCreateTitle")}
              </h2>
              <button
                onClick={() => setShowBatchModal(false)}
                className="p-2.5 hover:bg-[hsl(var(--bg-tertiary))] rounded transition-colors"
              >
                <X size={20} className="text-[hsl(var(--text-secondary))]" />
              </button>
            </div>

            <div className="px-4 sm:px-6 py-4 space-y-4 max-h-[60vh] overflow-y-auto">
              <div>
                <label className="block text-sm font-medium text-[hsl(var(--text-primary))] mb-1">
                  {t("codes.tier")}
                </label>
                <AdminSelect
                  fullWidth
                  value={batchFormData.tier}
                  onChange={(e) => setBatchFormData({ ...batchFormData, tier: e.target.value })}
                  className="text-[hsl(var(--text-primary))]"
                >
                  <option value="free">{t("codes.tierFree")}</option>
                  <option value="pro">{t("codes.tierPro")}</option>
                </AdminSelect>
              </div>

              <div>
                <label className="block text-sm font-medium text-[hsl(var(--text-primary))] mb-1">
                  {t("codes.duration")}
                </label>
                <input
                  type="number"
                  min={1}
                  value={batchFormData.duration_days}
                  onChange={(e) => setBatchFormData({ ...batchFormData, duration_days: parseInt(e.target.value) || 30 })}
                  className="w-full px-3 py-2.5 min-h-11 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg focus:outline-none focus:ring-2 focus:ring-[hsl(var(--accent-primary))] text-[hsl(var(--text-primary))]"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-[hsl(var(--text-primary))] mb-1">
                  {t("codes.batchCount")}
                </label>
                <input
                  type="number"
                  min={1}
                  max={100}
                  value={batchFormData.count}
                  onChange={(e) => setBatchFormData({ ...batchFormData, count: parseInt(e.target.value) || 10 })}
                  className="w-full px-3 py-2.5 min-h-11 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg focus:outline-none focus:ring-2 focus:ring-[hsl(var(--accent-primary))] text-[hsl(var(--text-primary))]"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-[hsl(var(--text-primary))] mb-1">
                  {t("codes.type")}
                </label>
                <AdminSelect
                  fullWidth
                  value={batchFormData.code_type}
                  onChange={(e) =>
                    setBatchFormData({
                      ...batchFormData,
                      code_type: e.target.value as CodeType,
                    })
                  }
                  className="text-[hsl(var(--text-primary))]"
                >
                  <option value="single_use">{t("codes.typeSingle")}</option>
                  <option value="multi_use">{t("codes.typeMulti")}</option>
                </AdminSelect>
              </div>

              <div>
                <label className="block text-sm font-medium text-[hsl(var(--text-primary))] mb-1">
                  {t("codes.notes")}
                </label>
                <textarea
                  value={batchFormData.notes}
                  onChange={(e) => setBatchFormData({ ...batchFormData, notes: e.target.value })}
                  rows={2}
                  className="w-full px-3 py-2.5 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg focus:outline-none focus:ring-2 focus:ring-[hsl(var(--accent-primary))] text-[hsl(var(--text-primary))] resize-none"
                  placeholder={t("codes.notesPlaceholder")}
                />
              </div>
            </div>

            <div className="flex flex-col-reverse sm:flex-row items-center justify-end gap-2 px-4 sm:px-6 py-4 border-t border-[hsl(var(--separator-color))]">
              <button
                onClick={() => setShowBatchModal(false)}
                disabled={batchCreateMutation.isPending}
                className="w-full sm:w-auto px-4 py-2.5 min-h-11 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg hover:bg-[hsl(var(--bg-tertiary))] active:scale-95 transition-all text-sm text-[hsl(var(--text-primary))] disabled:opacity-50"
              >
                {t("common:cancel")}
              </button>
              <button
                onClick={handleCreateBatch}
                disabled={batchCreateMutation.isPending}
                className="w-full sm:w-auto px-4 py-2.5 min-h-11 bg-[hsl(var(--accent-primary))] text-white rounded-lg hover:opacity-90 active:scale-95 transition-all text-sm disabled:opacity-50"
              >
                {batchCreateMutation.isPending ? t("common:loading") : t("codes.batchCreate")}
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
};

export default CodeManagement;
