import { describe, it, expect, vi, afterEach } from 'vitest'
import { act, cleanup, render, renderHook, screen } from '@testing-library/react'
import { LONG_WAIT_HINT_AFTER_S, StreamActivityLine } from '../StreamActivityLine'
import { useStreamActivity } from '../../hooks/useStreamActivity'

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

  it('follows the round from understanding to writing a named file', () => {
    const { result } = renderHook(() => useStreamActivity())
    act(() => result.current.begin())
    expect(result.current.activity).toEqual({ kind: 'understanding' })

    act(() => result.current.onAgentSelected('writer'))
    expect(result.current.activity).toEqual({ kind: 'writing' })

    act(() => result.current.onToolCall('query_files', { id: 'f1' }))
    expect(result.current.activity).toEqual({ kind: 'reading' })

    act(() => result.current.onToolCall('create_file', { title: '第一章 雨夜' }))
    expect(result.current.activity).toEqual({ kind: 'creatingFile', title: '第一章 雨夜' })
  })

  it('shows the activity with elapsed seconds and reassures after a long wait', () => {
    vi.useFakeTimers()
    const startedAt = Date.now()
    render(<StreamActivityLine activity={{ kind: 'creatingFile', title: '第一章' }} startedAt={startedAt} />)

    expect(screen.getByText('chat:activity.creatingFileTitled:{"title":"第一章"}')).toBeInTheDocument()
    expect(screen.queryByText('chat:activity.longWaitHint')).not.toBeInTheDocument()

    act(() => {
      vi.advanceTimersByTime(LONG_WAIT_HINT_AFTER_S * 1000)
    })
    expect(screen.getByText(`chat:activity.elapsed:{"seconds":${LONG_WAIT_HINT_AFTER_S}}`)).toBeInTheDocument()
    expect(screen.getByText('chat:activity.longWaitHint')).toBeInTheDocument()
  })
})
