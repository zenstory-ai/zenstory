import { useEffect, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { Check, CreditCard } from 'lucide-react'
import Modal from '../ui/Modal'
import { Button } from '../ui/Button'
import { paymentApi, paymentQueryKeys } from '../../lib/paymentApi'
import { ApiError } from '../../lib/apiClient'
import { trackEvent } from '../../lib/analytics'
import { formatYuan, getYearlySavings } from '../../lib/subscriptionEntitlements'
import { subscriptionApi } from '../../lib/subscriptionApi'
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
const PAYMENT_UNAVAILABLE_CODE = 'ERR_PAYMENT_UNAVAILABLE'

// Where payment-unavailable help is shown (retain the existing prop for callers).
export type PaymentRedeemEntry = 'on-page' | 'billing-page'

// Help navigation must not trigger the legacy activation deep link.
const BILLING_SUPPORT_PATH = '/dashboard/billing'

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
  /** The buyer already has Pro: the purchase extends it, so say "renew". */
  isRenewal?: boolean
  /**
   * 'on-page' when support is available in the opening page's footer help (billing
   * page); anywhere else the unavailable notice points to the billing page.
   */
  redeemEntry?: PaymentRedeemEntry
}

export function PaymentCheckoutModal({
  isOpen,
  onClose,
  initialCycle = 'month',
  monthlyPriceCents,
  yearlyPriceCents,
  upgradeSource: rawUpgradeSource,
  isRenewal = false,
  redeemEntry = 'billing-page',
}: PaymentCheckoutModalProps) {
  const upgradeSource = rawUpgradeSource && UPGRADE_SOURCE_PATTERN.test(rawUpgradeSource)
    ? rawUpgradeSource
    : undefined
  const { t, i18n } = useTranslation(['dashboard', 'common', 'errors'])
  const { data: benefitCatalog, isPending: isCatalogPending } = useQuery({
    queryKey: ['public-subscription-catalog'],
    queryFn: () => subscriptionApi.getCatalog(),
    staleTime: 60_000,
    enabled: isOpen,
  })
  const entitlements = benefitCatalog?.tiers.find(plan => plan.name === 'pro')?.entitlements
  const aiLimit = entitlements?.ai_conversations_per_day
  const projectLimit = entitlements?.active_projects_limit
  const materialLimit = entitlements?.material_decompositions_monthly
  const benefitParts = [
    aiLimit === -1 ? t('dashboard:billing.paymentAiUnlimited', 'AI 消息不限条数')
      : typeof aiLimit === 'number' ? t('dashboard:billing.paymentAiFinite', '每日 AI 消息 {{limit}} 条', { limit: aiLimit }) : null,
    projectLimit === -1 ? t('dashboard:billing.paymentProjectsUnlimited', '项目不限')
      : typeof projectLimit === 'number' ? t('dashboard:billing.paymentProjectsFinite', '最多 {{limit}} 个项目', { limit: projectLimit }) : null,
    materialLimit === -1 ? t('dashboard:billing.paymentMaterialsUnlimited', '素材拆解不限次数')
      : typeof materialLimit === 'number' && materialLimit > 0 ? t('dashboard:billing.paymentMaterialsFinite', '每月 {{limit}} 次素材拆解', { limit: materialLimit }) : null,
  ].filter(Boolean)
  const [cycle, setCycle] = useState<PaymentCycle>(initialCycle)
  const [error, setError] = useState('')
  // Set once the order exists and the form is submitted: until the browser has
  // left the page another click would create a second order.
  const [redirecting, setRedirecting] = useState(false)
  const paymentUnavailableText = redeemEntry === 'on-page'
    ? t('dashboard:billing.paymentUnavailableOnPage', '暂时无法在线支付，请稍后重试。如需帮助，可在本页底部查看客服联系方式。')
    : t('dashboard:billing.paymentUnavailable', '暂时无法在线支付，请稍后重试。如需帮助，可在「订阅权益」页查看客服联系方式。')

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
      } else if (code === PAYMENT_UNAVAILABLE_CODE) {
        // The errors: copy has no page context; say where help is available.
        setError(paymentUnavailableText)
      } else {
        setError(code ? t(`errors:${code}`, fallback) : fallback)
      }
    },
  })
  const isBusy = createOrder.isPending || redirecting
  const yearlySavings = getYearlySavings(monthlyPriceCents, yearlyPriceCents)
  const selectedPrice = cycle === 'month' ? monthlyPriceCents : yearlyPriceCents
  const hasSelectedPrice = typeof selectedPrice === 'number' && Number.isSafeInteger(selectedPrice) && selectedPrice > 0

  const alipayEnabled = optionsQuery.data?.enabled === true
    && optionsQuery.data.payment_methods.includes('alipay')
  const isUnavailable = optionsQuery.isError || (!optionsQuery.isLoading && !alipayEnabled)

  const handlePay = () => {
    if (!alipayEnabled || !hasSelectedPrice || isBusy) return
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
      title={isRenewal
        ? t('dashboard:billing.paymentTitleRenew', '续费 Pro')
        : t('dashboard:billing.paymentTitle', '开通 Pro')}
      size="md"
      closeOnBackdropClick={!isBusy}
      closeOnEscape={!isBusy}
    >
      <Modal.Body>
        <div className="space-y-4">
          {benefitParts.length > 0 && <p className="text-sm text-[hsl(var(--text-secondary))]">{benefitParts.join(' · ')}</p>}
          <div className="grid grid-cols-1 gap-3 pt-2 sm:grid-cols-2" role="radiogroup" aria-label={t('dashboard:billing.billingCycleLabel', '购买时长')}>
            {(['month', 'year'] as PaymentCycle[]).map((item) => {
              const selected = cycle === item
              const itemPrice = item === 'month' ? monthlyPriceCents : yearlyPriceCents
              const savings = item === 'year' ? yearlySavings : null
              return (
                <button
                  type="button"
                  role="radio"
                  aria-checked={selected}
                  key={item}
                  onClick={() => setCycle(item)}
                  disabled={isBusy}
                  className={`flex min-w-0 flex-col items-stretch rounded-lg border p-3 text-left transition-colors ${
                    selected
                      ? 'border-[hsl(var(--accent-primary))] bg-[hsl(var(--accent-primary)/0.08)] ring-1 ring-[hsl(var(--accent-primary))]'
                      : savings
                      ? 'border-[hsl(var(--accent-primary)/0.5)] hover:bg-[hsl(var(--accent-primary)/0.04)]'
                      : 'border-[hsl(var(--border-color))] hover:bg-[hsl(var(--bg-tertiary))]'
                  }`}
                >
                  <span className="flex items-center justify-between gap-2 text-sm font-medium text-[hsl(var(--text-primary))]">
                    {item === 'month'
                      ? t('dashboard:billing.monthlyDuration', '月付 · 30 天')
                      : t('dashboard:billing.yearlyDuration', '年付 · 365 天')}
                    {selected && <Check className="h-4 w-4 shrink-0 text-[hsl(var(--accent-primary))]" />}
                  </span>
                  {typeof itemPrice === 'number' && Number.isSafeInteger(itemPrice) && itemPrice > 0 && (
                    <span className="mt-1 block text-xl font-semibold text-[hsl(var(--text-primary))]">
                      {formatYuan(itemPrice, i18n.language)}
                    </span>
                  )}
                  {savings ? (
                    <span className="mt-0.5 block text-xs text-[hsl(var(--accent-primary))]">
                      {t('dashboard:billing.yearlyEquivalent', '折合 {{price}}/月', { price: formatYuan(savings.monthlyEquivalent, i18n.language) })}
                    </span>
                  ) : (
                    item === 'month' && (
                      <span className="mt-0.5 block text-xs text-[hsl(var(--text-secondary))]">
                        {t('dashboard:billing.monthlyFlexible', '先试一个月')}
                      </span>
                    )
                  )}
                  {/* The one savings line: a pale accent tag, so it never competes with the solid pay button. */}
                  {savings && (
                    <span className="mt-2 self-start rounded-full bg-[hsl(var(--accent-primary)/0.1)] px-2 py-0.5 text-xs font-medium text-[hsl(var(--accent-primary))]">
                      {t('dashboard:billing.yearlyBestValue', '最划算 · 省 {{percent}}%', { percent: savings.percent })}
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
            <p className="mt-2 text-xs text-[hsl(var(--text-secondary))]">
              {t('dashboard:billing.paymentNoAutoRenew', '一次性付款，不会自动续费。到期前续费，时长会接在当前到期日之后。')}
            </p>
          </div>

          {optionsQuery.isLoading && (
            <p role="status" className="text-sm text-[hsl(var(--text-secondary))]">{t('common:loading', "加载中…")}</p>
          )}
          {!hasSelectedPrice && (
            <p role={isCatalogPending ? 'status' : 'alert'} className="text-sm text-[hsl(var(--text-secondary))]">
              {isCatalogPending
                ? t('dashboard:billing.paymentPriceLoading', '正在加载价格…')
                : t('dashboard:billing.paymentPriceUnavailable', '暂时无法确认价格，请刷新页面后重试。')}
            </p>
          )}
          {isUnavailable && (
            <div className="rounded-lg bg-[hsl(var(--warning)/0.1)] p-3 text-sm text-[hsl(var(--warning-text))]" role="alert">
              {paymentUnavailableText}
              {redeemEntry === 'billing-page' && (
                <Link to={BILLING_SUPPORT_PATH} className="ml-1 font-medium underline">
                  {t('dashboard:billing.paymentUnavailableOpenBilling', '去订阅权益页')}
                </Link>
              )}
            </div>
          )}
          {error && (
            <div className="rounded-lg bg-[hsl(var(--error)/0.1)] p-3 text-sm text-[hsl(var(--error-text))]" role="alert">
              {error}
            </div>
          )}
        </div>
      </Modal.Body>
      <Modal.Footer>
        <Button size="touch" variant="secondary" onClick={handleClose} disabled={isBusy}>
          {t('common:cancel', '取消')}
        </Button>
        <Button size="touch" onClick={handlePay} disabled={!alipayEnabled || !hasSelectedPrice || isBusy || optionsQuery.isLoading}>
          {redirecting
            ? t('dashboard:billing.paymentRedirecting', "正在前往支付宝…")
            : createOrder.isPending
            ? t('dashboard:billing.paymentCreating', "正在创建订单…")
            : hasSelectedPrice
            ? t('dashboard:billing.goToPayAmount', '去支付 {{price}}', { price: formatYuan(selectedPrice, i18n.language) })
            : t('dashboard:billing.goToPay', '去支付')}
        </Button>
      </Modal.Footer>
    </Modal>
  )
}
