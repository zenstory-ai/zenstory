import { createElement } from 'react'
import { act, cleanup, configure, fireEvent, render, screen, waitFor } from '@testing-library/react'
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
import { apiCall, clearAuthStorage, resolveOwnedAuthSession, tryRefreshToken } from '../apiClient'
import { handleSsoRedirect } from '../ssoRedirect'
import i18n from '../i18n'

vi.mock('../analytics', async importOriginal => ({
  ...(await importOriginal<typeof import('../analytics')>()),
  identifyUser: vi.fn(), resetAnalytics: vi.fn(), trackEvent: vi.fn(),
  trackPageView: vi.fn(), captureException: vi.fn(),
}))

const locales = import.meta.glob<Record<string, unknown>>('../../../public/locales/en/*.json', { eager: true, import: 'default' })
const target = 'https://app.zenstory.ai/offline-callback'
type Session = ReturnType<typeof session>
type Call = { path: string; method: string; authorization: string | null; body: string | null }
let caseId = 0
let a: Session
let b: Session
let calls: Call[] = []
let pending: Array<ReturnType<typeof deferred<Response>>> = []
let request: (call: Call) => Promise<Response>
let queryClient: QueryClient | undefined

function session(id: string, suffix = '0') {
  return {
    access_token: `offline-${id}-${caseId}-${suffix}-access`,
    refresh_token: `offline-${id}-${caseId}-${suffix}-refresh`,
    user: { id, username: id, email: `${id}@example.invalid`, email_verified: true,
      is_active: true, is_superuser: false, created_at: '2026-10-06T00:00:00Z', updated_at: '2026-10-06T00:00:00Z' },
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(accept => { resolve = accept })
  return { promise, resolve }
}

function hold() {
  const value = deferred<Response>()
  pending.push(value)
  return value
}

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } })
}

function seed(value: Session) {
  localStorage.setItem('access_token', value.access_token)
  localStorage.setItem('refresh_token', value.refresh_token)
  localStorage.setItem('user', JSON.stringify(value.user))
  localStorage.setItem('auth_validated_at', String(Date.now()))
}

function snapshot() {
  return Object.fromEntries(['access_token', 'refresh_token', 'user', 'auth_validated_at'].map(key => [key, localStorage.getItem(key)]))
}

beforeEach(async () => {
  vi.stubGlobal('localStorage', new window.Storage());
  vi.stubGlobal('sessionStorage', new window.Storage());
  vi.clearAllMocks()
  caseId += 1
  a = session('account-a')
  b = session('account-b')
  calls = []
  pending = []
  clearAuthStorage('offline-case-reset')
  sessionStorage.clear()
  expect(localStorage).toBeInstanceOf(Storage)
  expect(vi.isMockFunction(localStorage.setItem)).toBe(false)
  request = async call => { throw new Error(`Unexpected offline API: ${call.path}`) }
  vi.stubGlobal('fetch', async (input: RequestInfo | URL, options?: RequestInit) => {
    const url = new URL(String(input), window.location.origin)
    if (url.pathname.startsWith('/locales/')) {
      const ns = url.pathname.split('/').at(-1)
      return json(locales[`../../../public/locales/en/${ns}`] ?? {})
    }
    const body = options?.body instanceof FormData
      ? JSON.stringify(Object.fromEntries(options.body.entries()))
      : typeof options?.body === 'string' ? options.body : null
    const call = { path: url.pathname, method: options?.method ?? 'GET', authorization: new Headers(options?.headers).get('Authorization'), body }
    calls.push(call)
    return request(call)
  })
  for (const [path, data] of Object.entries(locales)) {
    i18n.addResourceBundle('en', path.split('/').at(-1)!.replace('.json', ''), data, true, true)
  }
  await i18n.changeLanguage('en')
})

afterEach(async () => {
  cleanup()
  configure({ reactStrictMode: false })
  queryClient?.clear()
  queryClient = undefined
  // Finish unused deferred fake responses as transient failures, never perform
  // partner navigation or leave shared refresh promises pending across cases.
  await act(async () => { pending.forEach(value => value.resolve(json({}, 503))) })
  vi.restoreAllMocks()
  clearAuthStorage('offline-case-cleanup')
  localStorage.clear()
  sessionStorage.clear()
  vi.unstubAllGlobals()
  window.history.replaceState({}, '', '/')
})

