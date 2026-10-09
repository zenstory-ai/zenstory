import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

const mockGetQuota = vi.hoisted(() => vi.fn())

vi.mock('../../lib/subscriptionApi', () => ({
  subscriptionApi: { getQuota: mockGetQuota },
  subscriptionQueryKeys: { quota: () => ['subscription-quota'] },
}))

import { isAiMessageQuotaExhausted, useAiMessageQuota } from '../useAiMessageQuota'

const quota = (used: number, limit = 10) => ({
  ai_conversations: { used, limit, reset_at: '2026-10-09T16:00:00Z' },
})

describe('useAiMessageQuota', () => {
  beforeEach(() => {
    mockGetQuota.mockReset()
  })

  it('re-reads the quota when the home page mounts, even with a fresh cached answer', async () => {
    // Same defaults as the app: cached answers stay fresh for minutes and are not refetched on mount.
    const client = new QueryClient({
      defaultOptions: { queries: { staleTime: 5 * 60 * 1000, refetchOnMount: false, retry: false } },
    })
    // Left a round at 10/10; the server refunded it after the chat's own refresh ran.
    client.setQueryData(['subscription-quota'], quota(10))
    mockGetQuota.mockResolvedValue(quota(9))
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    )

    const { result } = renderHook(() => useAiMessageQuota(), { wrapper })
    expect(result.current.exhausted).toBe(true)

    await waitFor(() => expect(result.current.exhausted).toBe(false))
    expect(mockGetQuota).toHaveBeenCalledTimes(1)
    expect(result.current.metric?.used).toBe(9)
  })

  it.each([
    ['used up', quota(10), true],
    ['some left', quota(9), false],
    ['Pro (no daily count)', quota(999, -1), false],
    ['a plan without AI messages (limit 0)', quota(0, 0), false],
    ['unknown', undefined, false],
  ])('treats %s as exhausted=%s', (_label, value, expected) => {
    expect(isAiMessageQuotaExhausted(value?.ai_conversations)).toBe(expected)
  })
})
