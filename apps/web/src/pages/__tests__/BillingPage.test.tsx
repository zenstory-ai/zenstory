import { fireEvent, render, screen, within } from '@testing-library/react'
import { StrictMode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import BillingPage from '../BillingPage'

const trackUpgradeClick = vi.fn()
const trackUpgradeConversion = vi.fn()
const trackEvent = vi.fn()
const refetchStatus = vi.fn()
const refetchCatalog = vi.fn()
const refetchQuota = vi.fn()
const assignSpy = vi.fn()
const inspirationFeature = vi.hoisted(() => ({ enabled: true }))

let currentSearch = 'source=chat_quota_blocked'

let statusResponse: Record<string, unknown> = {}
let catalogResponse: Record<string, unknown> = {}
let quotaResponse: Record<string, unknown> = {}
let paymentOptionsResponse: Record<string, unknown> = {}

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string, values?: Record<string, unknown>) =>
      (
        (
        {
          'dashboard:billing.title': 'Billing',
          'dashboard:billing.subtitle': 'Manage plans',
          'dashboard:billing.ctaUpgradePro': 'Upgrade Pro',
          'dashboard:billing.ctaBuyPro': 'Buy Pro Online',
          'dashboard:billing.ctaRenewPro': 'Renew Pro',
          'dashboard:billing.ctaProNeutral': 'Get or renew Pro',
          'settings:subscription.redeemCode': 'Redeem Code',
          'dashboard:billing.currentPlan': 'Current plan',
          'dashboard:billing.usageTitle': 'Usage',
          'dashboard:billing.dailyQuotaResetHint': 'Daily AI conversation quotas reset at 00:00 Beijing time (UTC+8).',
          'dashboard:billing.freeDailyMessageLimitHint': `Free users can send up to ${values?.limit} AI messages per day. It resets the next day at 00:00 Beijing time (UTC+8).`,
          'dashboard:billing.monthlyQuotaResetHint': 'Monthly quotas reset on the 1st at 00:00 Beijing time (UTC+8).',
          'common:error': 'Load failed',
          'common:retry': 'Retry',
          'dashboard:billing.compareTitle': 'Plan comparison',
          'dashboard:billing.current': 'Current',
          'dashboard:billing.recommended': 'Recommended',
          'dashboard:billing.perMonth': '/month',
          'dashboard:billing.perYear': '/year',
          'dashboard:billing.free': 'Free',
          'dashboard:billing.unknownPlan': 'Unknown',
          'common:noData': 'No data',
          'dashboard:billing.metricAiConversations': 'AI conversations',
          'dashboard:billing.metricProjects': 'Projects',
          'dashboard:billing.metricMaterialDecompositions': 'Materials',
          'dashboard:billing.metricCustomSkills': 'Skills',
          'dashboard:billing.metricInspirationCopies': 'Inspiration copies',
          'settings:subscription.unlimited': 'Unlimited',
          'dashboard:billing.priceWithYearlyOffer': '{{monthly}}, or {{yearly}} (≈{{equivalent}}/month, save {{percent}}%)',
          'dashboard:billing.proNoDailyLimit': 'Pro has no daily AI message limit.',
          'dashboard:billing.availableWithPro': 'Available with Pro',
        } as Record<string, string>
      )[key] ?? fallback ?? key
      ).replace(/{{\s*(\w+)\s*}}/g, (_, name: string) => String(values?.[name] ?? '')),
    i18n: {
      language: 'en-US',
    },
  }),
}))

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return {
    ...actual,
    useSearchParams: () => [new URLSearchParams(currentSearch)],
  }
})

vi.mock('@tanstack/react-query', () => ({
  useQuery: ({ queryKey }: { queryKey: string[] }) => {
    const firstKey = queryKey[0]
    if (firstKey === 'public-subscription-catalog') {
      return catalogResponse
    }
    if (firstKey === 'quota') {
      return quotaResponse
    }
    if (firstKey === 'payment-options') {
      return paymentOptionsResponse
    }
    return statusResponse
  },
}))

vi.mock('../../components/dashboard/DashboardPageHeader', () => ({
  DashboardPageHeader: ({
    title,
    subtitle,
    action,
  }: {
    title: string
    subtitle?: string
    action: React.ReactNode
  }) => (
    <div>
      <h1>{title}</h1>
      {subtitle && <p>{subtitle}</p>}
      {action}
    </div>
  ),
}))

vi.mock('../../components/ui/Button', () => ({
  Button: ({
    children,
    onClick,
  }: {
    children: React.ReactNode
    onClick?: () => void
  }) => <button onClick={onClick}>{children}</button>,
}))

