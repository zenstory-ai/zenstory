import { StrictMode } from 'react'
import { act, cleanup, fireEvent, render, renderHook, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createInstance, type i18n } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import process from 'node:process'

type Locale = 'zh' | 'en'
type Gate = { promise: Promise<Response>; release: (status?: number) => void; reject: () => void; settled: () => boolean }
type RequestTrace = { path: string; cache: RequestCache | undefined; status?: number; rejection?: string }
let Suggestions: typeof import('../../components/inspirations/DashboardInspirationSuggestions')['DashboardInspirationSuggestions']
let source: typeof import('../../lib/dashboardInspirationSource')
let useDashboardInspirations: typeof import('../useDashboardInspirations')['useDashboardInspirations']
let translations: i18n
let gates: Gate[]
let requests: RequestTrace[]
let unexpected: string[]
let pending: Set<Promise<Response>>
let checkpoints: { label: string; text: string; titles: string[]; requests: number }[]
let rejections: string[]
let route: (locale: Locale) => Promise<Response>
let originalOverflow: string
let originalFetch: typeof fetch
let originalStorage: Storage
let originalSessionStorage: Storage
let originalUnhandledListeners: number
let restoreDate: () => void
let featureEnabled: boolean
const onSelect = vi.fn()
const unhandled = (reason: unknown) => { rejections.push(reason instanceof Error ? reason.message : String(reason)) }

function payload(locale: Locale) {
  const items = Object.fromEntries(['novel', 'short', 'screenplay'].map(type => [type,
    Array.from({ length: 16 }, (_, index) => ({ id: `${locale}-${type}-${index}`, title: `${locale} title ${index}`,
      hook: `${locale} hook ${index}`, tags: ['local'], source: 'offline-fixture' })),
  ]))
  return { version: 1, locale, generated_at: '2026-10-06T12:00:00Z', items, homepage_priority: items }
}
function response(locale: Locale, status = 200) {
  return new Response(JSON.stringify(payload(locale)), { status, headers: { 'Content-Type': 'application/json' } })
}
function gate(locale: Locale): Gate {
  let done = false
  let resolve!: (value: Response) => void
  let reject!: (reason: Error) => void
  const promise = new Promise<Response>((yes, no) => { resolve = yes; reject = no })
  const value = { promise, release: (status = 200) => { if (!done) { done = true; resolve(response(locale, status)) } },
    reject: () => { if (!done) { done = true; reject(new TypeError(`offline ${locale} network rejection`)) } }, settled: () => done }
  gates.push(value)
  return value
}
async function flush() {
  // Await the owned macrotask so genuine uncaught promise emissions are observed.
  await act(async () => {
    for (let i = 0; i < 10; i++) await Promise.resolve()
    await new Promise<void>(resolve => setTimeout(resolve, 0))
  })
}
async function language(locale: Locale) { await act(async () => { await translations.changeLanguage(locale) }); await flush() }
function titles() { return screen.queryAllByRole('button').map(button => button.getAttribute('aria-label')).filter((title): title is string => Boolean(title)) }
function snapshot(label: string) { checkpoints.push({ label, text: document.body.textContent ?? '', titles: titles(), requests: requests.length }) }
function leaf(strict: boolean, count = 1) {
  const content = <I18nextProvider i18n={translations}>{Array.from({ length: count }, (_, index) =>
    <Suggestions key={index} projectType="novel" onSelect={onSelect} />)}</I18nextProvider>
  return strict ? <StrictMode>{content}</StrictMode> : content
}

beforeEach(async () => {
  vi.resetModules()
  gates = []; requests = []; unexpected = []; pending = new Set(); checkpoints = []; rejections = []
  onSelect.mockClear(); originalOverflow = document.body.style.overflow
  originalFetch = globalThis.fetch; originalStorage = localStorage; originalSessionStorage = sessionStorage
  originalUnhandledListeners = process.listenerCount('unhandledRejection')
  vi.stubGlobal('localStorage', new window.Storage()); vi.stubGlobal('sessionStorage', new window.Storage())
  expect(localStorage).toBeInstanceOf(window.Storage); expect(vi.isMockFunction(localStorage.getItem)).toBe(false)
  localStorage.clear(); sessionStorage.clear()
  const dateSpy = vi.spyOn(Date, 'now').mockReturnValue(Date.parse('2026-10-06T12:00:00Z'))
  restoreDate = () => dateSpy.mockRestore()
  route = async locale => response(locale)
  vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) => {
    const path = typeof input === 'string' ? input : input instanceof URL ? input.pathname : input.url
    const match = /^\/generated\/dashboard-inspirations\.v1\.(zh|en)\.json$/.exec(path)
    if (!match) { unexpected.push(path); return Promise.reject(new Error(`OFFLINE_FETCH_DENIED ${path}`)) }
    const record: RequestTrace = { path, cache: init?.cache }; requests.push(record)
    const request = route(match[1] as Locale).then(value => { record.status = value.status; return value }, error => {
      record.rejection = error instanceof Error ? error.message : String(error); throw error
    })
    pending.add(request); void request.then(() => pending.delete(request), () => pending.delete(request))
    return request
  })
  process.on('unhandledRejection', unhandled)
  translations = createInstance()
  await translations.init({ lng: 'zh', fallbackLng: 'en', defaultNS: 'dashboard', initImmediate: false,
    resources: { zh: { dashboard: { dashboard: { realInspirationsTitle: 'Suggestions', realInspirationsRefresh: 'Another batch' } } },
      en: { dashboard: { dashboard: { realInspirationsTitle: 'Suggestions', realInspirationsRefresh: 'Another batch' } } } } })
  source = await import('../../lib/dashboardInspirationSource')
  ;({ DashboardInspirationSuggestions: Suggestions } = await import('../../components/inspirations/DashboardInspirationSuggestions'))
  ;({ useDashboardInspirations } = await import('../useDashboardInspirations'))
  featureEnabled = (await import('../../config/inspirations')).inspirationsConfig.enabled
})

