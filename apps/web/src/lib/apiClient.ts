/**
 * API Client with automatic authentication token handling.
 *
 * This module provides a centralized HTTP client for making authenticated API calls
 * to the backend. It handles:
 * - Automatic JWT token injection in Authorization header
 * - Token refresh when access tokens expire (401 responses)
 * - Request retry after successful token refresh
 * - Concurrent refresh request deduplication
 * - Network error resilience with cooldown mechanisms
 *
 * @module apiClient
 * @example
 * ```typescript
 * import { api } from './apiClient';
 *
 * // Simple GET request
 * const projects = await api.get<Project[]>('/api/v1/projects');
 *
 * // POST with data
 * const newProject = await api.post<Project>('/api/v1/projects', { name: 'My Novel' });
 * ```
 */

import { resolveApiErrorMessage, toUserErrorMessage } from './errorHandler';
import { logger } from './logger';

const API_BASE_RAW = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

/**
 * Get the API base URL, auto-upgrading to HTTPS if needed.
 *
 * This function ensures that when the page is served over HTTPS,
 * API calls are also made over HTTPS to prevent mixed content errors.
 *
 * @returns The appropriate API base URL
 */
export function getApiBase(): string {
  if (typeof window !== 'undefined' &&
      window.location.protocol === 'https:' &&
      API_BASE_RAW.startsWith('http:')) {
    return API_BASE_RAW.replace('http:', 'https:');
  }
  return API_BASE_RAW;
}

/**
 * Global lock to prevent concurrent token refresh requests.
 * When multiple API calls fail with 401 simultaneously, this ensures only one
 * refresh request is sent while others wait for the result.
 */
let refreshPromise: Promise<boolean> | null = null;
let refreshPromiseToken: string | null = null;

/**
 * Timestamp of the last failed refresh attempt.
 * Used to implement cooldown and prevent infinite refresh loops.
 */
let lastRefreshFailTime = 0;
let lastFailedRefreshToken: string | null = null;
const refreshLineage = new Map<string, { accessToken: string; refreshToken: string }>();

/** Resolve only the captured token pair or its confirmed refresh descendant. */
export function resolveOwnedAuthSession(entryAccess: string | null, entryRefresh: string | null): {
  accessToken: string | null;
  refreshToken: string | null;
} | null {
  const accessToken = localStorage.getItem('access_token');
  const refreshToken = localStorage.getItem('refresh_token');
  if (accessToken === entryAccess && refreshToken === entryRefresh) {
    return { accessToken, refreshToken };
  }
  const descendant = entryAccess && entryRefresh ? refreshLineage.get(entryRefresh) : undefined;
  return descendant && accessToken === descendant.accessToken && refreshToken === descendant.refreshToken
    ? { accessToken, refreshToken }
    : null;
}

const isCredentialFailure = (status: number): boolean => status === 401 || status === 403;

/**
 * Cooldown period in milliseconds after a refresh failure.
 * Prevents rapid-fire refresh attempts that could overwhelm the server.
 * @constant {number}
 */
const REFRESH_COOLDOWN_MS = 5000; // 5 seconds cooldown after refresh failure

/**
 * Clear all authentication data from localStorage and notify listeners.
 *
 * Removes access token, refresh token, user data, and authentication
 * validation timestamp from localStorage. Dispatches a custom 'auth:logout'
 * event to notify AuthContext in the same tab.
 *
 * @param reason - Optional reason for clearing auth (e.g., 'refresh_failed', 'missing_token')
 * @fires window#auth:logout - Custom event with reason in detail
 *
 * @example
 * ```typescript
 * // Manual logout
 * clearAuthStorage('user_initiated');
 *
 * // Clear on token expiration
 * clearAuthStorage('token_expired');
 * ```
 */
export function clearAuthStorage(reason?: string): void {
  refreshLineage.clear();
  localStorage.removeItem('access_token');
  localStorage.removeItem('refresh_token');
  localStorage.removeItem('user');
  localStorage.removeItem('auth_validated_at'); // Clear cache timestamp

  // Notify AuthContext to update state (same-tab)
  if (typeof window !== 'undefined') {
    window.dispatchEvent(
      new CustomEvent('auth:logout', {
        detail: {
          reason: reason || 'unknown',
        },
      }),
    );
  }
}

