import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

const mockGetStatus = vi.hoisted(() => vi.fn())

vi.mock('../../lib/subscriptionApi', () => ({
  subscriptionApi: { getStatus: mockGetStatus },
  subscriptionQueryKeys: { status: () => ['subscription-status'] },
}))

import { usePaidPlanWhenOpen } from '../usePaidPlanWhenOpen'

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  return renderHook(({ open }) => usePaidPlanWhenOpen(open), { wrapper, initialProps: { open: true } })
}

describe('usePaidPlanWhenOpen', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    mockGetStatus.mockReset()
  })
  afterEach(() => vi.useRealTimers())

  it('stays neutral while the plan is loading and gives up after 4 seconds with the free copy', async () => {
    mockGetStatus.mockReturnValue(new Promise(() => {}))
    const { result } = setup()
    expect(result.current).toEqual({ isPaid: false, resolved: false })

    await act(async () => {
      vi.advanceTimersByTime(3999)
    })
    expect(result.current.resolved).toBe(false)

    await act(async () => {
      vi.advanceTimersByTime(1)
    })
    expect(result.current).toEqual({ isPaid: false, resolved: true })
  })

  it('waits for a fresh answer on the next open instead of reusing an earlier give-up', async () => {
    let failSlowLookup: (reason: Error) => void = () => {}
    mockGetStatus.mockReturnValueOnce(new Promise((_resolve, reject) => { failSlowLookup = reject }))
    const { result, rerender } = setup()
    await act(async () => {
      vi.advanceTimersByTime(4000)
    })
    expect(result.current.resolved).toBe(true)
    // The slow lookup finally fails too.
    await act(async () => {
      failSlowLookup(new Error('network'))
    })

    rerender({ open: false })
    let answer: (value: { tier: string }) => void = () => {}
    mockGetStatus.mockReturnValueOnce(new Promise((resolve) => { answer = resolve }))
    rerender({ open: true })
    // A paid author must not see the free copy flash while the plan is looked up again.
    expect(result.current.resolved).toBe(false)

    await act(async () => {
      answer({ tier: 'pro' })
    })
    expect(result.current).toEqual({ isPaid: true, resolved: true })
  })
})
