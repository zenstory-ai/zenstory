import '@testing-library/jest-dom/vitest'
import { MemoryRouter } from 'react-router-dom'
import { StrictMode } from 'react'
import type { ReactNode } from 'react'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import adminEnglish from '../../../../public/locales/en/admin.json'
import adminChinese from '../../../../public/locales/zh/admin.json'
import commonEnglish from '../../../../public/locales/en/common.json'
import commonChinese from '../../../../public/locales/zh/common.json'
import CodeManagement from '../CodeManagement'
import SubscriptionManagement from '../SubscriptionManagement'
import PaymentOrderManagement from '../PaymentOrderManagement'
import SubscriptionPlanManagement from '../SubscriptionPlanManagement'
import QuotaManagement from '../QuotaManagement'
import { adminApi } from '../../../lib/adminApi'
import type { AdminPaymentOrder, Subscription, RedemptionCode, PaymentOrderSyncResponse } from '../../../lib/adminApi'
import type { SubscriptionPlan } from '../../../types/subscription'
import type { UserQuotaDetail } from '../../../types/admin'
import { toast } from '../../../lib/toast'

vi.mock('../../../lib/adminApi', () => ({ adminApi: {
  getCodes: vi.fn(), createCode: vi.fn(), createCodesBatch: vi.fn(), updateCode: vi.fn(),
  getSubscriptions: vi.fn(), getPlans: vi.fn(), updatePlan: vi.fn(), updateUserSubscription: vi.fn(),
  getPaymentOrders: vi.fn(), syncPaymentOrder: vi.fn(), getQuotaUsageStats: vi.fn(), getUserQuota: vi.fn(),
} }))
vi.mock('../../../lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const i18n = createInstance()
const t = (key: string) => i18n.t(`admin:${key}`)
const plan: SubscriptionPlan = { id: 'plan-pro', name: 'pro', display_name: 'Pro', display_name_en: 'Pro',
  price_monthly_cents: 4900, price_yearly_cents: 49000, is_active: true, features: { max_projects: 10 } }
const code = (created_at: string): RedemptionCode => ({ id: 'code-a', code: 'CODE-A', tier: 'pro',
  duration_days: 30, code_type: 'single_use', max_uses: 1, current_uses: 0, is_active: true, notes: null,
  created_at, updated_at: created_at })
const subscription = (value: string | null): Subscription => ({ id: 'sub-a', user_id: 'user-a', username: 'writer',
  email: 'writer@example.test', plan_name: 'pro', plan_display_name: 'Pro', status: 'active', has_subscription_record: true,
  current_period_start: value, current_period_end: value, created_at: value ?? 'invalid', updated_at: 'invalid' })
const order = (id = 'a'): AdminPaymentOrder => ({ id, out_trade_no: `ORDER-${id}`, trade_no: null,
  user_id: `user-${id}`, username: `writer-${id}`, email: `${id}@example.test`, plan_name: 'pro', plan_display_name: 'Pro',
  cycle: 'month', amount_cents: 4900, payment_method: 'alipay', status: 'paid', fulfillment_status: 'failed',
  created_at: '2026-10-06T00:32:00', paid_at: null, fulfilled_at: null, failure_reason: null })
const completed = (id = 'a'): PaymentOrderSyncResponse => ({ outcome: 'fulfilled', order: { ...order(id), fulfillment_status: 'succeeded' } })
const quota = (id: string): UserQuotaDetail => ({ user_id: id, username: `quota-${id}`, email: `${id}@example.test`,
  plan_name: 'pro', plan_display_name: 'Pro', plan_display_name_en: 'Pro',
  ai_conversations: { used: 50, limit: 100, reset_at: null },
  material_decompositions: { used: 0, limit: -1, reset_at: null },
  custom_skills: { used: 0, limit: 0, reset_at: null },
  inspiration_copies: { used: 0, limit: 0, reset_at: null } })
let clients: QueryClient[]
let gates: { done: () => boolean; settle: () => void }[]
let unexpected: string[]
let overflow: string
function deferred<T>(fallback: T) {
  let settled = false
  let resolve!: (value: T) => void
  let reject!: (error: Error) => void
  const promise = new Promise<T>((yes, no) => {
    resolve = value => { if (!settled) { settled = true; yes(value) } }
    reject = error => { if (!settled) { settled = true; no(error) } }
  })
  gates.push({ done: () => settled, settle: () => resolve(fallback) })
  return { promise, resolve, reject }
}
async function language(lng: 'en' | 'zh') {
  localStorage.setItem('zenstory-language', lng)
  await i18n.changeLanguage(lng)
}
function mount(node: ReactNode, strict = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity, staleTime: Infinity }, mutations: { retry: false } } })
  clients.push(client)
  const tree = <QueryClientProvider client={client}><I18nextProvider i18n={i18n}><MemoryRouter>{node}</MemoryRouter></I18nextProvider></QueryClientProvider>
  return { ...render(strict ? <StrictMode>{tree}</StrictMode> : tree), client }
}
const flush = async () => { await act(async () => { await new Promise(resolve => setTimeout(resolve, 5)) }) }
const dateOptions: Intl.DateTimeFormatOptions = { year: 'numeric', month: '2-digit', day: '2-digit' }
const timeOptions: Intl.DateTimeFormatOptions = { ...dateOptions, hour: '2-digit', minute: '2-digit' }
const formatted = (value: string, locale: string, time = false) => new Date(value).toLocaleString(locale, time ? timeOptions : dateOptions).replace(/\s+/g, ' ')
async function codeRow(value: string) {
  vi.mocked(adminApi.getCodes).mockResolvedValue({ items: [code(value)], total: 1, page: 1, page_size: 20 })
  const view = mount(<CodeManagement />)
  await screen.findByRole('cell', { name: 'CODE-A' })
  return { ...view, row: screen.getByRole('cell', { name: 'CODE-A' }).closest('tr')! }
}
async function subscriptionRow(value: string | null) {
  vi.mocked(adminApi.getSubscriptions).mockResolvedValue({ items: [subscription(value)], total: 1, page: 1, page_size: 20 })
  const view = mount(<SubscriptionManagement />)
  await screen.findByRole('cell', { name: /writer@example.test/ })
  return { ...view, row: screen.getByRole('cell', { name: /writer@example.test/ }).closest('tr')! }
}
async function paymentPage(strict = false) {
  const view = mount(<PaymentOrderManagement />, strict)
  await screen.findByRole('cell', { name: 'ORDER-a' })
  return view
}
function openOrder(id: string) {
  const row = screen.getByRole('cell', { name: `ORDER-${id}` }).closest('tr')!
  fireEvent.click(within(row).getByRole('button', { name: t('paymentOrders.details') }))
}
const closeOrder = () => fireEvent.click(screen.getByRole('button', { name: i18n.t('common:closeModal') }))
const startSync = () => fireEvent.click(screen.getByRole('button', { name: t('paymentOrders.sync') }))

