import { aiQuotaPeriodKey } from "../../hooks/useAiMessageQuota";
import { useMobileLayout } from "../../contexts/MobileLayoutContext";
import { usePaidPlanWhenOpen } from "../../hooks/usePaidPlanWhenOpen";
import { useUpgradeExposure } from "../../hooks/useUpgradeExposure";
import { useQuery } from '@tanstack/react-query';
import { Sparkles } from "lucide-react";
import { subscriptionApi, subscriptionQueryKeys } from '../../lib/subscriptionApi';
import { useTranslation } from 'react-i18next';
import { Badge } from '../ui/Badge';
import { IconButton } from '../ui/IconButton';
import { buildUpgradeUrl, getUpgradePromptDefinition } from "../../config/upgradeExperience";
import { trackUpgradeClick } from "../../lib/upgradeAnalytics";
import {
  buildStagedUpgradeSource,
  resolveUpgradeTriggerStage,
} from "../../lib/upgradeTriggerStrategy";

interface QuotaBadgeProps {
  /**
   * Narrow headers (the chat panel) show only "{used}/{limit}" and an icon-only
   * upgrade button; the full wording moves into title / aria-label.
   * Other surfaces keep the default full label.
   * @default false
   */
  compact?: boolean;
}

export function QuotaBadge({ compact = false }: QuotaBadgeProps = {}) {
  const { t } = useTranslation('settings');
  const settingsUpgradePrompt = getUpgradePromptDefinition("settings_subscription_upgrade");
  const { data: quota } = useQuery({
    queryKey: subscriptionQueryKeys.quota(),
    queryFn: () => subscriptionApi.getQuota(),
    refetchInterval: 60000,
  });

  const used = quota?.ai_conversations.used ?? 0;
  const limit = quota?.ai_conversations.limit ?? -1;
  const isUnlimited = limit === -1;
  const stage = !quota || isUnlimited
    ? "normal"
    : resolveUpgradeTriggerStage({ used, limit });
  const stagedSource = buildStagedUpgradeSource(settingsUpgradePrompt.source, stage);

  const { isMobile, activePanel } = useMobileLayout();
  const { isPaid, resolved } = usePaidPlanWhenOpen(Boolean(quota) && stage !== "normal" && !isUnlimited);
  const canUpgrade = resolved && !isPaid;
  const exposureRef = useUpgradeExposure({
    enabled: Boolean(quota) && stage !== "normal" && !isUnlimited && canUpgrade && (!compact || !isMobile || activePanel === "chat"),
    source: stagedSource, surface: "badge",
    placement: compact ? "chat-header" : "page-header",
    period: aiQuotaPeriodKey(quota?.ai_conversations.reset_at),
  });

  if (!quota) return null;

  const getVariant = (): 'success' | 'warning' | 'error' | 'info' => {
    if (stage === 'blocked') return 'error';
    if (stage === 'reminder_80') return 'warning';
    if (stage === 'reminder_50') return 'info';
    return 'success';
  };

  const shouldShowUpgradeAction = stage !== "normal" && !isUnlimited && canUpgrade;
  const usageLabel = isUnlimited
    ? t('subscription.aiUsageUnlimited', 'AI 消息不限条数')
    : t('subscription.aiUsageCount', '今日 AI 消息 {{used}}/{{limit}} 条', { used, limit });
  const compactUsageLabel = isUnlimited ? "∞" : `${used}/${limit}`;
  const upgradeLabel = stage === "blocked"
    ? t("subscription.upgradeNow", "开通 Pro")
    : t("subscription.upgradeSuggestion", "开通 Pro，AI 消息不限条数");

  const handleUpgradeClick = () => {
    if (!stagedSource) return;

    trackUpgradeClick(stagedSource, "primary", "billing", "badge");
    window.location.assign(
      buildUpgradeUrl(settingsUpgradePrompt.billingPath, stagedSource)
    );
  };

  const badge = (
    <Badge
      variant={getVariant()}
      className={compact ? "whitespace-nowrap tabular-nums" : "whitespace-nowrap"}
      icon={
        <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
        </svg>
      }
    >
      {compact ? compactUsageLabel : usageLabel}
    </Badge>
  );

  return (
    <div ref={exposureRef} className={`flex items-center text-xs ${compact ? "shrink-0 gap-1" : "min-w-0 max-w-full flex-wrap gap-2"}`}>
      {compact ? (
        // Not a live region: the counter rarely changes, and role="status"
        // would compete with the page's real status announcements. Screen readers
        // get the full wording; the bare digits are visual only.
        <span
          data-testid="quota-badge-compact"
          title={usageLabel}
          className="inline-flex"
        >
          <span className="sr-only">{usageLabel}</span>
          <span aria-hidden="true" className="inline-flex">{badge}</span>
        </span>
      ) : badge}
      {shouldShowUpgradeAction && (
        compact ? (
          <IconButton
            onClick={handleUpgradeClick}
            label={upgradeLabel}
            icon={<Sparkles size={14} aria-hidden="true" />}
            tone="plain"
            className="text-[hsl(var(--accent-primary))]"
          />
        ) : (
          <button
            type="button"
            onClick={handleUpgradeClick}
            title={upgradeLabel}
            aria-label={upgradeLabel}
            className="inline-flex min-h-11 md:min-h-10 shrink-0 items-center justify-center whitespace-nowrap rounded-lg px-3 text-[hsl(var(--accent-primary))] hover:bg-[hsl(var(--bg-tertiary))] hover:underline"
          >
            {t('subscription.upgradeNow', '开通 Pro')}
          </button>
        )
      )}
    </div>
  );
}
