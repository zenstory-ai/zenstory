import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import PaymentReturnPage from '../PaymentReturnPage'
import { paymentApi } from '../../lib/paymentApi'

vi.mock('../../lib/paymentApi', async () => {
  const actual = await vi.importActual<typeof import('../../lib/paymentApi')>('../../lib/paymentApi')
  return { ...actual, paymentApi: { getOrder: vi.fn() } }
})

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
})
