import type { i18n as I18n } from 'i18next';
import { api } from './apiClient';
import { logger } from './logger';

const SUPPORTED_LANGUAGES = new Set(['zh', 'en']);

function primaryLanguage(language: string | null | undefined): string {
  return (language ?? '').split('-')[0].toLowerCase();
}

/**
 * Keep the interface language on the signed-in account. The account's language (sent with the
 * user payload) wins, so a new device or browser opens in it; a language the user switches to
 * afterwards is saved back. Returns the cleanup for sign-out or a different account.
 */
export function syncAccountLanguage(i18n: I18n, accountLanguage: string | null | undefined): () => void {
  let active = true;
  let saved = primaryLanguage(accountLanguage);
  if (SUPPORTED_LANGUAGES.has(saved) && saved !== primaryLanguage(i18n.language)) {
    void i18n.changeLanguage(saved);
  }

  const onLanguageChanged = (lng: string) => {
    const language = primaryLanguage(lng);
    if (!active || !SUPPORTED_LANGUAGES.has(language) || language === saved) return;
    saved = language;
    api.put('/api/auth/me/preferences', { ui_language: language })
      .catch(error => logger.warn('[AccountLanguage] Failed to save the interface language', error));
  };
  i18n.on('languageChanged', onLanguageChanged);

  return () => {
    active = false;
    i18n.off('languageChanged', onLanguageChanged);
  };
}
