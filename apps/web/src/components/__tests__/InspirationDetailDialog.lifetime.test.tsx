import { StrictMode, type ReactNode } from 'react'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import i18next, { type i18n as I18n } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { InspirationGrid } from '../inspirations/InspirationGrid'
import { clearAuthStorage, getApiBase } from '../../lib/apiClient'
import type { InspirationDetail } from '../../types'
import InspirationDetailPage from '../../pages/InspirationDetailPage'

// Actual parents/hooks/API/client/Router/QueryClient/Modal/i18n. Only the fetch
// response boundary and peripheral notification/SDK effects are replaced.
const effects = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), analytics: vi.fn() }))
vi.mock('../../lib/toast', () => ({ toast: { success: effects.success, error: effects.error, info: vi.fn() } }))
vi.mock('../../lib/analytics', () => ({ trackEvent: effects.analytics, captureException: effects.analytics }))

type Request = { url: string; path: string; query: Record<string, string>; method: string; authorization: string | null; body?: unknown; status?: number }
type Gate = { settled: () => boolean; release: () => void }
let queryClient: QueryClient
let translations: I18n
let requests: Request[]
let unexpected: string[]
let gates: Gate[]
let pending: Set<Promise<Response>>
let route: (request: Request) => Promise<Response>
let router: ReturnType<typeof createMemoryRouter>
let unsubscribe: () => void
let routes: { path: string; action: string }[]
let checkpoints: unknown[]
let originalOverflow: string

