import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import PaymentReturnPage from '../PaymentReturnPage'
import { paymentApi } from '../../lib/paymentApi'
import { trackEvent } from '../../lib/analytics'
import type { PaymentOrder } from '../../types/payment'

vi.mock('../../lib/paymentApi', async () => {
  const actual = await vi.importActual<typeof import('../../lib/paymentApi')>('../../lib/paymentApi')
  return { ...actual, paymentApi: { getOrder: vi.fn(), syncOrder: vi.fn() } }
})

vi.mock('../../lib/analytics', () => ({ trackEvent: vi.fn() }))

const pendingOrder: PaymentOrder = {
  id: 'order-9', out_trade_no: 'ZP9', trade_no: null, user_id: 'user-1', plan_name: 'pro',
  plan_display_name: 'Pro', cycle: 'month', amount_cents: 1900, payment_method: 'alipay', status: 'pending',
  fulfillment_status: 'pending', created_at: '2026-10-05T00:00:00Z', paid_at: null, fulfilled_at: null, failure_reason: null,
}

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_key: string, fallback: string) => fallback }),
}))

function renderPage(search: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/dashboard/billing/payment-return${search}`]}>
        <Routes><Route path="/dashboard/billing/payment-return" element={<PaymentReturnPage />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
  return invalidate
}

describe('PaymentReturnPage', () => {
  beforeEach(() => vi.clearAllMocks())
  afterEach(() => vi.useRealTimers())

  it('does not trust forged success query parameters when the server order is pending', async () => {
    vi.mocked(paymentApi.getOrder).mockResolvedValue({
      id: 'order-1', out_trade_no: 'ZP1', trade_no: null, user_id: 'user-1', plan_name: 'pro',
      plan_display_name: 'Pro', cycle: 'month', amount_cents: 1900, payment_method: 'alipay', status: 'pending',
      fulfillment_status: 'pending', created_at: '2026-10-05T00:00:00Z', paid_at: null, fulfilled_at: null, failure_reason: null,
    })
    const invalidate = renderPage('?out_trade_no=ZP1&trade_status=TRADE_SUCCESS&money=0.01')
    expect(await screen.findByText('支付结果处理中')).toBeInTheDocument()
    expect(screen.queryByText('支付成功，Pro 已开通')).not.toBeInTheDocument()
    expect(invalidate).not.toHaveBeenCalled()
  })

  it('shows success and invalidates subscription data only for fulfilled paid orders', async () => {
    vi.mocked(paymentApi.getOrder).mockResolvedValue({
      id: 'order-2', out_trade_no: 'ZP2', trade_no: 'trade-2', user_id: 'user-1', plan_name: 'pro',
      plan_display_name: 'Pro', cycle: 'year', amount_cents: 19000, payment_method: 'alipay', status: 'paid',
      fulfillment_status: 'succeeded', created_at: '2026-10-05T00:00:00Z', paid_at: '2026-10-05T00:01:00Z', fulfilled_at: '2026-10-05T00:01:00Z', failure_reason: null,
    })
    const invalidate = renderPage('?out_trade_no=ZP2')
    expect(await screen.findByText('支付成功，Pro 已开通')).toBeInTheDocument()
    await waitFor(() => expect(invalidate).toHaveBeenCalledTimes(3))
  })

  it('rejects a return without an order number without calling the API', () => {
    renderPage('?trade_status=TRADE_SUCCESS')
    expect(screen.getByText('无法确认支付订单')).toBeInTheDocument()
    expect(paymentApi.getOrder).not.toHaveBeenCalled()
  })

  it('keeps polling for two minutes, then warns against paying twice and queries Zpay once', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false })
    vi.mocked(paymentApi.getOrder).mockResolvedValue(pendingOrder)
    vi.mocked(paymentApi.syncOrder).mockResolvedValue(pendingOrder)
    renderPage('?out_trade_no=ZP9')
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(screen.getByText('支付结果处理中')).toBeInTheDocument()

    // The old counter-based poll stopped after ~8 seconds; 60 seconds in we must still poll.
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000) })
    const callsAtOneMinute = vi.mocked(paymentApi.getOrder).mock.calls.length
    expect(callsAtOneMinute).toBeGreaterThan(10)
    expect(paymentApi.syncOrder).not.toHaveBeenCalled()

    await act(async () => { await vi.advanceTimersByTimeAsync(61_000) })
    expect(screen.getByText(/请勿重复支付：我们已向支付平台查询该订单/)).toBeInTheDocument()
    expect(paymentApi.syncOrder).toHaveBeenCalledTimes(1)
    expect(paymentApi.syncOrder).toHaveBeenCalledWith('ZP9')
    expect(trackEvent).toHaveBeenCalledWith('payment_return_result', expect.objectContaining({
      result: 'pending_timeout', out_trade_no: 'ZP9',
    }))

    // Polling has stopped...
    const callsAtTimeout = vi.mocked(paymentApi.getOrder).mock.calls.length
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000) })
    expect(vi.mocked(paymentApi.getOrder).mock.calls.length).toBe(callsAtTimeout)
    expect(paymentApi.syncOrder).toHaveBeenCalledTimes(1)

    // ...and a manual refresh restarts the window.
    fireEvent.click(screen.getByRole('button', { name: '刷新支付结果' }))
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000) })
    expect(vi.mocked(paymentApi.getOrder).mock.calls.length).toBeGreaterThan(callsAtTimeout + 2)
    expect(screen.queryByText(/请勿重复支付：我们已向支付平台查询该订单/)).not.toBeInTheDocument()
  })

  it('lets a failed fulfillment refresh into success after Zpay retries', async () => {
    vi.mocked(paymentApi.getOrder).mockResolvedValueOnce({
      ...pendingOrder, status: 'paid', trade_no: 'trade-9', fulfillment_status: 'failed',
      failure_reason: 'subscription_fulfillment_failed',
    }).mockResolvedValue({
      ...pendingOrder, status: 'paid', trade_no: 'trade-9', fulfillment_status: 'succeeded',
    })
    renderPage('?out_trade_no=ZP9')
    expect(await screen.findByText('支付已确认，正在重试开通会员')).toBeInTheDocument()
    expect(trackEvent).toHaveBeenCalledWith('payment_return_result', expect.objectContaining({ result: 'failed' }))
    fireEvent.click(screen.getByRole('button', { name: '刷新支付结果' }))
    expect(await screen.findByText('支付成功，Pro 已开通')).toBeInTheDocument()
    expect(trackEvent).toHaveBeenCalledWith('payment_return_result', expect.objectContaining({ result: 'succeeded' }))
  })

  it('renders an order state it does not know without crashing', async () => {
    vi.mocked(paymentApi.getOrder).mockResolvedValue({ ...pendingOrder, status: 'refunded', fulfillment_status: 'reverted' })
    renderPage('?out_trade_no=ZP9')
    expect(await screen.findByText('ZP9')).toBeInTheDocument()
  })
})
