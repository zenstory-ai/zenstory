import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { Eye, X, RotateCcw } from "lucide-react";
import { AdminPageState, AdminSelect } from "../../components/admin";
import { adminApi, type AuditLog } from "../../lib/adminApi";
import { getLocaleCode } from "../../lib/i18n-helpers";
import { parseUTCDate } from "../../lib/dateUtils";

// Action type color mapping
const getActionColor = (action: string): string => {
  if (action.includes("create")) {
    return "bg-[hsl(var(--success)/0.15)] text-[hsl(var(--success))]";
  }
  if (action.includes("delete") || action.includes("reject")) {
    return "bg-[hsl(var(--error)/0.15)] text-[hsl(var(--error))]";
  }
  if (action.includes("update") || action.includes("approve")) {
    return "bg-[hsl(var(--accent-primary)/0.15)] text-[hsl(var(--accent-primary))]";
  }
  return "bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-secondary))]";
};

// Resource type icon mapping
const getResourceTypeLabel = (resourceType: string, t: (key: string) => string): string => {
  const typeMap: Record<string, string> = {
    user: t("auditLogs.resourceUser"),
    code: t("auditLogs.resourceCode"),
    subscription: t("auditLogs.resourceSubscription"),
    inspiration: t("auditLogs.resourceInspiration"),
    plan: t("auditLogs.resourcePlan"),
  };
  return typeMap[resourceType] || resourceType;
};

// Mobile card component for audit logs
const AuditLogCard: React.FC<{
  log: AuditLog;
  onViewDetails: (log: AuditLog) => void;
  t: (key: string, options?: Record<string, unknown>) => string;
  formatDate: (dateStr: string) => string;
}> = ({ log, onViewDetails, t, formatDate }) => {
  return (
    <div className="admin-surface p-4 space-y-3">
      <div className="flex items-center justify-between">
        <span className="font-medium text-[hsl(var(--text-primary))]">
          {log.admin_name}
        </span>
        <span className="text-xs text-[hsl(var(--text-secondary))]">
          {formatDate(log.created_at)}
        </span>
      </div>
      <div className="flex flex-wrap gap-2">
        <span className={`px-2 py-1 rounded text-xs font-medium ${getActionColor(log.action)}`}>
          {t(`auditLogs.action${log.action.charAt(0).toUpperCase()}${log.action.slice(1).replace(/_/g, "")}`, { defaultValue: log.action })}
        </span>
        <span className="px-2 py-1 rounded text-xs font-medium bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-secondary))]">
          {getResourceTypeLabel(log.resource_type, t)}
        </span>
      </div>
      <div className="text-sm text-[hsl(var(--text-secondary))]">
        <span>{t("auditLogs.resourceId")}: </span>
        <span className="font-mono text-[hsl(var(--text-primary))]">{log.resource_id || "-"}</span>
      </div>
      {log.details && (
        <div className="text-sm text-[hsl(var(--text-secondary))] line-clamp-2">
          {log.details}
        </div>
      )}
      <div className="flex justify-end pt-2 border-t border-[hsl(var(--separator-color))]">
        <button
          onClick={() => onViewDetails(log)}
          className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg hover:bg-[hsl(var(--bg-tertiary))] transition-colors"
        >
          <Eye size={14} />
          <span>{t("auditLogs.viewDetails")}</span>
        </button>
      </div>
    </div>
  );
};

