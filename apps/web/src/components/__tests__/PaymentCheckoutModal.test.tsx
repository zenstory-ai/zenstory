import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { PaymentCheckoutModal } from '../subscription/PaymentCheckoutModal'
import { paymentApi } from '../../lib/paymentApi'
import { ApiError } from '../../lib/apiClient'
import { trackEvent } from '../../lib/analytics'
import zhCommon from '../../../public/locales/zh/common.json'
import zhDashboard from '../../../public/locales/zh/dashboard.json'
import zhErrors from '../../../public/locales/zh/errors.json'
import enCommon from '../../../public/locales/en/common.json'
import enDashboard from '../../../public/locales/en/dashboard.json'
import enErrors from '../../../public/locales/en/errors.json'

vi.mock('../../lib/analytics', () => ({ trackEvent: vi.fn() }))

vi.mock('../../lib/paymentApi', async () => {
  const actual = await vi.importActual<typeof import('../../lib/paymentApi')>('../../lib/paymentApi')
  return {
    ...actual,
    paymentApi: {
      getOptions: vi.fn(),
      createOrder: vi.fn(),
    },
  }
})

// When `resources` is set, `t` resolves keys against the shipped locale files.
const i18nState = vi.hoisted(() => ({ resources: null as Record<string, unknown> | null }))

function lookup(resources: Record<string, unknown>, key: string): unknown {
  const [namespace, path] = key.split(':')
  return path.split('.').reduce<unknown>(
    (node, part) => (node && typeof node === 'object' ? (node as Record<string, unknown>)[part] : undefined),
    resources[namespace],
  )
}

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback: string) => {
      if (i18nState.resources) {
        const value = lookup(i18nState.resources, key)
        return typeof value === 'string' ? value : fallback
      }
      return key.startsWith('errors:') ? key : fallback
    },
    i18n: { language: 'zh-CN' },
  }),
}))

const LOCALES = {
  zh: { common: zhCommon, dashboard: zhDashboard, errors: zhErrors },
  en: { common: enCommon, dashboard: enDashboard, errors: enErrors },
}

function renderModal(initialCycle: 'month' | 'year' = 'month', upgradeSource?: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <PaymentCheckoutModal
        isOpen
        onClose={vi.fn()}
        initialCycle={initialCycle}
        monthlyPriceCents={1900}
        yearlyPriceCents={19000}
        upgradeSource={upgradeSource}
      />
    </QueryClientProvider>
  )
}

describe('PaymentCheckoutModal', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    i18nState.resources = null
    vi.mocked(paymentApi.getOptions).mockResolvedValue({ enabled: true, payment_methods: ['alipay'] })
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
    expect(screen.getByText('开通 Pro 会员')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^去支付$/ })).toBeInTheDocument()
    expect(screen.queryByText(/微信/)).not.toBeInTheDocument()
    expect(screen.queryByText(/服务器|收银台/)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('radio', { name: /年付/ }))
    fireEvent.click(screen.getByRole('button', { name: /去支付/ }))

    await waitFor(() => expect(paymentApi.createOrder).toHaveBeenCalledTimes(1))
    expect(paymentApi.createOrder).toHaveBeenCalledWith({ plan_name: 'pro', cycle: 'year', payment_method: 'alipay' })
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1))
    const submittedForm = submit.mock.instances[0] as HTMLFormElement
    expect(submittedForm.method.toLowerCase()).toBe('post')
    expect(submittedForm.action).toBe('https://zpayz.cn/submit.php')
    expect(submittedForm.querySelector<HTMLInputElement>('input[name="type"]')?.value).toBe('alipay')
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

  it('disables checkout when server options are disabled', async () => {
    vi.mocked(paymentApi.getOptions).mockResolvedValue({ enabled: false, payment_methods: [] })
    renderModal()
    expect(await screen.findByText('暂时无法在线支付。有兑换码的话，可在「订阅权益」页点「兑换码」开通。')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /去支付/ })).toBeDisabled()
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

    renderModal('month', 'pricing_page_primary')
    const payButton = await screen.findByRole('button', { name: /去支付/ })
    await waitFor(() => expect(payButton).toBeEnabled())
    fireEvent.click(payButton)

    expect(await screen.findByRole('button', { name: '正在前往支付宝...' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '取消' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '正在前往支付宝...' }))
    expect(paymentApi.createOrder).toHaveBeenCalledTimes(1)
    expect(paymentApi.createOrder).toHaveBeenCalledWith({
      plan_name: 'pro', cycle: 'month', payment_method: 'alipay', upgrade_source: 'pricing_page_primary',
    })
    expect(submit).toHaveBeenCalledTimes(1)
    expect(trackEvent).toHaveBeenCalledWith('checkout_started', { cycle: 'month', upgrade_source: 'pricing_page_primary' })
    expect(trackEvent).toHaveBeenCalledWith('checkout_redirected', {
      cycle: 'month', out_trade_no: 'ZP3', upgrade_source: 'pricing_page_primary',
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
      const payButton = await screen.findByRole('button', { name: /去支付|Pay now/ })
      await waitFor(() => expect(payButton).toBeEnabled())
      fireEvent.click(payButton)
      return screen.findByRole('alert')
    }

    it('asks for a reload when the API rejects the request body (page and API versions differ)', async () => {
      // e.g. an older API answering 422 extra_forbidden for a field a newer page sends
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
})
