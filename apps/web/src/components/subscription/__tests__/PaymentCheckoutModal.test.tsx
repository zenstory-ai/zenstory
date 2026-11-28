import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PaymentCheckoutModal, type PaymentRedeemEntry } from '../PaymentCheckoutModal'
import { paymentApi } from '../../../lib/paymentApi'
import { subscriptionApi } from '../../../lib/subscriptionApi'
import { ApiError } from '../../../lib/apiClient'
import { trackEvent } from '../../../lib/analytics'
import zhCommon from '../../../../public/locales/zh/common.json'
import zhDashboard from '../../../../public/locales/zh/dashboard.json'
import zhErrors from '../../../../public/locales/zh/errors.json'
import enCommon from '../../../../public/locales/en/common.json'
import enDashboard from '../../../../public/locales/en/dashboard.json'
import enErrors from '../../../../public/locales/en/errors.json'

vi.mock('../../../lib/analytics', () => ({ trackEvent: vi.fn() }))

vi.mock('../../../lib/paymentApi', async () => {
  const actual = await vi.importActual<typeof import('../../../lib/paymentApi')>('../../../lib/paymentApi')
  return {
    ...actual,
    paymentApi: {
      getOptions: vi.fn(),
      createOrder: vi.fn(),
    },
  }
})

vi.mock('../../../lib/subscriptionApi', () => ({
  subscriptionApi: {
    getCatalog: vi.fn(),
  },
}))

const i18nState = vi.hoisted(() => ({
  language: 'zh-CN',
  resources: null as Record<string, unknown> | null,
}))

function lookup(resources: Record<string, unknown>, key: string): unknown {
  const [namespace, path] = key.split(':')
  return path.split('.').reduce<unknown>(
    (node, part) => (node && typeof node === 'object' ? (node as Record<string, unknown>)[part] : undefined),
    resources[namespace],
  )
}

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string | Record<string, unknown>, options?: Record<string, unknown>) => {
      const textFallback = typeof fallback === 'string' ? fallback : undefined
      const params = typeof fallback === 'object' && fallback !== null ? fallback : options
      const interpolate = (text: string | undefined) =>
        (text ?? key).replace(/\{\{(\w+)\}\}/g, (_match, name: string) => String(params?.[name] ?? ''))
      if (i18nState.resources) {
        const value = lookup(i18nState.resources, key)
        return interpolate(typeof value === 'string' ? value : textFallback)
      }
      return key.startsWith('errors:') ? key : interpolate(textFallback)
    },
    i18n: {
      get language() {
        return i18nState.language
      },
    },
  }),
}))

const LOCALES = {
  zh: { common: zhCommon, dashboard: zhDashboard, errors: zhErrors },
  en: { common: enCommon, dashboard: enDashboard, errors: enErrors },
}

