import { useState } from 'react'
import { act, cleanup, configure, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { BrowserRouter, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
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
import { AuthProvider, useAuth } from '../AuthContext'
import OAuthCallback from '../../pages/OAuthCallback'
import { Login } from '../../pages/Login'
import i18n from '../../lib/i18n'
import { clearAuthStorage } from '../../lib/apiClient'
import { takeAuthCallbackParams } from '../../lib/authCallbackParams'
import { captureException, identifyUser, trackEvent } from '../../lib/analytics'

// Only analytics and network/external navigation boundaries are intercepted.
// Provider, commands, pages, API/client, Storage and BrowserRouter are actual.
vi.mock('../../lib/analytics', async importOriginal => ({
  ...(await importOriginal<typeof import('../../lib/analytics')>()),
  identifyUser: vi.fn(), resetAnalytics: vi.fn(), trackEvent: vi.fn(), trackPageView: vi.fn(), captureException: vi.fn(),
}))
const locales = import.meta.glob<Record<string, unknown>>('../../../public/locales/en/*.json', { eager: true, import: 'default' })
const target = 'https://app.zenstory.ai/offline-return'
function account(id: string) {
  return { access_token: `offline-access-${id}`, refresh_token: `offline-refresh-${id}`, user: {
    id, username: id, email: `${id}@example.invalid`, email_verified: true, is_active: true, is_superuser: false,
    created_at: '2020-01-01T00:00:00Z', updated_at: '2026-10-06T00:00:00Z',
  } }
}
const a = account('A'), b = account('B')
type Call = { path: string; method: string; authorization: string | null }
function json(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } }) }
let calls: Call[], unexpected: string[], hrefWrites: string[]
let handler: (call: Call) => Promise<Response>
let pending: { promise: Promise<Response>; resolve: (reply: Response) => void }[]
function hold() {
  let resolve!: (reply: Response) => void
  const promise = new Promise<Response>(accept => { resolve = accept })
  const deferred = { promise, resolve }; pending.push(deferred); return deferred
}
async function settle(deferred: ReturnType<typeof hold>, value: unknown, status = 200) { await act(async () => { deferred.resolve(json(value, status)) }) }
function storage() { return Object.fromEntries(['access_token', 'refresh_token', 'user', 'auth_validated_at', 'zenstory_current_project_id:A', 'zenstory_current_project_id:B'].map(key => [key, localStorage.getItem(key)])) }
function Commands() {
  const auth = useAuth(), navigate = useNavigate(), location = useLocation()
  const [failure, setFailure] = useState('')
  return <>
    <button onClick={() => auth.logout()}>Command logout</button>
    <button onClick={() => { void auth.login('B', 'offline-password').catch(error => setFailure(String(error))) }}>Command login B</button>
    <button onClick={() => navigate('/departed')}>Command depart</button>
    <output data-testid="auth-state">{JSON.stringify({ id: auth.user?.id ?? null, loading: auth.loading, path: location.pathname + location.search, failure })}</output>
  </>
}
function readState() { return JSON.parse(screen.getByTestId('auth-state').textContent!) as { id: string | null; loading: boolean; path: string; failure: string } }
function mount(strict: boolean, path: string, routeState?: object) {
  configure({ reactStrictMode: strict })
  window.history.replaceState(routeState ? { usr: routeState } : {}, '', path)
  render(<BrowserRouter><AuthProvider><Commands /><Routes>
    <Route path="/auth/callback" element={<OAuthCallback />} />
    <Route path="/login" element={<Login />} />
    <Route path="*" element={<div>Embedded destination</div>} />
  </Routes></AuthProvider></BrowserRouter>)
  return userEvent.setup()
}
async function command(user: ReturnType<typeof userEvent.setup>, label: string) { await act(async () => { await user.click(screen.getByRole('button', { name: label })) }) }
async function submitLogin() {
  fireEvent.change(screen.getByTestId('email-input'), { target: { value: 'A' } })
  fireEvent.change(screen.getByTestId('password-input'), { target: { value: 'offline-password' } })
  await act(async () => { await userEvent.click(screen.getByTestId('login-submit')) })
}
function observe(scenario: string, strict: boolean, extra: Record<string, unknown> = {}) {
  console.info('AUTH_EMBEDDED_OBSERVATION', JSON.stringify({ scenario, strict, state: readState(), storage: storage(), hrefWrites, calls, analytics: { identified: vi.mocked(identifyUser).mock.calls.map(([user]) => user.id), events: vi.mocked(trackEvent).mock.calls.map(([event]) => event) }, ...extra }))
}
beforeEach(async () => {
  vi.stubGlobal('localStorage', new window.Storage());
  vi.stubGlobal('sessionStorage', new window.Storage());
  calls = []; unexpected = []; pending = []; hrefWrites = []; vi.clearAllMocks()
  expect(localStorage).toBeInstanceOf(Storage); expect(vi.isMockFunction(localStorage.getItem)).toBe(false)
  localStorage.clear(); sessionStorage.clear(); clearAuthStorage('offline-case-start'); takeAuthCallbackParams()
  handler = async call => {
    if (call.path === '/api/auth/logout') return json({})
    if (call.path === '/api/auth/login') return json(b)
    throw new Error(`Unrecognized embedded request: ${call.path}`)
  }
  vi.spyOn(window.location, 'href', 'set').mockImplementation(value => { hrefWrites.push(value) })
  vi.stubGlobal('fetch', async (input: RequestInfo | URL, options?: RequestInit) => {
    const path = new URL(input instanceof Request ? input.url : String(input), window.location.origin).pathname
    if (path.startsWith('/locales/')) return json(locales[`../../../public/locales/en/${path.split('/').at(-1)}`] ?? {})
    const call = { path, method: options?.method ?? 'GET', authorization: new Headers(options?.headers).get('Authorization') }
    calls.push(call)
    try { return await handler(call) } catch (error) { unexpected.push(`${call.method} ${path}`); throw error }
  })
  for (const [path, data] of Object.entries(locales)) i18n.addResourceBundle('en', path.split('/').at(-1)!.replace('.json', ''), data, true, true)
  await i18n.changeLanguage('en')
})
afterEach(async () => {
  cleanup()
  await act(async () => { pending.forEach(value => value.resolve(json({ detail: 'Offline teardown' }, 503))) })
  clearAuthStorage('offline-case-end'); localStorage.clear(); sessionStorage.clear(); takeAuthCallbackParams()
  configure({ reactStrictMode: false }); vi.restoreAllMocks(); vi.unstubAllGlobals()
  expect(unexpected).toEqual([])
  expect(localStorage.length).toBe(0); expect(sessionStorage.length).toBe(0)
})
describe('actual embedded current OAuth default mode', () => {
  const strict = false
    it.each(['free-query', 'paid-hash', 'external-query'] as const)('current OAuth %s preserves real establishment and scrub', async mode => {
      const delayed = hold(); handler = async call => { if (call.path === '/api/auth/me') return delayed.promise; throw new Error(call.path) }
      if (mode === 'paid-hash') sessionStorage.setItem('oauth_plan_intent', 'pro')
      const params = `access_token=${a.access_token}&refresh_token=${a.refresh_token}${mode === 'external-query' ? `&redirect=${encodeURIComponent(target)}` : ''}`
      mount(strict, `/auth/callback${mode === 'paid-hash' ? '#' : '?'}${params}`)
      await waitFor(() => expect(calls.filter(call => call.path === '/api/auth/me')).toHaveLength(1))
      expect(window.location.search).toBe(''); expect(window.location.hash).toBe('')
      await settle(delayed, a.user)
      observe(`oauth-current-${mode}`, strict)
      expect.soft(readState()).toMatchObject({ id: 'A', loading: false })
      expect.soft(localStorage.getItem('access_token')).toBe(a.access_token)
      if (mode === 'external-query') expect(hrefWrites).toEqual([`${target}?token=${a.access_token}`])
      else expect(readState().path).toBe(mode === 'paid-hash' ? '/dashboard/billing?plan=pro' : '/dashboard')
      expect(calls.filter(call => call.path === '/api/auth/me')).toHaveLength(1)
    })
})
for (const strict of [false, true]) {
  describe(`actual embedded OAuth/Login provider strict=${strict}`, () => {
    it.each([
      ['logout', false], ['logout', true], ['new B', false], ['new B', true], ['page departure', false], ['page departure', true],
    ] as const)('obsolete OAuth after %s external=%s has no captured-A navigation', async (boundary, external) => {
      const delayed = hold()
      handler = async call => {
        if (call.path === '/api/auth/me') return delayed.promise
        if (call.path === '/api/auth/login') return json(b)
        if (call.path === '/api/auth/logout') return json({})
        throw new Error(call.path)
      }
      const user = mount(strict, `/auth/callback?access_token=${a.access_token}&refresh_token=${a.refresh_token}${external ? `&redirect=${encodeURIComponent(target)}` : ''}`)
      await waitFor(() => expect(calls.filter(call => call.path === '/api/auth/me')).toHaveLength(1))
      if (boundary === 'logout') await command(user, 'Command logout')
      if (boundary === 'new B') { await command(user, 'Command login B'); await waitFor(() => expect(readState().id).toBe('B')) }
      if (boundary === 'page departure') await command(user, 'Command depart')
      const path = readState().path, before = storage()
      await settle(delayed, a.user)
      observe(`oauth-stale-${boundary}`, strict, { external, before })
      expect.soft(hrefWrites).toEqual([])
      expect(captureException).not.toHaveBeenCalled()
      expect.soft(readState().path).toBe(path)
      if (boundary !== 'page departure') {
        expect.soft(storage()).toEqual(before)
        expect(vi.mocked(trackEvent).mock.calls.map(([event]) => event)).not.toContain('oauth_callback_success')
      }
      if (boundary === 'new B') expect.soft(readState().id).toBe('B')
      // Page departure alone invalidates the caller, not an otherwise valid
      // AuthProvider command. Do not invent blanket cancellation of establishment.
    })
    it.each(['logout', 'new B'] as const)('obsolete non-OK OAuth after %s is exact quiet cancellation', async boundary => {
      const delayed = hold()
      handler = async call => {
        if (call.path === '/api/auth/me') return delayed.promise
        if (call.path === '/api/auth/login') return json(b)
        if (call.path === '/api/auth/logout') return json({})
        throw new Error(call.path)
      }
      const user = mount(strict, `/auth/callback?access_token=${a.access_token}&refresh_token=${a.refresh_token}&redirect=${encodeURIComponent(target)}`)
      await waitFor(() => expect(calls.filter(call => call.path === '/api/auth/me')).toHaveLength(1))
      await command(user, boundary === 'logout' ? 'Command logout' : 'Command login B')
      if (boundary === 'new B') await waitFor(() => expect(readState().id).toBe('B'))
      const before = storage(), path = readState().path
      await settle(delayed, { detail: 'Obsolete OAuth denial' }, 401)
      expect(storage()).toEqual(before); expect(readState().path).toBe(path)
      expect(hrefWrites).toEqual([]); expect(captureException).not.toHaveBeenCalled()
      expect(screen.queryByRole('button', { name: 'Back to login' })).not.toBeInTheDocument()
    })
    it.each(['recent', 'saved', 'paid', 'deep-link'] as const)('current Login %s continuation control', async mode => {
      const list = hold()
      handler = async call => {
        if (call.path === '/api/auth/login') return json(a)
        if (call.path === '/api/v1/projects') return list.promise
        throw new Error(call.path)
      }
      localStorage.setItem('zenstory_current_project_id:A', 'A-old')
      mount(strict, mode === 'paid' ? '/login?plan=pro' : '/login', mode === 'deep-link' ? { from: { pathname: '/dashboard/settings', search: '?tab=privacy', hash: '#choice', state: { authorIntent: true } } } : undefined)
      await waitFor(() => expect(screen.getByTestId('email-input')).toBeInTheDocument())
      await submitLogin()
      if (mode === 'recent') localStorage.removeItem('zenstory_current_project_id:A')
      if (mode === 'recent' || mode === 'saved') {
        await waitFor(() => expect(calls.filter(call => call.path === '/api/v1/projects')).toHaveLength(1))
        await settle(list, [{ id: 'A-old', created_at: '2020-01-01' }, { id: 'A-recent', updated_at: '2026-10-06' }])
      }
      observe(`login-current-${mode}`, strict)
      expect(readState().id).toBe('A')
      expect(window.location.pathname + window.location.search + window.location.hash).toBe({ recent: '/project/A-recent', saved: '/project/A-old', paid: '/dashboard/billing?plan=pro', 'deep-link': '/dashboard/settings?tab=privacy#choice' }[mode])
      expect(calls.filter(call => call.path === '/api/v1/projects')).toHaveLength(mode === 'paid' || mode === 'deep-link' ? 0 : 1)
    })
    it.each(['logout', 'new B', 'page departure'] as const)('obsolete Login project-list response after %s cannot select A route', async boundary => {
      const list = hold()
      handler = async call => {
        if (call.path === '/api/auth/login') return json(calls.filter(value => value.path === '/api/auth/login').length === 1 ? a : b)
        if (call.path === '/api/v1/projects') return list.promise
        if (call.path === '/api/auth/logout') return json({})
        throw new Error(call.path)
      }
      const user = mount(strict, '/login')
      await waitFor(() => expect(screen.getByTestId('email-input')).toBeInTheDocument()); await submitLogin()
      await waitFor(() => expect(calls.filter(call => call.path === '/api/v1/projects')).toHaveLength(1))
      if (boundary === 'logout') await command(user, 'Command logout')
      if (boundary === 'new B') { await command(user, 'Command login B'); await waitFor(() => expect(readState().id).toBe('B')) }
      if (boundary === 'page departure') await command(user, 'Command depart')
      const before = storage(), path = readState().path
      await settle(list, [{ id: 'A-recent', updated_at: '2026-10-06' }])
      observe(`login-stale-${boundary}`, strict, { before })
      expect.soft(readState().path).toBe(path)
      expect(storage()).toEqual(before)
      expect(readState().id).toBe(boundary === 'logout' ? null : boundary === 'new B' ? 'B' : 'A')
    })
  })
}
