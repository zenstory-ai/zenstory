import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createInstance, type i18n } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { PointsHistory } from '../points/PointsHistory'
import { ReferralStats } from '../referral/ReferralStats'
import { InviteCodeCard } from '../referral/InviteCodeCard'
import { pointsApi } from '@/lib/pointsApi'
import { referralApi } from '@/lib/referralApi'
import type { InviteCode } from '@/types/referral'
import type { PointsTransaction } from '@/types/points'
import enPoints from '../../../public/locales/en/points.json'
import zhPoints from '../../../public/locales/zh/points.json'
import enReferral from '../../../public/locales/en/referral.json'
import zhReferral from '../../../public/locales/zh/referral.json'

vi.mock('@/lib/pointsApi', () => ({ pointsApi: { getTransactions: vi.fn() } }))
vi.mock('@/lib/referralApi', () => ({ referralApi: { getStats: vi.fn(), getRewards: vi.fn() } }))

const NOW = '2026-10-06T02:00:00Z'
const formats = ['naive', 'Z', '+08'] as const
function stamp(utc: string, format: typeof formats[number]) {
  if (format === 'naive') return utc.slice(0, -1)
  if (format === 'Z') return utc
  return new Date(new Date(utc).getTime() + 8 * 3600000).toISOString().slice(0, -1) + '+08:00'
}
const invite = (expires_at: string | null, extra: Partial<InviteCode> = {}): InviteCode => ({
  id: 'invite', code: 'LOCAL-CODE', max_uses: 5, current_uses: 1, is_active: true,
  expires_at, created_at: '2026-10-01T00:00:00Z', ...extra,
})
const transaction = (id: string, created_at: string, extra: Partial<PointsTransaction> = {}): PointsTransaction => ({
  id, created_at, amount: 10, balance_after: 100, transaction_type: 'check_in',
  source_id: null, description: null, expires_at: null, is_expired: false, ...extra,
})
let client: QueryClient
let language: i18n
let offlineFetch: ReturnType<typeof vi.fn>

beforeEach(async () => {
  vi.stubGlobal('localStorage', new window.Storage())
  vi.stubGlobal('sessionStorage', new window.Storage())
  vi.stubEnv('TZ', 'America/Los_Angeles')
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(NOW))
  expect(new Date().getTimezoneOffset()).toBe(420)
  offlineFetch = vi.fn().mockRejectedValue(new Error('Unexpected network in timestamp fixture'))
  vi.stubGlobal('fetch', offlineFetch)
  vi.clearAllMocks()
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
  language = createInstance()
  await language.init({ lng: 'en', fallbackLng: 'en', initImmediate: false, resources: {
    en: { points: enPoints, referral: enReferral }, zh: { points: zhPoints, referral: zhReferral },
  } })
  vi.mocked(referralApi.getStats).mockResolvedValue({ total_invites: 1, successful_invites: 1, total_points: 10, available_points: 10 })
  vi.mocked(referralApi.getRewards).mockResolvedValue([])
})
afterEach(() => {
  cleanup()
  client.clear()
  language.off('languageChanged')
  expect(offlineFetch).not.toHaveBeenCalled()
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
  vi.restoreAllMocks()
})
async function show(element: React.ReactNode, locale: 'en' | 'zh' = 'en') {
  localStorage.setItem('zenstory-language', locale)
  await language.changeLanguage(locale)
  return render(<QueryClientProvider client={client}><I18nextProvider i18n={language}>{element}</I18nextProvider></QueryClientProvider>)
}
function history(rows: PointsTransaction[]) {
  vi.mocked(pointsApi.getTransactions).mockResolvedValue({ transactions: rows, total: rows.length, page: 1, page_size: 10, total_pages: 1 })
}
function reward(created_at: string) {
  vi.mocked(referralApi.getRewards).mockResolvedValue([{ id: 'reward', reward_type: 'points', amount: 10, source: 'local reward', is_used: false, expires_at: null, created_at }])
}

