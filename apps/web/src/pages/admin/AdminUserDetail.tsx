import React, { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Activity, Edit } from "lucide-react";
import { AdminPageState, AdminSelect } from "../../components/admin";
import { UserQuotaCards } from "../../components/admin/UserQuotaCards";
import { Modal } from "../../components/ui/Modal";
import { adminApi, type UserSubscriptionDetailResponse } from "../../lib/adminApi";
import { buildSubscriptionUpdate, type SubscriptionChangeForm } from "../../lib/adminSubscription";
import { ApiError } from "../../lib/apiClient";
import { formatAdminDate, formatAdminDateTime, parseUTCDate } from "../../lib/dateUtils";
import { getLocaleCode } from "../../lib/i18n-helpers";
import { getLocalizedPlanDisplayName } from "../../lib/subscriptionEntitlements";
import { toast } from "../../lib/toast";

const RECENT_LIMIT = 10;

const errorText = (error: unknown, fallback: string) =>
  error instanceof Error && error.message ? error.message : fallback;

const Section: React.FC<{ title: string; actions?: React.ReactNode; children: React.ReactNode }> = ({
  title,
  actions,
  children,
}) => (
  <section className="space-y-3" aria-label={title}>
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h2 className="text-lg font-semibold text-[hsl(var(--text-primary))]">{title}</h2>
      {actions}
    </div>
    {children}
  </section>
);

const Field: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <div className="min-w-0">
    <dt className="text-xs text-[hsl(var(--text-secondary))]">{label}</dt>
    <dd className="mt-0.5 break-words text-sm text-[hsl(var(--text-primary))]">{children}</dd>
  </div>
);

/** Subscription row status as the user experiences it (an ended period is expired). */
const effectiveStatus = (detail: UserSubscriptionDetailResponse | null | undefined): string => {
  const subscription = detail?.subscription;
  if (!subscription?.status) return "active";
  const status = subscription.status === "canceled" ? "cancelled" : subscription.status;
  if (status !== "active" || !subscription.current_period_end) return status;
  return parseUTCDate(subscription.current_period_end).getTime() <= Date.now() ? "expired" : "active";
};

const STATUS_KEYS: Record<string, string> = {
  active: "subscriptions.statusActive",
  expired: "subscriptions.statusPastDue",
  past_due: "subscriptions.statusPastDue",
  cancelled: "subscriptions.statusCanceled",
};