afterEach(async () => {
  const unsettledAtAssertion = gates.filter(item => !item.settled()).length
  for (const item of gates) item.release()
  await flush(); snapshot('before cleanup')
  cleanup(); await flush(); await Promise.allSettled([...pending])
  const cleanupState = { unsettledAtAssertion, pending: pending.size, unexpected, suggestions: screen.queryAllByTestId('dashboard-real-inspirations').length,
    bodyOverflowRestored: document.body.style.overflow === originalOverflow }
  console.info('M19_DASHBOARD_OBSERVATION', JSON.stringify({ case: expect.getState().currentTestName, requests, checkpoints,
    selections: onSelect.mock.calls, unhandledRejections: rejections, featureEnabled, cleanup: cleanupState }))
  process.off('unhandledRejection', unhandled); translations.off('languageChanged')
  localStorage.clear(); sessionStorage.clear(); expect(localStorage.length).toBe(0); expect(sessionStorage.length).toBe(0)
  restoreDate(); vi.unstubAllGlobals(); vi.resetModules()
  // Restore the evidence setup's pre-test deny fence as well as ordinary fetch.
  globalThis.fetch = originalFetch
  expect(globalThis.fetch).toBe(originalFetch); expect(localStorage).toBe(originalStorage); expect(sessionStorage).toBe(originalSessionStorage)
  expect(process.listenerCount('unhandledRejection')).toBe(originalUnhandledListeners)
  expect(unexpected).toEqual([]); expect(unsettledAtAssertion).toBe(0); expect(pending.size).toBe(0)
  expect(cleanupState.suggestions).toBe(0); expect(cleanupState.bodyOverflowRestored).toBe(true)
})

