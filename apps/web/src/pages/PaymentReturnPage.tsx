import { useCallback, useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Check, Clock3, Crown, RefreshCw, TriangleAlert } from 'lucide-react'
import { Button } from '../components/ui/Button'
import { Card } from '../components/ui/Card'
import { paymentApi, paymentQueryKeys } from '../lib/paymentApi'
import { subscriptionApi, subscriptionQueryKeys } from '../lib/subscriptionApi'
import { getSubscriptionFeatureRows } from '../lib/subscriptionEntitlements'
import { CelebrationBurst } from '../components/subscription/CelebrationBurst'
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
  const { t, i18n } = useTranslation(['dashboard', 'settings', 'common'])
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

  // Read once Pro is active so the success card can show what was unlocked.
  const statusQuery = useQuery({
    queryKey: subscriptionQueryKeys.status(),
    queryFn: () => subscriptionApi.getStatus(),
    enabled: isSucceeded,
  })
  const activeStatus = isSucceeded && statusQuery.data?.tier !== 'free' ? statusQuery.data : undefined
  const unlockedRows = getSubscriptionFeatureRows(activeStatus?.features, t, i18n.language).slice(0, 4)
  const validUntil = activeStatus?.current_period_end
    ? new Date(activeStatus.current_period_end).toLocaleDateString(
        i18n.language?.startsWith('en') ? 'en-US' : 'zh-CN',
        { year: 'numeric', month: 'long', day: 'numeric' },
      )
    : null
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
        <ResultHeader icon={<TriangleAlert className="h-10 w-10 text-[hsl(var(--error))]" />} title={t('dashboard:billing.paymentReturnInvalid', '没有找到这笔订单')} />
        <p className="mt-3 text-sm text-[hsl(var(--text-secondary))]">
          {t('dashboard:billing.paymentReturnMissingOrder', '回到「订阅权益」查看 Pro 是否已开通。')}
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
        <ResultHeader icon={<TriangleAlert className="h-10 w-10 text-[hsl(var(--error))]" />} title={t('dashboard:billing.paymentCheckFailed', '暂时查不到支付结果')} />
        <p className="mt-3 text-sm text-[hsl(var(--text-secondary))]">
          {t('dashboard:billing.paymentCheckFailedHint', '已付款请不要重复支付，稍后点「刷新支付结果」。')}
        </p>
        <ReturnActions onRefresh={refreshAndResumePolling} onBilling={() => navigate('/dashboard/billing')} />
      </Card>
    )
  }

  return (
    <Card variant="outlined" padding="lg" className="relative mx-auto max-w-xl overflow-hidden">
      {isSucceeded ? (
        <>
          <CelebrationBurst />
          <ResultHeader
            icon={
              <span className="animate-celebration-pop flex h-14 w-14 items-center justify-center rounded-full bg-[hsl(var(--accent-primary)/0.12)]">
                <Crown className="h-8 w-8 text-[hsl(var(--accent-primary))]" />
              </span>
            }
            title={t('dashboard:billing.paymentActivated', '支付成功，Pro 已开通')}
          />
          <p className="mt-3 text-center text-sm text-[hsl(var(--text-secondary))]" role="status">
            {validUntil
              ? t('dashboard:billing.paymentActivatedUntil', '有效期至 {{date}}，现在就去写吧。', { date: validUntil })
              : t('dashboard:billing.paymentActivatedHint', '新额度已经生效，现在就去写吧。')}
          </p>
          {unlockedRows.length > 0 && (
            <ul className="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-2" aria-label={t('dashboard:billing.paymentUnlockedTitle', '已解锁')}>
              {unlockedRows.map((row) => (
                <li
                  key={row.key}
                  className="flex items-center justify-between gap-2 rounded-lg bg-[hsl(var(--accent-primary)/0.06)] px-3 py-2 text-sm"
                >
                  <span className="flex items-center gap-1.5 text-[hsl(var(--text-secondary))]">
                    <Check className="h-3.5 w-3.5 text-[hsl(var(--success))]" />
                    {row.label}
                  </span>
                  <span className="font-medium text-[hsl(var(--text-primary))]">{row.value}</span>
                </li>
              ))}
            </ul>
          )}
        </>
      ) : isFailed ? (
        <>
          <ResultHeader icon={<TriangleAlert className="h-10 w-10 text-[hsl(var(--error))]" />} title={t('dashboard:billing.paymentFulfillmentRetrying', '付款已确认，Pro 还没开通')} />
          <p className="mt-3 text-sm text-[hsl(var(--text-secondary))]">
            {t('dashboard:billing.paymentFulfillmentRetryingHint', '请不要重复付款。稍后点「刷新支付结果」；如果一直没有开通，请发邮件到 support@zenstory.ai 并附上下方订单号。')}
          </p>
        </>
      ) : (
        <>
          <ResultHeader icon={<Clock3 className="h-10 w-10 text-[hsl(var(--warning))]" />} title={t('dashboard:billing.paymentPending', '付款确认中')} />
          <p className="mt-3 text-sm text-[hsl(var(--text-secondary))]" role="status">
            {timedOut
              ? t('dashboard:billing.paymentPendingTimeout', '还没有确认到付款。已付款请不要重复支付，稍后点「刷新支付结果」；如果一直没有开通，请发邮件到 support@zenstory.ai 并附上订单号。')
              : t('dashboard:billing.paymentPendingHint', '本页会自动更新。已付款请不要重复支付。')}
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
        onStartWriting={isSucceeded ? () => navigate('/dashboard') : undefined}
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

function ReturnActions({
  onRefresh,
  onBilling,
  onStartWriting,
}: {
  onRefresh?: () => void
  onBilling: () => void
  onStartWriting?: () => void
}) {
  const { t } = useTranslation(['dashboard', 'common'])
  return (
    <div className="mt-5 flex flex-wrap justify-center gap-3">
      {onRefresh && (
        <Button variant="secondary" onClick={onRefresh}>
          {t('dashboard:billing.paymentRefresh', '刷新支付结果')}
        </Button>
      )}
      {onStartWriting ? (
        <>
          <Button variant="secondary" onClick={onBilling}>
            {t('dashboard:billing.backToBilling', '返回订阅与权益')}
          </Button>
          <Button onClick={onStartWriting}>{t('dashboard:billing.startWriting', '开始写作')}</Button>
        </>
      ) : (
        <Button onClick={onBilling}>{t('dashboard:billing.backToBilling', '返回订阅与权益')}</Button>
      )}
    </div>
  )
}
