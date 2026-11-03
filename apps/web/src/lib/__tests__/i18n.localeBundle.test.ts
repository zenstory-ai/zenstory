import i18next from 'i18next';
import { describe, expect, it, vi } from 'vitest';
import {
  LocaleBundleBackend,
  type LocaleBundle,
  type LocaleBundleLoaders,
} from '../localeBundleBackend';

const namespaces = ['common', 'auth', 'dashboard', 'editor', 'chat', 'settings', 'home', 'privacy', 'errors', 'project', 'versions', 'admin', 'announcements', 'skills', 'materials', 'points', 'referral', 'onboarding', 'inspirations', 'docs'];

describe('translation locale bundle backend', () => {
  it('loads real locale bundles and switches languages without namespace requests', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch');
    const instance = i18next.createInstance().use(new LocaleBundleBackend());

    await instance.init({
      lng: 'zh',
      fallbackLng: 'zh',
      supportedLngs: ['zh', 'en'],
      ns: namespaces,
      defaultNS: 'common',
    });
    expect(instance.t('language.zh')).toBe('中文');
    expect(instance.hasResourceBundle('zh', 'docs')).toBe(true);

    await instance.changeLanguage('en');
    expect(instance.t('language.en')).toBe('EN');
    expect(instance.hasResourceBundle('en', 'docs')).toBe(true);
    await instance.changeLanguage('zh');
    expect(instance.t('language.zh')).toBe('中文');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('loads each language once when i18next requests multiple namespaces', async () => {
    const bundle = (language: string): LocaleBundle => ({
      common: { language, ...(language === 'zh' ? { fallbackOnly: '中文回退' } : {}) },
      auth: { title: `${language} auth` },
      dashboard: { title: `${language} dashboard` },
    });
    const loaders: LocaleBundleLoaders = {
      zh: vi.fn(async () => bundle('zh')),
      en: vi.fn(async () => bundle('en')),
    };
    const instance = i18next.createInstance().use(new LocaleBundleBackend(loaders));

    await instance.init({
      lng: 'en',
      fallbackLng: 'zh',
      supportedLngs: ['zh', 'en'],
      ns: ['common', 'auth', 'dashboard'],
      defaultNS: 'common',
    });

    expect(loaders.en).toHaveBeenCalledTimes(1);
    expect(loaders.zh).toHaveBeenCalledTimes(1);
    expect(instance.t('auth:title')).toBe('en auth');
    expect(instance.t('dashboard:title')).toBe('en dashboard');
    expect(instance.t('fallbackOnly')).toBe('中文回退');
  });

  it('uses the bounded i18next retry for a transient language import failure', async () => {
    const en = vi.fn()
      .mockRejectedValueOnce(new Error('temporary chunk failure'))
      .mockResolvedValue({ common: { ready: 'ready' } });
    const instance = i18next.createInstance().use(new LocaleBundleBackend({
      zh: async () => ({ common: {} }),
      en,
    }));

    await instance.init({
      lng: 'en',
      fallbackLng: false,
      supportedLngs: ['zh', 'en'],
      ns: ['common'],
      defaultNS: 'common',
      retryTimeout: 1,
    });

    expect(en).toHaveBeenCalledTimes(2);
    expect(instance.t('ready')).toBe('ready');
  });

  it('allows an explicit reload after bounded retries are exhausted', async () => {
    const en = vi.fn()
      .mockRejectedValueOnce(new Error('temporary chunk failure'))
      .mockRejectedValueOnce(new Error('temporary chunk failure'))
      .mockResolvedValue({ common: { ready: 'ready' } });
    const instance = i18next.createInstance().use(new LocaleBundleBackend({
      zh: async () => ({ common: {} }),
      en,
    }));

    await instance.init({
      lng: 'zh',
      fallbackLng: false,
      supportedLngs: ['zh', 'en'],
      ns: ['common'],
      defaultNS: 'common',
      maxRetries: 1,
      retryTimeout: 1,
    });

    await instance.changeLanguage('en');
    expect(en).toHaveBeenCalledTimes(2);
    expect(instance.hasResourceBundle('en', 'common')).toBe(false);

    await instance.reloadResources('en', ['common']);
    expect(en).toHaveBeenCalledTimes(3);
    expect(instance.t('ready')).toBe('ready');
  });

  it('reports an unknown namespace instead of silently caching an empty bundle', async () => {
    const backend = new LocaleBundleBackend({
      zh: async () => ({ common: {} }),
      en: async () => ({ common: {} }),
    });

    await expect(new Promise((resolve, reject) => {
      backend.read('zh', 'missing', (error, data) => error ? reject(error) : resolve(data));
    })).rejects.toThrow('Unknown translation namespace: zh/missing');
  });
});
