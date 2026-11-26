import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PaymentCheckoutModal } from '../subscription/PaymentCheckoutModal'
import { subscriptionApi } from '../../lib/subscriptionApi'
import { paymentApi } from '../../lib/paymentApi'
import type { SubscriptionCatalogResponse } from '../../types/subscription'
import zhDashboard from '../../../public/locales/zh/dashboard.json'
import enDashboard from '../../../public/locales/en/dashboard.json'
import zhCommon from '../../../public/locales/zh/common.json'
import enCommon from '../../../public/locales/en/common.json'

vi.mock('../../lib/subscriptionApi', () => ({ subscriptionApi: { getCatalog: vi.fn() } }))
vi.mock('../../lib/paymentApi', async () => ({
  ...await vi.importActual<typeof import('../../lib/paymentApi')>('../../lib/paymentApi'),
  paymentApi: { getOptions: vi.fn(), createOrder: vi.fn() },
}))
vi.mock('../../lib/analytics', () => ({ trackEvent: vi.fn() }))

const catalog = (limit: number) => ({ tiers: [{ name: 'pro', entitlements: { material_decompositions_monthly: limit } }] }) as SubscriptionCatalogResponse

async function show(language: 'zh' | 'en') {
  const i18n = createInstance()
  await i18n.init({ lng: language, fallbackLng: language, interpolation: { escapeValue: false }, resources: {
    zh: { dashboard: zhDashboard, common: zhCommon },
    en: { dashboard: enDashboard, common: enCommon },
  } })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<I18nextProvider i18n={i18n}><QueryClientProvider client={client}><MemoryRouter>
    <PaymentCheckoutModal isOpen onClose={vi.fn()} monthlyPriceCents={4900} yearlyPriceCents={39900} />
  </MemoryRouter></QueryClientProvider></I18nextProvider>)
  return client
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(paymentApi.getOptions).mockResolvedValue({ enabled: true, payment_methods: ['alipay'] })
})
afterEach(cleanup)

describe('payment Pro benefits', () => {
  it.each(['zh', 'en'] as const)('uses and refreshes the catalog limit in shipped %s copy', async (language) => {
    vi.mocked(subscriptionApi.getCatalog).mockResolvedValue(catalog(7))
    const client = await show(language)
    const text = (n: number) => language === 'zh' ? `每月 ${n} 次素材拆解` : `${n} material breakdowns per month`
    expect(await screen.findByText(new RegExp(text(7)))).toBeInTheDocument()
    expect(screen.queryByText(new RegExp(text(5)))).not.toBeInTheDocument()
    vi.mocked(subscriptionApi.getCatalog).mockResolvedValue(catalog(11))
    await act(async () => { await client.invalidateQueries({ queryKey: ['public-subscription-catalog'] }) })
    expect(await screen.findByText(new RegExp(text(11)))).toBeInTheDocument()
    expect(screen.queryByText(new RegExp(text(7)))).not.toBeInTheDocument()
    expect(paymentApi.createOrder).not.toHaveBeenCalled()
  })

  it('does not invent a count while loading or when the catalog fails', async () => {
    let reject!: (reason: Error) => void
    vi.mocked(subscriptionApi.getCatalog).mockImplementation(() => new Promise((_resolve, fail) => { reject = fail }))
    const client = await show('zh')
    expect(screen.queryByText(/每月 \d+ 次素材拆解/)).not.toBeInTheDocument()
    await act(async () => { reject(new Error('Catalog unavailable')) })
    await waitFor(() => expect(client.getQueryState(['public-subscription-catalog'])?.status).toBe('error'))
    expect(screen.queryByText(/每月 \d+ 次素材拆解/)).not.toBeInTheDocument()
  })
})

describe('checkout catalog consistency', () => {
  it('shows finite AI and project limits rather than promising unlimited access', async () => {
    vi.mocked(subscriptionApi.getCatalog).mockResolvedValue({ tiers: [{ name: 'pro', entitlements: {
      ai_conversations_per_day: 12, active_projects_limit: 8, material_decompositions_monthly: 2,
    } }] } as SubscriptionCatalogResponse)
    await show('zh')
    expect(await screen.findByText('每日 AI 消息 12 条 · 最多 8 个项目 · 每月 2 次素材拆解')).toBeInTheDocument()
    expect(screen.queryByText(/AI 消息不限|项目不限/)).not.toBeInTheDocument()
    expect(paymentApi.createOrder).not.toHaveBeenCalled()
  })

  it('does not promise AI or project benefits when those catalog fields are absent', async () => {
    vi.mocked(subscriptionApi.getCatalog).mockResolvedValue(catalog(7))
    await show('en')
    expect(await screen.findByText('7 material breakdowns per month')).toBeInTheDocument()
    expect(screen.queryByText(/Unlimited AI|Unlimited projects/)).not.toBeInTheDocument()
  })
})
