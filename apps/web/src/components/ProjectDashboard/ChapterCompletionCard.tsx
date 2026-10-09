import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { CheckCircle2, Circle, Loader2, BookOpen } from 'lucide-react';
import type { ProjectDashboardStatsResponse, ChapterDetailItem } from '../../types/writingStats';
import { Card } from '../ui/Card';
import { IconWrapper } from '../ui/IconWrapper';
import { plannedTotal, statsUnitKey, writtenChapters } from './statsUnits';

interface ChapterCompletionCardProps {
  /** Dashboard statistics data */
  stats: ProjectDashboardStatsResponse | null;
  /** Loading state */
  isLoading?: boolean;
  /** Maximum number of chapters to show in the list */
  maxVisibleChapters?: number;
}

/**
 * Get status icon and color for chapter status
 */
function getStatusDisplay(
  status: ChapterDetailItem['status'],
  t: (key: string) => string
): { icon: React.ElementType; colorClass: string; bgClass: string; label: string } {
  switch (status) {
    case 'complete':
      return {
        icon: CheckCircle2,
        colorClass: 'text-emerald-500',
        bgClass: 'bg-emerald-500/10',
        label: t('statistics.chapterCompletion.finished'),
      };
    case 'in_progress':
      return {
        icon: Loader2,
        colorClass: 'text-amber-500',
        bgClass: 'bg-amber-500/10',
        label: t('statistics.chapterCompletion.inProgress'),
      };
    case 'not_started':
    default:
      return {
        icon: Circle,
        colorClass: 'text-[hsl(var(--text-secondary))]',
        bgClass: 'bg-[hsl(var(--bg-tertiary))]',
        label: t('statistics.chapterCompletion.planned'),
      };
  }
}

/**
 * ChapterCompletionCard component showing chapter completion percentage and status
 */
