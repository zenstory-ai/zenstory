import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import {
  Bot,
  MessageSquare,
  Sparkles,
  Clock,
  ChevronRight,
  Lightbulb,
  Plus,
} from 'lucide-react';
import type { ProjectDashboardStatsResponse, AIUsagePeriodSummary } from '../../types/writingStats';
import { Card } from '../ui/Card';
import { Badge } from '../ui/Badge';
import { IconWrapper } from '../ui/IconWrapper';
import { getLocaleCode } from '../../lib/i18n-helpers';
import { subscriptionApi, subscriptionQueryKeys } from '../../lib/subscriptionApi';

interface AiUsageCardProps {
  /** Dashboard statistics data */
  stats: ProjectDashboardStatsResponse | null;
  /** Loading state */
  isLoading?: boolean;
  /** Project ID for navigation actions */
  projectId?: string;
}

/**
 * Format a number with locale-aware formatting
 */
function formatNumber(num: number): string {
  if (num >= 1000000) {
    return `${(num / 1000000).toFixed(1)}M`;
  }
  if (num >= 1000) {
    return `${(num / 1000).toFixed(1)}K`;
  }
  return num.toLocaleString();
}

/**
 * Get relative time string
 */
function getRelativeTime(dateStr: string | null, t: (key: string, opts?: Record<string, unknown>) => string): string {
  if (!dateStr) return '';

  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);

  if (diffMins < 1) return t('time.justNow');
  if (diffMins < 60) return t('time.minutesAgo', { count: diffMins });
  if (diffHours < 24) return t('time.hoursAgo', { count: diffHours });
  if (diffDays === 1) return t('time.yesterday');
  if (diffDays < 7) return t('time.daysAgo', { count: diffDays });
  return date.toLocaleDateString(getLocaleCode());
}

/**
 * Get period summary display data
 */
function getPeriodDisplay(
  period: AIUsagePeriodSummary | undefined,
  t: (key: string, options?: Record<string, unknown>) => string,
  labelKey: string
): {
  label: string;
  totalMessages: number;
  messages: string;
  tokens: string;
} {
  if (!period) {
    return {
      label: t(labelKey),
      totalMessages: 0,
      messages: '0',
      tokens: '0',
    };
  }

  return {
    label: t(labelKey),
    // Messages the author sent; the AI's replies and tool steps are not counted.
    totalMessages: period.user ?? 0,
    messages: formatNumber(period.user ?? 0),
    tokens: formatNumber(period.estimated_tokens),
  };
}

/**
 * AiUsageCard component highlighting actionable AI suggestions and collaboration status
 */
