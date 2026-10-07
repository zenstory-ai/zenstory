import { type ReactNode } from 'react'
import { act, cleanup, configure, renderHook, waitFor } from '@testing-library/react'
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
import { AuthProvider, useAuth, type AuthContextType, type User } from '../AuthContext'
import { authApi } from '@/lib/api'
import { ApiError, clearAuthStorage } from '@/lib/apiClient'
import { identifyUser, resetAnalytics, trackEvent } from '@/lib/analytics'

vi.mock('@/lib/api', () => ({
  authApi: {
    login: vi.fn(),
    verifyEmail: vi.fn(),
    register: vi.fn(),
    refreshToken: vi.fn(),
    resendVerification: vi.fn(),
  },
}))

vi.mock('@/lib/analytics', () => ({
  identifyUser: vi.fn(),
  resetAnalytics: vi.fn(),
  trackEvent: vi.fn(),
}))

type Method = 'login' | 'verifyEmail'
type Boundary = 'explicit logout' | 'auth:logout' | 'newer account' | 'provider unmount' | 'newer account fails'
type Reply = { access_token: string; refresh_token: string; user: User }
const methods: Method[] = ['login', 'verifyEmail']
const boundaries: Boundary[] = ['explicit logout', 'auth:logout', 'newer account', 'provider unmount', 'newer account fails']
const authKeys = ['access_token', 'refresh_token', 'user', 'auth_validated_at'] as const

function reply(id: string): Reply {
  return {
    access_token: `offline-access-${id}`,
    refresh_token: `offline-refresh-${id}`,
    user: {
      id,
      username: id,
      email: `${id}@example.invalid`,
      email_verified: true,
      is_active: true,
      is_superuser: false,
      created_at: '2026-10-06T00:00:00Z',
      updated_at: '2026-10-06T00:00:00Z',
    },
  }
}

const owner = reply('owner')
const accountA = reply('account-a')
const accountB = reply('account-b')
const mockFetch = vi.fn<typeof fetch>()

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: Error) => void
  const promise = new Promise<T>((accept, decline) => {
    resolve = accept
    reject = decline
  })
  return { promise, resolve, reject }
}

function seed(data: Reply) {
  localStorage.setItem('access_token', data.access_token)
  localStorage.setItem('refresh_token', data.refresh_token)
  localStorage.setItem('user', JSON.stringify(data.user))
  localStorage.setItem('auth_validated_at', String(Date.now()))
}

function storageSnapshot() {
  return Object.fromEntries(authKeys.map(key => [key, localStorage.getItem(key)]))
}

function start(auth: AuthContextType, method: Method, user: User) {
  return method === 'login'
    ? auth.login(user.username, 'offline-fixture-password')
    : auth.verifyEmail(user.email, '123456')
}

function successEvent(method: Method) {
  return method === 'login' ? 'login_success' : 'verify_email_success'
}

function successCount(method: Method) {
  return vi.mocked(trackEvent).mock.calls.filter(([event]) => event === successEvent(method)).length
}

function requestCount(method: Method, user: User) {
  return vi.mocked(authApi[method]).mock.calls.filter(([identifier]) => (
    identifier === (method === 'login' ? user.username : user.email)
  )).length
}

function assertIdentity(auth: AuthContextType, data: Reply | null) {
  expect(auth.user).toEqual(data?.user ?? null)
  expect(localStorage.getItem('access_token')).toBe(data?.access_token ?? null)
  expect(localStorage.getItem('refresh_token')).toBe(data?.refresh_token ?? null)
  expect(localStorage.getItem('user')).toBe(data ? JSON.stringify(data.user) : null)
  if (data) {
    expect(Number(localStorage.getItem('auth_validated_at'))).toBeGreaterThan(0)
  } else {
    expect(localStorage.getItem('auth_validated_at')).toBeNull()
  }
}

beforeEach(() => {
  vi.stubGlobal('localStorage', new window.Storage());
  vi.stubGlobal('sessionStorage', new window.Storage());
  vi.resetAllMocks()
  // The suite intentionally requires happy-dom's native Storage. The repo's
  // shared setup replaces it; this suite installs its own native instances.
  expect(vi.isMockFunction(localStorage.setItem)).toBe(false)
  expect(localStorage).toBeInstanceOf(Storage)
  localStorage.clear()
  sessionStorage.clear()
  mockFetch.mockImplementation(async (input, options) => {
    const url = String(input)
    if (url.endsWith('/api/auth/logout') && options?.method === 'POST') {
      return new Response(null, { status: 204 })
    }
    if (url.endsWith('/api/auth/me')) {
      const authorization = new Headers(options?.headers).get('Authorization')
      const data = [owner, accountA, accountB].find(candidate => (
        authorization === `Bearer ${candidate.access_token}`
      ))
      if (data) return new Response(JSON.stringify(data.user), { status: 200 })
    }
    throw new Error(`Unexpected offline fetch: ${url}`)
  })
  vi.stubGlobal('fetch', mockFetch)
})

