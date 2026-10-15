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
const disclosureClass = 'flex min-h-14 w-full items-center justify-between gap-3 px-5 py-4 text-left text-sm font-medium text-[hsl(var(--text-primary))] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[hsl(var(--accent-primary))]';
const panelClass = 'rounded-2xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))]';

/** Project facts first; details are optional, never recommendations or scores. */
export default function ProjectDashboardPage() {
  const { t, i18n } = useTranslation('dashboard');
  const { projectId } = useParams<{ projectId: string }>();
  const navigate = useNavigate();
  const [timeRange, setTimeRange] = useState<TimeRange>('daily');
  const [chaptersOpen, setChaptersOpen] = useState(false);
  const [recordsOpen, setRecordsOpen] = useState(false);
  const { stats, isLoading, isFetching, error, refetch } = useWritingStats({ projectId, enabled: !!projectId });
  const initialLoading = isLoading || (isFetching && !stats);
  const format = (value: number | undefined) => typeof value === 'number' ? value.toLocaleString(i18n.language) : '—';
  const period = periods.find(item => item.id === timeRange)!;
  const mine = stats?.[period.mine] ?? 0;
  const ai = stats?.[period.ai] ?? 0;
  const completion = stats?.chapter_completion;
  const planned = plannedTotal(completion);
  const percentage = completion ? Math.min(100, Math.max(0, completion.completion_percentage)) : 0;
  const back = () => navigate(projectId ? `/project/${projectId}` : '/dashboard');

  return (
    <main className="ui-sans-headings min-h-dvh bg-[hsl(var(--bg-primary))] text-[hsl(var(--text-primary))]">
      <div className="mx-auto w-full max-w-5xl px-4 py-6 md:px-6 md:py-8">
        <header className="mb-6 flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0 flex-1">
            <h1 className="text-2xl font-semibold tracking-tight">{t('dashboard.title')}</h1>
            {stats?.project_name && <p className="mt-1 break-words text-sm text-[hsl(var(--text-secondary))]">{stats.project_name}</p>}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <IconButton label={t('common.refresh', '刷新')} icon={<RefreshCw size={18} className={isFetching ? 'animate-spin motion-reduce:animate-none' : ''} />} disabled={isFetching} aria-busy={isFetching} onClick={() => void refetch()} />
            <Button size="md" className="max-md:min-h-11" leftIcon={<ArrowLeft size={16} />} onClick={back}>{t('projectStatistics.back')}</Button>
          </div>
        </header>

        {initialLoading ? (
          <div role="status" className={`${panelClass} p-6`}>
            <p className="text-sm text-[hsl(var(--text-secondary))]">{t('projectStatistics.loading')}</p>
            <div className="mt-4 grid gap-4 md:grid-cols-3" aria-hidden="true">
              {[0, 1, 2].map(item => <div key={item} className="h-28 animate-pulse rounded-xl bg-[hsl(var(--bg-tertiary))] motion-reduce:animate-none" />)}
            </div>
          </div>
        ) : !stats ? (
          <div className={`${panelClass} p-6`} role={error ? 'alert' : undefined}>
            <p className="mb-4 text-sm text-[hsl(var(--text-secondary))]">{error ? t('dashboard.error.title') : t('projectStatistics.empty')}</p>
            <Button variant="secondary" size="touch" onClick={() => void refetch()}>{t('common.retry', '重试')}</Button>
          </div>
        ) : (
          <div className="space-y-4">
            {error && <p role="alert" className="text-sm text-[hsl(var(--error-text))]">{t('projectStatistics.refreshError')}</p>}
            <section className={panelClass} aria-labelledby="project-overview-title">
              <div className="border-b border-[hsl(var(--border-color))] px-5 py-4">
                <h2 id="project-overview-title" className="text-base font-semibold">{t('projectStatistics.overview')}</h2>
                <p className="mt-1 text-xs text-[hsl(var(--text-secondary))]">{t('projectStatistics.timezone')}</p>
              </div>
              <dl className="grid divide-y divide-[hsl(var(--border-color))] md:grid-cols-3 md:divide-x md:divide-y-0">
                <div className="p-5">
                  <dt className="text-sm text-[hsl(var(--text-secondary))]">{t('statistics.wordCount.total')}</dt>
                  <dd className="mt-2 text-3xl font-semibold tabular-nums">{format(stats.total_word_count)}</dd>
                </div>
                <div className="p-5">
                  <dt className="text-sm text-[hsl(var(--text-secondary))]">{t(`statistics.wordCount.${period.label}`)}</dt>
                  <dd className="mt-2 text-3xl font-semibold tabular-nums">{format(mine + ai)}</dd>
                  <dd className="mt-1 text-xs text-[hsl(var(--text-secondary))]">{t('statistics.wordCount.breakdown', { mine: format(mine), ai: format(ai) })}</dd>
                  <dd className="mt-3"><div className="flex flex-wrap gap-1" role="group" aria-label={t('projectStatistics.period')}>
                    {periods.map(item => <button key={item.id} type="button" aria-pressed={timeRange === item.id} onClick={() => setTimeRange(item.id)} className={`min-h-11 rounded-lg px-3 text-xs font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))] ${timeRange === item.id ? 'bg-[hsl(var(--accent-primary)/0.1)] text-[hsl(var(--accent-primary))]' : 'text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--bg-tertiary))]'}`}>{t(`projectStatistics.periods.${item.id}`)}</button>)}
                  </div></dd>
                </div>
                <div className="p-5">
                  <dt className="text-sm text-[hsl(var(--text-secondary))]">{t('projectStatistics.progress')}</dt>
                  <dd className="mt-2 text-2xl font-semibold tabular-nums">{t(statsUnitKey(stats, 'written'), { count: writtenChapters(stats.chapter_completion) })}</dd>
                  <dd className="mt-2 text-sm text-[hsl(var(--text-secondary))]">{planned === null ? t('projectStatistics.noPlan') : t(statsUnitKey(stats, 'plannedCount'), { count: planned })}</dd>
                </div>
              </dl>
            </section>

            <section className={panelClass}>
              <h2><button type="button" className={disclosureClass} aria-expanded={chaptersOpen} aria-controls="project-chapter-details" onClick={() => setChaptersOpen(open => !open)}>{t(statsUnitKey(stats, 'detailsTitle'))}<ChevronDown size={18} className={`shrink-0 ${chaptersOpen ? 'rotate-180' : ''}`} aria-hidden="true" /></button></h2>
              {chaptersOpen && <div id="project-chapter-details" className="border-t border-[hsl(var(--border-color))] p-5">
                <div className="flex flex-wrap gap-x-5 gap-y-2 text-sm text-[hsl(var(--text-secondary))]">
                  <span>{t(statsUnitKey(stats, 'countFinished'), { count: stats.chapter_completion.completed_chapters })}</span>
                  <span>{t(statsUnitKey(stats, 'countInProgress'), { count: stats.chapter_completion.in_progress_chapters })}</span>
                  <span>{t(statsUnitKey(stats, 'countPlanned'), { count: stats.chapter_completion.not_started_chapters })}</span>
                </div>
                {planned !== null && <div className="mt-4">
                  <p className="mb-2 text-xs text-[hsl(var(--text-secondary))]">{t('statistics.chapterCompletion.percentage', { percent: percentage })}</p>
                  <div role="progressbar" aria-label={t(statsUnitKey(stats, 'completionTitle'))} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percentage} className="h-1.5 overflow-hidden rounded-full bg-[hsl(var(--bg-tertiary))]"><div className="h-full bg-[hsl(var(--accent-primary))]" style={{ width: `${percentage}%` }} /></div>
                </div>}
                {stats.chapter_completion.chapter_details.length === 0 ? <p className="mt-5 text-sm text-[hsl(var(--text-secondary))]">{t(statsUnitKey(stats, 'noChapters'))}</p> : <ul className="mt-4 divide-y divide-[hsl(var(--border-color))]">
                  {stats.chapter_completion.chapter_details.map((chapter, index) => {
                    const fileId = chapter.draft_id || chapter.outline_id;
                    const state = chapter.status === 'complete' ? 'finished' : chapter.status === 'in_progress' ? 'inProgress' : 'planned';
                    const content = <><span className="min-w-0 break-words text-sm font-medium">{chapter.title}</span><span className="mt-1 block text-xs text-[hsl(var(--text-secondary))]">{t(`statistics.chapterCompletion.${state}`)} · {t('statistics.wordCount.words', { count: chapter.word_count })}{chapter.target_word_count ? ` / ${t('statistics.wordCount.words', { count: chapter.target_word_count })}` : ''}</span></>;
                    return <li key={fileId || index}>{fileId && projectId ? <Link to={`/project/${encodeURIComponent(projectId)}?file=${encodeURIComponent(fileId)}`} className="block min-h-11 rounded-lg py-3 hover:text-[hsl(var(--accent-primary))] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))]">{content}</Link> : <div className="py-3">{content}</div>}</li>;
                  })}
                </ul>}
              </div>}
            </section>

            <WordCountTrendChart projectId={projectId} timeRange={timeRange} />

            <section className={panelClass}>
              <h2><button type="button" className={disclosureClass} aria-expanded={recordsOpen} aria-controls="project-writing-records" onClick={() => setRecordsOpen(open => !open)}>{t('projectStatistics.records')}<ChevronDown size={18} className={`shrink-0 ${recordsOpen ? 'rotate-180' : ''}`} aria-hidden="true" /></button></h2>
              {recordsOpen && <div id="project-writing-records" className="grid gap-6 border-t border-[hsl(var(--border-color))] p-5 md:grid-cols-2">
                <section aria-labelledby="project-streak-title">
                  <h3 id="project-streak-title" className="text-sm font-semibold">{t('statistics.streak.title')}</h3>
                  <p className="mt-2 text-xs text-[hsl(var(--text-secondary))]">{t('projectStatistics.streakNote')}</p>
                  <dl className="mt-4 space-y-3 text-sm">
                    <div className="flex justify-between gap-3"><dt className="text-[hsl(var(--text-secondary))]">{t('statistics.streak.currentStreak')}</dt><dd>{t('statistics.streak.days', { count: stats.streak.current_streak })}</dd></div>
                    <div className="flex justify-between gap-3"><dt className="text-[hsl(var(--text-secondary))]">{t('statistics.streak.longestStreak')}</dt><dd>{t('statistics.streak.days', { count: stats.streak.longest_streak })}</dd></div>
                    <div className="flex justify-between gap-3"><dt className="text-[hsl(var(--text-secondary))]">{t('projectStatistics.lastWriting')}</dt><dd>{stats.streak.last_writing_date ? formatCalendarDate(stats.streak.last_writing_date, i18n.language) : '—'}</dd></div>
                  </dl>
                </section>
                <section aria-labelledby="project-ai-records-title">
                  <h3 id="project-ai-records-title" className="text-sm font-semibold">{t('statistics.aiUsage.title')}</h3>
                  <p className="mt-2 text-xs text-[hsl(var(--text-secondary))]">{t('statistics.aiUsage.quotaNote')}</p>
                  <dl className="mt-4 space-y-3 text-sm">
                    <div className="flex justify-between gap-3"><dt className="text-[hsl(var(--text-secondary))]">{t('projectStatistics.totalSent')}</dt><dd>{typeof stats.ai_usage.current.user_messages === 'number' ? t('statistics.aiUsage.messages', { count: stats.ai_usage.current.user_messages }) : '—'}</dd></div>
                    <div className="flex justify-between gap-3"><dt className="text-[hsl(var(--text-secondary))]">{t('projectStatistics.todaySent')}</dt><dd>{typeof stats.ai_usage.today.user === 'number' ? t('statistics.aiUsage.messages', { count: stats.ai_usage.today.user }) : '—'}</dd></div>
                    <div className="flex justify-between gap-3"><dt className="text-[hsl(var(--text-secondary))]">{t('projectStatistics.conversations')}</dt><dd>{format(stats.ai_usage.current.total_sessions)}</dd></div>
                  </dl>
                </section>
              </div>}
            </section>
          </div>
        )}
      </div>
    </main>
  );
}