// Detail modal component
const DetailModal: React.FC<{
  log: AuditLog | null;
  onClose: () => void;
  t: (key: string, options?: Record<string, unknown>) => string;
  formatDate: (dateStr: string) => string;
}> = ({ log, onClose, t, formatDate }) => {
  if (!log) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50">
      <div className="bg-[hsl(var(--bg-primary))] border border-[hsl(var(--separator-color))] rounded-lg shadow-xl w-full max-w-2xl max-h-[90vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-4 sm:px-6 py-4 border-b border-[hsl(var(--separator-color))] shrink-0">
          <h2 className="text-lg font-semibold text-[hsl(var(--text-primary))]">
            {t("auditLogs.detailTitle")}
          </h2>
          <button
            onClick={onClose}
            className="p-2.5 hover:bg-[hsl(var(--bg-tertiary))] rounded transition-colors"
          >
            <X size={20} className="text-[hsl(var(--text-secondary))]" />
          </button>
        </div>

        {/* Content */}
        <div className="px-4 sm:px-6 py-4 space-y-4 overflow-y-auto flex-1">
          {/* Basic info */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-[hsl(var(--text-secondary))] mb-1">
                {t("auditLogs.admin")}
              </label>
              <p className="text-[hsl(var(--text-primary))]">{log.admin_name}</p>
            </div>
            <div>
              <label className="block text-sm font-medium text-[hsl(var(--text-secondary))] mb-1">
                {t("auditLogs.timestamp")}
              </label>
              <p className="text-[hsl(var(--text-primary))]">{formatDate(log.created_at)}</p>
            </div>
            <div>
              <label className="block text-sm font-medium text-[hsl(var(--text-secondary))] mb-1">
                {t("auditLogs.action")}
              </label>
              <span className={`inline-block px-2 py-1 rounded text-xs font-medium ${getActionColor(log.action)}`}>
                {log.action}
              </span>
            </div>
            <div>
              <label className="block text-sm font-medium text-[hsl(var(--text-secondary))] mb-1">
                {t("auditLogs.resourceType")}
              </label>
              <p className="text-[hsl(var(--text-primary))]">{getResourceTypeLabel(log.resource_type, t)}</p>
            </div>
          </div>

          {/* Resource ID */}
          <div>
            <label className="block text-sm font-medium text-[hsl(var(--text-secondary))] mb-1">
              {t("auditLogs.resourceId")}
            </label>
            <p className="font-mono text-sm text-[hsl(var(--text-primary))] bg-[hsl(var(--bg-secondary))] px-3 py-2 rounded">
              {log.resource_id || "-"}
            </p>
          </div>

          {/* Old Value */}
          {log.old_value && Object.keys(log.old_value).length > 0 && (
            <div>
              <label className="block text-sm font-medium text-[hsl(var(--text-secondary))] mb-1">
                {t("auditLogs.oldValue")}
              </label>
              <pre className="text-sm text-[hsl(var(--text-primary))] bg-[hsl(var(--bg-secondary))] px-3 py-2 rounded overflow-x-auto whitespace-pre-wrap break-words">
                {JSON.stringify(log.old_value, null, 2)}
              </pre>
            </div>
          )}

          {/* New Value */}
          {log.new_value && Object.keys(log.new_value).length > 0 && (
            <div>
              <label className="block text-sm font-medium text-[hsl(var(--text-secondary))] mb-1">
                {t("auditLogs.newValue")}
              </label>
              <pre className="text-sm text-[hsl(var(--text-primary))] bg-[hsl(var(--bg-secondary))] px-3 py-2 rounded overflow-x-auto whitespace-pre-wrap break-words">
                {JSON.stringify(log.new_value, null, 2)}
              </pre>
            </div>
          )}

          {/* Details (fallback) */}
          {log.details && !log.old_value && !log.new_value && (
            <div>
              <label className="block text-sm font-medium text-[hsl(var(--text-secondary))] mb-1">
                {t("auditLogs.details")}
              </label>
              <p className="text-sm text-[hsl(var(--text-primary))] bg-[hsl(var(--bg-secondary))] px-3 py-2 rounded">
                {log.details}
              </p>
            </div>
          )}

          {/* IP Address */}
          {log.ip_address && (
            <div>
              <label className="block text-sm font-medium text-[hsl(var(--text-secondary))] mb-1">
                {t("auditLogs.ipAddress")}
              </label>
              <p className="font-mono text-sm text-[hsl(var(--text-primary))] bg-[hsl(var(--bg-secondary))] px-3 py-2 rounded">
                {log.ip_address}
              </p>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex justify-end px-4 sm:px-6 py-4 border-t border-[hsl(var(--separator-color))] shrink-0">
          <button
            onClick={onClose}
            className="px-4 py-2.5 min-h-11 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg hover:bg-[hsl(var(--bg-tertiary))] active:scale-95 transition-all text-sm"
          >
            {t("common:close")}
          </button>
        </div>
      </div>
    </div>
  );
};

export const AuditLogPage: React.FC = () => {
  const { t } = useTranslation(["admin", "common"]);
  const [page, setPage] = useState(1);
  const [resourceTypeFilter, setResourceTypeFilter] = useState<string>("");
  const [actionFilter, setActionFilter] = useState<string>("");
  const [selectedLog, setSelectedLog] = useState<AuditLog | null>(null);
  const pageSize = 20;

  // Fetch audit logs
  const { data, isLoading, isFetching, isError, error, refetch } = useQuery({
    queryKey: ["admin", "audit-logs", page, resourceTypeFilter, actionFilter],
    queryFn: () =>
      adminApi.getAuditLogs({
        page,
        page_size: pageSize,
        resource_type: resourceTypeFilter || undefined,
        action: actionFilter || undefined,
      }),
    staleTime: 30 * 1000,
  });

  const logs = data?.items ?? [];
  const hasMore = page * pageSize < (data?.total ?? 0);
  const queryErrorText = error instanceof Error && error.message
    ? error.message
    : t("common:error");

  const formatDate = (dateStr: string) => {
    const date = parseUTCDate(dateStr);
    if (Number.isNaN(date.getTime())) {
      return "-";
    }

    return date.toLocaleString(getLocaleCode(), {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  };

  const handleViewDetails = (log: AuditLog) => {
    setSelectedLog(log);
  };

  // Resource type options
  const resourceTypeOptions = [
    { value: "", label: t("auditLogs.allResourceTypes") },
    { value: "user", label: t("auditLogs.resourceUser") },
    { value: "code", label: t("auditLogs.resourceCode") },
    { value: "subscription", label: t("auditLogs.resourceSubscription") },
    { value: "inspiration", label: t("auditLogs.resourceInspiration") },
    { value: "plan", label: t("auditLogs.resourcePlan") },
  ];

  // Action type options
  const actionOptions = [
    { value: "", label: t("auditLogs.allActions") },
    { value: "create", label: t("auditLogs.actionCreate") },
    { value: "update", label: t("auditLogs.actionUpdate") },
    { value: "delete", label: t("auditLogs.actionDelete") },
    { value: "approve", label: t("auditLogs.actionApprove") },
    { value: "reject", label: t("auditLogs.actionReject") },
  ];

  return (
    <div className="admin-page admin-page-fluid">
      <div>
        <h1 className="admin-page-title">
          {t("auditLogs.title")}
        </h1>
        <p className="admin-page-subtitle">
          {t("auditLogs.subtitle")}
        </p>
      </div>

      {/* Filters */}
      <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2">
        <AdminSelect
          value={resourceTypeFilter}
          onChange={(e) => {
            setResourceTypeFilter(e.target.value);
            setPage(1);
          }}
          className="text-[hsl(var(--text-primary))]"
        >
          {resourceTypeOptions.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </AdminSelect>
        <AdminSelect
          value={actionFilter}
          onChange={(e) => {
            setActionFilter(e.target.value);
            setPage(1);
          }}
          className="text-[hsl(var(--text-primary))]"
        >
          {actionOptions.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </AdminSelect>
        {(resourceTypeFilter || actionFilter) && (
          <button
            onClick={() => {
              setResourceTypeFilter("");
              setActionFilter("");
              setPage(1);
            }}
            className="w-full sm:w-auto px-4 py-2.5 min-h-11 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg hover:bg-[hsl(var(--bg-tertiary))] active:scale-95 transition-all text-[hsl(var(--text-primary))] flex items-center justify-center gap-2"
          >
            <RotateCcw size={16} />
            {t("common:reset")}
          </button>
        )}
      </div>

      {/* Audit logs table/cards */}
      <AdminPageState
        isLoading={isLoading}
        isFetching={isFetching}
        isError={isError}
        isEmpty={logs.length === 0}
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
            {logs.map((log) => (
              <AuditLogCard
                key={log.id}
                log={log}
                onViewDetails={handleViewDetails}
                t={t}
                formatDate={formatDate}
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
                      {t("auditLogs.admin")}
                    </th>
                    <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                      {t("auditLogs.action")}
                    </th>
                    <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                      {t("auditLogs.resourceType")}
                    </th>
                    <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                      {t("auditLogs.resourceId")}
                    </th>
                    <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                      {t("auditLogs.timestamp")}
                    </th>
                    <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                      {t("auditLogs.actions")}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {logs.map((log) => (
                    <tr
                      key={log.id}
                      className="border-b border-[hsl(var(--separator-color))] hover:bg-[hsl(var(--bg-tertiary))]"
                    >
                      <td className="px-4 py-3 text-sm text-[hsl(var(--text-primary))]">
                        {log.admin_name}
                      </td>
                      <td className="px-4 py-3 text-sm">
                        <span className={`px-2 py-1 rounded text-xs font-medium ${getActionColor(log.action)}`}>
                          {log.action}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-sm text-[hsl(var(--text-primary))]">
                        {getResourceTypeLabel(log.resource_type, t)}
                      </td>
                      <td className="px-4 py-3 text-sm font-mono text-[hsl(var(--text-primary))]">
                        {log.resource_id || "-"}
                      </td>
                      <td className="px-4 py-3 text-sm text-[hsl(var(--text-secondary))]">
                        {formatDate(log.created_at)}
                      </td>
                      <td className="px-4 py-3 text-sm">
                        <button
                          onClick={() => handleViewDetails(log)}
                          className="p-1.5 hover:bg-[hsl(var(--bg-tertiary))] rounded transition-colors"
                          title={t("auditLogs.viewDetails")}
                        >
                          <Eye size={16} className="text-[hsl(var(--text-secondary))]" />
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
      <div className="flex items-center justify-between gap-4">
        <div className="text-sm text-[hsl(var(--text-secondary))]">
          {t("auditLogs.pageInfo", { page })}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page === 1}
            className="px-4 py-2.5 min-h-11 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg hover:bg-[hsl(var(--bg-tertiary))] active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed transition-all text-sm"
          >
            {t("common:previous")}
          </button>
          <span className="text-sm text-[hsl(var(--text-primary))]">
            {page}
          </span>
          <button
            onClick={() => setPage((p) => p + 1)}
            disabled={!hasMore || isFetching || isError}
            className="px-4 py-2.5 min-h-11 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg hover:bg-[hsl(var(--bg-tertiary))] active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed transition-all text-sm"
          >
            {t("common:next")}
          </button>
        </div>
      </div>

      {/* Detail Modal */}
      <DetailModal
        log={selectedLog}
        onClose={() => setSelectedLog(null)}
        t={t}
        formatDate={formatDate}
      />

    </div>
  );
};

export default AuditLogPage;
