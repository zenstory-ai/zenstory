import { useEffect, useRef } from "react";
import { useQuery } from '@tanstack/react-query';
import { Sparkles } from "lucide-react";
import { subscriptionApi, subscriptionQueryKeys } from '../../lib/subscriptionApi';
import { useTranslation } from 'react-i18next';
import { Badge } from '../ui/Badge';
import { buildUpgradeUrl, getUpgradePromptDefinition } from "../../config/upgradeExperience";
import { trackUpgradeClick, trackUpgradeExpose } from "../../lib/upgradeAnalytics";
import {
  buildStagedUpgradeSource,
  resolveUpgradeTriggerStage,
  type UpgradeTriggerStage,
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
  const lastExposedStageRef = useRef<UpgradeTriggerStage | null>(null);
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

  useEffect(() => {
    if (!quota) return;
    if (stage === "normal" || !stagedSource) return;
    if (lastExposedStageRef.current === stage) return;

    trackUpgradeExpose(stagedSource, "toast");
    lastExposedStageRef.current = stage;
  }, [quota, stage, stagedSource]);

  if (!quota) return null;

  const getVariant = (): 'success' | 'warning' | 'error' | 'info' => {
    if (stage === 'blocked') return 'error';
    if (stage === 'reminder_80') return 'warning';
    if (stage === 'reminder_50') return 'info';
    return 'success';
  };

  const shouldShowUpgradeAction = stage !== "normal" && !isUnlimited;
  const usageLabel = isUnlimited
    ? t('subscription.aiUsageUnlimited', 'AI 消息不限条数')
    : t('subscription.aiUsageCount', '今日 AI 消息 {{used}}/{{limit}} 条', { used, limit });
  const compactUsageLabel = isUnlimited ? "∞" : `${used}/${limit}`;
  const upgradeLabel = stage === "blocked"
    ? t("subscription.upgradeNow", "开通 Pro")
    : t("subscription.upgradeSuggestion", "开通 Pro，AI 消息不限条数");

  const handleUpgradeClick = () => {
    if (!stagedSource) return;

    trackUpgradeClick(stagedSource, "primary", "billing", "toast");
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
    <div className={`flex items-center text-xs ${compact ? "shrink-0 gap-1" : "min-w-0 gap-2"}`}>
      {compact ? (
        // Not a live region: the counter rarely changes, and role="status"
        // would compete with the page's real status announcements.
        <span
          data-testid="quota-badge-compact"
          title={usageLabel}
          aria-label={usageLabel}
          className="inline-flex"
        >
          {badge}
        </span>
      ) : badge}
      {shouldShowUpgradeAction && (
        compact ? (
          <button
            type="button"
            onClick={handleUpgradeClick}
            title={upgradeLabel}
            aria-label={upgradeLabel}
            className="inline-flex items-center justify-center rounded p-1 text-[hsl(var(--accent-primary))] hover:bg-[hsl(var(--bg-tertiary))] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)]"
          >
            <Sparkles size={14} aria-hidden="true" />
          </button>
        ) : (
          <button
            type="button"
            onClick={handleUpgradeClick}
            className="whitespace-nowrap text-[hsl(var(--accent-primary))] hover:underline"
          >
            {upgradeLabel}
          </button>
        )
      )}
    </div>
  );
}