describe.each(['en', 'zh'] as const)('actual timestamp UI, locale %s', (locale) => {
  it.each(formats)('points minute/hour instant parity: %s', async (format) => {
    history([transaction('minutes', stamp('2026-10-06T01:55:00Z', format)), transaction('hours', stamp('2026-10-06T00:00:00Z', format))])
    await show(<PointsHistory />, locale)
    expect(await screen.findByText(language.t('points:relativeTime.minutesAgo', { count: 5 }))).toBeInTheDocument()
    expect(screen.getByText(language.t('points:relativeTime.hoursAgo', { count: 2 }))).toBeInTheDocument()
    expect(pointsApi.getTransactions).toHaveBeenCalledTimes(1)
  })
  it.each(formats)('reward local calendar instant parity: %s', async (format) => {
    reward(stamp('2026-10-06T00:32:00Z', format))
    await show(<ReferralStats />, locale)
    const expected = new Date('2026-10-06T00:32:00Z').toLocaleDateString(locale === 'en' ? 'en-US' : 'zh-CN')
    expect(await screen.findByText(expected)).toBeInTheDocument()
    expect(referralApi.getStats).toHaveBeenCalledTimes(1)
    expect(referralApi.getRewards).toHaveBeenCalledTimes(1)
  })
  it.each(formats)('expired invite instant parity: %s', async (format) => {
    await show(<InviteCodeCard inviteCode={invite(stamp('2026-10-06T01:30:00Z', format))} />, locale)
    expect(screen.getAllByText(language.t('referral:card.expired'))).toHaveLength(2)
    expect(screen.queryByText(language.t('referral:card.available'))).not.toBeInTheDocument()
  })
  it.each(formats)('invite ceil-day and local calendar presentation: %s', async (format) => {
    await show(<>
      <InviteCodeCard inviteCode={invite(stamp('2026-10-07T03:00:00Z', format))} />
      <InviteCodeCard inviteCode={invite(stamp('2026-10-20T00:32:00Z', format), { id: 'long', code: 'LONG-CODE' })} />
    </>, locale)
    expect(screen.getByText(language.t('referral:card.expiresInDays', { count: 2 }))).toBeInTheDocument()
    expect(screen.getByText(new Date('2026-10-20T00:32:00Z').toLocaleDateString(locale === 'en' ? 'en-US' : 'zh-CN'))).toBeInTheDocument()
  })
})

it('missing expiry, max cap, inactive flag and tomorrow preserve current status policy', async () => {
  await show(<>
    <InviteCodeCard inviteCode={invite(null)} />
    <InviteCodeCard inviteCode={invite(null, { id: 'full', current_uses: 5 })} />
    <InviteCodeCard inviteCode={invite('2026-10-06T03:00:00Z', { id: 'off', is_active: false })} />
  </>)
  expect(screen.getByText('Available')).toBeInTheDocument()
  expect(screen.getByText('Used up')).toBeInTheDocument()
  expect(screen.getByText('Disabled')).toBeInTheDocument()
  expect(screen.getByText('Expires tomorrow')).toBeInTheDocument()
})
it('points server expired flag and yesterday/week/calendar thresholds remain intact', async () => {
  history([
    transaction('yesterday', '2026-10-05T01:00:00Z', { is_expired: true }),
    transaction('days', '2026-10-03T01:00:00Z'), transaction('old', '2026-09-01T00:32:00Z'),
  ])
  await show(<PointsHistory />)
  expect(await screen.findByText('Yesterday')).toBeInTheDocument()
  expect(screen.getByText('3 days ago')).toBeInTheDocument()
  expect(screen.getByText(new Date('2026-09-01T00:32:00Z').toLocaleDateString('en-US'))).toBeInTheDocument()
  expect(screen.getByText('Expired')).toBeInTheDocument()
})
it('invalid timestamps retain Invalid Date fallthrough and nonexpired invite status', async () => {
  history([transaction('invalid', 'not-a-date')])
  reward('not-a-date')
  await show(<><PointsHistory /><ReferralStats /><InviteCodeCard inviteCode={invite('not-a-date')} /></>)
  await screen.findByText('local reward')
  expect(screen.getAllByText('Invalid Date')).toHaveLength(3)
  expect(screen.getByText('Available')).toBeInTheDocument()
})
