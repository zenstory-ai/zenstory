import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ContinueWritingCard } from '../ContinueWritingCard'
import { OutstandingTasksCard } from '../OutstandingTasksCard'
import { WritingStreakCard } from '../WritingStreakCard'
import { ProjectHealthCard } from '../ProjectHealthCard'
import { WordCountTrendChart } from '../WordCountTrendChart'
import { AiUsageCard } from '../AiUsageCard'

const mockNavigate = vi.fn()
let mockTrendResponse: {
  data: Array<{ date: string; net_words: number }>
} | null = null
let trendLoading = false
let trendFetching = false

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  }
})

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown> | string) => {
      if (typeof options === 'string') return options
      const count = options?.count
      const time = options?.time
      return (
        {
          'statistics.continueWriting.title': 'Continue Writing',
          'statistics.continueWriting.action': 'Continue',
          'statistics.continueWriting.noFiles': 'No files',
          'statistics.continueWriting.createFirst': 'Create your first file',
          'statistics.continueWriting.inProgress': 'In Progress',
          'statistics.continueWriting.notStarted': 'Ready to Start',
          'statistics.continueWriting.completed': 'Recently Finished',
          'statistics.continueWriting.hintInProgress': 'Resume your current chapter',
          'statistics.continueWriting.hintNotStarted': 'Start the next chapter',
          'statistics.continueWriting.hintCompleted': 'Review the latest completion',
          'statistics.outstandingTasks.title': 'Outstanding Tasks',
          'statistics.outstandingTasks.allClear': 'All clear',
          'statistics.outstandingTasks.allClearHint': 'Nothing is blocking progress',
          'statistics.outstandingTasks.types.outlineWithoutDraft': 'Outline only',
          'statistics.outstandingTasks.types.notStarted': 'Not started',
          'statistics.outstandingTasks.outlineWithoutDraftDesc': 'Draft still missing',
          'statistics.outstandingTasks.notStartedDesc': 'Draft exists but is not started',
          'statistics.outstandingTasks.notStartedCount': 'not started',
          'statistics.outstandingTasks.outlineWithoutDraftCount': 'outline only',
          'statistics.outstandingTasks.total': `${count} total`,
          'statistics.outstandingTasks.moreTasks': `${count} more`,
          'statistics.streak.title': 'Streak',
          'statistics.streak.startWriting': 'Start writing to build a streak',
          'statistics.streak.status.active': 'Active',
          'statistics.streak.status.atRisk': 'At Risk',
          'statistics.streak.status.broken': 'Broken',
          'statistics.streak.status.none': 'None',
          'statistics.streak.currentStreak': 'Current streak',
          'statistics.streak.longestStreak': 'Longest streak',
          'statistics.streak.days': `${count} days`,
          'statistics.streak.daysUntilBreak': `${count} day until break`,
          'statistics.streak.recoveryAvailable': 'Recovery available',
          'statistics.streak.day': `${count} day`,
          'statistics.projectHealth.title': 'Project Health',
          'statistics.projectHealth.overallStatus': 'Overall status',
          'statistics.projectHealth.status.good': 'Good',
          'statistics.projectHealth.status.warning': 'Warning',
          'statistics.projectHealth.status.critical': 'Critical',
          'statistics.projectHealth.status.neutral': 'Neutral',
          'statistics.projectHealth.indicators.chapters': 'Chapters',
          'statistics.projectHealth.indicators.chaptersDesc': `${count}% complete`,
          'statistics.projectHealth.indicators.noChapters': 'No chapters yet',
          'statistics.projectHealth.indicators.activity': 'Activity',
          'statistics.projectHealth.indicators.activityGood': `${count} words today`,
          'statistics.projectHealth.indicators.activityWarning': 'Some activity this week',
          'statistics.projectHealth.indicators.activityInactive': 'Inactive',
          'statistics.projectHealth.indicators.streak': 'Streak',
          'statistics.projectHealth.indicators.streakActive': 'Streak is active',
          'statistics.projectHealth.indicators.streakInactive': 'No current streak',
          'statistics.projectHealth.indicators.aiUsage': 'AI usage',
          'statistics.projectHealth.indicators.aiActive': 'AI active',
          'statistics.projectHealth.indicators.aiInactive': 'AI inactive',
          'statistics.wordCount.title': 'Word Count',
          'statistics.wordCount.today': 'Today',
          'statistics.wordCount.thisWeek': 'This Week',
          'statistics.wordCount.thisMonth': 'This Month',
          'statistics.wordCount.total': 'Total',
          'statistics.wordCount.trend.title': 'Trend',
          'statistics.wordCount.words': `${count} words`,
          'statistics.noData': 'No data',
          'statistics.chapterCompletion.title': 'Chapter Completion',
          'statistics.chapterCompletion.finished': 'Finished',
          'statistics.chapterCompletion.inProgress': 'In Progress',
          'statistics.chapterCompletion.planned': 'Planned',
          'statistics.chapterCompletion.total': `${count} chapters`,
          'statistics.chapterCompletion.completed': 'Completed',
          'statistics.aiUsage.title': 'AI Usage',
          'statistics.aiUsage.getStartTitle': 'Get started',
          'statistics.aiUsage.getStartDesc': 'Try the AI assistant',
          'statistics.aiUsage.startChat': 'Start Chat',
          'statistics.aiUsage.continueConversation': 'Continue Conversation',
          'statistics.aiUsage.lastInteraction': `Last interaction ${time}`,
          'statistics.aiUsage.sessionInProgress': 'Session in progress',
          'statistics.aiUsage.resume': 'Resume',
          'statistics.aiUsage.askForHelp': 'Ask for help',
          'statistics.aiUsage.askForHelpDesc': 'Launch a new assistant session',
          'statistics.aiUsage.startNew': 'Start New',
          'statistics.aiUsage.recentActivity': 'Recent activity',
          'statistics.aiUsage.messages': `${count} messages`,
          'statistics.aiUsage.today': 'Today',
          'statistics.aiUsage.thisWeek': 'This Week',
          'statistics.aiUsage.sessions': `${count} sessions`,
          'time.justNow': 'just now',
          'time.minutesAgo': `${count} minutes ago`,
          'time.hoursAgo': `${count} hours ago`,
          'time.daysAgo': `${count} days ago`,
          'time.yesterday': 'yesterday',
          'statistics.lastWritingDate': 'Last writing date',
        } as Record<string, string>
      )[key] ?? key
    },
  }),
}))

