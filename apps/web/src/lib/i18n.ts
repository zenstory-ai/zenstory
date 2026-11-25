/**
 * Internationalization (i18n) configuration and initialization.
 *
 * Sets up i18next with React integration for multi-language support.
 * Uses one lazy-loaded bundle per language; the locale comes from an
 * explicit `?lang=` or the preference stored in localStorage.
 *
 * Architecture:
 * - Uses i18next as the core i18n framework
 * - React integration via react-i18next hooks (useTranslation)
 * - Locale bundle backend code-splits the existing /public/locales/ JSON by language
 * - `?lang=` (sent by zenstory.ai links) or the stored preference, persisted to localStorage
 *
 * Supported Languages:
 * - English: 'en' - Default/fallback language for browsers
 * - Chinese (Simplified): 'zh' - Fallback for crawlers to keep indexed pages stable
 *
 * Translation Namespaces:
 * - common: Shared UI elements, buttons, labels
 * - auth: Login, registration, password reset
 * - dashboard: Dashboard-specific content
 * - editor: Editor interface and controls
 * - chat: AI chat interface
 * - settings: Application settings
 * - home: Landing page content
 * - privacy: Privacy policy
 * - errors: Error messages
 * - project: Project management
 * - versions: Version history
 * - admin: Admin panel
 * - skills: Skills/special abilities
 * - materials: Writing materials/references
 * - onboarding: New user onboarding and persona setup
 *
 * Usage:
 * ```tsx
 * import { useTranslation } from 'react-i18next';
 *
 * function MyComponent() {
 *   const { t, i18n } = useTranslation();
 *   return (
 *     <div>
 *       <h1>{t('welcome')}</h1>
 *       <button onClick={() => i18n.changeLanguage('en')}>
 *         Switch to English
 *       </button>
 *     </div>
 *   );
 * }
 * ```
 *
 * @module lib/i18n
 */

import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import LanguageDetector from 'i18next-browser-languagedetector';
import { LocaleBundleBackend } from './localeBundleBackend';
import { CORE_LOCALE_NAMESPACES } from './localeNamespaces';

/** Search and link-preview crawlers: they keep the Chinese default so each indexed page stays in one language. */
const CRAWLER_USER_AGENT = /bot|crawl|spider|slurp|facebookexternalhit|inspectiontool/i;

/**
 * Which language the app opens in. Detection order: an explicit `?lang=` (zenstory.ai links carry the
 * language of the page the visitor came from), then the preference stored in localStorage, then the
 * browser's languages (people only, not crawlers). A browser language the app does not support falls
 * back to English; crawlers skip `navigator` and keep Chinese fallback so already-indexed pages stay
 * in one stable language. The detected language is cached, so the choice outlives the first page.
 *
 * Regional codes are reduced to the language (`zh-CN` → `zh`) before matching: i18next takes the first
 * exact match, so otherwise Edge's `['zh-CN', 'en', …]` on Chinese Windows would open in English.
 */
export function languageDetectionFor(userAgent: string) {
  return {
    order: CRAWLER_USER_AGENT.test(userAgent)
      ? ['querystring', 'localStorage']
      : ['querystring', 'localStorage', 'navigator'],
    convertDetectedLanguage: (language: string) => language.split('-')[0],
    lookupQuerystring: 'lang',
    caches: ['localStorage'],
    lookupLocalStorage: 'zenstory-language',
  };
}

export const languageDetection = languageDetectionFor(navigator.userAgent);

export const languageOptions = {
  /** Default language when browser detection fails */
  fallbackLng: 'en',
  /** Languages with available translations */
  supportedLngs: ['zh', 'en'],
};

/**
 * Resolve i18next options for a given user agent.
 * Crawlers do not read `navigator.languages` and must still fall back to `'zh'`
 * so already-indexed Chinese pages (and static `<html lang="zh-CN">` shells) keep a stable language.
 */
export function languageOptionsFor(userAgent: string) {
  return {
    ...languageOptions,
    fallbackLng: CRAWLER_USER_AGENT.test(userAgent) ? 'zh' : languageOptions.fallbackLng,
  };
}

i18n
  // One lazy bundle per language, shared across all namespace reads
  .use(new LocaleBundleBackend())
  // Language detection from `?lang=` or localStorage (see languageDetection)
  .use(LanguageDetector)
  // React integration for hooks and components
  .use(initReactI18next)
  .init({
    ...languageOptionsFor(navigator.userAgent),

    /**
     * Core translation namespaces loaded upfront on initial boot.
     * Heavy/route-specific namespaces (admin, privacy, materials, skills,
     * onboarding, inspirations, docs, referral) are loaded lazily on demand
     * by LocaleBundleBackend when their routes or Suspense-wrapped panes mount.
     */
    ns: [...CORE_LOCALE_NAMESPACES],

    /** Default namespace when not specified in useTranslation() */
    defaultNS: 'common',

    /** Language detection configuration */
    detection: languageDetection,

    /** Interpolation settings */
    interpolation: {
      /** React already escapes values, disable i18next escaping */
      escapeValue: false,
    },

    /** Debug mode - set to true for development to see missing keys */
    debug: false,

    /** React-specific configuration */
    react: {
      /** Use React Suspense for loading translations */
      useSuspense: true,
    },
  });

export default i18n;
