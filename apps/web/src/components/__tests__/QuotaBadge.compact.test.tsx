import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as subscriptionApi from '../../lib/subscriptionApi'
import { QuotaBadge } from '../subscription/QuotaBadge'

vi.mock('../../lib/subscriptionApi', () => ({
  subscriptionApi: {
    getQuota: vi.fn(),
    getStatus: vi.fn(async () => ({ tier: "free" })),
  },
  subscriptionQueryKeys: {
    status: () => ['subscription-status', 'test-user'],
    quota: () => ['subscription-quota', 'test-user'],
    quotaLite: () => ['quota', 'test-user'],
  },
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, defaultValue: string, values?: { used?: number; limit?: number }) => {
      if (key === 'subscription.aiUsageUnlimited') return 'AI 消息不限条数'
      if (key === 'subscription.aiUsageCount') return `今日 AI 消息 ${values?.used}/${values?.limit} 条`
      return defaultValue
    },
  }),
}))

const mockGetQuota = vi.mocked(subscriptionApi.subscriptionApi.getQuota)

const quotaResponse = (used: number, limit: number) => ({
  ai_conversations: { used, limit, reset_at: '2026-10-09T16:00:00Z' },
  projects: { used: 1, limit: 3 },
}) as never

function renderWithQuery(ui: JSX.Element) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>)
}

describe('QuotaBadge compact', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows only used/limit and gives screen readers the full wording', async () => {
    mockGetQuota.mockResolvedValue(quotaResponse(4, 10))

    renderWithQuery(<QuotaBadge compact />)

    const pill = await screen.findByTestId('quota-badge-compact')
    // A static counter is not a live region and must not compete with real status messages.
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(pill).toHaveAttribute('title', '今日 AI 消息 4/10 条')
    expect(screen.getByText('今日 AI 消息 4/10 条')).toHaveClass('sr-only')
    expect(screen.getByText('4/10').closest('[aria-hidden="true"]')).not.toBeNull()
  })

  it('turns the upgrade action into an icon button with an accessible name', async () => {
    mockGetQuota.mockResolvedValue(quotaResponse(10, 10))

    renderWithQuery(<QuotaBadge compact />)

    const upgrade = await screen.findByRole('button', { name: '开通 Pro' })
    expect(upgrade).toHaveAttribute('title', '开通 Pro')
    expect(upgrade.textContent).toBe('')
  })

  it('keeps the default full label when compact is not set', async () => {
    mockGetQuota.mockResolvedValue(quotaResponse(10, 10))

    renderWithQuery(<QuotaBadge />)

    expect(await screen.findByText('今日 AI 消息 10/10 条')).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: '开通 Pro' })).toHaveTextContent('开通 Pro')
    expect(screen.queryByTestId('quota-badge-compact')).not.toBeInTheDocument()
  })
})
