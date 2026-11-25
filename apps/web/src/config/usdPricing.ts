// Chinese readers keep the CNY catalog price paid with Alipay; non-Chinese
// locales (English today, future languages later) see Pro's USD list price
// paid by card through the merchant of record. The USD list lives here until
// the plan catalog carries per-currency prices.
export const PRO_USD_PRICE_CENTS = { month: 999, year: 7900 } as const;

export function usesUsdPricing(language: string | undefined): boolean {
  return !(language ?? "").trim().toLowerCase().startsWith("zh");
}