beforeEach(async () => {
  vi.resetAllMocks()
  clients = []; gates = []; unexpected = []; overflow = document.body.style.overflow
  vi.stubGlobal('localStorage', new window.Storage()); vi.stubGlobal('sessionStorage', new window.Storage())
  expect(vi.isMockFunction(localStorage.getItem)).toBe(false)
  vi.stubEnv('TZ', 'Pacific/Honolulu')
  expect(new Date('2026-10-06T00:32:00Z').getTimezoneOffset()).toBe(600)
  vi.stubGlobal('fetch', async (input: RequestInfo | URL) => { unexpected.push(String(input)); throw new Error('OFFLINE unexpected fetch') })
  await i18n.init({ lng: 'en', fallbackLng: 'en', defaultNS: 'common', ns: ['admin', 'common'],
    resources: { en: { admin: adminEnglish, common: commonEnglish }, zh: { admin: adminChinese, common: commonChinese } },
    interpolation: { escapeValue: false } })
  localStorage.setItem('zenstory-language', 'en')
  vi.mocked(adminApi.getPlans).mockResolvedValue([plan])
  vi.mocked(adminApi.getCodes).mockResolvedValue({ items: [code('2026-10-06T00:32:00Z')], total: 1, page: 1, page_size: 20 })
  vi.mocked(adminApi.getSubscriptions).mockResolvedValue({ items: [subscription('2026-10-06T00:32:00Z')], total: 1, page: 1, page_size: 20 })
  vi.mocked(adminApi.getPaymentOrders).mockResolvedValue({ items: [order('a'), order('b')], total: 2, page: 1, page_size: 20 })
  vi.mocked(adminApi.syncPaymentOrder).mockImplementation(async id => completed(id))
  vi.mocked(adminApi.updatePlan).mockResolvedValue(plan)
  vi.mocked(adminApi.createCode).mockResolvedValue(code('2026-10-06T00:32:00Z'))
  vi.mocked(adminApi.createCodesBatch).mockResolvedValue({ codes: ['ONE', 'TWO'], count: 2 })
  vi.mocked(adminApi.updateUserSubscription).mockResolvedValue({ success: true })
  vi.mocked(adminApi.getQuotaUsageStats).mockResolvedValue({ period_start: '2026-10-01T00:00:00Z', period_end: '2026-11-01T00:00:00Z', material_decompositions: 2, skills_created: 3, inspiration_copies: 0 })
  vi.mocked(adminApi.getUserQuota).mockImplementation(async id => quota(id))
})
afterEach(async () => {
  const pending = gates.filter(g => !g.done()).length
  await act(async () => { gates.forEach(g => g.settle()) })
  cleanup()
  for (const client of clients) { await client.cancelQueries(); client.clear() }
  console.info('M16_UI_OBSERVATION', JSON.stringify({ queries: vi.mocked(adminApi.getPaymentOrders).mock.calls,
    sync: vi.mocked(adminApi.syncPaymentOrder).mock.calls, codes: vi.mocked(adminApi.getCodes).mock.calls,
    subscriptions: vi.mocked(adminApi.getSubscriptions).mock.calls, userQuota: vi.mocked(adminApi.getUserQuota).mock.calls,
    toastSuccess: vi.mocked(toast.success).mock.calls, toastError: vi.mocked(toast.error).mock.calls,
    cleanup: { pending, portals: document.querySelectorAll('[role="dialog"]').length, queryCount: clients.reduce((n, c) => n + c.getQueryCache().getAll().length, 0) }, unexpected }))
  expect(pending).toBe(0); expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(0)
  expect(document.body.style.overflow).toBe(overflow); expect(unexpected).toEqual([])
  expect(clients.every(c => c.getQueryCache().getAll().length === 0 && c.getMutationCache().getAll().length === 0)).toBe(true)
  localStorage.clear(); sessionStorage.clear(); i18n.off('languageChanged'); i18n.off('loaded')
  vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.unstubAllEnvs()
})

