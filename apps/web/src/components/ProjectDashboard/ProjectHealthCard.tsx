import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Activity,
  BookOpen,
  CheckCircle2,
  AlertCircle,
  XCircle,
  TrendingUp,
  Zap,
} from 'lucide-react';
import type { ProjectDashboardStatsResponse } from '../../types/writingStats';
import { useIsMobile } from '../../hooks/useMediaQuery';
import { Card } from '../ui/Card';
import { IconWrapper } from '../ui/IconWrapper';
import { plannedTotal, statsUnitKey, writtenChapters } from './statsUnits';

interface ProjectHealthCardProps {
  /** Dashboard statistics data */
  stats: ProjectDashboardStatsResponse | null;
  /** Loading state */
  isLoading?: boolean;
}

/**
 * Health status levels for project indicators
 */
type HealthLevel = 'good' | 'warning' | 'critical' | 'neutral';

/**
 * Get health indicator configuration
 */
function getHealthDisplay(
  level: HealthLevel,
  t: (key: string) => string
): {
  icon: React.ElementType;
  colorClass: string;
  bgClass: string;
  label: string;
} {
  switch (level) {
    case 'good':
      return {
        icon: CheckCircle2,
        colorClass: 'text-emerald-500',
        bgClass: 'bg-emerald-500/10',
        label: t('statistics.projectHealth.status.good'),
      };
    case 'warning':
      return {
        icon: AlertCircle,
        colorClass: 'text-amber-500',
        bgClass: 'bg-amber-500/10',
        label: t('statistics.projectHealth.status.warning'),
      };
    case 'critical':
      return {
        icon: XCircle,
        colorClass: 'text-red-500',
        bgClass: 'bg-red-500/10',
        label: t('statistics.projectHealth.status.critical'),
      };
    case 'neutral':
    default:
      return {
        icon: Activity,
        colorClass: 'text-[hsl(var(--text-secondary))]',
        bgClass: 'bg-[hsl(var(--bg-tertiary))]',
        label: t('statistics.projectHealth.status.neutral'),
      };
  }
}

/**
 * Individual health indicator item
 */
interface HealthIndicator {
  id: string;
  icon: React.ElementType;
  label: string;
  value: string;
  level: HealthLevel;
  description?: string;
}

/**
 * ProjectHealthCard component showing overall project health with simple indicators
 */
