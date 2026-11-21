import { Sparkles } from "lucide-react";
import { useUpgradeExposure } from "../../hooks/useUpgradeExposure";
import { useTranslation } from "react-i18next";
import { usePaidPlanWhenOpen } from "../../hooks/usePaidPlanWhenOpen";
import { trackUpgradeClick, type UpgradeFunnelSurface } from "../../lib/upgradeAnalytics";
import {
  buildUpgradeUrl,
  getUpgradePromptDefinition,
  type UpgradePromptScenario,
} from "../../config/upgradeExperience";
import { Modal } from "../ui/Modal";
import { Button } from "../ui/Button";

export type UpgradePromptId = UpgradePromptScenario;

interface UpgradePromptModalProps {
  open: boolean;
  onClose: () => void;
  /**
   * Optional prompt scenario from `config/upgradeExperience.ts`.
   * Supplies default `source`, `surface`, `primaryDestination` ("billing"),
   * `secondaryDestination` ("pricing"), and `onPrimary`/`onSecondary` navigation.
   * Any explicitly provided prop overrides the scenario default.
   */
  promptId?: UpgradePromptId;
  title: string;
  description: string;
  /**
   * What a paid author can still do about this limit (e.g. "删掉不用的技能就能腾出名额。").
   * Shown instead of the upgrade copy when the author is already on a paid plan.
   */
  paidDescription?: string;
  primaryLabel: string;
  onPrimary?: () => void;
  secondaryLabel?: string;
  onSecondary?: () => void;
  source?: string;
  surface?: UpgradeFunnelSurface;
  primaryDestination?: string;
  secondaryDestination?: string;
}

export function UpgradePromptModal({
  open,
  onClose,
  promptId,
  title,
  description,
  paidDescription,
  primaryLabel,
  onPrimary,
  secondaryLabel,
  onSecondary,
  source,
  surface,
  primaryDestination,
  secondaryDestination,
}: UpgradePromptModalProps) {
  const { t } = useTranslation(["dashboard"]);
  const promptDefinition = promptId ? getUpgradePromptDefinition(promptId) : undefined;
  const resolvedSource = source ?? promptDefinition?.source;
  const resolvedSurface = surface ?? promptDefinition?.surface ?? "modal";
  const resolvedPrimaryDestination = primaryDestination ?? (promptDefinition ? "billing" : undefined);
  const resolvedSecondaryDestination = secondaryDestination ?? (promptDefinition ? "pricing" : undefined);

  const handlePrimary =
    onPrimary ??
    (promptDefinition
      ? () => {
          window.location.assign(
            buildUpgradeUrl(promptDefinition.billingPath, resolvedSource ?? promptDefinition.source),
          );
        }
      : undefined);

  const handleSecondary =
    onSecondary ??
    (promptDefinition && secondaryLabel
      ? () => {
          window.location.assign(
            buildUpgradeUrl(promptDefinition.pricingPath, resolvedSource ?? promptDefinition.source),
          );
        }
      : undefined);

  // A paid author hitting a paid-plan limit has nothing to upgrade to: say the limit is
  // reached instead of offering "开通 Pro", and keep it out of the upgrade funnel.
  const { isPaid, resolved } = usePaidPlanWhenOpen(open);

  const exposureRef = useUpgradeExposure({
    enabled: open && resolved && !isPaid,
    source: resolvedSource,
    surface: resolvedSurface,
    placement: "limit-dialog",
  });

  if (open && !resolved) {
    // Plan not known yet: never flash "开通 Pro" at someone who may already have it.
    return (
      <Modal
        open={open}
        onClose={onClose}
        size="md"
        title={title}
      >
        <div className="space-y-3" data-testid="upgrade-prompt-pending" aria-busy="true">
          <div className="h-4 w-full animate-pulse rounded bg-[hsl(var(--bg-tertiary))]" />
          <div className="h-4 w-2/3 animate-pulse rounded bg-[hsl(var(--bg-tertiary))]" />
          <div className="h-10 w-full animate-pulse rounded-lg bg-[hsl(var(--bg-tertiary))]" />
        </div>
      </Modal>
    );
  }

  if (isPaid) {
    return (
      <Modal
        open={open}
        onClose={onClose}
        size="md"
        title={title}
        footer={
          <Button size="touch" className="w-full" onClick={onClose}>
            {t("dashboard:billing.gotIt", "知道了")}
          </Button>
        }
      >
        <p className="text-sm leading-relaxed text-[hsl(var(--text-secondary))]">
          {paidDescription || t("dashboard:billing.paidLimitReached", "当前套餐的这项额度已经用满了。")}
        </p>
      </Modal>
    );
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      size="md"
      title={title}
      footer={
        <div className="w-full flex flex-col gap-2">
          <Button
            size="touch"
            className="w-full"
            onClick={() => {
              if (resolvedSource) {
                trackUpgradeClick(resolvedSource, "primary", resolvedPrimaryDestination, resolvedSurface);
              }
              handlePrimary?.();
              onClose();
            }}
          >
            {primaryLabel}
          </Button>

          {secondaryLabel && handleSecondary && (
            <Button
              variant="secondary"
              size="touch"
              className="w-full"
              onClick={() => {
                if (resolvedSource) {
                  trackUpgradeClick(resolvedSource, "secondary", resolvedSecondaryDestination, resolvedSurface);
                }
                handleSecondary();
                onClose();
              }}
            >
              {secondaryLabel}
            </Button>
          )}
        </div>
      }
    >
      <div ref={exposureRef} className="space-y-3">
        <div className="inline-flex items-center gap-1.5 rounded-full border border-[hsl(var(--accent-primary)/0.35)] bg-[hsl(var(--accent-primary)/0.08)] px-2.5 py-1 text-xs font-medium text-[hsl(var(--accent-primary))]">
          <Sparkles className="h-3.5 w-3.5" />
          {/* "Pro" reads the same in every locale. */}
          <span>Pro</span>
        </div>

        <p className="text-sm leading-relaxed text-[hsl(var(--text-secondary))]">{description}</p>
      </div>
    </Modal>
  );
}

export default UpgradePromptModal;
