import { useEffect, useRef } from "react";
import { useQuery } from '@tanstack/react-query';
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

export function QuotaBadge() {
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

  const handleUpgradeClick = () => {
    if (!stagedSource) return;

    trackUpgradeClick(stagedSource, "primary", "billing", "toast");
    window.location.assign(
      buildUpgradeUrl(settingsUpgradePrompt.billingPath, stagedSource)
    );
  };

  return (
    <div className="flex items-center gap-2 text-xs">
      <Badge
        variant={getVariant()}
        icon={
          <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
          </svg>
        }
      >
        {usageLabel}
      </Badge>
      {shouldShowUpgradeAction && (
        <button
          type="button"
          onClick={handleUpgradeClick}
          className="text-[hsl(var(--accent-primary))] hover:underline"
        >
          {stage === "blocked"
            ? t("subscription.upgradeNow", "开通 Pro")
            : t("subscription.upgradeSuggestion", "开通 Pro，AI 消息不限条数")}
        </button>
      )}
    </div>
  );
}
