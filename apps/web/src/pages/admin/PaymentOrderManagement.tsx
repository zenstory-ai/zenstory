import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { RefreshCw } from "lucide-react";
import { AdminPageState, AdminSelect } from "../../components/admin";
import { Modal } from "../../components/ui/Modal";
import { adminApi, type AdminPaymentOrder } from "../../lib/adminApi";
import { parseUTCDate } from "../../lib/dateUtils";
import { getLocaleCode } from "../../lib/i18n-helpers";

const PAGE_SIZE = 20;

export default function PaymentOrderManagement() {
  const { t } = useTranslation(["admin", "common"]);
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState<"" | "pending" | "paid">("");
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [selectedOrder, setSelectedOrder] = useState<AdminPaymentOrder | null>(null);
  const { data, isLoading, isFetching, isError, refetch } = useQuery({
    queryKey: ["admin", "payment-orders", page, status, search],
    queryFn: () => adminApi.getPaymentOrders({
      page, page_size: PAGE_SIZE, status: status || undefined, search: search || undefined,
    }),
  });
  const orders = data?.items ?? [];
  const totalPages = Math.max(1, Math.ceil((data?.total ?? 0) / PAGE_SIZE));
  const locale = getLocaleCode();
  const formatDate = (value: string | null) => value ? parseUTCDate(value).toLocaleString(locale) : "-";
  const formatAmount = (cents: number) => `¥${(cents / 100).toLocaleString(locale, {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  })}`;
  const paymentStatus = (order: AdminPaymentOrder) => t(`paymentOrders.status.${order.status}`);
  const fulfillmentStatus = (order: AdminPaymentOrder) => t(`paymentOrders.fulfillment.${order.fulfillment_status}`);
  const cycleLabel = (order: AdminPaymentOrder) => t(`paymentOrders.cycles.${order.cycle}`);
  const columnClass = "px-4 py-3 text-left text-sm";

  return (
    <div className="admin-page">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="admin-page-title">{t("paymentOrders.title")}</h1>
          <p className="admin-page-subtitle">{t("paymentOrders.subtitle")}</p>
        </div>
        <button type="button" className="btn-secondary flex items-center gap-2" disabled={isFetching} onClick={() => void refetch()}>
          <RefreshCw className="h-4 w-4" />{t("paymentOrders.refresh")}
        </button>
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
                <td className={columnClass}>{t("paymentOrders.alipay")}</td>
                <td className={columnClass}>{paymentStatus(order)}</td>
                <td className={columnClass}><span className={order.fulfillment_status === "failed" ? "text-[hsl(var(--error))]" : ""}>{fulfillmentStatus(order)}</span></td>
                <td className={`${columnClass} whitespace-nowrap`}>{formatDate(order.created_at)}</td>
                <td className={columnClass}><button type="button" className="text-[hsl(var(--accent-primary))] hover:underline" onClick={() => setSelectedOrder(order)}>{t("paymentOrders.details")}</button></td>
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
        {selectedOrder && <dl className="space-y-3 text-sm">
          {[
            ["orderNumber", selectedOrder.out_trade_no], ["providerOrderNumber", selectedOrder.trade_no ?? "-"],
            ["user", `${selectedOrder.username || selectedOrder.user_id} · ${selectedOrder.email}`],
            ["plan", `${selectedOrder.plan_display_name} · ${cycleLabel(selectedOrder)}`],
            ["amount", formatAmount(selectedOrder.amount_cents)], ["paymentMethod", t("paymentOrders.alipay")],
            ["paymentStatus", paymentStatus(selectedOrder)], ["fulfillmentStatus", fulfillmentStatus(selectedOrder)],
            ["createdAt", formatDate(selectedOrder.created_at)], ["paidAt", formatDate(selectedOrder.paid_at)],
            ["fulfilledAt", formatDate(selectedOrder.fulfilled_at)], ["failureReason", selectedOrder.failure_reason ?? "-"],
          ].map(([key, value]) => <div key={key} className="grid grid-cols-[minmax(90px,1fr)_2fr] gap-3">
            <dt className="text-[hsl(var(--text-secondary))]">{t(`paymentOrders.${key}`)}</dt>
            <dd className="min-w-0 break-words text-[hsl(var(--text-primary))]">{value}</dd>
          </div>)}
        </dl>}
      </Modal>
    </div>
  );
}
