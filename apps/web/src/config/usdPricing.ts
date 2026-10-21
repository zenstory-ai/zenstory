// English readers see Pro's USD list price (card checkout through the merchant of
// record); Chinese readers keep the CNY catalog price paid with Alipay. The USD list
// lives here until the plan catalog carries per-currency prices.
export const PRO_USD_PRICE_CENTS = { month: 999, year: 7900 } as const;

export function usesUsdPricing(language: string | undefined): boolean {
  return Boolean(language?.startsWith("en"));
}
