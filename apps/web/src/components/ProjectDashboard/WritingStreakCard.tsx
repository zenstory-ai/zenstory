import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Calendar, Clock, TrendingUp } from 'lucide-react';
import type { ProjectDashboardStatsResponse, WritingStreakResponse } from '../../types/writingStats';
import { Card } from '../ui/Card';
import { IconWrapper } from '../ui/IconWrapper';
import { getLocaleCode } from '../../lib/i18n-helpers';
import { formatCalendarDate } from '../../lib/dateUtils';

interface WritingStreakCardProps {
  /** Dashboard statistics data */
  stats: ProjectDashboardStatsResponse | null;
  /** Loading state */
  isLoading?: boolean;
}

/**
 * Get status display configuration for streak status
 */
function getStatusDisplay(
  status: WritingStreakResponse['streak_status'],
  t: (key: string) => string
): {
  label: string;
  colorClass: string;
} {
  switch (status) {
    case 'active':
      return {
        label: t('statistics.streak.status.active'),
        colorClass: 'text-emerald-500',
      };
    case 'at_risk':
      return {
        label: t('statistics.streak.status.atRisk'),
        colorClass: 'text-amber-500',
      };
    case 'broken':
      return {
        label: t('statistics.streak.status.broken'),
        colorClass: 'text-red-500',
      };
    case 'none':
    default:
      return {
        label: t('statistics.streak.status.none'),
        colorClass: 'text-[hsl(var(--text-secondary))]',
      };
  }
}

/**
 * WritingStreakCard component displaying writing consistency information
 */
export function WritingStreakCard({
  stats,
  isLoading = false,
}: WritingStreakCardProps) {
  const { t } = useTranslation(['dashboard']);

  // Get streak data from stats
  const streakData = useMemo(() => {
    if (!stats?.streak) return null;
    return stats.streak;
  }, [stats]);

  // Get status display configuration
  const statusDisplay = useMemo(() => {
    if (!streakData) return null;
    return getStatusDisplay(streakData.streak_status, t);
  }, [streakData, t]);

  // Loading skeleton
  if (isLoading) {
    return (
      <Card padding="lg" isLoading>
        <div className="flex items-center gap-2 mb-4">
          <div className="h-5 w-5 rounded bg-[hsl(var(--bg-tertiary))] animate-pulse" />
          <div className="h-5 w-24 rounded bg-[hsl(var(--bg-tertiary))] animate-pulse" />
        </div>
        <div className="flex items-center gap-4 mb-4">
          <div className="h-12 w-16 rounded-lg bg-[hsl(var(--bg-tertiary))] animate-pulse" />
          <div className="flex-1 space-y-2">
            <div className="h-4 w-20 rounded bg-[hsl(var(--bg-tertiary))] animate-pulse" />
            <div className="h-3 w-32 rounded bg-[hsl(var(--bg-tertiary))] animate-pulse" />
          </div>
        </div>
      </Card>
    );
  }

  // No data state
  if (!streakData || !statusDisplay) {
    return (
      <Card padding="lg">
        <div className="flex items-center gap-2 mb-4">
          <Calendar className="w-5 h-5 text-[hsl(var(--text-secondary))]" />
          <h3 className="text-base font-semibold text-[hsl(var(--text-primary))]">
            {t('statistics.streak.title')}
          </h3>
        </div>
        <div className="flex flex-col items-center justify-center py-8 text-[hsl(var(--text-secondary))]">
          <Calendar className="w-10 h-10 mb-2 opacity-50" />
          <p className="text-sm">{t('statistics.streak.startWriting')}</p>
        </div>
      </Card>
    );
  }

  return (
    <Card padding="lg" hoverable>
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <IconWrapper size="xl" variant="primary">
            <Calendar className="w-4 h-4" />
          </IconWrapper>
          <h3 className="text-base font-semibold text-[hsl(var(--text-primary))]">
            {t('statistics.streak.title')}
          </h3>
        </div>
      </div>

      {/* Main Stats Display */}
      <div className="space-y-4">
        {/* Current Streak Row */}
        <div className="flex items-center justify-between p-3 rounded-lg bg-[hsl(var(--bg-tertiary)/0.5)]">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-[hsl(var(--bg-tertiary))]">
              <Clock className="w-4 h-4 text-[hsl(var(--text-secondary))]" />
            </div>
            <div>
              <p className="text-sm text-[hsl(var(--text-secondary))]">
                {t('statistics.streak.currentStreak')}
              </p>
              <div className="flex items-baseline gap-1.5">
                <span className="text-xl font-semibold text-[hsl(var(--text-primary))]">
                  {streakData.current_streak}
                </span>
                <span className="text-sm text-[hsl(var(--text-secondary))]">
                  {t('statistics.streak.dayUnit', { count: streakData.current_streak })}
                </span>
                <span className={`text-xs font-medium ${statusDisplay.colorClass}`}>
                  • {statusDisplay.label}
                </span>
              </div>
            </div>
          </div>
        </div>

        {/* Longest Streak Row */}
        <div className="flex items-center justify-between p-3 rounded-lg bg-[hsl(var(--bg-tertiary)/0.5)]">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-[hsl(var(--bg-tertiary))]">
              <TrendingUp className="w-4 h-4 text-[hsl(var(--text-secondary))]" />
            </div>
            <div>
              <p className="text-sm text-[hsl(var(--text-secondary))]">
                {t('statistics.streak.longestStreak')}
              </p>
              <div className="flex items-baseline gap-1.5">
                <span className="text-xl font-semibold text-[hsl(var(--text-primary))]">
                  {streakData.longest_streak}
                </span>
                <span className="text-sm text-[hsl(var(--text-secondary))]">
                  {t('statistics.streak.dayUnit', { count: streakData.longest_streak })}
                </span>
              </div>
            </div>
          </div>
        </div>

        {/* Days Until Break Warning (only if at risk) */}
        {streakData.streak_status === 'at_risk' && streakData.days_until_break !== null && streakData.days_until_break > 0 && (
          <div className="flex items-center justify-between p-3 rounded-lg bg-amber-500/5 border border-amber-500/10">
            <div className="flex items-center gap-2 text-amber-600 dark:text-amber-400">
              <Clock className="w-4 h-4" />
              <span className="text-sm">
                {t('statistics.streak.daysUntilBreak', { count: streakData.days_until_break })}
              </span>
            </div>
          </div>
        )}

        {/* Recovery Info */}
        {streakData.streak_recovery_count > 0 && (
          <div className="flex items-center justify-between text-xs text-[hsl(var(--text-secondary))] pt-2">
            <span>{t('statistics.streak.recoveryAvailable')}</span>
            <span>{streakData.streak_recovery_count}</span>
          </div>
        )}

        {/* Last Writing Date */}
        {streakData.last_writing_date && (
          <div className="flex items-center justify-between text-xs text-[hsl(var(--text-secondary))] pt-2 border-t border-[hsl(var(--border-color))]">
            <span>{t('statistics.lastWritingDate')}</span>
            <span>{formatCalendarDate(streakData.last_writing_date, getLocaleCode())}</span>
          </div>
        )}
      </div>
    </Card>
  );
}

export default WritingStreakCard;
