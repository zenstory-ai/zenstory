import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { AlertTriangle, RefreshCw } from "lucide-react";
import { AdminPageState, AdminSelect } from "../../components/admin";
import { Modal } from "../../components/ui/Modal";
import { adminApi, type AdminPaymentOrder, type PaymentOrderSyncResponse } from "../../lib/adminApi";
import { ApiError } from "../../lib/apiClient";
import { parseUTCDate } from "../../lib/dateUtils";
import { getLocaleCode } from "../../lib/i18n-helpers";

const PAGE_SIZE = 20;

type FulfillmentFilter = "" | "pending" | "succeeded" | "failed";

function syncErrorReason(cause: unknown): string {
  if (cause instanceof ApiError && cause.rawMessage.startsWith("sync_failed:")) {
    return cause.rawMessage.slice("sync_failed:".length);
  }
  return "unknown";
}

export default function PaymentOrderManagement() {
  const { t } = useTranslation(["admin", "common"]);
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState<"" | "pending" | "paid">("");
  const [fulfillment, setFulfillment] = useState<FulfillmentFilter>("");
  const [needsAttention, setNeedsAttention] = useState(false);
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [selectedOrder, setSelectedOrder] = useState<AdminPaymentOrder | null>(null);
  const [syncMessage, setSyncMessage] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
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
    mutationFn: (orderId: string) => adminApi.syncPaymentOrder(orderId),
    onSuccess: (result: PaymentOrderSyncResponse) => {
      setSelectedOrder(result.order);
      setSyncMessage({ tone: "ok", text: t(`paymentOrders.syncOutcome.${result.outcome}`) });
      void queryClient.invalidateQueries({ queryKey: ["admin", "payment-orders"] });
    },
    onError: (cause: unknown) => {
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
    setSyncMessage(null);
    setSelectedOrder(order);
  };
  const columnClass = "px-4 py-3 text-left text-sm";

  return (
    <div className="admin-page">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="admin-page-title">{t("paymentOrders.title")}</h1>
          <p className="admin-page-subtitle">{t("paymentOrders.subtitle")}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            aria-pressed={needsAttention}
            className={`${needsAttention ? "btn-primary" : "btn-secondary"} flex items-center gap-2`}
            onClick={() => { setNeedsAttention(!needsAttention); setPage(1); }}
          >
            <AlertTriangle className="h-4 w-4" />
            {t("paymentOrders.needsAttention")}
            {attentionTotal > 0 && (
              <span className="rounded-full bg-[hsl(var(--error))] px-2 text-xs text-white" aria-label={t("paymentOrders.needsAttentionCount", { count: attentionTotal })}>
                {attentionTotal}
              </span>
            )}
          </button>
          <button type="button" className="btn-secondary flex items-center gap-2" disabled={isFetching} onClick={() => void refetch()}>
            <RefreshCw className="h-4 w-4" />{t("paymentOrders.refresh")}
          </button>
        </div>
      </div>
      <form className="flex flex-col gap-3 sm:flex-row" onSubmit={(event) => {
        event.preventDefault(); setSearch(searchInput.trim()); setPage(1);
      }}>
        <AdminSelect aria-label={t("paymentOrders.paymentStatus")} value={status} onChange={(event) => {
          setStatus(event.target.value as typeof status); setPage(1);
        }}>
          <option value="">{t("paymentOrders.allStatus")}</option>
          <option value="pending">{t("paymentOrders.status.pending")}</option>
          <option value="paid">{t("paymentOrders.status.paid")}</option>
        </AdminSelect>
        <AdminSelect aria-label={t("paymentOrders.fulfillmentStatus")} value={fulfillment} onChange={(event) => {
          setFulfillment(event.target.value as FulfillmentFilter); setPage(1);
        }}>
          <option value="">{t("paymentOrders.allFulfillment")}</option>
          <option value="pending">{t("paymentOrders.fulfillment.pending")}</option>
          <option value="succeeded">{t("paymentOrders.fulfillment.succeeded")}</option>
          <option value="failed">{t("paymentOrders.fulfillment.failed")}</option>
        </AdminSelect>
        <input className="min-w-0 flex-1 rounded-lg border border-[hsl(var(--separator-color))] bg-[hsl(var(--bg-secondary))] px-3 py-2 text-sm text-[hsl(var(--text-primary))]"
          aria-label={t("paymentOrders.search")} placeholder={t("paymentOrders.searchPlaceholder")}
          value={searchInput} onChange={(event) => setSearchInput(event.target.value)} />
        <button type="submit" className="btn-secondary">{t("paymentOrders.search")}</button>
      </form>
      <AdminPageState isLoading={isLoading} isFetching={isFetching} isError={isError} isEmpty={orders.length === 0}
        errorText={t("paymentOrders.loadError")} onRetry={() => void refetch()}>
        <div className="admin-table-shell overflow-x-auto">
          <table className="w-full min-w-[960px] text-[hsl(var(--text-primary))]">
            <thead><tr className="border-b border-[hsl(var(--separator-color))]">
              {["orderNumber", "user", "plan", "amount", "paymentMethod", "paymentStatus", "fulfillmentStatus", "createdAt", "details"].map((key) => (
                <th key={key} className={`${columnClass} font-semibold`}>{t(`paymentOrders.${key}`)}</th>
              ))}
            </tr></thead>
            <tbody>{orders.map((order) => (
              <tr key={order.id} className="border-b border-[hsl(var(--separator-color))] last:border-0">
                <td className={`${columnClass} font-mono text-xs`}>{order.out_trade_no}</td>
                <td className={columnClass}><div>{order.username || order.user_id}</div><div className="text-xs text-[hsl(var(--text-secondary))]">{order.email}</div></td>
                <td className={columnClass}><div>{order.plan_display_name}</div><div className="text-xs text-[hsl(var(--text-secondary))]">{cycleLabel(order)}</div></td>
                <td className={`${columnClass} whitespace-nowrap`}>{formatAmount(order.amount_cents)}</td>
                <td className={columnClass}>{order.payment_method === "alipay" ? t("paymentOrders.alipay") : order.payment_method}</td>
                <td className={columnClass}>{paymentStatus(order)}</td>
                <td className={columnClass}><span className={isPaidNotFulfilled(order) ? "text-[hsl(var(--error))]" : ""}>{fulfillmentStatus(order)}</span></td>
                <td className={`${columnClass} whitespace-nowrap`}>{formatDate(order.created_at)}</td>
                <td className={columnClass}><button type="button" className="text-[hsl(var(--accent-primary))] hover:underline" onClick={() => openDetails(order)}>{t("paymentOrders.details")}</button></td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      </AdminPageState>
      {!isError && (data?.total ?? 0) > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-[hsl(var(--text-secondary))]">
          <span>{t("paymentOrders.total", { count: data?.total ?? 0 })}</span>
          <div className="flex items-center gap-3">
            <button type="button" className="btn-secondary" disabled={page <= 1 || isFetching} onClick={() => setPage(page - 1)}>{t("paymentOrders.previousPage")}</button>
            <span>{page} / {totalPages}</span>
            <button type="button" className="btn-secondary" disabled={page >= totalPages || isFetching} onClick={() => setPage(page + 1)}>{t("paymentOrders.nextPage")}</button>
          </div>
        </div>
      )}
      <Modal open={selectedOrder !== null} onClose={() => setSelectedOrder(null)} title={t("paymentOrders.details")} size="lg">
        {selectedOrder && <div className="space-y-4">
          <dl className="space-y-3 text-sm">
            {[
              ["orderNumber", selectedOrder.out_trade_no], ["providerOrderNumber", selectedOrder.trade_no ?? "-"],
              ["user", `${selectedOrder.username || selectedOrder.user_id} · ${selectedOrder.email}`],
              ["plan", `${selectedOrder.plan_display_name} · ${cycleLabel(selectedOrder)}`],
              ["amount", formatAmount(selectedOrder.amount_cents)], ["paymentMethod", t("paymentOrders.alipay")],
              ["paymentStatus", paymentStatus(selectedOrder)], ["fulfillmentStatus", fulfillmentStatus(selectedOrder)],
              ["createdAt", formatDate(selectedOrder.created_at)], ["paidAt", formatDate(selectedOrder.paid_at)],
              ["fulfilledAt", formatDate(selectedOrder.fulfilled_at)], ["failureReason", selectedOrder.failure_reason ?? "-"],
              ["upgradeSource", selectedOrder.upgrade_source ?? "-"],
            ].map(([key, value]) => <div key={key} className="grid grid-cols-[minmax(90px,1fr)_2fr] gap-3">
              <dt className="text-[hsl(var(--text-secondary))]">{t(`paymentOrders.${key}`)}</dt>
              <dd className="min-w-0 break-words text-[hsl(var(--text-primary))]">{value}</dd>
            </div>)}
          </dl>
          {selectedOrder.fulfillment_status !== "succeeded" && (
            <div className="space-y-2 border-t border-[hsl(var(--separator-color))] pt-3">
              <p className="text-xs text-[hsl(var(--text-secondary))]">{t("paymentOrders.syncHint")}</p>
              <button
                type="button"
                className="btn-primary flex items-center gap-2"
                disabled={syncOrder.isPending}
                onClick={() => { setSyncMessage(null); syncOrder.mutate(selectedOrder.id); }}
              >
                <RefreshCw className={`h-4 w-4 ${syncOrder.isPending ? "animate-spin" : ""}`} />
                {syncOrder.isPending ? t("paymentOrders.syncing") : t("paymentOrders.sync")}
              </button>
            </div>
          )}
          {syncMessage && (
            <p role="status" className={`text-sm ${syncMessage.tone === "error" ? "text-[hsl(var(--error))]" : "text-[hsl(var(--success))]"}`}>
              {syncMessage.text}
            </p>
          )}
        </div>}
      </Modal>
    </div>
  );
}
