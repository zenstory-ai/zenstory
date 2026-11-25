/**
 * Single source of truth for i18n locale namespace splitting.
 *
 * - CORE_LOCALE_NAMESPACES: bundled into `virtual:zenstory-locale-{lang}-core`
 *   and loaded upfront on initial boot. Includes every namespace requested on
 *   initial `/dashboard` or `/project/:id` render (plus imperative `errors` and
 *   tiny modal namespaces that mount without a local Suspense boundary).
 * - LAZY_LOCALE_NAMESPACES: each emitted as its own code-split virtual module
 *   `virtual:zenstory-locale-{lang}-{ns}` and fetched on demand when a route or
 *   Suspense-wrapped pane requests it.
 */
export const LOCALE_LANGUAGES = ['zh', 'en'] as const;
export type LocaleLanguage = (typeof LOCALE_LANGUAGES)[number];

export const CORE_LOCALE_NAMESPACES = [
  'announcements',
  'auth',
  'chat',
  'common',
  'dashboard',
  'editor',
  'errors',
  'home',
  'points',
  'project',
  'settings',
  'versions',
] as const;

export const LAZY_LOCALE_NAMESPACES = [
  'admin',
  'docs',
  'inspirations',
  'materials',
  'onboarding',
  'privacy',
  'referral',
  'skills',
] as const;

export type CoreLocaleNamespace = (typeof CORE_LOCALE_NAMESPACES)[number];
export type LazyLocaleNamespace = (typeof LAZY_LOCALE_NAMESPACES)[number];
export type LocaleNamespace = CoreLocaleNamespace | LazyLocaleNamespace;

export const LAZY_LOCALE_NAMESPACE_SET: ReadonlySet<string> = new Set(LAZY_LOCALE_NAMESPACES);
export const CORE_LOCALE_NAMESPACE_SET: ReadonlySet<string> = new Set(CORE_LOCALE_NAMESPACES);
