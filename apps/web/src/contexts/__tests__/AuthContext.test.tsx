import { renderHook, act, waitFor, render, screen, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { ReactNode, StrictMode } from 'react'
import i18next from 'i18next'
import { AuthIdentityQueryBoundary, AuthProvider, useAuth, User } from '../AuthContext'
import * as api from '@/lib/api'
import { trackEvent } from '@/lib/analytics'
import { getPreferredProjectType, setPreferredProjectType } from '@/lib/preferredProjectType'

// Mock authApi
vi.mock('@/lib/api', () => ({
  authApi: {
    login: vi.fn(),
    register: vi.fn(),
    verifyEmail: vi.fn(),
    resendVerification: vi.fn(),
  },
}))

// Mock fetch for /api/auth/me and /api/auth/refresh calls
vi.mock('@/lib/analytics', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/analytics')>()),
  trackEvent: vi.fn(),
}))

const mockFetch = vi.fn()
global.fetch = mockFetch

// Mock console methods to reduce noise (following pattern from ProjectContext.test.tsx)
vi.spyOn(console, 'log').mockImplementation(() => {})
vi.spyOn(console, 'warn').mockImplementation(() => {})
vi.spyOn(console, 'error').mockImplementation(() => {})

// Helper to create wrapper
function createWrapper() {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <AuthProvider>{children}</AuthProvider>
  }
}

// Helper to create mock user
function createMockUser(overrides?: Partial<User>): User {
  return {
    id: 'user-123',
    username: 'testuser',
    email: 'test@example.com',
    email_verified: true,
    avatar_url: undefined,
    nickname: undefined,
    is_active: true,
    is_superuser: false,
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
    ...overrides,
  }
}

// Helper to create mock auth response
function createMockAuthResponse(user?: Partial<User>) {
  return {
    access_token: 'mock-access-token',
    refresh_token: 'mock-refresh-token',
    user: createMockUser(user),
  }
}

