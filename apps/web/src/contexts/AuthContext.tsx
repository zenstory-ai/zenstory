import React, { createContext, useContext, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import i18next from 'i18next';
import { authApi } from '../lib/api';
import type { UserSubscription, UsageQuota } from '../types/subscription';
import { logger } from '../lib/logger';
import { clearAuthStorage, getApiBase, resolveOwnedAuthSession, tryRefreshToken as tryRefreshTokenSingleFlight } from '../lib/apiClient';
import { identifyUser, resetAnalytics, trackEvent } from '../lib/analytics';
import { getEntrySource } from '../lib/entrySource';
import { getAcquisition } from '../lib/acquisition';
import { getLocale } from '../lib/i18n-helpers';
import { saveOAuthPlanIntent, type PlanIntent } from '../lib/authFlow';
import { clearPendingUpgradeFunnelEvents } from '../lib/upgradeAnalytics';
import { claimPreferredProjectType } from '../lib/preferredProjectType';
import { supportedAccountLanguage, syncAccountLanguage } from '../lib/accountLanguage';

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
  /** Interface language saved on the account; null until the user signs up with or switches to one. */
  ui_language?: string | null;
  /** Whether persona onboarding is still required (mirrors GET /api/v1/persona/onboarding). */
  onboarding_required?: boolean | null;
  subscription?: UserSubscription;
  quota?: UsageQuota;
}

