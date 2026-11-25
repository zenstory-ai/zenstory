import type {
  BackendModule,
  InitOptions,
  ReadCallback,
  ResourceKey,
  ResourceLanguage,
  Services,
} from 'i18next';
import type { LazyLocaleNamespace, LocaleLanguage } from './localeNamespaces';

export type LocaleBundle = ResourceLanguage;
export type LocaleBundleLoaders = Record<LocaleLanguage, () => Promise<LocaleBundle>>;
export type LazyNamespaceLoaders = Record<
  LocaleLanguage,
  Partial<Record<LazyLocaleNamespace | string, () => Promise<ResourceKey>>>
>;

const defaultCoreLoaders: LocaleBundleLoaders = {
  zh: async () => (await import('virtual:zenstory-locale-zh-core')).default,
  en: async () => (await import('virtual:zenstory-locale-en-core')).default,
};

const defaultLazyLoaders: LazyNamespaceLoaders = {
  zh: {
    admin: async () => (await import('virtual:zenstory-locale-zh-admin')).default,
    docs: async () => (await import('virtual:zenstory-locale-zh-docs')).default,
    inspirations: async () => (await import('virtual:zenstory-locale-zh-inspirations')).default,
    materials: async () => (await import('virtual:zenstory-locale-zh-materials')).default,
    privacy: async () => (await import('virtual:zenstory-locale-zh-privacy')).default,
    skills: async () => (await import('virtual:zenstory-locale-zh-skills')).default,
  },
  en: {
    admin: async () => (await import('virtual:zenstory-locale-en-admin')).default,
    docs: async () => (await import('virtual:zenstory-locale-en-docs')).default,
    inspirations: async () => (await import('virtual:zenstory-locale-en-inspirations')).default,
    materials: async () => (await import('virtual:zenstory-locale-en-materials')).default,
    privacy: async () => (await import('virtual:zenstory-locale-en-privacy')).default,
    skills: async () => (await import('virtual:zenstory-locale-en-skills')).default,
  },
};

/**
 * i18next backend that loads one code-split core bundle per language upfront
 * and fetches heavy/route-specific namespaces on demand as separate chunks.
 */
export class LocaleBundleBackend implements BackendModule {
  readonly type = 'backend' as const;
  private readonly pendingCore = new Map<LocaleLanguage, Promise<LocaleBundle>>();
  private readonly pendingLazy = new Map<string, Promise<ResourceKey>>();
  private readonly coreLoaders: LocaleBundleLoaders;
  private readonly lazyLoaders: LazyNamespaceLoaders;

  constructor(
    coreLoaders: LocaleBundleLoaders = defaultCoreLoaders,
    lazyLoaders: LazyNamespaceLoaders = defaultLazyLoaders,
  ) {
    this.coreLoaders = coreLoaders;
    this.lazyLoaders = lazyLoaders;
  }

  init(_services: Services, _backendOptions: object, _i18nextOptions: InitOptions): void {}

  read(language: string, namespace: string, callback: ReadCallback): void {
    const normalizedLanguage = language.split('-')[0] as LocaleLanguage;
    const coreLoader = this.coreLoaders[normalizedLanguage];
    if (!coreLoader) {
      callback(new Error(`Unsupported translation language: ${language}`), null);
      return;
    }

    const lazyLoader = this.lazyLoaders[normalizedLanguage]?.[namespace];
    if (lazyLoader) {
      const cacheKey = `${normalizedLanguage}:${namespace}`;
      let pending = this.pendingLazy.get(cacheKey);
      if (!pending) {
        pending = lazyLoader().catch((error: unknown) => {
          this.pendingLazy.delete(cacheKey);
          throw error;
        });
        this.pendingLazy.set(cacheKey, pending);
      }

      void pending.then(
        (resource) => callback(null, resource),
        // `true` marks a transient backend failure, so i18next leaves this
        // language/namespace reloadable instead of permanently caching failure.
        (error: unknown) => callback(error instanceof Error ? error : new Error(String(error)), true),
      );
      return;
    }

    let pending = this.pendingCore.get(normalizedLanguage);
    if (!pending) {
      pending = coreLoader().catch((error: unknown) => {
        this.pendingCore.delete(normalizedLanguage);
        throw error;
      });
      this.pendingCore.set(normalizedLanguage, pending);
    }

    void pending.then(
      (bundle) => {
        if (!Object.prototype.hasOwnProperty.call(bundle, namespace)) {
          callback(new Error(`Unknown translation namespace: ${language}/${namespace}`), null);
          return;
        }
        callback(null, bundle[namespace]);
      },
      // `true` marks a transient backend failure, so i18next leaves this
      // language/namespace reloadable instead of permanently caching failure.
      (error: unknown) => callback(error instanceof Error ? error : new Error(String(error)), true),
    );
  }
}
