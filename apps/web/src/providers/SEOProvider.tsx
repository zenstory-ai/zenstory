import React, { createContext, useCallback, useContext, useState, useEffect } from 'react';
import { useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { pageSEOConfig, defaultSEOConfig } from '../lib/seo-config';
import type { SEOConfig } from '../types/seo';
import { getPublicCanonicalUrl, isAppPath } from '../lib/site-domain';

interface SEOContextType {
  seoConfig: SEOConfig;
  updateSEO: (config: Partial<SEOConfig>) => void;
}

const SEOContext = createContext<SEOContextType | undefined>(undefined);

function readStaticSchema(): SEOConfig['schema'] | undefined {
  const element = document.querySelector<HTMLScriptElement>('script[type="application/ld+json"]');
  if (!element?.textContent) return undefined;
  try {
    return JSON.parse(element.textContent) as SEOConfig['schema'];
  } catch {
    return undefined;
  }
}

function readStaticOpenGraph(): SEOConfig['og'] | undefined {
  const read = (property: string) =>
    document.querySelector<HTMLMetaElement>(`meta[property="${property}"]`)?.content;
  const og = {
    type: read('og:type'),
    title: read('og:title'),
    description: read('og:description'),
    image: read('og:image'),
  };

  return Object.values(og).some(Boolean) ? og : undefined;
}

function resolvedOpenGraph(config: SEOConfig, pathname: string): SEOConfig['og'] | undefined {
  if (config.noindex) return config.og;
  return config.og ?? {
    type: pathname === '/docs' || pathname.startsWith('/docs/') ? 'article' : 'website',
    title: config.title,
    description: config.description,
    image: 'https://zenstory.ai/brand/zenstory-ai-mark.svg',
  };
}

function getRouteConfig(pathname: string, language: string): SEOConfig {
  const langKey = language.startsWith('zh') ? 'zh' : 'en';
  let config = pageSEOConfig[pathname]?.[langKey];

  if (!config && pathname.startsWith('/docs/')) {
    config = pageSEOConfig['/docs']?.[langKey];
  }

  if (!config && pathname.startsWith('/project/')) {
    config = {
      zh: {
        title: '项目 - zenstory',
        description: 'AI辅助的小说创作项目',
        noindex: true,
      },
      en: {
        title: 'Project - zenstory',
        description: 'AI-assisted novel writing project',
        noindex: true,
      }
    }[langKey];
  }

  const fallback = defaultSEOConfig[langKey] ?? defaultSEOConfig.en;
  const resolved = config ?? (isAppPath(pathname) && pathname !== '/pricing'
    ? { ...fallback, noindex: true }
    : fallback);
  const canonical = getPublicCanonicalUrl(window.location.href);

  // Generated public shells carry route-specific metadata marked data-rh.
  // Seed Helmet with the same values so its first reconciliation preserves
  // rather than removes the correct server-rendered Open Graph tags.
  const staticCanonical = document.querySelector<HTMLLinkElement>('link[rel="canonical"]')?.href;
  if (staticCanonical === canonical && !resolved.noindex) {
    const staticDescription = document.querySelector<HTMLMetaElement>('meta[name="description"]')?.content;
    return {
      ...resolved,
      title: document.title || resolved.title,
      description: staticDescription || resolved.description,
      canonical,
      og: readStaticOpenGraph() ?? resolvedOpenGraph(resolved, pathname),
      schema: readStaticSchema() ?? resolved.schema,
    };
  }

  return {
    ...resolved,
    canonical,
    og: resolvedOpenGraph(resolved, pathname),
  };
}

export function SEOProvider({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  const { i18n } = useTranslation();

  const [seoConfig, setSeoConfig] = useState<SEOConfig>(() =>
    getRouteConfig(location.pathname, i18n.language)
  );

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSeoConfig(getRouteConfig(location.pathname, i18n.language));
  }, [location.pathname, i18n.language]);

  useEffect(() => {
    if (location.pathname !== '/docs' && !location.pathname.startsWith('/docs/')) return;

    const syncRenderedDocumentMetadata = () => {
      const heading = document.querySelector<HTMLElement>('article h1')?.textContent?.trim();
      if (!heading) return;
      const description = document.querySelector<HTMLElement>('article p')?.textContent?.trim();
      const canonical = getPublicCanonicalUrl(window.location.href);
      const title = `${heading} · zenstory 文档 | ZenStory AI`;
      const resolvedDescription = description?.slice(0, 180);

      setSeoConfig((previous) => ({
        ...previous,
        title,
        description: resolvedDescription ?? previous.description,
        canonical,
        og: {
          type: 'article',
          title,
          description: resolvedDescription ?? previous.description,
          image: previous.og?.image ?? 'https://zenstory.ai/brand/zenstory-ai-mark.svg',
        },
        schema: {
          '@context': 'https://schema.org',
          '@type': 'TechArticle',
          headline: heading,
          url: canonical,
          publisher: {
            '@type': 'Organization',
            name: 'ZenStory AI',
            url: 'https://zenstory.ai',
          },
        },
      }));
    };

    const observer = new MutationObserver(syncRenderedDocumentMetadata);
    observer.observe(document.body, { childList: true, subtree: true });
    const timer = window.setTimeout(syncRenderedDocumentMetadata, 0);

    return () => {
      window.clearTimeout(timer);
      observer.disconnect();
    };
  }, [location.pathname]);

  const updateSEO = useCallback((updates: Partial<SEOConfig>) => {
    setSeoConfig(prev => ({ ...prev, ...updates }));
  }, []);

  return (
    <SEOContext.Provider value={{ seoConfig, updateSEO }}>
      {children}
    </SEOContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useSEO() {
  const context = useContext(SEOContext);
  if (!context) {
    throw new Error('useSEO must be used within SEOProvider');
  }
  return context;
}
