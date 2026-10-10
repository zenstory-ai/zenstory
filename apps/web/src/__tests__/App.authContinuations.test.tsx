import { act, cleanup, configure, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// Offline translation startup runs during imports, before the per-case API fence.
// This file owns the boundary; the repository's shared setup remains unchanged.
vi.hoisted(() => {
  vi.stubGlobal('fetch', async (input: RequestInfo | URL) => {
    const path = new URL(String(input), window.location.origin).pathname;
    if (path.startsWith('/locales/')) {
      return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    throw new Error(`Unexpected request before offline fixture: ${path}`);
  });
});
import App from '../App'
import i18n from '../lib/i18n'
import { clearAuthStorage } from '../lib/apiClient'
import { takeAuthCallbackParams } from '../lib/authCallbackParams'
import { rememberSignedInUser } from '../lib/authFlow'
import { identifyUser, trackEvent } from '../lib/analytics'

// Actual App includes BrowserRouter, AuthProvider, AuthIdentityQueryBoundary,
// lazy pages, PublicRoute, ProtectedRoute and ProtectedProviders. No page,
// provider, router, helper or client mocks. Only network/analytics boundaries.
vi.mock('../lib/analytics', async importOriginal => ({
  ...(await importOriginal<typeof import('../lib/analytics')>()),
  identifyUser: vi.fn(), resetAnalytics: vi.fn(), trackEvent: vi.fn(), trackPageView: vi.fn(), captureException: vi.fn(),
}))
const locales = import.meta.glob<Record<string, unknown>>('../../public/locales/en/*.json', { eager: true, import: 'default' })
const target = 'https://app.zenstory.ai/offline-return'
function account(id: string, revision = '0') {
  return { access_token: `offline-access-${id}-${revision}`, refresh_token: `offline-refresh-${id}-${revision}`, user: {
    id, username: id, email: `${id}@example.invalid`, email_verified: true, is_active: true, is_superuser: false,
    created_at: '2020-01-01T00:00:00Z', updated_at: '2026-10-06T00:00:00Z',
  } }
}
const a = account('A'), rotatedA = account('A', '1'), b = account('B')
type Call = { path: string; method: string; authorization: string | null; body: unknown }
function json(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } }) }
let calls: Call[], unexpected: string[], hrefWrites: string[]
let pending: { promise: Promise<Response>; resolve: (reply: Response) => void; reject: (error: Error) => void }[]
let handler: (call: Call) => Promise<Response>
let queryClient: QueryClient
let expectedNetworkError: Error | null
function hold() {
  let resolve!: (reply: Response) => void, reject!: (error: Error) => void
  const promise = new Promise<Response>((accept, decline) => { resolve = accept; reject = decline })
  const value = { promise, resolve, reject }; pending.push(value); return value
}
async function settle(value: ReturnType<typeof hold>, reply: unknown, status = 200) { await act(async () => { value.resolve(json(reply, status)) }) }
function seed(value: typeof a, cached: boolean) {
  localStorage.setItem('access_token', value.access_token); localStorage.setItem('refresh_token', value.refresh_token)
  if (cached) { localStorage.setItem('user', JSON.stringify(value.user)); localStorage.setItem('auth_validated_at', String(Date.now())) }
}
function storage() { return Object.fromEntries(['access_token', 'refresh_token', 'user', 'auth_validated_at', 'zenstory_current_project_id:A', 'zenstory_current_project_id:B'].map(key => [key, localStorage.getItem(key)])) }
function callsTo(path: string) { return calls.filter(call => call.path === path) }
async function ordinary(call: Call) {
  if (call.method === 'GET' && call.path === '/api/v1/subscription/me') return json({ tier: 'free', features: { materials_library_access: false } })
  if (call.method === 'GET' && call.path === '/api/v1/projects') return json([])
  if (call.method === 'GET' && call.path === '/api/v1/project-templates') return json({})
  if (call.method === 'GET' && call.path === '/api/v1/subscription/catalog') return json({ version: 'offline', comparison_mode: 'outcome', pricing_anchor_monthly_cents: 0, tiers: [] })
  if (call.method === 'GET' && call.path === '/api/v1/subscription/quota') return json(Object.fromEntries(['ai_conversations', 'projects', 'material_uploads', 'material_decompositions', 'skill_creates', 'inspiration_copies'].map(key => [key, { used: 0, limit: 1, reset_at: null }])))
  if (call.method === 'GET' && call.path === '/api/v1/payments/options') return json({ enabled: false, payment_methods: [] })
  if (call.method === 'GET' && call.path === '/api/v1/persona/onboarding') return json({ required: false, profile: null, recommendations: [] })
  if (call.method === 'POST' && call.path === '/api/auth/logout') return json({})
  throw new Error(`Unrecognized actual App request: ${call.method} ${call.path}`)
}
function mount(strict: boolean, path: string, routeState?: object) {
  configure({ reactStrictMode: strict }); window.history.replaceState(routeState ? { usr: routeState } : {}, '', path)
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { retry: false } } })
  render(<QueryClientProvider client={queryClient}><App /></QueryClientProvider>)
}
function depart(path: string) { act(() => { window.history.pushState({}, '', path); window.dispatchEvent(new PopStateEvent('popstate')) }) }
async function login(id: string) {
  await waitFor(() => expect(screen.getByTestId('email-input')).toBeInTheDocument())
  fireEvent.change(screen.getByTestId('email-input'), { target: { value: id } })
  fireEvent.change(screen.getByTestId('password-input'), { target: { value: 'offline-password' } })
  await act(async () => { await userEvent.click(screen.getByTestId('login-submit')) })
}
function observe(scenario: string, strict: boolean, extra: Record<string, unknown> = {}) {
  console.info('AUTH_APP_OBSERVATION', JSON.stringify({ scenario, strict, path: window.location.pathname + window.location.search, hash: window.location.hash,
    storage: storage(), hrefWrites, calls, loginVisible: Boolean(screen.queryByTestId('login-form')), headings: screen.queryAllByRole('heading').map(value => value.textContent),
    identified: vi.mocked(identifyUser).mock.calls.map(([user]) => user.id), events: vi.mocked(trackEvent).mock.calls.map(([event]) => event),
    historyPushes: vi.mocked(window.history.pushState).mock.calls.map(([, , url]) => String(url)),
    historyReplaces: vi.mocked(window.history.replaceState).mock.calls.map(([, , url]) => String(url)),
    historyState: window.history.state, historyStates: vi.mocked(window.history.replaceState).mock.calls.map(([state, , url]) => ({ state, url })),
    queryKeys: queryClient.getQueryCache().getAll().map(query => query.queryKey), ...extra }))
}
beforeEach(async () => {
  vi.stubGlobal('localStorage', new window.Storage());
  vi.stubGlobal('sessionStorage', new window.Storage());
  calls = []; unexpected = []; pending = []; hrefWrites = []; expectedNetworkError = null; vi.clearAllMocks()
  expect(localStorage).toBeInstanceOf(Storage); expect(vi.isMockFunction(localStorage.getItem)).toBe(false)
  localStorage.clear(); sessionStorage.clear(); clearAuthStorage('offline-case-start'); takeAuthCallbackParams(); rememberSignedInUser(null)
  handler = ordinary
  vi.spyOn(window.history, 'pushState'); vi.spyOn(window.history, 'replaceState') // Call-through observation, real Router/history retained.
  vi.spyOn(window.location, 'href', 'set').mockImplementation(value => { hrefWrites.push(value) })
  vi.stubGlobal('fetch', async (input: RequestInfo | URL, options?: RequestInit) => {
    const path = new URL(input instanceof Request ? input.url : String(input), window.location.origin).pathname
    if (path.startsWith('/locales/')) return json(locales[`../../public/locales/en/${path.split('/').at(-1)}`] ?? {})
    const call = { path, method: options?.method ?? 'GET', authorization: new Headers(options?.headers).get('Authorization'), body: typeof options?.body === 'string' ? JSON.parse(options.body) : null }
    calls.push(call)
    try { return await handler(call) } catch (error) { if (error !== expectedNetworkError) unexpected.push(`${call.method} ${path}`); throw error }
  })
  for (const [path, data] of Object.entries(locales)) i18n.addResourceBundle('en', path.split('/').at(-1)!.replace('.json', ''), data, true, true)
  await i18n.changeLanguage('en')
})
afterEach(async () => {
  cleanup(); queryClient?.clear()
  expect(queryClient?.getQueryCache().getAll() ?? []).toEqual([])
  await act(async () => { pending.forEach(value => value.resolve(json({ detail: 'Offline teardown' }, 503))) })
  clearAuthStorage('offline-case-end'); localStorage.clear(); sessionStorage.clear(); takeAuthCallbackParams()
  configure({ reactStrictMode: false }); vi.restoreAllMocks(); vi.unstubAllGlobals()
  expect(unexpected).toEqual([])
  expect(localStorage.length).toBe(0); expect(sessionStorage.length).toBe(0)
})
// Start with real cold lazy callback under root StrictMode. Later mounts are
// explicitly warmed; the co-mounted startup case remains separately recorded.
for (const strict of [true, false]) {
  describe(`actual App continuation and eager-provider proof strict=${strict}`, () => {
    it('initial OAuth external callback (cold first lazy import; later warmed) establishes once', async () => {
      const delayed = hold(); handler = async call => call.path === '/api/auth/me' ? delayed.promise : ordinary(call)
      mount(strict, `/auth/callback#access_token=${a.access_token}&refresh_token=${a.refresh_token}&redirect=${encodeURIComponent(target)}`)
      await waitFor(() => expect(callsTo('/api/auth/me')).toHaveLength(1))
      expect(window.location.search).toBe(''); expect(window.location.hash).toBe('')
      await settle(delayed, a.user)
      observe('oauth-current-external', strict)
      expect.soft(localStorage.getItem('access_token')).toBe(a.access_token)
      expect.soft(hrefWrites).toEqual([`${target}?token=${a.access_token}`])
      expect.soft(vi.mocked(identifyUser).mock.calls.at(-1)?.[0].id).toBe('A')
    })
    it.each(['free', 'paid', 'external'] as const)('current OAuth %s with already-live App provider preserves success', async mode => {
      const delayed = hold()
      handler = async call => call.path === '/api/auth/me' ? delayed.promise : ordinary(call)
      mount(strict, '/privacy-policy')
      await waitFor(() => expect(screen.queryAllByRole('heading').length).toBeGreaterThan(0))
      if (mode === 'paid') sessionStorage.setItem('oauth_plan_intent', 'pro')
      depart(`/auth/callback?access_token=${a.access_token}&refresh_token=${a.refresh_token}${mode === 'external' ? `&redirect=${encodeURIComponent(target)}` : ''}`)
      await waitFor(() => expect(callsTo('/api/auth/me')).toHaveLength(1))
      expect(window.location.search).toBe(''); expect(window.location.hash).toBe('')
      await settle(delayed, a.user)
      await waitFor(() => expect(vi.mocked(identifyUser).mock.calls.at(-1)?.[0].id).toBe('A'))
      observe(`oauth-current-persisted-provider-${mode}`, strict)
      expect(localStorage.getItem('access_token')).toBe(a.access_token)
      expect(vi.mocked(identifyUser).mock.calls.at(-1)?.[0].id).toBe('A')
      if (mode === 'external') expect(hrefWrites).toEqual([`${target}?token=${a.access_token}`])
      if (mode === 'paid') { expect.soft(window.location.pathname).toBe('/dashboard/billing'); expect.soft(window.location.search).toBe('?plan=pro') }
      if (mode === 'free') expect(window.location.pathname).toBe('/dashboard')
    })
    it.each(['new B', 'page departure'] as const)('obsolete OAuth external after %s cannot export A token from actual App', async boundary => {
      const delayed = hold()
      handler = async call => {
        if (call.path === '/api/auth/me') return delayed.promise
        if (call.path === '/api/auth/login') return json(b)
        return ordinary(call)
      }
      mount(strict, `/auth/callback?access_token=${a.access_token}&refresh_token=${a.refresh_token}&redirect=${encodeURIComponent(target)}`)
      await waitFor(() => expect(callsTo('/api/auth/me')).toHaveLength(1))
      if (boundary === 'new B') { depart('/login'); await login('B'); await waitFor(() => expect(localStorage.getItem('access_token')).toBe(b.access_token)) }
      else { depart('/privacy-policy'); await waitFor(() => expect(window.location.pathname).toBe('/privacy-policy')) }
      const before = storage(), path = window.location.pathname
      await settle(delayed, a.user)
      observe(`oauth-stale-${boundary}`, strict, { before, pathBefore: path })
      expect.soft(hrefWrites).toEqual([])
      expect.soft(window.location.pathname).toBe(path)
      if (boundary === 'new B') expect.soft(storage()).toEqual(before)
    })
    it.each(['saved', 'recent'] as const)('Login identity self-unmount preserves healthy %s project continuation', async mode => {
      const list = hold(); let projects = 0
      handler = async call => {
        if (call.path === '/api/auth/login') return json(a)
        if (call.path === '/api/v1/projects') return ++projects === 1 ? list.promise : hold().promise
        return ordinary(call)
      }
      if (mode === 'saved') localStorage.setItem('zenstory_current_project_id:A', 'A-saved')
      mount(strict, '/login'); await login('A')
      await waitFor(() => expect(callsTo('/api/v1/projects').length).toBeGreaterThanOrEqual(1))
      await settle(list, [{ id: 'A-saved', created_at: '2020-01-01' }, { id: 'A-recent', updated_at: '2026-10-06' }])
      // Observe current behavior rather than assuming PublicRoute and identity
      // boundary should cancel the legitimate successful-login continuation.
      await waitFor(() => expect(window.location.pathname).not.toBe('/login'))
      observe(`login-current-after-identity-remount-${mode}`, strict)
      expect(localStorage.getItem('access_token')).toBe(a.access_token)
      expect(vi.mocked(identifyUser).mock.calls.at(-1)?.[0].id).toBe('A')
      expect(window.location.pathname).toBe(mode === 'saved' ? '/project/A-saved' : '/project/A-recent')
      expect(window.history.state?.usr?.zenstoryLoginAttempt).toBeUndefined()
    })
    it.each(['paid', 'deep-link'] as const)('Login current %s intent under actual identity boundary', async mode => {
      handler = async call => call.path === '/api/auth/login' ? json(a) : ordinary(call)
      mount(strict, mode === 'paid' ? '/login?plan=pro' : '/login', mode === 'deep-link' ? { from: { pathname: '/dashboard/projects', search: '?source=homepage', hash: '#intent', state: { authorIntent: true } } } : undefined)
      await login('A')
      await waitFor(() => expect(vi.mocked(identifyUser).mock.calls.at(-1)?.[0].id).toBe('A'))
      observe(`login-current-intent-${mode}`, strict)
      expect(localStorage.getItem('access_token')).toBe(a.access_token)
      expect(window.location.pathname + window.location.search + window.location.hash).toBe(mode === 'paid' ? '/dashboard/billing?plan=pro' : '/dashboard/projects?source=homepage#intent')
      expect(window.history.state?.usr?.zenstoryLoginAttempt).toBeUndefined()
      if (mode === 'deep-link') expect(window.history.state.usr).toEqual({ authorIntent: true })
    })
    // 同一浏览器登出 A 再登入 B：ProtectedRoute 留下的 A 回跳目标不能把 B 送进 A 的页面；
    // 同账号重新登录仍回到原深链接。走真实身份边界重挂（AuthIdentityQueryBoundary）。
    it.each(['B', 'A'] as const)('logout inside A protected page then Login as %s only restores an A-owned return target', async next => {
      let logins = 0
      handler = async call => call.path === '/api/auth/login' ? json(++logins === 1 ? a : next === 'B' ? b : a) : ordinary(call)
      mount(strict, '/login'); await login('A')
      await waitFor(() => expect(window.location.pathname).toBe('/dashboard'))
      depart('/dashboard/projects?owner=A#recent')
      await waitFor(() => expect(screen.queryByTestId('login-form')).not.toBeInTheDocument())
      act(() => clearAuthStorage('offline-explicit-logout'))
      await waitFor(() => expect(screen.getByTestId('login-form')).toBeInTheDocument())
      const returnState = window.history.state?.usr
      await login(next)
      await waitFor(() => expect(vi.mocked(identifyUser).mock.calls.at(-1)?.[0].id).toBe(next))
      await waitFor(() => expect(window.history.state?.usr?.zenstoryLoginAttempt).toBeUndefined())
      observe(`relogin-return-target-${next}`, strict, { returnState })
      expect(localStorage.getItem('access_token')).toBe(next === 'B' ? b.access_token : a.access_token)
      expect(window.location.pathname + window.location.search + window.location.hash)
        .toBe(next === 'B' ? '/dashboard' : '/dashboard/projects?owner=A#recent')
      expect(returnState?.from?.pathname).toBe('/dashboard/projects')
      expect(returnState?.fromUserId).toBe('A')
    })
    it.each(['logout', 'new B', 'page departure'] as const)('obsolete Login list after %s cannot replace current App route', async boundary => {
      const list = hold(); let projects = 0, logins = 0
      handler = async call => {
        if (call.path === '/api/auth/login') return json(++logins === 1 ? a : b)
        if (call.path === '/api/v1/projects') return ++projects === 1 ? list.promise : hold().promise
        return ordinary(call)
      }
      mount(strict, '/login'); await login('A')
      await waitFor(() => expect(callsTo('/api/v1/projects').length).toBeGreaterThanOrEqual(1))
      if (boundary === 'new B') {
        // Actual clear primitive emits auth:logout; then actual page/provider
        // command establishes B. No fake context or arbitrary token replacement.
        act(() => clearAuthStorage('offline-explicit-logout')); depart('/login'); await login('B')
        await waitFor(() => expect(localStorage.getItem('access_token')).toBe(b.access_token))
      } else if (boundary === 'logout') { act(() => clearAuthStorage('offline-explicit-logout')); await waitFor(() => expect(screen.getByTestId('login-form')).toBeInTheDocument()) }
      else depart('/privacy-policy')
      const before = storage(), path = window.location.pathname, pushes = vi.mocked(window.history.pushState).mock.calls.length
      await settle(list, [{ id: 'A-obsolete', updated_at: '2026-10-06' }])
      observe(`login-stale-${boundary}`, strict, { before, pathBefore: path })
      expect.soft(window.location.pathname).toBe(path)
      expect.soft(vi.mocked(window.history.pushState).mock.calls.slice(pushes).map(([, , url]) => String(url))).not.toContain('/project/A-obsolete')
      expect.soft(storage()).toEqual(before)
    })
    it.each(['logout', 'new B', 'page departure', 'manual dashboard'] as const)('obsolete Login rejected list after %s cannot use fallback navigation', async boundary => {
      const list = hold(); let projects = 0, logins = 0
      handler = async call => {
        if (call.path === '/api/auth/login') return json(++logins === 1 ? a : b)
        if (call.path === '/api/v1/projects') return ++projects === 1 ? list.promise : hold().promise
        return ordinary(call)
      }
      mount(strict, '/login'); await login('A')
      await waitFor(() => expect(callsTo('/api/v1/projects').length).toBeGreaterThanOrEqual(1))
      if (boundary === 'new B') {
        act(() => clearAuthStorage('offline-explicit-logout')); depart('/login'); await login('B')
        await waitFor(() => expect(localStorage.getItem('access_token')).toBe(b.access_token))
      } else if (boundary === 'logout') {
        act(() => clearAuthStorage('offline-explicit-logout'))
        await waitFor(() => expect(screen.getByTestId('login-form')).toBeInTheDocument())
      } else depart(boundary === 'manual dashboard' ? '/dashboard' : '/privacy-policy')
      const before = storage(), path = window.location.pathname
      const pushes = vi.mocked(window.history.pushState).mock.calls.length
      const replaces = vi.mocked(window.history.replaceState).mock.calls.length
      await settle(list, { detail: 'Offline list failed' }, 503)
      observe(`login-rejected-stale-${boundary}`, strict, { before })
      expect(window.location.pathname).toBe(path)
      expect(vi.mocked(window.history.pushState).mock.calls.slice(pushes)).toEqual([])
      expect(vi.mocked(window.history.replaceState).mock.calls.slice(replaces)).toEqual([])
      expect(storage()).toEqual(before)
    })
    it('manual dashboard departure removes authority for old successful Login list', async () => {
      const list = hold(); let projects = 0
      handler = async call => {
        if (call.path === '/api/auth/login') return json(a)
        if (call.path === '/api/v1/projects') return ++projects === 1 ? list.promise : hold().promise
        return ordinary(call)
      }
      mount(strict, '/login'); await login('A')
      await waitFor(() => expect(callsTo('/api/v1/projects').length).toBeGreaterThanOrEqual(1))
      depart('/dashboard'); const before = storage()
      await settle(list, [{ id: 'A-obsolete', updated_at: '2026-10-06' }])
      expect(window.location.pathname).toBe('/dashboard'); expect(storage()).toEqual(before)
    })
    it('Login list continuation accepts its actual shared-refresh descendant', async () => {
      const list = hold(); let projects = 0
      handler = async call => {
        if (call.path === '/api/auth/login') return json(a)
        if (call.path === '/api/v1/projects') return ++projects === 1 ? list.promise : hold().promise
        if (call.path === '/api/auth/refresh') return json(rotatedA)
        if (call.path === '/api/v1/subscription/me' && call.authorization === `Bearer ${a.access_token}`) return json({ detail: 'Offline rotation' }, 401)
        return ordinary(call)
      }
      mount(strict, '/login'); await login('A')
      await waitFor(() => expect(localStorage.getItem('access_token')).toBe(rotatedA.access_token))
      await settle(list, [{ id: 'A-recent', updated_at: '2026-10-06' }])
      expect(window.location.pathname).toBe('/project/A-recent')
      expect(localStorage.getItem('access_token')).toBe(rotatedA.access_token)
      expect(callsTo('/api/auth/refresh')).toHaveLength(1)
    })
    it.each([401, 403, 503])('obsolete original init status %s cannot clear its healthy descendant', async status => {
      const initial = hold(); seed(a, true)
      handler = async call => {
        if (call.path === '/api/auth/me') return initial.promise
        if (call.path === '/api/auth/refresh') return json(rotatedA)
        if (call.path === '/api/v1/subscription/me' && call.authorization === `Bearer ${a.access_token}`) return json({ detail: 'Offline rotation' }, 401)
        return ordinary(call)
      }
      mount(strict, '/dashboard/projects')
      await waitFor(() => expect(localStorage.getItem('access_token')).toBe(rotatedA.access_token))
      await waitFor(() => expect(vi.mocked(identifyUser).mock.calls.at(-1)?.[0].id).toBe('A'))
      const before = storage()
      await settle(initial, { detail: 'Obsolete A0 failure' }, status)
      expect(storage()).toEqual(before); expect(window.location.pathname).toBe('/dashboard/projects')
      expect(vi.mocked(identifyUser).mock.calls.at(-1)?.[0].id).toBe('A')
      expect(callsTo('/api/auth/refresh')).toHaveLength(1)
    })
    it.each([false, true])('A initialization ownership across actual eager subscription 401 rotation cached=%s', async cached => {
      const initial = hold()
      seed(a, cached)
      handler = async call => {
        if (call.path === '/api/auth/me') return initial.promise
        if (call.path === '/api/auth/refresh') return json(rotatedA)
        if (call.path === '/api/v1/subscription/me' && call.authorization === `Bearer ${a.access_token}`) return json({ detail: 'Offline A rotation required' }, 401)
        return ordinary(call)
      }
      mount(strict, '/dashboard/projects')
      await waitFor(() => expect(callsTo('/api/auth/me').length).toBeGreaterThanOrEqual(strict ? 2 : 1))
      await waitFor(() => expect(callsTo('/api/auth/refresh')).toHaveLength(1))
      await waitFor(() => expect(localStorage.getItem('access_token')).toBe(rotatedA.access_token))
      observe('eager-subscription-before-init-completion', strict, { cached, meStillPending: true })
      await settle(initial, a.user)
      await waitFor(() => expect(callsTo('/api/v1/subscription/me').some(call => call.authorization === `Bearer ${rotatedA.access_token}`)).toBe(true))
      observe('eager-subscription-after-init-completion', strict, { cached })
      expect.soft(localStorage.getItem('access_token')).toBe(rotatedA.access_token)
      expect.soft(window.location.pathname).toBe('/dashboard/projects')
      expect.soft(screen.queryByTestId('login-form')).not.toBeInTheDocument()
      expect.soft(vi.mocked(identifyUser).mock.calls.at(-1)?.[0].id).toBe('A')
    })
    it.each([false, true])('original init network failure cannot clear A1 tokens/cache cached=%s', async cached => {
      const initial = hold(); seed(a, cached)
      handler = async call => {
        if (call.path === '/api/auth/me') return initial.promise
        if (call.path === '/api/auth/refresh') return json(rotatedA)
        if (call.path === '/api/v1/subscription/me' && call.authorization === `Bearer ${a.access_token}`) return json({ detail: 'Offline rotation' }, 401)
        return ordinary(call)
      }
      mount(strict, '/dashboard/projects')
      await waitFor(() => expect(localStorage.getItem('access_token')).toBe(rotatedA.access_token))
      const before = storage()
      expectedNetworkError = new TypeError('Owned offline /me rejection')
      await act(async () => { initial.reject(expectedNetworkError!) })
      expect(storage()).toEqual(before)
      if (cached) expect(window.location.pathname).toBe('/dashboard/projects')
      expect(callsTo('/api/auth/refresh')).toHaveLength(1)
    })
    it.each([401, 403, 503])('uncached original init failure %s does not erase the A1 cache/token', async status => {
      const initial = hold(); seed(a, false)
      handler = async call => {
        if (call.path === '/api/auth/me') return initial.promise
        if (call.path === '/api/auth/refresh') return json(rotatedA)
        if (call.path === '/api/v1/subscription/me' && call.authorization === `Bearer ${a.access_token}`) return json({ detail: 'Offline rotation' }, 401)
        return ordinary(call)
      }
      mount(strict, '/dashboard/projects')
      await waitFor(() => expect(localStorage.getItem('access_token')).toBe(rotatedA.access_token))
      const before = storage()
      await settle(initial, { detail: 'Obsolete uncached init failure' }, status)
      expect(storage()).toEqual(before); expect(callsTo('/api/auth/refresh')).toHaveLength(1)
    })
    it('unrelated B actual Login owns session while cached A background init remains pending', async () => {
      const initial = hold(); seed(a, true)
      handler = async call => {
        if (call.path === '/api/auth/me') return initial.promise
        if (call.path === '/api/auth/login') return json(b)
        return ordinary(call)
      }
      mount(strict, '/dashboard/projects')
      await waitFor(() => expect(vi.mocked(identifyUser).mock.calls.some(([user]) => user.id === 'A')).toBe(true))
      act(() => clearAuthStorage('offline-explicit-logout')); depart('/login'); await login('B')
      await waitFor(() => expect(localStorage.getItem('access_token')).toBe(b.access_token))
      const before = storage()
      await settle(initial, a.user)
      observe('cached-background-old-A-after-current-B', strict, { before })
      expect(storage()).toEqual(before)
      expect(vi.mocked(identifyUser).mock.calls.at(-1)?.[0].id).toBe('B')
      expect(screen.queryByTestId('login-form')).not.toBeInTheDocument()
    })
    it('current uncached init without an eager 401 authenticates normally', async () => {
      const initial = hold(); seed(a, false)
      handler = async call => call.path === '/api/auth/me' ? initial.promise : ordinary(call)
      mount(strict, '/dashboard/projects')
      await waitFor(() => expect(callsTo('/api/auth/me').length).toBeGreaterThanOrEqual(strict ? 2 : 1))
      expect(callsTo('/api/auth/refresh')).toHaveLength(0)
      expect(screen.queryByTestId('login-form')).not.toBeInTheDocument()
      await settle(initial, a.user)
      await waitFor(() => expect(vi.mocked(identifyUser).mock.calls.at(-1)?.[0].id).toBe('A'))
      observe('current-uncached-init-control', strict)
      expect(window.location.pathname).toBe('/dashboard/projects')
      expect(localStorage.getItem('access_token')).toBe(a.access_token)
    })
    it('uncached pending init blocks real Login until its transient response settles; current B succeeds', async () => {
      const initial = hold(); seed(a, false)
      handler = async call => {
        if (call.path === '/api/auth/me') return initial.promise
        if (call.path === '/api/auth/login') return json(b)
        return ordinary(call)
      }
      mount(strict, '/dashboard/projects')
      await waitFor(() => expect(callsTo('/api/auth/me').length).toBeGreaterThanOrEqual(strict ? 2 : 1))
      depart('/login')
      // Current init owns global loading, so finish it with a transient status
      // before the actual Login can mount. This proves reachability separately
      // from a fabricated login command under a loading guard.
      await settle(initial, { detail: 'Offline transient A init failure' }, 503)
      await login('B'); await waitFor(() => expect(localStorage.getItem('access_token')).toBe(b.access_token))
      observe('current-B-after-A-init', strict)
      expect(localStorage.getItem('access_token')).toBe(b.access_token)
      expect(vi.mocked(identifyUser).mock.calls.at(-1)?.[0].id).toBe('B')
    })
  })
}
