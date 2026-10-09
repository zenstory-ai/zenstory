/**
 * Self-service password reset (emailed 6-digit code).
 *
 * Both calls are anonymous: they never send the stored access token and never
 * trigger the 401 refresh flow in `apiCall`.
 */
import { ApiError, apiErrorFromPayload, getApiBase } from './apiClient';

export interface PasswordResetResponse {
  message: string;
}

function currentLanguage(): string {
  try {
    return localStorage.getItem('zenstory-language') || 'zh';
  } catch {
    return 'zh';
  }
}

async function postAnonymous<T>(endpoint: string, body: unknown): Promise<T> {
  const response = await fetch(`${getApiBase()}${endpoint}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Accept-Language': currentLanguage(),
    },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    let payload: unknown = null;
    try {
      payload = await response.json();
    } catch {
      // Non-JSON error body (e.g. a proxy 404 page).
    }
    throw apiErrorFromPayload(response.status, payload);
  }
  return response.json() as Promise<T>;
}

export const passwordResetApi = {
  /** Always resolves the same way whether or not the email has an account. */
  requestCode: (email: string, language: string) =>
    postAnonymous<PasswordResetResponse>('/api/auth/password-reset/request', { email, language }),

  /** Sets the new password; every existing session is signed out. */
  confirm: (email: string, code: string, newPassword: string) =>
    postAnonymous<PasswordResetResponse>('/api/auth/password-reset/confirm', {
      email,
      code,
      new_password: newPassword,
    }),
};

/**
 * True when the backend does not have the reset endpoints yet (the web app can
 * ship before the API). The page then falls back to "email support".
 */
export function isPasswordResetUnavailable(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 404 || error.status === 405);
}