export function ProjectHealthCard({
  stats,
  isLoading = false,
}: ProjectHealthCardProps) {
  const { t } = useTranslation(['dashboard']);
  const isMobile = useIsMobile();

  // Calculate health indicators from stats
  const healthIndicators = useMemo((): HealthIndicator[] => {
    if (!stats) return [];

    const indicators: HealthIndicator[] = [];

    // Chapter Progress Indicator
    if (stats.chapter_completion) {
      const cc = stats.chapter_completion;
      const planned = plannedTotal(cc);
      if (planned !== null) {
        const chapterLevel: HealthLevel =
          cc.completion_percentage >= 50
            ? 'good'
            : cc.completion_percentage >= 25
              ? 'warning'
              : 'critical';

        indicators.push({
          id: 'chapters',
          icon: BookOpen,
          label: t(statsUnitKey(stats, 'progress')),
          value: `${cc.completed_chapters}/${planned}`,
          level: chapterLevel,
          description: t('statistics.projectHealth.indicators.chaptersDesc', {
            percent: cc.completion_percentage,
          }),
        });
      } else {
        // Unknown plan: report what is written, without grading progress.
        indicators.push({
          id: 'chapters',
          icon: BookOpen,
          label: t(statsUnitKey(stats, 'progress')),
          value: t(statsUnitKey(stats, 'written'), { count: writtenChapters(cc) }),
          level: 'neutral',
          description: cc.total_chapters > 0 ? undefined : t(statsUnitKey(stats, 'noChapters')),
        });
      }
    }

    // Writing Activity Indicator: the author's words plus what the AI wrote.
    const wordsToday = (stats.words_today || 0) + (stats.ai_words_today ?? 0);
    const wordsThisWeek = (stats.words_this_week || 0) + (stats.ai_words_this_week ?? 0);
    const activityLevel: HealthLevel =
      wordsToday > 0 ? 'good' : wordsThisWeek > 0 ? 'warning' : 'neutral';

    indicators.push({
      id: 'activity',
      icon: TrendingUp,
      label: t('statistics.projectHealth.indicators.activity'),
      value:
        wordsToday > 0
          ? t('statistics.wordCount.today')
          : wordsThisWeek > 0
            ? t('statistics.wordCount.thisWeek')
            : t('statistics.projectHealth.indicators.inactive'),
      level: activityLevel,
      description:
        wordsToday > 0
          ? t('statistics.projectHealth.indicators.activityGood', {
              count: wordsToday,
            })
          : wordsThisWeek > 0
            ? t('statistics.projectHealth.indicators.activityWarning')
            : t('statistics.projectHealth.indicators.activityInactive'),
    });

    // Streak Indicator
    if (stats.streak) {
      const streak = stats.streak;
      const streakLevel: HealthLevel =
        streak.streak_status === 'active'
          ? 'good'
          : streak.streak_status === 'at_risk'
            ? 'warning'
            : streak.streak_status === 'broken'
              ? 'critical'
              : 'neutral';

      indicators.push({
        id: 'streak',
        icon: Zap,
        label: t('statistics.projectHealth.indicators.streak'),
        value:
          streak.current_streak > 0
            ? t('statistics.streak.days', { count: streak.current_streak })
            : t('statistics.streak.status.none'),
        level: streakLevel,
        description:
          streak.current_streak > 0
            ? t('statistics.projectHealth.indicators.streakActive')
            : t('statistics.projectHealth.indicators.streakInactive'),
      });
    }

    // AI Usage Indicator
    if (stats.ai_usage?.current) {
      const ai = stats.ai_usage.current;
      const aiLevel: HealthLevel =
        ai.total_messages > 10 ? 'good' : ai.total_messages > 0 ? 'neutral' : 'neutral';

      indicators.push({
        id: 'ai',
        icon: Activity,
        label: t('statistics.projectHealth.indicators.aiUsage'),
        value: t('statistics.aiUsage.sent', { count: ai.user_messages ?? 0 }),
        level: aiLevel,
        description:
          (ai.user_messages ?? 0) > 0
            ? t('statistics.aiUsage.sentDesc')
            : t('statistics.projectHealth.indicators.aiInactive'),
      });
    }

    return indicators;
  }, [stats, t]);

  // Calculate overall health status
  const overallHealth = useMemo((): HealthLevel => {
    if (healthIndicators.length === 0) return 'neutral';

    const levels = healthIndicators.map((i) => i.level);
    if (levels.some((l) => l === 'critical')) return 'critical';
    if (levels.some((l) => l === 'warning')) return 'warning';
    if (levels.every((l) => l === 'neutral')) return 'neutral';
    return 'good';
  }, [healthIndicators]);

  const overallDisplay = getHealthDisplay(overallHealth, t);
  const OverallIcon = overallDisplay.icon;

  // Loading skeleton
  if (isLoading) {
    return (
      <Card padding={isMobile ? 'sm' : 'lg'} isLoading>
        <div className="flex items-center gap-2 mb-4">
          <div className="h-5 w-5 rounded bg-[hsl(var(--bg-tertiary))] animate-pulse" />
          <div className="h-5 w-28 rounded bg-[hsl(var(--bg-tertiary))] animate-pulse" />
        </div>
        <div className="mb-5">
          <div className="flex items-center gap-3">
            <div className={`${isMobile ? 'h-10 w-10' : 'h-12 w-12'} rounded-full bg-[hsl(var(--bg-tertiary))] animate-pulse`} />
            <div className="flex-1 space-y-2">
              <div className="h-4 w-20 rounded bg-[hsl(var(--bg-tertiary))] animate-pulse" />
              <div className="h-3 w-32 rounded bg-[hsl(var(--bg-tertiary))] animate-pulse" />
            </div>
          </div>
        </div>
        <div className="space-y-3">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className={`flex items-center gap-3 ${isMobile ? 'p-2' : 'p-2.5'}`}>
              <div className="h-5 w-5 rounded bg-[hsl(var(--bg-tertiary))] animate-pulse" />
              <div className="flex-1 space-y-1.5">
                <div className="h-3.5 w-16 rounded bg-[hsl(var(--bg-tertiary))] animate-pulse" />
                <div className="h-3 w-24 rounded bg-[hsl(var(--bg-tertiary))] animate-pulse" />
              </div>
              <div className="h-4 w-12 rounded bg-[hsl(var(--bg-tertiary))] animate-pulse" />
            </div>
          ))}
        </div>
      </Card>
    );
  }

  // No data state
  if (!stats || healthIndicators.length === 0) {
    return (
      <Card padding={isMobile ? 'sm' : 'lg'}>
        <div className="flex items-center gap-2 mb-4">
          <Activity className="w-5 h-5 text-[hsl(var(--text-secondary))]" />
          <h3 className={`${isMobile ? 'text-sm' : 'text-base'} font-semibold text-[hsl(var(--text-primary))]`}>
            {t('statistics.projectHealth.title')}
          </h3>
        </div>
        <div className="flex flex-col items-center justify-center py-8 text-[hsl(var(--text-secondary))]">
          <Activity className="w-10 h-10 mb-2 opacity-50" />
          <p className="text-sm">{t('statistics.noData')}</p>
        </div>
      </Card>
    );
  }

  return (
    <Card padding={isMobile ? 'sm' : 'lg'} hoverable>
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <IconWrapper size={isMobile ? 'lg' : 'xl'} variant="primary">
            <Activity className={`${isMobile ? 'w-3.5 h-3.5' : 'w-4 h-4'}`} />
          </IconWrapper>
          <h3 className={`${isMobile ? 'text-sm' : 'text-base'} font-semibold text-[hsl(var(--text-primary))]`}>
            {t('statistics.projectHealth.title')}
          </h3>
        </div>
      </div>

      {/* Overall Health Status */}
      <div className={`flex items-center ${isMobile ? 'gap-2' : 'gap-3'} mb-5 p-3 rounded-lg bg-[hsl(var(--bg-tertiary)/0.5)]`}>
        <div
          className={`flex ${isMobile ? 'h-10 w-10' : 'h-12 w-12'} items-center justify-center rounded-full ${overallDisplay.bgClass}`}
        >
          <OverallIcon className={`${isMobile ? 'w-5 h-5' : 'w-6 h-6'} ${overallDisplay.colorClass}`} />
        </div>
        <div>
          <p className={`${isMobile ? 'text-xs' : 'text-sm'} font-medium text-[hsl(var(--text-primary))]`}>
            {overallDisplay.label}
          </p>
          <p className="text-xs text-[hsl(var(--text-secondary))]">
            {t('statistics.projectHealth.overallStatus')}
          </p>
        </div>
      </div>

      {/* Health Indicators List */}
      <div className="space-y-2">
        {healthIndicators.map((indicator) => {
          const display = getHealthDisplay(indicator.level, t);
          const IndicatorIcon = indicator.icon;
          const StatusIcon = display.icon;

          return (
            <div
              key={indicator.id}
              className={`flex items-center gap-3 ${isMobile ? 'p-2' : 'p-2.5'} rounded-lg hover:bg-[hsl(var(--bg-tertiary)/0.5)] transition-colors`}
            >
              {/* Indicator Icon */}
              <div
                className={`flex ${isMobile ? 'h-6 w-6' : 'h-7 w-7'} items-center justify-center rounded-lg ${display.bgClass}`}
              >
                <IndicatorIcon className={`${isMobile ? 'w-3 h-3' : 'w-3.5 h-3.5'} ${display.colorClass}`} />
              </div>

              {/* Indicator Info */}
              <div className="flex-1 min-w-0">
                <div className="flex items-center justify-between">
                  <span className={`${isMobile ? 'text-xs' : 'text-sm'} font-medium text-[hsl(var(--text-primary))]`}>
                    {indicator.label}
                  </span>
                  <span className="text-xs text-[hsl(var(--text-secondary))]">
                    {indicator.value}
                  </span>
                </div>
                {indicator.description && !isMobile && (
                  <p className="text-xs text-[hsl(var(--text-secondary))] truncate mt-0.5">
                    {indicator.description}
                  </p>
                )}
              </div>

              {/* Status Icon */}
              <StatusIcon className={`${isMobile ? 'w-3.5 h-3.5' : 'w-4 h-4'} ${display.colorClass} flex-shrink-0`} />
            </div>
          );
        })}
      </div>
    </Card>
  );
}

export default ProjectHealthCard;
