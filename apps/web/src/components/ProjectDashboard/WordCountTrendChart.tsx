import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { ChevronDown } from 'lucide-react';
import { writingStatsApi } from '../../lib/writingStatsApi';
import { normalizeLocale } from '../../lib/i18n-helpers';
import { Button } from '../ui/Button';

export type TimeRange = 'daily' | 'weekly' | 'monthly';
const windows = { daily: 14, weekly: 84, monthly: 365 } as const;
const BEIJING_TIME_ZONE = 'Asia/Shanghai';
const ONE_DAY_MS = 24 * 60 * 60 * 1000;

/**
 * Label a trend row from its Beijing calendar date ("YYYY-MM-DD": the day, the
 * week's Monday or the month's first day) in the app language. The server's
 * period_label is always English, so it is not shown.
 */
function formatTrendPeriodLabel(date: string, range: TimeRange, language: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(date);
  if (!match) return date;
  const locale = normalizeLocale(language) === 'en' ? 'en-US' : 'zh-CN';
  // Noon in Beijing, so the instant lies on this calendar day there.
  const start = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]), 4));
  if (range === 'monthly') {
    return start.toLocaleDateString(locale, { year: 'numeric', month: 'long', timeZone: BEIJING_TIME_ZONE });
  }
  const dayFormat = new Intl.DateTimeFormat(locale, { month: 'short', day: 'numeric', timeZone: BEIJING_TIME_ZONE });
  if (range === 'weekly') {
    // Joined by hand: formatRange output differs between ICU builds (Chinese may drop 月/日).
    return `${dayFormat.format(start)} – ${dayFormat.format(new Date(start.getTime() + 6 * ONE_DAY_MS))}`;
  }
  return dayFormat.format(start);
}

/** The existing trend endpoint reports only the author's net edits, not AI. */
export function WordCountTrendChart({ projectId, timeRange = 'daily' }: { projectId?: string; timeRange?: TimeRange }) {
  const { t, i18n } = useTranslation('dashboard');
  const [expanded, setExpanded] = useState(false);
  const id = useId();
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['writing-stats-word-trend', projectId, timeRange, windows[timeRange]],
    queryFn: () => projectId ? writingStatsApi.getWordCountTrend(projectId, { period: timeRange, days: windows[timeRange] }) : null,
    enabled: expanded && !!projectId,
    staleTime: 60 * 1000,
  });
  const rows = data?.data.slice(-8) ?? [];
  const maximum = Math.max(1, ...rows.map(row => Math.abs(row.net_words)));

  return (
    <div className="mt-3 border-t border-[hsl(var(--border-color))]">
      <h3>
        <button
          type="button"
          aria-expanded={expanded}
          aria-controls={id}
          onClick={() => setExpanded(open => !open)}
          className="flex min-h-11 w-full items-center justify-between gap-3 rounded-lg py-3 text-left text-sm font-medium text-[hsl(var(--accent-primary))] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))]"
        >
          {t(expanded ? 'projectStatistics.trendHide' : 'projectStatistics.trendShow')}
          <ChevronDown size={18} className={`shrink-0 ${expanded ? 'rotate-180' : ''}`} aria-hidden="true" />
        </button>
      </h3>
      {expanded && (
        <div id={id} className="pt-2">
          <p className="mb-4 text-xs leading-5 text-[hsl(var(--text-secondary))]">{t('projectStatistics.trendNote')}</p>
          {isLoading && <p role="status" className="text-sm text-[hsl(var(--text-secondary))]">{t('projectStatistics.trendLoading')}</p>}
          {error && (
            <div role="alert" className="mb-4 text-sm text-[hsl(var(--error-text))]">
              <p>{t('projectStatistics.trendError')}</p>
              <Button variant="secondary" size="touch" className="mt-3" onClick={() => void refetch()}>
                {t('projectStatistics.trendRetry')}
              </Button>
            </div>
          )}
          {!isLoading && !error && rows.length === 0 && <p className="text-sm text-[hsl(var(--text-secondary))]">{t('statistics.noData')}</p>}
          {rows.length > 0 && (
            <ul className="space-y-3" data-testid="writing-trend-series">
              {rows.map((row, index) => (
                <li key={`${row.date}-${index}`}>
                  <div className="mb-1 flex justify-between gap-3 text-xs">
                    <span className="text-[hsl(var(--text-secondary))]">
                      {formatTrendPeriodLabel(row.date, timeRange, i18n.language)}
                    </span>
                    <span className={`font-medium tabular-nums ${row.net_words < 0 ? 'text-[hsl(var(--error-text))]' : 'text-[hsl(var(--text-primary))]'}`}>
                      {row.net_words.toLocaleString(i18n.language)}
                    </span>
                  </div>
                  <div aria-hidden="true" className="h-1.5 overflow-hidden rounded-full bg-[hsl(var(--bg-tertiary))]">
                    <div className={`h-full rounded-full ${row.net_words < 0 ? 'bg-[hsl(var(--error-text))]' : 'bg-[hsl(var(--accent-primary))]'}`} style={{ width: `${Math.abs(row.net_words) / maximum * 100}%` }} />
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

export default WordCountTrendChart;