describe.each(['en', 'zh'] as const)('real nonUTC admin dates locale=%s', lng => {
  it('code creation naive UTC has the same local instant as explicit Z', async () => {
    await language(lng); const locale = lng === 'en' ? 'en-US' : 'zh-CN'
    const { row } = await codeRow('2026-10-06T00:32:00')
    const expected = formatted('2026-10-06T00:32:00Z', locale, true)
    expect(expected).not.toBe(formatted('2026-10-06T00:32:00', locale, true))
    expect(row).toHaveTextContent(expected)
  })
  it('subscription period list/details and creation datetime treat naive UTC consistently', async () => {
    await language(lng); const locale = lng === 'en' ? 'en-US' : 'zh-CN'
    const { row, container } = await subscriptionRow('2026-10-06T00:32:00')
    fireEvent.click(within(row).getByTitle(t('subscriptions.viewDetails')))
    const modal = container.querySelector('.fixed.inset-0')!
    expect.soft(row).toHaveTextContent(formatted('2026-10-06T00:32:00Z', locale))
    expect(modal).toHaveTextContent(formatted('2026-10-06T00:32:00Z', locale, true))
  })
})
it.each(['2026-10-06T00:32:00Z', '2026-10-06T08:32:00+08:00'])('preserves aware code/subscription instant %s', async value => {
  const codeView = await codeRow(value)
  expect(codeView.row).toHaveTextContent(formatted(value, 'en-US', true)); codeView.unmount()
  const subView = await subscriptionRow(value)
  expect(subView.row).toHaveTextContent(formatted(value, 'en-US'))
})
it('invalid and nullable dates keep their existing dash fallthrough', async () => {
  const codeView = await codeRow('invalid')
  expect(within(codeView.row).getByRole('cell', { name: '-' })).toBeInTheDocument(); codeView.unmount()
  const subView = await subscriptionRow(null)
  fireEvent.click(within(subView.row).getByTitle(t('subscriptions.viewDetails')))
  expect(within(subView.container.querySelector('.fixed.inset-0') as HTMLElement).getAllByText('-')).toHaveLength(3)
})

