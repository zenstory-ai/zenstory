import { useQuery } from '@tanstack/react-query';
import { subscriptionApi, subscriptionQueryKeys } from '../../lib/subscriptionApi';
import { useTranslation } from 'react-i18next';
import { Badge } from '../ui/Badge';
import {
  getLocalizedPlanDisplayName,
  getSubscriptionFeatureRows,
} from '../../lib/subscriptionEntitlements';

interface SubscriptionStatusProps {
  onRedeemClick?: () => void;
  onUpgradeClick?: () => void;
}

export function SubscriptionStatus({ onRedeemClick, onUpgradeClick }: SubscriptionStatusProps) {
  const { t, i18n } = useTranslation(['settings', 'dashboard']);
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

  const statusLabel = (() => {
    if (status.status === 'active') return t('settings:subscription.active', '生效中');
    if (status.status === 'cancelled') return t('settings:subscription.cancelled', '已取消');
    if (status.status === 'none') return t('settings:subscription.none', '未开通');
    return t('settings:subscription.expired', '已过期');
  })();

  return (
    <div className="rounded-lg border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Badge variant={isPaidTier ? 'purple' : 'neutral'}>
            {getLocalizedPlanDisplayName(
              {
                display_name: status.display_name,
                display_name_en: status.display_name_en,
              },
              i18n.language,
            )}
          </Badge>
          <span className="text-xs text-[hsl(var(--text-secondary))]">
            {statusLabel}
          </span>
        </div>
      </div>

      {isPaidTier && status.status === 'active' && status.days_remaining !== null && (
        <p className="mb-3 text-sm text-[hsl(var(--text-secondary))]">
          {t('settings:subscription.daysRemaining', '剩余 {{days}} 天', { days: status.days_remaining })}
        </p>
      )}

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
          <button
            type="button"
            onClick={onUpgradeClick}
            className="px-3 py-1.5 text-sm rounded-md bg-[hsl(var(--accent-primary))] text-white hover:opacity-90 transition-colors"
          >
            {t('settings:subscription.upgradePrimary', '升级专业版')}
          </button>
        )}

        {onRedeemClick && (
          <button
            type="button"
            onClick={onRedeemClick}
            className={`px-3 py-1.5 text-sm rounded-md border transition-colors ${
              isPaidTier === false && onUpgradeClick
                ? 'border-[hsl(var(--border-color))] text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))]'
                : 'bg-[hsl(var(--accent-primary))] text-white hover:opacity-90 border-transparent'
            }`}
          >
            {t('settings:subscription.redeemCode', '兑换码')}
          </button>
        )}
      </div>
    </div>
  );
}