function detail(id: 'a' | 'b'): InspirationDetail {
  return { id, name: `Template ${id.toUpperCase()}`, description: `Local template ${id}`,
    cover_image: null, project_type: 'novel', tags: ['local'], source: 'official',
    author_id: null, original_project_id: null, copy_count: 1, is_featured: false,
    created_at: '2026-10-06T00:00:00Z', file_preview: [{ title: `Draft ${id}`, file_type: 'draft', has_content: true }] }
}
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
const copied = () => json({ success: true, message: 'Fake copy committed', project_id: 'new-a', project_name: 'A copied project' })
const denied = () => json({ detail: 'Copy fixture denied' }, 500)
function deferred(fallback: Response) {
  let fulfill!: (value: Response) => void
  let done = false
  const promise = new Promise<Response>(resolve => { fulfill = value => { if (!done) { done = true; resolve(value) } } })
  gates.push({ settled: () => done, release: () => fulfill(fallback) })
  return { promise, resolve: fulfill }
}
async function ordinary(request: Request): Promise<Response> {
  if (request.path === '/api/v1/inspirations/featured') return json([])
  if (request.path === '/api/v1/inspirations') return json({ inspirations: [detail('a'), detail('b')], total: 2, page: Number(request.query.page ?? 1), page_size: Number(request.query.page_size ?? 12) })
  if (request.path === '/api/v1/inspirations/a') return json(detail('a'))
  if (request.path === '/api/v1/inspirations/b') return json(detail('b'))
  if (request.path === '/api/v1/inspirations/a/copy') return copied()
  unexpected.push(request.url)
  throw new Error(`OFFLINE: unexpected ${request.url}`)
}
async function flush() {
  await act(async () => {
    for (let index = 0; index < 12; index++) await Promise.resolve()
    await vi.advanceTimersByTimeAsync(0)
  })
}
async function advance(milliseconds: number) {
  await act(async () => { await vi.advanceTimersByTimeAsync(milliseconds) })
  await flush()
}
function snapshot(label: string) {
  const dialog = screen.queryByRole('dialog')
  const state = { label, path: router?.state.location.pathname, dialogs: screen.queryAllByRole('dialog').length,
    title: dialog?.textContent, projectName: dialog?.querySelector('input')?.value,
    timers: vi.getTimerCount(), copyCalls: requests.filter(item => item.method === 'POST').length,
    successes: effects.success.mock.calls, errors: effects.error.mock.calls }
  checkpoints.push(state)
  return state
}
function wrap(strict: boolean, content: ReactNode) {
  const tree = <I18nextProvider i18n={translations}><QueryClientProvider client={queryClient}>{content}</QueryClientProvider></I18nextProvider>
  return strict ? <StrictMode>{tree}</StrictMode> : tree
}
function mountGrid(strict: boolean, projectType = 'novel') {
  router = createMemoryRouter([{ path: '/dashboard/inspirations', element: <InspirationGrid projectType={projectType} /> }], { initialEntries: ['/dashboard/inspirations'] })
  routes = [{ path: router.state.location.pathname, action: router.state.historyAction }]
  unsubscribe = router.subscribe(state => routes.push({ path: state.location.pathname, action: state.historyAction }))
  return render(wrap(strict, <RouterProvider router={router} />))
}
function view(id: 'a' | 'b') { fireEvent.click(screen.getAllByRole('button', { name: `View：Template ${id.toUpperCase()}` })[0]) }
function close() { fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Close modal' })) }
function copy(name = 'A copied project') {
  const dialog = within(screen.getByRole('dialog'))
  fireEvent.change(dialog.getByRole('textbox'), { target: { value: name } })
  fireEvent.click(dialog.getByRole('button', { name: 'Copy template' }))
}
function expectB(name = '') {
  const dialog = screen.queryByRole('dialog')
  expect(dialog).not.toBeNull()
  expect(within(dialog!).getByText('Template B')).toBeTruthy()
  expect((within(dialog!).getByRole('textbox') as HTMLInputElement).value).toBe(name)
}

beforeEach(async () => {
  vi.clearAllMocks()
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
  vi.setSystemTime(new Date('2026-10-06T12:00:00Z'))
  originalOverflow = document.body.style.overflow
  // Self-contained under repository setup, which otherwise installs a Storage mock.
  vi.stubGlobal('localStorage', new window.Storage())
  vi.stubGlobal('sessionStorage', new window.Storage())
  localStorage.clear(); sessionStorage.clear()
  expect(localStorage).toBe(window.localStorage)
  expect(localStorage).toBeInstanceOf(window.Storage)
  expect(vi.isMockFunction(localStorage.getItem)).toBe(false)
  localStorage.setItem('access_token', 'fake-local-access-a')
  localStorage.setItem('refresh_token', 'fake-local-refresh-a')
  localStorage.setItem('zenstory-language', 'en')
  requests = []; unexpected = []; gates = []; pending = new Set(); routes = []; checkpoints = []
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { retry: false, gcTime: Infinity } } })
  translations = i18next.createInstance()
  await translations.init({ lng: 'en', fallbackLng: 'en', defaultNS: 'inspirations', ns: ['inspirations', 'common'],
    resources: { en: { inspirations: { view: 'View', use: 'Use', useThis: 'Copy template', copied: 'Copied', copying: 'Copying', cancel: 'Cancel', copySuccess: 'Copy success', copyError: 'Copy error', searchPlaceholder: 'Search templates', search: 'Search', pagination: { next: 'Next page', previous: 'Previous page' } }, common: { closeModal: 'Close modal' } } }, react: { useSuspense: false }, interpolation: { escapeValue: false } })
  route = ordinary
  vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input)); const headers = new Headers(init?.headers)
    const request: Request = { url: String(input), path: url.pathname, query: Object.fromEntries(url.searchParams), method: init?.method ?? 'GET', authorization: headers.get('Authorization'), body: typeof init?.body === 'string' ? JSON.parse(init.body) : undefined }
    if (url.origin !== new URL(getApiBase()).origin || !/^\/api\/v1\/inspirations(?:\/(?:featured|a|b)(?:\/copy)?)?$/.test(request.path)) {
      unexpected.push(request.url); return Promise.reject(new Error(`OFFLINE: denied ${request.url}`))
    }
    requests.push(request)
    const response = route(request).then(value => { request.status = value.status; return value })
    pending.add(response)
    void response.then(() => pending.delete(response), () => pending.delete(response))
    return response
  })
})

