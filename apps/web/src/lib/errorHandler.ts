/**
 * Error handling utilities for API error translation and user-friendly messaging.
 *
 * Provides centralized error handling with i18n support for translating backend
 * error codes to localized user-facing messages.
 */

import i18n from 'i18next';

function hasText(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0;
}

function extractNestedMessage(value: unknown): string | null {
  if (!value || typeof value !== 'object') {
    return null;
  }

  const payload = value as Record<string, unknown>;
  const candidates = [payload.message, payload.detail, payload.error];

  for (const candidate of candidates) {
    if (hasText(candidate)) {
      return candidate;
    }
  }

  return null;
}

export function toUserErrorMessage(message: string): string {
  if (!hasText(message)) {
    return translateError('ERR_INTERNAL_SERVER_ERROR');
  }

  return message.startsWith('ERR_') ? translateError(message) : message;
}

/**
 * Resolve API error payload into a displayable message/error code string.
 *
 * Supports backend error payloads such as:
 * - { detail: "ERR_QUOTA_EXCEEDED", error_code: "ERR_QUOTA_EXCEEDED" }
 * - { error_code: "ERR_QUOTA_EXCEEDED", error_detail: { message: "..." } }
 * - { detail: "human readable message" }
 */
export function resolveApiErrorMessage(
  payload: unknown,
  fallbackMessage: string,
): string {
  if (!payload || typeof payload !== 'object') {
    return fallbackMessage;
  }

  const errorPayload = payload as Record<string, unknown>;
  const candidates = [
    errorPayload.error_code,
    errorPayload.detail,
    errorPayload.error_detail,
    errorPayload.message,
    errorPayload.error,
  ];

  for (const candidate of candidates) {
    if (hasText(candidate)) {
      return candidate;
    }

    const nested = extractNestedMessage(candidate);
    if (nested) {
      return nested;
    }
  }

  return fallbackMessage;
}

/**
 * Translate an error code to a localized error message.
 *
 * Looks up the error code in the 'errors' i18n namespace. An untranslated
 * `ERR_*` code falls back to the generic error message so raw codes never
 * reach the UI; any other text is returned unchanged.
 *
 * @param errorCode - The error code from the backend (e.g., "ERR_PROJECT_NOT_FOUND")
 * @returns Translated error message
 */
export function translateError(errorCode: string): string {
  const normalizedCode = errorCode.trim();
  if (!normalizedCode) {
    return i18n.t('errors:ERR_INTERNAL_SERVER_ERROR');
  }

  // Try to find translation in errors namespace
  const translated = i18n.t(`errors:${normalizedCode}`, { defaultValue: '' });

  if (translated) {
    return translated;
  }

  // Quota sub-codes should never leak raw technical code in UI.
  if (normalizedCode.startsWith('ERR_QUOTA_')) {
    const quotaFallback = i18n.t('errors:ERR_QUOTA_EXCEEDED', { defaultValue: '' });
    if (quotaFallback) {
      return quotaFallback;
    }
  }

  // Unknown error codes never reach the UI as raw codes: show the generic message.
  if (normalizedCode.startsWith('ERR_')) {
    return i18n.t('errors:ERR_INTERNAL_SERVER_ERROR');
  }

  // Plain (non-code) messages pass through unchanged.
  return normalizedCode;
}

/**
 * Handle API error and return a translated, user-friendly message.
 *
 * Processes various error types (Error objects, strings, unknown) and returns
 * an appropriate translated message. Error codes (starting with "ERR_") are
 * translated via the i18n system; other messages are returned as-is.
 *
 * @param error - The error object from API call (can be Error, string, or unknown)
 * @returns Translated error message suitable for display to users
 */
export function handleApiError(error: unknown): string {
  // Check if it's an Error object with a message
  if (error instanceof Error) {
    const message = error.message;
    if (hasText(message)) {
      return toUserErrorMessage(message);
    }
  }

  // Handle string errors
  if (typeof error === 'string') {
    if (hasText(error)) {
      return toUserErrorMessage(error);
    }
  }

  // Handle API-like payload objects
  if (error && typeof error === 'object') {
    const resolved = resolveApiErrorMessage(error, '');
    if (hasText(resolved)) {
      return toUserErrorMessage(resolved);
    }
  }

  // Fallback to generic error message
  return i18n.t('errors:ERR_INTERNAL_SERVER_ERROR');
}
