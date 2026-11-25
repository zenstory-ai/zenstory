import type { i18n as I18n } from 'i18next';
import { api } from './apiClient';
import { logger } from './logger';

const SUPPORTED_LANGUAGES = new Set(['zh', 'en']);

// Saves go out one after another, so the last switch is the one the account keeps.
let pendingSave: Promise<unknown> = Promise.resolve();

function primaryLanguage(language: string | null | undefined): string {
  return (language ?? '').split('-')[0].toLowerCase();
}

export function supportedAccountLanguage(language: string | null | undefined): string | null {
  const primary = primaryLanguage(language);
  return SUPPORTED_LANGUAGES.has(primary) ? primary : null;
}

interface SaveOptions {
  onlyIfUnset?: boolean;
  onResolved?: (storedLanguage: string | null) => void;
}

function enqueueLanguageSave(language: string, options: SaveOptions = {}): void {
  const payload = options.onlyIfUnset
    ? { ui_language: language, only_if_unset: true }
    : { ui_language: language };
  pendingSave = pendingSave
    .catch(() => undefined)
    .then(() => api.put<{ ui_language?: string | null }>('/api/auth/me/preferences', payload))
    .then(response => {
      options.onResolved?.(supportedAccountLanguage(response?.ui_language));
    })
    .catch(error => logger.warn('[AccountLanguage] Failed to save the interface language', error));
}

/**
 * Keep the interface language on the signed-in account. The account's language (sent with the
 * user payload) wins, so a new device or browser opens in it; if the account has no saved
 * language yet (or an unsupported one), the current interface language is backfilled with
 * `only_if_unset: true` so a transient read failure never overwrites an existing preference,
 * and the server's returned value is adopted. Subsequent switches are reported through
 * `onSwitched` (so the signed-in user and its cache follow at once) and saved back.
 * Returns the cleanup for sign-out or a different account.
 */
export function syncAccountLanguage(
  i18n: I18n,
  accountLanguage: string | null | undefined,
  onSwitched?: (language: string) => void,
  isCurrentAccount?: () => boolean,
): () => void {
  let active = true;
  let manuallySwitched = false;
  let saved = supportedAccountLanguage(accountLanguage) ?? '';
  const current = supportedAccountLanguage(i18n.language);
  const stillForAccount = isCurrentAccount ?? (() => active);

  if (saved) {
    if (saved !== current) {
      void i18n.changeLanguage(saved);
    }
  } else if (current) {
    saved = current;
    onSwitched?.(current);
    enqueueLanguageSave(current, {
      onlyIfUnset: true,
      onResolved: stored => {
        // Adopt the server's stored language when it already had one (e.g. /api/auth/me hit
        // a transient DB read error) unless the user signed out or manually switched away.
        if (!stillForAccount() || manuallySwitched || !stored || stored === current) return;
        if (supportedAccountLanguage(i18n.language) !== current) return;
        saved = stored;
        onSwitched?.(stored);
        void i18n.changeLanguage(stored);
      },
    });
  }

  const onLanguageChanged = (lng: string) => {
    const language = supportedAccountLanguage(lng);
    if (!active || !language || language === saved) return;
    manuallySwitched = true;
    saved = language;
    onSwitched?.(language);
    enqueueLanguageSave(language);
  };
  i18n.on('languageChanged', onLanguageChanged);

  return () => {
    active = false;
    i18n.off('languageChanged', onLanguageChanged);
  };
}
