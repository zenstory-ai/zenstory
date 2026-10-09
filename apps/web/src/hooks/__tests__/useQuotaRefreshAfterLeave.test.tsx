import { describe, it, expect, vi, afterEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { QUOTA_REFRESH_AFTER_LEAVE_DELAYS_MS, useQuotaRefreshAfterLeave } from '../useQuotaRefreshAfterLeave'

vi.mock('../../lib/subscriptionApi', () => ({
  subscriptionQueryKeys: {
    quota: () => ['subscription-quota'],
    quotaLite: () => ['quota'],
  },
}))

function setup(isStreaming: boolean) {
  const client = new QueryClient()
  const invalidate = vi.spyOn(client, 'invalidateQueries').mockResolvedValue()
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  const hook = renderHook(({ streaming }) => useQuotaRefreshAfterLeave(streaming, 'project-1'), {
    wrapper,
    initialProps: { streaming: isStreaming },
  })
  return { ...hook, invalidate }
}

describe('useQuotaRefreshAfterLeave', () => {
  afterEach(() => vi.useRealTimers())

  it('re-reads the quota after leaving mid-round, once the server had time to settle it', () => {
    vi.useFakeTimers()
    const { unmount, invalidate } = setup(true)

    unmount()
    expect(invalidate).not.toHaveBeenCalled()
    vi.advanceTimersByTime(Math.max(...QUOTA_REFRESH_AFTER_LEAVE_DELAYS_MS))

    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['subscription-quota'], refetchType: 'all' })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['quota'], refetchType: 'all' })
  })

  it('does nothing when the chat goes away between rounds', () => {
    vi.useFakeTimers()
    const { unmount, invalidate, rerender } = setup(true)
    rerender({ streaming: false })

    unmount()
    vi.advanceTimersByTime(10000)
    expect(invalidate).not.toHaveBeenCalled()
  })
})
