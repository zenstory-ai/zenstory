import { useQuery } from "@tanstack/react-query";
import { paymentApi, paymentQueryKeys } from "../lib/paymentApi";

/**
 * Whether the server has said online checkout is off (Zpay not configured).
 *
 * Only a successful answer with `enabled !== true` counts. While loading or when the
 * request fails the state is unknown and callers keep their normal "开通 Pro" path:
 * a single failed request on production (where checkout is on) must not tell authors
 * they cannot pay.
 *
 * @param enabled - Skip the request where no Pro entry is shown.
 */
export function useOnlineCheckoutKnownOff(enabled = true): boolean {
  const { data, isSuccess } = useQuery({
    queryKey: paymentQueryKeys.options(),
    queryFn: paymentApi.getOptions,
    retry: false,
    staleTime: 60 * 1000,
    enabled,
  });
  return enabled && isSuccess && data?.enabled !== true;
}
