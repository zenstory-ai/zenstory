import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SavedAgoLabel } from '../SavedAgoLabel'
import { RelativeTime } from '../RelativeTime'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: { count?: number }) =>
      options?.count === undefined ? key : `${key}:${options.count}`,
  }),
}))

vi.mock('../../lib/dateUtils', async () => {
  const actual = await vi.importActual<typeof import('../../lib/dateUtils')>('../../lib/dateUtils')
  return {
    ...actual,
    formatRelativeTime: (value: string, now: Date = new Date()) =>
      `${Math.floor((now.getTime() - actual.parseUTCDate(value).getTime()) / 60000)} min ago`,
  }
})

describe('relative time labels keep moving while the page stays open', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-10-09T08:00:00Z'))
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('editor save status counts up from 刚刚保存 with the number shown', () => {
    render(<SavedAgoLabel savedAt={new Date('2026-10-09T08:00:00Z')} />)
    expect(screen.getByText('editor:savedJustNow')).toBeInTheDocument()

    act(() => {
      vi.advanceTimersByTime(30_000)
    })
    expect(screen.getByText('editor:secondsAgo:30')).toBeInTheDocument()

    act(() => {
      vi.advanceTimersByTime(120_000)
    })
    expect(screen.getByText('editor:minutesAgo:2')).toBeInTheDocument()
  })

  it('project card activity time is re-evaluated instead of frozen at first render', () => {
    render(<RelativeTime value="2026-10-09T07:58:00Z" />)
    expect(screen.getByText('2 min ago')).toBeInTheDocument()

    act(() => {
      vi.advanceTimersByTime(5 * 60_000)
    })
    expect(screen.getByText('7 min ago')).toBeInTheDocument()
  })

  it('shows a dash when the project has no activity time', () => {
    render(<RelativeTime value={null} />)
    expect(screen.getByText('-')).toBeInTheDocument()
  })
})