describe('actual SSO helper completion ownership', () => {
  it.each(['logout', 'replacement', 'same-user-replacement'] as const)('does not return stale A redirect after %s during validation', async boundary => {
    seed(a)
    const validation = hold()
    request = async () => validation.promise
    const completion = handleSsoRedirect(target).catch(error => error)
    expect(calls[0]?.authorization).toBe(`Bearer ${a.access_token}`)
    clearAuthStorage('offline-session-transition')
    if (boundary === 'replacement') seed(b)
    if (boundary === 'same-user-replacement') seed(session('account-a', 'replacement'))
    const before = snapshot()
    validation.resolve(json(a.user))
    const result = await completion
    console.info('SSO_HELPER_OBSERVATION', JSON.stringify({ boundary, stage: 'validation', before, after: snapshot(), result, calls }))
    expect.soft(snapshot()).toEqual(before)
    expect.soft(result).toBeInstanceOf(DOMException)
    expect.soft(result.name).toBe('AbortError')
    expect.soft(result.message).toBe('SSO redirect superseded')
  })

  it('does not initiate replacement B refresh or clear B from old A validation failure', async () => {
    seed(a)
    const validation = hold()
    request = async call => call.path.endsWith('/me') ? validation.promise : json({}, 401)
    const completion = handleSsoRedirect(target).catch(error => error)
    clearAuthStorage('offline-replacement')
    seed(b)
    const before = snapshot()
    validation.resolve(json({}, 401))
    const result = await completion
    console.info('SSO_HELPER_OBSERVATION', JSON.stringify({ boundary: 'replacement', stage: 'late-validation-denial', before, after: snapshot(), result, calls }))
    expect.soft(snapshot()).toEqual(before)
    expect.soft(calls.filter(call => call.path.endsWith('/refresh'))).toHaveLength(0)
    expect.soft(result).toBeInstanceOf(DOMException)
    expect.soft(result.name).toBe('AbortError')
  })

  it('does not request clearing B after old A refresh is denied', async () => {
    seed(a)
    const refresh = hold()
    request = async call => call.path.endsWith('/me') ? json({}, 401) : refresh.promise
    const completion = handleSsoRedirect(target).catch(error => error)
    await waitFor(() => expect(calls.some(call => call.path.endsWith('/refresh'))).toBe(true))
    clearAuthStorage('offline-replacement')
    seed(b)
    const before = snapshot()
    refresh.resolve(json({}, 401))
    const result = await completion
    console.info('SSO_HELPER_OBSERVATION', JSON.stringify({ boundary: 'replacement', stage: 'late-refresh-denial', before, after: snapshot(), result, calls }))
    expect(snapshot()).toEqual(before) // Real apiClient stale-refresh guard works.
    expect.soft(result).toBeInstanceOf(DOMException)
    expect.soft(result.name).toBe('AbortError')
  })

  it.each([
    'ftp://zenstory.ai/offline', 'https://user@zenstory.ai/offline',
    'https://zenstory.ai.evil.invalid/offline', '//zenstory.ai/offline',
  ])('control: rejects disallowed URL %s before network work', async url => {
    seed(a)
    const before = snapshot()
    expect(await handleSsoRedirect(url)).toMatchObject({ success: false, clearAuth: false, reason: 'invalid_redirect' })
    expect(calls).toHaveLength(0)
    expect(snapshot()).toEqual(before)
  })

  it.each(['https://zenstory.ai/offline', 'http://app.zenstory.ai/offline'])('control: current valid session redirects to allowed %s', async url => {
    seed(a)
    request = async () => json(a.user)
    const result = await handleSsoRedirect(url)
    expect(result.success).toBe(true)
    expect(new URL(result.redirectUrl!).searchParams.get('token')).toBe(a.access_token)
    expect(calls).toHaveLength(1)
  })

  it('control: current session refresh returns its own rotated token', async () => {
    seed(a)
    const rotated = session('account-a', '1')
    request = async call => call.path.endsWith('/me') ? json({}, 401) : json(rotated)
    const result = await handleSsoRedirect(target)
    expect(result.success).toBe(true)
    expect(new URL(result.redirectUrl!).searchParams.get('token')).toBe(rotated.access_token)
    expect(localStorage.getItem('refresh_token')).toBe(rotated.refresh_token)
  })

  it('control: healthy same-session rotation and real retry lineage are not replacement', async () => {
    seed(a)
    const validation = hold()
    const oldRequest = hold()
    const first = session('account-a', '1')
    const second = session('account-a', '2')
    let refreshes = 0
    request = async call => {
      if (call.path.endsWith('/me')) return validation.promise
      if (call.path.endsWith('/refresh')) return json(++refreshes === 1 ? first : second)
      if (call.path.endsWith('/offline-lineage')) return call.authorization === `Bearer ${a.access_token}` ? oldRequest.promise : json({ ok: true })
      throw new Error(`Unexpected lineage fixture: ${call.path}`)
    }
    const oldCall = apiCall('/offline-lineage')
    const completion = handleSsoRedirect(target)
    expect(await tryRefreshToken()).toBe(true)
    oldRequest.resolve(json({}, 401))
    expect(await oldCall).toEqual({ ok: true })
    expect(calls.find(call => call.path.endsWith('/offline-lineage') && call.authorization === `Bearer ${first.access_token}`)).toBeDefined()
    validation.resolve(json({}, 401))
    const result = await completion
    expect(result.success).toBe(true)
    expect(new URL(result.redirectUrl!).searchParams.get('token')).toBe(localStorage.getItem('access_token'))
    expect(JSON.parse(localStorage.getItem('user')!).id).toBe(a.user.id)
    console.info('SSO_ROTATION_CONTROL', JSON.stringify({ result, calls, after: snapshot() }))
  })

  it('control: delayed valid response uses latest multigeneration descendant and never a mismatched pair', async () => {
    seed(a)
    const validation = hold()
    const first = session('account-a', '1')
    const latest = session('account-a', '2')
    let refreshes = 0
    request = async call => call.path.endsWith('/me') ? validation.promise : json(++refreshes === 1 ? first : latest)
    const completion = handleSsoRedirect(target)
    expect(await tryRefreshToken()).toBe(true)
    expect(await tryRefreshToken()).toBe(true)
    const before = snapshot()
    expect(resolveOwnedAuthSession(a.access_token, a.refresh_token)).toEqual({ accessToken: latest.access_token, refreshToken: latest.refresh_token })
    expect(snapshot()).toEqual(before)
    validation.resolve(json(a.user))
    expect(new URL((await completion).redirectUrl!).searchParams.get('token')).toBe(latest.access_token)
    expect(refreshes).toBe(2)
    localStorage.setItem('access_token', 'untracked-access')
    expect(resolveOwnedAuthSession(a.access_token, a.refresh_token)).toBeNull()
    localStorage.setItem('access_token', latest.access_token)
    clearAuthStorage('offline-lineage-ended')
    seed(latest)
    expect(resolveOwnedAuthSession(a.access_token, a.refresh_token)).toBeNull()
  })

  it('control: transient refresh failure preserves current credentials', async () => {
    seed(a)
    const before = snapshot()
    request = async call => json({}, call.path.endsWith('/me') ? 401 : 503)
    expect(await handleSsoRedirect(target)).toMatchObject({ success: false, clearAuth: false, reason: 'network_error' })
    expect(snapshot()).toEqual(before)
  })

  it('control: own definitive refresh denial clears own expired credentials', async () => {
    seed(a)
    request = async () => json({}, 401)
    expect(await handleSsoRedirect(target)).toMatchObject({ success: false, clearAuth: false, reason: 'session_expired' })
    expect(localStorage.getItem('access_token')).toBeNull()
    expect(localStorage.getItem('refresh_token')).toBeNull()
  })
})