describe.each([false, true])('actual dashboard suggestions rootStrict=%s', strict => {
  it('retains current B items after cancelled locale A completes with nonOK', async () => {
    const a = gate('zh'); const b = gate('en'); route = locale => locale === 'zh' ? a.promise : b.promise
    render(leaf(strict)); await flush(); await language('en')
    b.release(); await flush(); const current = titles(); expect(current).toHaveLength(2); expect(current.every(x => x.startsWith('en '))).toBe(true)
    snapshot('B current success before A failure'); a.release(503); await flush(); snapshot('cancelled A nonOK after B success')
    expect(titles()).toEqual(current)
  })

  it('retains current A items in ABA after cancelled B completes with nonOK', async () => {
    const a = gate('zh'); const b = gate('en'); route = locale => locale === 'zh' ? a.promise : b.promise
    render(leaf(strict)); await flush(); await language('en'); await language('zh')
    a.release(); await flush(); const current = titles(); expect(current).toHaveLength(2); expect(current.every(x => x.startsWith('zh '))).toBe(true)
    snapshot('ABA current A reused request success'); b.release(503); await flush(); snapshot('cancelled B nonOK after current A success')
    expect(requests).toHaveLength(2); expect(titles()).toEqual(current)
  })

  it('control: current success selects exact idea and rotates locally without another fetch', async () => {
    render(leaf(strict)); await flush(); const current = titles(); expect(current).toHaveLength(2)
    const button = screen.getByRole('button', { name: current[0] }); fireEvent.click(button)
    expect(onSelect).toHaveBeenCalledWith(`《${current[0]}》：${button.textContent?.replace(/^1/, '').trim()}`)
    fireEvent.click(screen.getByRole('button', { name: 'Another batch' })); await flush()
    expect(titles()).toHaveLength(2); expect(titles()).not.toEqual(current); expect(requests).toHaveLength(1)
    expect(requests[0].cache).toBe('no-store'); snapshot('live local rotation')
  })

  it('control: latest own nonOK clears prior locale and hides the whole suggestion leaf', async () => {
    const b = gate('en'); route = locale => locale === 'en' ? b.promise : Promise.resolve(response(locale))
    render(leaf(strict)); await flush(); expect(titles()).toHaveLength(2); await language('en')
    b.release(503); await flush(); expect(titles()).toEqual([]); expect(screen.queryByTestId('dashboard-real-inspirations')).toBeNull()
    snapshot('latest own error empty policy')
  })

  it('control: same locale concurrent consumers and cached remount use one real request', async () => {
    const a = gate('zh'); route = () => a.promise
    const view = render(leaf(strict, 2)); await flush(); expect(requests).toHaveLength(1)
    a.release(); await flush(); expect(titles()).toHaveLength(4); view.unmount()
    render(leaf(strict)); await flush(); expect(titles()).toHaveLength(2); expect(requests).toHaveLength(1)
    snapshot('shared cache after remount')
  })

  it('control: unsupported tab makes no fetch, supported rerender loads current items', async () => {
    const wrapper = ({ children }: { children: React.ReactNode }) => {
      const content = <I18nextProvider i18n={translations}>{children}</I18nextProvider>
      return strict ? <StrictMode>{content}</StrictMode> : content
    }
    const view = renderHook(({ tab }) => useDashboardInspirations(tab, 2), { initialProps: { tab: 'unsupported' }, wrapper })
    await flush(); expect(view.result.current).toEqual([]); expect(requests).toHaveLength(0)
    view.rerender({ tab: 'novel' }); await flush(); expect(view.result.current).toHaveLength(2); expect(requests).toHaveLength(1)
    view.rerender({ tab: 'unsupported' }); await flush(); expect(view.result.current).toEqual([]); expect(requests).toHaveLength(1)
  })

  it('control: unmounted old locale consumer does not clear another live consumer', async () => {
    const a = gate('zh'); const b = gate('en'); route = locale => locale === 'zh' ? a.promise : b.promise
    const old = render(leaf(strict)); await flush(); old.unmount(); await language('en'); render(leaf(strict)); await flush()
    b.release(); await flush(); const current = titles(); a.release(503); await flush()
    expect(titles()).toEqual(current); expect(titles()).toHaveLength(2); expect(requests).toHaveLength(2)
  })

  it('characterization: owned nonOK caches null across later load and remount; no visible retry contract', async () => {
    route = async locale => response(locale, 503)
    const view = render(leaf(strict)); await flush(); expect(titles()).toEqual([]); expect(requests).toHaveLength(1)
    route = async locale => response(locale)
    expect(await source.loadDashboardInspirationBundle('zh')).toBeNull(); view.unmount(); render(leaf(strict)); await flush()
    expect(titles()).toEqual([]); expect(requests).toHaveLength(1); snapshot('cached null despite network recovery')
  })

  it('retains the loader rejection cache while the actual mounted leaf stays quiet', async () => {
    const a = gate('zh'); route = () => a.promise
    const initial = source.loadDashboardInspirationBundle('zh'); const rejection = expect(initial).rejects.toThrow('offline zh network rejection')
    a.reject(); await rejection
    route = async locale => response(locale)
    const cached = source.loadDashboardInspirationBundle('zh'); expect(cached).toBe(initial)
    await expect(cached).rejects.toThrow('offline zh network rejection')
    render(leaf(strict)); await flush(); expect(requests).toHaveLength(1); expect(titles()).toEqual([])
    snapshot('actual hook consumes cached rejected promise')
    expect(rejections).toEqual([])
  })

  it('handles an owned current fetch rejection quietly with empty suggestions and one request', async () => {
    const a = gate('zh'); route = () => a.promise
    render(leaf(strict)); await flush(); expect(requests).toHaveLength(1)
    a.reject(); await flush(); snapshot('current mounted fetch rejected')
    expect(titles()).toEqual([]); expect(requests).toHaveLength(1); expect(rejections).toEqual([])
  })

  it('keeps exact current B suggestions and stays quiet after cancelled A fetch rejects', async () => {
    const a = gate('zh'); const b = gate('en'); route = locale => locale === 'zh' ? a.promise : b.promise
    render(leaf(strict)); await flush(); await language('en')
    b.release(); await flush(); const current = titles(); expect(current).toHaveLength(2)
    expect(current.every(x => x.startsWith('en '))).toBe(true); snapshot('B success before cancelled A rejection')
    a.reject(); await flush(); snapshot('cancelled A rejected after B success')
    expect(titles()).toEqual(current); expect(requests).toHaveLength(2); expect(rejections).toEqual([])
  })

  it('keeps exact current A suggestions and stays quiet after cancelled B fetch rejects in ABA', async () => {
    const a = gate('zh'); const b = gate('en'); route = locale => locale === 'zh' ? a.promise : b.promise
    render(leaf(strict)); await flush(); await language('en'); await language('zh')
    a.release(); await flush(); const current = titles(); expect(current).toHaveLength(2)
    expect(current.every(x => x.startsWith('zh '))).toBe(true); snapshot('ABA reused A success before B rejection')
    b.reject(); await flush(); snapshot('cancelled B rejected after current A success')
    expect(titles()).toEqual(current); expect(requests).toHaveLength(2); expect(rejections).toEqual([])
  })
})
