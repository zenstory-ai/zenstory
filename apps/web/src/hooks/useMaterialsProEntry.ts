import { useTranslation } from "react-i18next";
import { useOnlineCheckoutKnownOff } from "./useOnlineCheckoutKnownOff";

/**
 * The materials Pro entry. While online checkout is known to be off, "开通 Pro" would
 * only land on the billing page's redeem dialog, so the entry says what it does
 * (「兑换码开通」) and the caller opens the redeem dialog right there. Unknown
 * (loading or a failed request) keeps "开通 Pro" to the billing page.
 *
 * @param enabled - Skip the payment-options request where no Pro entry is shown.
 */
export function useMaterialsProEntry(enabled = true): { checkoutOff: boolean; label: string } {
  const { t } = useTranslation(["materials"]);
  const checkoutOff = useOnlineCheckoutKnownOff(enabled);
  return {
    checkoutOff,
    label: checkoutOff
      ? t("materials:quota.redeemPrimary", { defaultValue: "兑换码开通" })
      : t("materials:teaserPrimary", { defaultValue: "开通 Pro" }),
  };
}

/** One line explaining why the Pro entry is a redeem code while online checkout is off. */
export function useMaterialsCheckoutUnavailableText(): string {
  const { t } = useTranslation(["materials"]);
  return t("materials:quota.checkoutUnavailable", {
    defaultValue: "暂时不能在线付款。有兑换码的话，点「兑换码开通」就能开通 Pro。",
  });
}
