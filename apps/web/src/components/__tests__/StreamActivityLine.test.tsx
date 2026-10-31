import { describe, it, expect, vi, afterEach } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'
import { LONG_WAIT_HINT_AFTER_S, StreamActivityLine } from '../StreamActivityLine'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown>) =>
      options ? `${key}:${JSON.stringify(options)}` : key,
  }),
}))

describe('StreamActivityLine', () => {
  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  it('counts the seconds of a running round and reassures the author once it gets long', () => {
    vi.useFakeTimers()
    render(<StreamActivityLine startedAt={Date.now()} />)

    expect(screen.getByText('chat:activity.elapsed:{"seconds":0}')).toBeInTheDocument()
    expect(screen.queryByText('chat:activity.longWaitHint')).not.toBeInTheDocument()

    act(() => {
      vi.advanceTimersByTime(LONG_WAIT_HINT_AFTER_S * 1000)
    })
    expect(screen.getByText(`chat:activity.elapsed:{"seconds":${LONG_WAIT_HINT_AFTER_S}}`)).toBeInTheDocument()
    expect(screen.getByText('chat:activity.longWaitHint')).toBeInTheDocument()
  })

  it('asks phone authors to keep the page open during a long round', () => {
    vi.useFakeTimers()
    render(<StreamActivityLine startedAt={Date.now()} isMobile />)

    act(() => {
      vi.advanceTimersByTime(LONG_WAIT_HINT_AFTER_S * 1000)
    })
    expect(screen.getByText('chat:activity.longWaitHintMobile')).toBeInTheDocument()
    expect(screen.queryByText('chat:activity.longWaitHint')).not.toBeInTheDocument()
  })

  it('shows which file is getting its text and the words written so far, read on each tick', () => {
    vi.useFakeTimers()
    let snapshot: { title: string; words: number } | null = { title: '第一章', words: 0 }
    render(<StreamActivityLine startedAt={Date.now()} bodyWrite={() => snapshot} />)

    expect(screen.getByText('chat:activity.writingBody:{"title":"第一章"}')).toBeInTheDocument()

    snapshot = { title: '第一章', words: 1234 }
    act(() => {
      vi.advanceTimersByTime(1000)
    })
    expect(screen.getByText('chat:activity.writingBodyWords:{"title":"第一章","count":1234,"words":"1,234"}')).toBeInTheDocument()

    snapshot = null
    act(() => {
      vi.advanceTimersByTime(1000)
    })
    expect(screen.queryByText(/chat:activity\.writingBody/)).not.toBeInTheDocument()
    expect(screen.getByText('chat:activity.elapsed:{"seconds":2}')).toBeInTheDocument()
  })
})
