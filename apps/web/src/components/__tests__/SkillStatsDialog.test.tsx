import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import SkillStatsDialog from '../SkillStatsDialog'

const mockGetStats = vi.fn()
const loggerError = vi.fn()

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) =>
      (
        {
          'stats.title': 'Skill Stats',
          'stats.days7': '7 days',
          'stats.days30': '30 days',
          'stats.days90': '90 days',
          'stats.totalUses': 'Total uses',
          'stats.builtinCount': 'Builtin',
          'stats.userCount': 'User',
          'stats.topSkills': 'Top Skills',
          'stats.dailyUsage': 'Daily usage',
          'stats.builtin': 'Builtin',
          'stats.user': 'User',
          'stats.noData': 'No data yet',
        } as Record<string, string>
      )[key] ?? key,
  }),
}))

vi.mock('../../lib/api', () => ({
  skillsApi: {
    getStats: (...args: unknown[]) => mockGetStats(...args),
  },
}))

vi.mock('../ui/Modal', () => ({
  Modal: ({
    open,
    title,
    children,
  }: {
    open: boolean
    title: React.ReactNode
    children?: React.ReactNode
  }) => (open ? <div><div>{title}</div>{children}</div> : null),
}))

vi.mock('../../lib/logger', () => ({
  logger: {
    error: (...args: unknown[]) => loggerError(...args),
  },
}))

describe('SkillStatsDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetStats.mockResolvedValue({
      total_triggers: 12,
      builtin_count: 7,
      user_count: 5,
      top_skills: [
        { skill_source: 'builtin', skill_id: 'builtin-1', skill_name: 'Outline Helper', count: 6 },
        { skill_source: 'user', skill_id: 'user-1', skill_name: 'Tone Fixer', count: 3 },
      ],
      daily_usage: [
        { date: '2026-04-01', count: 1 },
        { date: '2026-04-02', count: 4 },
      ],
    })
  })

  it('loads and renders skill usage stats when opened', async () => {
    render(<SkillStatsDialog isOpen={true} onClose={vi.fn()} projectId="project-1" />)

    expect(await screen.findByText('Skill Stats')).toBeInTheDocument()
    expect(await screen.findByText('Outline Helper')).toBeInTheDocument()
    expect(mockGetStats).toHaveBeenCalledWith('project-1', 30)
  })

  it('reloads stats when the day range changes', async () => {
    render(<SkillStatsDialog isOpen={true} onClose={vi.fn()} projectId="project-1" />)
    expect(await screen.findByText('Skill Stats')).toBeInTheDocument()

    fireEvent.change(screen.getByDisplayValue('30 days'), { target: { value: '7' } })

    await waitFor(() => {
      expect(mockGetStats).toHaveBeenLastCalledWith('project-1', 7)
    })
  })

  it('charts every day in the selected range, not just the last 14', async () => {
    const daily = Array.from({ length: 30 }, (_, i) => ({
      date: `2026-04-${String(i + 1).padStart(2, '0')}`,
      count: i % 3,
    }))
    mockGetStats.mockResolvedValueOnce({
      total_triggers: 29, builtin_count: 29, user_count: 0, top_skills: [], daily_usage: daily,
    })

    render(<SkillStatsDialog isOpen={true} onClose={vi.fn()} projectId="project-1" />)

    const chart = await screen.findByTestId('skill-stats-daily-chart')
    expect(chart.children).toHaveLength(30)
    expect(chart.firstElementChild).toHaveAttribute('title', '2026-04-01: 0')
    // 30 天抽样显示日期标签，首尾一定可见
    const labels = Array.from(chart.querySelectorAll('span')).filter((span) => !span.className.includes('invisible'))
    expect(labels.length).toBeLessThanOrEqual(16)
    expect(labels[0]).toHaveTextContent('04-01')
    expect(labels[labels.length - 1]).toHaveTextContent('04-30')
  })

  it('logs errors and keeps the dialog rendered when loading fails', async () => {
    mockGetStats.mockRejectedValueOnce(new Error('stats failed'))

    render(<SkillStatsDialog isOpen={true} onClose={vi.fn()} projectId="project-1" />)

    expect(await screen.findByText('Skill Stats')).toBeInTheDocument()
    await waitFor(() => {
      expect(loggerError).toHaveBeenCalledWith('Failed to load skill stats:', expect.any(Error))
    })
  })
})
