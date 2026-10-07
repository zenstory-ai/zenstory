import React, { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  X,
  Edit,
  Calendar,
  ArrowUpRight,
  Search,
} from "lucide-react";
import { AdminPageState, AdminSelect } from "../../components/admin";
import { AdminUserLink } from "../../components/admin/AdminUserLink";
import { adminApi, type Subscription } from "../../lib/adminApi";
import { buildSubscriptionUpdate } from "../../lib/adminSubscription";
import { formatAdminDate, formatAdminDateTime } from "../../lib/dateUtils";
import { getLocalizedPlanDisplayName } from "../../lib/subscriptionEntitlements";
import { toast } from "../../lib/toast";
import type { SubscriptionPlan } from "../../types/subscription";

const LONG_TERM_YEAR_THRESHOLD = 2120;

const normalizeSubscriptionStatus = (status: string): string => {
  if (status === "past_due") return "expired";
  if (status === "canceled") return "cancelled";
  return status;
};

const getEffectiveStatus = (subscription: Subscription): string =>
  normalizeSubscriptionStatus(subscription.effective_status ?? subscription.status);

const getEffectivePlanName = (subscription: Subscription): string =>
  subscription.effective_plan_name ?? subscription.plan_name;

const getEffectivePlanDisplayName = (
  subscription: Subscription,
  language: string,
): string =>
  getLocalizedPlanDisplayName(
    {
      display_name:
        subscription.effective_plan_display_name
        ?? subscription.plan_display_name
        ?? getEffectivePlanName(subscription),
      display_name_en:
        subscription.effective_plan_display_name_en
        ?? subscription.plan_display_name_en,
    },
    language,
  );

const getStatusColor = (status: string, hasSubscriptionRecord: boolean = true): string => {
  if (!hasSubscriptionRecord) {
    return "bg-[hsl(var(--accent-primary)/0.15)] text-[hsl(var(--accent-primary))]";
  }
  switch (normalizeSubscriptionStatus(status)) {
    case "active":
      return "bg-[hsl(var(--success)/0.15)] text-[hsl(var(--success))]";
    case "expired":
      return "bg-[hsl(var(--warning)/0.15)] text-[hsl(var(--warning))]";
    case "cancelled":
      return "bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-secondary))]";
    default:
      return "bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-secondary))]";
  }
};

const getStatusI18nKey = (status: string): string => {
  switch (normalizeSubscriptionStatus(status)) {
    case "active":
      return "subscriptions.statusActive";
    case "expired":
      return "subscriptions.statusPastDue";
    case "cancelled":
      return "subscriptions.statusCanceled";
    default:
      return `subscriptions.status${status.charAt(0).toUpperCase() + status.slice(1)}`;
  }
};

const getUserPrimaryText = (subscription: Subscription): string => {
  if (subscription.email && subscription.email !== "-") {
    return subscription.email;
  }
  if (subscription.username && subscription.username !== "-") {
    return subscription.username;
  }
  return subscription.user_id;
};

const getUserSecondaryText = (subscription: Subscription): string => {
  if (subscription.username && subscription.username !== "-" && subscription.username !== subscription.email) {
    return `@${subscription.username}`;
  }
  if (subscription.user_id) {
    return `ID: ${subscription.user_id}`;
  }
  return "-";
};

const isLongTermSubscription = (subscription: Subscription): boolean => {
  if (!subscription.has_subscription_record) {
    return true;
  }
  if (!subscription.current_period_end) {
    return false;
  }
  const date = new Date(subscription.current_period_end);
  if (Number.isNaN(date.getTime())) {
    return false;
  }
  return date.getFullYear() >= LONG_TERM_YEAR_THRESHOLD;
};

const getStatusText = (
  subscription: Subscription,
  t: (key: string, defaultValue?: string) => string,
): string => {
  if (!subscription.has_subscription_record) {
    return t("subscriptions.statusUninitialized", "免费版（默认）");
  }
  return t(getStatusI18nKey(getEffectiveStatus(subscription)));
};

