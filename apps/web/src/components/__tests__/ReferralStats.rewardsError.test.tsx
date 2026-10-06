import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import '@testing-library/jest-dom/vitest'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createInstance, type i18n } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { ReferralStats } from '../referral/ReferralStats'
import { referralApi } from '@/lib/referralApi'
import type { UserReward } from '@/types/referral'
import enReferral from '../../../public/locales/en/referral.json'
import zhReferral from '../../../public/locales/zh/referral.json'

vi.mock('@/lib/referralApi', () => ({ referralApi: { getStats: vi.fn(), getRewards: vi.fn() } }))

let client: QueryClient
let language: i18n
let offlineFetch: ReturnType<typeof vi.fn>
const pending: Array<(rows: UserReward[]) => void> = []
function deferredRewards() {
  let resolve!: (rows: UserReward[]) => void
  const promise = new Promise<UserReward[]>((done) => { resolve = done })
  pending.push(resolve)
  return { promise, resolve }
}
beforeEach(async () => {
  vi.stubGlobal('localStorage', new window.Storage())
  vi.stubGlobal('sessionStorage', new window.Storage())
  offlineFetch = vi.fn().mockRejectedValue(new Error('Unexpected network in rewards error fixture'))
  vi.stubGlobal('fetch', offlineFetch)
  vi.clearAllMocks()
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
  language = createInstance()
  await language.init({ lng: 'en', fallbackLng: 'en', initImmediate: false,
    resources: { en: { referral: enReferral }, zh: { referral: zhReferral } },
  })
  vi.mocked(referralApi.getStats).mockResolvedValue({ total_invites: 2, successful_invites: 1, total_points: 10, available_points: 10 })
})
afterEach(async () => {
  await act(async () => {
    for (const resolve of pending.splice(0)) resolve([])
    cleanup()
    client.clear()
  })
  language.off('languageChanged')
  expect(offlineFetch).not.toHaveBeenCalled()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})
async function show(locale: 'en' | 'zh' = 'en') {
  localStorage.setItem('zenstory-language', locale)
  await language.changeLanguage(locale)
  render(<QueryClientProvider client={client}><I18nextProvider i18n={language}><ReferralStats /></I18nextProvider></QueryClientProvider>)
}
const text = (key: string) => language.t(`referral:stats.${key}`)

it.each(['en', 'zh'] as const)('fresh rewards error is distinct from empty; explicit recovery preserves loader/empty (%s)', async (locale) => {
  vi.mocked(referralApi.getRewards).mockRejectedValueOnce(new Error('Local rewards failure'))
  await show(locale)
  expect(await screen.findByText(text('loadError'))).toBeInTheDocument()
  expect(screen.getByText(text('totalInvites'))).toBeInTheDocument()
  expect(screen.queryByText(text('noRewards'))).not.toBeInTheDocument()
  expect(referralApi.getRewards).toHaveBeenCalledTimes(1)

  const recovery = deferredRewards()
  vi.mocked(referralApi.getRewards).mockReturnValueOnce(recovery.promise)
  let refetch!: Promise<void>
  await act(async () => { refetch = client.refetchQueries({ queryKey: ['userRewards'] }) })
  await waitFor(() => { expect(document.querySelector('.animate-spin')).toBeInTheDocument() })
  expect(screen.queryByText(text('loadError'))).not.toBeInTheDocument()
  expect(screen.queryByText(text('noRewards'))).not.toBeInTheDocument()
  await act(async () => { recovery.resolve([]); await refetch })
  expect(await screen.findByText(text('noRewards'))).toBeInTheDocument()
  expect(screen.queryByText(text('loadError'))).not.toBeInTheDocument()
  expect(referralApi.getRewards).toHaveBeenCalledTimes(2)
  expect(referralApi.getStats).toHaveBeenCalledTimes(1)
})

it('healthy empty remains empty; cached records survive background fetch and rejection', async () => {
  vi.mocked(referralApi.getRewards).mockResolvedValueOnce([])
  await show()
  expect(await screen.findByText(text('noRewards'))).toBeInTheDocument()
  expect(screen.queryByText(text('loadError'))).not.toBeInTheDocument()

  const rows: UserReward[] = [{ id: 'cached', reward_type: 'points', amount: 10, source: 'Retained reward', is_used: false, expires_at: null, created_at: '2026-10-06T00:32:00' }]
  vi.mocked(referralApi.getRewards).mockResolvedValueOnce(rows)
  await act(async () => { await client.refetchQueries({ queryKey: ['userRewards'] }) })
  expect(await screen.findByText('Retained reward')).toBeInTheDocument()
  const background = deferredRewards()
  vi.mocked(referralApi.getRewards).mockReturnValueOnce(background.promise)
  let refetch!: Promise<void>
  await act(async () => { refetch = client.refetchQueries({ queryKey: ['userRewards'] }) })
  expect(screen.getByText('Retained reward')).toBeInTheDocument()
  expect(document.querySelector('.animate-spin')).not.toBeInTheDocument()
  // Complete the healthy background fetch, then reject the next refresh.
  background.resolve(rows)
  await act(async () => { await refetch })
  vi.mocked(referralApi.getRewards).mockRejectedValueOnce(new Error('Local background failure'))
  await act(async () => { await client.refetchQueries({ queryKey: ['userRewards'] }) })
  await waitFor(() => { expect(client.getQueryState(['userRewards'])?.status).toBe('error') })
  expect(screen.getByText('Retained reward')).toBeInTheDocument()
  expect(screen.queryByText(text('noRewards'))).not.toBeInTheDocument()
  expect(screen.queryByText(text('loadError'))).not.toBeInTheDocument()
  expect(client.getQueryData(['userRewards'])).toEqual(rows)
  expect(referralApi.getRewards).toHaveBeenCalledTimes(4)
  expect(referralApi.getStats).toHaveBeenCalledTimes(1)
})
