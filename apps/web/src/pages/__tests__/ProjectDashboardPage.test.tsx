import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ProjectDashboardPage from '../ProjectDashboardPage'

const mockNavigate = vi.fn()
const mockRefetch = vi.fn()

let mockStats: Record<string, unknown> | null = null
let mockLoading = false
let mockFetching = false
let mockError: Error | null = null

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return {
    ...actual,
    useNavigate: () => mockNavigate,
    useParams: () => ({ projectId: 'project-1' }),
  }
})

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown> | string) => {
      const fallback = typeof options === 'string' ? options : undefined
      const values = typeof options === 'string' ? undefined : options
      const count = values?.count
      const time = values?.time
      return (
        {
          'common.back': 'Back',
          'common.retry': 'Retry',
          'common.refresh': 'Refresh',
          'dashboard.tabs.overview': 'Overview',
          'statistics.wordCount.title': 'Word Count',
          'statistics.chapterCompletion.title': 'Chapters',
          'statistics.streak.title': 'Streak',
          'statistics.aiUsage.title': 'AI Usage',
          'dashboard.sections.nextSteps': 'What’s Next',
          'dashboard.sections.statistics': 'Statistics',
          'dashboard.error.title': 'Failed to Load Dashboard',
          'statistics.continueWriting.action': 'Continue',
          'statistics.aiUsage.resume': 'Resume',
          'statistics.aiUsage.startChat': 'Start Chat',
          'time.minutesAgo': `${count} minutes ago`,
          'time.hoursAgo': `${count} hours ago`,
          'time.daysAgo': `${count} days ago`,
          'statistics.aiUsage.lastInteraction': `Last interaction ${time}`,
          'statistics.wordCount.words': `${count} words`,
          'statistics.streak.days': `${count} days`,
          'statistics.aiUsage.messages': `${count} messages`,
          'statistics.aiUsage.sessions': `${count} sessions`,
        } as Record<string, string>
      )[key] ?? fallback ?? key
    },
  }),
}))

vi.mock('../../hooks/useMediaQuery', () => ({
  useIsMobile: () => false,
  useIsTablet: () => false,
}))

vi.mock('../../hooks/useWritingStats', () => ({
  useWritingStats: () => ({
    stats: mockStats,
    isLoading: mockLoading,
    isFetching: mockFetching,
    error: mockError,
    refetch: mockRefetch,
  }),
}))

vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({
    projects: [{ id: 'project-1', name: 'Novel Project' }],
  }),
}))

vi.mock('@tanstack/react-query', () => ({
  useQuery: () => ({
    data: {
      data: [
        { date: '2026-04-01', net_words: 120 },
        { date: '2026-04-02', net_words: 260 },
      ],
    },
    isLoading: false,
    isFetching: false,
  }),
}))

describe('ProjectDashboardPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockLoading = false
    mockFetching = false
    mockError = null
    mockStats = {
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
            title: 'Chapter 1',
            status: 'complete',
            word_count: 1500,
            target_word_count: 1500,
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
            title: 'Chapter 3',
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
        streak_status: 'active',
        days_until_break: 1,
        streak_recovery_count: 1,
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
  })

  it('renders loading placeholders while dashboard data is loading', () => {
    mockLoading = true
    const { container } = render(<ProjectDashboardPage />)

    expect(container.querySelectorAll('.animate-pulse').length).toBeGreaterThan(0)
  })

  it('renders the error state with retry and back actions', () => {
    mockError = new Error('boom')
    render(<ProjectDashboardPage />)

    expect(screen.getByText('Failed to Load Dashboard')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    fireEvent.click(screen.getByRole('button', { name: 'Back' }))

    expect(mockRefetch).toHaveBeenCalled()
    expect(mockNavigate).toHaveBeenCalledWith('/project/project-1')
  })

  it('renders overview stats, supports navigation, and switches tabs', () => {
    render(<ProjectDashboardPage />)

    expect(screen.getByText('Novel Project')).toBeInTheDocument()
    expect(screen.getByText('Chapter 1')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Streak' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Streak' }))
    expect(screen.getByText('statistics.streak.currentStreak')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'AI Usage' }))
    expect(screen.getByText('Resume')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Resume/ }))
    expect(mockNavigate).toHaveBeenCalledWith('/project/project-1')

    fireEvent.click(screen.getByTitle('Refresh'))
    expect(mockRefetch).toHaveBeenCalled()
  })
})
