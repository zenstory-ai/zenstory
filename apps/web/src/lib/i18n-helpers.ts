/**
 * Internationalization (i18n) helper utilities.
 *
 * Provides utility functions for managing and retrieving locale settings
 * used throughout the application for language and region-specific formatting.
 *
 * Supported locales:
 * - 'zh' (Chinese, Simplified) - Default
 * - 'en' (English)
 *
 * @module lib/i18n-helpers
 */

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
 * Falls back to Chinese (Simplified) as the primary language.
 */
export const DEFAULT_LOCALE: SupportedLocale = 'zh';

/**
 * Get the current locale setting from localStorage.
 *
 * Reads the user's language preference from 'zenstory-language' in localStorage.
 * Returns the default locale if no valid preference is stored or if running
 * in a server-side rendering context (window undefined).
 *
 * @returns Current locale code ('zh' or 'en')
 *
 * @example
 * ```ts
 * const locale = getLocale();
 * console.log(locale); // 'zh' or 'en'
 * ```
 */
export function getLocale(): SupportedLocale {
  try {
    const storedLocale = localStorage.getItem('zenstory-language') as SupportedLocale;
    if (storedLocale && SUPPORTED_LOCALES.includes(storedLocale)) {
      return storedLocale;
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

  const normalized = language.toLowerCase();
  return normalized.startsWith('en') ? 'en' : 'zh';
}