describe('AuthContext', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockFetch.mockReset()
    // Clear localStorage
    localStorage.clear()
    sessionStorage.clear()
    // Reset window.location.href mock
    Object.defineProperty(window, 'location', {
      value: {
        href: '',
        search: '',
        origin: 'http://localhost:5173',
        pathname: '/',
      },
      writable: true,
    })
  })

  afterEach(() => {
    vi.clearAllTimers()
  })

  describe('initial state', () => {
    it('starts with user=null when no tokens', async () => {
      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })

      // Wait for initialization to complete
      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      expect(result.current.user).toBe(null)
    })

    it('initializes with user from localStorage when tokens exist and valid', async () => {
      const mockUser = createMockUser()

      localStorage.setItem('access_token', 'valid-token')
      localStorage.setItem('refresh_token', 'valid-refresh-token')
      localStorage.setItem('user', JSON.stringify(mockUser))

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(mockUser),
      })

      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      expect(result.current.user).toEqual(mockUser)
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/auth/me'),
        expect.objectContaining({
          headers: expect.objectContaining({
            Authorization: 'Bearer valid-token',
          }),
        })
      )
    })

    it('attempts token refresh when access token is invalid', async () => {
      const mockUser = createMockUser()

      localStorage.setItem('access_token', 'invalid-token')
      localStorage.setItem('refresh_token', 'valid-refresh-token')

      // First call to /me returns 401
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 401,
      })

      // Refresh call succeeds
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () =>
          Promise.resolve({
            access_token: 'new-access-token',
            refresh_token: 'new-refresh-token',
            user: mockUser,
          }),
      })

      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      expect(result.current.user).toEqual(mockUser)
      expect(localStorage.getItem('access_token')).toBe('new-access-token')
      expect(localStorage.getItem('refresh_token')).toBe('new-refresh-token')
    })

    it('clears auth state when refresh fails', async () => {
      localStorage.setItem('access_token', 'invalid-token')
      localStorage.setItem('refresh_token', 'invalid-refresh-token')
      localStorage.setItem('user', JSON.stringify(createMockUser()))

      // /me returns 401
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 401,
      })

      // Refresh fails
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 401,
      })

      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      expect(result.current.user).toBe(null)
      expect(localStorage.getItem('access_token')).toBe(null)
      expect(localStorage.getItem('refresh_token')).toBe(null)
    })

    it('handles network error during initialization gracefully', async () => {
      localStorage.setItem('access_token', 'some-token')

      // Network error
      mockFetch.mockRejectedValueOnce(new Error('Network error'))

      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      // Should clear user but preserve tokens (transient failure)
      expect(result.current.user).toBe(null)
      // Tokens preserved for potential retry
      expect(localStorage.getItem('access_token')).toBe('some-token')
    })

    it('preserves tokens when init refresh receives a retryable HTTP failure', async () => {
      localStorage.setItem('access_token', 'expired-access')
      localStorage.setItem('refresh_token', 'valid-refresh')
      mockFetch
        .mockResolvedValueOnce({ ok: false, status: 401 })
        .mockResolvedValueOnce({ ok: false, status: 503 })

      const { result } = renderHook(() => useAuth(), { wrapper: createWrapper() })
      await waitFor(() => expect(result.current.loading).toBe(false))

      expect(result.current.user).toBeNull()
      expect(localStorage.getItem('access_token')).toBe('expired-access')
      expect(localStorage.getItem('refresh_token')).toBe('valid-refresh')
    })
  })

  describe('login', () => {
    it('clears account A queries before account B mounts and ignores a late A request', async () => {
      let resolveLateA!: (value: string) => void
      let aRequestCount = 0
      const loadIdentityData = vi.fn((userId: string) => {
        if (userId === 'user-a') {
          aRequestCount += 1
          return aRequestCount === 1
            ? Promise.resolve('account-a-data')
            : new Promise<string>(resolve => { resolveLateA = resolve })
        }
        return Promise.resolve('account-b-data')
      })
      vi.mocked(api.authApi.login).mockImplementation(async (username: string) =>
        createMockAuthResponse({ id: username, username }),
      )
      mockFetch.mockResolvedValue({ ok: true })
      const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false, staleTime: Infinity } },
      })

      function Probe() {
        const auth = useAuth()
        const query = useQuery({
          queryKey: ['identity-data'],
          enabled: Boolean(auth.user),
          queryFn: () => loadIdentityData(auth.user!.id),
        })
        return (
          <div>
            <button onClick={() => void auth.login('user-a', 'password')}>Login A</button>
            <button onClick={() => void auth.login('user-b', 'password')}>Login B</button>
            <button onClick={auth.logout}>Logout</button>
            <span>{query.data}</span>
          </div>
        )
      }

      render(
        <QueryClientProvider client={queryClient}>
          <AuthProvider>
            <AuthIdentityQueryBoundary><Probe /></AuthIdentityQueryBoundary>
          </AuthProvider>
        </QueryClientProvider>,
      )
      await screen.findByRole('button', { name: 'Login A' })
      fireEvent.click(screen.getByRole('button', { name: 'Login A' }))
      expect(await screen.findByText('account-a-data')).toBeInTheDocument()

      void queryClient.invalidateQueries({ queryKey: ['identity-data'] })
      await waitFor(() => expect(loadIdentityData).toHaveBeenCalledTimes(2))
      fireEvent.click(screen.getByRole('button', { name: 'Logout' }))
      await waitFor(() => expect(screen.queryByText('account-a-data')).not.toBeInTheDocument())
      fireEvent.click(screen.getByRole('button', { name: 'Login B' }))
      expect(await screen.findByText('account-b-data')).toBeInTheDocument()

      resolveLateA('late-account-a-data')
      await act(async () => Promise.resolve())

      expect(screen.getByText('account-b-data')).toBeInTheDocument()
      expect(screen.queryByText('late-account-a-data')).not.toBeInTheDocument()
      expect(queryClient.getQueryData(['identity-data'])).toBe('account-b-data')
      expect(loadIdentityData.mock.calls.filter(([id]) => id === 'user-b')).toHaveLength(1)
    })

    it('does not let a rejected stale init request clear a newer login', async () => {
      localStorage.setItem('access_token', 'old-access')
      localStorage.setItem('refresh_token', 'old-refresh')
      let rejectInit!: (error: Error) => void
      mockFetch.mockReturnValueOnce(new Promise((_resolve, reject) => { rejectInit = reject }))
      const newResponse = createMockAuthResponse({ id: 'new-user' })
      vi.mocked(api.authApi.login).mockResolvedValueOnce(newResponse)
      const { result } = renderHook(() => useAuth(), { wrapper: createWrapper() })

      await act(async () => result.current.login('new-user', 'password'))
      rejectInit(new Error('old request failed'))

      await waitFor(() => expect(result.current.loading).toBe(false))
      expect(result.current.user).toEqual(newResponse.user)
      expect(localStorage.getItem('access_token')).toBe(newResponse.access_token)
    })

    it('logs in successfully with valid credentials', async () => {
      const mockResponse = createMockAuthResponse()
      vi.mocked(api.authApi.login).mockResolvedValueOnce(mockResponse)

      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      await act(async () => {
        await result.current.login('testuser', 'password123')
      })

      expect(api.authApi.login).toHaveBeenCalledWith('testuser', 'password123')
      expect(result.current.user).toEqual(mockResponse.user)
      expect(localStorage.getItem('access_token')).toBe(mockResponse.access_token)
      expect(localStorage.getItem('refresh_token')).toBe(mockResponse.refresh_token)
    })

    it('throws error on invalid credentials', async () => {
      const error = new Error('Invalid credentials')
      vi.mocked(api.authApi.login).mockRejectedValueOnce(error)

      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      await expect(
        act(async () => {
          await result.current.login('wronguser', 'wrongpass')
        })
      ).rejects.toThrow('Invalid credentials')

      expect(result.current.user).toBe(null)
    })

    it('stores user data in localStorage after login', async () => {
      const mockResponse = createMockAuthResponse({ username: 'newuser' })
      vi.mocked(api.authApi.login).mockResolvedValueOnce(mockResponse)

      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      await act(async () => {
        await result.current.login('newuser', 'password')
      })

      const storedUser = JSON.parse(localStorage.getItem('user') || '{}')
      expect(storedUser.username).toBe('newuser')
    })
  })

  describe('logout', () => {
    it('clears auth state and localStorage on logout', async () => {
      const mockResponse = createMockAuthResponse()
      vi.mocked(api.authApi.login).mockResolvedValueOnce(mockResponse)

      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      // First login
      await act(async () => {
        await result.current.login('testuser', 'password')
      })

      expect(result.current.user).not.toBe(null)

      // Then logout
      act(() => {
        result.current.logout()
      })

      expect(result.current.user).toBe(null)
      expect(localStorage.getItem('access_token')).toBe(null)
      expect(localStorage.getItem('refresh_token')).toBe(null)
      expect(localStorage.getItem('user')).toBe(null)
    })

    it('drops queued upgrade funnel events of the signed-out account', async () => {
      const mockResponse = createMockAuthResponse()
      vi.mocked(api.authApi.login).mockResolvedValueOnce(mockResponse)
      localStorage.setItem(
        'zenstory_upgrade_funnel_pending_events',
        JSON.stringify([{ event_name: 'upgrade_entry_click', action: 'click', source: 's', surface: 'modal', occurred_at: '2026-10-05T00:00:00Z' }]),
      )

      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })
      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })
      await act(async () => {
        await result.current.login('testuser', 'password')
      })

      act(() => {
        result.current.logout()
      })

      expect(localStorage.getItem('zenstory_upgrade_funnel_pending_events')).toBe(null)
    })

    it('clears locally immediately and sends best-effort server logout with the captured token', async () => {
      const mockUser = createMockUser()
      localStorage.setItem('access_token', 'captured-access')
      localStorage.setItem('refresh_token', 'captured-refresh')
      localStorage.setItem('user', JSON.stringify(mockUser))
      localStorage.setItem('auth_validated_at', Date.now().toString())
      mockFetch
        .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(mockUser) })
        .mockRejectedValueOnce(new Error('offline'))
      const { result } = renderHook(() => useAuth(), { wrapper: createWrapper() })
      await waitFor(() => expect(result.current.user).toEqual(mockUser))

      act(() => result.current.logout())

      expect(result.current.user).toBeNull()
      expect(localStorage.getItem('access_token')).toBeNull()
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/auth/logout'),
        expect.objectContaining({
          method: 'POST',
          headers: { Authorization: 'Bearer captured-access' },
        }),
      )
    })

    it('does not let late background validation resurrect a logged-out session', async () => {
      const mockUser = createMockUser()
      localStorage.setItem('access_token', 'old-access')
      localStorage.setItem('refresh_token', 'old-refresh')
      localStorage.setItem('user', JSON.stringify(mockUser))
      localStorage.setItem('auth_validated_at', Date.now().toString())
      let resolveValidation!: (response: unknown) => void
      mockFetch
        .mockReturnValueOnce(new Promise(resolve => { resolveValidation = resolve }))
        .mockResolvedValueOnce({ ok: true })
      const { result } = renderHook(() => useAuth(), { wrapper: createWrapper() })
      await waitFor(() => expect(result.current.user).toEqual(mockUser))

      act(() => result.current.logout())
      resolveValidation({ ok: true, json: () => Promise.resolve(mockUser) })

      await waitFor(() => expect(result.current.user).toBeNull())
      expect(localStorage.getItem('user')).toBeNull()
      expect(localStorage.getItem('access_token')).toBeNull()
    })

    it('does not let late background validation overwrite a newer account', async () => {
      const oldUser = createMockUser({ id: 'old-user', username: 'old-user' })
      const newResponse = createMockAuthResponse({ id: 'new-user', username: 'new-user' })
      localStorage.setItem('access_token', 'old-access')
      localStorage.setItem('refresh_token', 'old-refresh')
      localStorage.setItem('user', JSON.stringify(oldUser))
      localStorage.setItem('auth_validated_at', Date.now().toString())
      let resolveValidation!: (response: unknown) => void
      mockFetch.mockReturnValueOnce(new Promise(resolve => { resolveValidation = resolve }))
      vi.mocked(api.authApi.login).mockResolvedValueOnce(newResponse)
      const { result } = renderHook(() => useAuth(), { wrapper: createWrapper() })
      await waitFor(() => expect(result.current.user).toEqual(oldUser))

      await act(async () => result.current.login('new-user', 'password'))
      resolveValidation({ ok: true, json: () => Promise.resolve(oldUser) })

      await waitFor(() => expect(result.current.user).toEqual(newResponse.user))
      expect(localStorage.getItem('access_token')).toBe(newResponse.access_token)
      expect(JSON.parse(localStorage.getItem('user') || '{}').id).toBe('new-user')
    })
  })

  describe('register', () => {
    it('registers successfully and returns email info', async () => {
      const mockResponse = {
        email: 'new@example.com',
        email_verified: false,
      }
      vi.mocked(api.authApi.register).mockResolvedValueOnce(mockResponse)

      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      let registerResult
      await act(async () => {
        registerResult = await result.current.register('new@example.com', 'password123')
      })

      expect(api.authApi.register).toHaveBeenCalledWith({
        email: 'new@example.com',
        password: 'password123',
        language: 'en',
      })
      expect(registerResult).toEqual(mockResponse)
    })

    it('signs up in the visitor language and credits the entry they arrived from', async () => {
      localStorage.setItem('zenstory-language', 'en')
      sessionStorage.setItem('zenstory:entry-source', 'org_home_closing')
      vi.mocked(api.authApi.register).mockResolvedValueOnce({ email: 'new@example.com', email_verified: false })

      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })
      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })
      await act(async () => {
        await result.current.register('new@example.com', 'password123')
      })

      expect(api.authApi.register).toHaveBeenCalledWith(expect.objectContaining({ language: 'en' }))
      expect(trackEvent).toHaveBeenCalledWith('register_success', expect.objectContaining({ entry_source: 'org_home_closing' }))
    })

    it('does not authenticate user after registration', async () => {
      vi.mocked(api.authApi.register).mockResolvedValueOnce({
        email: 'new@example.com',
        email_verified: false,
      })

      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      await act(async () => {
        await result.current.register('new@example.com', 'password123')
      })

      // User should still be null after registration
      expect(result.current.user).toBe(null)
      expect(localStorage.getItem('access_token')).toBe(null)
    })
  })

  describe('handleOAuthCallback', () => {
    it('counts a Google sign-up as register_success only for new users', async () => {
      const mockUser = createMockUser()
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(mockUser),
      })
      vi.mocked(trackEvent).mockClear()

      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })
      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      await act(async () => {
        await result.current.handleOAuthCallback('new-access', 'new-refresh', { isNewUser: true })
      })
      expect(trackEvent).toHaveBeenCalledWith('register_success', expect.objectContaining({ method: 'google' }))
      expect(trackEvent).toHaveBeenCalledWith('oauth_callback_success', { is_new_user: true })

      vi.mocked(trackEvent).mockClear()
      await act(async () => {
        await result.current.handleOAuthCallback('old-access', 'old-refresh')
      })
      expect(trackEvent).not.toHaveBeenCalledWith('register_success', expect.anything())
      expect(trackEvent).toHaveBeenCalledWith('oauth_callback_success', { is_new_user: false })
      mockFetch.mockReset()
    })

    it('handles OAuth callback successfully', async () => {
      const mockUser = createMockUser()

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(mockUser),
      })

      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      setPreferredProjectType('screenplay')
      await act(async () => {
        await result.current.handleOAuthCallback('oauth-access-token', 'oauth-refresh-token')
      })

      expect(result.current.user).toEqual(mockUser)
      expect(localStorage.getItem('access_token')).toBe('oauth-access-token')
      expect(localStorage.getItem('refresh_token')).toBe('oauth-refresh-token')
      expect(getPreferredProjectType(mockUser.id)).toBe('screenplay')
      expect(getPreferredProjectType()).toBeNull()
    })

    it('uses HTTPS API base while completing OAuth callback on HTTPS pages', async () => {
      Object.defineProperty(window, 'location', {
        value: {
          href: '',
          search: '',
          origin: 'https://zenstory.ai',
          pathname: '/',
          protocol: 'https:',
        },
        writable: true,
      })
      const mockUser = createMockUser()
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(mockUser),
      })

      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      await act(async () => {
        await result.current.handleOAuthCallback('oauth-access-token', 'oauth-refresh-token')
      })

      expect(mockFetch).toHaveBeenCalledWith(
        'https://localhost:8000/api/auth/me',
        expect.objectContaining({
          headers: expect.objectContaining({
            Authorization: 'Bearer oauth-access-token',
          }),
        }),
      )
      expect(result.current.user).toEqual(mockUser)
    })

    it('logs out on OAuth callback failure', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 401,
      })

      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      await expect(
        act(async () => {
          await result.current.handleOAuthCallback('invalid-token', 'invalid-refresh')
        })
      ).rejects.toThrow('Failed to fetch user info')

      expect(result.current.user).toBe(null)
    })
  })

  describe('verifyEmail', () => {
    it('verifies email and authenticates user', async () => {
      const mockResponse = createMockAuthResponse()
      vi.mocked(api.authApi.verifyEmail).mockResolvedValueOnce(mockResponse)

      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      setPreferredProjectType('short')
      await act(async () => {
        await result.current.verifyEmail('test@example.com', '123456')
      })

      expect(api.authApi.verifyEmail).toHaveBeenCalledWith('test@example.com', '123456')
      expect(result.current.user).toEqual(mockResponse.user)
      expect(localStorage.getItem('access_token')).toBe(mockResponse.access_token)
      expect(getPreferredProjectType(mockResponse.user.id)).toBe('short')
      expect(getPreferredProjectType()).toBeNull()
    })
  })

  describe('resendVerification', () => {
    it('calls resendVerification API', async () => {
      vi.mocked(api.authApi.resendVerification).mockResolvedValueOnce({ success: true })

      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      await act(async () => {
        await result.current.resendVerification('test@example.com')
      })

      expect(api.authApi.resendVerification).toHaveBeenCalledWith('test@example.com')
    })
  })

  describe('googleLogin', () => {
    it('redirects to Google OAuth endpoint', async () => {
      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      act(() => {
        result.current.googleLogin({ planIntent: 'pro' })
      })

      expect(window.location.href).toContain('/api/auth/google')
      expect(sessionStorage.getItem('oauth_plan_intent')).toBe('pro')
    })

    it('passes redirect parameter to OAuth endpoint', async () => {
      // Mock URL with redirect parameter
      Object.defineProperty(window, 'location', {
        value: {
          href: '',
          search: '?redirect=http://example.com/callback',
          origin: 'http://localhost:5173',
          pathname: '/',
        },
        writable: true,
      })

      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      act(() => {
        result.current.googleLogin()
      })

      // The redirect parameter should be URL-encoded in the URL
      expect(window.location.href).toContain('redirect=http%3A%2F%2Fexample.com%2Fcallback')
    })

    it('upgrades Google OAuth endpoint to HTTPS on HTTPS pages', async () => {
      Object.defineProperty(window, 'location', {
        value: {
          href: '',
          search: '',
          origin: 'https://zenstory.ai',
          pathname: '/',
          protocol: 'https:',
        },
        writable: true,
      })

      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      act(() => {
        result.current.googleLogin()
      })

      expect(window.location.href).toMatch(/^https:\/\/localhost:8000\/api\/auth\/google/)
    })

    it('passes normalized current UI language to Google OAuth endpoint', async () => {
      const previousLanguage = i18next.language
      Object.defineProperty(i18next, 'language', { value: 'en-US', configurable: true })
      try {
        const { result } = renderHook(() => useAuth(), {
          wrapper: createWrapper(),
        })

        await waitFor(() => {
          expect(result.current.loading).toBe(false)
        })

        act(() => {
          result.current.googleLogin()
        })

        const redirected = new URL(window.location.href)
        expect(redirected.pathname).toBe('/api/auth/google')
        expect(redirected.searchParams.get('lang')).toBe('en')
      } finally {
        Object.defineProperty(i18next, 'language', { value: previousLanguage, configurable: true })
      }
    })
  })

  describe('auth:logout event', () => {
    it('listens for auth:logout event and clears user state', async () => {
      const mockResponse = createMockAuthResponse()
      vi.mocked(api.authApi.login).mockResolvedValueOnce(mockResponse)

      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      // Login first
      await act(async () => {
        await result.current.login('testuser', 'password')
      })

      expect(result.current.user).not.toBe(null)

      // Dispatch logout event
      act(() => {
        window.dispatchEvent(new CustomEvent('auth:logout'))
      })

      expect(result.current.user).toBe(null)
    })
  })

  describe('useAuth hook', () => {
    it('throws error when used outside AuthProvider', () => {
      const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})

      expect(() => {
        renderHook(() => useAuth())
      }).toThrow('useAuth must be used within an AuthProvider')

      consoleSpy.mockRestore()
    })

    it('provides all auth context values', async () => {
      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      expect(result.current).toHaveProperty('user')
      expect(result.current).toHaveProperty('loading')
      expect(result.current).toHaveProperty('login')
      expect(result.current).toHaveProperty('register')
      expect(result.current).toHaveProperty('logout')
      expect(result.current).toHaveProperty('handleOAuthCallback')
      expect(result.current).toHaveProperty('verifyEmail')
      expect(result.current).toHaveProperty('resendVerification')
      expect(result.current).toHaveProperty('googleLogin')

      expect(typeof result.current.login).toBe('function')
      expect(typeof result.current.register).toBe('function')
      expect(typeof result.current.logout).toBe('function')
    })
  })

  describe('token persistence', () => {
    it('persists tokens across re-renders', async () => {
      const mockResponse = createMockAuthResponse()
      vi.mocked(api.authApi.login).mockResolvedValueOnce(mockResponse)

      const { result, rerender } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      await act(async () => {
        await result.current.login('testuser', 'password')
      })

      expect(localStorage.getItem('access_token')).toBe(mockResponse.access_token)

      // Rerender should not clear tokens
      rerender()

      expect(localStorage.getItem('access_token')).toBe(mockResponse.access_token)
      expect(result.current.user).toEqual(mockResponse.user)
    })

    it('clears expired tokens on initialization', async () => {
      localStorage.setItem('access_token', 'expired-token')
      localStorage.setItem('refresh_token', 'expired-refresh-token')
      localStorage.setItem('user', JSON.stringify(createMockUser()))

      // /me returns 401
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 401,
      })

      // Refresh also fails
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 401,
      })

      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      expect(result.current.user).toBe(null)
      expect(localStorage.getItem('access_token')).toBe(null)
      expect(localStorage.getItem('refresh_token')).toBe(null)
    })
  })

  describe('loading states', () => {
    it('completes loading after initialization', async () => {
      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })

      // Wait for initialization to complete
      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      // Loading should be false after init completes
      expect(result.current.loading).toBe(false)
      expect(result.current.user).toBe(null)
    })
  })

  describe('protected route scenarios', () => {
    it('allows access when authenticated', async () => {
      const mockUser = createMockUser()

      localStorage.setItem('access_token', 'valid-token')

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(mockUser),
      })

      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      // Simulate protected route check
      const isAuthenticated = result.current.user !== null
      expect(isAuthenticated).toBe(true)
    })

    it('denies access when not authenticated', async () => {
      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      // Simulate protected route check
      const isAuthenticated = result.current.user !== null
      expect(isAuthenticated).toBe(false)
    })
  })

  describe('OAuth flow scenarios', () => {
    it('initiates Google OAuth flow', async () => {
      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      act(() => {
        result.current.googleLogin()
      })

      // Should redirect to OAuth endpoint
      expect(window.location.href).toMatch(/\/api\/auth\/google/)
    })

    it('completes OAuth flow with handleOAuthCallback', async () => {
      const mockUser = createMockUser()

      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(mockUser),
      })

      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      await act(async () => {
        await result.current.handleOAuthCallback('token', 'refresh')
      })

      expect(result.current.user).toEqual(mockUser)
      expect(localStorage.getItem('access_token')).toBe('token')
    })

    it('handles OAuth failure gracefully', async () => {
      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 500,
      })

      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      await expect(
        act(async () => {
          await result.current.handleOAuthCallback('bad-token', 'bad-refresh')
        })
      ).rejects.toThrow()

      expect(result.current.user).toBe(null)
    })
  })

  describe('error state handling', () => {
    it('handles login errors without crashing', async () => {
      vi.mocked(api.authApi.login).mockRejectedValueOnce(new Error('Network error'))

      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      await expect(
        act(async () => {
          await result.current.login('user', 'pass')
        })
      ).rejects.toThrow('Network error')

      // Context should remain in clean state
      expect(result.current.user).toBe(null)
      expect(result.current.loading).toBe(false)
    })

    it('handles registration errors without crashing', async () => {
      vi.mocked(api.authApi.register).mockRejectedValueOnce(new Error('Email already exists'))

      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      await expect(
        act(async () => {
          await result.current.register('existing@example.com', 'pass')
        })
      ).rejects.toThrow('Email already exists')

      expect(result.current.user).toBe(null)
    })

    it('handles email verification errors without crashing', async () => {
      vi.mocked(api.authApi.verifyEmail).mockRejectedValueOnce(new Error('Invalid code'))

      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      await expect(
        act(async () => {
          await result.current.verifyEmail('test@example.com', 'wrong')
        })
      ).rejects.toThrow('Invalid code')

      expect(result.current.user).toBe(null)
    })
  })

  describe('concurrent operations', () => {
    it('handles multiple logout calls gracefully', async () => {
      const mockResponse = createMockAuthResponse()
      vi.mocked(api.authApi.login).mockResolvedValueOnce(mockResponse)

      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      await act(async () => {
        await result.current.login('testuser', 'password')
      })

      // Multiple logout calls should not crash
      act(() => {
        result.current.logout()
        result.current.logout()
        result.current.logout()
      })

      expect(result.current.user).toBe(null)
      expect(localStorage.getItem('access_token')).toBe(null)
    })
  })

  // ========================================
  // Cache TTL Tests
  // ========================================
  describe('cache TTL behavior', () => {
    it('uses cached user when within TTL (cache hit)', async () => {
      const mockUser = createMockUser()

      // Set up cached user with recent timestamp (within TTL)
      localStorage.setItem('access_token', 'valid-token')
      localStorage.setItem('refresh_token', 'valid-refresh-token')
      localStorage.setItem('user', JSON.stringify(mockUser))
      localStorage.setItem('auth_validated_at', Date.now().toString())

      // Mock fetch for background validation (should be called after cache hit)
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(mockUser),
      })

      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })

      // User should be set immediately from cache (not waiting for fetch)
      await waitFor(() => {
        expect(result.current.user).toEqual(mockUser)
      })

      // Background validation should be called
      await waitFor(() => {
        expect(mockFetch).toHaveBeenCalledWith(
          expect.stringContaining('/api/auth/me'),
          expect.objectContaining({
            headers: expect.objectContaining({
              Authorization: 'Bearer valid-token',
            }),
          })
        )
      })
    })

    it('validates with server when cache TTL expired (cache miss)', async () => {
      const mockUser = createMockUser()

      // Set up cached user with expired timestamp (older than 5 minutes TTL)
      const fiveMinutesAgo = Date.now() - 5 * 60 * 1000 - 1000 // 1 second past TTL
      localStorage.setItem('access_token', 'valid-token')
      localStorage.setItem('refresh_token', 'valid-refresh-token')
      localStorage.setItem('user', JSON.stringify(mockUser))
      localStorage.setItem('auth_validated_at', fiveMinutesAgo.toString())

      // Mock fetch for server validation
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(mockUser),
      })

      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      expect(result.current.user).toEqual(mockUser)
      // Should have validated with server (not just used cache)
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/auth/me'),
        expect.any(Object)
      )
    })

    it('clears auth_validated_at on logout', async () => {
      const mockResponse = createMockAuthResponse()
      vi.mocked(api.authApi.login).mockResolvedValueOnce(mockResponse)

      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      await act(async () => {
        await result.current.login('testuser', 'password')
      })

      expect(localStorage.getItem('auth_validated_at')).not.toBe(null)

      act(() => {
        result.current.logout()
      })

      expect(localStorage.getItem('auth_validated_at')).toBe(null)
    })

    it('handles corrupted auth_validated_at gracefully', async () => {
      const mockUser = createMockUser()

      localStorage.setItem('access_token', 'valid-token')
      localStorage.setItem('refresh_token', 'valid-refresh-token')
      localStorage.setItem('user', JSON.stringify(mockUser))
      localStorage.setItem('auth_validated_at', 'not-a-number')

      // Mock fetch for server validation (should be called when cache is invalid)
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(mockUser),
      })

      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      // Should have fallen back to server validation
      expect(result.current.user).toEqual(mockUser)
    })
  })

  // ========================================
  // Registration Payload Tests
  // ========================================
  describe('register payload', () => {
    it('passes email, password, and language to API', async () => {
      const mockResponse = {
        email: 'new@example.com',
        email_verified: false,
      }
      vi.mocked(api.authApi.register).mockResolvedValueOnce(mockResponse)

      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      await act(async () => {
        await result.current.register('new@example.com', 'password123')
      })

      expect(api.authApi.register).toHaveBeenCalledWith({
        email: 'new@example.com',
        password: 'password123',
        language: 'en',
      })
    })
  })

  // ========================================
  // Corrupted localStorage Tests
  // ========================================
  describe('corrupted localStorage handling', () => {
    it('handles corrupted user JSON gracefully', async () => {
      localStorage.setItem('access_token', 'valid-token')
      localStorage.setItem('user', 'not-valid-json')
      localStorage.setItem('auth_validated_at', Date.now().toString())

      // Mock fetch for server validation
      mockFetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(createMockUser()),
      })

      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      // Should fall back to server validation
      expect(mockFetch).toHaveBeenCalled()
    })
  })

  // ========================================
  // Login State Persistence Tests
  // ========================================
  describe('login state persistence', () => {
    it('saves auth_validated_at timestamp on login', async () => {
      const mockResponse = createMockAuthResponse()
      vi.mocked(api.authApi.login).mockResolvedValueOnce(mockResponse)

      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      await act(async () => {
        await result.current.login('testuser', 'password')
      })

      const validatedAt = localStorage.getItem('auth_validated_at')
      expect(validatedAt).not.toBe(null)
      // Should be a recent timestamp
      expect(parseInt(validatedAt!, 10)).toBeGreaterThan(Date.now() - 1000)
    })

    it('saves auth_validated_at timestamp on verifyEmail', async () => {
      const mockResponse = createMockAuthResponse()
      vi.mocked(api.authApi.verifyEmail).mockResolvedValueOnce(mockResponse)

      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      await act(async () => {
        await result.current.verifyEmail('test@example.com', '123456')
      })

      const validatedAt = localStorage.getItem('auth_validated_at')
      expect(validatedAt).not.toBe(null)
    })
  })

  // ========================================
  // Edge Cases
  // ========================================
  describe('edge cases', () => {
    it('handles logout when already logged out', async () => {
      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      expect(result.current.user).toBe(null)

      // Logout when already logged out should not throw
      act(() => {
        result.current.logout()
      })

      expect(result.current.user).toBe(null)
    })

    it('preserves user data between re-renders', async () => {
      const mockResponse = createMockAuthResponse()
      vi.mocked(api.authApi.login).mockResolvedValueOnce(mockResponse)

      const { result, rerender } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      await act(async () => {
        await result.current.login('testuser', 'password')
      })

      const userBeforeRerender = result.current.user

      rerender()

      expect(result.current.user).toEqual(userBeforeRerender)
    })

    it('handles empty string tokens', async () => {
      localStorage.setItem('access_token', '')
      localStorage.setItem('refresh_token', '')

      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.loading).toBe(false)
      })

      // Empty string is falsy, so user should be null
      expect(result.current.user).toBe(null)
    })
  })

  describe('account ui_language backfill deduplication', () => {
    it('sends PUT /api/auth/me/preferences exactly once under StrictMode and racing background /api/auth/me', async () => {
      const previousLanguage = i18next.language
      Object.defineProperty(i18next, 'language', { value: 'en-US', configurable: true })

      try {
        const cachedUser = createMockUser({ id: 'backfill-user-1', ui_language: null })
        localStorage.setItem('access_token', 'valid-token')
        localStorage.setItem('refresh_token', 'valid-refresh')
        localStorage.setItem('user', JSON.stringify(cachedUser))
        localStorage.setItem('auth_validated_at', String(Date.now()))

        let resolveMe!: (value: unknown) => void
        const mePromise = new Promise(resolve => { resolveMe = resolve })
        const preferencePuts: Array<{ body: string | null }> = []

        mockFetch.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
          const url = String(input)
          if (url.endsWith('/api/auth/me')) {
            return mePromise
          }
          if (url.endsWith('/api/auth/me/preferences') && init?.method === 'PUT') {
            preferencePuts.push({ body: typeof init.body === 'string' ? init.body : null })
            return { ok: true, status: 200, json: async () => ({ ui_language: 'en' }) }
          }
          return { ok: true, status: 200, json: async () => ({}) }
        })

        const strictWrapper = ({ children }: { children: ReactNode }) => (
          <StrictMode>
            <AuthProvider>{children}</AuthProvider>
          </StrictMode>
        )

        const { result, rerender } = renderHook(() => useAuth(), { wrapper: strictWrapper })

        await waitFor(() => {
          expect(preferencePuts).toHaveLength(1)
        })
        expect(JSON.parse(preferencePuts[0].body ?? '{}')).toEqual({ ui_language: 'en', only_if_unset: true })
        expect(result.current.user?.ui_language).toBe('en')
        expect(JSON.parse(localStorage.getItem('user') ?? '{}').ui_language).toBe('en')

        // Background /api/auth/me returns stale ui_language: null (e.g. raced ahead of PUT).
        await act(async () => {
          resolveMe({
            ok: true,
            status: 200,
            json: async () => createMockUser({ id: 'backfill-user-1', ui_language: null }),
          })
          await Promise.resolve()
        })
        rerender()
        await new Promise(resolve => setTimeout(resolve, 20))

        expect(preferencePuts).toHaveLength(1)
        expect(result.current.user?.ui_language).toBe('en')
        expect(JSON.parse(localStorage.getItem('user') ?? '{}').ui_language).toBe('en')
      } finally {
        Object.defineProperty(i18next, 'language', { value: previousLanguage, configurable: true })
      }
    })

    it('sends zero preference PUTs when account already has ui_language, and backfills once per distinct null account', async () => {
      const previousLanguage = i18next.language
      Object.defineProperty(i18next, 'language', { value: 'en', configurable: true })

      try {
        const preferencePuts: Array<{ url: string; body: string | null }> = []
        mockFetch.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
          const url = String(input)
          if (url.endsWith('/api/auth/me/preferences') && init?.method === 'PUT') {
            preferencePuts.push({ url, body: typeof init.body === 'string' ? init.body : null })
            return { ok: true, status: 200, json: async () => ({ ui_language: 'en' }) }
          }
          return { ok: true, status: 200, json: async () => ({}) }
        })

        // Account A already has saved ui_language -> zero PUTs.
        vi.mocked(api.authApi.login).mockResolvedValueOnce(
          createMockAuthResponse({ id: 'user-saved', ui_language: 'en' }),
        )
        const { result } = renderHook(() => useAuth(), { wrapper: createWrapper() })
        await waitFor(() => expect(result.current.loading).toBe(false))

        await act(async () => {
          await result.current.login('saved@example.com', 'password')
        })
        await new Promise(resolve => setTimeout(resolve, 20))
        expect(preferencePuts).toHaveLength(0)

        // Sign out and sign in as Account B with ui_language: null -> exactly one PUT.
        act(() => {
          result.current.logout()
        })
        vi.mocked(api.authApi.login).mockResolvedValueOnce(
          createMockAuthResponse({ id: 'user-null-b', ui_language: null }),
        )
        await act(async () => {
          await result.current.login('nullb@example.com', 'password')
        })
        await waitFor(() => expect(preferencePuts).toHaveLength(1))
        expect(JSON.parse(preferencePuts[0].body ?? '{}')).toEqual({ ui_language: 'en', only_if_unset: true })
      } finally {
        Object.defineProperty(i18next, 'language', { value: previousLanguage, configurable: true })
      }
    })

    it('adopts server-stored ui_language when only_if_unset backfill discovers an existing value', async () => {
      const previousLanguage = i18next.language
      const changeLanguageSpy = vi.spyOn(i18next, 'changeLanguage').mockResolvedValue(i18next.t)
      Object.defineProperty(i18next, 'language', { value: 'en', configurable: true })

      try {
        const preferencePuts: Array<{ body: string | null }> = []
        mockFetch.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
          const url = String(input)
          if (url.endsWith('/api/auth/me/preferences') && init?.method === 'PUT') {
            preferencePuts.push({ body: typeof init.body === 'string' ? init.body : null })
            // Simulates /api/auth/me having returned null transiently while DB already holds 'zh'.
            return { ok: true, status: 200, json: async () => ({ ui_language: 'zh' }) }
          }
          return { ok: true, status: 200, json: async () => ({}) }
        })

        vi.mocked(api.authApi.login).mockResolvedValueOnce(
          createMockAuthResponse({ id: 'user-transient-null', ui_language: null }),
        )
        const { result } = renderHook(() => useAuth(), { wrapper: createWrapper() })
        await waitFor(() => expect(result.current.loading).toBe(false))

        await act(async () => {
          await result.current.login('transient@example.com', 'password')
        })

        await waitFor(() => expect(result.current.user?.ui_language).toBe('zh'))
        expect(preferencePuts).toHaveLength(1)
        expect(JSON.parse(preferencePuts[0].body ?? '{}')).toEqual({ ui_language: 'en', only_if_unset: true })
        expect(changeLanguageSpy).toHaveBeenCalledWith('zh')
        expect(JSON.parse(localStorage.getItem('user') ?? '{}').ui_language).toBe('zh')
      } finally {
        changeLanguageSpy.mockRestore()
        Object.defineProperty(i18next, 'language', { value: previousLanguage, configurable: true })
      }
    })
  })
})
