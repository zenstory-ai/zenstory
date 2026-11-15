import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mockGetQuota = vi.hoisted(() => vi.fn())

vi.mock('../../lib/subscriptionApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/subscriptionApi')>()
  return {
    ...actual,
    subscriptionApi: {
      ...actual.subscriptionApi,
      getQuota: mockGetQuota,
    },
  }
})

import { subscriptionApi, subscriptionQueryKeys } from '../../lib/subscriptionApi'
import { useAiMessageQuota } from '../useAiMessageQuota'

function QuotaConsumers() {
  const fullQuota = useQuery({
    queryKey: subscriptionQueryKeys.quota(),
    queryFn: () => subscriptionApi.getQuota(),
  })
  const aiQuota = useAiMessageQuota()
  return (
    <>
      <span data-testid="full-used">{fullQuota.data?.ai_conversations.used ?? 'loading'}</span>
      <span data-testid="ai-used">{aiQuota.metric?.used ?? 'loading'}</span>
    </>
  )
}

function wrapper(client: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>
  }
}

describe('quota query cache sharing', () => {
  beforeEach(() => {
    localStorage.clear()
    localStorage.setItem('user', JSON.stringify({ id: 'account-a' }))
    mockGetQuota.mockReset()
  })

  it('deduplicates one in-flight quota request across the full and AI-only consumers', async () => {
    mockGetQuota.mockResolvedValue({
      ai_conversations: { used: 2, limit: 3, reset_at: '2026-11-15T16:00:00Z' },
      projects: { used: 1, limit: 3 },
    })
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 30_000 } },
    })

    render(<QuotaConsumers />, { wrapper: wrapper(client) })

    await waitFor(() => expect(screen.getByTestId('full-used')).toHaveTextContent('2'))
    expect(screen.getByTestId('ai-used')).toHaveTextContent('2')
    expect(mockGetQuota).toHaveBeenCalledTimes(1)
  })
})
