import { useState } from "react";
import { useTranslation } from "react-i18next";
import { UpgradePromptModal } from "./UpgradePromptModal";
import { buildUpgradeUrl, getUpgradePromptDefinition } from "../../config/upgradeExperience";
import { useProMaterialDecompositionsLimit } from "../../hooks/useProMaterialDecompositionsLimit";

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
  const description = useMaterialsUploadDescription(open);

  return (
    <UpgradePromptModal
      open={open}
      onClose={onClose}
      source={source}
      primaryDestination="billing"
      secondaryDestination="pricing"
      title={t("materials:quota.uploadTitle", { defaultValue: "素材库是 Pro 功能" })}
      description={description}
      primaryLabel={t("materials:quota.upgradePrimary", { defaultValue: "开通 Pro" })}
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
  const description = useMaterialsUploadDescription();

  return (
    <div className={`space-y-2 ${className}`} data-testid="materials-upgrade-notice">
      <p className="text-sm font-medium text-[hsl(var(--text-primary))]">
        {t("materials:quota.uploadTitle", { defaultValue: "素材库是 Pro 功能" })}
      </p>
      <p className="text-xs leading-5 text-[hsl(var(--text-secondary))]">{description}</p>
      <button type="button" className="btn-primary h-8 px-3 text-xs" onClick={() => setOpen(true)}>
        {t("materials:teaserPrimary", { defaultValue: "开通 Pro" })}
      </button>
      <MaterialsUpgradePromptModal open={open} onClose={() => setOpen(false)} source={source} />
    </div>
  );
}
