import { useQuery } from '@tanstack/react-query';
import { subscriptionApi, subscriptionQueryKeys } from '../../lib/subscriptionApi';
import { useTranslation } from 'react-i18next';
import { Badge } from '../ui/Badge';
import { getLocalizedPlanDisplayName } from '../../lib/subscriptionEntitlements';

// 面板只展示认识的权益，按固定顺序、带默认中文名。后端新增字段（例如素材库开关）
// 若前端还没有名称，不能把 materials_library_access 这样的原始键名直接给用户看。
const FEATURE_LABELS: ReadonlyArray<readonly [string, string]> = [
  ['ai_conversations_per_day', '每日 AI 消息数'],
  ['max_projects', '最大项目数'],
  ['context_window_tokens', 'AI 单次可参考的内容量'],
  ['file_versions_per_file', '每个文件保留的版本数'],
  ['export_formats', '导出格式'],
  ['materials_library_access', '素材库'],
  ['material_uploads', '每月素材上传次数'],
  ['material_decompositions', '每月素材拆解次数'],
  ['custom_skills', '自定义技能数量'],
  ['inspiration_copies_monthly', '灵感复用次数'],
  ['custom_prompts', '自定义提示词'],
  ['priority_support', '优先支持'],
];

interface SubscriptionStatusProps {
  onRedeemClick?: () => void;
  onUpgradeClick?: () => void;
}

export function SubscriptionStatus({ onRedeemClick, onUpgradeClick }: SubscriptionStatusProps) {
  const { t, i18n } = useTranslation('settings');
  const { data: status, isLoading } = useQuery({
    queryKey: subscriptionQueryKeys.status(),
    queryFn: () => subscriptionApi.getStatus(),
  });

  if (isLoading) {
    return <div className="h-20 animate-pulse rounded-lg bg-[hsl(var(--bg-tertiary))]" />;
  }

  if (!status) return null;

  const isPaidTier = status.tier !== 'free';
  const features: Record<string, unknown> = { ...(status.features ?? {}) };
  const featureEntries = FEATURE_LABELS.filter(([key]) => key in features).map(
    ([key, label]) => [key, label, features[key]] as const,
  );

  const statusLabel = (() => {
    if (status.status === 'active') return t('subscription.active', '生效中');
    if (status.status === 'cancelled') return t('subscription.cancelled', '已取消');
    if (status.status === 'none') return t('subscription.none', '未开通');
    return t('subscription.expired', '已过期');
  })();

  const formatFeatureValue = (key: string, value: unknown): string => {
    if (value === -1) return t('subscription.unlimited', '无限');
    if (key === 'context_window_tokens' && typeof value === 'number') {
      return t('subscription.tokenCount', '{{value}} tokens', { value: value.toLocaleString(i18n.language) });
    }
    if (typeof value === 'boolean') {
      return value ? t('subscription.yes', '是') : t('subscription.no', '否');
    }
    if (Array.isArray(value)) return value.join(', ');
    if (value === null || value === undefined) return '-';
    return String(value);
  };

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
          {t('subscription.daysRemaining', '剩余 {{days}} 天', { days: status.days_remaining })}
        </p>
      )}

      {featureEntries.length > 0 && (
        <div className="mb-3 border-t border-[hsl(var(--border-color))] pt-3">
          <p className="mb-2 text-xs font-medium text-[hsl(var(--text-secondary))]">
            {t('subscription.featuresTitle', '套餐权益')}
          </p>
          <div className="space-y-1">
            {featureEntries.map(([key, label, value]) => (
              <div key={key} className="flex items-center justify-between text-xs">
                <span className="text-[hsl(var(--text-secondary))]">
                  {t(`subscription.features.${key}`, label)}
                </span>
                <span className="font-medium text-[hsl(var(--text-primary))]">
                  {formatFeatureValue(key, value)}
                </span>
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
            {t('subscription.upgradePrimary', '升级专业版')}
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
            {t('subscription.redeemCode', '兑换码')}
          </button>
        )}
      </div>
    </div>
  );
}
