import { fireEvent, render, screen, within } from '@testing-library/react'
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

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string) =>
      (
        {
          'dashboard:billing.title': 'Billing',
          'dashboard:billing.subtitle': 'Manage plans',
          'dashboard:billing.ctaUpgradePro': 'Upgrade Pro',
          'settings:subscription.redeemCode': 'Redeem Code',
          'dashboard:billing.currentPlan': 'Current plan',
          'dashboard:billing.unlockHint': 'Unlock more features',
          'dashboard:billing.usageTitle': 'Usage',
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
          'settings:subscription.features.ai_conversations_per_day': 'AI conversations',
          'settings:subscription.features.max_projects': 'Projects',
          'settings:subscription.features.material_decompositions': 'Materials',
          'settings:subscription.features.custom_skills': 'Skills',
          'settings:subscription.features.inspiration_copies_monthly': 'Inspiration copies',
          'settings:subscription.unlimited': 'Unlimited',
        } as Record<string, string>
      )[key] ?? fallback ?? key,
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
    return statusResponse
  },
}))

vi.mock('../../components/dashboard/DashboardPageHeader', () => ({
  DashboardPageHeader: ({
    title,
    action,
  }: {
    title: string
    action: React.ReactNode
  }) => (
    <div>
      <h1>{title}</h1>
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

vi.mock('../../lib/subscriptionApi', () => ({
  subscriptionApi: {},
  subscriptionQueryKeys: {
    status: () => ['status'],
    quota: () => ['quota'],
  },
}))

vi.mock('../../lib/subscriptionEntitlements', () => ({
  getEntitlementMetricDefinitions: () => [
    { key: 'projects', label: 'Projects', value: (plan: { project_limit: number }) => String(plan.project_limit) },
  ],
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
    vi.stubGlobal('location', { assign: assignSpy })
  })

  it('shows the free price once while preserving paid billing periods', () => {
    render(<BillingPage />)

    const comparison = screen.getByText('Plan comparison').closest('section')
    expect(comparison).not.toBeNull()
    expect(within(comparison!).getAllByText('Free')).toHaveLength(2)
    expect(screen.queryByText('Free · Free')).not.toBeInTheDocument()
    expect(screen.getByText('¥19/month · ¥190/year')).toBeInTheDocument()
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

    fireEvent.click(screen.getByRole('button', { name: 'Upgrade Pro' }))
    expect(trackUpgradeClick).toHaveBeenCalled()
    expect(assignSpy).not.toHaveBeenCalled()
    expect(await screen.findByText('Redeem modal')).toBeInTheDocument()
    expect(trackUpgradeClick).toHaveBeenCalledWith('chat_quota_blocked', 'direct', 'redeem', 'page')

    fireEvent.click(screen.getByRole('button', { name: 'Redeem Code' }))
    expect(await screen.findByText('Redeem modal')).toBeInTheDocument()
  })

  it('opens activation directly for a selected Pro plan', () => {
    currentSearch = 'plan=pro&source=billing_header_upgrade'
    render(<BillingPage />)
    expect(screen.getByText('Redeem modal')).toBeInTheDocument()
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

  it('renders loading placeholders and hides the upgrade CTA for paid tiers', () => {
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
    expect(screen.queryByRole('button', { name: 'Upgrade Pro' })).not.toBeInTheDocument()
  })
})
