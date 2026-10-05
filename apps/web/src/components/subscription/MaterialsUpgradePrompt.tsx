import { useState } from "react";
import { useTranslation } from "react-i18next";
import { UpgradePromptModal } from "./UpgradePromptModal";
import { buildUpgradeUrl, getUpgradePromptDefinition } from "../../config/upgradeExperience";

const materialUploadUpgradePrompt = getUpgradePromptDefinition("material_upload_quota_blocked");

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
  const { t } = useTranslation(["materials"]);

  return (
    <UpgradePromptModal
      open={open}
      onClose={onClose}
      source={source}
      primaryDestination="billing"
      secondaryDestination="pricing"
      title={t("materials:quota.uploadTitle", { defaultValue: "开通会员即可使用素材库" })}
      description={t("materials:quota.uploadDescription", {
        defaultValue: "当前套餐仅支持预览素材库能力。开通会员后，每月可使用 5 次素材拆解。",
      })}
      primaryLabel={t("materials:quota.upgradePrimary", { defaultValue: "查看升级方案" })}
      onPrimary={() => {
        window.location.assign(buildUpgradeUrl(materialUploadUpgradePrompt.billingPath, source));
      }}
      secondaryLabel={t("materials:quota.upgradeSecondary", { defaultValue: "查看套餐对比" })}
      onSecondary={() => {
        window.location.assign(buildUpgradeUrl(materialUploadUpgradePrompt.pricingPath, source));
      }}
    />
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

  return (
    <div className={`space-y-2 ${className}`} data-testid="materials-upgrade-notice">
      <p className="text-sm font-medium text-[hsl(var(--text-primary))]">
        {t("materials:quota.uploadTitle", { defaultValue: "开通会员即可使用素材库" })}
      </p>
      <p className="text-xs leading-5 text-[hsl(var(--text-secondary))]">
        {t("materials:quota.uploadDescription", {
          defaultValue: "当前套餐仅支持预览素材库能力。开通会员后，每月可使用 5 次素材拆解。",
        })}
      </p>
      <button type="button" className="btn-primary h-8 px-3 text-xs" onClick={() => setOpen(true)}>
        {t("materials:teaserPrimary", { defaultValue: "开通会员" })}
      </button>
      <MaterialsUpgradePromptModal open={open} onClose={() => setOpen(false)} source={source} />
    </div>
  );
}
