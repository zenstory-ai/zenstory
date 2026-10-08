import { useQuery } from "@tanstack/react-query";
import { subscriptionApi } from "../lib/subscriptionApi";

/**
 * Monthly material-breakdown limit of the Pro plan, read from the public plan
 * catalog (the same cached query BillingPage and PricingPage use), so paywall
 * copy never hard-codes a number the backend may change.
 *
 * @param enabled - Skip the request where no paywall copy is shown.
 * @returns The positive monthly limit, or null while loading, on error, or
 *   when the catalog has no finite Pro limit (callers then omit the number).
 */
export function useProMaterialDecompositionsLimit(enabled = true): number | null {
  const { data: catalog } = useQuery({
    queryKey: ["public-subscription-catalog"],
    queryFn: () => subscriptionApi.getCatalog(),
    staleTime: 60 * 1000,
    enabled,
  });

  const limit = catalog?.tiers.find((tier) => tier.name === "pro")?.entitlements
    .material_decompositions_monthly;
  return typeof limit === "number" && limit > 0 ? limit : null;
}
