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
})