export function ChapterCompletionCard({
  stats,
  isLoading = false,
  maxVisibleChapters = 5,
}: ChapterCompletionCardProps) {
  const { t } = useTranslation(['dashboard']);

  // Get chapter completion data
  const completionData = useMemo(() => {
    if (!stats?.chapter_completion) return null;
    return stats.chapter_completion;
  }, [stats]);

  // Without a planned total from the outline there is no honest percentage.
  const planned = plannedTotal(completionData);

  // Calculate progress bar color based on percentage
  const progressColorClass = useMemo(() => {
    if (!completionData) return 'bg-[hsl(var(--accent-primary))]';
    const percentage = completionData.completion_percentage;
    if (percentage >= 80) return 'bg-emerald-500';
    if (percentage >= 50) return 'bg-amber-500';
    if (percentage >= 25) return 'bg-blue-500';
    return 'bg-[hsl(var(--accent-primary))]';
  }, [completionData]);

  // Visible chapters (limited for display)
  const visibleChapters = useMemo(() => {
    if (!completionData?.chapter_details) return [];
    return completionData.chapter_details.slice(0, maxVisibleChapters);
  }, [completionData, maxVisibleChapters]);

  // Remaining chapters count
  const remainingCount = useMemo(() => {
    if (!completionData?.chapter_details) return 0;
    return Math.max(0, completionData.chapter_details.length - maxVisibleChapters);
  }, [completionData, maxVisibleChapters]);

  // Loading skeleton
  if (isLoading) {
    return (
      <Card padding="lg" isLoading>
        <div className="flex items-center gap-2 mb-4">
          <div className="h-5 w-5 rounded bg-[hsl(var(--bg-tertiary))] animate-pulse" />
          <div className="h-5 w-28 rounded bg-[hsl(var(--bg-tertiary))] animate-pulse" />
        </div>
        <div className="mb-4">
          <div className="h-8 w-20 rounded bg-[hsl(var(--bg-tertiary))] animate-pulse mb-2" />
          <div className="h-3 w-full rounded-full bg-[hsl(var(--bg-tertiary))] animate-pulse" />
        </div>
        <div className="space-y-2">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-10 rounded-lg bg-[hsl(var(--bg-tertiary))] animate-pulse" />
          ))}
        </div>
      </Card>
    );
  }

  // No data state
  if (!completionData) {
    return (
      <Card padding="lg">
        <div className="flex items-center gap-2 mb-4">
          <BookOpen className="w-5 h-5 text-[hsl(var(--text-secondary))]" />
          <h3 className="text-base font-semibold text-[hsl(var(--text-primary))]">
            {t(statsUnitKey(stats, 'completionTitle'))}
          </h3>
        </div>
        <div className="flex flex-col items-center justify-center py-8 text-[hsl(var(--text-secondary))]">
          <BookOpen className="w-10 h-10 mb-2 opacity-50" />
          <p className="text-sm">{t(statsUnitKey(stats, 'noChapters'))}</p>
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
            <BookOpen className="w-4 h-4" />
          </IconWrapper>
          <h3 className="text-base font-semibold text-[hsl(var(--text-primary))]">
            {t(statsUnitKey(stats, 'completionTitle'))}
          </h3>
        </div>
        <div className="text-sm text-[hsl(var(--text-secondary))]">
          {planned !== null
            ? t(statsUnitKey(stats, 'plannedOf'), {
                written: writtenChapters(completionData),
                planned,
                count: planned,
              })
            : t(statsUnitKey(stats, 'written'), { count: writtenChapters(completionData) })}
        </div>
      </div>

      {/* Progress Section */}
      <div className="mb-5">
        {planned !== null ? (
          <>
            <div className="flex items-baseline gap-2 mb-2">
              <span className="text-3xl font-bold text-[hsl(var(--text-primary))]">
                {completionData.completion_percentage}%
              </span>
              <span className="text-sm text-[hsl(var(--text-secondary))]">
                {t('statistics.chapterCompletion.completed')}
              </span>
            </div>
            <div className="h-2.5 bg-[hsl(var(--bg-tertiary))] rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full transition-all duration-500 ${progressColorClass}`}
                style={{ width: `${Math.min(completionData.completion_percentage, 100)}%` }}
              />
            </div>
          </>
        ) : (
          <p className="text-xs text-[hsl(var(--text-secondary))]">
            {t(statsUnitKey(stats, 'noPlanHint'))}
          </p>
        )}
        {/* Stats Summary */}
        <div className="flex items-center gap-4 mt-3 text-xs text-[hsl(var(--text-secondary))]">
          <div className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-emerald-500" />
            <span>{t(statsUnitKey(stats, 'countFinished'), { count: completionData.completed_chapters })}</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-amber-500" />
            <span>{t(statsUnitKey(stats, 'countInProgress'), { count: completionData.in_progress_chapters })}</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-[hsl(var(--text-secondary)/0.4)]" />
            <span>{t(statsUnitKey(stats, 'countPlanned'), { count: completionData.not_started_chapters })}</span>
          </div>
        </div>
      </div>

      {/* Chapter List */}
      {visibleChapters.length > 0 && (
        <div className="space-y-2">
          {visibleChapters.map((chapter, index) => {
            const statusDisplay = getStatusDisplay(chapter.status, t);
            const StatusIcon = statusDisplay.icon;

            return (
              <div
                key={chapter.outline_id || index}
                className="flex items-center gap-3 p-2.5 rounded-lg bg-[hsl(var(--bg-tertiary)/0.5)] hover:bg-[hsl(var(--bg-tertiary))] transition-colors group"
              >
                {/* Status Icon */}
                <div
                  role="img"
                  aria-label={statusDisplay.label}
                  title={statusDisplay.label}
                  className={`flex h-7 w-7 items-center justify-center rounded-lg ${statusDisplay.bgClass}`}
                >
                  <StatusIcon
                    className={`w-3.5 h-3.5 ${statusDisplay.colorClass} ${
                      chapter.status === 'in_progress' ? 'animate-spin' : ''
                    }`}
                  />
                </div>

                {/* Chapter Info */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium text-[hsl(var(--text-primary))] truncate">
                      {chapter.title}
                    </span>
                    {chapter.word_count > 0 && (
                      <span className="text-xs text-[hsl(var(--text-secondary))] ml-2 shrink-0">
                        {chapter.word_count.toLocaleString()} {t('statistics.wordCount.words', { count: chapter.word_count }).replace(/[\d,]+\s*/, '')}
                      </span>
                    )}
                  </div>
                  {/* Chapter progress bar (if target is set) */}
                  {chapter.target_word_count && chapter.target_word_count > 0 && (
                    <div className="mt-1.5 h-1 bg-[hsl(var(--bg-tertiary))] rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full transition-all ${
                          chapter.completion_percentage >= 100
                            ? 'bg-emerald-500'
                            : chapter.completion_percentage >= 50
                              ? 'bg-amber-500'
                              : 'bg-[hsl(var(--accent-primary))]'
                        }`}
                        style={{ width: `${Math.min(chapter.completion_percentage, 100)}%` }}
                      />
                    </div>
                  )}
                </div>
              </div>
            );
          })}

          {/* Show more indicator */}
          {remainingCount > 0 && (
            <div className="text-center py-2 text-xs text-[hsl(var(--text-secondary))]">
              {t(statsUnitKey(stats, 'more'), { count: remainingCount })}
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

export default ChapterCompletionCard;
