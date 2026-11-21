import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { AlertTriangle, RefreshCw } from "lucide-react";
import { AdminPageState, AdminSelect } from "../../components/admin";
import { AdminUserLink } from "../../components/admin/AdminUserLink";
import { Button } from "../../components/ui/Button";
import { Modal } from "../../components/ui/Modal";
import { adminApi, type AdminPaymentOrder, type PaymentOrderSyncResponse } from "../../lib/adminApi";
import { ApiError } from "../../lib/apiClient";
import { parseUTCDate } from "../../lib/dateUtils";
import { getLocaleCode } from "../../lib/i18n-helpers";
import { useIsDesktop } from "../../hooks/useMediaQuery";

const PAGE_SIZE = 20;

type FulfillmentFilter = "" | "pending" | "succeeded" | "failed";

function syncErrorReason(cause: unknown): string {
  if (cause instanceof ApiError) {
    // Coded errors carry the reason in error_detail; older servers sent it as detail.
    const raw = [cause.detailText, cause.rawMessage].find((text) => text?.startsWith("sync_failed:"));
    if (raw) return raw.slice("sync_failed:".length);
  }
  return "unknown";
}

export default function PaymentOrderManagement() {
  const { t } = useTranslation(["admin", "common"]);
  const queryClient = useQueryClient();
  const isDesktop = useIsDesktop();
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState<"" | "pending" | "paid">("");
  const [fulfillment, setFulfillment] = useState<FulfillmentFilter>("");
  const [needsAttention, setNeedsAttention] = useState(false);
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [selectedOrder, setSelectedOrder] = useState<AdminPaymentOrder | null>(null);
  const [syncMessage, setSyncMessage] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const detailsRequestRef = useRef(0);
  useEffect(() => () => { detailsRequestRef.current += 1; }, []);
  const { data, isLoading, isFetching, isError, refetch } = useQuery({
    queryKey: ["admin", "payment-orders", page, status, search, fulfillment, needsAttention],
    queryFn: () => adminApi.getPaymentOrders({
      page,
      page_size: PAGE_SIZE,
      status: status || undefined,
      fulfillment_status: fulfillment || undefined,
      needs_attention: needsAttention || undefined,
      search: search || undefined,
    }),
  });
  const syncOrder = useMutation({
    mutationFn: ({ orderId }: { orderId: string; detailsRequestId: number }) => adminApi.syncPaymentOrder(orderId),
    onSuccess: (result: PaymentOrderSyncResponse, { detailsRequestId }) => {
      if (detailsRequestId === detailsRequestRef.current) {
        setSelectedOrder(result.order);
        setSyncMessage({ tone: "ok", text: t(`paymentOrders.syncOutcome.${result.outcome}`) });
      }
      void queryClient.invalidateQueries({ queryKey: ["admin", "payment-orders"] });
    },
    onError: (cause: unknown, { detailsRequestId }) => {
      if (detailsRequestId !== detailsRequestRef.current) return;
      setSyncMessage({
        tone: "error",
        text: t("paymentOrders.syncFailed", { reason: syncErrorReason(cause) }),
      });
    },
  });
  const orders = data?.items ?? [];
  const attentionTotal = data?.needs_attention_total ?? 0;
  const totalPages = Math.max(1, Math.ceil((data?.total ?? 0) / PAGE_SIZE));
  const locale = getLocaleCode();
  const formatDate = (value: string | null) => value ? parseUTCDate(value).toLocaleString(locale) : "-";
  const formatAmount = (cents: number) => `¥${(cents / 100).toLocaleString(locale, {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  })}`;
  // Unknown values (e.g. a hand-edited row) fall back to the raw string.
  const paymentStatus = (order: AdminPaymentOrder) => t(`paymentOrders.status.${order.status}`, order.status);
  const fulfillmentStatus = (order: AdminPaymentOrder) =>
    t(`paymentOrders.fulfillment.${order.fulfillment_status}`, order.fulfillment_status);
  const cycleLabel = (order: AdminPaymentOrder) => t(`paymentOrders.cycles.${order.cycle}`, order.cycle);
  const isPaidNotFulfilled = (order: AdminPaymentOrder) =>
    (order.status === "paid" && order.fulfillment_status !== "succeeded") || order.fulfillment_status === "failed";
  const openDetails = (order: AdminPaymentOrder) => {
    detailsRequestRef.current += 1;
    setSyncMessage(null);
    setSelectedOrder(order);
  };
  const closeDetails = () => {
    detailsRequestRef.current += 1;
    setSelectedOrder(null);
  };
  const columnClass = "px-4 py-3 text-left text-sm";

  return (
    <div className="admin-page">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="admin-page-title">{t("paymentOrders.title")}</h1>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            variant={needsAttention ? "primary" : "secondary"}
            size="touch"
            aria-pressed={needsAttention}
            leftIcon={<AlertTriangle className="h-4 w-4" />}
            rightIcon={attentionTotal > 0 ? (
              <span className="rounded-full bg-[hsl(var(--danger-solid))] px-2 text-xs text-[hsl(var(--primary-foreground))]" aria-label={t("paymentOrders.needsAttentionCount", { count: attentionTotal })}>
                {attentionTotal}
              </span>
            ) : undefined}
            onClick={() => { setNeedsAttention(!needsAttention); setPage(1); }}
          >
            {t("paymentOrders.needsAttention")}
          </Button>
          <Button type="button" variant="secondary" size="touch" leftIcon={<RefreshCw className="h-4 w-4" />} disabled={isFetching} onClick={() => void refetch()}>
            {t("paymentOrders.refresh")}
          </Button>
        </div>
      </div>
      <form className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-[auto_auto_minmax(180px,1fr)]" onSubmit={(event) => {
        event.preventDefault(); setSearch(searchInput.trim()); setPage(1);
      }}>
        <AdminSelect fullWidth aria-label={t("paymentOrders.paymentStatus")} value={status} onChange={(event) => {
          setStatus(event.target.value as typeof status); setPage(1);
        }}>
          <option value="">{t("paymentOrders.allStatus")}</option>
          <option value="pending">{t("paymentOrders.status.pending")}</option>
          <option value="paid">{t("paymentOrders.status.paid")}</option>
        </AdminSelect>
        <AdminSelect fullWidth aria-label={t("paymentOrders.fulfillmentStatus")} value={fulfillment} onChange={(event) => {
          setFulfillment(event.target.value as FulfillmentFilter); setPage(1);
        }}>
          <option value="">{t("paymentOrders.allFulfillment")}</option>
          <option value="pending">{t("paymentOrders.fulfillment.pending")}</option>
          <option value="succeeded">{t("paymentOrders.fulfillment.succeeded")}</option>
          <option value="failed">{t("paymentOrders.fulfillment.failed")}</option>
        </AdminSelect>
        <div className="flex min-w-0 gap-3 sm:col-span-2 xl:col-span-1">
          <input className="min-h-11 min-w-0 flex-1 rounded-lg border border-[hsl(var(--separator-color))] bg-[hsl(var(--bg-secondary))] px-3 py-2 text-sm text-[hsl(var(--text-primary))]"
            aria-label={t("paymentOrders.search")} placeholder={t("paymentOrders.searchPlaceholder")}
            value={searchInput} onChange={(event) => setSearchInput(event.target.value)} />
          <Button type="submit" variant="secondary" size="touch" className="shrink-0">{t("paymentOrders.search")}</Button>
        </div>
      </form>
      <AdminPageState isLoading={isLoading} isFetching={isFetching} isError={isError} isEmpty={orders.length === 0}
        errorText={t("paymentOrders.loadError")} onRetry={() => void refetch()}>
        {isDesktop ? <div className="admin-table-shell overflow-x-auto">
          <table className="w-full min-w-[960px] text-[hsl(var(--text-primary))]">
            <thead><tr className="border-b border-[hsl(var(--separator-color))]">
              {["orderNumber", "user", "plan", "amount", "paymentMethod", "paymentStatus", "fulfillmentStatus", "createdAt", "details"].map((key) => (
                <th key={key} className={`${columnClass} font-semibold`}>{t(`paymentOrders.${key}`)}</th>
              ))}
            </tr></thead>
            <tbody>{orders.map((order) => (
              <tr key={order.id} className="border-b border-[hsl(var(--separator-color))] last:border-0">
                <td className={`${columnClass} font-mono text-xs`}>{order.out_trade_no}</td>
                <td className={columnClass}><div><AdminUserLink userId={order.user_id}>{order.username || order.user_id}</AdminUserLink></div><div className="text-xs text-[hsl(var(--text-secondary))]">{order.email}</div></td>
                <td className={columnClass}><div>{order.plan_display_name}</div><div className="text-xs text-[hsl(var(--text-secondary))]">{cycleLabel(order)}</div></td>
                <td className={`${columnClass} whitespace-nowrap`}>{formatAmount(order.amount_cents)}</td>
                <td className={columnClass}>{order.payment_method === "alipay" ? t("paymentOrders.alipay") : order.payment_method}</td>
                <td className={columnClass}>{paymentStatus(order)}</td>
                <td className={columnClass}><span className={isPaidNotFulfilled(order) ? "text-[hsl(var(--error-text))]" : ""}>{fulfillmentStatus(order)}</span></td>
                <td className={`${columnClass} whitespace-nowrap`}>{formatDate(order.created_at)}</td>
                <td className={columnClass}><button type="button" className="text-[var(--admin-accent-text)] hover:underline" onClick={() => openDetails(order)}>{t("paymentOrders.details")}</button></td>
              </tr>
            ))}</tbody>
          </table>
        </div> : <div className="space-y-3">
          {orders.map((order) => (
            <article key={order.id} className="admin-surface space-y-3 p-4">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0 flex-1 break-words">
                  <AdminUserLink userId={order.user_id}>{order.username || order.user_id}</AdminUserLink>
                  <p className="mt-1 text-xs text-[hsl(var(--text-secondary))]">{order.email}</p>
                </div>
                <span className="shrink-0 text-sm font-semibold tabular-nums">{formatAmount(order.amount_cents)}</span>
              </div>
              <p className="break-words text-sm">{order.plan_display_name} · {cycleLabel(order)}</p>
              <dl className="grid grid-cols-2 gap-3 text-xs">
                <div><dt className="text-[hsl(var(--text-secondary))]">{t("paymentOrders.paymentStatus")}</dt><dd className="mt-1">{paymentStatus(order)}</dd></div>
                <div><dt className="text-[hsl(var(--text-secondary))]">{t("paymentOrders.fulfillmentStatus")}</dt><dd className={`mt-1 ${isPaidNotFulfilled(order) ? "text-[hsl(var(--error-text))]" : ""}`}>{fulfillmentStatus(order)}</dd></div>
                <div className="col-span-2"><dt className="text-[hsl(var(--text-secondary))]">{t("paymentOrders.orderNumber")}</dt><dd className="mt-1 break-all font-mono">{order.out_trade_no}</dd></div>
              </dl>
              <div className="flex flex-wrap items-center justify-between gap-2 border-t border-[hsl(var(--separator-color))] pt-3">
                <span className="text-xs text-[hsl(var(--text-secondary))]">{formatDate(order.created_at)}</span>
                <Button type="button" variant="secondary" size="touch" onClick={() => openDetails(order)}>{t("paymentOrders.details")}</Button>
              </div>
            </article>
          ))}
        </div>}
      </AdminPageState>
      {!isError && (data?.total ?? 0) > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-[hsl(var(--text-secondary))]">
          <span>{t("paymentOrders.total", { count: data?.total ?? 0 })}</span>
          <div className="flex items-center gap-3">
            <Button type="button" variant="secondary" size="touch" disabled={page <= 1 || isFetching} onClick={() => setPage(page - 1)}>{t("paymentOrders.previousPage")}</Button>
            <span>{page} / {totalPages}</span>
            <Button type="button" variant="secondary" size="touch" disabled={page >= totalPages || isFetching} onClick={() => setPage(page + 1)}>{t("paymentOrders.nextPage")}</Button>
          </div>
        </div>
      )}
      <Modal open={selectedOrder !== null} onClose={closeDetails} title={t("paymentOrders.details")} size="lg">
        {selectedOrder && <div className="space-y-4">
          <dl className="space-y-3 text-sm">
            {([
              ["orderNumber", selectedOrder.out_trade_no], ["providerOrderNumber", selectedOrder.trade_no ?? "-"],
              ["user", <><AdminUserLink userId={selectedOrder.user_id}>{selectedOrder.username || selectedOrder.user_id}</AdminUserLink> · {selectedOrder.email}</>],
              ["plan", `${selectedOrder.plan_display_name} · ${cycleLabel(selectedOrder)}`],
              ["amount", formatAmount(selectedOrder.amount_cents)], ["paymentMethod", t("paymentOrders.alipay")],
              ["paymentStatus", paymentStatus(selectedOrder)], ["fulfillmentStatus", fulfillmentStatus(selectedOrder)],
              ["createdAt", formatDate(selectedOrder.created_at)], ["paidAt", formatDate(selectedOrder.paid_at)],
              ["fulfilledAt", formatDate(selectedOrder.fulfilled_at)], ["failureReason", selectedOrder.failure_reason ?? "-"],
              ["upgradeSource", selectedOrder.upgrade_source ?? "-"],
            ] as Array<[string, React.ReactNode]>).map(([key, value]) => <div key={key} className="grid grid-cols-[minmax(90px,1fr)_2fr] gap-3">
              <dt className="text-[hsl(var(--text-secondary))]">{t(`paymentOrders.${key}`)}</dt>
              <dd className="min-w-0 break-words text-[hsl(var(--text-primary))]">{value}</dd>
            </div>)}
          </dl>
          {selectedOrder.fulfillment_status !== "succeeded" && (
            <div className="space-y-2 border-t border-[hsl(var(--separator-color))] pt-3">
              <p className="text-xs text-[hsl(var(--text-secondary))]">{t("paymentOrders.syncHint")}</p>
              <Button
                type="button"
                variant="primary"
                size="touch"
                leftIcon={<RefreshCw className={`h-4 w-4 ${syncOrder.isPending ? "animate-spin" : ""}`} />}
                disabled={syncOrder.isPending}
                onClick={() => {
                  setSyncMessage(null);
                  syncOrder.mutate({ orderId: selectedOrder.id, detailsRequestId: detailsRequestRef.current });
                }}
              >
                {syncOrder.isPending ? t("paymentOrders.syncing") : t("paymentOrders.sync")}
              </Button>
            </div>
          )}
          {syncMessage && (
            <p role="status" className={`text-sm ${syncMessage.tone === "error" ? "text-[hsl(var(--error-text))]" : "text-[hsl(var(--success-text))]"}`}>
              {syncMessage.text}
            </p>
          )}
        </div>}
      </Modal>
    </div>
  );
}