vi.mock('../../hooks/useMediaQuery', () => ({
  useIsMobile: () => false,
}))

vi.mock('@tanstack/react-query', () => ({
  useQuery: () => ({
    data: mockTrendResponse,
    isLoading: trendLoading,
    isFetching: trendFetching,
  }),
}))

describe('ProjectDashboard cards', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    trendLoading = false
    trendFetching = false
    mockTrendResponse = {
      data: [
        { date: '2026-04-01', net_words: 120 },
        { date: '2026-04-02', net_words: -25 },
      ],
    }
  })

  const baseStats = {
    total_word_count: 12000,
    words_today: 1200,
    words_this_week: 4800,
    words_this_month: 12000,
    chapter_completion: {
      completion_percentage: 50,
      completed_chapters: 2,
      in_progress_chapters: 1,
      not_started_chapters: 1,
      total_chapters: 4,
      chapter_details: [
        {
          outline_id: 'outline-1',
          draft_id: 'draft-1',
          title: '第1章',
          status: 'complete',
          word_count: 1000,
          target_word_count: 1000,
          completion_percentage: 100,
        },
        {
          outline_id: 'outline-2',
          draft_id: 'draft-2',
          title: 'Chapter 2',
          status: 'in_progress',
          word_count: 800,
          target_word_count: 2000,
          completion_percentage: 40,
        },
        {
          outline_id: 'outline-3',
          draft_id: null,
          title: '第3章',
          status: 'not_started',
          word_count: 0,
          target_word_count: 1500,
          completion_percentage: 0,
        },
      ],
    },
    streak: {
      current_streak: 3,
      longest_streak: 9,
      streak_status: 'at_risk',
      days_until_break: 1,
      streak_recovery_count: 2,
      last_writing_date: '2026-04-06T00:00:00Z',
    },
    ai_usage: {
      current: {
        total_messages: 14,
        total_sessions: 2,
        active_session_id: 'session-1',
        last_interaction_date: '2026-04-07T03:00:00Z',
        last_interaction_at: '2026-04-07T03:00:00Z',
      },
      today: { total: 3, estimated_tokens: 500 },
      this_week: { total: 10, estimated_tokens: 2000 },
      this_month: { total: 22, estimated_tokens: 4500 },
    },
  }

  it('prefers the latest in-progress chapter for continue writing and navigates to it', () => {
    render(<ContinueWritingCard stats={baseStats as never} projectId="project-1" />)

    expect(screen.getByText('Chapter 2')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Continue/ }))

    expect(mockNavigate).toHaveBeenCalledWith('/project/project-1?file=draft-2')
  })

  it('falls back to not-started and completed chapters when no in-progress chapter exists', () => {
    const notStartedStats = {
      ...baseStats,
      chapter_completion: {
        ...baseStats.chapter_completion,
        chapter_details: [
          {
            outline_id: 'outline-4',
            draft_id: 'draft-4',
            title: '第4章',
            status: 'not_started',
            word_count: 0,
            target_word_count: 2000,
            completion_percentage: 0,
          },
        ],
      },
    }

    const { rerender } = render(<ContinueWritingCard stats={notStartedStats as never} projectId="project-1" />)
    expect(screen.getByText('待开始')).toBeInTheDocument()

    const completedOnlyStats = {
      ...baseStats,
      chapter_completion: {
        ...baseStats.chapter_completion,
        chapter_details: [
          {
            outline_id: 'outline-5',
            draft_id: 'draft-5',
            title: 'Chapter 5',
            status: 'complete',
            word_count: 2100,
            target_word_count: 2100,
            completion_percentage: 100,
          },
        ],
      },
    }
    rerender(<ContinueWritingCard stats={completedOnlyStats as never} projectId="project-1" />)
    expect(screen.getByText('已完成')).toBeInTheDocument()
  })

  it('parses chinese chapter numbers, ignores invalid titles, and skips navigation without a project id', () => {
    const chineseNumberStats = {
      ...baseStats,
      chapter_completion: {
        ...baseStats.chapter_completion,
        chapter_details: [
          {
            outline_id: 'outline-empty',
            draft_id: 'draft-empty',
            title: '',
            status: 'in_progress',
            word_count: 10,
            target_word_count: 100,
            completion_percentage: 10,
          },
          {
            outline_id: 'outline-freeform',
            draft_id: 'draft-freeform',
            title: 'Interlude',
            status: 'in_progress',
            word_count: 300,
            target_word_count: 1000,
            completion_percentage: 30,
          },
          {
            outline_id: 'outline-zero',
            draft_id: 'draft-zero',
            title: 'Chapter 0',
            status: 'in_progress',
            word_count: 0,
            target_word_count: 2000,
            completion_percentage: 0,
          },
          {
            outline_id: 'outline-cn',
            draft_id: 'draft-cn',
            title: '第十二章',
            status: 'in_progress',
            word_count: 1200,
            target_word_count: 2000,
            completion_percentage: 60,
          },
        ],
      },
    }

    render(<ContinueWritingCard stats={chineseNumberStats as never} projectId={undefined} />)

    expect(screen.getByText('第十二章')).toBeInTheDocument()
    expect(screen.getByText('60%')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /Continue/ }))

    expect(mockNavigate).not.toHaveBeenCalled()
  })

  it('renders outstanding tasks and prioritizes outline-only chapters', () => {
    render(<OutstandingTasksCard stats={baseStats as never} projectId="project-1" />)

    expect(screen.getByText('1 outline only')).toBeInTheDocument()
    expect(screen.getByText('第3章')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /第3章/ }))
    expect(mockNavigate).toHaveBeenCalledWith('/project/project-1?file=outline-3')
  })

  it('renders streak and health warning states with recovery info', () => {
    render(
      <>
        <WritingStreakCard stats={baseStats as never} />
        <ProjectHealthCard stats={baseStats as never} />
      </>,
    )

    expect(screen.getAllByText(/At Risk/).length).toBeGreaterThan(0)
    expect(screen.getByText('1 day until break')).toBeInTheDocument()
    expect(screen.getByText('Recovery available')).toBeInTheDocument()
    expect(screen.getByText('Project Health')).toBeInTheDocument()
    expect(screen.getByText('Warning')).toBeInTheDocument()
  })

  it('renders trend charts, no-data fallback, and get-started AI state', () => {
    const noAiStats = {
      ...baseStats,
      ai_usage: {
        current: {
          total_messages: 0,
          total_sessions: 0,
          active_session_id: null,
          last_interaction_date: null,
          last_interaction_at: null,
        },
        today: { total: 0, estimated_tokens: 0 },
        this_week: { total: 0, estimated_tokens: 0 },
        this_month: { total: 0, estimated_tokens: 0 },
      },
    }

    render(
      <>
        <WordCountTrendChart
          projectId="project-1"
          stats={baseStats as never}
          timeRange="daily"
          onTimeRangeChange={vi.fn()}
        />
        <AiUsageCard stats={noAiStats as never} projectId="project-1" />
      </>,
    )

    fireEvent.click(screen.getByRole('button', { name: /Trend/ }))
    expect(screen.getByText('120')).toBeInTheDocument()
    expect(screen.getByText('-25')).toBeInTheDocument()
    expect(screen.getByText('12,000 words')).toBeInTheDocument()
    expect(screen.getByText('Get started')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Start Chat' }))
    expect(mockNavigate).toHaveBeenCalledWith('/project/project-1')
  })

  it('renders active-session and ask-for-help AI suggestions', () => {
    const activeSessionStats = {
      ...baseStats,
    }
    const { rerender } = render(<AiUsageCard stats={activeSessionStats as never} projectId="project-1" />)

    expect(screen.getByText('Continue Conversation')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Resume/ }))
    expect(mockNavigate).toHaveBeenCalledWith('/project/project-1')

    const passiveAiStats = {
      ...baseStats,
      ai_usage: {
        current: {
          total_messages: 8,
          total_sessions: 2,
          active_session_id: null,
          last_interaction_date: '2026-04-02T03:00:00Z',
          last_interaction_at: '2026-04-02T03:00:00Z',
        },
        today: { total: 0, estimated_tokens: 0 },
        this_week: { total: 2, estimated_tokens: 200 },
        this_month: { total: 8, estimated_tokens: 500 },
      },
    }
    rerender(<AiUsageCard stats={passiveAiStats as never} projectId="project-1" />)
    expect(screen.getByText('Ask for help')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Start New/ }))
    expect(mockNavigate).toHaveBeenCalledWith('/project/project-1')
  })

  it('renders empty states for cards without meaningful stats', () => {
    const emptyStats = {
      ...baseStats,
      chapter_completion: {
        ...baseStats.chapter_completion,
        chapter_details: [],
        completed_chapters: 0,
        in_progress_chapters: 0,
        not_started_chapters: 0,
        total_chapters: 0,
        completion_percentage: 0,
      },
      streak: {
        ...baseStats.streak,
        current_streak: 0,
        longest_streak: 0,
        streak_status: 'none',
        days_until_break: null,
        streak_recovery_count: 0,
      },
      ai_usage: {
        current: {
          total_messages: 4,
          total_sessions: 1,
          active_session_id: null,
          last_interaction_date: '2026-04-05T03:00:00Z',
          last_interaction_at: '2026-04-05T03:00:00Z',
        },
        today: { total: 1, estimated_tokens: 100 },
        this_week: { total: 4, estimated_tokens: 300 },
        this_month: { total: 4, estimated_tokens: 300 },
      },
    }

    render(
      <>
        <ContinueWritingCard stats={emptyStats as never} projectId="project-1" />
        <OutstandingTasksCard stats={emptyStats as never} projectId="project-1" />
        <WritingStreakCard stats={emptyStats as never} />
      </>,
    )

    expect(screen.getByText('No files')).toBeInTheDocument()
    expect(screen.getByText('All clear')).toBeInTheDocument()
    expect(screen.getAllByText('0 days').length).toBeGreaterThan(0)
    expect(screen.getByText('Last writing date')).toBeInTheDocument()
  })

  it('renders loading states for primary dashboard cards', () => {
    const { container } = render(
      <>
        <ContinueWritingCard stats={null} isLoading projectId="project-1" />
        <OutstandingTasksCard stats={null} isLoading projectId="project-1" />
        <ProjectHealthCard stats={null} isLoading />
        <WordCountTrendChart stats={null} isLoading />
      </>,
    )

    expect(container.querySelectorAll('.animate-pulse').length).toBeGreaterThan(0)
  })

  it('renders no-data chart state and allows switching time ranges', () => {
    mockTrendResponse = { data: [] }
    const onTimeRangeChange = vi.fn()

    render(
      <WordCountTrendChart
        projectId="project-1"
        stats={baseStats as never}
        timeRange="monthly"
        onTimeRangeChange={onTimeRangeChange}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: /This Week/ }))
    fireEvent.click(screen.getByRole('button', { name: /This Month/ }))
    fireEvent.click(screen.getByRole('button', { name: /Trend/ }))

    expect(onTimeRangeChange).toHaveBeenCalledWith('weekly')
    expect(onTimeRangeChange).toHaveBeenCalledWith('monthly')
    expect(screen.getByText('No data')).toBeInTheDocument()
  })

  it('renders loading and no-data states for chart and AI summary branches', () => {
    const { rerender, container } = render(
      <WordCountTrendChart
        projectId="project-1"
        stats={baseStats as never}
        isLoading={true}
        timeRange="daily"
        onTimeRangeChange={vi.fn()}
      />,
    )

    expect(container.querySelectorAll('.animate-pulse').length).toBeGreaterThan(0)

    mockTrendResponse = null
    rerender(
      <>
        <WordCountTrendChart
          projectId="project-1"
          stats={null}
          timeRange="daily"
          onTimeRangeChange={vi.fn()}
        />
        <AiUsageCard stats={null} />
      </>,
    )

    expect(screen.getAllByText('No data').length).toBeGreaterThan(0)
    expect(screen.getByText('Get started')).toBeInTheDocument()
  })

  it('covers alternate AI collaboration states', () => {
    const recentActivityStats = {
      ...baseStats,
      ai_usage: {
        current: {
          total_messages: 6,
          total_sessions: 1,
          active_session_id: null,
          last_interaction_date: '2026-04-07T03:00:00Z',
          last_interaction_at: '2026-04-07T03:00:00Z',
        },
        today: { total: 2, estimated_tokens: 200 },
        this_week: { total: 6, estimated_tokens: 600 },
        this_month: { total: 6, estimated_tokens: 600 },
      },
    }
    const { rerender } = render(<AiUsageCard stats={recentActivityStats as never} projectId="project-1" />)
    expect(screen.getByText('AI active')).toBeInTheDocument()

    const inactiveStats = {
      ...baseStats,
      ai_usage: {
        current: {
          total_messages: 6,
          total_sessions: 1,
          active_session_id: null,
          last_interaction_date: '2026-04-01T03:00:00Z',
          last_interaction_at: '2026-04-01T03:00:00Z',
        },
        today: { total: 0, estimated_tokens: 0 },
        this_week: { total: 0, estimated_tokens: 0 },
        this_month: { total: 6, estimated_tokens: 600 },
      },
    }
    rerender(<AiUsageCard stats={inactiveStats as never} projectId="project-1" />)
    expect(screen.getByText('AI inactive')).toBeInTheDocument()
    expect(screen.getByText('Ask for help')).toBeInTheDocument()
  })
})