describe.each([false, true])('actual order sync rootStrict=%s', strict => {
  it('closed details stay closed after valid old sync success while broad list refresh remains', async () => {
    const gate = deferred(completed()); vi.mocked(adminApi.syncPaymentOrder).mockReturnValue(gate.promise)
    await paymentPage(strict); openOrder('a'); startSync()
    await waitFor(() => expect(adminApi.syncPaymentOrder).toHaveBeenCalledTimes(1))
    closeOrder(); await act(async () => gate.resolve(completed())); await flush()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    await waitFor(() => expect(adminApi.getPaymentOrders).toHaveBeenCalledTimes(2))
  })
  it('A success cannot replace currently opened B details or its message', async () => {
    const gate = deferred(completed()); vi.mocked(adminApi.syncPaymentOrder).mockReturnValue(gate.promise)
    await paymentPage(strict); openOrder('a'); startSync()
    await waitFor(() => expect(adminApi.syncPaymentOrder).toHaveBeenCalledTimes(1))
    closeOrder(); openOrder('b'); expect(within(screen.getByRole('dialog')).getByText('ORDER-b')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: t('paymentOrders.syncing') })).toBeDisabled()
    await act(async () => gate.resolve(completed())); await flush()
    const dialog = within(screen.getByRole('dialog'))
    expect(dialog.getByText('ORDER-b')).toBeInTheDocument(); expect(dialog.queryByRole('status')).not.toBeInTheDocument()
    expect(dialog.getByRole('button', { name: t('paymentOrders.sync') })).toBeEnabled()
    expect(adminApi.syncPaymentOrder).toHaveBeenCalledWith('a')
  })
  it('A error cannot display its message in currently opened B details', async () => {
    const gate = deferred(completed()); vi.mocked(adminApi.syncPaymentOrder).mockReturnValue(gate.promise)
    await paymentPage(strict); openOrder('a'); startSync()
    await waitFor(() => expect(adminApi.syncPaymentOrder).toHaveBeenCalledTimes(1))
    closeOrder(); openOrder('b'); expect(within(screen.getByRole('dialog')).getByText('ORDER-b')).toBeInTheDocument(); await act(async () => gate.reject(new Error('obsolete A failure'))); await flush()
    const dialog = within(screen.getByRole('dialog'))
    expect(dialog.getByText('ORDER-b')).toBeInTheDocument(); expect(dialog.queryByRole('status')).not.toBeInTheDocument()
    expect(dialog.getByRole('button', { name: t('paymentOrders.sync') })).toBeEnabled()
    expect(adminApi.getPaymentOrders).toHaveBeenCalledTimes(1)
  })
})
it('closing and reopening the same order is a new details lifetime', async () => {
  const gate = deferred(completed()); vi.mocked(adminApi.syncPaymentOrder).mockReturnValue(gate.promise)
  await paymentPage(); openOrder('a'); startSync(); await waitFor(() => expect(adminApi.syncPaymentOrder).toHaveBeenCalledTimes(1))
  closeOrder(); openOrder('a'); await act(async () => gate.resolve(completed())); await flush()
  const dialog = within(screen.getByRole('dialog'))
  expect(dialog.queryByRole('status')).not.toBeInTheDocument()
  expect(dialog.getByRole('button', { name: t('paymentOrders.sync') })).toBeEnabled()
})
it('current sync success shows exact result and refreshes the broad list once', async () => {
  await paymentPage(); openOrder('a'); startSync()
  expect(await screen.findByRole('status')).toHaveTextContent(t('paymentOrders.syncOutcome.fulfilled'))
  expect(within(screen.getByRole('dialog')).queryByRole('button', { name: t('paymentOrders.sync') })).not.toBeInTheDocument()
  await waitFor(() => expect(adminApi.getPaymentOrders).toHaveBeenCalledTimes(2))
  expect(adminApi.syncPaymentOrder).toHaveBeenCalledExactlyOnceWith('a')
})
it('current sync failure stays visible and retry can succeed', async () => {
  vi.mocked(adminApi.syncPaymentOrder).mockRejectedValueOnce(new Error('owned failure'))
  await paymentPage(); openOrder('a'); startSync()
  expect(await screen.findByRole('status')).toHaveTextContent('unknown')
  expect(screen.getByRole('button', { name: t('paymentOrders.sync') })).toBeEnabled(); startSync()
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(t('paymentOrders.syncOutcome.fulfilled')))
  expect(adminApi.syncPaymentOrder).toHaveBeenCalledTimes(2)
})
it('live plan error/retry preserves form and current success invalidates plans', async () => {
  vi.mocked(adminApi.updatePlan).mockRejectedValueOnce(new Error('owned plan error'))
  const { container } = mount(<SubscriptionPlanManagement />)
  fireEvent.click(await screen.findByRole('button', { name: t('plans.edit') }))
  fireEvent.click(screen.getByRole('button', { name: t('plans.save') }))
  await waitFor(() => expect(toast.error).toHaveBeenCalledTimes(1))
  expect(container.querySelector('.fixed.inset-0')).not.toBeNull()
  fireEvent.click(screen.getByRole('button', { name: t('plans.save') }))
  await waitFor(() => expect(container.querySelector('.fixed.inset-0')).toBeNull())
  expect(toast.success).toHaveBeenCalledExactlyOnceWith(t('plans.updateSuccess'))
  expect(adminApi.updatePlan).toHaveBeenCalledTimes(2); expect(adminApi.getPlans).toHaveBeenCalledTimes(2)
})
it('live code create failure retries, and batch completion uses returned count', async () => {
  vi.mocked(adminApi.createCode).mockRejectedValueOnce(new Error('owned code error'))
  const { container } = mount(<CodeManagement />); await screen.findByRole('cell', { name: 'CODE-A' })
  fireEvent.click(screen.getByRole('button', { name: t('codes.create') }))
  fireEvent.click(within(container.querySelector('.fixed.inset-0') as HTMLElement).getByRole('button', { name: t('codes.create') }))
  await waitFor(() => expect(toast.error).toHaveBeenCalledTimes(1))
  fireEvent.click(within(container.querySelector('.fixed.inset-0') as HTMLElement).getByRole('button', { name: t('codes.create') }))
  await waitFor(() => expect(container.querySelector('.fixed.inset-0')).toBeNull())
  fireEvent.click(screen.getByRole('button', { name: t('codes.batchCreate') }))
  fireEvent.click(within(container.querySelector('.fixed.inset-0') as HTMLElement).getByRole('button', { name: t('codes.batchCreate') }))
  const resultDialog = await screen.findByRole('dialog')
  expect(within(resultDialog).getByRole('textbox')).toHaveValue('ONE\nTWO')
  expect(within(resultDialog).getByRole('heading')).toHaveTextContent(i18n.t('admin:codes.batchResultTitle', { count: 2 }))
  fireEvent.click(within(resultDialog).getByRole('button', { name: i18n.t('common:close') }))
  await waitFor(() => expect(container.querySelector('.fixed.inset-0')).toBeNull())
  expect(toast.success).toHaveBeenLastCalledWith(i18n.t('admin:codes.batchCreateSuccess', { count: 2 }))
  expect(adminApi.createCode).toHaveBeenCalledTimes(2); expect(adminApi.createCodesBatch).toHaveBeenCalledTimes(1)
})
it('real quota query keys isolate latest searched user from delayed old data', async () => {
  const a = deferred(quota('a')); const b = deferred(quota('b'))
  vi.mocked(adminApi.getUserQuota).mockImplementation(id => id === 'a' ? a.promise : b.promise)
  mount(<QuotaManagement />); await screen.findByRole('heading', { level: 2, name: t('quota.stats') })
  const input = screen.getByPlaceholderText(t('quota.searchUser'))
  fireEvent.change(input, { target: { value: ' a ' } }); fireEvent.keyDown(input, { key: 'Enter' })
  await waitFor(() => expect(adminApi.getUserQuota).toHaveBeenCalledWith('a'))
  fireEvent.change(input, { target: { value: 'b' } }); fireEvent.click(screen.getByRole('button', { name: i18n.t('common:search') }))
  await act(async () => b.resolve(quota('b'))); await screen.findByText('quota-b')
  await act(async () => a.resolve(quota('a'))); await flush()
  expect(screen.getByText('quota-b')).toBeInTheDocument(); expect(screen.queryByText('quota-a')).not.toBeInTheDocument()
  expect(adminApi.getUserQuota).toHaveBeenCalledTimes(2)
})