afterEach(async () => {
  const unsettledAtAssertion = gates.filter(gate => !gate.settled()).length
  for (const gate of gates) gate.release()
  await flush()
  snapshot('after assertion / before owned cleanup')
  cleanup()
  await queryClient.cancelQueries(); queryClient.clear()
  await flush()
  await Promise.allSettled([...pending])
  const remainingTimersAfterUnmount = vi.getTimerCount()
  // Only this fixture's controlled timers exist; no shared timer/service is cleared.
  vi.clearAllTimers()
  const routeEventsBeforeEscape = routes.length
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
  expect(routes.length).toBe(routeEventsBeforeEscape)
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(document.body.style.overflow).toBe(originalOverflow)
  unsubscribe?.(); router?.dispose(); translations.off('languageChanged')
  clearAuthStorage('owned_inspiration_fixture_cleanup'); localStorage.clear(); sessionStorage.clear()
  expect(localStorage.length).toBe(0); expect(sessionStorage.length).toBe(0)
  console.info('M19_DIALOG_OBSERVATION', JSON.stringify({ case: expect.getState().currentTestName, requests, routes, checkpoints,
    successes: effects.success.mock.calls, errors: effects.error.mock.calls,
    cleanup: { unsettledAtAssertion, pending: pending.size, unexpected, remainingTimersAfterUnmount, timersAfterClear: vi.getTimerCount(), bodyOverflowRestored: document.body.style.overflow === originalOverflow, dialogsAfterUnmount: screen.queryAllByRole('dialog').length } }))
  vi.unstubAllGlobals(); vi.useRealTimers()
  expect(unexpected).toEqual([]); expect(unsettledAtAssertion).toBe(0); expect(pending.size).toBe(0)
})

function mountPage(strict: boolean) {
  router = createMemoryRouter([
    { path: '/dashboard/inspirations', element: <InspirationGrid /> },
    { path: '/dashboard/inspirations/:inspirationId', element: <InspirationDetailPage /> },
    { path: '/project/:id', element: <p>New project workspace route</p> },
  ], { initialEntries: ['/dashboard/inspirations', '/dashboard/inspirations/a'], initialIndex: 1 })
  routes = [{ path: router.state.location.pathname, action: router.state.historyAction }]
  unsubscribe = router.subscribe(state => routes.push({ path: state.location.pathname, action: state.historyAction }))
  return render(wrap(strict, <RouterProvider router={router} />))
}

