import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import i18next from 'i18next';
import LanguageDetector from 'i18next-browser-languagedetector';
import { languageDetection, languageOptions } from '../i18n';

// zenstory.ai links into the app carry `?lang=` for the page the visitor came from.
async function languageAt(url: string) {
  window.history.replaceState({}, '', url);
  const instance = i18next.createInstance().use(LanguageDetector);
  await instance.init({ ...languageOptions, detection: languageDetection, resources: {} });
  return instance.language;
}

describe('app language handoff from zenstory.ai', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    window.history.replaceState({}, '', '/');
  });

  it('opens in English for a visitor sent from the English site and remembers it', async () => {
    expect(await languageAt('/login?lang=en&source=org_header')).toBe('en');
    expect(localStorage.getItem('zenstory-language')).toBe('en');
    expect(await languageAt('/dashboard')).toBe('en');
  });

  it('follows the page language over an earlier stored default', async () => {
    localStorage.setItem('zenstory-language', 'zh');

    expect(await languageAt('/register?lang=en&source=org_home_closing')).toBe('en');
    expect(localStorage.getItem('zenstory-language')).toBe('en');
  });

  it('ignores an unsupported lang value and keeps the stored preference', async () => {
    localStorage.setItem('zenstory-language', 'en');

    expect(await languageAt('/login?lang=fr')).toBe('en');
  });

  it('keeps Chinese as the default without a lang parameter or stored preference', async () => {
    expect(await languageAt('/login')).toBe('zh');
  });
});
