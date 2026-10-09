import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft, BarChart3, BookOpen, Flame, Bot, RefreshCw, AlertCircle } from 'lucide-react';
import { useWritingStats } from '../hooks/useWritingStats';
import {
  WordCountTrendChart,
  ChapterCompletionCard,
  WritingStreakCard,
  AiUsageCard,
  ContinueWritingCard,
  ProjectHealthCard,
  OutstandingTasksCard,
} from '../components/ProjectDashboard';
import type { TimeRange } from '../components/ProjectDashboard';
import { statsUnitKey } from '../components/ProjectDashboard/statsUnits';
import { useIsMobile, useIsTablet } from '../hooks/useMediaQuery';
import { useProject } from '../contexts/ProjectContext';

type DashboardTab = 'overview' | 'wordcount' | 'chapters' | 'streak' | 'ai-usage';

/**
 * ProjectDashboardPage - Dashboard page showing project statistics
 *
 * Displays:
 * - Action-oriented cards (continue writing, outstanding tasks, project health)
 * - Word count trends (daily/weekly/monthly)
 * - Chapter completion status
 * - Writing streak with gamification
 * - AI usage metrics
 */
export default function ProjectDashboardPage() {
  const { t } = useTranslation(['dashboard']);
  const { projectId } = useParams<{ projectId: string }>();
  const navigate = useNavigate();
  const { projects } = useProject();

  const isMobile = useIsMobile();
  const isTablet = useIsTablet();

  const [activeTab, setActiveTab] = useState<DashboardTab>('overview');
  const [timeRange, setTimeRange] = useState<TimeRange>('daily');

  // Fetch dashboard statistics
  const {
    stats,
    isLoading,
    isFetching,
    error,
    refetch,
  } = useWritingStats({
    projectId,
    enabled: !!projectId,
  });
  const isDashboardLoading = isLoading || (isFetching && !stats);

  // Get project info
  const project = projects.find((p) => p.id === projectId);

  // Tab configuration
  const tabs: { id: DashboardTab; icon: React.ElementType; label: string }[] = [
    { id: 'overview', icon: BarChart3, label: t('dashboard.tabs.overview', '总览') },
    { id: 'wordcount', icon: BarChart3, label: t('statistics.wordCount.title') },
    { id: 'chapters', icon: BookOpen, label: t(statsUnitKey(stats, 'completionTitle')) },
    { id: 'streak', icon: Flame, label: t('statistics.streak.title') },
    { id: 'ai-usage', icon: Bot, label: t('statistics.aiUsage.title') },
  ];

  // Handle back navigation
  const handleBack = () => {
    if (projectId) {
      navigate(`/project/${projectId}`);
    } else {
      navigate('/dashboard');
    }
  };

  // Loading state
  if (isDashboardLoading) {
    return (
      <div className="min-h-screen bg-[hsl(var(--bg-primary))]">
        {/* Header */}
        <div className={`${isMobile ? 'p-4' : 'p-6'} border-b border-[hsl(var(--border-color))]`}>
          <div className="flex items-center gap-4">
            <button
              onClick={handleBack}
              className="p-2 rounded-lg hover:bg-[hsl(var(--bg-secondary))] transition-colors"
            >
              <ArrowLeft className="w-5 h-5 text-[hsl(var(--text-secondary))]" />
            </button>
            <div>
              <div className="h-6 w-32 rounded bg-[hsl(var(--bg-tertiary))] animate-pulse" />
              <div className="h-4 w-24 rounded bg-[hsl(var(--bg-tertiary))] animate-pulse mt-1" />
            </div>
          </div>
        </div>

        {/* Tab skeleton */}
        <div className={`${isMobile ? 'px-4 py-3' : 'px-6 py-4'}`}>
          <div className="flex gap-2 overflow-x-auto">
            {[1, 2, 3, 4, 5].map((i) => (
              <div
                key={i}
                className="h-9 w-24 rounded-lg bg-[hsl(var(--bg-tertiary))] animate-pulse shrink-0"
              />
            ))}
          </div>
        </div>

        {/* Content skeleton */}
        <div className={`${isMobile ? 'p-4' : 'p-6'} grid ${isMobile ? 'grid-cols-1' : isTablet ? 'grid-cols-2' : 'lg:grid-cols-3'} gap-4`}>
          {[1, 2, 3, 4, 5, 6].map((i) => (
            <div
              key={i}
              className="h-48 rounded-lg border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] animate-pulse"
            />
          ))}
        </div>
      </div>
    );
  }

  // Error state
  if (error) {
    return (
      <div className="min-h-screen bg-[hsl(var(--bg-primary))] flex items-center justify-center p-4">
        <div className="text-center max-w-md">
          <div className="w-16 h-16 rounded-2xl bg-[hsl(var(--error)/0.1)] flex items-center justify-center mx-auto mb-4">
            <AlertCircle className="w-8 h-8 text-[hsl(var(--error))]" />
          </div>
          <h2 className="text-xl font-semibold text-[hsl(var(--text-primary))] mb-2">
            {t('dashboard.error.title', '加载数据统计失败')}
          </h2>
          <p className="text-sm text-[hsl(var(--text-secondary))] mb-6">
            {t('dashboard.error.message', '点击“重试”重新加载。')}
          </p>
          <div className="flex gap-3 justify-center">
            <button onClick={handleBack} className="btn-ghost">
              {t('common.back', '返回')}
            </button>
            <button onClick={() => void refetch()} className="btn-primary flex items-center gap-2">
              <RefreshCw className="w-4 h-4" />
              {t('common.retry', '重试')}
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[hsl(var(--bg-primary))]">
      {/* Header */}
      <div className={`${isMobile ? 'p-4' : 'p-6'} border-b border-[hsl(var(--border-color))]`}>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <button
              onClick={handleBack}
              className="p-2 rounded-lg hover:bg-[hsl(var(--bg-secondary))] transition-colors"
              title={t('common.back', '返回')}
            >
              <ArrowLeft className="w-5 h-5 text-[hsl(var(--text-secondary))]" />
            </button>
            <div>
              <h1 className={`font-bold text-[hsl(var(--text-primary))] ${isMobile ? 'text-lg' : 'text-xl'}`}>
                {project?.name || t('dashboard.title', '项目数据统计')}
              </h1>
              <p className={`text-[hsl(var(--text-secondary))] ${isMobile ? 'text-xs' : 'text-sm'}`}>
                {t('dashboard.subtitle', '追踪你的写作进度')}
              </p>
            </div>
          </div>

          {/* Refresh button */}
          <button
            onClick={() => void refetch()}
            className="p-2 rounded-lg hover:bg-[hsl(var(--bg-secondary))] transition-colors"
            title={t('common.refresh', '刷新')}
          >
            <RefreshCw className={`w-5 h-5 text-[hsl(var(--text-secondary))] ${isDashboardLoading ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {/* Tab Navigation */}
      <div className={`${isMobile ? 'px-4 py-3' : 'px-6 py-4'} border-b border-[hsl(var(--border-color))]`}>
        <div className={`flex gap-2 overflow-x-auto ${isMobile ? 'scrollbar-none' : ''}`}>
          {tabs.map((tab) => {
            const TabIcon = tab.icon;
            const isActive = activeTab === tab.id;

            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`
                  flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium transition-all shrink-0
                  ${isMobile ? 'px-3 py-2 text-xs' : 'px-4 py-2 text-sm'}
                  ${isActive
                    ? 'bg-[hsl(var(--accent-primary))] text-white shadow-sm'
                    : 'bg-[hsl(var(--bg-secondary))] text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))]'
                  }
                `}
              >
                <TabIcon className={`${isMobile ? 'w-3.5 h-3.5' : 'w-4 h-4'}`} />
                <span className="whitespace-nowrap">{tab.label}</span>
              </button>
            );
          })}
        </div>
      </div>

      {/* Main Content */}
      <div className={`${isMobile ? 'p-4' : 'p-6'}`}>
        {/* Overview Tab - Action-Oriented Layout */}
        {activeTab === 'overview' && (
          <div className="space-y-6">
            {/* Primary Actions Section - Most Prominent */}
            <section>
              <h2 className={`font-semibold text-[hsl(var(--text-primary))] mb-3 ${isMobile ? 'text-sm' : 'text-base'}`}>
                {t('dashboard.sections.nextSteps', '下一步行动')}
              </h2>
              <div className={`grid ${isMobile ? 'grid-cols-1' : isTablet ? 'grid-cols-2' : 'lg:grid-cols-3'} gap-4`}>
                {/* Continue Writing - Primary Action */}
                <ContinueWritingCard
                  stats={stats}
                  isLoading={isDashboardLoading}
                  projectId={projectId}
                />

                {/* Outstanding Tasks - Items needing attention */}
                <OutstandingTasksCard
                  stats={stats}
                  isLoading={isDashboardLoading}
                  projectId={projectId}
                />

                {/* Project Health - Overall status */}
                <ProjectHealthCard
                  stats={stats}
                  isLoading={isDashboardLoading}
                />
              </div>
            </section>

            {/* Secondary Statistics Section - Less Prominent */}
            <section>
              <h2 className={`font-semibold text-[hsl(var(--text-secondary))] mb-3 ${isMobile ? 'text-sm' : 'text-base'}`}>
                {t('dashboard.sections.statistics', '统计概览')}
              </h2>
              <div className={`grid ${isMobile ? 'grid-cols-1' : isTablet ? 'grid-cols-2' : 'lg:grid-cols-4'} gap-4`}>
                {/* Quick Stats Summary Cards */}
                <WordCountTrendChart
                  projectId={projectId}
                  stats={stats}
                  isLoading={isDashboardLoading}
                  timeRange={timeRange}
                  onTimeRangeChange={setTimeRange}
                />
                <ChapterCompletionCard
                  stats={stats}
                  isLoading={isDashboardLoading}
                />
                <WritingStreakCard
                  stats={stats}
                  isLoading={isDashboardLoading}
                />
                <AiUsageCard
                  projectId={projectId}
                  stats={stats}
                  isLoading={isDashboardLoading}
                />
              </div>
            </section>
          </div>
        )}

        {/* Word Count Tab */}
        {activeTab === 'wordcount' && (
          <div className={`grid ${isMobile ? 'grid-cols-1' : isTablet ? 'grid-cols-2' : 'lg:grid-cols-2'} gap-4`}>
            <WordCountTrendChart
              projectId={projectId}
              stats={stats}
              isLoading={isDashboardLoading}
              timeRange={timeRange}
              onTimeRangeChange={setTimeRange}
            />
            {/* Additional word count details can be added here */}
            <div className="rounded-lg border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] p-5">
              <h3 className="text-base font-semibold text-[hsl(var(--text-primary))] mb-4">
                {t('statistics.wordCount.trend.title', '字数趋势')}
              </h3>
              <div className="space-y-3">
                <div className="flex items-center justify-between p-3 rounded-lg bg-[hsl(var(--bg-tertiary))]">
                  <span className="text-sm text-[hsl(var(--text-secondary))]">
                    {t('statistics.wordCount.today')}
                  </span>
                  <span className="text-lg font-semibold text-[hsl(var(--text-primary))]">
                    {stats?.words_today.toLocaleString() ?? 0}
                  </span>
                </div>
                <div className="flex items-center justify-between p-3 rounded-lg bg-[hsl(var(--bg-tertiary))]">
                  <span className="text-sm text-[hsl(var(--text-secondary))]">
                    {t('statistics.wordCount.thisWeek')}
                  </span>
                  <span className="text-lg font-semibold text-[hsl(var(--text-primary))]">
                    {stats?.words_this_week.toLocaleString() ?? 0}
                  </span>
                </div>
                <div className="flex items-center justify-between p-3 rounded-lg bg-[hsl(var(--bg-tertiary))]">
                  <span className="text-sm text-[hsl(var(--text-secondary))]">
                    {t('statistics.wordCount.thisMonth')}
                  </span>
                  <span className="text-lg font-semibold text-[hsl(var(--text-primary))]">
                    {stats?.words_this_month.toLocaleString() ?? 0}
                  </span>
                </div>
                <div className="flex items-center justify-between p-3 rounded-lg bg-[hsl(var(--bg-tertiary))]">
                  <span className="text-sm text-[hsl(var(--text-secondary))]">
                    {t('statistics.wordCount.total')}
                  </span>
                  <span className="text-lg font-semibold text-[hsl(var(--text-primary))]">
                    {stats?.total_word_count.toLocaleString() ?? 0}
                  </span>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Chapters Tab */}
        {activeTab === 'chapters' && (
          <div className="grid grid-cols-1 gap-4">
            <ChapterCompletionCard
              stats={stats}
              isLoading={isDashboardLoading}
              maxVisibleChapters={10}
            />
          </div>
        )}

        {/* Streak Tab */}
        {activeTab === 'streak' && (
          <div className={`grid ${isMobile ? 'grid-cols-1' : 'lg:grid-cols-2'} gap-4`}>
            <WritingStreakCard
              stats={stats}
              isLoading={isDashboardLoading}
            />
            {/* Streak tips */}
            <div className="rounded-lg border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] p-5">
              <h3 className="text-base font-semibold text-[hsl(var(--text-primary))] mb-4">
                {t('statistics.streak.tips.title', '连续写作小贴士')}
              </h3>
              <ul className="space-y-3 text-sm text-[hsl(var(--text-secondary))]">
                <li className="flex items-start gap-2">
                  <Flame className="w-4 h-4 text-orange-500 shrink-0 mt-0.5" />
                  <span>{t('statistics.streak.tips.daily', '每天至少写 10 个字，保持写作连续。')}</span>
                </li>
                <li className="flex items-start gap-2">
                  <Flame className="w-4 h-4 text-orange-500 shrink-0 mt-0.5" />
                  <span>{t('statistics.streak.tips.consistency', '比起单次高产，稳定连续更重要，哪怕每天一点点。')}</span>
                </li>
                <li className="flex items-start gap-2">
                  <Flame className="w-4 h-4 text-orange-500 shrink-0 mt-0.5" />
                  <span>{t('statistics.streak.tips.recovery', '漏写一天不会马上中断，隔天接着写就能保住连续记录。')}</span>
                </li>
              </ul>
            </div>
          </div>
        )}

        {/* AI Usage Tab */}
        {activeTab === 'ai-usage' && (
          <div className={`grid ${isMobile ? 'grid-cols-1' : 'lg:grid-cols-2'} gap-4`}>
            <AiUsageCard
              projectId={projectId}
              stats={stats}
              isLoading={isDashboardLoading}
            />
            {/* AI usage tips */}
            <div className="rounded-lg border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] p-5">
              <h3 className="text-base font-semibold text-[hsl(var(--text-primary))] mb-4">
                {t('statistics.aiUsage.tips.title', 'AI 使用建议')}
              </h3>
              <ul className="space-y-3 text-sm text-[hsl(var(--text-secondary))]">
                <li className="flex items-start gap-2">
                  <Bot className="w-4 h-4 text-purple-500 shrink-0 mt-0.5" />
                  <span>{t('statistics.aiUsage.tips.efficiency', '让 AI 帮你想点子、列大纲、改稿。')}</span>
                </li>
                <li className="flex items-start gap-2">
                  <Bot className="w-4 h-4 text-purple-500 shrink-0 mt-0.5" />
                  <span>{t('statistics.aiUsage.tips.iterative', '先让 AI 写一版，再告诉它哪里要改，效果更好。')}</span>
                </li>
                <li className="flex items-start gap-2">
                  <Bot className="w-4 h-4 text-purple-500 shrink-0 mt-0.5" />
                  <span>{t('statistics.aiUsage.tips.context', '在“AI 记忆”里写好角色、设定和文风，AI 的建议会更贴合你的故事。')}</span>
                </li>
              </ul>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
