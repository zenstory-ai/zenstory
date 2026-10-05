import { useEffect, useRef } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { CheckCircle2, Clock3, RefreshCw, TriangleAlert } from 'lucide-react'
import { Button } from '../components/ui/Button'
import { Card } from '../components/ui/Card'
import { paymentApi, paymentQueryKeys } from '../lib/paymentApi'
import { subscriptionQueryKeys } from '../lib/subscriptionApi'

const POLL_INTERVAL_MS = 2000
const MAX_POLL_ATTEMPTS = 8

export default function PaymentReturnPage() {
  const { t } = useTranslation(['dashboard', 'common'])
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const pollAttempts = useRef(0)
  const invalidatedOrder = useRef<string | null>(null)
  const outTradeNo = searchParams.get('out_trade_no')?.trim() ?? ''

  const orderQuery = useQuery({
    queryKey: paymentQueryKeys.order(outTradeNo),
    queryFn: () => paymentApi.getOrder(outTradeNo),
    enabled: outTradeNo.length > 0,
    retry: false,
    refetchInterval: (query) => {
      const order = query.state.data
      if (!order || order.status !== 'pending' || pollAttempts.current >= MAX_POLL_ATTEMPTS) {
        return false
      }
      pollAttempts.current += 1
      return POLL_INTERVAL_MS
    },
  })

  const order = orderQuery.data
  const isSucceeded = order?.status === 'paid' && order.fulfillment_status === 'succeeded'
  const isFailed = order?.fulfillment_status === 'failed'

  useEffect(() => {
    if (!isSucceeded || !order || invalidatedOrder.current === order.out_trade_no) return
    invalidatedOrder.current = order.out_trade_no
    void queryClient.invalidateQueries({ queryKey: subscriptionQueryKeys.status() })
    void queryClient.invalidateQueries({ queryKey: subscriptionQueryKeys.quota() })
    void queryClient.invalidateQueries({ queryKey: ['subscription-history'] })
  }, [isSucceeded, order, queryClient])

  if (!outTradeNo) {
    return (
      <Card variant="outlined" padding="lg" className="mx-auto max-w-xl">
        <ResultHeader icon={<TriangleAlert className="h-10 w-10 text-[hsl(var(--error))]" />} title={t('dashboard:billing.paymentReturnInvalid', '无法确认支付订单')} />
        <p className="mt-3 text-sm text-[hsl(var(--text-secondary))]">
          {t('dashboard:billing.paymentReturnMissingOrder', '返回链接缺少订单号。为保障账户安全，我们不会根据第三方页面参数直接开通会员。')}
        </p>
        <ReturnActions onBilling={() => navigate('/dashboard/billing')} />
      </Card>
    )
  }

  if (orderQuery.isLoading) {
    return (
      <Card variant="outlined" padding="lg" className="mx-auto max-w-xl">
        <ResultHeader icon={<RefreshCw className="h-10 w-10 animate-spin text-[hsl(var(--accent-primary))]" />} title={t('dashboard:billing.paymentChecking', '正在向服务器确认支付结果...')} />
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
        <ReturnActions onRefresh={() => void orderQuery.refetch()} onBilling={() => navigate('/dashboard/billing')} />
      </Card>
    )
  }

  return (
    <Card variant="outlined" padding="lg" className="mx-auto max-w-xl">
      {isSucceeded ? (
        <>
          <ResultHeader icon={<CheckCircle2 className="h-10 w-10 text-[hsl(var(--success))]" />} title={t('dashboard:billing.paymentActivated', '支付成功，Pro 已开通')} />
          <p className="mt-3 text-sm text-[hsl(var(--text-secondary))]">
            {t('dashboard:billing.paymentActivatedHint', '会员权益已经到账，可返回订阅页面查看最新配额。')}
          </p>
        </>
      ) : isFailed ? (
        <>
          <ResultHeader icon={<TriangleAlert className="h-10 w-10 text-[hsl(var(--error))]" />} title={t('dashboard:billing.paymentFulfillmentFailed', '支付已确认，但会员开通异常')} />
          <p className="mt-3 text-sm text-[hsl(var(--text-secondary))]">
            {t('dashboard:billing.paymentContactSupport', '请联系支持并提供下方订单号，我们不会要求你重复付款。')}
          </p>
        </>
      ) : (
        <>
          <ResultHeader icon={<Clock3 className="h-10 w-10 text-[hsl(var(--warning))]" />} title={t('dashboard:billing.paymentPending', '支付结果处理中')} />
          <p className="mt-3 text-sm text-[hsl(var(--text-secondary))]">
            {t('dashboard:billing.paymentPendingHint', '服务器尚未确认到账。页面会短暂自动刷新，你也可以稍后返回订阅页面查看。')}
          </p>
        </>
      )}

      <div className="mt-4 rounded-lg bg-[hsl(var(--bg-tertiary))] p-3 text-sm">
        <span className="text-[hsl(var(--text-secondary))]">{t('dashboard:billing.paymentOrderNumber', '订单号')}</span>
        <p className="mt-1 break-all font-mono text-[hsl(var(--text-primary))]">{order.out_trade_no}</p>
      </div>

      <ReturnActions
        onRefresh={!isSucceeded && !isFailed ? () => void orderQuery.refetch() : undefined}
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
