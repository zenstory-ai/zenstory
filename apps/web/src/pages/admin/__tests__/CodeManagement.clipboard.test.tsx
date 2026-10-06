import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import CodeManagement from '../CodeManagement'
import adminEn from '../../../../public/locales/en/admin.json'
import adminZh from '../../../../public/locales/zh/admin.json'
import commonEn from '../../../../public/locales/en/common.json'
import commonZh from '../../../../public/locales/zh/common.json'
import { adminApi } from '../../../lib/adminApi'
import { toast } from '../../../lib/toast'

vi.mock('../../../lib/adminApi', () => ({ adminApi: {
  getCodes: vi.fn(), createCode: vi.fn(), createCodesBatch: vi.fn(), updateCode: vi.fn(),
} }))
vi.mock('../../../lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
const translations = createInstance()
let writeCalls: string[]
let writeResult: Promise<void>
const writeText = (text: string) => { writeCalls.push(text); return writeResult }
let client: QueryClient
let settle: (() => void) | undefined
let unexpected: string[]
let emissions: unknown[]
let previousListeners: number
const unhandled = (reason: unknown) => { emissions.push(reason) }
function deferredWrite() {
  let done = false
  let resolve!: () => void
  let reject!: (reason: Error) => void
  const pending = new Promise<void>((yes, no) => {
    resolve = () => { if (!done) { done = true; yes() } }
    reject = reason => { if (!done) { done = true; no(reason) } }
  })
  // An async API returns a native Promise even when happy-dom replaces global Promise.
  const promise = (async () => { await pending })()
  settle = resolve
  return { promise, resolve, reject }
}
async function page(lng = 'en') {
  localStorage.setItem('zenstory-language', lng)
  await translations.changeLanguage(lng)
  render(<QueryClientProvider client={client}><I18nextProvider i18n={translations}><CodeManagement /></I18nextProvider></QueryClientProvider>)
  await screen.findByRole('cell', { name: 'FAKE-CODE-A' })
  fireEvent.click(screen.getAllByTitle(translations.t('admin:codes.copyCode'))[0])
  expect(writeCalls).toEqual(['FAKE-CODE-A'])
}
const flush = async () => { await act(async () => { await new Promise(resolve => setTimeout(resolve, 5)) }) }
beforeEach(async () => {
  vi.resetAllMocks(); settle = undefined; unexpected = []; emissions = []; writeCalls = []; writeResult = Promise.resolve()
  previousListeners = process.listenerCount('unhandledRejection'); process.on('unhandledRejection', unhandled)
  vi.stubGlobal('localStorage', new window.Storage()); vi.stubGlobal('sessionStorage', new window.Storage())
  expect(vi.isMockFunction(localStorage.getItem)).toBe(false)
  vi.stubGlobal('fetch', async (input: RequestInfo | URL) => { unexpected.push(String(input)); throw new Error('OFFLINE unexpected fetch') })
  vi.spyOn(navigator, 'clipboard', 'get').mockReturnValue({ writeText } as unknown as Clipboard)
  await translations.init({ lng: 'en', fallbackLng: 'en', ns: ['admin', 'common'], defaultNS: 'common',
    resources: { en: { admin: adminEn, common: commonEn }, zh: { admin: adminZh, common: commonZh } } })
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
  vi.mocked(adminApi.getCodes).mockResolvedValue({ items: [{ id: 'a', code: 'FAKE-CODE-A', tier: 'pro',
    duration_days: 30, code_type: 'single_use', max_uses: 1, current_uses: 0, is_active: true, notes: null,
    created_at: '2026-10-06T00:32:00Z', updated_at: '2026-10-06T00:32:00Z' }], total: 1, page: 1, page_size: 20 })
})
afterEach(async () => {
  settle?.(); await flush(); cleanup(); await client.cancelQueries(); client.clear()
  console.info('M16_CLIPBOARD_OBSERVATION', JSON.stringify({ writeCalls,
    success: vi.mocked(toast.success).mock.calls, error: vi.mocked(toast.error).mock.calls,
    unhandled: emissions.map(reason => String(reason)), unexpected, queryEntries: client.getQueryCache().getAll().length }))
  process.off('unhandledRejection', unhandled)
  expect(process.listenerCount('unhandledRejection')).toBe(previousListeners)
  expect(unexpected).toEqual([]); expect(client.getQueryCache().getAll()).toHaveLength(0)
  localStorage.clear(); sessionStorage.clear(); translations.off('languageChanged'); translations.off('loaded')
  vi.restoreAllMocks(); vi.unstubAllGlobals()
})
it('does not announce success until the actual clipboard Promise resolves', async () => {
  const gate = deferredWrite(); writeResult = gate.promise
  await page(); const premature = vi.mocked(toast.success).mock.calls.length
  await act(async () => gate.resolve()); await flush()
  expect(toast.success).toHaveBeenCalledExactlyOnceWith(adminEn.codes.copied)
  expect(premature).toBe(0); expect(toast.error).not.toHaveBeenCalled()
})
it('catches clipboard rejection without success or unhandled emission', async () => {
  const gate = deferredWrite(); writeResult = gate.promise
  await page(); gate.reject(new Error('test-owned clipboard denial')); await flush()
  expect.soft(toast.success).not.toHaveBeenCalled()
  expect.soft(emissions).toEqual([])
  expect(toast.error).toHaveBeenCalledExactlyOnceWith(commonEn.operationFailed)
})
it('preserves the copied string and current Chinese success after a healthy write', async () => {
  writeResult = Promise.resolve(); await page('zh'); await flush()
  expect(toast.success).toHaveBeenCalledExactlyOnceWith(adminZh.codes.copied)
  expect(toast.error).not.toHaveBeenCalled(); expect(emissions).toEqual([])
})
