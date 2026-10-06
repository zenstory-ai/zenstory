/**
 * SSO (Single Sign-On) redirect utilities for cross-application authentication.
 *
 * This module handles secure token-based authentication redirects between
 * zenstory and trusted partner applications. It validates tokens before
 * redirecting to prevent token leakage and handles various error scenarios
 * including network failures.
 *
 * Security Architecture:
 * - Whitelist-based domain validation prevents open redirect vulnerabilities
 * - Tokens are only sent to explicitly allowed domains
 * - Token validation with backend ensures active session before redirect
 * - Network errors are handled gracefully without clearing auth tokens
 *
 * Flow:
 * 1. Validate redirect URL against ALLOWED_REDIRECT_DOMAINS whitelist
 * 2. Check for existing access token in localStorage
 * 3. Validate token with backend (/auth/me endpoint)
 * 4. If invalid, attempt token refresh via refresh_token
 * 5. Append validated token to redirect URL and return for navigation
 *
 * Usage:
 * ```ts
 * import { handleSsoRedirect, isValidRedirectUrl } from '../lib/ssoRedirect';
 *
 * // Check if URL is allowed before processing
 * if (isValidRedirectUrl(redirectUrl)) {
 *   const result = await handleSsoRedirect(redirectUrl);
 *   if (result.success && result.redirectUrl) {
 *     window.location.href = result.redirectUrl;
 *   } else if (result.shouldShowLogin) {
 *     // Show login UI
 *   }
 * }
 * ```
 *
 * @module lib/ssoRedirect
 */

import { resolveOwnedAuthSession, tryRefreshToken, validateToken } from './apiClient';
import { logger } from './logger';

/**
 * Whitelist of allowed domains for SSO redirects.
 *
 * Only URLs with hosts matching these domains (or subdomains) will be
 * accepted for SSO redirects. This prevents open redirect vulnerabilities.
 *
 * @constant
 */
const ALLOWED_REDIRECT_DOMAINS = [
  'zenstory.ai',          // production
  'www.zenstory.ai',      // production www
];

/**
 * Validates if a URL is allowed for SSO redirect based on domain whitelist.
 *
 * Checks the URL's host against ALLOWED_REDIRECT_DOMAINS, supporting both
 * exact matches and subdomain matches (e.g., app.zenstory.ai matches zenstory.ai).
 *
 * @param url - The full URL to validate
 * @returns true if the URL's host is in the allowed domains list, false otherwise
 *
 * @example
 * ```ts
 * isValidRedirectUrl('https://zenstory.ai/dashboard'); // true
 * isValidRedirectUrl('https://app.zenstory.ai/editor'); // true (subdomain)
 * isValidRedirectUrl('https://malicious.com'); // false
 * isValidRedirectUrl('not-a-url'); // false (parse error)
 * ```
 */
export function isValidRedirectUrl(url: string): boolean {
  try {
    const parsedUrl = new URL(url);
    if (!['http:', 'https:'].includes(parsedUrl.protocol)) {
      return false;
    }
    if (parsedUrl.username || parsedUrl.password) {
      return false;
    }
    const host = parsedUrl.host;
    return ALLOWED_REDIRECT_DOMAINS.some(domain =>
      host === domain || host.endsWith('.' + domain)
    );
  } catch {
    return false;
  }
}

/**
 * Result object returned by handleSsoRedirect containing redirect status and instructions.
 *
 * @property success - Whether SSO redirect can proceed
 * @property redirectUrl - The redirect URL with token appended (only when success is true)
 * @property shouldShowLogin - Whether the UI should show login form
 * @property clearAuth - Whether auth tokens should be cleared from localStorage
 * @property reason - Specific reason for failure (for logging/debugging)
 * @property error - User-friendly error message
 */
export interface SsoRedirectResult {
  success: boolean;
  redirectUrl?: string;
  shouldShowLogin?: boolean;
  clearAuth?: boolean;
  reason?: 'invalid_redirect' | 'missing_access_token' | 'network_error' | 'session_expired';
  error?: string;
}

/**
 * Handles SSO redirect with comprehensive token validation and error handling.
 *
 * This is a CRITICAL security function that validates tokens before redirecting
 * to prevent token leakage and ensure a valid session exists. The function
 * implements a multi-step validation process with graceful network error handling.
 *
 * Processing Steps:
 * 1. **URL Validation**: Verify redirect URL is in allowed domains whitelist
 * 2. **Token Check**: Confirm access token exists in localStorage
 * 3. **Backend Validation**: Call /auth/me to verify token is still valid
 * 4. **Token Refresh**: If invalid (but not network error), attempt refresh
 * 5. **Redirect Construction**: Append validated token to URL as query param
 *
 * Network Error Handling Strategy:
 * - If token validation fails due to network error, still try refresh
 *   (refresh endpoint may be reachable even if /auth/me is not)
 * - If refresh also fails due to network error, show login but keep tokens
 *   (user can retry later when network recovers)
 * - clearAuth is false for network errors to preserve tokens for retry
 *
 * Failure Reasons:
 * - `invalid_redirect`: URL not in whitelist (security violation)
 * - `missing_access_token`: No token in localStorage
 * - `network_error`: Network failure during validation/refresh
 * - `session_expired`: Token definitively invalid and refresh failed
 *
 * @param redirectUrl - The target URL for SSO redirect (must be in allowed domains)
 * @returns Promise resolving to SsoRedirectResult with redirect URL or error info
 *
 * @example
 * ```ts
 * // Successful redirect
 * const result = await handleSsoRedirect('https://zenstory.ai/dashboard');
 * if (result.success && result.redirectUrl) {
 *   window.location.href = result.redirectUrl;
 *   // Navigates to: https://zenstory.ai/dashboard?token=eyJ...
 * }
 *
 * // Session expired - show login and clear tokens
 * if (result.reason === 'session_expired') {
 *   if (result.clearAuth) {
 *     localStorage.removeItem('access_token');
 *     localStorage.removeItem('refresh_token');
 *   }
 *   showLoginForm();
 * }
 *
 * // Network error - preserve tokens for retry
 * if (result.reason === 'network_error') {
 *   showToast('Network error, please try again');
 *   // tokens still in localStorage for retry
 * }
 * ```
 */