export interface AuthContextType {
  user: User | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  register: (email: string, password: string, inviteCode?: string) => Promise<{ email: string; email_verified: boolean; verification_sent?: boolean }>;
  logout: () => void;
  handleOAuthCallback: (
    accessToken: string,
    refreshToken: string,
    options?: { isNewUser?: boolean },
  ) => Promise<void>;
  verifyEmail: (email: string, code: string) => Promise<void>;
  resendVerification: (email: string) => Promise<void>;
  googleLogin: (options?: { inviteCode?: string; redirectUrl?: string; planIntent?: PlanIntent | null }) => void;
  setOnboardingRequired: (required: boolean) => void;
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

// Cache TTL: 5 minutes (fallback for non-JWT opaque tokens in legacy/test fixtures)
const AUTH_CACHE_TTL_MS = 5 * 60 * 1000;
// Clock-skew / in-flight expiry leeway for optimistic JWT boot (seconds).
// Tokens expiring within this window take the blocking validation/refresh path
// instead of booting optimistically into a burst of doomed 401 requests.
const OPTIMISTIC_JWT_EXPIRY_LEEWAY_SEC = 60;

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

// Decode JWT payload claims locally for optimistic boot expiry checks.
// The server remains authoritative and always verifies signatures via /api/auth/me.
const readJwtClaims = (token: string | null): Record<string, unknown> | null => {
  try {
    if (!token) return null;
    const parts = token.split('.');
    if (parts.length !== 3 || !parts[1]) return null;
    const base64 = parts[1].replace(/-/g, '+').replace(/_/g, '/');
    const padded = base64.padEnd(base64.length + ((4 - (base64.length % 4)) % 4), '=');
    const claims: unknown = JSON.parse(atob(padded));
    return claims && typeof claims === 'object' && !Array.isArray(claims)
      ? (claims as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
};

// Parse cached user object from localStorage without enforcing cache TTL.
const parseCachedUser = (): User | null => {
  try {
    const cachedUserStr = localStorage.getItem(CACHE_KEYS.USER);
    if (!cachedUserStr) return null;
    const parsed = JSON.parse(cachedUserStr) as User | null;
    if (!parsed || typeof parsed !== 'object' || typeof parsed.id !== 'string' || !parsed.id) {
      return null;
    }
    return parsed;
  } catch {
    logger.warn('[Auth] Failed to read user cache (corrupted data)');
    return null;
  }
};

// Return cached user eligible for optimistic returning-user boot:
// - For real JWT access tokens: require an unexpired `exp` claim with clock-skew
//   leeway AND a string `sub` matching `cachedUser.id`, allowing instant render
//   even past AUTH_CACHE_TTL_MS while /api/auth/me revalidates in the background.
//   Expired, near-expiry, claim-less, or mismatched JWTs fall back to blocking validation/refresh.
// - For non-JWT opaque tokens (legacy/test fixtures): fall back to AUTH_CACHE_TTL_MS.
const getOptimisticCachedUser = (accessToken: string): User | null => {
  const cachedUser = parseCachedUser();
  if (!cachedUser) return null;

  const claims = readJwtClaims(accessToken);
  if (claims) {
    const nowSec = Date.now() / 1000;
    if (
      typeof claims.exp !== 'number' ||
      !Number.isFinite(claims.exp) ||
      claims.exp <= nowSec + OPTIMISTIC_JWT_EXPIRY_LEEWAY_SEC
    ) {
      return null;
    }
    if (typeof claims.sub !== 'string' || claims.sub !== cachedUser.id) {
      return null;
    }
    return cachedUser;
  }

  const lastValidatedStr = localStorage.getItem(CACHE_KEYS.VALIDATED_AT);
  if (!lastValidatedStr) return null;

  const lastValidated = parseInt(lastValidatedStr, 10);
  if (!Number.isFinite(lastValidated)) return null;

  return Date.now() - lastValidated < AUTH_CACHE_TTL_MS ? cachedUser : null;
};

const isDeepEqual = (a: unknown, b: unknown): boolean => {
  if (Object.is(a, b)) return true;
  if (!a || !b || typeof a !== 'object' || typeof b !== 'object') return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  if (Array.isArray(a) && Array.isArray(b)) {
    return a.length === b.length && a.every((item, idx) => isDeepEqual(item, b[idx]));
  }
  const aObj = a as Record<string, unknown>;
  const bObj = b as Record<string, unknown>;
  const keys = new Set([...Object.keys(aObj), ...Object.keys(bObj)]);
  for (const key of keys) {
    if (!isDeepEqual(aObj[key], bObj[key])) return false;
  }
  return true;
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
  const [user, setUserState] = useState<User | null>(null);
  const setUser = React.useCallback((next: User | null) => {
    if (next) claimPreferredProjectType(next.id, { currentTabOnly: true });
    setUserState(next);
  }, []);
  const [loading, setLoading] = useState(true);
  const previousUserIdRef = React.useRef<string | null>(null);
  const authGenerationRef = React.useRef(0);
  // Tracks the language already synced (backfilled or switched) for the current signed-in user
  // so StrictMode double-mounts or a racing background /api/auth/me response with null ui_language
  // never trigger duplicate PUT /api/auth/me/preferences requests for the same account.
  const syncedLanguageRef = React.useRef<{ userId: string; language: string } | null>(null);
  // Tracks local onboarding state updates (e.g. completing/skipping onboarding) so an earlier
  // in-flight background /api/auth/me response cannot revert onboarding_required back to true.
  const onboardingSeqRef = React.useRef(0);
  const onboardingOverrideRef = React.useRef<{ userId: string; required: boolean; seq: number } | null>(null);

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

        // Check for cached user eligible for optimistic returning-user boot
        const cachedUser = getOptimisticCachedUser(accessToken);

        if (cachedUser) {
          // Cache hit with unexpired token - immediately set user for fast initial render
          logger.log('[Auth] Using cached user for optimistic boot');
          setUser(cachedUser);
          setLoading(false);

          // Validate token in background to ensure freshness & enforce server revocation
          // This doesn't block the UI - user sees cached data immediately
          const fetchStartOnboardingSeq = onboardingSeqRef.current;
          void (async () => {
            try {
              const response = await fetch(`${getApiBase()}/api/auth/me`, {
                headers: { 'Authorization': `Bearer ${accessToken}` },
              });
              if (!ownsSession(generation, accessToken, refreshToken)) return;
              if (response.ok) {
                const serverUser = await response.json();
                if (!ownsSession(generation, accessToken, refreshToken)) return;
                const synced = syncedLanguageRef.current;
                let hydratedUser: User =
                  !supportedAccountLanguage(serverUser?.ui_language) &&
                  synced &&
                  synced.userId === serverUser?.id
                    ? { ...serverUser, ui_language: synced.language }
                    : serverUser;
                const onboardingOverride = onboardingOverrideRef.current;
                if (
                  onboardingOverride &&
                  onboardingOverride.userId === hydratedUser?.id &&
                  onboardingOverride.seq > fetchStartOnboardingSeq
                ) {
                  hydratedUser = { ...hydratedUser, onboarding_required: onboardingOverride.required };
                }
                claimPreferredProjectType(hydratedUser.id, { currentTabOnly: true });
                // Keep object identity stable when nothing changed so downstream
                // user-dependent effects (e.g. ProjectProvider) don't refetch redundantly.
                setUserState((current) => (current && isDeepEqual(current, hydratedUser) ? current : hydratedUser));
                saveUserCache(hydratedUser);
              } else if (response.status === 403 && ownsOriginalSession()) {
                clearAuthState(setUser, 'background_validation_failed');
              } else if (response.status === 401 && ownsOriginalSession()) {
                // Attempt single-flight token refresh first so clock-skewed or just-expired
                // sessions recover seamlessly; definitively revoked sessions fail refresh and
                // have their storage cleared inside tryRefreshTokenSingleFlight.
                if (refreshToken) {
                  const refreshed = await attemptTokenRefresh(
                    setUser,
                    () => authGenerationRef.current === generation,
                  );
                  if (!refreshed && ownsOriginalSession()) {
                    setUser(null);
                  }
                } else {
                  clearAuthState(setUser, 'background_validation_failed');
                }
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
  }, [ownsSession, setUser]);

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
  }, [setUser]);

  // The interface language follows the account across devices (see lib/accountLanguage).
  // A switch updates the signed-in user and its cache at once, so a reload inside the cache
  // window does not bring back the previous language.
  const signedInUserId = user?.id;
  const signedInUserIdRef = React.useRef<string | undefined>(signedInUserId);
  signedInUserIdRef.current = signedInUserId;
  const accountLanguage = user?.ui_language;
  const rememberAccountLanguage = React.useCallback((language: string) => {
    if (!signedInUserId || signedInUserIdRef.current !== signedInUserId) return;
    syncedLanguageRef.current = { userId: signedInUserId, language };
    setUserState(current => (current && current.id === signedInUserId ? { ...current, ui_language: language } : current));
    const cached = parseCachedUser();
    if (cached && cached.id === signedInUserId) saveUserCache({ ...cached, ui_language: language });
  }, [signedInUserId]);
  const setOnboardingRequired = React.useCallback((required: boolean) => {
    const activeUserId = signedInUserIdRef.current;
    if (!activeUserId) return;
    const seq = ++onboardingSeqRef.current;
    onboardingOverrideRef.current = { userId: activeUserId, required, seq };
    setUserState(current => (
      current && current.id === activeUserId && current.onboarding_required !== required
        ? { ...current, onboarding_required: required }
        : current
    ));
    const cached = parseCachedUser();
    if (cached && cached.id === activeUserId) {
      saveUserCache({ ...cached, onboarding_required: required });
    }
  }, []);
  useEffect(() => {
    if (!signedInUserId) {
      syncedLanguageRef.current = null;
      onboardingOverrideRef.current = null;
      return;
    }
    if (onboardingOverrideRef.current && onboardingOverrideRef.current.userId !== signedInUserId) {
      onboardingOverrideRef.current = null;
    }
    const effectiveAccountLanguage =
      supportedAccountLanguage(accountLanguage) ??
      (syncedLanguageRef.current?.userId === signedInUserId
        ? syncedLanguageRef.current.language
        : accountLanguage);
    return syncAccountLanguage(
      i18next,
      effectiveAccountLanguage,
      rememberAccountLanguage,
      () => signedInUserIdRef.current === signedInUserId,
    );
  }, [signedInUserId, accountLanguage, rememberAccountLanguage]);

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

    claimPreferredProjectType(data.user.id);
    setUser(data.user);
    trackEvent('login_success', {
      login_method: 'password',
      is_superuser: data.user.is_superuser,
    });
  };

  const register = async (email: string, password: string, inviteCode?: string) => {
    // The verification email goes out in the language the visitor is using; the
    // server names the account after the email prefix.
    const data = await authApi.register({ email, password, invite_code: inviteCode, language: getLocale(), acquisition: getAcquisition() });
    trackEvent('register_success', {
      method: 'password',
      email_verified: data.email_verified,
      invite_code_provided: Boolean(inviteCode?.trim()),
      entry_source: getEntrySource(),
    });

    // Return email and verification status for redirect
    return {
      email: data.email,
      email_verified: data.email_verified,
      verification_sent: data.verification_sent,
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

    claimPreferredProjectType(data.user.id);
    setUser(data.user);
    trackEvent('verify_email_success', {
      email_verified: data.user.email_verified,
    });
  };

  const resendVerification = async (email: string) => {
    return await authApi.resendVerification(email);
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

    claimPreferredProjectType(user.id);
    setUser(user);
    const isNewUser = options.isNewUser === true;
    if (isNewUser) {
      // Google sign-ups never pass through register(); count them here.
      trackEvent('register_success', {
        method: 'google',
        email_verified: true,
        entry_source: getEntrySource(),
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
    const acquisition = getAcquisition();
    if (acquisition) {
      // Recorded on the account if this Google sign-in creates one.
      url.searchParams.set('acq', JSON.stringify(acquisition));
    }
    const currentLanguage = supportedAccountLanguage(i18next.language);
    if (currentLanguage) {
      // Recorded on the account if this Google sign-in creates one.
      url.searchParams.set('lang', currentLanguage);
    }

    window.location.href = url.toString();
  };

  return (
    <AuthContext.Provider value={{ user, loading, login, register, logout, handleOAuthCallback, verifyEmail, resendVerification, googleLogin, setOnboardingRequired }}>
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
