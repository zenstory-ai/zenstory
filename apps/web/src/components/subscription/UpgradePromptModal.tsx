import { Sparkles } from "lucide-react";
import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { usePaidPlanWhenOpen } from "../../hooks/usePaidPlanWhenOpen";
import { trackUpgradeClick, trackUpgradeExpose, type UpgradeFunnelSurface } from "../../lib/upgradeAnalytics";
import { Modal } from "../ui/Modal";
import { Button } from "../ui/Button";

interface UpgradePromptModalProps {
  open: boolean;
  onClose: () => void;
  title: string;
  description: string;
  primaryLabel: string;
  onPrimary: () => void;
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
  title,
  description,
  primaryLabel,
  onPrimary,
  secondaryLabel,
  onSecondary,
  source,
  surface = "modal",
  primaryDestination,
  secondaryDestination,
}: UpgradePromptModalProps) {
  const { t } = useTranslation(["dashboard"]);
  const trackedExposeRef = useRef(false);
  // A paid author hitting a paid-plan limit has nothing to upgrade to: say the limit is
  // reached instead of offering "开通 Pro", and keep it out of the upgrade funnel.
  const { isPaid, resolved } = usePaidPlanWhenOpen(open);

  useEffect(() => {
    if (!open) {
      trackedExposeRef.current = false;
      return;
    }

    if (source && resolved && !isPaid && !trackedExposeRef.current) {
      trackUpgradeExpose(source, surface);
      trackedExposeRef.current = true;
    }
  }, [open, source, surface, resolved, isPaid]);

  if (isPaid) {
    return (
      <Modal
        open={open}
        onClose={onClose}
        size="md"
        title={title}
        className="w-[calc(100vw-32px)] sm:w-auto"
      >
        <div className="space-y-3">
          <p className="text-sm leading-relaxed text-[hsl(var(--text-secondary))]">
            {t("dashboard:billing.paidLimitReached", "当前套餐的这项额度已经用满了。")}
          </p>
          <Button className="w-full" onClick={onClose}>
            {t("dashboard:billing.gotIt", "知道了")}
          </Button>
        </div>
      </Modal>
    );
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      size="md"
      title={title}
      className="w-[calc(100vw-32px)] sm:w-auto"
    >
      <div className="space-y-3">
        <div className="inline-flex items-center gap-1.5 rounded-full border border-[hsl(var(--accent-primary)/0.35)] bg-[hsl(var(--accent-primary)/0.08)] px-2.5 py-1 text-xs font-medium text-[hsl(var(--accent-primary))]">
          <Sparkles className="h-3.5 w-3.5" />
          {/* "Pro" reads the same in every locale. */}
          <span>Pro</span>
        </div>

        <p className="text-sm leading-relaxed text-[hsl(var(--text-secondary))]">{description}</p>

        <div className="flex flex-col gap-2 pt-1">
          <Button
            className="w-full"
            onClick={() => {
              if (source) {
                trackUpgradeClick(source, "primary", primaryDestination, surface);
              }
              onPrimary();
              onClose();
            }}
          >
            {primaryLabel}
          </Button>

          {secondaryLabel && onSecondary && (
            <Button
              variant="secondary"
              className="w-full"
              onClick={() => {
                if (source) {
                  trackUpgradeClick(source, "secondary", secondaryDestination, surface);
                }
                onSecondary();
                onClose();
              }}
            >
              {secondaryLabel}
            </Button>
          )}
        </div>
      </div>
    </Modal>
  );
}

export default UpgradePromptModal;
