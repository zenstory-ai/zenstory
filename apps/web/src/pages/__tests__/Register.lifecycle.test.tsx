import { act, cleanup, configure, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, useLocation } from 'react-router-dom'
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
import { AuthProvider, useAuth } from '../../contexts/AuthContext'
import { clearAuthStorage } from '../../lib/apiClient'
import i18n from '../../lib/i18n'
import Register from '../Register'

vi.mock('../../lib/analytics', async importOriginal => ({
  ...(await importOriginal<typeof import('../../lib/analytics')>()),
  identifyUser: vi.fn(), resetAnalytics: vi.fn(), trackEvent: vi.fn(),
  trackPageView: vi.fn(), captureException: vi.fn(),
}))

const locales = import.meta.glob<Record<string, unknown>>('../../../public/locales/en/*.json', { eager: true, import: 'default' })
const policy = { invite_code_optional: true, variant: 'offline-control', rollout_percent: 100 }
type Call = { path: string; method: string; body: string | null }
let calls: Call[]
let pending: Array<ReturnType<typeof deferred>>
let policies: Array<ReturnType<typeof deferred>>
let registrations: Array<ReturnType<typeof deferred>>
let holdPolicies: boolean
let queryClient: QueryClient
let unexpected: string[]

function deferred() {
  let resolve!: (response: Response) => void
  const promise = new Promise<Response>(accept => { resolve = accept })
  return { promise, resolve }
}
function hold() {
  const value = deferred()
  pending.push(value)
  return value
}
function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } })
}
function State() {
  const auth = useAuth()
  const route = useLocation()
  return <output data-testid="authority">{JSON.stringify({ user: auth.user?.id ?? null, path: route.pathname, state: route.state })}</output>
}

beforeEach(async () => {
  vi.stubGlobal('localStorage', new window.Storage());
  vi.stubGlobal('sessionStorage', new window.Storage());
  vi.clearAllMocks()
  calls = []
  pending = []
  policies = []
  registrations = []
  unexpected = []
  holdPolicies = false
  clearAuthStorage('offline-register-reset')
  localStorage.clear()
  sessionStorage.clear()
  expect(localStorage).toBeInstanceOf(Storage)
  expect(vi.isMockFunction(localStorage.setItem)).toBe(false)
  vi.stubGlobal('fetch', async (input: RequestInfo | URL, options?: RequestInit) => {
    const path = new URL(String(input), window.location.origin).pathname
    if (path.startsWith('/locales/')) return json(locales[`../../../public/locales/en/${path.split('/').at(-1)}`] ?? {})
    calls.push({ path, method: options?.method ?? 'GET', body: typeof options?.body === 'string' ? options.body : null })
    if (path === '/api/auth/register-policy') {
      if (!holdPolicies) return json(policy)
      const value = hold()
      policies.push(value)
      return value.promise
    }
    if (path === '/api/auth/register') {
      const value = hold()
      registrations.push(value)
      return value.promise
    }
    unexpected.push(path)
    throw new Error(`Unexpected offline API: ${path}`)
  })
  for (const [path, data] of Object.entries(locales)) {
    i18n.addResourceBundle('en', path.split('/').at(-1)!.replace('.json', ''), data, true, true)
  }
  await i18n.changeLanguage('en')
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
})

afterEach(async () => {
  cleanup()
  queryClient.clear()
  await act(async () => { pending.forEach(value => value.resolve(json({ detail: 'Offline cleanup' }, 503))) })
  configure({ reactStrictMode: false })
  clearAuthStorage('offline-register-cleanup')
  localStorage.clear()
  sessionStorage.clear()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  expect(unexpected).toEqual([])
})

async function click(user: ReturnType<typeof userEvent.setup>, element: Element) {
  await act(async () => { await user.click(element) })
}

async function mount(strict: boolean) {
  configure({ reactStrictMode: strict }) // RTL's root StrictMode, not an inner wrapper.
  render(<QueryClientProvider client={queryClient}><MemoryRouter initialEntries={['/register']}><AuthProvider><Register /><State /></AuthProvider></MemoryRouter></QueryClientProvider>)
  const user = userEvent.setup()
  expect(screen.getByRole('button', { name: 'Create Account' })).toBeDisabled()
  fireEvent.change(screen.getByLabelText(/^Username/), { target: { value: 'offline_user' } })
  fireEvent.change(screen.getByLabelText(/^Email/), { target: { value: 'offline@example.invalid' } })
  fireEvent.change(screen.getByLabelText(/^Password/), { target: { value: 'OfflinePass123!' } })
  fireEvent.change(screen.getByLabelText(/^Confirm Password/), { target: { value: 'OfflinePass123!' } })
  await click(user, screen.getByRole('checkbox'))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Create Account' })).toBeEnabled())
  await waitFor(() => expect(calls.some(call => call.path === '/api/auth/register-policy')).toBe(true))
  return user
}

