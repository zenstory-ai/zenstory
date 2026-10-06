import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Check, CreditCard } from 'lucide-react'
import Modal from '../ui/Modal'
import { Button } from '../ui/Button'
import { paymentApi, paymentQueryKeys } from '../../lib/paymentApi'
import { ApiError } from '../../lib/apiClient'
import { trackEvent } from '../../lib/analytics'
import type { PaymentCycle, PaymentCheckout } from '../../types/payment'

// Codes whose errors: translation tells the buyer what to do next.
const PAYMENT_ERROR_CODES = new Set([
  'ERR_PAYMENT_UNAVAILABLE',
  'ERR_PAYMENT_UNSUPPORTED_OPTION',
  'ERR_PAYMENT_PLAN_UNAVAILABLE',
  'ERR_PAYMENT_ORDER_CREATE_FAILED',
  'ERR_PAYMENT_RATE_LIMITED',
])

// Mirrors the backend's upgrade_source validation; anything else would 422 the order.
const UPGRADE_SOURCE_PATTERN = /^[A-Za-z0-9_:-]{1,64}$/

const VALIDATION_ERROR_CODE = 'ERR_VALIDATION_ERROR'

function paymentErrorCode(cause: unknown): string | null {
  if (!(cause instanceof ApiError)) return null
  const code = cause.errorCode ?? cause.rawMessage
  if (code && PAYMENT_ERROR_CODES.has(code)) return code
  // A rejected request body means this page and the API are on different
  // releases; reloading the page picks up the matching client.
  if (cause.status === 422 || code === VALIDATION_ERROR_CODE) return VALIDATION_ERROR_CODE
  return null
}

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
  /** Upgrade entry that led here; stored on the order for paid-conversion attribution. */
  upgradeSource?: string
}

export function PaymentCheckoutModal({
  isOpen,
  onClose,
  initialCycle = 'month',
  monthlyPriceCents,
  yearlyPriceCents,
  upgradeSource: rawUpgradeSource,
}: PaymentCheckoutModalProps) {
  const upgradeSource = rawUpgradeSource && UPGRADE_SOURCE_PATTERN.test(rawUpgradeSource)
    ? rawUpgradeSource
    : undefined
  const { t, i18n } = useTranslation(['dashboard', 'common', 'errors'])
  const [cycle, setCycle] = useState<PaymentCycle>(initialCycle)
  const [error, setError] = useState('')
  // Set once the order exists and the form is submitted: until the browser has
  // left the page another click would create a second order.
  const [redirecting, setRedirecting] = useState(false)

  useEffect(() => {
    // Back-navigation from the cashier can restore this page from bfcache with
    // the lock still set; unlock so the buyer is not stuck.
    const handlePageShow = (event: PageTransitionEvent) => {
      if (event.persisted) setRedirecting(false)
    }
    window.addEventListener('pageshow', handlePageShow)
    return () => window.removeEventListener('pageshow', handlePageShow)
  }, [])

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
      ...(upgradeSource ? { upgrade_source: upgradeSource } : {}),
    }),
    onSuccess: ({ order, checkout }) => {
      try {
        submitPaymentCheckout(checkout)
        setRedirecting(true)
        trackEvent('checkout_redirected', {
          cycle: order.cycle,
          out_trade_no: order.out_trade_no,
          upgrade_source: upgradeSource,
        })
      } catch {
        setError(t('dashboard:billing.paymentInvalidCheckout', '无法跳转到支付宝，请刷新页面后重试'))
      }
    },
    onError: (cause: unknown) => {
      const code = paymentErrorCode(cause)
      trackEvent('checkout_failed', { cycle, error_code: code ?? 'unknown', upgrade_source: upgradeSource })
      const fallback = t('dashboard:billing.paymentCreateFailed', '暂时无法创建订单，请稍后重试')
      if (code === VALIDATION_ERROR_CODE) {
        setError(t('dashboard:billing.paymentPageOutdated', '页面已更新，请刷新页面后重试'))
      } else {
        setError(code ? t(`errors:${code}`, fallback) : fallback)
      }
    },
  })
  const isBusy = createOrder.isPending || redirecting

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
    if (!alipayEnabled || isBusy) return
    setError('')
    trackEvent('checkout_started', { cycle, upgrade_source: upgradeSource })
    createOrder.mutate()
  }

  const handleClose = () => {
    if (isBusy) return
    setCycle(initialCycle)
    setError('')
    onClose()
  }

  return (
    <Modal
      open={isOpen}
      onClose={handleClose}
      title={t('dashboard:billing.paymentTitle', '用支付宝开通 Pro')}
      size="md"
      closeOnBackdropClick={!isBusy}
      closeOnEscape={!isBusy}
    >
      <Modal.Body>
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3" role="radiogroup" aria-label={t('dashboard:billing.billingCycleLabel', '购买时长')}>
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
                  disabled={isBusy}
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
          </div>

          {optionsQuery.isLoading && (
            <p role="status">{t('common:loading', '加载中...')}</p>
          )}
          {isUnavailable && (
            <div className="rounded-lg bg-[hsl(var(--warning)/0.1)] p-3 text-[hsl(var(--warning))]" role="alert">
              {t('dashboard:billing.paymentUnavailable', '暂时无法在线支付。有兑换码的话，可在「订阅权益」页点「兑换码」开通。')}
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
        <Button variant="secondary" onClick={handleClose} disabled={isBusy}>
          {t('common:cancel', '取消')}
        </Button>
        <Button onClick={handlePay} disabled={!alipayEnabled || isBusy || optionsQuery.isLoading}>
          {redirecting
            ? t('dashboard:billing.paymentRedirecting', '正在前往支付宝...')
            : createOrder.isPending
            ? t('dashboard:billing.paymentCreating', '正在创建订单...')
            : t('dashboard:billing.payWithAlipay', '支付宝支付{{price}}', { price: formattedPrice ? ` ${formattedPrice}` : '' })}
        </Button>
      </Modal.Footer>
    </Modal>
  )
}