// Mobile card component for subscriptions
const SubscriptionCard: React.FC<{
  subscription: Subscription;
  onViewDetails: (sub: Subscription) => void;
  onModify: (sub: Subscription) => void;
  t: (key: string, defaultValue?: string) => string;
  getPeriodEndDisplay: (sub: Subscription) => string;
  getPlanDisplayName: (sub: Subscription) => string;
}> = ({
  subscription,
  onViewDetails,
  onModify,
  t,
  getPeriodEndDisplay,
  getPlanDisplayName,
}) => {
  return (
    <div className="admin-surface p-4 space-y-3">
      <div className="flex items-center justify-between">
        <div>
          <div className="font-medium text-[hsl(var(--text-primary))]">
            <AdminUserLink userId={subscription.user_id}>{getUserPrimaryText(subscription)}</AdminUserLink>
          </div>
          <div className="text-xs text-[hsl(var(--text-secondary))]">
            {getUserSecondaryText(subscription)}
          </div>
        </div>
        <span
          className={`px-2 py-1 rounded text-xs font-medium ${getStatusColor(
            getEffectiveStatus(subscription),
            subscription.has_subscription_record ?? true,
          )}`}
        >
          {getStatusText(subscription, t)}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-2 text-sm">
        <div>
          <span className="text-[hsl(var(--text-secondary))]">{t("subscriptions.plan")}:</span>
          <span className="ml-1 text-[hsl(var(--text-primary))] font-medium">
            {getPlanDisplayName(subscription)}
          </span>
        </div>
        <div>
          <span className="text-[hsl(var(--text-secondary))]">{t("subscriptions.periodEnd")}:</span>
          <span className="ml-1 text-[hsl(var(--text-primary))]">
            {getPeriodEndDisplay(subscription)}
          </span>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {subscription.is_test_account && (
          <div className="text-xs inline-flex items-center w-fit px-2 py-1 rounded bg-[hsl(var(--warning)/0.15)] text-[hsl(var(--warning))]">
            {t("subscriptions.testAccount", "测试账号")}
          </div>
        )}
        {!subscription.has_subscription_record && (
          <div className="text-xs inline-flex items-center w-fit px-2 py-1 rounded bg-[hsl(var(--accent-primary)/0.15)] text-[hsl(var(--accent-primary))]">
            {t("subscriptions.noRecord", "无订阅记录")}
          </div>
        )}
      </div>
      <div className="flex items-center justify-end gap-2 pt-2 border-t border-[hsl(var(--separator-color))]">
        <button
          onClick={() => onViewDetails(subscription)}
          className="px-3 py-1.5 text-sm bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg hover:bg-[hsl(var(--bg-tertiary))] transition-colors text-[hsl(var(--text-primary))]"
        >
          {t("subscriptions.viewDetails")}
        </button>
        <button
          onClick={() => onModify(subscription)}
          className="px-3 py-1.5 text-sm bg-[hsl(var(--accent-primary))] text-white rounded-lg hover:opacity-90 transition-all flex items-center gap-1"
        >
          <Edit size={14} />
          {t("subscriptions.modify")}
        </button>
      </div>
    </div>
  );
};

