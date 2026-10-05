import { useCallback, useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { CheckCircle2, Clock3, RefreshCw, TriangleAlert } from 'lucide-react'
import { Button } from '../components/ui/Button'
import { Card } from '../components/ui/Card'
import { paymentApi, paymentQueryKeys } from '../lib/paymentApi'
import { subscriptionQueryKeys } from '../lib/subscriptionApi'
import { trackEvent } from '../lib/analytics'
import type { PaymentOrder } from '../types/payment'

// Zpay's notify often lands well after the browser returns (desktop QR flow), so
// the page keeps checking for two minutes from arrival, backing off as it goes.
const PAYMENT_POLL_WINDOW_MS = 120_000

function paymentPollDelay(elapsedMs: number): number | false {
  if (elapsedMs >= PAYMENT_POLL_WINDOW_MS) return false
  if (elapsedMs < 20_000) return 2_000
  if (elapsedMs < 60_000) return 4_000
  return 8_000
}

type ReturnResult = 'succeeded' | 'failed' | 'pending_timeout'

function isFulfilled(order: PaymentOrder | undefined): boolean {
  return order?.status === 'paid' && order.fulfillment_status === 'succeeded'
}

export default function PaymentReturnPage() {
  const { t } = useTranslation(['dashboard', 'common'])
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const invalidatedOrder = useRef<string | null>(null)
  const trackedResults = useRef(new Set<ReturnResult>())
  const syncRequested = useRef(false)
  const [pollStartedAt, setPollStartedAt] = useState(() => Date.now())
  const [timedOut, setTimedOut] = useState(false)
  const outTradeNo = searchParams.get('out_trade_no')?.trim() ?? ''

  const orderQuery = useQuery({
    queryKey: paymentQueryKeys.order(outTradeNo),
    queryFn: () => paymentApi.getOrder(outTradeNo),
    enabled: outTradeNo.length > 0,
    retry: false,
    refetchInterval: (query) => {
      const current = query.state.data
      if (!current || isFulfilled(current)) return false
      // Keep checking failed orders too: Zpay retries notify and a retry may succeed.
      return paymentPollDelay(Date.now() - pollStartedAt)
    },
  })

  const order = orderQuery.data
  const isSucceeded = isFulfilled(order)
  const isFailed = !isSucceeded && order?.fulfillment_status === 'failed'

  useEffect(() => {
    if (!order || isSucceeded) return
    const remaining = PAYMENT_POLL_WINDOW_MS - (Date.now() - pollStartedAt)
    const timer = window.setTimeout(() => setTimedOut(true), Math.max(0, remaining))
    return () => window.clearTimeout(timer)
  }, [order, isSucceeded, pollStartedAt])

  const trackResult = useCallback((result: ReturnResult, current: PaymentOrder) => {
    if (trackedResults.current.has(result)) return
    trackedResults.current.add(result)
    trackEvent('payment_return_result', {
      result,
      out_trade_no: current.out_trade_no,
      cycle: current.cycle,
      plan_name: current.plan_name,
    })
  }, [])

  useEffect(() => {
    if (!order) return
    if (isSucceeded) trackResult('succeeded', order)
    else if (isFailed) trackResult('failed', order)
  }, [isFailed, isSucceeded, order, trackResult])

  // Still unconfirmed after the window: ask the server to query Zpay once.
  useEffect(() => {
    if (!timedOut || !order || isSucceeded || syncRequested.current) return
    syncRequested.current = true
    trackResult('pending_timeout', order)
    paymentApi
      .syncOrder(order.out_trade_no)
      .then((synced) => {
        queryClient.setQueryData(paymentQueryKeys.order(order.out_trade_no), synced)
      })
      .catch(() => {
        // The order is re-read on refresh; a failed query must not alarm the buyer.
      })
  }, [isSucceeded, order, queryClient, timedOut, trackResult])

  useEffect(() => {
    if (!isSucceeded || !order || invalidatedOrder.current === order.out_trade_no) return
    invalidatedOrder.current = order.out_trade_no
    void queryClient.invalidateQueries({ queryKey: subscriptionQueryKeys.status() })
    void queryClient.invalidateQueries({ queryKey: subscriptionQueryKeys.quota() })
    void queryClient.invalidateQueries({ queryKey: ['subscription-history'] })
  }, [isSucceeded, order, queryClient])

  const refreshAndResumePolling = () => {
    setPollStartedAt(Date.now())
    setTimedOut(false)
    void orderQuery.refetch()
  }

  if (!outTradeNo) {
    return (
      <Card variant="outlined" padding="lg" className="mx-auto max-w-xl">
        <ResultHeader icon={<TriangleAlert className="h-10 w-10 text-[hsl(var(--error))]" />} title={t('dashboard:billing.paymentReturnInvalid', '无法确认支付订单')} />
        <p className="mt-3 text-sm text-[hsl(var(--text-secondary))]">
          {t('dashboard:billing.paymentReturnMissingOrder', '未找到订单信息，请返回订阅页查看。')}
        </p>
        <ReturnActions onBilling={() => navigate('/dashboard/billing')} />
      </Card>
    )
  }

  if (orderQuery.isLoading) {
    return (
      <Card variant="outlined" padding="lg" className="mx-auto max-w-xl">
        <ResultHeader icon={<RefreshCw className="h-10 w-10 animate-spin text-[hsl(var(--accent-primary))]" />} title={t('dashboard:billing.paymentChecking', '正在确认支付结果...')} />
      </Card>
    )
  }

  if (orderQuery.isError || !order) {
    return (
      <Card variant="outlined" padding="lg" className="mx-auto max-w-xl">
        <ResultHeader icon={<TriangleAlert className="h-10 w-10 text-[hsl(var(--error))]" />} title={t('dashboard:billing.paymentCheckFailed', '订单查询失败')} />
        <p className="mt-3 text-sm text-[hsl(var(--text-secondary))]">
          {t('dashboard:billing.paymentCheckFailedHint', '请稍后重试。若已完成付款，请勿重复支付。')}
        </p>
        <ReturnActions onRefresh={refreshAndResumePolling} onBilling={() => navigate('/dashboard/billing')} />
      </Card>
    )
  }

  return (
    <Card variant="outlined" padding="lg" className="mx-auto max-w-xl">
      {isSucceeded ? (
        <>
          <ResultHeader icon={<CheckCircle2 className="h-10 w-10 text-[hsl(var(--success))]" />} title={t('dashboard:billing.paymentActivated', '支付成功，Pro 已开通')} />
          <p className="mt-3 text-sm text-[hsl(var(--text-secondary))]">
            {t('dashboard:billing.paymentActivatedHint', '可返回订阅页面查看最新配额。')}
          </p>
        </>
      ) : isFailed ? (
        <>
          <ResultHeader icon={<TriangleAlert className="h-10 w-10 text-[hsl(var(--error))]" />} title={t('dashboard:billing.paymentFulfillmentRetrying', '支付已确认，会员暂未开通')} />
          <p className="mt-3 text-sm text-[hsl(var(--text-secondary))]">
            {t('dashboard:billing.paymentFulfillmentRetryingHint', '请勿重复付款，可稍后点击刷新；如长时间未开通，请联系客服并提供下方订单号。')}
          </p>
        </>
      ) : (
        <>
          <ResultHeader icon={<Clock3 className="h-10 w-10 text-[hsl(var(--warning))]" />} title={t('dashboard:billing.paymentPending', '支付结果处理中')} />
          <p className="mt-3 text-sm text-[hsl(var(--text-secondary))]" role="status">
            {timedOut
              ? t('dashboard:billing.paymentPendingTimeout', '暂未确认到支付结果。若已付款，请勿重复支付，可稍后点击刷新；长时间未开通请联系客服并提供订单号。')
              : t('dashboard:billing.paymentPendingHint', '页面会自动刷新。若已付款，请勿重复支付。')}
          </p>
        </>
      )}

      <div className="mt-4 rounded-lg bg-[hsl(var(--bg-tertiary))] p-3 text-sm">
        <span className="text-[hsl(var(--text-secondary))]">{t('dashboard:billing.paymentOrderNumber', '订单号')}</span>
        <p className="mt-1 break-all font-mono text-[hsl(var(--text-primary))]">{order.out_trade_no}</p>
      </div>

      <ReturnActions
        onRefresh={isSucceeded ? undefined : refreshAndResumePolling}
        onBilling={() => navigate('/dashboard/billing')}
      />
    </Card>
  )
}

function ResultHeader({ icon, title }: { icon: React.ReactNode; title: string }) {
  return (
    <div className="flex flex-col items-center text-center">
      {icon}
      <h1 className="mt-3 text-xl font-semibold text-[hsl(var(--text-primary))]">{title}</h1>
    </div>
  )
}

function ReturnActions({ onRefresh, onBilling }: { onRefresh?: () => void; onBilling: () => void }) {
  const { t } = useTranslation(['dashboard', 'common'])
  return (
    <div className="mt-5 flex justify-center gap-3">
      {onRefresh && (
        <Button variant="secondary" onClick={onRefresh}>
          {t('dashboard:billing.paymentRefresh', '刷新支付结果')}
        </Button>
      )}
      <Button onClick={onBilling}>{t('dashboard:billing.backToBilling', '返回订阅与权益')}</Button>
    </div>
  )
}