function renderModal(props: Partial<Parameters<typeof PaymentCheckoutModal>[0]> = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <PaymentCheckoutModal
          isOpen
          onClose={vi.fn()}
          initialCycle="month"
          monthlyPriceCents={1900}
          yearlyPriceCents={19000}
          {...props}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('PaymentCheckoutModal', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    i18nState.language = 'zh-CN'
    i18nState.resources = null
    vi.mocked(subscriptionApi.getCatalog).mockResolvedValue({
      tiers: [{
        name: 'pro',
        entitlements: {
          ai_conversations_per_day: -1,
          active_projects_limit: -1,
          material_decompositions_monthly: 5,
        },
      }],
    } as never)
    vi.mocked(paymentApi.getOptions).mockResolvedValue({ enabled: true, payment_methods: ['alipay'] })
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('offers only Alipay and creates the selected yearly order once', async () => {
    const submit = vi.spyOn(HTMLFormElement.prototype, 'submit').mockImplementation(() => undefined)
    vi.mocked(paymentApi.createOrder).mockResolvedValue({
      order: {
        id: 'order-1', out_trade_no: 'ZP1', trade_no: null, user_id: 'user-1', plan_name: 'pro',
        plan_display_name: 'Pro', cycle: 'year', amount_cents: 19000, payment_method: 'alipay', status: 'pending',
        fulfillment_status: 'pending', created_at: '2026-10-05T00:00:00Z', paid_at: null, fulfilled_at: null, failure_reason: null,
      },
      checkout: { action: 'https://zpayz.cn/submit.php', method: 'POST', fields: { pid: 'merchant', type: 'alipay', sign: 'signed' } },
    })

    renderModal()
    expect(await screen.findByText('支付宝')).toBeInTheDocument()
    expect(screen.getByText('开通 Pro')).toBeInTheDocument()
    expect(await screen.findByText('AI 消息不限条数 · 项目不限 · 每月 5 次素材拆解')).toBeInTheDocument()
    expect(screen.getByText('一次性付款，不会自动续费。到期前续费，时长会接在当前到期日之后。')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^去支付 ¥19$/ })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /年付/ })).toHaveTextContent('最划算 · 省 17%')
    expect(screen.getByRole('radio', { name: /年付/ })).toHaveTextContent('折合 ¥15.83/月')
    expect(screen.getByRole('radio', { name: /年付/ })).not.toHaveTextContent('比月付省')
    expect(screen.queryByText(/微信/)).not.toBeInTheDocument()
    expect(screen.queryByText(/服务器|收银台/)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('radio', { name: /年付/ }))
    fireEvent.click(screen.getByRole('button', { name: /去支付 ¥190/ }))

    await waitFor(() => expect(paymentApi.createOrder).toHaveBeenCalledTimes(1))
    expect(paymentApi.createOrder).toHaveBeenCalledWith({ plan_name: 'pro', cycle: 'year', payment_method: 'alipay' })
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1))
    const submittedForm = submit.mock.instances[0] as HTMLFormElement
    expect(submittedForm.method.toLowerCase()).toBe('post')
    expect(submittedForm.action).toBe('https://zpayz.cn/submit.php')
    expect(submittedForm.querySelector<HTMLInputElement>('input[name="type"]')?.value).toBe('alipay')
  })

  it.each([
    ['month' as const, { monthlyPriceCents: undefined, yearlyPriceCents: 19000 }],
    ['year' as const, { monthlyPriceCents: 1900, yearlyPriceCents: undefined }],
    ['month' as const, { monthlyPriceCents: 0, yearlyPriceCents: 19000 }],
    ['year' as const, { monthlyPriceCents: 1900, yearlyPriceCents: Number.NaN }],
  ])('does not create a %s order before a valid selected price is displayed', async (cycle, prices) => {
    renderModal({ initialCycle: cycle, ...prices })
    await screen.findByText('AI 消息不限条数 · 项目不限 · 每月 5 次素材拆解')
    const pay = screen.getByRole('button', { name: /^去支付/ })
    expect(pay).toBeDisabled()
    fireEvent.click(pay)
    expect(paymentApi.createOrder).not.toHaveBeenCalled()
  })

  it('does not submit a checkout response with an untrusted action', async () => {
    const submit = vi.spyOn(HTMLFormElement.prototype, 'submit').mockImplementation(() => undefined)
    vi.mocked(paymentApi.createOrder).mockResolvedValue({
      order: {
        id: 'order-2', out_trade_no: 'ZP2', trade_no: null, user_id: 'user-1', plan_name: 'pro',
        plan_display_name: 'Pro', cycle: 'month', amount_cents: 1900, payment_method: 'alipay', status: 'pending',
        fulfillment_status: 'pending', created_at: '2026-10-05T00:00:00Z', paid_at: null, fulfilled_at: null, failure_reason: null,
      },
      checkout: { action: 'https://evil.example/submit', method: 'POST', fields: { sign: 'stolen' } },
    })

    renderModal()
    const payButton = await screen.findByRole('button', { name: /去支付/ })
    await waitFor(() => expect(payButton).toBeEnabled())
    fireEvent.click(payButton)

    expect(await screen.findByText('无法跳转到支付宝，请刷新页面后重试')).toBeInTheDocument()
    expect(submit).not.toHaveBeenCalled()
  })

  it('disables checkout and points to the billing page support by default', async () => {
    vi.mocked(paymentApi.getOptions).mockResolvedValue({ enabled: false, payment_methods: [] })
    renderModal()
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('暂时无法在线支付，请稍后重试。如需帮助，可在「订阅权益」页查看客服联系方式。')
    expect(screen.getByRole('link', { name: '去订阅权益页' })).toHaveAttribute('href', '/dashboard/billing')
    expect(screen.getByRole('button', { name: /去支付/ })).toBeDisabled()
  })

  it('points to support on the billing page without a redemption prompt', async () => {
    vi.mocked(paymentApi.getOptions).mockResolvedValue({ enabled: false, payment_methods: [] })
    renderModal({ isRenewal: false, redeemEntry: 'on-page' })
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('暂时无法在线支付，请稍后重试。如需帮助，可在本页底部查看客服联系方式。')
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })

  it.each([
    ['billing-page' as PaymentRedeemEntry, '暂时无法在线支付，请稍后重试。如需帮助，可在「订阅权益」页查看客服联系方式。'],
    ['on-page' as PaymentRedeemEntry, '暂时无法在线支付，请稍后重试。如需帮助，可在本页底部查看客服联系方式。'],
  ])('names the %s support location when order creation reports payments are off', async (redeemEntry, text) => {
    vi.mocked(paymentApi.createOrder).mockRejectedValue(new ApiError(503, 'ERR_PAYMENT_UNAVAILABLE'))
    renderModal({ isRenewal: false, redeemEntry })
    const payButton = await screen.findByRole('button', { name: /去支付/ })
    await waitFor(() => expect(payButton).toBeEnabled())
    fireEvent.click(payButton)
    expect(await screen.findByRole('alert')).toHaveTextContent(text)
  })

  it('titles the checkout as a renewal for a Pro buyer', async () => {
    renderModal({ isRenewal: true })
    expect(await screen.findByText('续费 Pro')).toBeInTheDocument()
    expect(screen.queryByText('开通 Pro')).not.toBeInTheDocument()
  })

  it('locks checkout after redirecting, records the funnel, and unlocks on bfcache restore', async () => {
    const submit = vi.spyOn(HTMLFormElement.prototype, 'submit').mockImplementation(() => undefined)
    vi.mocked(paymentApi.createOrder).mockResolvedValue({
      order: {
        id: 'order-3', out_trade_no: 'ZP3', trade_no: null, user_id: 'user-1', plan_name: 'pro',
        plan_display_name: 'Pro', cycle: 'month', amount_cents: 1900, payment_method: 'alipay', status: 'pending',
        fulfillment_status: 'pending', created_at: '2026-10-05T00:00:00Z', paid_at: null, fulfilled_at: null, failure_reason: null,
      },
      checkout: { action: 'https://zpayz.cn/submit.php', method: 'POST', fields: { sign: 'signed' } },
    })

    renderModal({ upgradeSource: 'pricing_page_primary' })
    const payButton = await screen.findByRole('button', { name: /去支付/ })
    await waitFor(() => expect(payButton).toBeEnabled())
    fireEvent.click(payButton)

    expect(await screen.findByRole('button', { name: '正在前往支付宝…' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '取消' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '正在前往支付宝…' }))
    expect(paymentApi.createOrder).toHaveBeenCalledTimes(1)
    expect(paymentApi.createOrder).toHaveBeenCalledWith({
      plan_name: 'pro', cycle: 'month', payment_method: 'alipay', upgrade_source: 'pricing_page_primary',
    })
    expect(submit).toHaveBeenCalledTimes(1)
    expect(trackEvent).toHaveBeenCalledWith('checkout_started', {
      cycle: 'month',
      payment_method: 'alipay',
      provider: 'zpay',
      currency: 'CNY',
      upgrade_source: 'pricing_page_primary',
    })
    expect(trackEvent).toHaveBeenCalledWith('checkout_redirected', {
      cycle: 'month',
      out_trade_no: 'ZP3',
      payment_method: 'alipay',
      provider: 'zpay',
      currency: 'CNY',
      upgrade_source: 'pricing_page_primary',
    })

    act(() => {
      const restored = new Event('pageshow') as PageTransitionEvent
      Object.defineProperty(restored, 'persisted', { value: true })
      window.dispatchEvent(restored)
    })
    expect(await screen.findByRole('button', { name: /去支付/ })).toBeEnabled()
  })

  it('shows a translated message for a backend payment error code', async () => {
    vi.mocked(paymentApi.createOrder).mockRejectedValue(new ApiError(429, 'ERR_PAYMENT_RATE_LIMITED'))
    renderModal()
    const payButton = await screen.findByRole('button', { name: /去支付/ })
    await waitFor(() => expect(payButton).toBeEnabled())
    fireEvent.click(payButton)
    expect(await screen.findByText('errors:ERR_PAYMENT_RATE_LIMITED')).toBeInTheDocument()
    expect(trackEvent).toHaveBeenCalledWith('checkout_failed', expect.objectContaining({ error_code: 'ERR_PAYMENT_RATE_LIMITED' }))
  })

  it('falls back to a localized generic message for untranslated errors', async () => {
    vi.mocked(paymentApi.createOrder).mockRejectedValue(new ApiError(400, 'Some English backend detail'))
    renderModal()
    const payButton = await screen.findByRole('button', { name: /去支付/ })
    await waitFor(() => expect(payButton).toBeEnabled())
    fireEvent.click(payButton)
    expect(await screen.findByText('暂时无法创建订单，请稍后重试')).toBeInTheDocument()
    expect(screen.queryByText('Some English backend detail')).not.toBeInTheDocument()
  })

  describe.each([
    {
      lang: 'zh' as const,
      outdated: '页面已更新，请刷新页面后重试',
      rateLimited: '操作太频繁，请 1 分钟后再试',
      generic: '暂时无法创建订单，请稍后重试',
      planUnavailable: '该套餐暂时无法购买，请刷新页面后重试',
    },
    {
      lang: 'en' as const,
      outdated: 'This page has been updated. Refresh the page and try again.',
      rateLimited: 'Too many attempts. Please try again in 1 minute.',
      generic: 'Could not create the order right now. Please try again later.',
      planUnavailable: 'This plan cannot be purchased right now. Refresh the page and try again.',
    },
  ])('create-order errors in shipped $lang copy', ({ lang, outdated, rateLimited, generic, planUnavailable }) => {
    beforeEach(() => {
      i18nState.resources = LOCALES[lang]
    })

    async function failWith(cause: unknown) {
      vi.mocked(paymentApi.createOrder).mockRejectedValue(cause)
      renderModal()
      const payButton = await screen.findByRole('button', { name: /去支付|Pay / })
      await waitFor(() => expect(payButton).toBeEnabled())
      fireEvent.click(payButton)
      return screen.findByRole('alert')
    }

    it('asks for a reload when the API rejects the request body (page and API versions differ)', async () => {
      const alert = await failWith(new ApiError(422, 'ERR_VALIDATION_ERROR'))
      expect(alert).toHaveTextContent(outdated)
      expect(trackEvent).toHaveBeenCalledWith('checkout_failed', expect.objectContaining({ error_code: 'ERR_VALIDATION_ERROR' }))
    })

    it('tells the buyer when to retry after the per-minute limit', async () => {
      const alert = await failWith(new ApiError(429, 'ERR_PAYMENT_RATE_LIMITED'))
      expect(alert).toHaveTextContent(rateLimited)
    })

    it('asks for a reload when the plan or option is no longer offered', async () => {
      const alert = await failWith(new ApiError(400, 'ERR_PAYMENT_PLAN_UNAVAILABLE'))
      expect(alert).toHaveTextContent(planUnavailable)
    })

    it.each([
      ['a server error', new ApiError(500, 'ERR_INTERNAL_SERVER_ERROR')],
      ['a network failure', new TypeError('Failed to fetch')],
    ])('shows the generic retry message for %s', async (_label, cause) => {
      const alert = await failWith(cause)
      expect(alert).toHaveTextContent(generic)
      expect(trackEvent).toHaveBeenCalledWith('checkout_failed', expect.objectContaining({ error_code: 'unknown' }))
    })
  })

  describe('dual-currency Creem + Zpay checkout', () => {
    const assignSpy = vi.fn()

    function renderDualCurrencyModal(props: Partial<Parameters<typeof PaymentCheckoutModal>[0]> = {}) {
      return renderModal({
        monthlyPriceCents: 4900,
        yearlyPriceCents: 39900,
        monthlyUsdPriceCents: 999,
        yearlyUsdPriceCents: 7900,
        ...props,
      })
    }

    beforeEach(() => {
      assignSpy.mockReset()
      i18nState.language = 'en'
      vi.mocked(subscriptionApi.getCatalog).mockResolvedValue({
        tiers: [{ name: 'pro', price_monthly_usd_cents: 999, price_yearly_usd_cents: 7900, entitlements: {} }],
      } as never)
      vi.mocked(paymentApi.getOptions).mockResolvedValue({ enabled: true, payment_methods: ['alipay', 'card'] })
      vi.stubGlobal('location', { assign: assignSpy })
    })

    it('defaults to USD card in English, allows switching to Alipay CNY, and redirects to Creem GET checkout', async () => {
      vi.mocked(paymentApi.createOrder).mockResolvedValue({
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
      } as never)

      renderDualCurrencyModal()

      const payBtn = screen.getByRole('button', { name: /\$9\.99/ })
      await waitFor(() => expect(payBtn).toBeEnabled())

      fireEvent.click(screen.getByRole('radio', { name: /支付宝 \(CNY\)/ }))
      expect(screen.getByRole('button', { name: /¥49/ })).toBeEnabled()

      fireEvent.click(screen.getByRole('radio', { name: /银行卡 \(USD\)/ }))
      fireEvent.click(screen.getByRole('button', { name: /\$9\.99/ }))

      await waitFor(() => {
        expect(paymentApi.createOrder).toHaveBeenCalledWith({
          plan_name: 'pro',
          cycle: 'month',
          payment_method: 'card',
        })
      })
      expect(assignSpy).toHaveBeenCalledWith('https://creem.io/test/checkout/prod_123/ch_456')
    })

    it('rejects non-allowlisted redirect hosts on GET checkout', async () => {
      vi.mocked(paymentApi.createOrder).mockResolvedValue({
        order: { id: 'ord-2', out_trade_no: '202611240000002', cycle: 'month', payment_method: 'card' },
        checkout: { action: 'https://evil.example.com/phish', method: 'GET', fields: {} },
      } as never)

      renderDualCurrencyModal()
      const payBtn = screen.getByRole('button', { name: /\$9\.99/ })
      await waitFor(() => expect(payBtn).toBeEnabled())
      fireEvent.click(payBtn)

      expect(await screen.findByRole('alert')).toHaveTextContent('无法跳转到支付页面')
      expect(assignSpy).not.toHaveBeenCalled()
    })

    it('accepts checkout.creem.io as an allowlisted redirect host on GET checkout', async () => {
      vi.mocked(paymentApi.createOrder).mockResolvedValue({
        order: { id: 'ord-3', out_trade_no: '202611240000003', cycle: 'month', payment_method: 'card' },
        checkout: { action: 'https://checkout.creem.io/test/checkout/prod_123/ch_789', method: 'GET', fields: {} },
      } as never)

      renderDualCurrencyModal()
      const payBtn = screen.getByRole('button', { name: /\$9\.99/ })
      await waitFor(() => expect(payBtn).toBeEnabled())
      fireEvent.click(payBtn)

      await waitFor(() => {
        expect(assignSpy).toHaveBeenCalledWith('https://checkout.creem.io/test/checkout/prod_123/ch_789')
      })
    })

    it('defaults to Alipay CNY in Chinese when both methods are enabled and switches to USD card on selection', async () => {
      i18nState.language = 'zh-CN'
      renderDualCurrencyModal()
      const payBtn = screen.getByRole('button', { name: /¥49/ })
      await waitFor(() => expect(payBtn).toBeEnabled())
      expect(screen.getByRole('radio', { name: /年付/ })).toHaveTextContent('¥399')

      fireEvent.click(screen.getByRole('radio', { name: /银行卡 \(USD\)/ }))
      expect(screen.getByRole('button', { name: /\$9\.99/ })).toBeEnabled()
      expect(screen.getByRole('radio', { name: /年付/ })).toHaveTextContent('$79')
    })

    it('shows only Alipay CNY prices when only alipay is enabled in English', async () => {
      i18nState.language = 'en'
      vi.mocked(paymentApi.getOptions).mockResolvedValue({ enabled: true, payment_methods: ['alipay'] })
      renderDualCurrencyModal()
      const payBtn = await screen.findByRole('button', { name: /¥49/ })
      await waitFor(() => expect(payBtn).toBeEnabled())
      expect(screen.getByRole('radio', { name: /年付/ })).toHaveTextContent('¥399')
      expect(screen.queryByRole('radiogroup', { name: '支付方式' })).not.toBeInTheDocument()
    })

    it('shows only Card USD prices when only card is enabled in Chinese', async () => {
      i18nState.language = 'zh-CN'
      vi.mocked(paymentApi.getOptions).mockResolvedValue({ enabled: true, payment_methods: ['card'] })
      renderDualCurrencyModal()
      const payBtn = await screen.findByRole('button', { name: /\$9\.99/ })
      await waitFor(() => expect(payBtn).toBeEnabled())
      expect(screen.getByRole('radio', { name: /年付/ })).toHaveTextContent('$79')
      expect(screen.queryByRole('radiogroup', { name: '支付方式' })).not.toBeInTheDocument()
    })

    it('defaults to Card USD for non-Chinese non-English locales when both methods are enabled', async () => {
      i18nState.language = 'ja'
      renderDualCurrencyModal()
      const payBtn = await screen.findByRole('button', { name: /\$9\.99/ })
      await waitFor(() => expect(payBtn).toBeEnabled())
      expect(screen.getByRole('radio', { name: /年付/ })).toHaveTextContent('$79')
    })

    it('renders truthful Creem USD trust copy in English when card USD is selected and hides it for Alipay', async () => {
      i18nState.language = 'en'
      i18nState.resources = LOCALES.en
      renderDualCurrencyModal()

      const trustNote = await screen.findByTestId('payment-usd-trust-note')
      expect(trustNote).toHaveTextContent('Instant activation · Secure USD checkout via Creem')
      expect(await screen.findByRole('radio', { name: /Card \(USD\)/ })).toBeInTheDocument()

      fireEvent.click(screen.getByRole('radio', { name: /Alipay \(CNY\)/ }))
      expect(screen.queryByTestId('payment-usd-trust-note')).not.toBeInTheDocument()
    })
  })
})
