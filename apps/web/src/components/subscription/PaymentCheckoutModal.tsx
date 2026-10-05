import { useMemo, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Check, CreditCard } from 'lucide-react'
import Modal from '../ui/Modal'
import { Button } from '../ui/Button'
import { paymentApi, paymentQueryKeys } from '../../lib/paymentApi'
import { handleApiError } from '../../lib/errorHandler'
import type { PaymentCycle, PaymentCheckout } from '../../types/payment'

const ZPAY_CHECKOUT_ACTION = 'https://zpayz.cn/submit.php'

function submitPaymentCheckout(checkout: PaymentCheckout): void {
  if (checkout.action !== ZPAY_CHECKOUT_ACTION || checkout.method !== 'POST') {
    throw new Error('Invalid payment checkout destination')
  }

  const form = document.createElement('form')
  form.method = 'POST'
  form.action = ZPAY_CHECKOUT_ACTION
  form.style.display = 'none'

  Object.entries(checkout.fields).forEach(([name, value]) => {
    const input = document.createElement('input')
    input.type = 'hidden'
    input.name = name
    input.value = value
    form.appendChild(input)
  })

  document.body.appendChild(form)
  try {
    form.submit()
  } finally {
    form.remove()
  }
}

interface PaymentCheckoutModalProps {
  isOpen: boolean
  onClose: () => void
  initialCycle?: PaymentCycle
  monthlyPriceCents?: number
  yearlyPriceCents?: number
}

export function PaymentCheckoutModal({
  isOpen,
  onClose,
  initialCycle = 'month',
  monthlyPriceCents,
  yearlyPriceCents,
}: PaymentCheckoutModalProps) {
  const { t, i18n } = useTranslation(['dashboard', 'common'])
  const [cycle, setCycle] = useState<PaymentCycle>(initialCycle)
  const [error, setError] = useState('')

  const optionsQuery = useQuery({
    queryKey: paymentQueryKeys.options(),
    queryFn: paymentApi.getOptions,
    enabled: isOpen,
    retry: false,
  })

  const createOrder = useMutation({
    mutationFn: () => paymentApi.createOrder({
      plan_name: 'pro',
      cycle,
      payment_method: 'alipay',
    }),
    onSuccess: ({ checkout }) => {
      try {
        submitPaymentCheckout(checkout)
      } catch {
        setError(t('dashboard:billing.paymentInvalidCheckout', '支付跳转校验失败，请重试或联系支持'))
      }
    },
    onError: (cause: unknown) => {
      setError(handleApiError(cause) || t('dashboard:billing.paymentCreateFailed', '创建支付订单失败，请重试'))
    },
  })

  const alipayEnabled = optionsQuery.data?.enabled === true
    && optionsQuery.data.payment_methods.includes('alipay')
  const isUnavailable = optionsQuery.isError || (!optionsQuery.isLoading && !alipayEnabled)

  const price = cycle === 'month' ? monthlyPriceCents : yearlyPriceCents
  const formattedPrice = useMemo(() => {
    if (price === undefined) return null
    const locale = i18n.language?.startsWith('en') ? 'en-US' : 'zh-CN'
    return `¥${(price / 100).toLocaleString(locale, { maximumFractionDigits: 2 })}`
  }, [i18n.language, price])

  const handlePay = () => {
    if (!alipayEnabled || createOrder.isPending) return
    setError('')
    createOrder.mutate()
  }

  const handleClose = () => {
    if (createOrder.isPending) return
    setCycle(initialCycle)
    setError('')
    onClose()
  }

  return (
    <Modal
      open={isOpen}
      onClose={handleClose}
      title={t('dashboard:billing.paymentTitle', '支付宝在线开通 Pro')}
      size="md"
      closeOnBackdropClick={!createOrder.isPending}
      closeOnEscape={!createOrder.isPending}
    >
      <Modal.Body>
        <div className="space-y-4">
          <p>{t('dashboard:billing.paymentDescription', '选择开通时长，确认后将前往支付宝收银台。')}</p>

          <div className="grid grid-cols-2 gap-3" role="radiogroup" aria-label={t('dashboard:billing.billingCycleLabel', '选择计费周期')}>
            {(['month', 'year'] as PaymentCycle[]).map((item) => {
              const selected = cycle === item
              const itemPrice = item === 'month' ? monthlyPriceCents : yearlyPriceCents
              return (
                <button
                  type="button"
                  role="radio"
                  aria-checked={selected}
                  key={item}
                  onClick={() => setCycle(item)}
                  disabled={createOrder.isPending}
                  className={`rounded-lg border p-3 text-left transition-colors ${
                    selected
                      ? 'border-[hsl(var(--accent-primary))] bg-[hsl(var(--accent-primary)/0.08)]'
                      : 'border-[hsl(var(--border-color))]'
                  }`}
                >
                  <span className="flex items-center justify-between font-medium text-[hsl(var(--text-primary))]">
                    {item === 'month'
                      ? t('dashboard:billing.monthlyDuration', '月付 · 30 天')
                      : t('dashboard:billing.yearlyDuration', '年付 · 365 天')}
                    {selected && <Check className="h-4 w-4 text-[hsl(var(--accent-primary))]" />}
                  </span>
                  {itemPrice !== undefined && (
                    <span className="mt-1 block text-sm text-[hsl(var(--text-secondary))]">
                      ¥{(itemPrice / 100).toLocaleString(i18n.language?.startsWith('en') ? 'en-US' : 'zh-CN', { maximumFractionDigits: 2 })}
                    </span>
                  )}
                </button>
              )
            })}
          </div>

          <div className="rounded-lg border border-[hsl(var(--border-color))] p-3">
            <div className="flex items-center gap-2 text-[hsl(var(--text-primary))]">
              <CreditCard className="h-4 w-4 text-[#1677ff]" />
              <span className="font-medium">{t('dashboard:billing.alipay', '支付宝')}</span>
            </div>
            <p className="mt-1 text-xs">{t('dashboard:billing.paymentSecureHint', '订单由服务器生成，会员权益以服务器到账确认为准。')}</p>
          </div>

          {optionsQuery.isLoading && (
            <p role="status">{t('common:loading', '加载中...')}</p>
          )}
          {isUnavailable && (
            <div className="rounded-lg bg-[hsl(var(--warning)/0.1)] p-3 text-[hsl(var(--warning))]" role="alert">
              {t('dashboard:billing.paymentUnavailable', '在线支付暂未开放，你仍可使用兑换码开通。')}
            </div>
          )}
          {error && (
            <div className="rounded-lg bg-[hsl(var(--error)/0.1)] p-3 text-[hsl(var(--error))]" role="alert">
              {error}
            </div>
          )}
        </div>
      </Modal.Body>
      <Modal.Footer>
        <Button variant="secondary" onClick={handleClose} disabled={createOrder.isPending}>
          {t('common:cancel', '取消')}
        </Button>
        <Button onClick={handlePay} disabled={!alipayEnabled || createOrder.isPending || optionsQuery.isLoading}>
          {createOrder.isPending
            ? t('dashboard:billing.paymentCreating', '正在创建订单...')
            : t('dashboard:billing.payWithAlipay', '支付宝支付{{price}}', { price: formattedPrice ? ` ${formattedPrice}` : '' })}
        </Button>
      </Modal.Footer>
    </Modal>
  )
}