describe.each([false, true])('actual parent/dialog copy lifetime rootStrict=%s', strict => {
  it('retains successful routed copy destination after obsolete dialog 1500ms callback', async () => {
    const result = deferred(copied())
    route = request => request.path.endsWith('/copy') ? result.promise : ordinary(request)
    mountPage(strict); await flush()
    expect(screen.getByRole('dialog')).toBeTruthy()
    copy(); await flush()
    expect(requests.filter(item => item.method === 'POST')).toHaveLength(1)
    expect(requests.find(item => item.method === 'POST')?.body).toEqual({ project_name: 'A copied project' })
    result.resolve(copied()); await flush()
    expect(router.state.location.pathname).toBe('/project/new-a')
    expect(effects.success).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('dialog')).toBeNull()
    snapshot('copy committed and actual parent navigated')
    await advance(1500)
    snapshot('after old dialog delay')
    expect(router.state.location.pathname).toBe('/project/new-a')
    expect(requests.filter(item => item.method === 'POST')).toHaveLength(1)
  })

  it('control: routed API copy failure retains current dialog without success or delayed navigation', async () => {
    const result = deferred(denied())
    route = request => request.path.endsWith('/copy') ? result.promise : ordinary(request)
    mountPage(strict); await flush(); copy(); await flush()
    result.resolve(denied()); await flush(); await advance(1500)
    expect(router.state.location.pathname).toBe('/dashboard/inspirations/a')
    expect(screen.getByRole('dialog')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Copied' })).toBeNull()
    expect((screen.getByRole('button', { name: 'Copy template' }) as HTMLButtonElement).disabled).toBe(false)
    expect(effects.success).not.toHaveBeenCalled(); expect(effects.error).toHaveBeenCalledTimes(1)
  })

  it('control: current live grid copy shows success and closes only at 1500ms', async () => {
    const result = deferred(copied())
    route = request => request.path.endsWith('/copy') ? result.promise : ordinary(request)
    mountGrid(strict); await flush(); view('a'); await flush(); copy(); await flush()
    expect((screen.getByRole('button', { name: 'Copying' }) as HTMLButtonElement).disabled).toBe(true)
    result.resolve(copied()); await flush()
    expect((screen.getByRole('button', { name: 'Copied' }) as HTMLButtonElement).disabled).toBe(true)
    await advance(1499); expect(screen.getByRole('dialog')).toBeTruthy()
    await advance(1); expect(screen.queryByRole('dialog')).toBeNull()
    expect(effects.success).toHaveBeenCalledTimes(1); expect(effects.error).not.toHaveBeenCalled()
    expect(requests.filter(item => item.method === 'POST')).toHaveLength(1)
  })

  it('control: current grid copy failure retains dialog without false success or delayed close', async () => {
    const result = deferred(denied())
    route = request => request.path.endsWith('/copy') ? result.promise : ordinary(request)
    mountGrid(strict); await flush(); view('a'); await flush(); copy(); await flush()
    result.resolve(denied()); await flush(); await advance(1500)
    expect(screen.getByRole('dialog')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Copied' })).toBeNull()
    expect((screen.getByRole('button', { name: 'Copy template' }) as HTMLButtonElement).disabled).toBe(false)
    expect(effects.success).not.toHaveBeenCalled(); expect(effects.error).toHaveBeenCalledTimes(1)
    expect(requests.filter(item => item.method === 'POST')).toHaveLength(1)
  })

  it('keeps B open when completed A copy timer outlives closing A and selecting B', async () => {
    mountGrid(strict); await flush(); view('a'); await flush(); copy(); await flush()
    expect(screen.getByRole('button', { name: 'Copied' })).toBeTruthy()
    close(); await flush(); view('b'); await flush()
    fireEvent.change(within(screen.getByRole('dialog')).getByRole('textbox'), { target: { value: 'B own name' } })
    expectB('B own name'); snapshot('A completed, B current before old timer')
    await advance(1500); snapshot('B after obsolete A completion timer')
    expectB('B own name')
    expect(requests.filter(item => item.method === 'POST')).toHaveLength(1)
    expect(effects.success).toHaveBeenCalledTimes(1)
  })

  it('keeps B open after pending A copy completes following A close and B selection', async () => {
    const result = deferred(copied())
    route = request => request.path.endsWith('/copy') ? result.promise : ordinary(request)
    mountGrid(strict); await flush(); view('a'); await flush(); copy(); await flush()
    close(); await flush(); view('b'); await flush(); expectB()
    result.resolve(copied()); await flush()
    expectB(); expect(screen.queryByRole('button', { name: 'Copied' })).toBeNull()
    expect(effects.success).toHaveBeenCalledTimes(1); snapshot('old A server copy complete, current B')
    await advance(1500); snapshot('B after timer from pending A completion')
    expectB()
    expect(requests.filter(item => item.method === 'POST')).toHaveLength(1)
  })

  it('control: actual grid remount B survives completion of old unmounted A copy', async () => {
    const result = deferred(copied())
    route = request => request.path.endsWith('/copy') ? result.promise : ordinary(request)
    const first = mountGrid(strict); await flush(); view('a'); await flush(); copy(); await flush()
    first.unmount(); unsubscribe(); router.dispose()
    mountGrid(strict); await flush(); view('b'); await flush(); expectB()
    result.resolve(copied()); await flush(); await advance(1500)
    expectB(); expect(requests.filter(item => item.method === 'POST')).toHaveLength(1)
    expect(effects.success).toHaveBeenCalledTimes(1)
  })
})
