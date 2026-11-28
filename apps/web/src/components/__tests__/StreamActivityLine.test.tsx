import { describe, it, expect, vi, afterEach } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'
import {
  LONG_THINKING_DRAFTING_AFTER_S,
  LONG_WAIT_HINT_AFTER_S,
  StreamActivityLine,
} from '../StreamActivityLine'

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

    expect(screen.getByText('chat:activity.preparingBody:{"title":"第一章"}')).toBeInTheDocument()

    snapshot = { title: '第一章', words: 1234 }
    act(() => {
      vi.advanceTimersByTime(1000)
    })
    expect(screen.getByText('chat:activity.writingBodyWords:{"title":"第一章","count":1234,"words":"1,234"}')).toBeInTheDocument()

    snapshot = null
    act(() => {
      vi.advanceTimersByTime(1000)
    })
    expect(screen.queryByText(/chat:activity\.(preparing|writing)Body/)).not.toBeInTheDocument()
    expect(screen.getByText('chat:activity.elapsed:{"seconds":2}')).toBeInTheDocument()
  })

  it('renders live tool-argument preparation status cleanly without being masked by generic elapsed copy', () => {
    vi.useFakeTimers()
    let liveStatus: string | null = '正在准备编辑文件…'
    render(<StreamActivityLine startedAt={Date.now()} statusText={() => liveStatus} />)

    expect(screen.getByTestId('stream-activity-stage')).toHaveTextContent('正在准备编辑文件…')
    expect(screen.getByText('chat:activity.seconds:{"seconds":0}')).toBeInTheDocument()
    expect(screen.queryByText(/chat:activity\.elapsed/)).not.toBeInTheDocument()

    // Progress heartbeat updates smoothly in place on tick.
    liveStatus = '正在准备编辑文件…\n  （已生成 1,500 字）'
    act(() => {
      vi.advanceTimersByTime(1000)
    })
    expect(screen.getByTestId('stream-activity-stage')).toHaveTextContent('正在准备编辑文件… （已生成 1,500 字）')
    expect(screen.getByText('chat:activity.seconds:{"seconds":1}')).toBeInTheDocument()

    // Empty/cleared status falls back cleanly to generic elapsed copy.
    liveStatus = '   '
    act(() => {
      vi.advanceTimersByTime(1000)
    })
    expect(screen.queryByTestId('stream-activity-stage')).not.toBeInTheDocument()
    expect(screen.getByText('chat:activity.elapsed:{"seconds":2}')).toBeInTheDocument()
  })

  it('prefers active bodyWrite progress over generic statusText and updates string statusText immediately', () => {
    vi.useFakeTimers()
    let snapshot: { title: string; words: number } | null = { title: '第二章', words: 420 }
    const { rerender } = render(
      <StreamActivityLine
        startedAt={Date.now()}
        bodyWrite={() => snapshot}
        statusText="正在准备编辑文件…"
      />,
    )

    expect(screen.getByTestId('stream-activity-stage')).toHaveTextContent(
      'chat:activity.writingBodyWords:{"title":"第二章","count":420,"words":"420"}',
    )

    snapshot = null
    rerender(
      <StreamActivityLine
        startedAt={Date.now()}
        bodyWrite={() => snapshot}
        statusText="正在准备检索文件…"
      />,
    )
    expect(screen.getByTestId('stream-activity-stage')).toHaveTextContent('正在准备检索文件…')
  })

  it('shows calm localized thinking phases after 20s and 45s when thinking runs with 0 visible prose', () => {
    vi.useFakeTimers()
    render(<StreamActivityLine startedAt={Date.now()} isThinking />)

    // Before 20s, standard elapsed indicator without thinking phase override
    expect(screen.queryByTestId('stream-activity-stage')).not.toBeInTheDocument()
    expect(screen.getByText('chat:activity.elapsed:{"seconds":0}')).toBeInTheDocument()

    // At 20s+, calm planning phase + elapsed seconds + long wait hint
    act(() => {
      vi.advanceTimersByTime(LONG_WAIT_HINT_AFTER_S * 1000)
    })
    expect(screen.getByTestId('stream-activity-stage')).toHaveTextContent('chat:activity.thinkingPlanning')
    expect(screen.getByText(`chat:activity.seconds:{"seconds":${LONG_WAIT_HINT_AFTER_S}}`)).toBeInTheDocument()
    expect(screen.getByText('chat:activity.longWaitHint')).toBeInTheDocument()

    // At 45s+, advances to drafting/edits phase
    act(() => {
      vi.advanceTimersByTime((LONG_THINKING_DRAFTING_AFTER_S - LONG_WAIT_HINT_AFTER_S) * 1000)
    })
    expect(screen.getByTestId('stream-activity-stage')).toHaveTextContent('chat:activity.thinkingDrafting')
    expect(screen.getByText(`chat:activity.seconds:{"seconds":${LONG_THINKING_DRAFTING_AFTER_S}}`)).toBeInTheDocument()
  })
})
