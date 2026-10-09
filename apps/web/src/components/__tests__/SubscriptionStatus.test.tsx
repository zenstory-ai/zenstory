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

const mediaState = vi.hoisted(() => ({ isMobile: false }))

vi.mock('../../hooks/useMediaQuery', () => ({
  useIsMobile: () => mediaState.isMobile,
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, defaultValue: string, options?: Record<string, unknown>) => {
      const translated = {
        'settings:subscription.redeemCode': '兑换码',
        'settings:subscription.upgradePrimary': '开通 Pro',
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
    mediaState.isMobile = false
  })

  it('renders loading skeleton while query is pending', () => {
    mockGetStatus.mockImplementation(() => new Promise(() => {}))

    renderWithQuery(<SubscriptionStatus />)

    expect(document.querySelector('.animate-pulse')).toBeInTheDocument()
  })

  it('renders the free plan without a status word, with upgrade and redeem actions', async () => {
    const onUpgradeClick = vi.fn()
    const onRedeemClick = vi.fn()
    mockGetStatus.mockResolvedValue({
      tier: 'free',
      status: 'none',
      display_name: '免费试用',
      display_name_en: 'Free Trial',
      current_period_end: null,
      days_remaining: null,
      features: {
        ai_conversations_per_day: 10,
      },
    })

    renderWithQuery(
      <SubscriptionStatus onUpgradeClick={onUpgradeClick} onRedeemClick={onRedeemClick} />
    )

    // The stored legacy name is replaced by the glossary name for the tier.
    expect(await screen.findByText('免费版')).toBeInTheDocument()
    expect(screen.queryByText('免费试用')).not.toBeInTheDocument()
    expect(screen.queryByText(/未开通|生效中/)).not.toBeInTheDocument()
    expect(screen.getByText('兑换码')).toBeInTheDocument()
    expect(screen.getByText('开通 Pro')).toBeInTheDocument()
    expect(screen.queryByText('续费 Pro')).not.toBeInTheDocument()

    fireEvent.click(screen.getByText('开通 Pro'))
    expect(onUpgradeClick).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByText('兑换码'))
    expect(onRedeemClick).toHaveBeenCalledTimes(1)
  })

  it('shows when Pro ends and offers renewal next to redeem', async () => {
    const onRedeemClick = vi.fn()
    const onRenewClick = vi.fn()
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

    renderWithQuery(<SubscriptionStatus onRedeemClick={onRedeemClick} onRenewClick={onRenewClick} />)

    expect(await screen.findByText('Pro')).toBeInTheDocument()
    expect(screen.getByText('有效期至 2026/04/01 · 剩余 10 天')).toBeInTheDocument()

    fireEvent.click(screen.getByText('续费 Pro'))
    expect(onRenewClick).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByText('兑换码'))
    expect(onRedeemClick).toHaveBeenCalledTimes(1)
  })

  it('uses the 44px touch size on phones like the other settings tabs, and 40px on desktop', async () => {
    const freeStatus = {
      tier: 'free',
      status: 'none',
      display_name: '免费试用',
      display_name_en: 'Free Trial',
      current_period_end: null,
      days_remaining: null,
      features: { ai_conversations_per_day: 10 },
    }
    mockGetStatus.mockResolvedValue(freeStatus)
    const actions = <SubscriptionStatus onUpgradeClick={vi.fn()} onRedeemClick={vi.fn()} />

    mediaState.isMobile = true
    const phone = renderWithQuery(actions)
    const phoneUpgrade = (await screen.findByText('开通 Pro')).closest('button')!
    const phoneRedeem = screen.getByText('兑换码').closest('button')!
    expect(phoneUpgrade.className).toContain('min-h-[44px]')
    expect(phoneRedeem.className).toContain('min-h-[44px]')
    phone.unmount()

    mediaState.isMobile = false
    renderWithQuery(actions)
    const desktopUpgrade = (await screen.findByText('开通 Pro')).closest('button')!
    expect(desktopUpgrade.className).toContain('min-h-[40px]')
    expect(screen.getByText('兑换码').closest('button')!.className).toContain('min-h-[40px]')
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