afterEach(() => {
  cleanup()
  configure({ reactStrictMode: false })
  localStorage.clear()
  sessionStorage.clear()
  vi.unstubAllGlobals()
})

describe.each([false, true])('AuthProvider lifecycle (root StrictMode=%s)', strict => {
  function mount() {
    // RTL wraps the entire root here; a nested StrictMode inside wrapper would
    // not replay initial effects with the installed React version.
    configure({ reactStrictMode: strict })
    const wrapper = ({ children }: { children: ReactNode }) => <AuthProvider>{children}</AuthProvider>
    return renderHook(() => useAuth(), { wrapper })
  }

  async function mountReady(data: Reply | null = null) {
    if (data) seed(data)
    const requestsBefore = mockFetch.mock.calls.length
    const view = mount()
    await waitFor(() => expect(view.result.current.loading).toBe(false))
    await act(async () => {})
    assertIdentity(view.result.current, data)
    if (data) expect(mockFetch.mock.calls.length - requestsBefore).toBe(strict ? 2 : 1)
    return view
  }

  describe.each(methods)('%s', method => {
    it.each(boundaries)('fences delayed success across %s', async boundary => {
      const view = await mountReady(owner)
      const pending = deferred<Reply>()
      vi.mocked(authApi[method]).mockReturnValueOnce(pending.promise)
      let completion!: Promise<unknown>
      act(() => {
        completion = start(view.result.current, method, accountA.user).then(
          () => undefined,
          error => error,
        )
      })
      expect(requestCount(method, accountA.user)).toBe(1)
      assertIdentity(view.result.current, owner)

      const replacementMethod: Method = method === 'login' ? 'verifyEmail' : 'login'
      let liveView = view
      if (boundary === 'explicit logout') {
        act(() => view.result.current.logout())
        assertIdentity(view.result.current, null)
        expect(mockFetch.mock.calls.filter(([url]) => String(url).endsWith('/api/auth/logout'))).toEqual([
          [expect.stringContaining('/api/auth/logout'), {
            method: 'POST', headers: { Authorization: `Bearer ${owner.access_token}` },
          }],
        ])
      } else if (boundary === 'auth:logout') {
        act(() => clearAuthStorage('offline-invalid-session'))
        assertIdentity(view.result.current, null)
      } else {
        if (boundary === 'provider unmount') {
          view.unmount()
          // A replacement instance establishes B through actual provider logic;
          // assertions below observe external storage/analytics and live B.
          liveView = await mountReady(owner)
        }
        if (boundary === 'newer account fails') {
          const failure = new Error('offline-newer-request-failure')
          vi.mocked(authApi[replacementMethod]).mockRejectedValueOnce(failure)
          await act(async () => {
            await expect(start(liveView.result.current, replacementMethod, accountB.user)).rejects.toBe(failure)
          })
          assertIdentity(liveView.result.current, owner)
        } else {
          vi.mocked(authApi[replacementMethod]).mockResolvedValueOnce(accountB)
          await act(async () => { await start(liveView.result.current, replacementMethod, accountB.user) })
          assertIdentity(liveView.result.current, accountB)
        }
      }

      const before = storageSnapshot()
      const liveUserBefore = liveView.result.current.user
      const successesBefore = successCount(method)
      const identifiedABefore = vi.mocked(identifyUser).mock.calls.filter(([user]) => user.id === accountA.user.id).length
      let cancellation: unknown
      await act(async () => {
        pending.resolve(accountA)
        cancellation = await completion
      })
      const after = storageSnapshot()
      const observed = {
        kind: 'race', strict, method, boundary,
        apiCallsForA: requestCount(method, accountA.user),
        apiCallsForB: requestCount(replacementMethod, accountB.user),
        fetchCalls: mockFetch.mock.calls.length,
        beforeUserId: liveUserBefore?.id ?? null,
        afterUserId: liveView.result.current.user?.id ?? null,
        before, after,
        changedStorageKeys: authKeys.filter(key => before[key] !== after[key]),
        lateSuccessEvents: successCount(method) - successesBefore,
        lateIdentifyA: vi.mocked(identifyUser).mock.calls.filter(([user]) => user.id === accountA.user.id).length - identifiedABefore,
        completionOutcome: cancellation instanceof DOMException ? cancellation.name : 'resolved-void',
      }
      console.info('AUTH_LIFECYCLE_OBSERVATION', JSON.stringify(observed))
      expect.soft(after).toEqual(before)
      expect.soft(liveView.result.current.user).toEqual(liveUserBefore)
      expect.soft(observed.lateSuccessEvents).toBe(0)
      expect.soft(observed.lateIdentifyA).toBe(0)
      expect.soft(cancellation).toBeInstanceOf(DOMException)
      expect.soft(cancellation).toMatchObject({ name: 'AbortError', message: 'Auth establishment superseded' })
    })

    it('control: ordinary successful establishment from anonymous', async () => {
      const view = await mountReady()
      vi.mocked(authApi[method]).mockResolvedValueOnce(accountA)
      await act(async () => { await start(view.result.current, method, accountA.user) })
      assertIdentity(view.result.current, accountA)
      expect(requestCount(method, accountA.user)).toBe(1)
      expect(successCount(method)).toBe(1)
      expect(identifyUser).toHaveBeenCalledWith(accountA.user)
      console.info('AUTH_LIFECYCLE_CONTROL', JSON.stringify({ strict, method, control: 'anonymous-success', requests: 1, successEvents: successCount(method) }))
    })

    it('control: current failure rejects without establishing anonymous credentials', async () => {
      const view = await mountReady()
      const failure = new Error('offline-current-request-failure')
      vi.mocked(authApi[method]).mockRejectedValueOnce(failure)
      await act(async () => { await expect(start(view.result.current, method, accountA.user)).rejects.toBe(failure) })
      assertIdentity(view.result.current, null)
      expect(requestCount(method, accountA.user)).toBe(1)
      expect(successCount(method)).toBe(0)
      expect(identifyUser).not.toHaveBeenCalled()
      console.info('AUTH_LIFECYCLE_CONTROL', JSON.stringify({ strict, method, control: 'anonymous-failure', requests: 1, successEvents: 0 }))
    })

    it('control: current failure preserves already-owned credentials', async () => {
      const view = await mountReady(owner)
      const before = storageSnapshot()
      const identifiesBefore = vi.mocked(identifyUser).mock.calls.length
      const failure = new Error('offline-owned-request-failure')
      vi.mocked(authApi[method]).mockRejectedValueOnce(failure)
      await act(async () => { await expect(start(view.result.current, method, accountA.user)).rejects.toBe(failure) })
      assertIdentity(view.result.current, owner)
      expect(storageSnapshot()).toEqual(before)
      expect(requestCount(method, accountA.user)).toBe(1)
      expect(successCount(method)).toBe(0)
      expect(identifyUser).toHaveBeenCalledTimes(identifiesBefore)
      console.info('AUTH_LIFECYCLE_CONTROL', JSON.stringify({ strict, method, control: 'owned-failure', requests: 1, successEvents: 0 }))
    })

    it('control: ordinary success replaces currently-owned credentials', async () => {
      const view = await mountReady(owner)
      vi.mocked(authApi[method]).mockResolvedValueOnce(accountA)
      await act(async () => { await start(view.result.current, method, accountA.user) })
      assertIdentity(view.result.current, accountA)
      expect(requestCount(method, accountA.user)).toBe(1)
      expect(successCount(method)).toBe(1)
      expect(identifyUser).toHaveBeenCalledWith(accountA.user)
      console.info('AUTH_LIFECYCLE_CONTROL', JSON.stringify({ strict, method, control: 'owned-success', requests: 1, successEvents: 1 }))
    })

    it.each([new ApiError(401, 'offline-api-failure'), new TypeError('offline-type-failure')])(
      'control: preserves current %s rejection identity', async failure => {
        const view = await mountReady()
        vi.mocked(authApi[method]).mockRejectedValueOnce(failure)
        await act(async () => { await expect(start(view.result.current, method, accountA.user)).rejects.toBe(failure) })
        assertIdentity(view.result.current, null)
        expect(successCount(method)).toBe(0)
        expect(identifyUser).not.toHaveBeenCalled()
      },
    )
  })

  it('control: explicit logout clears owned credentials and uses captured logout token', async () => {
    const view = await mountReady(owner)
    const resetsBefore = vi.mocked(resetAnalytics).mock.calls.length
    act(() => view.result.current.logout())
    await act(async () => {})
    assertIdentity(view.result.current, null)
    const logoutCalls = mockFetch.mock.calls.filter(([url]) => String(url).endsWith('/api/auth/logout'))
    expect(logoutCalls).toHaveLength(1)
    expect(logoutCalls[0]?.[1]?.headers).toEqual({ Authorization: `Bearer ${owner.access_token}` })
    expect(trackEvent).toHaveBeenCalledWith('logout')
    expect(vi.mocked(resetAnalytics).mock.calls.length).toBeGreaterThan(resetsBefore)
    console.info('AUTH_LIFECYCLE_CONTROL', JSON.stringify({ strict, control: 'owned-logout', logoutCalls: 1, cacheCleared: true }))
  })
})
