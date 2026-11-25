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

function enqueueLanguageSave(language: string): void {
  pendingSave = pendingSave
    .catch(() => undefined)
    .then(() => api.put('/api/auth/me/preferences', { ui_language: language }))
    .catch(error => logger.warn('[AccountLanguage] Failed to save the interface language', error));
}

/**
 * Keep the interface language on the signed-in account. The account's language (sent with the
 * user payload) wins, so a new device or browser opens in it; if the account has no saved
 * language yet (or an unsupported one), the current interface language is backfilled at once.
 * Subsequent switches are reported through `onSwitched` (so the signed-in user and its cache
 * follow at once) and saved back. Returns the cleanup for sign-out or a different account.
 */
export function syncAccountLanguage(
  i18n: I18n,
  accountLanguage: string | null | undefined,
  onSwitched?: (language: string) => void,
): () => void {
  let active = true;
  let saved = supportedAccountLanguage(accountLanguage) ?? '';
  const current = supportedAccountLanguage(i18n.language);

  if (saved) {
    if (saved !== current) {
      void i18n.changeLanguage(saved);
    }
  } else if (current) {
    saved = current;
    onSwitched?.(current);
    enqueueLanguageSave(current);
  }

  const onLanguageChanged = (lng: string) => {
    const language = supportedAccountLanguage(lng);
    if (!active || !language || language === saved) return;
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
