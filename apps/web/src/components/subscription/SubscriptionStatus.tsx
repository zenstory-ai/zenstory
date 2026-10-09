import { useQuery } from '@tanstack/react-query';
import { subscriptionApi, subscriptionQueryKeys } from '../../lib/subscriptionApi';
import { useTranslation } from 'react-i18next';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { useIsMobile } from '../../hooks/useMediaQuery';
import {
  getLocalizedPlanDisplayName,
  getSubscriptionFeatureRows,
  getSubscriptionStatusLine,
} from '../../lib/subscriptionEntitlements';

interface SubscriptionStatusProps {
  onRedeemClick?: () => void;
  onUpgradeClick?: () => void;
  /** Pro users renew from the billing page; there is no auto-renewal. */
  onRenewClick?: () => void;
}

export function SubscriptionStatus({ onRedeemClick, onUpgradeClick, onRenewClick }: SubscriptionStatusProps) {
  const { t, i18n } = useTranslation(['settings', 'dashboard']);
  // Phones get the 44px touch size, matching the other settings tabs' actions.
  const actionSize = useIsMobile() ? 'touch' : 'md';
  const { data: status, isLoading } = useQuery({
    queryKey: subscriptionQueryKeys.status(),
    queryFn: () => subscriptionApi.getStatus(),
  });

  if (isLoading) {
    return <div className="h-20 animate-pulse rounded-lg bg-[hsl(var(--bg-tertiary))]" />;
  }

  if (!status) return null;

  const isPaidTier = status.tier !== 'free';
  const featureRows = getSubscriptionFeatureRows(status.features, t, i18n.language);

  const statusLine = getSubscriptionStatusLine(status, t);

  return (
    <div className="rounded-lg border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={isPaidTier ? 'purple' : 'neutral'}>
            {getLocalizedPlanDisplayName(
              {
                display_name: status.display_name,
                display_name_en: status.display_name_en,
                tier: status.tier,
              },
              i18n.language,
            )}
          </Badge>
          {statusLine && (
            <span className="text-xs text-[hsl(var(--text-secondary))]">
              {statusLine}
            </span>
          )}
        </div>
      </div>

      {featureRows.length > 0 && (
        <div className="mb-3 border-t border-[hsl(var(--border-color))] pt-3">
          <p className="mb-2 text-xs font-medium text-[hsl(var(--text-secondary))]">
            {t('settings:subscription.featuresTitle', '套餐权益')}
          </p>
          <div className="space-y-1">
            {featureRows.map((row) => (
              <div key={row.key} className="flex items-center justify-between gap-3 text-xs">
                <span className="text-[hsl(var(--text-secondary))]">{row.label}</span>
                <span className="font-medium text-[hsl(var(--text-primary))]">{row.value}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        {isPaidTier === false && onUpgradeClick && (
          <Button type="button" size={actionSize} onClick={onUpgradeClick}>
            {t('settings:subscription.upgradePrimary', '开通 Pro')}
          </Button>
        )}

        {isPaidTier && onRenewClick && (
          <Button type="button" size={actionSize} onClick={onRenewClick}>
            {t('dashboard:billing.ctaRenewPro', '续费 Pro')}
          </Button>
        )}

        {onRedeemClick && (
          <Button
            type="button"
            size={actionSize}
            onClick={onRedeemClick}
            variant={
              (isPaidTier === false && onUpgradeClick) || (isPaidTier && onRenewClick)
                ? 'secondary'
                : 'primary'
            }
          >
            {t('settings:subscription.redeemCode', '兑换码')}
          </Button>
        )}
      </div>
    </div>
  );
}
