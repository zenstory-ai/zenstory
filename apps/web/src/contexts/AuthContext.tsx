import React, { createContext, useContext, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { authApi } from '../lib/api';
import type { UserSubscription, UsageQuota } from '../types/subscription';
import { logger } from '../lib/logger';
import { clearAuthStorage, getApiBase, resolveOwnedAuthSession, tryRefreshToken as tryRefreshTokenSingleFlight } from '../lib/apiClient';
import { identifyUser, resetAnalytics, trackEvent } from '../lib/analytics';
import { saveOAuthPlanIntent, type PlanIntent } from '../lib/authFlow';
import { clearPendingUpgradeFunnelEvents } from '../lib/upgradeAnalytics';

export interface User {
  id: string;
  username: string;
  email: string;
  email_verified: boolean;
  avatar_url?: string;
  nickname?: string;
  is_active: boolean;
  is_superuser: boolean;
  created_at: string;
  updated_at: string;
  subscription?: UserSubscription;
  quota?: UsageQuota;
}

export interface AuthContextType {
  user: User | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  register: (username: string, email: string, password: string, inviteCode?: string) => Promise<{ email: string; email_verified: boolean }>;
  logout: () => void;
  refreshToken: () => Promise<void>;
  handleOAuthCallback: (
    accessToken: string,
    refreshToken: string,
    options?: { isNewUser?: boolean },
  ) => Promise<void>;
  verifyEmail: (email: string, code: string) => Promise<void>;
  resendVerification: (email: string) => Promise<void>;
  googleLogin: (options?: { inviteCode?: string; redirectUrl?: string; planIntent?: PlanIntent | null }) => void;
  appleLogin: () => void;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

/**
 * Clears identity-bound React Query state before descendants for a new user
 * are allowed to mount. AuthProvider intentionally stays independent from
 * QueryClientProvider so its unit and embedded consumers keep working.
 */
export const AuthIdentityQueryBoundary: React.FC<{ children: ReactNode }> = ({ children }) => {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const identity = user?.id ?? null;
  const [clearedIdentity, setClearedIdentity] = useState<string | null>(identity);

  React.useLayoutEffect(() => {
    if (clearedIdentity === identity) return;

    // Cancellation prevents cooperative query functions from finishing, while
    // removal also detaches non-cooperative in-flight requests from the cache.
    void queryClient.cancelQueries();
    queryClient.removeQueries();
    setClearedIdentity(identity);
  }, [clearedIdentity, identity, queryClient]);

  if (clearedIdentity !== identity) return null;

  return <React.Fragment key={identity ?? 'anonymous'}>{children}</React.Fragment>;
};

// Cache TTL: 5 minutes (conservative to balance performance and security)
const AUTH_CACHE_TTL_MS = 5 * 60 * 1000;

// Cache keys
const CACHE_KEYS = {
  USER: 'user',
  VALIDATED_AT: 'auth_validated_at',
  ACCESS_TOKEN: 'access_token',
  REFRESH_TOKEN: 'refresh_token',
} as const;

// Helper to clear auth state (including cache)
const clearAuthState = (setUser: (user: User | null) => void, reason = 'auth_state_cleared') => {
  clearAuthStorage(reason);
  // Queued funnel events belong to the account that is signing out.
  clearPendingUpgradeFunnelEvents();
  setUser(null);
};

// Helper to save user cache with timestamp
const saveUserCache = (userData: User) => {
  try {
    localStorage.setItem(CACHE_KEYS.USER, JSON.stringify(userData));
    localStorage.setItem(CACHE_KEYS.VALIDATED_AT, Date.now().toString());
  } catch {
    logger.warn('[Auth] Failed to save user cache (localStorage may be unavailable)');
  }
};

// Helper to read cached user if valid (within TTL)
const getValidCachedUser = (): User | null => {
  try {
    const cachedUserStr = localStorage.getItem(CACHE_KEYS.USER);
    const lastValidatedStr = localStorage.getItem(CACHE_KEYS.VALIDATED_AT);

    if (!cachedUserStr || !lastValidatedStr) {
      return null;
    }

    const lastValidated = parseInt(lastValidatedStr, 10);
    const now = Date.now();

    // Check if cache is still within TTL
    if (now - lastValidated < AUTH_CACHE_TTL_MS) {
      return JSON.parse(cachedUserStr) as User;
    }

    return null;
  } catch {
    logger.warn('[Auth] Failed to read user cache (corrupted data)');
    return null;
  }
};

// Helper to attempt token refresh
const attemptTokenRefresh = async (
  setUser: (user: User | null) => void,
  ownsGeneration: () => boolean,
): Promise<boolean> => {
  const refreshed = await tryRefreshTokenSingleFlight();
  if (!refreshed || !ownsGeneration()) return false;

  // tryRefreshTokenSingleFlight already persisted tokens + user cache.
  const cachedUserStr = localStorage.getItem(CACHE_KEYS.USER);
  if (cachedUserStr) {
    try {
      const cachedUser = JSON.parse(cachedUserStr) as User;
      if (ownsGeneration()) setUser(cachedUser);
    } catch {
      // Corrupted cache - fallback to unauthenticated state.
      if (ownsGeneration()) setUser(null);
    }
  }

  return ownsGeneration();
};

export const AuthProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const previousUserIdRef = React.useRef<string | null>(null);
  const authGenerationRef = React.useRef(0);

  const ownsSession = React.useCallback((generation: number, accessToken: string, refreshToken: string | null) => (
    authGenerationRef.current === generation &&
    resolveOwnedAuthSession(accessToken, refreshToken) !== null
  ), []);

  // Initialize user from localStorage on mount with validation
  // Cache Strategy: Use cached user within TTL for instant load, validate in background
  useEffect(() => {
    const initializeAuth = async () => {
      const generation = authGenerationRef.current;
      let accessToken: string | null = null;
      let refreshToken: string | null = null;
      const ownsOriginalSession = () => (
        authGenerationRef.current === generation &&
        localStorage.getItem(CACHE_KEYS.ACCESS_TOKEN) === accessToken &&
        localStorage.getItem(CACHE_KEYS.REFRESH_TOKEN) === refreshToken
      );
      try {
        accessToken = localStorage.getItem(CACHE_KEYS.ACCESS_TOKEN);
        refreshToken = localStorage.getItem(CACHE_KEYS.REFRESH_TOKEN);

        // No token at all - nothing to validate, clear any stale cache
        if (!accessToken) {
          clearAuthState(setUser);
          return;
        }

        // Check for valid cached user (within TTL)
        const cachedUser = getValidCachedUser();

        if (cachedUser) {
          // Cache hit and valid - immediately set user for fast initial render
          logger.log('[Auth] Using cached user (within TTL)');
          setUser(cachedUser);
          setLoading(false);

          // Validate token in background to ensure freshness
          // This doesn't block the UI - user sees cached data immediately
          void (async () => {
            try {
              const response = await fetch(`${getApiBase()}/api/auth/me`, {
                headers: { 'Authorization': `Bearer ${accessToken}` },
              });
              if (!ownsSession(generation, accessToken, refreshToken)) return;
              if (response.ok) {
                const serverUser = await response.json();
                if (!ownsSession(generation, accessToken, refreshToken)) return;
                setUser(serverUser);
                saveUserCache(serverUser);
              } else if ((response.status === 401 || response.status === 403) && ownsOriginalSession()) {
                clearAuthState(setUser, 'background_validation_failed');
              }
            } catch (error) {
              logger.warn('[Auth] Background validation failed (network error):', error);
            }
          })();
          return;
        }

        // Cache miss or expired - normal validation flow
        logger.log('[Auth] Cache miss or expired, validating token...');
        try {
          const response = await fetch(`${getApiBase()}/api/auth/me`, {
            headers: { 'Authorization': `Bearer ${accessToken}` },
          });
          if (!ownsSession(generation, accessToken, refreshToken)) return;

          if (response.ok) {
            // Token valid - use server response and cache it
            const serverUser = await response.json();
            if (!ownsSession(generation, accessToken, refreshToken)) return;
            saveUserCache(serverUser);
            setUser(serverUser);
            logger.log('[Auth] Token validated on init');
          } else if (!ownsOriginalSession()) {
            return;
          } else if (response.status === 401 || response.status === 403) {
            // Token invalid - try refresh
            logger.log('[Auth] Token invalid on init, attempting refresh...');
            if (refreshToken) {
              const refreshed = await attemptTokenRefresh(
                setUser,
                () => authGenerationRef.current === generation,
              );
              if (refreshed) {
                logger.log('[Auth] Token refreshed on init');
              } else {
                logger.warn('[Auth] Token refresh failed on init');
                // Definitive rejection clears storage inside apiClient. If the
                // captured token remains, the failure was transient.
                if (ownsOriginalSession()) {
                  setUser(null);
                }
              }
            } else {
              clearAuthState(setUser);
            }
          } else {
            logger.warn('[Auth] Transient validation failure during init:', response.status);
            setUser(null);
          }
        } catch {
          // Network error - avoid cached user to prevent stale SSO state,
          // but preserve tokens because the failure may be transient.
          logger.warn('[Auth] Network error during init, marking unauthenticated without clearing tokens');
          if (accessToken && ownsOriginalSession()) setUser(null);
        }
      } catch (error) {
        logger.error('Failed to initialize auth:', error);
        if (ownsOriginalSession()) {
          clearAuthState(setUser);
        }
      } finally {
        setLoading(false);
      }
    };

    initializeAuth();
    return () => {
      authGenerationRef.current += 1;
    };
  }, [ownsSession]);

  // Keep auth state in sync when API client clears tokens (same-tab)
  useEffect(() => {
    const onLogout = () => {
      logger.log('[Auth] Logout event received');
      authGenerationRef.current += 1;
      setUser(null);
    };

    window.addEventListener('auth:logout', onLogout as EventListener);
    return () => {
      window.removeEventListener('auth:logout', onLogout as EventListener);
    };
  }, []);

  useEffect(() => {
    if (loading) return;

    if (user) {
      identifyUser(user);
      previousUserIdRef.current = user.id;
      return;
    }

    if (previousUserIdRef.current) {
      resetAnalytics();
      previousUserIdRef.current = null;
    }
  }, [loading, user]);

  const login = async (username: string, password: string) => {
    const generation = ++authGenerationRef.current;
    const data = await authApi.login(username, password);
    if (authGenerationRef.current !== generation) {
      throw new DOMException('Auth establishment superseded', 'AbortError');
    }

    // Store tokens and user data with cache timestamp
    localStorage.setItem(CACHE_KEYS.ACCESS_TOKEN, data.access_token);
    localStorage.setItem(CACHE_KEYS.REFRESH_TOKEN, data.refresh_token);
    saveUserCache(data.user);

    setUser(data.user);
    trackEvent('login_success', {
      login_method: 'password',
      is_superuser: data.user.is_superuser,
    });
  };

  const register = async (username: string, email: string, password: string, inviteCode?: string) => {
    const data = await authApi.register({ username, email, password, invite_code: inviteCode });
    trackEvent('register_success', {
      method: 'password',
      email_verified: data.email_verified,
      invite_code_provided: Boolean(inviteCode?.trim()),
    });

    // Return email and verification status for redirect
    return {
      email: data.email,
      email_verified: data.email_verified
    };
  };

  const logout = () => {
    const accessToken = localStorage.getItem(CACHE_KEYS.ACCESS_TOKEN);
    authGenerationRef.current += 1;
    trackEvent('logout');
    resetAnalytics();
    clearAuthState(setUser, 'user_initiated');
    if (accessToken) {
      void Promise.resolve(fetch(`${getApiBase()}/api/auth/logout`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${accessToken}` },
      })).catch((error) => logger.warn('[Auth] Server logout failed:', error));
    }
  };

  const verifyEmail = async (email: string, code: string) => {
    const generation = ++authGenerationRef.current;
    const data = await authApi.verifyEmail(email, code);
    if (authGenerationRef.current !== generation) {
      throw new DOMException('Auth establishment superseded', 'AbortError');
    }

    // Store tokens and user data with cache timestamp
    localStorage.setItem(CACHE_KEYS.ACCESS_TOKEN, data.access_token);
    localStorage.setItem(CACHE_KEYS.REFRESH_TOKEN, data.refresh_token);
    saveUserCache(data.user);

    setUser(data.user);
    trackEvent('verify_email_success', {
      email_verified: data.user.email_verified,
    });
  };

  const resendVerification = async (email: string) => {
    return await authApi.resendVerification(email);
  };

  const refreshToken = async () => {
    const refreshToken = localStorage.getItem(CACHE_KEYS.REFRESH_TOKEN);
    if (!refreshToken) {
      throw new Error('No refresh token available');
    }

    const generation = authGenerationRef.current;
    try {
      const data = await authApi.refreshToken(refreshToken);

      if (
        authGenerationRef.current !== generation ||
        localStorage.getItem(CACHE_KEYS.REFRESH_TOKEN) !== refreshToken
      ) {
        return;
      }

      // Update tokens and cache
      localStorage.setItem(CACHE_KEYS.ACCESS_TOKEN, data.access_token);
      localStorage.setItem(CACHE_KEYS.REFRESH_TOKEN, data.refresh_token);
      saveUserCache(data.user);

      setUser(data.user);
    } catch (error) {
      const status = (error as { status?: number } | null)?.status;
      if (
        authGenerationRef.current === generation &&
        localStorage.getItem(CACHE_KEYS.REFRESH_TOKEN) === refreshToken &&
        (status === 401 || status === 403)
      ) {
        clearAuthState(setUser, 'refresh_failed');
      }
      throw error;
    }
  };

  const handleOAuthCallback = async (
    accessToken: string,
    refreshToken: string,
    options: { isNewUser?: boolean } = {},
  ) => {
    const generation = ++authGenerationRef.current;
    // After OAuth callback, we need to fetch user info using the access token
    const response = await fetch(`${getApiBase()}/api/auth/me`, {
      headers: {
        'Authorization': `Bearer ${accessToken}`,
      },
    });

    if (!response.ok) {
      if (authGenerationRef.current !== generation) {
        throw new DOMException('Auth establishment superseded', 'AbortError');
      }
      clearAuthState(setUser, 'oauth_callback_failed');
      throw new Error('Failed to fetch user info');
    }

    const user = await response.json();
    if (authGenerationRef.current !== generation) {
      throw new DOMException('Auth establishment superseded', 'AbortError');
    }

    // Store tokens and user data with cache timestamp
    localStorage.setItem(CACHE_KEYS.ACCESS_TOKEN, accessToken);
    localStorage.setItem(CACHE_KEYS.REFRESH_TOKEN, refreshToken);
    saveUserCache(user);

    setUser(user);
    const isNewUser = options.isNewUser === true;
    if (isNewUser) {
      // Google sign-ups never pass through register(); count them here.
      trackEvent('register_success', {
        method: 'google',
        email_verified: true,
      });
    }
    trackEvent('oauth_callback_success', { is_new_user: isNewUser });
  };

  const googleLogin = (options?: { inviteCode?: string; redirectUrl?: string; planIntent?: PlanIntent | null }) => {
    // Check for redirect parameter from external apps
    const params = new URLSearchParams(window.location.search);
    const redirectUrl = options?.redirectUrl ?? params.get('redirect');
    const inviteCode = options?.inviteCode?.trim();
    saveOAuthPlanIntent(options?.planIntent);

    // Redirect to backend Google OAuth endpoint
    const googleAuthUrl = `${getApiBase()}/api/auth/google`;
    const url = new URL(googleAuthUrl);

    if (redirectUrl) {
      // Pass redirect parameter to backend OAuth flow
      url.searchParams.set('redirect', redirectUrl);
    }
    if (inviteCode) {
      // Pass invite code for new-user gating (especially when registration requires invite codes)
      url.searchParams.set('invite_code', inviteCode);
    }

    window.location.href = url.toString();
  };

  /**
   * Apple OAuth login - pending backend implementation.
   *
   * @feature_request Sign in with Apple support
   * @backend_needed Add /api/v1/auth/apple endpoint with Apple ID integration
   * @see https://developer.apple.com/sign-in-with-apple/
   * @see apps/server/api/auth.py for OAuth implementation reference
   */
  const appleLogin = () => {
    logger.log('Apple OAuth coming soon');
  };

  return (
    <AuthContext.Provider value={{ user, loading, login, register, logout, refreshToken, handleOAuthCallback, verifyEmail, resendVerification, googleLogin, appleLogin }}>
      {children}
    </AuthContext.Provider>
  );
};

// eslint-disable-next-line react-refresh/only-export-components
export const useAuth = () => {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};
