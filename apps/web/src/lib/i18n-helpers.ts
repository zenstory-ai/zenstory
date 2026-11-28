/**
 * Internationalization (i18n) helper utilities.
 *
 * Provides utility functions for managing and retrieving locale settings
 * used throughout the application for language and region-specific formatting.
 *
 * Supported locales:
 * - 'en' (English) - Default fallback for non-Chinese locales (aligned with PR #478)
 * - 'zh' (Chinese, Simplified) - Selected only for explicit zh* locales
 *
 * @module lib/i18n-helpers
 */

import i18next from 'i18next';

/**
 * Supported locale codes for the application.
 *
 * These represent the primary language codes supported by the i18n system.
 * Each locale has corresponding translation files in /public/locales/{locale}/.
 */
export type SupportedLocale = 'zh' | 'en';

/**
 * Array of all supported locale codes.
 *
 * Used for validation when reading user preferences from storage.
 */
export const SUPPORTED_LOCALES: SupportedLocale[] = ['zh', 'en'];

/**
 * Default locale used when no preference is stored or detected.
 *
 * Falls back to English so non-Chinese readers (en, id, pt, fr, es, etc.)
 * never get silently routed to Chinese.
 */
export const DEFAULT_LOCALE: SupportedLocale = 'en';

/**
 * Get the current locale setting.
 *
 * Prefers the active `i18next` language first, then reads 'zenstory-language'
 * from localStorage, and falls back to DEFAULT_LOCALE ('en'). Only explicit
 * `zh*` values resolve to `'zh'`.
 *
 * @returns Current locale code ('zh' or 'en')
 */
export function getLocale(): SupportedLocale {
  const active = (i18next.resolvedLanguage || i18next.language || '').trim();
  if (active) {
    return normalizeLocale(active);
  }

  try {
    const storedLocale = (localStorage.getItem('zenstory-language') || '').trim();
    if (storedLocale) {
      return normalizeLocale(storedLocale);
    }
  } catch {
    // Ignore storage access errors and fall back to default locale.
  }

  return DEFAULT_LOCALE;
}

/**
 * Persist and activate a new UI locale when it differs from the current one.
 */
export async function switchLocale(
  i18n: { changeLanguage: (lng: string) => Promise<unknown> },
  currentLanguage: SupportedLocale,
  nextLanguage: SupportedLocale,
): Promise<void> {
  if (currentLanguage === nextLanguage) return;

  try {
    localStorage.setItem('zenstory-language', nextLanguage);
  } catch {
    // Ignore storage access errors and still update active session language.
  }

  await i18n.changeLanguage(nextLanguage);
}

/**
 * Get the full locale code for date/time/number formatting.
 *
 * Converts the simple locale code to the full BCP 47 language tag
 * required by Intl APIs (toLocaleDateString, toLocaleTimeString, etc.).
 *
 * @returns Full locale code ('zh-CN' or 'en-US')
 *
 * @example
 * ```ts
 * const date = new Date();
 * const formatted = date.toLocaleDateString(getLocaleCode());
 * // Returns: '2024/01/12' (zh-CN) or '1/12/2024' (en-US)
 * ```
 */
export function getLocaleCode(): string {
  const locale = getLocale();
  return locale === 'zh' ? 'zh-CN' : 'en-US';
}

/**
 * Normalize an i18n language string (e.g. "zh-CN", "en-US") to supported locale code.
 *
 * @param language - Raw language code from i18next/browser detector
 * @returns Normalized supported locale ('zh' or 'en')
 */
export function normalizeLocale(language: string | null | undefined): SupportedLocale {
  if (!language) {
    return DEFAULT_LOCALE;
  }

  const normalized = language.trim().toLowerCase();
  if (!normalized) {
    return DEFAULT_LOCALE;
  }
  return normalized.startsWith('zh') ? 'zh' : 'en';
}