export const SubscriptionManagement: React.FC = () => {
  const { t, i18n } = useTranslation(["admin", "common"]);
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [search, setSearch] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [selectedSubscription, setSelectedSubscription] = useState<Subscription | null>(null);
  const [showModifyModal, setShowModifyModal] = useState(false);
  const [modifyFormData, setModifyFormData] = useState({
    plan_name: "pro",
    duration_days: 0,
    status: "active",
  });
  const pageSize = 20;

  // Fetch subscriptions list
  const { data, isLoading, isFetching, isError, error, refetch } = useQuery({
    queryKey: ["admin", "subscriptions", page, statusFilter, search],
    queryFn: () =>
      adminApi.getSubscriptions({
        page,
        page_size: pageSize,
        status: statusFilter || undefined,
        search: search || undefined,
      }),
    staleTime: 30 * 1000,
  });

  const { data: availablePlans } = useQuery({
    queryKey: ["admin", "plans"],
    queryFn: () => adminApi.getPlans(),
    staleTime: 60 * 1000,
  });

  // Update subscription mutation
  const updateMutation = useMutation({
    mutationFn: ({
      userId,
      data,
    }: {
      userId: string;
      data: { plan_name?: string; duration_days?: number; status?: string };
    }) => adminApi.updateUserSubscription(userId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "subscriptions"] });
      setShowModifyModal(false);
      setSelectedSubscription(null);
      toast.success(t("subscriptions.updateSuccess"));
    },
    onError: () => {
      toast.error(t("subscriptions.updateFailed"));
    },
  });

  const subscriptions = data?.items ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.ceil(total / pageSize);
  const queryErrorText = error instanceof Error && error.message
    ? error.message
    : t("common:error");
  const translate = (key: string, defaultValue?: string) =>
    defaultValue === undefined ? t(key) : t(key, defaultValue);

  const planOptions = useMemo(() => {
    const plans = [...(availablePlans ?? [])].sort(
      (a: SubscriptionPlan, b: SubscriptionPlan) =>
        a.price_monthly_cents - b.price_monthly_cents || a.name.localeCompare(b.name)
    );

    if (plans.length > 0) {
      return plans.map((plan) => ({
        value: plan.name,
        label: getLocalizedPlanDisplayName(plan, i18n.language),
      }));
    }

    return [
      { value: "free", label: t("subscriptions.planFree", "免费试用") },
      { value: "pro", label: t("subscriptions.planPro", "专业版") },
    ];
  }, [availablePlans, i18n.language, t]);

  const selectablePlanOptions = useMemo(() => {
    if (!modifyFormData.plan_name) {
      return planOptions;
    }

    const hasCurrentOption = planOptions.some(
      (option) => option.value === modifyFormData.plan_name
    );

    if (hasCurrentOption) {
      return planOptions;
    }

    return [
      ...planOptions,
      { value: modifyFormData.plan_name, label: modifyFormData.plan_name },
    ];
  }, [modifyFormData.plan_name, planOptions]);

  const handleViewDetails = (sub: Subscription) => {
    setSelectedSubscription(sub);
  };

  const handleModify = (sub: Subscription) => {
    setSelectedSubscription(sub);
    setModifyFormData({
      plan_name: getEffectivePlanName(sub),
      duration_days: 0,
      status: getEffectiveStatus(sub),
    });
    setShowModifyModal(true);
  };

  const handleModifySubmit = () => {
    if (!selectedSubscription) return;

    const change = buildSubscriptionUpdate(
      {
        plan_name: getEffectivePlanName(selectedSubscription),
        status: getEffectiveStatus(selectedSubscription),
      },
      modifyFormData,
    );
    if (!change.ok) {
      toast.error(
        change.reason === "durationRequired"
          ? t("subscriptions.extendDurationRequired", "更换套餐时请填写大于 0 的天数")
          : t("subscriptions.noChanges", "没有需要保存的修改"),
      );
      return;
    }

    updateMutation.mutate({
      userId: selectedSubscription.user_id,
      data: change.payload,
    });
  };

  const formatDate = formatAdminDate;
  const formatDateTime = formatAdminDateTime;

  const applySearch = () => {
    setSearch(searchInput.trim());
    setPage(1);
  };

  const getPeriodEndDisplay = (sub: Subscription) => {
    if (!sub.has_subscription_record) {
      return t("subscriptions.uninitializedLongTerm", "长期（免费版）");
    }
    if (isLongTermSubscription(sub)) {
      return t("subscriptions.longTerm", "长期有效");
    }
    return formatDate(sub.current_period_end);
  };

  const getPlanDisplayName = (subscription: Subscription) =>
    getEffectivePlanDisplayName(subscription, i18n.language);

  return (
    <div className="admin-page admin-page-fluid">
      <div>
        <h1 className="admin-page-title">
          {t("subscriptions.title")}
        </h1>
        <p className="admin-page-subtitle">
          {t("subscriptions.subtitle")}
        </p>
      </div>

      {/* Filters */}
      <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2">
        <div className="relative flex-1">
          <Search
            className="absolute left-3 top-1/2 -translate-y-1/2 text-[hsl(var(--text-secondary))]"
            size={18}
          />
          <input
            type="search"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") applySearch();
            }}
            placeholder={t("subscriptions.searchPlaceholder")}
            aria-label={t("subscriptions.searchPlaceholder")}
            className="w-full pl-10 pr-4 py-2.5 min-h-11 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg focus:outline-none focus:ring-2 focus:ring-[hsl(var(--accent-primary))] text-[hsl(var(--text-primary))] placeholder-[hsl(var(--text-secondary))]"
          />
        </div>
        <button
          onClick={applySearch}
          className="w-full sm:w-auto px-4 py-2.5 min-h-11 bg-[hsl(var(--accent-primary))] text-white rounded-lg hover:opacity-90 active:scale-95 transition-all"
        >
          {t("common:search")}
        </button>
        <AdminSelect
          value={statusFilter}
          onChange={(e) => {
            setStatusFilter(e.target.value);
            setPage(1);
          }}
          className="text-[hsl(var(--text-primary))]"
        >
          <option value="">{t("subscriptions.allStatus")}</option>
          <option value="active">{t("subscriptions.statusActive")}</option>
          <option value="expired">{t("subscriptions.statusPastDue")}</option>
          <option value="cancelled">{t("subscriptions.statusCanceled")}</option>
        </AdminSelect>
      </div>

      {/* Subscriptions table/cards */}
      <AdminPageState
        isLoading={isLoading}
        isFetching={isFetching}
        isError={isError}
        isEmpty={subscriptions.length === 0}
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
            {subscriptions.map((sub) => (
              <SubscriptionCard
                key={sub.id}
                subscription={sub}
                onViewDetails={handleViewDetails}
                onModify={handleModify}
                t={translate}
                getPeriodEndDisplay={getPeriodEndDisplay}
                getPlanDisplayName={getPlanDisplayName}
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
                      {t("subscriptions.user")}
                    </th>
                    <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                      {t("subscriptions.plan")}
                    </th>
                    <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                      {t("subscriptions.status")}
                    </th>
                    <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                      {t("subscriptions.periodEnd")}
                    </th>
                    <th className="px-4 py-3 text-left text-sm font-semibold text-[hsl(var(--text-primary))]">
                      {t("subscriptions.actions")}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {subscriptions.map((sub) => (
                    <tr
                      key={sub.id}
                      className="border-b border-[hsl(var(--separator-color))] hover:bg-[hsl(var(--bg-tertiary))]"
                    >
                      <td className="px-4 py-3 text-sm">
                        <div className="flex flex-col">
                          <span className="text-[hsl(var(--text-primary))] font-medium">
                            <AdminUserLink userId={sub.user_id}>{getUserPrimaryText(sub)}</AdminUserLink>
                          </span>
                          <span className="text-[hsl(var(--text-secondary))] text-xs">
                            {getUserSecondaryText(sub)}
                          </span>
                          {sub.is_test_account && (
                            <span className="mt-1 inline-flex w-fit px-2 py-0.5 rounded text-[10px] bg-[hsl(var(--warning)/0.15)] text-[hsl(var(--warning))]">
                              {t("subscriptions.testAccount", "测试账号")}
                            </span>
                          )}
                          {!sub.has_subscription_record && (
                            <span className="mt-1 inline-flex w-fit px-2 py-0.5 rounded text-[10px] bg-[hsl(var(--accent-primary)/0.15)] text-[hsl(var(--accent-primary))]">
                              {t("subscriptions.noRecord", "无订阅记录")}
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="px-4 py-3 text-sm text-[hsl(var(--text-primary))] font-medium">
                        {getPlanDisplayName(sub)}
                      </td>
                      <td className="px-4 py-3 text-sm">
                        <span
                          className={`px-2 py-1 rounded text-xs font-medium ${getStatusColor(
                            getEffectiveStatus(sub),
                            sub.has_subscription_record ?? true,
                          )}`}
                        >
                          {getStatusText(sub, translate)}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-sm text-[hsl(var(--text-secondary))]">
                        {getPeriodEndDisplay(sub)}
                      </td>
                      <td className="px-4 py-3 text-sm">
                        <div className="flex items-center gap-2">
                          <button
                            onClick={() => handleViewDetails(sub)}
                            className="p-1.5 hover:bg-[hsl(var(--bg-tertiary))] rounded transition-colors"
                            title={t("subscriptions.viewDetails")}
                          >
                            <ArrowUpRight size={16} className="text-[hsl(var(--text-primary))]" />
                          </button>
                          <button
                            onClick={() => handleModify(sub)}
                            className="p-1.5 hover:bg-[hsl(var(--bg-tertiary))] rounded transition-colors"
                            title={t("subscriptions.modify")}
                          >
                            <Edit size={16} className="text-[hsl(var(--text-primary))]" />
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

      {/* Subscription Details Modal */}
      {selectedSubscription && !showModifyModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50">
          <div className="bg-[hsl(var(--bg-primary))] border border-[hsl(var(--separator-color))] rounded-lg shadow-xl w-full max-w-lg">
            <div className="flex items-center justify-between px-4 sm:px-6 py-4 border-b border-[hsl(var(--separator-color))]">
              <h2 className="text-lg font-semibold text-[hsl(var(--text-primary))]">
                {t("subscriptions.detailsTitle")}
              </h2>
              <button
                onClick={() => setSelectedSubscription(null)}
                className="p-2.5 hover:bg-[hsl(var(--bg-tertiary))] rounded transition-colors"
              >
                <X size={20} className="text-[hsl(var(--text-secondary))]" />
              </button>
            </div>

            <div className="px-4 sm:px-6 py-4 space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm text-[hsl(var(--text-secondary))] mb-1">
                    {t("subscriptions.user")}
                  </label>
                  <p className="text-[hsl(var(--text-primary))] font-medium">
                    {getUserPrimaryText(selectedSubscription)}
                  </p>
                  <p className="text-xs text-[hsl(var(--text-secondary))] mt-1">
                    {getUserSecondaryText(selectedSubscription)}
                  </p>
                </div>
                <div>
                  <label className="block text-sm text-[hsl(var(--text-secondary))] mb-1">
                    {t("subscriptions.email")}
                  </label>
                  <p className="text-[hsl(var(--text-primary))]">
                    {selectedSubscription.email}
                  </p>
                </div>
                <div>
                  <label className="block text-sm text-[hsl(var(--text-secondary))] mb-1">
                    {t("subscriptions.plan")}
                  </label>
                  <p className="text-[hsl(var(--text-primary))] font-medium">
                    {getPlanDisplayName(selectedSubscription)}
                  </p>
                </div>
                <div>
                  <label className="block text-sm text-[hsl(var(--text-secondary))] mb-1">
                    {t("subscriptions.status")}
                  </label>
                  <span
                    className={`px-2 py-1 rounded text-xs font-medium ${getStatusColor(
                      getEffectiveStatus(selectedSubscription),
                      selectedSubscription.has_subscription_record ?? true,
                    )}`}
                  >
                  {getStatusText(selectedSubscription, translate)}
                  </span>
                </div>
                <div>
                  <label className="block text-sm text-[hsl(var(--text-secondary))] mb-1">
                    {t("subscriptions.periodStart")}
                  </label>
                  <p className="text-[hsl(var(--text-primary))]">
                    {formatDate(selectedSubscription.current_period_start)}
                  </p>
                </div>
                <div>
                  <label className="block text-sm text-[hsl(var(--text-secondary))] mb-1">
                    {t("subscriptions.periodEnd")}
                  </label>
                  <p className="text-[hsl(var(--text-primary))]">
                    {getPeriodEndDisplay(selectedSubscription)}
                  </p>
                </div>
                <div className="col-span-2">
                  <label className="block text-sm text-[hsl(var(--text-secondary))] mb-1">
                    {t("subscriptions.createdAt")}
                  </label>
                  <p className="text-[hsl(var(--text-primary))]">
                    {formatDateTime(selectedSubscription.created_at)}
                  </p>
                </div>
              </div>
            </div>

            <div className="flex flex-col-reverse sm:flex-row items-center justify-end gap-2 px-4 sm:px-6 py-4 border-t border-[hsl(var(--separator-color))]">
              <button
                onClick={() => setSelectedSubscription(null)}
                className="w-full sm:w-auto px-4 py-2.5 min-h-11 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg hover:bg-[hsl(var(--bg-tertiary))] active:scale-95 transition-all text-sm text-[hsl(var(--text-primary))]"
              >
                {t("common:close")}
              </button>
              <button
                onClick={() => {
                  setModifyFormData({
                    plan_name: getEffectivePlanName(selectedSubscription),
                    duration_days: 0,
                    status: getEffectiveStatus(selectedSubscription),
                  });
                  setShowModifyModal(true);
                }}
                className="w-full sm:w-auto px-4 py-2.5 min-h-11 bg-[hsl(var(--accent-primary))] text-white rounded-lg hover:opacity-90 active:scale-95 transition-all text-sm"
              >
                {t("subscriptions.modify")}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Modify Subscription Modal */}
      {showModifyModal && selectedSubscription && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50">
          <div className="bg-[hsl(var(--bg-primary))] border border-[hsl(var(--separator-color))] rounded-lg shadow-xl w-full max-w-md">
            <div className="flex items-center justify-between px-4 sm:px-6 py-4 border-b border-[hsl(var(--separator-color))]">
              <h2 className="text-lg font-semibold text-[hsl(var(--text-primary))]">
                {t("subscriptions.modifyTitle")}
              </h2>
              <button
                onClick={() => {
                  setShowModifyModal(false);
                  setSelectedSubscription(null);
                }}
                disabled={updateMutation.isPending}
                aria-label={t("common:close")}
                className="p-2.5 hover:bg-[hsl(var(--bg-tertiary))] rounded transition-colors"
              >
                <X size={20} className="text-[hsl(var(--text-secondary))]" />
              </button>
            </div>

            <div className="px-4 sm:px-6 py-4 space-y-4">
              <div className="p-3 bg-[hsl(var(--bg-secondary))] rounded-lg">
                <p className="text-sm text-[hsl(var(--text-secondary))]">
                  {t("subscriptions.modifyingFor")}
                </p>
                <p className="text-[hsl(var(--text-primary))] font-medium mt-1">
                  {getUserPrimaryText(selectedSubscription)}
                </p>
                <p className="text-xs text-[hsl(var(--text-secondary))] mt-1">
                  {getUserSecondaryText(selectedSubscription)}
                </p>
              </div>

              <div>
                <label className="block text-sm font-medium text-[hsl(var(--text-primary))] mb-1">
                  {t("subscriptions.plan")}
                </label>
                <AdminSelect
                  fullWidth
                  value={modifyFormData.plan_name}
                  onChange={(e) =>
                    setModifyFormData({ ...modifyFormData, plan_name: e.target.value })
                  }
                  className="text-[hsl(var(--text-primary))]"
                >
                  {selectablePlanOptions.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </AdminSelect>
              </div>

              <div>
                <label className="block text-sm font-medium text-[hsl(var(--text-primary))] mb-1">
                  <div className="flex items-center gap-2">
                    <Calendar size={16} />
                    {t("subscriptions.extendDuration")}
                  </div>
                </label>
                <input
                  type="number"
                  min={0}
                  value={modifyFormData.duration_days}
                  onChange={(e) =>
                    setModifyFormData({
                      ...modifyFormData,
                      duration_days: Math.max(0, parseInt(e.target.value, 10) || 0),
                    })
                  }
                  className="w-full px-3 py-2.5 min-h-11 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg focus:outline-none focus:ring-2 focus:ring-[hsl(var(--accent-primary))] text-[hsl(var(--text-primary))]"
                />
                <p className="text-xs text-[hsl(var(--text-secondary))] mt-1">
                  {t("subscriptions.extendHint")}
                  {" "}
                  {t("subscriptions.extendHintOptional", "只改状态时填 0。")}
                </p>
              </div>

              <div>
                <label className="block text-sm font-medium text-[hsl(var(--text-primary))] mb-1">
                  {t("subscriptions.status")}
                </label>
                <AdminSelect
                  fullWidth
                  value={modifyFormData.status}
                  onChange={(e) =>
                    setModifyFormData({ ...modifyFormData, status: e.target.value })
                  }
                  className="text-[hsl(var(--text-primary))]"
                >
                  <option value="active">{t("subscriptions.statusActive")}</option>
                  <option value="expired">{t("subscriptions.statusPastDue")}</option>
                  <option value="cancelled">{t("subscriptions.statusCanceled")}</option>
                </AdminSelect>
              </div>
            </div>

            <div className="flex flex-col-reverse sm:flex-row items-center justify-end gap-2 px-4 sm:px-6 py-4 border-t border-[hsl(var(--separator-color))]">
              <button
                onClick={() => {
                  setShowModifyModal(false);
                  setSelectedSubscription(null);
                }}
                disabled={updateMutation.isPending}
                className="w-full sm:w-auto px-4 py-2.5 min-h-11 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg hover:bg-[hsl(var(--bg-tertiary))] active:scale-95 transition-all text-sm text-[hsl(var(--text-primary))] disabled:opacity-50"
              >
                {t("common:cancel")}
              </button>
              <button
                onClick={handleModifySubmit}
                disabled={updateMutation.isPending}
                className="w-full sm:w-auto px-4 py-2.5 min-h-11 bg-[hsl(var(--accent-primary))] text-white rounded-lg hover:opacity-90 active:scale-95 transition-all text-sm disabled:opacity-50"
              >
                {updateMutation.isPending ? t("common:loading") : t("subscriptions.saveChanges")}
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
};

export default SubscriptionManagement;
