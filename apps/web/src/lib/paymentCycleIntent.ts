import type { PaymentCycle } from "../types/payment";

/** Accept only the billing-cycle spellings used by our own links and checkout state. */
export function parsePaymentCycleIntent(value: string | null): PaymentCycle | null {
  const normalized = value?.trim().toLowerCase();
  if (normalized === "month" || normalized === "monthly") return "month";
  if (normalized === "year" || normalized === "yearly") return "year";
  return null;
}