for (const strict of [false, true]) {
  describe(strict ? 'actual Register root StrictMode' : 'actual Register default root', () => {
    it('does not admit two enabled-button submissions while submit policy lookup is pending', async () => {
      const user = await mount(strict)
      holdPolicies = true
      const button = screen.getByRole('button', { name: 'Create Account' })
      await click(user, button)
      await waitFor(() => expect(policies).toHaveLength(1))
      const pendingState = { disabled: button.hasAttribute('disabled'), busy: screen.getByTestId('register-form').getAttribute('aria-busy') }
      await click(user, button) // An ordinary enabled click; no forced submit or disabled bypass.
      await act(async () => { policies.forEach(value => value.resolve(json(policy))) })
      await waitFor(() => expect(registrations.length).toBeGreaterThan(0))
      await act(async () => { await Promise.resolve() })
      console.info('REGISTER_REENTRY_OBSERVATION', JSON.stringify({ strict, pendingState, policyCount: policies.length, registerCount: registrations.length, calls }))
      expect.soft(registrations).toHaveLength(1)
      expect.soft(policies).toHaveLength(1)
      await act(async () => { registrations.forEach(value => value.resolve(json({ detail: 'Offline duplicate probe finished' }, 400))) })
      await waitFor(() => expect(button).toBeEnabled())
      expect(localStorage.getItem('access_token')).toBeNull()
    })

    it('releases submit gate after required-invite policy and permits retry with an invite', async () => {
      const user = await mount(strict)
      holdPolicies = true
      await click(user, screen.getByRole('button', { name: 'Create Account' }))
      await waitFor(() => expect(policies).toHaveLength(1))
      await act(async () => { policies[0].resolve(json({ ...policy, invite_code_optional: false })) })
      await waitFor(() => expect(screen.getByTestId('register-form')).toHaveAttribute('aria-busy', 'false'))
      expect(registrations).toHaveLength(0)
      expect(screen.getByRole('button', { name: 'Create Account' })).toBeDisabled()
      expect(screen.getByLabelText(/^Email/)).toBeEnabled()
      fireEvent.change(document.getElementById('invite_code')!, { target: { value: 'ABCD' } })
      await waitFor(() => expect(screen.getByRole('button', { name: 'Create Account' })).toBeEnabled())
      await click(user, screen.getByRole('button', { name: 'Create Account' }))
      await waitFor(() => expect(policies).toHaveLength(2))
      await act(async () => { policies[1].resolve(json({ ...policy, invite_code_optional: false })) })
      await waitFor(() => expect(registrations).toHaveLength(1))
      expect(JSON.parse(calls.find(call => call.path === '/api/auth/register')!.body!).invite_code).toBe('ABCD-')
      await act(async () => { registrations[0].resolve(json({ detail: 'Offline invite retry finished' }, 400)) })
      await waitFor(() => expect(screen.getByRole('button', { name: 'Create Account' })).toBeEnabled())
    })

    it('preserves policy-network fallback and releases gate after POST failure', async () => {
      const user = await mount(strict)
      holdPolicies = true
      await click(user, screen.getByRole('button', { name: 'Create Account' }))
      await waitFor(() => expect(policies).toHaveLength(1))
      await act(async () => { policies[0].resolve(json({}, 503)) })
      await waitFor(() => expect(registrations).toHaveLength(1))
      await act(async () => { registrations[0].resolve(json({ detail: 'Offline fallback finished' }, 400)) })
      await waitFor(() => expect(screen.getByRole('button', { name: 'Create Account' })).toBeEnabled())
      expect(screen.getByTestId('register-form')).toHaveAttribute('aria-busy', 'false')
    })

    it('locks ordinary clicks/fields during register POST and allows retry after failure', async () => {
      const user = await mount(strict)
      await click(user, screen.getByRole('button', { name: 'Create Account' }))
      await waitFor(() => expect(registrations).toHaveLength(1))
      const button = screen.getByRole('button', { name: 'Creating account...' })
      expect(button).toBeDisabled()
      expect(screen.getByTestId('register-form')).toHaveAttribute('aria-busy', 'true')
      expect(screen.getByLabelText(/^Email/)).toBeDisabled()
      await click(user, button)
      expect(registrations).toHaveLength(1)
      await act(async () => { registrations[0].resolve(json({ detail: 'Offline rejected registration' }, 400)) })
      await waitFor(() => expect(screen.getByRole('button', { name: 'Create Account' })).toBeEnabled())
      expect(screen.getByTestId('register-form')).toHaveAttribute('aria-busy', 'false')
      expect(screen.getByLabelText(/^Email/)).toBeEnabled()
      await click(user, screen.getByRole('button', { name: 'Create Account' }))
      await waitFor(() => expect(registrations).toHaveLength(2))
      await act(async () => { registrations[1].resolve(json({ detail: 'Offline retry finished' }, 400)) })
      await waitFor(() => expect(screen.getByRole('button', { name: 'Create Account' })).toBeEnabled())
      console.info('REGISTER_POST_CONTROL', JSON.stringify({ strict, calls, authority: screen.getByTestId('authority').textContent }))
    })

    it('retains successful form lock and real router verification handoff without establishing tokens', async () => {
      const user = await mount(strict)
      await click(user, screen.getByRole('button', { name: 'Create Account' }))
      await waitFor(() => expect(registrations).toHaveLength(1))
      await act(async () => { registrations[0].resolve(json({ email: 'offline@example.invalid', email_verified: false })) })
      await waitFor(() => expect(screen.getByTestId('register-form')).toHaveAttribute('aria-busy', 'false'))
      expect(screen.getByRole('button', { name: 'Create Account' })).toBeDisabled()
      expect(screen.getByLabelText(/^Email/)).toBeDisabled()
      await click(user, screen.getByRole('button', { name: 'Create Account' }))
      expect(registrations).toHaveLength(1)
      await waitFor(() => expect(screen.getByTestId('authority').textContent).toContain('"path":"/verify-email"'), { timeout: 3000 })
      expect(screen.getByTestId('authority').textContent).toContain('"email":"offline@example.invalid"')
      expect(localStorage.getItem('access_token')).toBeNull()
      expect(localStorage.getItem('refresh_token')).toBeNull()
      console.info('REGISTER_SUCCESS_CONTROL', JSON.stringify({ strict, calls, authority: screen.getByTestId('authority').textContent }))
    })
  })
}