export function AiUsageCard({
  stats,
  isLoading = false,
  projectId,
}: AiUsageCardProps) {
  const { t } = useTranslation(['dashboard']);
  // Same cached quota the chat badge polls. The daily-allowance sentence only applies to
  // a finite daily limit (free); Pro (-1) and an unknown quota see only the first sentence.
  const { data: quota } = useQuery({
    queryKey: subscriptionQueryKeys.quota(),
    queryFn: () => subscriptionApi.getQuota(),
  });
  const aiMessageLimit = quota?.ai_conversations?.limit;
  const showDailyAllowance = typeof aiMessageLimit === 'number' && aiMessageLimit > 0;
  const navigate = useNavigate();

  // Get AI usage data from stats
  const aiUsageData = useMemo(() => {
    if (!stats?.ai_usage) return null;
    return stats.ai_usage;
  }, [stats]);

  // Get current stats
  const currentStats = useMemo(() => {
    return aiUsageData?.current;
  }, [aiUsageData]);

  // Determine if AI collaboration is active
  const collaborationStatus = useMemo(() => {
    if (!currentStats) return { isActive: false, label: '', hint: '' };

    const hasActiveSession = currentStats.active_session_id !== null;
    const hasRecentActivity = (aiUsageData?.today?.total ?? 0) > 0;
    const hasUsage = currentStats.total_messages > 0;

    if (hasActiveSession && hasRecentActivity) {
      return {
        isActive: true,
        label: t('statistics.projectHealth.indicators.aiActive'),
        hint: t('statistics.aiUsage.continueSession'),
      };
    }

    if (hasRecentActivity) {
      return {
        isActive: true,
        label: t('statistics.projectHealth.indicators.aiActive'),
        hint: t('statistics.aiUsage.recentActivity'),
      };
    }

    if (hasUsage) {
      return {
        isActive: false,
        label: t('statistics.projectHealth.indicators.aiInactive'),
        hint: t('statistics.aiUsage.tryAgain'),
      };
    }

    return {
      isActive: false,
      label: t('statistics.projectHealth.indicators.aiInactive'),
      hint: t('statistics.projectHealth.indicators.aiInactive'),
    };
  }, [aiUsageData, currentStats, t]);

  // Get period summaries (secondary info)
  const periodSummaries = useMemo(() => {
    if (!aiUsageData) return null;

    return {
      today: getPeriodDisplay(aiUsageData.today, t, 'statistics.aiUsage.today'),
      thisWeek: getPeriodDisplay(aiUsageData.this_week, t, 'statistics.aiUsage.thisWeek'),
      thisMonth: getPeriodDisplay(aiUsageData.this_month, t, 'statistics.aiUsage.thisMonth'),
    };
  }, [aiUsageData, t]);

  // Get relative time for last interaction
  const lastInteractionRelative = useMemo(() => {
    const lastInteractionTime =
      currentStats?.last_interaction_date ?? currentStats?.last_interaction_at ?? null;
    return getRelativeTime(lastInteractionTime, t);
  }, [currentStats, t]);

  const openAiWorkspace = () => {
    if (!projectId) return;
    navigate(`/project/${projectId}`);
  };

  // Action suggestions based on current state
  const suggestions = useMemo(() => {
    const items: Array<{
      icon: React.ElementType;
      title: string;
      description: string;
      action: string;
      variant: 'primary' | 'secondary';
    }> = [];

    if (currentStats?.active_session_id) {
      items.push({
        icon: MessageSquare,
        title: t('statistics.aiUsage.continueConversation'),
        description: lastInteractionRelative
          ? t('statistics.aiUsage.lastInteraction', { time: lastInteractionRelative })
          : t('statistics.aiUsage.sessionInProgress'),
        action: t('statistics.aiUsage.resume'),
        variant: 'primary',
      });
    }

    if (currentStats?.total_sessions === 0 || !currentStats) {
      items.push({
        icon: Lightbulb,
        title: t('statistics.aiUsage.getStartTitle'),
        description: t('statistics.aiUsage.getStartDesc'),
        action: t('statistics.aiUsage.startChat'),
        variant: 'primary',
      });
    }

    // Add suggestion for more AI help
    if (currentStats && currentStats.total_sessions > 0 && !currentStats.active_session_id) {
      items.push({
        icon: Sparkles,
        title: t('statistics.aiUsage.askForHelp'),
        description: t('statistics.aiUsage.askForHelpDesc'),
        action: t('statistics.aiUsage.startNew'),
        variant: 'secondary',
      });
    }

    return items;
  }, [currentStats, lastInteractionRelative, t]);

  // Loading skeleton
  if (isLoading) {
    return (
      <Card padding="lg" isLoading>
        <div className="flex items-center gap-2 mb-4">
          <div className="h-5 w-5 rounded bg-[hsl(var(--bg-tertiary))] animate-pulse" />
          <div className="h-5 w-28 rounded bg-[hsl(var(--bg-tertiary))] animate-pulse" />
        </div>
        <div className="mb-4">
          <div className="h-8 w-32 rounded bg-[hsl(var(--bg-tertiary))] animate-pulse mb-2" />
          <div className="h-4 w-48 rounded bg-[hsl(var(--bg-tertiary))] animate-pulse" />
        </div>
        <div className="space-y-2">
          <div className="h-14 rounded-lg bg-[hsl(var(--bg-tertiary))] animate-pulse" />
          <div className="h-14 rounded-lg bg-[hsl(var(--bg-tertiary))] animate-pulse" />
        </div>
      </Card>
    );
  }

  // No data state - show getting started suggestion
  if (!aiUsageData || !currentStats || currentStats.total_sessions === 0) {
    return (
      <Card padding="lg" hoverable>
        {/* Header */}
        <div className="flex items-center gap-2 mb-4">
          <IconWrapper size="xl" variant="purple">
            <Bot className="w-4 h-4" />
          </IconWrapper>
          <h3 className="text-base font-semibold text-[hsl(var(--text-primary))]">
            {t('statistics.aiUsage.title')}
          </h3>
        </div>

        {/* Get Started CTA */}
        <div className="flex flex-col items-center justify-center py-6 text-center">
          <IconWrapper size="4xl" variant="purple" rounded="full">
            <Sparkles className="w-6 h-6" />
          </IconWrapper>
          <p className="text-sm font-medium text-[hsl(var(--text-primary))] mb-1">
            {t('statistics.aiUsage.getStartTitle')}
          </p>
          <p className="text-xs text-[hsl(var(--text-secondary))] mb-4 max-w-[200px]">
            {t('statistics.aiUsage.getStartDesc')}
          </p>
          <button
            type="button"
            onClick={openAiWorkspace}
            disabled={!projectId}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-purple-500 text-white text-sm font-medium hover:bg-purple-600 transition-colors"
          >
            <Plus className="w-4 h-4" />
            {t('statistics.aiUsage.startChat')}
          </button>
        </div>
      </Card>
    );
  }

  return (
    <Card padding="lg" hoverable>
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <IconWrapper size="xl" variant="purple">
            <Bot className="w-4 h-4" />
          </IconWrapper>
          <h3 className="text-base font-semibold text-[hsl(var(--text-primary))]">
            {t('statistics.aiUsage.title')}
          </h3>
        </div>
        {/* Status Badge */}
        <Badge variant={collaborationStatus.isActive ? 'success' : 'neutral'} dot>
          {collaborationStatus.label}
        </Badge>
      </div>

      {/* Status Section */}
      <div className="mb-4">
        <p className="text-sm text-[hsl(var(--text-secondary))]">{collaborationStatus.hint}</p>
      </div>

      {/* Action Suggestions */}
      {suggestions.length > 0 && (
        <div className="space-y-2 mb-4">
          {suggestions.map((suggestion, index) => {
            const IconComponent = suggestion.icon;
            return (
              <button
                key={index}
                type="button"
                onClick={openAiWorkspace}
                disabled={!projectId}
                className={`w-full flex items-center gap-3 p-3 rounded-lg transition-colors group text-left ${
                  suggestion.variant === 'primary'
                    ? 'bg-purple-500/10 hover:bg-purple-500/20 border border-purple-500/20'
                    : 'bg-[hsl(var(--bg-tertiary)/0.5)] hover:bg-[hsl(var(--bg-tertiary))] border border-transparent'
                }`}
              >
                <div
                  className={`flex h-8 w-8 items-center justify-center rounded-lg ${
                    suggestion.variant === 'primary'
                      ? 'bg-purple-500/20 text-purple-500'
                      : 'bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-secondary))]'
                  }`}
                >
                  <IconComponent className="w-4 h-4" />
                </div>
                <div className="flex-1 min-w-0">
                  <p
                    className={`text-sm font-medium ${
                      suggestion.variant === 'primary'
                        ? 'text-purple-600 dark:text-purple-400'
                        : 'text-[hsl(var(--text-primary))]'
                    }`}
                  >
                    {suggestion.title}
                  </p>
                  <p className="text-xs text-[hsl(var(--text-secondary))] truncate">
                    {suggestion.description}
                  </p>
                </div>
                <div className="flex items-center gap-1 text-xs font-medium text-purple-500 group-hover:text-purple-600">
                  <span>{suggestion.action}</span>
                  <ChevronRight className="w-3.5 h-3.5 transition-transform group-hover:translate-x-0.5" />
                </div>
              </button>
            );
          })}
        </div>
      )}

      {/* Usage Summary (Secondary Info) */}
      <div className="pt-4 border-t border-[hsl(var(--border-color))]">
        <div className="flex items-center justify-between text-xs text-[hsl(var(--text-secondary))] mb-3">
          <span className="flex items-center gap-1.5">
            <Clock className="w-3 h-3" />
            {t('statistics.aiUsage.recentActivity')}
          </span>
          {periodSummaries && (
            <span>
              {t('statistics.aiUsage.todaySent', { count: periodSummaries.today.totalMessages })}
            </span>
          )}
        </div>

        {/* Quick Stats */}
        <div className="grid grid-cols-3 gap-2 text-center">
          <div className="p-2 rounded-lg bg-[hsl(var(--bg-tertiary))]/30">
            <p className="text-[hsl(var(--text-tertiary))] text-[10px] mb-0.5">
              {t('statistics.aiUsage.today')}
            </p>
            <p className="font-medium text-[hsl(var(--text-primary))] text-sm">
              {periodSummaries?.today.messages || '0'}
            </p>
          </div>
          <div className="p-2 rounded-lg bg-[hsl(var(--bg-tertiary))]/30">
            <p className="text-[hsl(var(--text-tertiary))] text-[10px] mb-0.5">
              {t('statistics.aiUsage.thisWeek')}
            </p>
            <p className="font-medium text-[hsl(var(--text-primary))] text-sm">
              {periodSummaries?.thisWeek.messages || '0'}
            </p>
          </div>
          <div className="p-2 rounded-lg bg-[hsl(var(--bg-tertiary))]/30">
            <p className="text-[hsl(var(--text-tertiary))] text-[10px] mb-0.5">
              {t('statistics.aiUsage.sessions', { count: currentStats?.total_sessions || 0 }).replace(/[\d,]+/, '')}
            </p>
            <p className="font-medium text-[hsl(var(--text-primary))] text-sm">
              {formatNumber(currentStats?.total_sessions || 0)}
            </p>
          </div>
        </div>
        <p className="mt-3 text-[11px] leading-relaxed text-[hsl(var(--text-tertiary))]">
          {showDailyAllowance
            ? t('statistics.aiUsage.quotaNoteWithDaily')
            : t('statistics.aiUsage.quotaNote')}
        </p>
      </div>
    </Card>
  );
}

export default AiUsageCard;
