import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as subscriptionApi from '../../lib/subscriptionApi'
import { SubscriptionStatus } from '../subscription/SubscriptionStatus'

vi.mock('../../lib/subscriptionApi', () => ({
  subscriptionApi: {
    getStatus: vi.fn(),
  },
  subscriptionQueryKeys: {
    status: () => ['subscription-status', 'test-user'],
    quota: () => ['subscription-quota', 'test-user'],
    quotaLite: () => ['quota', 'test-user'],
  },
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, defaultValue: string, options?: Record<string, unknown>) => {
      const translated = {
        'subscription.active': '生效中',
        'subscription.expired': '已过期',
        'subscription.redeemCode': '兑换码',
        'subscription.upgradePrimary': '升级专业版',
      }[key]

      const text = translated ?? defaultValue
      if (!options) {
        return text
      }

      return text.replace(/{{\s*(\w+)\s*}}/g, (_, name: string) => String(options[name] ?? ''))
    },
    i18n: {
      language: 'zh-CN',
    },
  }),
}))

const mockGetStatus = vi.mocked(subscriptionApi.subscriptionApi.getStatus)

function renderWithQuery(ui: JSX.Element) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  })

  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>)
}

describe('SubscriptionStatus', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders loading skeleton while query is pending', () => {
    mockGetStatus.mockImplementation(() => new Promise(() => {}))

    renderWithQuery(<SubscriptionStatus />)

    expect(document.querySelector('.animate-pulse')).toBeInTheDocument()
  })

  it('renders free tier status with upgrade and redeem actions', async () => {
    const onUpgradeClick = vi.fn()
    const onRedeemClick = vi.fn()
    mockGetStatus.mockResolvedValue({
      tier: 'free',
      status: 'active',
      display_name: '免费版',
      display_name_en: 'Free',
      current_period_end: null,
      days_remaining: null,
      features: {
        ai_conversations_per_day: 10,
      },
    })

    renderWithQuery(
      <SubscriptionStatus onUpgradeClick={onUpgradeClick} onRedeemClick={onRedeemClick} />
    )

    expect(await screen.findByText('免费版')).toBeInTheDocument()
    expect(screen.getByText('生效中')).toBeInTheDocument()
    expect(screen.getByText('兑换码')).toBeInTheDocument()
    expect(screen.getByText('升级专业版')).toBeInTheDocument()

    fireEvent.click(screen.getByText('升级专业版'))
    expect(onUpgradeClick).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByText('兑换码'))
    expect(onRedeemClick).toHaveBeenCalledTimes(1)
  })

  it('renders paid tier days remaining and handles redeem click', async () => {
    const onRedeemClick = vi.fn()
    mockGetStatus.mockResolvedValue({
      tier: 'pro',
      status: 'active',
      display_name: '专业版',
      display_name_en: 'Pro',
      current_period_end: '2026-04-01T00:00:00Z',
      days_remaining: 10,
      features: {
        ai_conversations_per_day: -1,
      },
    })

    renderWithQuery(<SubscriptionStatus onRedeemClick={onRedeemClick} />)

    expect(await screen.findByText('专业版')).toBeInTheDocument()
    expect(screen.getByText('剩余 10 天')).toBeInTheDocument()

    fireEvent.click(screen.getByText('兑换码'))
    expect(onRedeemClick).toHaveBeenCalledTimes(1)
  })

  it('shows known entitlements with labels and hides raw unknown keys', async () => {
    mockGetStatus.mockResolvedValue({
      tier: 'pro',
      status: 'active',
      display_name: '专业版',
      display_name_en: 'Pro',
      current_period_end: '2026-04-01T00:00:00Z',
      days_remaining: 27,
      features: {
        materials_library_access: true,
        priority_support: true,
        context_window_tokens: 16384,
        some_future_flag: true,
      },
    })

    renderWithQuery(<SubscriptionStatus />)

    expect(await screen.findByText('素材库')).toBeInTheDocument()
    expect(screen.queryByText('materials_library_access')).not.toBeInTheDocument()
    expect(screen.queryByText('some_future_flag')).not.toBeInTheDocument()
    expect(screen.getByText('优先支持').nextSibling).toHaveTextContent('是')
    expect(screen.getByText('AI 单次可参考的内容量').nextSibling).toHaveTextContent(/16.?384 tokens/)
  })

  it('returns null when status data is unavailable', async () => {
    mockGetStatus.mockResolvedValue(null as never)

    renderWithQuery(<SubscriptionStatus />)

    await waitFor(() => {
      expect(mockGetStatus).toHaveBeenCalledTimes(1)
    })
    expect(screen.queryByText('兑换码')).not.toBeInTheDocument()
  })
})