/**
 * Attempt to refresh the access token using the stored refresh token.
 *
 * This function implements several safety mechanisms:
 * - **Cooldown**: Skips refresh if a recent attempt failed (5s cooldown)
 * - **Deduplication**: Returns existing promise if refresh is already in progress
 * - **Network resilience**: Does NOT clear tokens on network errors
 * - **State cleanup**: Clears tokens only when refresh is definitively rejected
 *
 * @returns Promise resolving to `true` if refresh succeeded, `false` otherwise
 *
 * @example
 * ```typescript
 * const refreshed = await tryRefreshToken();
 * if (refreshed) {
 *   // New access token is now in localStorage
 *   const newToken = getAccessToken();
 * }
 * ```
 */
export async function tryRefreshToken(): Promise<boolean> {
  const refreshToken = localStorage.getItem('refresh_token');

  // Check if we're in cooldown period after a recent refresh failure
  if (
    refreshToken === lastFailedRefreshToken &&
    Date.now() - lastRefreshFailTime < REFRESH_COOLDOWN_MS
  ) {
    logger.warn('[Auth] Refresh in cooldown, skipping refresh attempt');
    return false;
  }

  if (!refreshToken) {
    logger.warn('[Auth] No refresh token available');
    return false;
  }

  // Wait for any ongoing refresh to complete
  if (refreshPromise) {
    logger.log('[Auth] Waiting for ongoing token refresh...');
    if (refreshPromiseToken === refreshToken) return refreshPromise;
    await refreshPromise;
    if (localStorage.getItem('refresh_token') !== refreshToken) return false;
    return tryRefreshToken();
  }

  // This request is responsible for performing the refresh
  logger.log('[Auth] Starting token refresh...');
  refreshPromiseToken = refreshToken;
  refreshPromise = (async () => {
    try {
      const response = await fetch(`${getApiBase()}/api/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });

      if (response.ok) {
        const data = await response.json();
        if (localStorage.getItem('refresh_token') !== refreshToken) {
          logger.warn('[Auth] Ignoring stale token refresh result');
          return false;
        }
        localStorage.setItem('access_token', data.access_token);
        localStorage.setItem('refresh_token', data.refresh_token);
        localStorage.setItem('user', JSON.stringify(data.user));
        localStorage.setItem('auth_validated_at', Date.now().toString()); // Update cache timestamp
        lastFailedRefreshToken = null;
        for (const [ancestor, latest] of refreshLineage) {
          if (latest.refreshToken === refreshToken) {
            refreshLineage.set(ancestor, {
              accessToken: data.access_token,
              refreshToken: data.refresh_token,
            });
          }
        }
        refreshLineage.set(refreshToken, {
          accessToken: data.access_token,
          refreshToken: data.refresh_token,
        });
        logger.log('[Auth] Token refresh successful');
        return true;
      } else {
        logger.warn('[Auth] Token refresh failed', response.status);
        lastRefreshFailTime = Date.now();
        lastFailedRefreshToken = refreshToken;
        if (
          isCredentialFailure(response.status) &&
          localStorage.getItem('refresh_token') === refreshToken
        ) {
          clearAuthStorage('refresh_failed');
        }
        return false;
      }
    } catch (error) {
      logger.error('[Auth] Token refresh network error:', error);
      lastRefreshFailTime = Date.now();
      lastFailedRefreshToken = refreshToken;
      // Do NOT clear tokens on network error - network is transient
      // User can retry later when network recovers
      return false;
    } finally {
      refreshPromise = null;
      refreshPromiseToken = null;
    }
  })();

  return refreshPromise;
}

/**
 * Result of token validation returned by {@link validateToken}.
 *
 * @interface TokenValidationResult
 * @property {boolean} valid - Whether the token is valid
 * @property {unknown} [user] - User data returned from /api/auth/me if token is valid
 * @property {boolean} [isNetworkError] - True if validation failed due to network error
 *                                        (distinguishes from invalid token scenarios)
 */
export interface TokenValidationResult {
  valid: boolean;
  user?: unknown;
  isNetworkError?: boolean;  // Distinguish network errors from invalid tokens
}

/**
 * Validate current access token by calling the /api/auth/me endpoint.
 *
 * This function checks if the stored access token is still valid by making
 * an authenticated request to the backend. It distinguishes between:
 * - **Valid tokens**: Returns user data
 * - **Invalid/expired tokens**: Returns valid=false without network error
 * - **Network errors**: Returns valid=false with isNetworkError=true
 *
 * The distinction allows callers to handle network issues gracefully
 * without forcing re-authentication.
 *
 * @returns Promise resolving to validation result with optional user data
 *
 * @example
 * ```typescript
 * const result = await validateToken();
 * if (result.valid) {
 *   logger.log('User:', result.user);
 * } else if (result.isNetworkError) {
 *   // Network issue - may want to retry or proceed optimistically
 *   logger.log('Network error during validation');
 * } else {
 *   // Token is definitely invalid - need to re-authenticate
 *   redirectToLogin();
 * }
 * ```
 */
export async function validateToken(): Promise<TokenValidationResult> {
  const accessToken = localStorage.getItem('access_token');

  if (!accessToken) {
    return { valid: false };
  }

  try {
    const response = await fetch(`${getApiBase()}/api/auth/me`, {
      headers: {
        'Authorization': `Bearer ${accessToken}`,
      },
    });

    if (response.ok) {
      const user = await response.json();
      return { valid: true, user, isNetworkError: false };
    }

    return {
      valid: false,
      isNetworkError: !isCredentialFailure(response.status),
    };
  } catch (error) {
    logger.warn('[Auth] Network error during token validation:', error);
    // Network error - distinguish from invalid token
    // Caller may want to try refresh or proceed optimistically
    return { valid: false, isNetworkError: true };
  }
}

/**
 * Get the current access token from localStorage.
 *
 * Returns the raw JWT access token string, or null if not authenticated.
 * Use this when you need direct access to the token (e.g., for WebSocket auth).
 *
 * @returns The access token string, or null if not stored
 *
 * @example
 * ```typescript
 * const token = getAccessToken();
 * if (token) {
 *   websocket.send({ type: 'auth', token });
 * }
 * ```
 */
export function getAccessToken(): string | null {
  return localStorage.getItem('access_token');
}

/**
 * Error codes whose string `error_detail` is a user-facing reason worth
 * showing after the localized headline (the generic headline alone would not
 * tell the user what to fix).
 */
export const ERROR_CODES_WITH_REASON: ReadonlySet<string> = new Set([
  'ERR_SKILL_PACKAGE_INVALID',
  'ERR_SKILL_PACKAGE_TOO_LARGE',
]);

/**
 * Custom error class for API errors with HTTP status code.
 *
 * Extends the standard Error class with an HTTP status code for better
 * error handling. Automatically translates error codes (starting with 'ERR_')
 * using the i18n error handler.
 *
 * @extends Error
 *
 * @example
 * ```typescript
 * try {
 *   await api.get('/api/v1/protected');
 * } catch (error) {
 *   if (error instanceof ApiError) {
 *     logger.log(`Status: ${error.status}, Message: ${error.message}`);
 *     if (error.status === 404) {
 *       handleNotFound();
 *     }
 *   }
 * }
 * ```
 */
export class ApiError extends Error {
  /**
   * HTTP status code from the failed response.
   * @type {number}
   */
  public status: number;

  /**
   * Raw backend error token/message before i18n translation.
   * Useful for programmatic branching (e.g. upgrade prompts).
   */
  public rawMessage: string;

  /**
   * Backend error code when available (e.g. ERR_QUOTA_PROJECTS_EXCEEDED).
   */
  public errorCode?: string;

  /**
   * Structured `error_detail` payload from the backend, when it is an object.
   *
   * Some errors carry machine-readable context that the caller must act on —
   * e.g. a 409 `ERR_RESOURCE_CONFLICT` from `PUT /files/{id}` ships
   * `{ reason: "stale_write", current_content, current_updated_at }` so the
   * editor can refresh instead of silently losing the user's edit. Flattening
   * everything into a string would throw that away.
   */
  public details?: Record<string, unknown>;

  /**
   * Plain-text `error_detail` from the backend, when it is a string.
   *
   * For codes in {@link ERROR_CODES_WITH_REASON} this is the user-facing reason
   * (e.g. which file in a skill package is invalid) and is appended to the
   * localized headline in `message`.
   */
  public detailText?: string;

  /**
   * Create a new ApiError.
   *
   * @param status - HTTP status code (e.g., 401, 404, 500)
   * @param message - Error message or error code (will be translated if starts with 'ERR_')
   * @param details - Structured error_detail object when the backend provides one
   * @param detailText - String error_detail when the backend provides one
   */
  constructor(
    status: number,
    message: string,
    details?: Record<string, unknown>,
    detailText?: string,
  ) {
    const normalized = message.trim();
    const headline = toUserErrorMessage(normalized);
    const reason = detailText?.trim() || undefined;
    super(
      reason && reason !== normalized && ERROR_CODES_WITH_REASON.has(normalized)
        ? `${headline}: ${reason}`
        : headline,
    );
    this.status = status;
    this.name = 'ApiError';
    this.rawMessage = normalized;
    this.errorCode = normalized.startsWith('ERR_') ? normalized : undefined;
    this.details = details;
    this.detailText = reason;
  }
}

/**
 * Build an ApiError from a failed response's parsed JSON body (or null when
 * the body could not be parsed).
 */
export function apiErrorFromPayload(status: number, errorData: unknown): ApiError {
  const errorMessage = resolveApiErrorMessage(errorData, `API error: ${status}`);
  const rawDetail = (errorData as Record<string, unknown> | null)?.error_detail;
  // 保留结构化的 error_detail：409 冲突等场景需要里面的 current_content /
  // current_updated_at 才能正确恢复，压成一句文案就没法用了
  const details =
    rawDetail && typeof rawDetail === 'object' && !Array.isArray(rawDetail)
      ? (rawDetail as Record<string, unknown>)
      : undefined;
  const detailText = typeof rawDetail === 'string' ? rawDetail : undefined;
  return new ApiError(status, errorMessage, details, detailText);
}

/**
 * Make an authenticated API call with automatic token handling.
 *
 * This is the core HTTP client function that:
 * 1. Injects the Authorization header with the access token
 * 2. Adds Accept-Language header for i18n
 * 3. Automatically retries with token refresh on 401 responses
 * 4. Throws typed ApiError for non-successful responses
 *
 * For most use cases, prefer the convenience methods in the {@link api} object.
 *
 * @typeParam T - Expected response type
 * @param endpoint - API endpoint path (e.g., '/api/v1/projects')
 * @param options - Fetch options (method, headers, body, etc.)
 * @returns Promise resolving to parsed JSON response of type T
 * @throws {ApiError} On HTTP errors (includes status code)
 *
 * @example
 * ```typescript
 * // GET request
 * const user = await apiCall<User>('/api/v1/users/me');
 *
 * // POST request
 * const created = await apiCall<Project>('/api/v1/projects', {
 *   method: 'POST',
 *   body: JSON.stringify({ name: 'New Project' }),
 * });
 * ```
 */
export async function apiCall<T>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  const accessToken = localStorage.getItem('access_token');
  const refreshTokenAtRequest = localStorage.getItem('refresh_token');

  // Get current language from i18n
  const language = localStorage.getItem('zenstory-language') || 'zh';

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    'Accept-Language': language,
    ...(options.headers as Record<string, string>),
  };

  // Add Authorization header if token exists
  if (accessToken) {
    headers['Authorization'] = `Bearer ${accessToken}`;
  }

  let response = await fetch(`${getApiBase()}${endpoint}`, {
    ...options,
    headers,
  });

  // Handle 401 Unauthorized - try to refresh token
  if (response.status === 401) {
    const currentAccessToken = localStorage.getItem('access_token');
    const currentRefreshToken = localStorage.getItem('refresh_token');
    const stillOwnsRequestSession = (
      currentAccessToken === accessToken &&
      currentRefreshToken === refreshTokenAtRequest
    );
    const rotatedSession = refreshTokenAtRequest
      ? refreshLineage.get(refreshTokenAtRequest)
      : undefined;
    const belongsToRotatedSession = Boolean(
      rotatedSession &&
      currentAccessToken === rotatedSession.accessToken &&
      currentRefreshToken === rotatedSession.refreshToken
    );
    if (!stillOwnsRequestSession && !belongsToRotatedSession) {
      throw new ApiError(401, 'Not authenticated');
    }

    if (!accessToken || !refreshTokenAtRequest) {
      if (stillOwnsRequestSession) clearAuthStorage('missing_token');
      throw new ApiError(401, 'Not authenticated');
    }

    if (stillOwnsRequestSession) {
      if (!await tryRefreshToken()) {
        throw new ApiError(401, 'Not authenticated');
      }
    }

    const ownedSession = resolveOwnedAuthSession(accessToken, refreshTokenAtRequest);
    const newAccessToken = ownedSession?.accessToken;
    const newRefreshToken = ownedSession?.refreshToken;
    if (!newAccessToken || !newRefreshToken) {
      throw new ApiError(401, 'Not authenticated');
    }
    headers['Authorization'] = `Bearer ${newAccessToken}`;
    response = await fetch(`${getApiBase()}${endpoint}`, {
      ...options,
      headers,
    });
    if (response.status === 401) {
      if (
        localStorage.getItem('access_token') === newAccessToken &&
        localStorage.getItem('refresh_token') === newRefreshToken
      ) {
        clearAuthStorage('retry_still_unauthorized');
      }
      throw new ApiError(401, 'Not authenticated');
    }
  }

  // Handle other errors
  if (!response.ok) {
    let errorData: unknown = null;
    try {
      errorData = await response.json();
    } catch {
      // Could not parse error response
    }
    throw apiErrorFromPayload(response.status, errorData);
  }

  return response.json();
}

/**
 * Typed API client with convenience methods for common HTTP operations.
 *
 * Provides a cleaner interface over {@link apiCall} with pre-configured
 * methods for GET, POST, PUT, PATCH, and DELETE requests.
 *
 * All methods:
 * - Are type-safe with generic response types
 * - Automatically handle authentication
 * - Support optional RequestInit overrides
 *
 * @example
 * ```typescript
 * import { api } from './apiClient';
 *
 * // GET - fetch all projects
 * const projects = await api.get<Project[]>('/api/v1/projects');
 *
 * // POST - create new project
 * const newProject = await api.post<Project>('/api/v1/projects', {
 *   name: 'My Novel',
 *   description: 'A mystery thriller'
 * });
 *
 * // PUT - full update
 * await api.put(`/api/v1/projects/${id}`, { name: 'Updated Name' });
 *
 * // PATCH - partial update
 * await api.patch(`/api/v1/projects/${id}`, { description: 'New desc' });
 *
 * // DELETE - remove resource
 * await api.delete(`/api/v1/projects/${id}`);
 * ```
 */
export const api = {
  /**
   * Make a GET request.
   *
   * @typeParam T - Expected response type
   * @param endpoint - API endpoint path
   * @param options - Optional fetch options
   * @returns Promise resolving to parsed response
   */
  get: <T>(endpoint: string, options?: RequestInit) =>
    apiCall<T>(endpoint, { ...options, method: 'GET' }),

  /**
   * Make a POST request to create a resource.
   *
   * @typeParam T - Expected response type
   * @param endpoint - API endpoint path
   * @param data - Request body data (will be JSON stringified)
   * @param options - Optional fetch options
   * @returns Promise resolving to parsed response
   */
  post: <T>(endpoint: string, data?: unknown, options?: RequestInit) =>
    apiCall<T>(endpoint, {
      ...options,
      method: 'POST',
      body: data ? JSON.stringify(data) : undefined,
    }),

  /**
   * Make a PUT request to fully update a resource.
   *
   * @typeParam T - Expected response type
   * @param endpoint - API endpoint path
   * @param data - Request body data (will be JSON stringified)
   * @param options - Optional fetch options
   * @returns Promise resolving to parsed response
   */
  put: <T>(endpoint: string, data?: unknown, options?: RequestInit) =>
    apiCall<T>(endpoint, {
      ...options,
      method: 'PUT',
      body: data ? JSON.stringify(data) : undefined,
    }),

  /**
   * Make a PATCH request to partially update a resource.
   *
   * @typeParam T - Expected response type
   * @param endpoint - API endpoint path
   * @param data - Request body data (will be JSON stringified)
   * @param options - Optional fetch options
   * @returns Promise resolving to parsed response
   */
  patch: <T>(endpoint: string, data?: unknown, options?: RequestInit) =>
    apiCall<T>(endpoint, {
      ...options,
      method: 'PATCH',
      body: data ? JSON.stringify(data) : undefined,
    }),

  /**
   * Make a DELETE request to remove a resource.
   *
   * @typeParam T - Expected response type
   * @param endpoint - API endpoint path
   * @param options - Optional fetch options
   * @returns Promise resolving to parsed response
   */
  delete: <T>(endpoint: string, options?: RequestInit) =>
    apiCall<T>(endpoint, { ...options, method: 'DELETE' }),
};
