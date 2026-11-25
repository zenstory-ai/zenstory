import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { PaymentCheckoutModal } from '../PaymentCheckoutModal'

let currentLang = 'en'
const getOptionsMock = vi.fn()
const createOrderMock = vi.fn()
const getCatalogMock = vi.fn()
const assignSpy = vi.fn()

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (_key: string, fallback?: string | Record<string, unknown>, vars?: Record<string, unknown>) => {
      const text = typeof fallback === 'string' ? fallback : _key
      const params = typeof fallback === 'object' && fallback !== null ? fallback : vars
      if (!params) return text
      return Object.entries(params).reduce(
        (acc, [k, v]) => acc.replaceAll(`{{${k}}}`, String(v)),
        text,
      )
    },
    i18n: {
      get language() {
        return currentLang
      },
    },
  }),
}))

vi.mock('../../../lib/paymentApi', () => ({
  paymentQueryKeys: {
    options: () => ['payment', 'options'],
    order: (id: string) => ['payment', 'order', id],
  },
  paymentApi: {
    getOptions: () => getOptionsMock(),
    createOrder: (req: unknown) => createOrderMock(req),
  },
}))

vi.mock('../../../lib/subscriptionApi', () => ({
  subscriptionApi: {
    getCatalog: () => getCatalogMock(),
  },
}))

vi.mock('../../../lib/analytics', () => ({
  trackEvent: vi.fn(),
}))

function renderModal(props: Partial<Parameters<typeof PaymentCheckoutModal>[0]> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <PaymentCheckoutModal
          isOpen
          onClose={vi.fn()}
          monthlyPriceCents={4900}
          yearlyPriceCents={39900}
          monthlyUsdPriceCents={999}
          yearlyUsdPriceCents={7900}
          {...props}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('PaymentCheckoutModal dual-currency Creem + Zpay checkout', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    currentLang = 'en'
    getCatalogMock.mockResolvedValue({
      tiers: [{ name: 'pro', price_monthly_usd_cents: 999, price_yearly_usd_cents: 7900, entitlements: {} }],
    })
    getOptionsMock.mockResolvedValue({ enabled: true, payment_methods: ['alipay', 'card'] })
    vi.stubGlobal('location', { assign: assignSpy })
  })

  it('defaults to USD card in English, allows switching to Alipay CNY, and redirects to Creem GET checkout', async () => {
    createOrderMock.mockResolvedValue({
      order: {
        id: 'ord-1',
        provider: 'creem',
        currency: 'USD',
        out_trade_no: '202611240000001',
        cycle: 'month',
        amount_cents: 999,
        payment_method: 'card',
      },
      checkout: {
        action: 'https://creem.io/test/checkout/prod_123/ch_456',
        method: 'GET',
        fields: {},
      },
    })

    renderModal()

    const payBtn = screen.getByRole('button', { name: /\$9\.99/ })
    await waitFor(() => expect(payBtn).toBeEnabled())

    // Switch to Alipay (CNY) and verify price flips to ¥49.
    fireEvent.click(screen.getByRole('radio', { name: /支付宝 \(CNY\)/ }))
    expect(screen.getByRole('button', { name: /¥49/ })).toBeEnabled()

    // Switch back to Card (USD) and submit Creem checkout.
    fireEvent.click(screen.getByRole('radio', { name: /银行卡 \(USD\)/ }))
    fireEvent.click(screen.getByRole('button', { name: /\$9\.99/ }))

    await waitFor(() => {
      expect(createOrderMock).toHaveBeenCalledWith({
        plan_name: 'pro',
        cycle: 'month',
        payment_method: 'card',
      })
    })
    expect(assignSpy).toHaveBeenCalledWith('https://creem.io/test/checkout/prod_123/ch_456')
  })

  it('rejects non-allowlisted redirect hosts on GET checkout', async () => {
    createOrderMock.mockResolvedValue({
      order: { id: 'ord-2', out_trade_no: '202611240000002', cycle: 'month', payment_method: 'card' },
      checkout: { action: 'https://evil.example.com/phish', method: 'GET', fields: {} },
    })

    renderModal()
    const payBtn = screen.getByRole('button', { name: /\$9\.99/ })
    await waitFor(() => expect(payBtn).toBeEnabled())
    fireEvent.click(payBtn)

    expect(await screen.findByRole('alert')).toHaveTextContent('无法跳转到支付页面')
    expect(assignSpy).not.toHaveBeenCalled()
  })

  it('accepts checkout.creem.io as an allowlisted redirect host on GET checkout', async () => {
    createOrderMock.mockResolvedValue({
      order: { id: 'ord-3', out_trade_no: '202611240000003', cycle: 'month', payment_method: 'card' },
      checkout: { action: 'https://checkout.creem.io/test/checkout/prod_123/ch_789', method: 'GET', fields: {} },
    })

    renderModal()
    const payBtn = screen.getByRole('button', { name: /\$9\.99/ })
    await waitFor(() => expect(payBtn).toBeEnabled())
    fireEvent.click(payBtn)

    await waitFor(() => {
      expect(assignSpy).toHaveBeenCalledWith('https://checkout.creem.io/test/checkout/prod_123/ch_789')
    })
  })

  it('defaults to Alipay CNY in Chinese when both methods are enabled and switches to USD card on selection', async () => {
    currentLang = 'zh-CN'
    renderModal()
    const payBtn = screen.getByRole('button', { name: /¥49/ })
    await waitFor(() => expect(payBtn).toBeEnabled())
    expect(screen.getByRole('radio', { name: /年付/ })).toHaveTextContent('¥399')

    fireEvent.click(screen.getByRole('radio', { name: /银行卡 \(USD\)/ }))
    expect(screen.getByRole('button', { name: /\$9\.99/ })).toBeEnabled()
    expect(screen.getByRole('radio', { name: /年付/ })).toHaveTextContent('$79')
  })

  it('shows only Alipay CNY prices when only alipay is enabled in English', async () => {
    currentLang = 'en'
    getOptionsMock.mockResolvedValue({ enabled: true, payment_methods: ['alipay'] })
    renderModal()
    const payBtn = await screen.findByRole('button', { name: /¥49/ })
    await waitFor(() => expect(payBtn).toBeEnabled())
    expect(screen.getByRole('radio', { name: /年付/ })).toHaveTextContent('¥399')
    expect(screen.queryByRole('radiogroup', { name: '支付方式' })).not.toBeInTheDocument()
  })

  it('shows only Card USD prices when only card is enabled in Chinese', async () => {
    currentLang = 'zh-CN'
    getOptionsMock.mockResolvedValue({ enabled: true, payment_methods: ['card'] })
    renderModal()
    const payBtn = await screen.findByRole('button', { name: /\$9\.99/ })
    await waitFor(() => expect(payBtn).toBeEnabled())
    expect(screen.getByRole('radio', { name: /年付/ })).toHaveTextContent('$79')
    expect(screen.queryByRole('radiogroup', { name: '支付方式' })).not.toBeInTheDocument()
  })

  it('defaults to Card USD for non-Chinese non-English locales when both methods are enabled', async () => {
    currentLang = 'ja'
    renderModal()
    const payBtn = await screen.findByRole('button', { name: /\$9\.99/ })
    await waitFor(() => expect(payBtn).toBeEnabled())
    expect(screen.getByRole('radio', { name: /年付/ })).toHaveTextContent('$79')
  })
})