export const AdminUserDetail: React.FC = () => {
  const { userId = "" } = useParams<{ userId: string }>();
  const { t, i18n } = useTranslation(["admin", "common"]);
  const queryClient = useQueryClient();
  const [editingSubscription, setEditingSubscription] = useState(false);
  const [subscriptionForm, setSubscriptionForm] = useState<SubscriptionChangeForm>({
    plan_name: "free",
    duration_days: 0,
    status: "active",
  });
  const [adjustAmount, setAdjustAmount] = useState("");
  const [adjustReason, setAdjustReason] = useState("");

  const userQuery = useQuery({
    queryKey: ["admin", "user", userId],
    queryFn: () => adminApi.getUser(userId),
    enabled: Boolean(userId),
  });
  const subscriptionQuery = useQuery({
    queryKey: ["admin", "user", userId, "subscription"],
    // A user without a subscription row is on the default free plan.
    queryFn: async () => {
      try {
        return await adminApi.getUserSubscription(userId);
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }
    },
    enabled: Boolean(userId),
  });
  const plansQuery = useQuery({
    queryKey: ["admin", "plans"],
    queryFn: () => adminApi.getPlans(),
    staleTime: 5 * 60 * 1000,
  });
  const quotaQuery = useQuery({
    queryKey: ["admin", "quota", "user", userId],
    queryFn: () => adminApi.getUserQuota(userId),
    enabled: Boolean(userId),
  });
  const pointsQuery = useQuery({
    queryKey: ["admin", "points", "user", userId],
    queryFn: () => adminApi.getUserPoints(userId),
    enabled: Boolean(userId),
  });
  const transactionsQuery = useQuery({
    queryKey: ["admin", "points", "transactions", userId, 1],
    queryFn: () => adminApi.getUserPointsTransactions(userId, { page: 1, page_size: RECENT_LIMIT }),
    enabled: Boolean(userId),
  });
  const ordersQuery = useQuery({
    queryKey: ["admin", "payment-orders", "user", userId],
    queryFn: () => adminApi.getPaymentOrders({ user_id: userId, page: 1, page_size: RECENT_LIMIT }),
    enabled: Boolean(userId),
  });

  const subscriptionMutation = useMutation({
    mutationFn: (data: { plan_name?: string; duration_days?: number; status?: string }) =>
      adminApi.updateUserSubscription(userId, data),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin", "user", userId] });
      void queryClient.invalidateQueries({ queryKey: ["admin", "quota", "user", userId] });
      void queryClient.invalidateQueries({ queryKey: ["admin", "subscriptions"] });
      setEditingSubscription(false);
      toast.success(t("subscriptions.updateSuccess"));
    },
    onError: () => toast.error(t("subscriptions.updateFailed")),
  });

  const adjustMutation = useMutation({
    mutationFn: (data: { amount: number; reason: string }) => adminApi.adjustUserPoints(userId, data),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ["admin", "points", "user", userId] });
      void queryClient.invalidateQueries({ queryKey: ["admin", "points", "transactions", userId] });
      setAdjustAmount("");
      setAdjustReason("");
      toast.success(t("points.adjustSuccess", { balance: result.new_balance }));
    },
    onError: () => toast.error(t("points.adjustFailed")),
  });

  const user = userQuery.data;
  const subscriptionDetail = subscriptionQuery.data;
  const currentPlanName = subscriptionDetail?.plan?.name ?? "free";
  const currentStatus = effectiveStatus(subscriptionDetail);
  const plans = plansQuery.data ?? [];
  const planName = (name: string) => {
    const plan = plans.find((item) => item.name === name);
    return plan ? getLocalizedPlanDisplayName(plan, i18n.language) : name;
  };
  const locale = getLocaleCode();
  const formatAmount = (cents: number) =>
    `¥${(cents / 100).toLocaleString(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

  const openSubscriptionEditor = () => {
    setSubscriptionForm({ plan_name: currentPlanName, duration_days: 0, status: currentStatus });
    setEditingSubscription(true);
  };

  const submitSubscription = () => {
    const change = buildSubscriptionUpdate(
      { plan_name: currentPlanName, status: currentStatus },
      subscriptionForm,
    );
    if (!change.ok) {
      toast.error(
        change.reason === "durationRequired"
          ? t("subscriptions.extendDurationRequired")
          : t("subscriptions.noChanges"),
      );
      return;
    }
    subscriptionMutation.mutate(change.payload);
  };

  const submitAdjust = (event: React.FormEvent) => {
    event.preventDefault();
    const amount = Number.parseInt(adjustAmount, 10);
    if (!Number.isFinite(amount) || amount === 0 || !adjustReason.trim()) return;
    adjustMutation.mutate({ amount, reason: adjustReason.trim() });
  };

  const sectionState = {
    loadingText: t("common:loading"),
    emptyText: t("common:noData"),
    retryText: t("common:retry"),
    stateClassName: "admin-surface flex items-center justify-center py-8",
  };

  const inputClass =
    "w-full px-3 py-2.5 min-h-11 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg focus:outline-none focus:ring-2 focus:ring-[hsl(var(--accent-primary))] text-[hsl(var(--text-primary))]";

  return (
    <div className="admin-page">
      <div className="space-y-2">
        <Link
          to="/admin/users"
          className="inline-flex items-center gap-1 text-sm text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))]"
        >
          <ArrowLeft size={16} />
          {t("userDetail.back")}
        </Link>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h1 className="admin-page-title break-all">{user?.username ?? t("userDetail.title")}</h1>
          <Link
            to={`/admin/usage?user=${encodeURIComponent(userId)}`}
            className="inline-flex items-center gap-2 rounded-lg border border-[hsl(var(--separator-color))] bg-[hsl(var(--bg-secondary))] px-3 py-2 text-sm text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))]"
          >
            <Activity size={16} />
            {t("userDetail.viewUsage")}
          </Link>
        </div>
      </div>

      <Section title={t("userDetail.account")}>
        <AdminPageState
          isLoading={userQuery.isLoading}
          isFetching={userQuery.isFetching}
          isError={userQuery.isError}
          isEmpty={!user}
          errorText={
            userQuery.error instanceof ApiError && userQuery.error.status === 404
              ? t("userDetail.notFound")
              : errorText(userQuery.error, t("common:error"))
          }
          onRetry={() => void userQuery.refetch()}
          {...sectionState}
        >
          {user && (
            <dl className="admin-surface grid grid-cols-1 gap-4 p-4 sm:grid-cols-2 lg:grid-cols-3">
              <Field label={t("users.email")}>
                {user.email}
                {!user.email_verified && (
                  <span className="ml-2 text-xs text-[hsl(var(--warning))]">{t("userDetail.emailUnverified")}</span>
                )}
              </Field>
              <Field label={t("users.isActive")}>
                {user.is_active ? t("users.active") : t("users.inactive")}
              </Field>
              <Field label={t("users.isSuperuser")}>
                {user.is_superuser ? t("users.yes") : t("users.no")}
              </Field>
              <Field label={t("users.createdAt")}>{formatAdminDateTime(user.created_at)}</Field>
              <Field label={t("userDetail.userId")}>
                <span className="font-mono text-xs">{user.id}</span>
              </Field>
            </dl>
          )}
        </AdminPageState>
      </Section>

      <Section
        title={t("userDetail.subscription")}
        actions={
          <button
            type="button"
            onClick={openSubscriptionEditor}
            disabled={subscriptionQuery.isLoading || subscriptionQuery.isError}
            className="inline-flex items-center gap-1.5 rounded-lg bg-[hsl(var(--accent-primary))] px-3 py-2 text-sm text-white hover:opacity-90 disabled:opacity-50"
          >
            <Edit size={14} />
            {t("subscriptions.modify")}
          </button>
        }
      >
        <AdminPageState
          isLoading={subscriptionQuery.isLoading}
          isFetching={subscriptionQuery.isFetching}
          isError={subscriptionQuery.isError}
          isEmpty={false}
          errorText={errorText(subscriptionQuery.error, t("common:error"))}
          onRetry={() => void subscriptionQuery.refetch()}
          {...sectionState}
        >
          <dl className="admin-surface grid grid-cols-1 gap-4 p-4 sm:grid-cols-2 lg:grid-cols-4">
            <Field label={t("userDetail.effectivePlan")}>
              {quotaQuery.data
                ? (getLocaleCode().startsWith("en")
                  ? quotaQuery.data.plan_display_name_en
                  : quotaQuery.data.plan_display_name) || quotaQuery.data.plan_name
                : "-"}
            </Field>
            <Field label={t("subscriptions.plan")}>
              {subscriptionDetail?.plan
                ? getLocalizedPlanDisplayName(subscriptionDetail.plan, i18n.language)
                : t("subscriptions.statusUninitialized")}
            </Field>
            <Field label={t("subscriptions.status")}>
              {subscriptionDetail ? t(STATUS_KEYS[currentStatus] ?? currentStatus, currentStatus) : "-"}
            </Field>
            <Field label={t("subscriptions.periodEnd")}>
              {formatAdminDate(subscriptionDetail?.subscription?.current_period_end)}
            </Field>
          </dl>
        </AdminPageState>
      </Section>

      <Section title={t("userDetail.quota")}>
        <AdminPageState
          isLoading={quotaQuery.isLoading}
          isFetching={quotaQuery.isFetching}
          isError={quotaQuery.isError}
          isEmpty={!quotaQuery.data}
          errorText={errorText(quotaQuery.error, t("common:error"))}
          onRetry={() => void quotaQuery.refetch()}
          {...sectionState}
        >
          {quotaQuery.data && <UserQuotaCards quota={quotaQuery.data} />}
        </AdminPageState>
      </Section>

      <Section title={t("userDetail.points")}>
        <AdminPageState
          isLoading={pointsQuery.isLoading}
          isFetching={pointsQuery.isFetching}
          isError={pointsQuery.isError}
          isEmpty={!pointsQuery.data}
          errorText={errorText(pointsQuery.error, t("common:error"))}
          onRetry={() => void pointsQuery.refetch()}
          {...sectionState}
        >
          {pointsQuery.data && (
            <dl className="admin-surface grid grid-cols-2 gap-4 p-4 lg:grid-cols-4">
              <Field label={t("points.available")}>{pointsQuery.data.available.toLocaleString()}</Field>
              <Field label={t("points.pendingExpiration")}>{pointsQuery.data.pending_expiration.toLocaleString()}</Field>
              <Field label={t("points.totalEarned")}>{pointsQuery.data.total_earned.toLocaleString()}</Field>
              <Field label={t("points.totalSpent")}>{pointsQuery.data.total_spent.toLocaleString()}</Field>
            </dl>
          )}
        </AdminPageState>

        <form onSubmit={submitAdjust} className="admin-surface grid grid-cols-1 gap-3 p-4 sm:grid-cols-[160px_1fr_auto] sm:items-end">
          <label className="block text-sm text-[hsl(var(--text-primary))]">
            {t("points.adjustAmount")}
            <input
              type="number"
              value={adjustAmount}
              onChange={(e) => setAdjustAmount(e.target.value)}
              placeholder={t("points.adjustAmountPlaceholder")}
              className={`mt-1 ${inputClass}`}
            />
          </label>
          <label className="block text-sm text-[hsl(var(--text-primary))]">
            {t("points.adjustReason")}
            <input
              type="text"
              value={adjustReason}
              onChange={(e) => setAdjustReason(e.target.value)}
              placeholder={t("points.adjustReasonPlaceholder")}
              className={`mt-1 ${inputClass}`}
            />
          </label>
          <button
            type="submit"
            disabled={adjustMutation.isPending || !adjustReason.trim() || !Number.parseInt(adjustAmount, 10)}
            className="min-h-11 rounded-lg bg-[hsl(var(--accent-primary))] px-4 text-sm text-white hover:opacity-90 disabled:opacity-50"
          >
            {adjustMutation.isPending ? t("common:loading") : t("points.adjustPoints")}
          </button>
        </form>

        <h3 className="text-sm font-semibold text-[hsl(var(--text-primary))]">{t("userDetail.recentTransactions")}</h3>
        <AdminPageState
          isLoading={transactionsQuery.isLoading}
          isFetching={transactionsQuery.isFetching}
          isError={transactionsQuery.isError}
          isEmpty={(transactionsQuery.data?.items?.length ?? 0) === 0}
          errorText={errorText(transactionsQuery.error, t("common:error"))}
          onRetry={() => void transactionsQuery.refetch()}
          {...sectionState}
          emptyText={t("userDetail.noTransactions")}
        >
          <ul className="admin-surface divide-y divide-[hsl(var(--separator-color))]">
            {transactionsQuery.data?.items?.map((tx) => (
              <li key={tx.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 text-sm">
                <span className="text-[hsl(var(--text-primary))]">
                  {t(`points.types.${tx.transaction_type}`, tx.transaction_type)}
                  {tx.description && tx.transaction_type === "admin_adjust" && (
                    <span className="ml-2 text-xs text-[hsl(var(--text-secondary))]">{tx.description}</span>
                  )}
                </span>
                <span className="flex items-center gap-4">
                  <span className={tx.amount > 0 ? "text-[hsl(var(--success))]" : "text-[hsl(var(--error))]"}>
                    {tx.amount > 0 ? "+" : ""}{tx.amount.toLocaleString()}
                  </span>
                  <span className="text-xs text-[hsl(var(--text-secondary))]">{formatAdminDateTime(tx.created_at)}</span>
                </span>
              </li>
            ))}
          </ul>
        </AdminPageState>
      </Section>

      <Section title={t("userDetail.payments")}>
        <AdminPageState
          isLoading={ordersQuery.isLoading}
          isFetching={ordersQuery.isFetching}
          isError={ordersQuery.isError}
          isEmpty={(ordersQuery.data?.items?.length ?? 0) === 0}
          errorText={errorText(ordersQuery.error, t("common:error"))}
          onRetry={() => void ordersQuery.refetch()}
          {...sectionState}
          emptyText={t("userDetail.noPayments")}
        >
          <ul className="admin-surface divide-y divide-[hsl(var(--separator-color))]">
            {ordersQuery.data?.items?.map((order) => (
              <li key={order.id} className="grid grid-cols-1 gap-1 px-4 py-2.5 text-sm sm:grid-cols-[1fr_auto_auto_auto] sm:items-center sm:gap-4">
                <span className="min-w-0">
                  <span className="text-[hsl(var(--text-primary))]">{order.plan_display_name}</span>
                  <span className="ml-2 font-mono text-xs text-[hsl(var(--text-secondary))]">{order.out_trade_no}</span>
                </span>
                <span className="text-[hsl(var(--text-primary))]">{formatAmount(order.amount_cents)}</span>
                <span className="text-[hsl(var(--text-secondary))]">
                  {t(`paymentOrders.status.${order.status}`, order.status)} · {t(`paymentOrders.fulfillment.${order.fulfillment_status}`, order.fulfillment_status)}
                </span>
                <span className="text-xs text-[hsl(var(--text-secondary))]">{formatAdminDateTime(order.created_at)}</span>
              </li>
            ))}
          </ul>
          {(ordersQuery.data?.total ?? 0) > RECENT_LIMIT && (
            <p className="text-xs text-[hsl(var(--text-secondary))]">
              {t("userDetail.olderOrders", { count: (ordersQuery.data?.total ?? 0) - RECENT_LIMIT })}
            </p>
          )}
        </AdminPageState>
      </Section>

      <Modal
        open={editingSubscription}
        onClose={() => setEditingSubscription(false)}
        title={t("subscriptions.modifyTitle")}
        footer={
          <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
            <button
              type="button"
              onClick={() => setEditingSubscription(false)}
              disabled={subscriptionMutation.isPending}
              className="min-h-11 rounded-lg border border-[hsl(var(--separator-color))] bg-[hsl(var(--bg-secondary))] px-4 text-sm text-[hsl(var(--text-primary))] disabled:opacity-50"
            >
              {t("common:cancel")}
            </button>
            <button
              type="button"
              onClick={submitSubscription}
              disabled={subscriptionMutation.isPending}
              className="min-h-11 rounded-lg bg-[hsl(var(--accent-primary))] px-4 text-sm text-white disabled:opacity-50"
            >
              {subscriptionMutation.isPending ? t("common:loading") : t("subscriptions.saveChanges")}
            </button>
          </div>
        }
      >
        <div className="space-y-4">
          <label className="block text-sm font-medium text-[hsl(var(--text-primary))]">
            {t("subscriptions.plan")}
            <AdminSelect
              fullWidth
              value={subscriptionForm.plan_name}
              onChange={(e) => setSubscriptionForm({ ...subscriptionForm, plan_name: e.target.value })}
              className="mt-1 text-[hsl(var(--text-primary))]"
            >
              {Array.from(new Set([currentPlanName, ...plans.map((plan) => plan.name as string)])).map((name) => (
                <option key={name} value={name}>{planName(name)}</option>
              ))}
            </AdminSelect>
          </label>
          <label className="block text-sm font-medium text-[hsl(var(--text-primary))]">
            {t("subscriptions.extendDuration")}
            <input
              type="number"
              min={0}
              value={subscriptionForm.duration_days}
              onChange={(e) =>
                setSubscriptionForm({
                  ...subscriptionForm,
                  duration_days: Math.max(0, Number.parseInt(e.target.value, 10) || 0),
                })
              }
              className={`mt-1 ${inputClass}`}
            />
            <span className="mt-1 block text-xs font-normal text-[hsl(var(--text-secondary))]">
              {t("subscriptions.extendHint")} {t("subscriptions.extendHintOptional")}
            </span>
          </label>
          <label className="block text-sm font-medium text-[hsl(var(--text-primary))]">
            {t("subscriptions.status")}
            <AdminSelect
              fullWidth
              value={subscriptionForm.status}
              onChange={(e) => setSubscriptionForm({ ...subscriptionForm, status: e.target.value })}
              className="mt-1 text-[hsl(var(--text-primary))]"
            >
              <option value="active">{t("subscriptions.statusActive")}</option>
              <option value="expired">{t("subscriptions.statusPastDue")}</option>
              <option value="cancelled">{t("subscriptions.statusCanceled")}</option>
            </AdminSelect>
          </label>
        </div>
      </Modal>
    </div>
  );
};

export default AdminUserDetail;
