import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft, ChevronDown, RefreshCw } from 'lucide-react';
import { useWritingStats } from '../hooks/useWritingStats';
import { WordCountTrendChart, type TimeRange } from '../components/ProjectDashboard/WordCountTrendChart';
import { plannedTotal, statsUnitKey, writtenChapters } from '../components/ProjectDashboard/statsUnits';
import { Button } from '../components/ui/Button';
import { IconButton } from '../components/ui/IconButton';
import { formatCalendarDate } from '../lib/dateUtils';

const periods = [
  { id: 'daily', mine: 'words_today', ai: 'ai_words_today', label: 'today' },
  { id: 'weekly', mine: 'words_this_week', ai: 'ai_words_this_week', label: 'thisWeek' },
  { id: 'monthly', mine: 'words_this_month', ai: 'ai_words_this_month', label: 'thisMonth' },
] as const;
const panelClass = 'rounded-2xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))]';
// Seven-digit totals stay on one line on a 320px phone; the size shrinks rather than splitting the number.
const figureClass = 'mt-2 whitespace-nowrap text-[clamp(1.25rem,6.5vw,1.875rem)] font-semibold tabular-nums md:text-4xl';

/** Show useful project facts immediately; only long lists and trends need opening. */
export default function ProjectDashboardPage() {
  const { t, i18n } = useTranslation('dashboard');
  const { projectId } = useParams<{ projectId: string }>();
  const navigate = useNavigate();
  const [timeRange, setTimeRange] = useState<TimeRange>('daily');
  const [chaptersOpen, setChaptersOpen] = useState(false);
  const { stats, isLoading, isFetching, error, refetch } = useWritingStats({ projectId, enabled: !!projectId });
  const initialLoading = isLoading || (isFetching && !stats);
  const format = (value: number | undefined) => typeof value === 'number' ? value.toLocaleString(i18n.language) : '—';
  const period = periods.find(item => item.id === timeRange)!;
  const mine = stats?.[period.mine] ?? 0;
  const ai = stats?.[period.ai] ?? 0;
  const completion = stats?.chapter_completion;
  const planned = plannedTotal(completion);
  const percentage = completion ? Math.min(100, Math.max(0, completion.completion_percentage)) : 0;
  // The API retains the previous length after a streak breaks; status owns whether it is current.
  const currentStreak = stats && (stats.streak.streak_status === 'active' || stats.streak.streak_status === 'at_risk')
    ? stats.streak.current_streak : 0;
  const hasActivity = stats && (stats.streak.last_writing_date || currentStreak > 0);
  const back = () => navigate(projectId ? `/project/${projectId}` : '/dashboard');

  return (
    <main className="ui-sans-headings min-h-dvh bg-[hsl(var(--bg-primary))] text-[hsl(var(--text-primary))]">
      <div className="mx-auto w-full max-w-6xl px-4 py-6 md:px-6 md:py-8">
        <header className="mb-5">
          <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
            <h1 className="min-w-0 text-2xl font-semibold tracking-tight">{t('dashboard.title')}</h1>
            <div className="flex shrink-0 items-center gap-2">
              <IconButton
                label={t('common.refresh', '刷新')}
                icon={<RefreshCw size={18} className={isFetching ? 'animate-spin motion-reduce:animate-none' : ''} />}
                disabled={isFetching}
                aria-busy={isFetching}
                onClick={() => void refetch()}
              />
              <Button variant="secondary" size="md" className="max-md:min-h-11" leftIcon={<ArrowLeft size={16} />} onClick={back}>
                {t('projectStatistics.back')}
              </Button>
            </div>
          </div>
          {stats?.project_name && (
            <p className="mt-2 break-words text-sm text-[hsl(var(--text-secondary))]">{stats.project_name}</p>
          )}
        </header>

        {initialLoading ? (
          <div role="status" className={`${panelClass} p-5`}>
            <p className="text-sm text-[hsl(var(--text-secondary))]">{t('projectStatistics.loading')}</p>
            <div className="mt-4 grid grid-cols-2 gap-4" aria-hidden="true">
              {[0, 1].map(item => <div key={item} className="h-24 animate-pulse rounded-xl bg-[hsl(var(--bg-tertiary))] motion-reduce:animate-none" />)}
            </div>
          </div>
        ) : !stats ? (
          <div className={`${panelClass} p-6`} role={error ? 'alert' : undefined}>
            <p className="mb-4 text-sm text-[hsl(var(--text-secondary))]">
              {error ? t('dashboard.error.title') : t('projectStatistics.empty')}
            </p>
            <Button variant="secondary" size="touch" onClick={() => void refetch()}>{t('common.retry', '重试')}</Button>
          </div>
        ) : (
          <div className="space-y-4">
            {error && <p role="alert" className="text-sm text-[hsl(var(--error-text))]">{t('projectStatistics.refreshError')}</p>}
            <section className={panelClass} aria-labelledby="project-overview-title">
              <h2 id="project-overview-title" className="sr-only">{t('projectStatistics.overview')}</h2>
              <dl className="grid grid-cols-2 divide-x divide-[hsl(var(--border-color))]">
                <div className="min-w-0 p-4 sm:p-5 md:p-6">
                  <dt className="text-sm text-[hsl(var(--text-secondary))]">{t('statistics.wordCount.total')}</dt>
                  <dd className={figureClass}>{format(stats.total_word_count)}</dd>
                </div>
                <div className="min-w-0 p-4 sm:p-5 md:p-6">
                  <dt className="text-sm text-[hsl(var(--text-secondary))]">{t(`statistics.wordCount.${period.label}`)}</dt>
                  <dd className={figureClass}>{format(mine + ai)}</dd>
                  <dd className="mt-2 text-xs leading-5 text-[hsl(var(--text-secondary))]">
                    {t('statistics.wordCount.breakdown', { mine: format(mine), ai: format(ai) })}
                  </dd>
                </div>
              </dl>
              <div className="border-t border-[hsl(var(--border-color))] px-4 py-3 md:flex md:items-center md:justify-between md:gap-4">
                <div className="flex rounded-xl bg-[hsl(var(--bg-primary))] p-1 md:min-w-72 md:shrink-0" role="group" aria-label={t('projectStatistics.period')}>
                  {periods.map(item => (
                    <button
                      key={item.id}
                      type="button"
                      aria-pressed={timeRange === item.id}
                      onClick={() => setTimeRange(item.id)}
                      className={`min-h-11 flex-1 whitespace-nowrap rounded-lg px-2 text-sm sm:px-3 font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))] ${timeRange === item.id ? 'bg-[hsl(var(--accent-primary)/0.1)] text-[hsl(var(--accent-primary))]' : 'text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--bg-tertiary))]'}`}
                    >
                      {t(`projectStatistics.periods.${item.id}`)}
                    </button>
                  ))}
                </div>
                <p className="mt-2 text-xs text-[hsl(var(--text-secondary))] md:mt-0">{t('projectStatistics.timezone')}</p>
              </div>
            </section>

            <div className="grid items-start gap-4 lg:grid-cols-12">
              <section className={`${panelClass} min-w-0 p-5 lg:col-span-7`} aria-labelledby="project-progress-title">
                <h2 id="project-progress-title" className="text-base font-semibold">{t('projectStatistics.progress')}</h2>
                <div className="mt-3 flex flex-wrap items-baseline justify-between gap-2">
                  <p className="text-2xl font-semibold tabular-nums">
                    {t(statsUnitKey(stats, 'written'), { count: writtenChapters(stats.chapter_completion) })}
                  </p>
                  <p className="text-sm text-[hsl(var(--text-secondary))]">
                    {planned === null ? t(statsUnitKey(stats, 'noPlanHint')) : t(statsUnitKey(stats, 'plannedCount'), { count: planned })}
                  </p>
                </div>
                <div className="mt-3 flex flex-wrap gap-x-4 gap-y-2 text-xs text-[hsl(var(--text-secondary))]">
                  <span>{t(statsUnitKey(stats, 'countFinished'), { count: stats.chapter_completion.completed_chapters })}</span>
                  <span>{t(statsUnitKey(stats, 'countInProgress'), { count: stats.chapter_completion.in_progress_chapters })}</span>
                  <span>{t(statsUnitKey(stats, 'countPlanned'), { count: stats.chapter_completion.not_started_chapters })}</span>
                </div>
                {planned !== null && (
                  <div className="mt-4">
                    <p className="mb-2 text-xs text-[hsl(var(--text-secondary))]">
                      {t('projectStatistics.completedPercentage', { percent: percentage })}
                    </p>
                    <div
                      role="progressbar"
                      aria-label={t(statsUnitKey(stats, 'completionTitle'))}
                      aria-valuemin={0}
                      aria-valuemax={100}
                      aria-valuenow={percentage}
                      className="h-1.5 overflow-hidden rounded-full bg-[hsl(var(--bg-tertiary))]"
                    >
                      <div className="h-full bg-[hsl(var(--accent-primary))]" style={{ width: `${percentage}%` }} />
                    </div>
                  </div>
                )}
                {stats.chapter_completion.chapter_details.length === 0 ? (
                  <p className="mt-4 text-sm text-[hsl(var(--text-secondary))]">{t(statsUnitKey(stats, 'noChapters'))}</p>
                ) : (
                  <>
                    <button
                      type="button"
                      className="mt-3 flex min-h-11 w-full items-center justify-between gap-3 rounded-lg text-left text-sm font-medium text-[hsl(var(--accent-primary))] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))]"
                      aria-expanded={chaptersOpen}
                      aria-controls="project-chapter-details"
                      onClick={() => setChaptersOpen(open => !open)}
                    >
                      {t(statsUnitKey(stats, chaptersOpen ? 'hideDetails' : 'viewDetails'), { count: stats.chapter_completion.chapter_details.length })}
                      <ChevronDown size={18} className={`shrink-0 ${chaptersOpen ? 'rotate-180' : ''}`} aria-hidden="true" />
                    </button>
                    {chaptersOpen && (
                      <ul id="project-chapter-details" className="mt-2 divide-y divide-[hsl(var(--border-color))] border-t border-[hsl(var(--border-color))]">
                        {stats.chapter_completion.chapter_details.map((chapter, index) => {
                          const fileId = chapter.draft_id || chapter.outline_id;
                          const state = chapter.status === 'complete' ? 'finished' : chapter.status === 'in_progress' ? 'inProgress' : 'planned';
                          const content = (
                            <>
                              <span className="min-w-0 break-words text-sm font-medium">{chapter.title}</span>
                              <span className="mt-1 block text-xs text-[hsl(var(--text-secondary))]">
                                {t(`statistics.chapterCompletion.${state}`)} · {t('statistics.wordCount.words', { count: chapter.word_count, formatted: format(chapter.word_count) })}
                                {chapter.target_word_count ? ` / ${t('statistics.wordCount.words', { count: chapter.target_word_count, formatted: format(chapter.target_word_count) })}` : ''}
                              </span>
                            </>
                          );
                          return (
                            <li key={fileId || index}>
                              {fileId && projectId ? (
                                <Link to={`/project/${encodeURIComponent(projectId)}?file=${encodeURIComponent(fileId)}`} className="block min-h-11 rounded-lg py-3 hover:text-[hsl(var(--accent-primary))] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))]">
                                  {content}
                                </Link>
                              ) : <div className="py-3">{content}</div>}
                            </li>
                          );
                        })}
                      </ul>
                    )}
                  </>
                )}
              </section>

              <section className="min-w-0 px-1 py-3 lg:col-span-5 lg:px-5" aria-labelledby="project-activity-title">
                <h2 id="project-activity-title" className="text-sm font-semibold">{t('projectStatistics.activity')}</h2>
                {hasActivity ? (
                  <>
                    <dl className="mt-3 space-y-2 text-sm">
                      <div className="flex flex-wrap justify-between gap-x-3 gap-y-1">
                        <dt className="text-[hsl(var(--text-secondary))]">{t('projectStatistics.lastWriting')}</dt>
                        <dd>{stats.streak.last_writing_date ? formatCalendarDate(stats.streak.last_writing_date, i18n.language) : '—'}</dd>
                      </div>
                      <div className="flex flex-wrap justify-between gap-x-3 gap-y-1">
                        <dt className="text-[hsl(var(--text-secondary))]">{t('statistics.streak.currentStreak')}</dt>
                        <dd>{t('statistics.streak.days', { count: currentStreak })}</dd>
                      </div>
                    </dl>
                    <p className="mt-3 text-xs leading-5 text-[hsl(var(--text-secondary))]">{t('projectStatistics.streakNote')}</p>
                  </>
                ) : <p className="mt-3 text-sm text-[hsl(var(--text-secondary))]">{t('projectStatistics.noActivity')}</p>}
                <WordCountTrendChart projectId={projectId} timeRange={timeRange} />
              </section>
            </div>
          </div>
        )}
      </div>
    </main>
  );
}