export async function handleSsoRedirect(
  redirectUrl: string
): Promise<SsoRedirectResult> {
  // Step 1: Validate redirect URL for security
  if (!isValidRedirectUrl(redirectUrl)) {
    logger.error('[SSO] Invalid redirect URL rejected:', redirectUrl);
    return {
      success: false,
      shouldShowLogin: true,
      clearAuth: false,
      reason: 'invalid_redirect',
      error: 'Invalid redirect URL',
    };
  }

  // Step 2: Check if access token exists
  const accessToken = localStorage.getItem('access_token');
  const entryRefresh = localStorage.getItem('refresh_token');
  const superseded = () => new DOMException('SSO redirect superseded', 'AbortError');
  const requireOwnedSession = () => {
    const owned = resolveOwnedAuthSession(accessToken, entryRefresh);
    if (!owned) throw superseded();
    return owned;
  };
  const refreshOwnedSession = async () => {
    requireOwnedSession();
    const refreshed = await tryRefreshToken();
    if (!resolveOwnedAuthSession(accessToken, entryRefresh) && (
      refreshed || localStorage.getItem('access_token') || localStorage.getItem('refresh_token')
    )) throw superseded();
    return refreshed;
  };
  if (!accessToken) {
    logger.warn('[SSO] No access token available');
    return {
      success: false,
      shouldShowLogin: true,
      clearAuth: true,
      reason: 'missing_access_token',
      error: 'No access token',
    };
  }

  // Step 3: CRITICAL - Validate token with backend
  logger.log('[SSO] Validating token before redirect...');
  const validation = await validateToken();
  const ownedSession = requireOwnedSession();

  if (validation.valid) {
    // Token is valid, proceed with redirect
    logger.log('[SSO] Token validated successfully, redirecting...');
    const redirectUrlWithToken = new URL(redirectUrl);
    redirectUrlWithToken.searchParams.set('token', requireOwnedSession().accessToken!);

    return {
      success: true,
      redirectUrl: redirectUrlWithToken.toString(),
    };
  }

  // NEW: Handle network error during validation
  if (validation.isNetworkError) {
    logger.warn('[SSO] Network error during token validation');
    // Still try refresh - refresh endpoint may be reachable
    const refreshToken = ownedSession.refreshToken;

    if (!refreshToken) {
      logger.warn('[SSO] No refresh token available after network error');
      // Show login but don't clear tokens - network is transient
      return {
        success: false,
        shouldShowLogin: true,
        clearAuth: false,
        reason: 'network_error',
        error: 'Network error, please check your connection and try again',
      };
    }

    logger.log('[SSO] Attempting refresh despite validation network error...');
    const refreshed = await refreshOwnedSession();

    if (refreshed) {
      // Refresh succeeded - get new token and redirect
      const newAccessToken = requireOwnedSession().accessToken;
      if (newAccessToken) {
        logger.log('[SSO] Token refreshed after validation network error, redirecting...');
        const redirectUrlWithToken = new URL(redirectUrl);
        redirectUrlWithToken.searchParams.set('token', newAccessToken);

        return {
          success: true,
          redirectUrl: redirectUrlWithToken.toString(),
        };
      }
    }

    // Refresh also failed (likely network issue)
    logger.warn('[SSO] Refresh also failed, likely network issue');
    // Show login but preserve tokens - user can retry later
    return {
      success: false,
      shouldShowLogin: true,
      clearAuth: false,
      reason: 'network_error',
      error: 'Network error, please check your connection and try again',
    };
  }

  // Step 4: Token definitively invalid (not network error) - try refresh
  logger.log('[SSO] Token invalid, attempting refresh...');
  const refreshToken = ownedSession.refreshToken;

  if (!refreshToken) {
    logger.warn('[SSO] No refresh token available');
    return {
      success: false,
      shouldShowLogin: true,
      clearAuth: true,
      reason: 'session_expired',
      error: 'Session expired, please login',
    };
  }

  const refreshed = await refreshOwnedSession();

  if (refreshed) {
    // Refresh succeeded - get new token and redirect
    const newAccessToken = requireOwnedSession().accessToken;
    if (newAccessToken) {
      logger.log('[SSO] Token refreshed, redirecting...');
      const redirectUrlWithToken = new URL(redirectUrl);
      redirectUrlWithToken.searchParams.set('token', newAccessToken);

      return {
        success: true,
        redirectUrl: redirectUrlWithToken.toString(),
      };
    }
  }

  if (resolveOwnedAuthSession(accessToken, entryRefresh)) {
    return {
      success: false,
      shouldShowLogin: true,
      clearAuth: false,
      reason: 'network_error',
      error: 'Authentication service is temporarily unavailable, please try again',
    };
  }

  // Step 5: All attempts failed - show login
  logger.warn('[SSO] Token validation and refresh failed');
  return {
    success: false,
    shouldShowLogin: true,
    clearAuth: false, // The refresh primitive already cleared its owned credentials.
    reason: 'session_expired',
    error: 'Session expired, please login again',
  };
}