describe('actual App PublicRoute caller completion', () => {
  async function mountCaller(stage: 'validation' | 'refresh', strict = false) {
    configure({ reactStrictMode: strict })
    seed(a)
    window.history.replaceState({}, '', `/login?redirect=${encodeURIComponent(target)}`)
    const lateA = hold()
    const lateRefresh = hold()
    const logoutReasons: string[] = []
    const hrefWrites: string[] = []
    const onLogout = (event: Event) => logoutReasons.push((event as CustomEvent<{ reason: string }>).detail.reason)
    window.addEventListener('auth:logout', onLogout)
    // Observe the actual production assignment at the external-network boundary;
    // never follow a partner URL or replace the actual BrowserRouter/history.
    vi.spyOn(window.location, 'href', 'set').mockImplementation(value => { hrefWrites.push(value) })
    let meA = 0
    request = async call => {
      if (call.path.endsWith('/me')) {
        if (call.authorization === `Bearer ${a.access_token}`) return ++meA <= (strict ? 2 : 1) ? json(a.user) : lateA.promise
        if (call.authorization === `Bearer ${b.access_token}`) return hold().promise
      }
      if (call.path.endsWith('/refresh')) return lateRefresh.promise
      if (call.path.endsWith('/login')) return json(b)
      throw new Error(`Unexpected actual App fixture request: ${call.path}`)
    }
    const { default: App } = await import('../../App')
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
    render(createElement(QueryClientProvider, { client: queryClient, children: createElement(App) }))
    await waitFor(() => expect(meA).toBeGreaterThanOrEqual(strict ? 3 : 2))
    if (stage === 'refresh') {
      await act(async () => { lateA.resolve(json({}, 401)) })
      await waitFor(() => expect(calls.filter(call => call.path.endsWith('/refresh'))).toHaveLength(1))
    }
    return { lateA, lateRefresh, hrefWrites, logoutReasons, removeListener: () => window.removeEventListener('auth:logout', onLogout) }
  }

  it.each([false, true])('control: live PublicRoute performs one ordinary validated SSO redirect strict=%s', async strict => {
    const fixture = await mountCaller('validation', strict)
    try {
      await act(async () => { fixture.lateA.resolve(json(a.user)) })
      expect(fixture.hrefWrites).toHaveLength(1)
      expect(new URL(fixture.hrefWrites[0]).searchParams.get('token')).toBe(a.access_token)
      expect(localStorage.getItem('access_token')).toBe(a.access_token)
      console.info('SSO_CALLER_CONTROL', JSON.stringify({ hrefWrites: fixture.hrefWrites, calls }))
    } finally { fixture.removeListener() }
  })

  it.each([false, true])('control: own definitive denial exits spinner with one primitive clear strict=%s', async strict => {
    const fixture = await mountCaller('refresh', strict)
    try {
      await act(async () => { fixture.lateRefresh.resolve(json({}, 401)) })
      await waitFor(() => expect(screen.getByTestId('email-input')).toBeInTheDocument())
      expect(localStorage.getItem('access_token')).toBeNull()
      expect(fixture.logoutReasons.filter(reason => reason === 'refresh_failed')).toHaveLength(1)
      expect(fixture.logoutReasons.filter(reason => reason === 'sso_redirect_failed')).toHaveLength(0)
      expect(fixture.hrefWrites).toHaveLength(0)
    } finally { fixture.removeListener() }
  })

  it('does not redirect from departed login route after old validation succeeds', async () => {
    const fixture = await mountCaller('validation')
    try {
      act(() => {
        window.history.pushState({}, '', '/privacy-policy')
        window.dispatchEvent(new PopStateEvent('popstate'))
      })
      await waitFor(() => expect(screen.getByRole('heading', { level: 1 })).toBeInTheDocument())
      expect(window.location.pathname).toBe('/privacy-policy')
      await act(async () => { fixture.lateA.resolve(json(a.user)) })
      console.info('SSO_CALLER_OBSERVATION', JSON.stringify({ boundary: 'router-departure', pathname: window.location.pathname, hrefWrites: fixture.hrefWrites, after: snapshot(), calls }))
      expect.soft(fixture.hrefWrites).toHaveLength(0)
    } finally { fixture.removeListener() }
  })

  it('does not redirect anonymous replacement route after logout during old validation', async () => {
    const fixture = await mountCaller('validation')
    try {
      act(() => clearAuthStorage('offline-explicit-logout'))
      await waitFor(() => expect(screen.getByTestId('email-input')).toBeInTheDocument())
      const before = snapshot()
      await act(async () => { fixture.lateA.resolve(json(a.user)) })
      console.info('SSO_CALLER_OBSERVATION', JSON.stringify({ boundary: 'logout', before, after: snapshot(), hrefWrites: fixture.hrefWrites, calls }))
      expect(snapshot()).toEqual(before)
      expect.soft(fixture.hrefWrites).toHaveLength(0)
    } finally { fixture.removeListener() }
  })

  it('does not clear actually established B when old A refresh is denied', async () => {
    const fixture = await mountCaller('refresh')
    try {
      act(() => {
        clearAuthStorage('offline-replacement-login')
        window.history.pushState({}, '', `/login?redirect=${encodeURIComponent('https://app.zenstory.ai/offline-b')}`)
        window.dispatchEvent(new PopStateEvent('popstate'))
      })
      await waitFor(() => expect(screen.getByTestId('email-input')).toBeInTheDocument())
      fireEvent.change(screen.getByTestId('email-input'), { target: { value: b.user.email } })
      fireEvent.change(screen.getByTestId('password-input'), { target: { value: 'offline-password' } })
      fireEvent.click(screen.getByTestId('login-submit'))
      await waitFor(() => expect(localStorage.getItem('access_token')).toBe(b.access_token))
      await waitFor(() => expect(calls.some(call => call.authorization === `Bearer ${b.access_token}`)).toBe(true))
      const before = snapshot()
      await act(async () => { fixture.lateRefresh.resolve(json({}, 401)) })
      const staleClears = fixture.logoutReasons.filter(reason => reason === 'sso_redirect_failed').length
      console.info('SSO_CALLER_OBSERVATION', JSON.stringify({ boundary: 'actually-established-B', before, after: snapshot(), staleClears, anonymousLoginVisible: Boolean(screen.queryByTestId('email-input')), calls }))
      expect.soft(snapshot()).toEqual(before)
      expect.soft(staleClears).toBe(0)
    } finally { fixture.removeListener() }
  })
})
