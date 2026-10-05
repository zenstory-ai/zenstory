import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { PaymentCheckoutModal } from '../subscription/PaymentCheckoutModal'
import { paymentApi } from '../../lib/paymentApi'
import { ApiError } from '../../lib/apiClient'
import { trackEvent } from '../../lib/analytics'

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

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback: string, options?: { price?: string }) =>
      key.startsWith('errors:')
        ? key
        : options?.price !== undefined ? fallback.replace('{{price}}', options.price) : fallback,
    i18n: { language: 'zh-CN' },
  }),
}))

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
    expect(screen.queryByText(/微信/)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('radio', { name: /年付/ }))
    fireEvent.click(screen.getByRole('button', { name: /支付宝支付/ }))

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
    const payButton = await screen.findByRole('button', { name: /支付宝支付/ })
    await waitFor(() => expect(payButton).toBeEnabled())
    fireEvent.click(payButton)

    expect(await screen.findByText('支付跳转校验失败，请重试或联系支持')).toBeInTheDocument()
    expect(submit).not.toHaveBeenCalled()
  })

  it('disables checkout when server options are disabled', async () => {
    vi.mocked(paymentApi.getOptions).mockResolvedValue({ enabled: false, payment_methods: [] })
    renderModal()
    expect(await screen.findByText('在线支付暂未开放，你仍可使用兑换码开通。')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /支付宝支付/ })).toBeDisabled()
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
    const payButton = await screen.findByRole('button', { name: /支付宝支付/ })
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
    expect(await screen.findByRole('button', { name: /支付宝支付/ })).toBeEnabled()
  })

  it('shows a translated message for a backend payment error code', async () => {
    vi.mocked(paymentApi.createOrder).mockRejectedValue(new ApiError(429, 'ERR_PAYMENT_RATE_LIMITED'))
    renderModal()
    const payButton = await screen.findByRole('button', { name: /支付宝支付/ })
    await waitFor(() => expect(payButton).toBeEnabled())
    fireEvent.click(payButton)
    expect(await screen.findByText('errors:ERR_PAYMENT_RATE_LIMITED')).toBeInTheDocument()
    expect(trackEvent).toHaveBeenCalledWith('checkout_failed', expect.objectContaining({ error_code: 'ERR_PAYMENT_RATE_LIMITED' }))
  })

  it('falls back to a localized generic message for untranslated errors', async () => {
    vi.mocked(paymentApi.createOrder).mockRejectedValue(new ApiError(400, 'Some English backend detail'))
    renderModal()
    const payButton = await screen.findByRole('button', { name: /支付宝支付/ })
    await waitFor(() => expect(payButton).toBeEnabled())
    fireEvent.click(payButton)
    expect(await screen.findByText('创建支付订单失败，请重试')).toBeInTheDocument()
    expect(screen.queryByText('Some English backend detail')).not.toBeInTheDocument()
  })
})