vi.mock('../../components/ui/Badge', () => ({
  Badge: ({ children }: { children: React.ReactNode }) => <span>{children}</span>,
}))

vi.mock('../../components/ui/Card', () => ({
  Card: ({ children }: { children: React.ReactNode }) => <section>{children}</section>,
}))

vi.mock('../../components/subscription/RedeemCodeModal', () => ({
  RedeemCodeModal: ({ isOpen }: { isOpen: boolean }) => (isOpen ? <div>Redeem modal</div> : null),
}))

vi.mock('../../components/subscription/PaymentCheckoutModal', () => ({
  PaymentCheckoutModal: ({
    isOpen,
    initialCycle,
    upgradeSource,
    isRenewal,
    redeemEntry,
  }: { isOpen: boolean; initialCycle: string; upgradeSource?: string; isRenewal?: boolean; redeemEntry?: string }) =>
    (isOpen ? (
      <div
        data-cycle={initialCycle}
        data-source={upgradeSource}
        data-renewal={String(Boolean(isRenewal))}
        data-redeem-entry={redeemEntry}
      >
        Payment modal
      </div>
    ) : null),
}))

vi.mock('../../lib/subscriptionApi', () => ({
  subscriptionApi: {},
  subscriptionQueryKeys: {
    status: () => ['status'],
    quota: () => ['quota'],
  },
}))

vi.mock('../../lib/paymentApi', () => ({
  paymentApi: {},
  paymentQueryKeys: {
    options: () => ['payment-options'],
  },
}))

vi.mock('../../lib/subscriptionEntitlements', async () => ({
  ...(await vi.importActual<typeof import('../../lib/subscriptionEntitlements')>('../../lib/subscriptionEntitlements')),
  getEntitlementMetricDefinitions: () => [
    {
      key: 'projects',
      label: 'Projects',
      value: (plan: { project_limit: number }) => String(plan.project_limit),
      compareValue: (plan: { project_limit: number }) => plan.project_limit,
    },
    {
      key: 'material_decompositions_monthly',
      label: 'Material breakdowns',
      value: (plan: { name: string }) => (plan.name === 'pro' ? '5 / month' : 'Not included'),
      compareValue: (plan: { name: string }) => (plan.name === 'pro' ? 5 : 0),
    },
  ],
  filterAvailableMetrics: <T,>(definitions: T[]) => definitions,
  getLocalizedPlanDisplayName: (plan: { display_name?: string; name?: string }) => plan.display_name ?? plan.name ?? 'Plan',
}))

vi.mock('../../config/upgradeExperience', () => ({
  buildUpgradeUrl: (path: string, source: string) => `${path}?source=${source}`,
  getUpgradePromptDefinition: () => ({
    source: 'billing_header_upgrade',
    pricingPath: '/pricing',
    billingPath: '/billing',
  }),
}))

vi.mock('../../lib/upgradeAnalytics', () => ({
  trackUpgradeClick: (...args: unknown[]) => trackUpgradeClick(...args),
  trackUpgradeConversion: (...args: unknown[]) => trackUpgradeConversion(...args),
}))

vi.mock('../../lib/analytics', () => ({
  trackEvent: (...args: unknown[]) => trackEvent(...args),
}))

vi.mock('../../config/inspirations', () => ({
  inspirationsConfig: inspirationFeature,
}))

