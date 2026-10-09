import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { UpgradePromptModal } from "./UpgradePromptModal";
import { RedeemCodeModal } from "./RedeemCodeModal";
import { buildUpgradeUrl, getUpgradePromptDefinition } from "../../config/upgradeExperience";
import { useProMaterialDecompositionsLimit } from "../../hooks/useProMaterialDecompositionsLimit";
import { useMaterialsCheckoutUnavailableText, useMaterialsProEntry } from "../../hooks/useMaterialsProEntry";
import { subscriptionApi, subscriptionQueryKeys } from "../../lib/subscriptionApi";
import { formatBeijingPeriodDate } from "../../lib/dateUtils";

const materialUploadUpgradePrompt = getUpgradePromptDefinition("material_upload_quota_blocked");

/** Paywall body copy; the monthly number comes from the Pro plan catalog, never a constant. */
function useMaterialsUploadDescription(enabled = true): string {
  const { t } = useTranslation(["materials"]);
  const limit = useProMaterialDecompositionsLimit(enabled);
  return limit === null
    ? t("materials:quota.uploadDescriptionNoLimit", {
        defaultValue: "免费版不含素材库，开通 Pro 后即可使用。",
      })
    : t("materials:quota.uploadDescription", {
        defaultValue: "免费版不含素材库。开通 Pro 后，每月可拆解 {{limit}} 次。",
        limit,
      });
}

/**
 * What a paid author can do when the materials paywall still shows up for them: once
 * this month's breakdowns are used up, say when they come back (Beijing month start).
 * Without that fact the generic paid sentence of UpgradePromptModal stays.
 */
function useMaterialsPaidDescription(enabled: boolean): string | undefined {
  const { t } = useTranslation(["materials"]);
  const { data: quota } = useQuery({
    queryKey: subscriptionQueryKeys.quota(),
    queryFn: () => subscriptionApi.getQuota(),
    enabled,
  });
  const decompositions = quota?.material_decompositions;
  if (!decompositions || decompositions.limit <= 0 || decompositions.used < decompositions.limit) {
    return undefined;
  }
  return t("materials:quotaExhausted", {
    defaultValue: "本月 {{limit}} 次拆解已用完，将于 {{resetAt}} 恢复。已拆好的内容仍可查看和引用。",
    limit: decompositions.limit,
    resetAt: decompositions.reset_at
      ? formatBeijingPeriodDate(decompositions.reset_at)
      : t("materials:quotaResetFallback", { defaultValue: "下月 1 日" }),
  });
}

interface MaterialsUpgradePromptModalProps {
  open: boolean;
  onClose: () => void;
  source?: string;
}

/** The materials-library paywall modal (shared by the materials page, sidebar and detail page). */
export function MaterialsUpgradePromptModal({
  open,
  onClose,
  source = materialUploadUpgradePrompt.source,
}: MaterialsUpgradePromptModalProps) {
  const { t, i18n } = useTranslation(["materials"]);
  const [redeemOpen, setRedeemOpen] = useState(false);
  const description = useMaterialsUploadDescription(open);
  const paidDescription = useMaterialsPaidDescription(open);
  const { checkoutOff, label } = useMaterialsProEntry(open);
  const checkoutUnavailable = useMaterialsCheckoutUnavailableText();

  return (
    <>
      <UpgradePromptModal
        open={open}
        onClose={onClose}
        source={source}
        primaryDestination={checkoutOff ? "redeem" : "billing"}
        secondaryDestination="pricing"
        title={t("materials:quota.uploadTitle", { defaultValue: "素材库是 Pro 功能" })}
        description={
          checkoutOff
            ? // Chinese sentences join without a space; English needs one.
              [description, checkoutUnavailable].join(i18n?.language?.startsWith("en") ? " " : "")
            : description
        }
        paidDescription={paidDescription}
        primaryLabel={label}
        onPrimary={() => {
          if (checkoutOff) {
            setRedeemOpen(true);
            return;
          }
          window.location.assign(buildUpgradeUrl(materialUploadUpgradePrompt.billingPath, source));
        }}
        secondaryLabel={t("materials:quota.upgradeSecondary", { defaultValue: "查看套餐对比" })}
        onSecondary={() => {
          window.location.assign(buildUpgradeUrl(materialUploadUpgradePrompt.pricingPath, source));
        }}
      />
      <RedeemCodeModal isOpen={redeemOpen} onClose={() => setRedeemOpen(false)} source={source} />
    </>
  );
}

interface MaterialsUpgradeNoticeProps {
  /** Analytics source for the upgrade funnel. */
  source: string;
  className?: string;
}

/**
 * Inline notice shown where paid materials would appear for a plan without
 * the materials library (instead of a "failed to load" error).
 */
export function MaterialsUpgradeNotice({ source, className = "" }: MaterialsUpgradeNoticeProps) {
  const { t } = useTranslation(["materials"]);
  const [open, setOpen] = useState(false);
  const description = useMaterialsUploadDescription();
  const { label } = useMaterialsProEntry();

  return (
    <div className={`space-y-2 ${className}`} data-testid="materials-upgrade-notice">
      <p className="text-sm font-medium text-[hsl(var(--text-primary))]">
        {t("materials:quota.uploadTitle", { defaultValue: "素材库是 Pro 功能" })}
      </p>
      <p className="text-xs leading-5 text-[hsl(var(--text-secondary))]">{description}</p>
      <button type="button" className="btn-primary h-8 px-3 text-xs" onClick={() => setOpen(true)}>
        {label}
      </button>
      <MaterialsUpgradePromptModal open={open} onClose={() => setOpen(false)} source={source} />
    </div>
  );
}
