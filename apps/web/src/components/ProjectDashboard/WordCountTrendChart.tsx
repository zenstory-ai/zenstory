import { useState, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { TrendingUp, Calendar, ChevronDown, ChevronUp, BarChart3 } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import type { ProjectDashboardStatsResponse } from '../../types/writingStats';
import { writingStatsApi } from '../../lib/writingStatsApi';
import { Card } from '../ui/Card';
import { IconWrapper } from '../ui/IconWrapper';

export type TimeRange = 'daily' | 'weekly' | 'monthly';

interface WordCountTrendChartProps {
  /** Project ID used to load trend series data */
  projectId?: string;
  /** Dashboard statistics data */
  stats: ProjectDashboardStatsResponse | null;
  /** Loading state */
  isLoading?: boolean;
  /** Selected time range */
  timeRange?: TimeRange;
  /** Time range change handler */
  onTimeRangeChange?: (range: TimeRange) => void;
}

/**
 * Word count trend chart component for displaying daily/weekly/monthly writing progress
 * Simplified to show key summary with optional detailed chart
 */
export function WordCountTrendChart({
  projectId,
  stats,
  isLoading = false,
  timeRange = 'daily',
  onTimeRangeChange,
}: WordCountTrendChartProps) {
  const { t } = useTranslation(['dashboard']);
  const [isChartExpanded, setIsChartExpanded] = useState(false);

  const trendWindowByRange: Record<TimeRange, number> = {
    daily: 14,
    weekly: 84,
    monthly: 365,
  };

  const {
    data: trendResponse,
    isLoading: isTrendLoading,
    isFetching: isTrendFetching,
  } = useQuery({
    queryKey: ['writing-stats-word-trend', projectId, timeRange, trendWindowByRange[timeRange]],
    queryFn: async () => {
      if (!projectId) return null;
      return writingStatsApi.getWordCountTrend(projectId, {
        period: timeRange,
        days: trendWindowByRange[timeRange],
      });
    },
    enabled: !!projectId,
    staleTime: 60 * 1000,
  });

  // Period words: the author's own (words_*) plus what the AI wrote
  // (ai_words_*, absent from older servers).
  const periodWords = useMemo(() => {
    if (!stats) return null;
    const period = (mine: number, ai: number | undefined) => ({
      mine,
      ai: ai ?? 0,
      total: mine + (ai ?? 0),
    });
    return {
      daily: period(stats.words_today, stats.ai_words_today),
      weekly: period(stats.words_this_week, stats.ai_words_this_week),
      monthly: period(stats.words_this_month, stats.ai_words_this_month),
    };
  }, [stats]);

  // Get the appropriate word count based on time range
  const displayData = useMemo(() => {
    if (!periodWords) return null;
    const labels: Record<TimeRange, string> = {
      daily: t('statistics.wordCount.today'),
      weekly: t('statistics.wordCount.thisWeek'),
      monthly: t('statistics.wordCount.thisMonth'),
    };
    const range: TimeRange = timeRange in periodWords ? timeRange : 'daily';
    return { ...periodWords[range], value: periodWords[range].total, label: labels[range] };
  }, [periodWords, timeRange, t]);

  // Summary stats for display
  const summaryStats = useMemo(() => {
    if (!stats || !periodWords) return [];
    return [
      {
        key: 'today',
        value: periodWords.daily.total,
        label: t('statistics.wordCount.today'),
        colorClass: 'bg-emerald-500',
        isActive: timeRange === 'daily',
      },
      {
        key: 'week',
        value: periodWords.weekly.total,
        label: t('statistics.wordCount.thisWeek'),
        colorClass: 'bg-blue-500',
        isActive: timeRange === 'weekly',
      },
      {
        key: 'month',
        value: periodWords.monthly.total,
        label: t('statistics.wordCount.thisMonth'),
        colorClass: 'bg-purple-500',
        isActive: timeRange === 'monthly',
      },
    ];
  }, [stats, periodWords, timeRange, t]);

  // Trend series data for expanded chart.
  const trendBars = useMemo(() => {
    const data = trendResponse?.data;
    if (!data || data.length === 0) return [];

    return data.slice(-8).map((item, index) => {
      const row = item as {
        date?: string;
        period_label?: string;
        word_count?: number;
        net_words?: number;
      };
      const dateLabel =
        typeof row.period_label === 'string' && row.period_label
          ? row.period_label
          : typeof row.date === 'string' && row.date.length >= 10
            ? row.date.slice(5)
            : `#${index + 1}`;

      const trendValue =
        typeof row.net_words === 'number'
          ? row.net_words
          : typeof row.word_count === 'number'
            ? row.word_count
            : 0;

      return {
        key: `${row.date ?? 'row'}-${index}`,
        value: trendValue,
        label: dateLabel,
      };
    });
  }, [trendResponse]);

  const trendMaxValue = useMemo(() => {
    return Math.max(...trendBars.map((d) => Math.abs(d.value)), 1);
  }, [trendBars]);
  const isTrendSeriesLoading = isTrendLoading || (isTrendFetching && trendBars.length === 0);

  // Loading skeleton
  if (isLoading) {
    return (
      <Card padding="lg" isLoading>
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <div className="h-8 w-8 rounded-lg bg-[hsl(var(--bg-tertiary))] animate-pulse" />
            <div className="h-5 w-28 rounded bg-[hsl(var(--bg-tertiary))] animate-pulse" />
          </div>
        </div>
        <div className="mb-4">
          <div className="h-8 w-24 rounded bg-[hsl(var(--bg-tertiary))] animate-pulse mb-2" />
          <div className="h-3 w-48 rounded bg-[hsl(var(--bg-tertiary))] animate-pulse" />
        </div>
        <div className="flex gap-4">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-4 w-20 rounded bg-[hsl(var(--bg-tertiary))] animate-pulse" />
          ))}
        </div>
      </Card>
    );
  }

  // No data state
  if (!stats) {
    return (
      <Card padding="lg">
        <div className="flex items-center gap-2 mb-4">
          <BarChart3 className="w-5 h-5 text-[hsl(var(--text-secondary))]" />
          <h3 className="text-base font-semibold text-[hsl(var(--text-primary))]">
            {t('statistics.wordCount.title')}
          </h3>
        </div>
        <div className="flex flex-col items-center justify-center py-8 text-[hsl(var(--text-secondary))]">
          <Calendar className="w-10 h-10 mb-2 opacity-50" />
          <p className="text-sm">{t('statistics.noData')}</p>
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
            <TrendingUp className="w-4 h-4" />
          </IconWrapper>
          <h3 className="text-base font-semibold text-[hsl(var(--text-primary))]">
            {t('statistics.wordCount.title')}
          </h3>
        </div>
      </div>

      {/* Primary Stats Display */}
      <div className="mb-5">
        <div className="flex items-baseline gap-2 mb-2">
          <span className="text-3xl font-bold text-[hsl(var(--text-primary))]">
            {(displayData?.value ?? 0).toLocaleString()}
          </span>
          <span className="text-sm text-[hsl(var(--text-secondary))]">
            {t('statistics.wordCount.words', { count: displayData?.value ?? 0 }).replace(/[\d,]+\s*/, '')}
          </span>
        </div>
        <p className="text-xs text-[hsl(var(--text-secondary))]">
          {displayData?.label}
        </p>
        {displayData && displayData.ai > 0 && (
          <p className="text-xs text-[hsl(var(--text-secondary))] mt-0.5">
            {t('statistics.wordCount.breakdown', {
              mine: displayData.mine.toLocaleString(),
              ai: displayData.ai.toLocaleString(),
            })}
          </p>
        )}
      </div>

      {/* Summary Stats Row */}
      <div className="flex items-center gap-4 mb-5 text-xs text-[hsl(var(--text-secondary))]">
        {summaryStats.map((stat) => (
          <button
            key={stat.key}
            onClick={() => onTimeRangeChange?.(stat.key === 'today' ? 'daily' : stat.key === 'week' ? 'weekly' : 'monthly')}
            className={`flex items-center gap-1.5 transition-colors hover:opacity-80 ${
              stat.isActive ? 'text-[hsl(var(--text-primary))]' : ''
            }`}
          >
            <span className={`w-2 h-2 rounded-full ${stat.colorClass}`} />
            <span className={stat.isActive ? 'font-medium' : ''}>
              {stat.value.toLocaleString()} {stat.label}
            </span>
          </button>
        ))}
      </div>

      {/* Expandable Chart Section */}
      <div className="border-t border-[hsl(var(--border-color))] pt-3">
        <button
          onClick={() => setIsChartExpanded(!isChartExpanded)}
          className="flex items-center justify-between w-full text-sm text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] transition-colors"
        >
          <span className="font-medium">{t('statistics.wordCount.trend.title')}</span>
          {isChartExpanded ? (
            <ChevronUp className="w-4 h-4" />
          ) : (
            <ChevronDown className="w-4 h-4" />
          )}
        </button>

        {isChartExpanded && (
          <div className="mt-4 space-y-3">
            {isTrendSeriesLoading && (
              <div className="space-y-2">
                {[1, 2, 3].map((row) => (
                  <div key={row} className="h-8 rounded bg-[hsl(var(--bg-tertiary))] animate-pulse" />
                ))}
              </div>
            )}

            {!isTrendSeriesLoading && trendBars.length === 0 && (
              <p className="text-xs text-[hsl(var(--text-secondary))]">
                {t('statistics.noData')}
              </p>
            )}

            {!isTrendSeriesLoading && trendBars.map((item, index) => {
              const barWidth = (Math.abs(item.value) / trendMaxValue) * 100;
              const isLatest = index === trendBars.length - 1;
              const isNegative = item.value < 0;

              return (
                <div key={item.key}>
                  <div className="flex items-center justify-between mb-1">
                    <span
                      className={`text-xs font-medium ${
                        isLatest
                          ? 'text-[hsl(var(--accent-primary))]'
                          : 'text-[hsl(var(--text-secondary))]'
                      }`}
                    >
                      {item.label}
                    </span>
                    <span
                      className={`text-xs font-medium ${
                        isLatest
                          ? 'text-[hsl(var(--accent-primary))]'
                          : 'text-[hsl(var(--text-secondary))]'
                      }`}
                    >
                      {item.value.toLocaleString()}
                    </span>
                  </div>
                  <div className="h-2 bg-[hsl(var(--bg-tertiary))] rounded-full overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all duration-300 ${
                        isNegative
                          ? isLatest
                            ? 'bg-red-500'
                            : 'bg-red-400/70'
                          : isLatest
                            ? 'bg-[hsl(var(--accent-primary))]'
                            : 'bg-[hsl(var(--accent-primary)/0.45)]'
                      }`}
                      style={{ width: `${Math.max(barWidth, 2)}%` }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Total Word Count Footer */}
      <div className="mt-4 pt-3 border-t border-[hsl(var(--border-color))]">
        <div className="flex items-center justify-between">
          <span className="text-sm text-[hsl(var(--text-secondary))]">
            {t('statistics.wordCount.total')}
          </span>
          <span className="text-sm font-semibold text-[hsl(var(--text-primary))]">
            {stats.total_word_count.toLocaleString()} {t('statistics.wordCount.words', { count: stats.total_word_count }).replace(/[\d,]+\s*/, '')}
          </span>
        </div>
      </div>
    </Card>
  );
}

export default WordCountTrendChart;
