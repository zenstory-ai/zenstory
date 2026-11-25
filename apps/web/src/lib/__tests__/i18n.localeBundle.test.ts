import i18next from 'i18next';
import { describe, expect, it, vi } from 'vitest';
import {
  LocaleBundleBackend,
  type LazyNamespaceLoaders,
  type LocaleBundle,
  type LocaleBundleLoaders,
} from '../localeBundleBackend';
import { CORE_LOCALE_NAMESPACES, LAZY_LOCALE_NAMESPACES } from '../localeNamespaces';

describe('translation locale bundle backend', () => {
  it('loads only core namespaces upfront and fetches lazy namespaces on demand for both zh and en without fetch', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch');
    const instance = i18next.createInstance().use(new LocaleBundleBackend());

    await instance.init({
      lng: 'zh',
      fallbackLng: 'zh',
      supportedLngs: ['zh', 'en'],
      ns: [...CORE_LOCALE_NAMESPACES],
      defaultNS: 'common',
    });

    expect(instance.t('language.zh')).toBe('中文');
    for (const coreNs of CORE_LOCALE_NAMESPACES) {
      expect(instance.hasResourceBundle('zh', coreNs)).toBe(true);
    }
    for (const lazyNs of LAZY_LOCALE_NAMESPACES) {
      expect(instance.hasResourceBundle('zh', lazyNs)).toBe(false);
    }

    // Demand-load lazy namespaces in zh
    await instance.loadNamespaces(['admin', 'privacy', 'docs', 'materials']);
    expect(instance.hasResourceBundle('zh', 'admin')).toBe(true);
    expect(instance.hasResourceBundle('zh', 'privacy')).toBe(true);
    expect(instance.hasResourceBundle('zh', 'docs')).toBe(true);
    expect(instance.hasResourceBundle('zh', 'materials')).toBe(true);
    expect(instance.hasResourceBundle('zh', 'skills')).toBe(false);

    // Switch to en: core + previously requested lazy namespaces load cleanly
    await instance.changeLanguage('en');
    expect(instance.t('language.en')).toBe('EN');
    expect(instance.hasResourceBundle('en', 'common')).toBe(true);
    expect(instance.hasResourceBundle('en', 'docs')).toBe(true);
    expect(instance.hasResourceBundle('en', 'admin')).toBe(true);
    expect(instance.hasResourceBundle('en', 'skills')).toBe(false);

    // Demand-load another lazy namespace in en
    await instance.loadNamespaces('skills');
    expect(instance.hasResourceBundle('en', 'skills')).toBe(true);

    await instance.changeLanguage('zh');
    expect(instance.t('language.zh')).toBe('中文');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('loads each language core bundle once and never invokes lazy loaders until requested', async () => {
    const coreBundle = (language: string): LocaleBundle => ({
      common: { language, ...(language === 'zh' ? { fallbackOnly: '中文回退' } : {}) },
      auth: { title: `${language} auth` },
      dashboard: { title: `${language} dashboard` },
    });
    const coreLoaders: LocaleBundleLoaders = {
      zh: vi.fn(async () => coreBundle('zh')),
      en: vi.fn(async () => coreBundle('en')),
    };
    const lazyLoaders: LazyNamespaceLoaders = {
      zh: {
        admin: vi.fn(async () => ({ heading: 'zh admin' })),
        docs: vi.fn(async () => ({ heading: 'zh docs' })),
      },
      en: {
        admin: vi.fn(async () => ({ heading: 'en admin' })),
        docs: vi.fn(async () => ({ heading: 'en docs' })),
      },
    };
    const instance = i18next.createInstance().use(new LocaleBundleBackend(coreLoaders, lazyLoaders));

    await instance.init({
      lng: 'en',
      fallbackLng: 'zh',
      supportedLngs: ['zh', 'en'],
      ns: ['common', 'auth', 'dashboard'],
      defaultNS: 'common',
    });

    expect(coreLoaders.en).toHaveBeenCalledTimes(1);
    expect(coreLoaders.zh).toHaveBeenCalledTimes(1);
    expect(lazyLoaders.en.admin).not.toHaveBeenCalled();
    expect(lazyLoaders.zh.admin).not.toHaveBeenCalled();
    expect(lazyLoaders.en.docs).not.toHaveBeenCalled();
    expect(instance.t('auth:title')).toBe('en auth');
    expect(instance.t('dashboard:title')).toBe('en dashboard');
    expect(instance.t('fallbackOnly')).toBe('中文回退');

    // Requesting 'admin' loads ONLY admin (not docs) and does not re-fetch core
    await instance.loadNamespaces('admin');
    expect(lazyLoaders.en.admin).toHaveBeenCalledTimes(1);
    expect(lazyLoaders.zh.admin).toHaveBeenCalledTimes(1);
    expect(lazyLoaders.en.docs).not.toHaveBeenCalled();
    expect(coreLoaders.en).toHaveBeenCalledTimes(1);
    expect(instance.t('admin:heading')).toBe('en admin');
  });

  it('uses the bounded i18next retry for a transient core language import failure', async () => {
    const en = vi.fn()
      .mockRejectedValueOnce(new Error('temporary chunk failure'))
      .mockResolvedValue({ common: { ready: 'ready' } });
    const instance = i18next.createInstance().use(new LocaleBundleBackend({
      zh: async () => ({ common: {} }),
      en,
    }, { zh: {}, en: {} }));

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

  it('retries and allows explicit reload for a transient lazy namespace chunk failure', async () => {
    const enAdmin = vi.fn()
      .mockRejectedValueOnce(new Error('temporary lazy chunk failure'))
      .mockRejectedValueOnce(new Error('temporary lazy chunk failure'))
      .mockResolvedValue({ panel: 'Admin Panel' });
    const instance = i18next.createInstance().use(new LocaleBundleBackend(
      {
        zh: async () => ({ common: {} }),
        en: async () => ({ common: {} }),
      },
      {
        zh: {},
        en: { admin: enAdmin },
      },
    ));

    await instance.init({
      lng: 'en',
      fallbackLng: false,
      supportedLngs: ['zh', 'en'],
      ns: ['common'],
      defaultNS: 'common',
      maxRetries: 1,
      retryTimeout: 1,
    });

    await instance.loadNamespaces('admin');
    expect(enAdmin).toHaveBeenCalledTimes(2);
    expect(instance.hasResourceBundle('en', 'admin')).toBe(false);

    await instance.reloadResources('en', ['admin']);
    expect(enAdmin).toHaveBeenCalledTimes(3);
    expect(instance.t('admin:panel')).toBe('Admin Panel');
  });

  it('allows an explicit reload after bounded retries are exhausted for core bundles', async () => {
    const en = vi.fn()
      .mockRejectedValueOnce(new Error('temporary chunk failure'))
      .mockRejectedValueOnce(new Error('temporary chunk failure'))
      .mockResolvedValue({ common: { ready: 'ready' } });
    const instance = i18next.createInstance().use(new LocaleBundleBackend({
      zh: async () => ({ common: {} }),
      en,
    }, { zh: {}, en: {} }));

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
    const backend = new LocaleBundleBackend(
      {
        zh: async () => ({ common: {} }),
        en: async () => ({ common: {} }),
      },
      { zh: {}, en: {} },
    );

    await expect(new Promise((resolve, reject) => {
      backend.read('zh', 'missing', (error, data) => error ? reject(error) : resolve(data));
    })).rejects.toThrow('Unknown translation namespace: zh/missing');
  });
});
