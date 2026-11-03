import type {
  BackendModule,
  InitOptions,
  ReadCallback,
  ResourceLanguage,
  Services,
} from 'i18next';

export type LocaleBundle = ResourceLanguage;
export type LocaleBundleLoaders = Record<'zh' | 'en', () => Promise<LocaleBundle>>;

const defaultLoaders: LocaleBundleLoaders = {
  zh: async () => (await import('virtual:zenstory-locale-zh')).default,
  en: async () => (await import('virtual:zenstory-locale-en')).default,
};

/** i18next backend that loads one code-split bundle per language. */
export class LocaleBundleBackend implements BackendModule {
  readonly type = 'backend' as const;
  private readonly pending = new Map<keyof LocaleBundleLoaders, Promise<LocaleBundle>>();
  private readonly loaders: LocaleBundleLoaders;

  constructor(loaders: LocaleBundleLoaders = defaultLoaders) {
    this.loaders = loaders;
  }

  init(_services: Services, _backendOptions: object, _i18nextOptions: InitOptions): void {}

  read(language: string, namespace: string, callback: ReadCallback): void {
    const normalizedLanguage = language.split('-')[0] as keyof LocaleBundleLoaders;
    const loader = this.loaders[normalizedLanguage];
    if (!loader) {
      callback(new Error(`Unsupported translation language: ${language}`), null);
      return;
    }

    let pending = this.pending.get(normalizedLanguage);
    if (!pending) {
      pending = loader().catch((error: unknown) => {
        this.pending.delete(normalizedLanguage);
        throw error;
      });
      this.pending.set(normalizedLanguage, pending);
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
