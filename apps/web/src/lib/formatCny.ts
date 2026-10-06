import { getLocaleCode } from "./i18n-helpers";

/** ¥ with 2 decimals from ¥1 up and up to 4 below, so tiny cache-hit costs stay visible. */
export function formatCny(value: string | number, locale = getLocaleCode()): string {
  const amount = Number(value) || 0;
  return `¥${amount.toLocaleString(locale, {
    minimumFractionDigits: 2,
    maximumFractionDigits: Math.abs(amount) >= 1 ? 2 : 4,
  })}`;
}