describe('BillingPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    sessionStorage.clear()
    inspirationFeature.enabled = true
    currentSearch = 'source=chat_quota_blocked'
    statusResponse = {
      data: {
        tier: 'free',
        display_name: 'Free',
        display_name_en: 'Free',
        status: 'active',
      },
      isLoading: false,
      isError: false,
      refetch: refetchStatus,
    }
    catalogResponse = {
      data: {
        tiers: [
          { id: 'pro', name: 'pro', display_name: 'Pro', price_monthly_cents: 1900, price_yearly_cents: 19000, recommended: true, project_limit: 50 },
          { id: 'free', name: 'free', display_name: 'Free', price_monthly_cents: 0, price_yearly_cents: 0, recommended: false, project_limit: 3 },
        ],
      },
      isLoading: false,
      isFetching: false,
      isError: false,
      refetch: refetchCatalog,
    }
    quotaResponse = {
      data: {
        ai_conversations: { used: 2, limit: 10 },
        projects: { used: 1, limit: 3 },
        material_decompositions: { used: 0, limit: -1 },
        skill_creates: { used: 1, limit: 5 },
        inspiration_copies: { used: 4, limit: 5 },
      },
      isLoading: false,
      isError: false,
      refetch: refetchQuota,
    }
    paymentOptionsResponse = {
      data: { enabled: true, payment_methods: ['alipay'] },
      isLoading: false,
    }
    vi.stubGlobal('location', { assign: assignSpy })
  })

  it('shows the free price once while preserving paid billing periods', () => {
    render(<BillingPage />)

    const comparison = screen.getByText('Plan comparison').closest('section')
    expect(comparison).not.toBeNull()
    expect(within(comparison!).getAllByText('Free')).toHaveLength(2)
    expect(screen.queryByText('Free · Free')).not.toBeInTheDocument()
    // The yearly offer is spelled out next to both prices.
    expect(screen.getByText('¥19/month, or ¥190/year (≈¥15.83/month, save 17%)')).toBeInTheDocument()
  })

  it('keeps online checkout as the main action when it is available', () => {
    render(<BillingPage />)
    expect(screen.getByRole('button', { name: 'Buy Pro Online' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Redeem Code' })).toBeInTheDocument()
    expect(screen.queryByTestId('billing-checkout-unavailable')).not.toBeInTheDocument()
    expect(screen.getByText('Manage plans')).toBeInTheDocument()
  })

  it('does not tell a Pro author to upgrade in the page subtitle', () => {
    statusResponse = {
      ...statusResponse,
      data: { tier: 'pro', display_name: 'Pro', display_name_en: 'Pro', status: 'active' },
    }
    render(<BillingPage />)
    expect(screen.getByText('查看当前套餐和用量，到期前可以续费 Pro 或使用兑换码。')).toBeInTheDocument()
    expect(screen.queryByText('Manage plans')).not.toBeInTheDocument()
  })

  it('shows a used-up allowance in the error colour, like the header badge, and a near-full one as a warning', () => {
    quotaResponse = {
      ...quotaResponse,
      data: {
        ...(quotaResponse.data as Record<string, unknown>),
        ai_conversations: { used: 10, limit: 10 },
        inspiration_copies: { used: 4, limit: 5 },
      },
    }
    render(<BillingPage />)

    expect(screen.getByTestId('billing-usage-ai_conversations').className).toContain('text-[hsl(var(--error))]')
    expect(screen.getByTestId('billing-usage-bar-ai_conversations').className).toContain('bg-[hsl(var(--error))]')
    expect(screen.getByTestId('billing-usage-inspiration_copies').className).toContain('text-[hsl(var(--warning))]')
    expect(screen.getByTestId('billing-usage-bar-inspiration_copies').className).toContain('bg-[hsl(var(--warning))]')
  })

  it('tells Pro users there is no daily limit instead of a reset time', () => {
    statusResponse = {
      ...statusResponse,
      data: { tier: 'pro', display_name: 'Pro', display_name_en: 'Pro', status: 'active' },
    }
    quotaResponse = {
      ...quotaResponse,
      data: { ...(quotaResponse.data as Record<string, unknown>), ai_conversations: { used: 12, limit: -1 } },
    }
    render(<BillingPage />)

    const dailyUsage = screen.getByText('AI conversations').parentElement!.parentElement!
    expect(within(dailyUsage).getByText('Pro has no daily AI message limit.')).toBeInTheDocument()
    expect(within(dailyUsage).queryByText(/reset/)).not.toBeInTheDocument()
  })

  it('shows a feature the plan does not include as available with Pro, without a bar or reset hint', () => {
    quotaResponse = {
      ...quotaResponse,
      data: { ...(quotaResponse.data as Record<string, unknown>), material_decompositions: { used: 0, limit: 0 } },
    }
    render(<BillingPage />)

    const materialsUsage = screen.getByText('Materials').parentElement!.parentElement!
    expect(within(materialsUsage).getByText('Available with Pro')).toBeInTheDocument()
    expect(within(materialsUsage).queryByText('0/0')).not.toBeInTheDocument()
    expect(within(materialsUsage).queryByText(/Monthly quotas reset/)).not.toBeInTheDocument()
    expect(materialsUsage.querySelector('.rounded-full')).toBeNull()
  })

  it('marks a feature a plan does not include with a neutral cross, not a green check', () => {
    render(<BillingPage />)

    const comparison = screen.getByText('Plan comparison').closest('section')!
    const rows = within(comparison).getAllByTestId('billing-plan-metric-material_decompositions_monthly')
    const freeRow = rows.find((row) => within(row).queryByText('Not included'))!
    const proRow = rows.find((row) => within(row).queryByText('5 / month'))!

    expect(freeRow).toHaveAttribute('data-included', 'false')
    expect(freeRow.querySelector('.lucide-x')).not.toBeNull()
    expect(freeRow.querySelector('.lucide-check')).toBeNull()
    expect(proRow).toHaveAttribute('data-included', 'true')
    expect(proRow.querySelector('.lucide-check')).not.toBeNull()
  })

  it('opens checkout as a renewal for Pro users', () => {
    // Opened from the header, not from an attributed link.
    currentSearch = ''
    statusResponse = {
      ...statusResponse,
      data: { tier: 'pro', display_name: 'Pro', display_name_en: 'Pro', status: 'active' },
    }
    render(<BillingPage />)

    fireEvent.click(screen.getByRole('button', { name: 'Renew Pro' }))
    expect(screen.getByText('Payment modal')).toHaveAttribute('data-renewal', 'true')
    // Renewals are attributed apart from upgrades.
    expect(screen.getByText('Payment modal')).toHaveAttribute('data-source', 'billing_header_renew')
    expect(trackUpgradeClick).toHaveBeenCalledWith('billing_header_renew', 'direct', 'checkout', 'page')
  })

  it('shows the free daily message limit and Beijing midnight reset beside usage', () => {
    render(<BillingPage />)

    const dailyUsage = screen.getByText('AI conversations').parentElement!.parentElement!
    expect(within(dailyUsage).getByText('Free users can send up to 10 AI messages per day. It resets the next day at 00:00 Beijing time (UTC+8).')).toBeInTheDocument()
    expect(within(dailyUsage).getByText('2/10')).toBeInTheDocument()
  })

  it('explains monthly resets for materials, but not the owned skills cap', () => {
    render(<BillingPage />)

    const materialsUsage = screen.getByText('Materials').parentElement!.parentElement!
    expect(within(materialsUsage).getByText('Monthly quotas reset on the 1st at 00:00 Beijing time (UTC+8).')).toBeInTheDocument()
    const skillsUsage = screen.getByText('Skills').parentElement!.parentElement!
    expect(within(skillsUsage).queryByText(/Monthly quotas reset/)).not.toBeInTheDocument()
  })

  it('tracks attribution, renders current usage, and supports upgrade and redeem actions', async () => {
    render(<BillingPage />)

    expect(trackUpgradeConversion).toHaveBeenCalledWith('chat_quota_blocked', 'billing')
    expect(trackEvent).toHaveBeenCalledWith('billing_page_view', expect.objectContaining({
      attribution_source: 'chat_quota_blocked',
    }))

    expect(screen.getByText('Billing')).toBeInTheDocument()
    expect(screen.getByText('AI conversations')).toBeInTheDocument()
    expect(screen.getByText('2/10')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Buy Pro Online' }))
    expect(trackUpgradeClick).toHaveBeenCalled()
    expect(assignSpy).not.toHaveBeenCalled()
    expect(await screen.findByText('Payment modal')).toBeInTheDocument()
    // This page has its own 兑换码 button, so the unavailable notice may point to it.
    expect(screen.getByText('Payment modal')).toHaveAttribute('data-redeem-entry', 'on-page')
    expect(trackUpgradeClick).toHaveBeenCalledWith('chat_quota_blocked', 'direct', 'checkout', 'page')

    fireEvent.click(screen.getByRole('button', { name: 'Redeem Code' }))
    expect(await screen.findByText('Redeem modal')).toBeInTheDocument()
  })

  it('opens activation directly for a selected Pro plan', () => {
    currentSearch = 'plan=pro&source=billing_header_upgrade'
    render(<BillingPage />)
    expect(screen.getByText('Payment modal')).toBeInTheDocument()
    expect(screen.getByText('Payment modal')).toHaveAttribute('data-source', 'billing_header_upgrade')
    expect(assignSpy).not.toHaveBeenCalled()
  })

  it('does not open activation for an unrelated plan', () => {
    currentSearch = 'plan=free'
    render(<BillingPage />)
    expect(screen.queryByText('Redeem modal')).not.toBeInTheDocument()
  })

  it('hides inspiration quota usage when inspirations are disabled', () => {
    inspirationFeature.enabled = false
    render(<BillingPage />)

    expect(screen.queryByText('Inspiration copies')).not.toBeInTheDocument()
    expect(screen.queryByText('4/5')).not.toBeInTheDocument()
  })

  it('renders error recovery and empty catalog states', () => {
    statusResponse = { ...statusResponse, isError: true }
    catalogResponse = { ...catalogResponse, data: { tiers: [] } }
    quotaResponse = { ...quotaResponse, isError: true }

    render(<BillingPage />)

    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(refetchStatus).toHaveBeenCalled()
    expect(refetchCatalog).toHaveBeenCalled()
    expect(refetchQuota).toHaveBeenCalled()
    expect(screen.getByText('No data')).toBeInTheDocument()
  })

  it('preserves the yearly checkout intent during Strict Mode initialization', () => {
    sessionStorage.setItem('payment_cycle_intent', 'year')
    currentSearch = 'plan=pro'
    render(<StrictMode><BillingPage /></StrictMode>)
    expect(screen.getByText('Payment modal')).toHaveAttribute('data-cycle', 'year')
    expect(sessionStorage.getItem('payment_cycle_intent')).toBeNull()
  })

  it('renders loading placeholders and offers renewal for paid tiers', () => {
    statusResponse = {
      ...statusResponse,
      data: {
        tier: 'pro',
        display_name: 'Pro',
        display_name_en: 'Pro',
        status: 'active',
      },
      isLoading: true,
    }
    catalogResponse = { ...catalogResponse, isLoading: true, isFetching: true }
    quotaResponse = { ...quotaResponse, isLoading: true }

    const { container } = render(<BillingPage />)

    expect(container.querySelectorAll('.animate-pulse').length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: 'Renew Pro' })).toBeInTheDocument()
  })

  describe('when online checkout is not configured', () => {
    beforeEach(() => {
      paymentOptionsResponse = {
        data: { enabled: false, payment_methods: [] },
        isLoading: false,
      }
    })

    it('says online payment is off and offers the redeem code itself, not a "开通 Pro" that only opens it', async () => {
      render(<BillingPage />)

      expect(screen.queryByRole('button', { name: 'Buy Pro Online' })).not.toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'Upgrade Pro' })).not.toBeInTheDocument()
      expect(screen.queryByText(/微信|WeChat|AIchuangzuo/)).not.toBeInTheDocument()
      expect(screen.getByTestId('billing-checkout-unavailable')).toHaveTextContent(
        '暂时不能在线付款。有兑换码的话，点「兑换码」就能开通 Pro。',
      )
      expect(screen.getByText('查看当前套餐和用量，需要更多额度时可以用兑换码开通 Pro。')).toBeInTheDocument()

      fireEvent.click(screen.getByRole('button', { name: 'Redeem Code' }))
      expect(trackUpgradeClick).toHaveBeenCalledWith('chat_quota_blocked', 'direct', 'redeem', 'page')
      expect(await screen.findByText('Redeem modal')).toBeInTheDocument()
      expect(screen.queryByText('Payment modal')).not.toBeInTheDocument()
    })

    it('opens the redeem modal for a selected Pro plan', () => {
      currentSearch = 'plan=pro'
      render(<BillingPage />)
      expect(screen.getByText('Redeem modal')).toBeInTheDocument()
      expect(screen.queryByText('Payment modal')).not.toBeInTheDocument()
    })

    it('does not offer online renewal to paid tiers', () => {
      statusResponse = {
        ...statusResponse,
        data: { tier: 'pro', display_name: 'Pro', display_name_en: 'Pro', status: 'active' },
      }
      render(<BillingPage />)
      expect(screen.queryByRole('button', { name: 'Renew Pro' })).not.toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'Upgrade Pro' })).not.toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Redeem Code' })).toBeInTheDocument()
      expect(screen.queryByTestId('billing-checkout-unavailable')).not.toBeInTheDocument()
      expect(screen.getByText('查看当前套餐和用量，到期前可以用兑换码续期。')).toBeInTheDocument()
    })
  })

  it('waits for payment options before acting on a selected Pro plan', () => {
    paymentOptionsResponse = { data: undefined, isLoading: true }
    currentSearch = 'plan=pro'
    render(<BillingPage />)
    expect(screen.queryByText('Payment modal')).not.toBeInTheDocument()
    expect(screen.queryByText('Redeem modal')).not.toBeInTheDocument()
  })

  it('uses neutral checkout copy while the subscription status is unknown', () => {
    statusResponse = { ...statusResponse, data: undefined, isLoading: true }
    render(<BillingPage />)
    expect(screen.getByRole('button', { name: 'Get or renew Pro' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Renew Pro' })).not.toBeInTheDocument()
  })

  it('uses neutral checkout copy when the subscription status failed to load', () => {
    statusResponse = { ...statusResponse, data: undefined, isError: true }
    render(<BillingPage />)
    expect(screen.getByRole('button', { name: 'Get or renew Pro' })).toBeInTheDocument()
  })
})
