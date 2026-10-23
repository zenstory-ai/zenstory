import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import i18next from 'i18next';
import LanguageDetector from 'i18next-browser-languagedetector';
import { languageDetection, languageDetectionFor, languageOptions } from '../i18n';

// zenstory.ai links into the app carry `?lang=` for the page the visitor came from.
async function languageAt(url: string, detection = languageDetection) {
  window.history.replaceState({}, '', url);
  const instance = i18next.createInstance().use(LanguageDetector);
  await instance.init({ ...languageOptions, detection, resources: {} });
  return instance.language;
}

function browserLanguages(languages: string[]) {
  vi.spyOn(navigator, 'languages', 'get').mockReturnValue(languages);
  vi.spyOn(navigator, 'language', 'get').mockReturnValue(languages[0]);
}

describe('app language handoff from zenstory.ai', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    window.history.replaceState({}, '', '/');
    vi.restoreAllMocks();
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

  it('follows the browser language when there is no lang parameter or stored preference', async () => {
    browserLanguages(['en-US', 'en']);
    expect(await languageAt('/login')).toBe('en');
    expect(localStorage.getItem('zenstory-language')).toBe('en');

    localStorage.clear();
    browserLanguages(['zh-CN', 'zh']);
    expect(await languageAt('/login')).toBe('zh');
  });

  it('ranks a regional Chinese browser language above a later English one', async () => {
    // Edge on Chinese Windows lists zh-CN first and then plain English codes.
    browserLanguages(['zh-CN', 'en', 'en-GB', 'en-US']);
    expect(await languageAt('/register')).toBe('zh');

    localStorage.clear();
    browserLanguages(['zh-TW', 'en']);
    expect(await languageAt('/register')).toBe('zh');
  });

  it('keeps Chinese for unsupported browser languages and for crawlers', async () => {
    browserLanguages(['fr-FR', 'fr']);
    expect(await languageAt('/')).toBe('zh');

    localStorage.clear();
    browserLanguages(['en-US', 'en']);
    const googlebot = 'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)';
    expect(await languageAt('/', languageDetectionFor(googlebot))).toBe('zh');
  });

  it('keeps a stored preference over the browser language', async () => {
    localStorage.setItem('zenstory-language', 'zh');
    browserLanguages(['en-US', 'en']);
    expect(await languageAt('/dashboard')).toBe('zh');
  });
});
