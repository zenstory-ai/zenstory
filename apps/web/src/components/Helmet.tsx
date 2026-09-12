/**
 * @fileoverview SEO Helmet component - Manages document head metadata for SEO.
 *
 * This module provides the SEOHelmet component that integrates with react-helmet-async
 * to manage document head metadata. It automatically generates SEO-friendly meta tags,
 * Open Graph tags, structured data (JSON-LD), and hreflang links for internationalization.
 *
 * Features:
 * - Automatic title and meta description management
 * - Open Graph tag generation for social sharing
 * - JSON-LD structured data for search engines
 * - hreflang tags for multi-language SEO (Chinese/English)
 * - Canonical URL support
 * - Robots meta tag control (noindex for private pages)
 * - i18n language detection and html lang attribute
 *
 * @module components/Helmet
 * @see {@link https://github.com/nfl/react-helmet} react-helmet-async
 */

import { Helmet } from 'react-helmet-async';
import { useSEO } from '../providers/SEOProvider';
import { useTranslation } from 'react-i18next';
import { getBaseUrl } from '../lib/utils';

/**
 * Props for the SEOHelmet component.
 *
 * This component currently has no props as it retrieves all SEO configuration
 * from the SEOProvider context. The interface is defined for future extensibility
 * and to maintain consistent component patterns across the codebase.
 *
 * @interface SEOHelmetProps
 */
// eslint-disable-next-line @typescript-eslint/no-empty-object-type
export interface SEOHelmetProps {
  // Currently no props - all configuration comes from SEOProvider context
  // Future props could include:
  // - overrideTitle?: string - Override context title
  // - noindex?: boolean - Override context noindex setting
}

/**
 * SEO helmet component for managing document head metadata.
 *
 * This component automatically generates all necessary SEO meta tags based on
 * the current SEO configuration from SEOProvider. It handles:
 *
 * - **Basic SEO**: Title, description, keywords meta tags
 * - **Canonical URLs**: Prevents duplicate content issues
 * - **Robots control**: noindex directive for private pages
 * - **Internationalization**: hreflang tags for zh-CN and en-US
 * - **Social sharing**: Open Graph tags for better link previews
 * - **Structured data**: JSON-LD schema for rich search results
 *
 * The component uses the SEOProvider context for configuration, so it must
 * be rendered within an SEOProvider wrapper.
 *
 * @param _props - Component props (currently unused, all config from context)
 * @returns The Helmet component with all SEO meta tags
 *
 * @example
 * // Basic usage (within SEOProvider)
 * import { SEOProvider } from '../providers/SEOProvider';
 * import { SEOHelmet } from './Helmet';
 *
 * function App() {
 *   return (
 *     <SEOProvider>
 *       <SEOHelmet />
 *       <main>Page content</main>
 *     </SEOProvider>
 *   );
 * }
 *
 * @example
 * // SEO configuration is set via SEOProvider
 * <SEOProvider config={{
 *   title: "My Page",
 *   description: "Page description",
 *   keywords: ["novel", "writing"],
 *   og: { type: "website", image: "/og-image.png" }
 * }}>
 *   <SEOHelmet />
 * </SEOProvider>
 *
 * @see {@link SEOConfig} for available configuration options
 */
export function SEOHelmet(_props: SEOHelmetProps = {}) {
  const { seoConfig } = useSEO();
  const { i18n } = useTranslation();
  const lang = i18n.language;
  const langKey = lang.startsWith('zh') ? 'zh-CN' : 'en-US';

  // 只为公开页面生成hreflang
  const isPublicPage = !seoConfig.noindex;

  // 构建完整URL用于hreflang
  const baseUrl = getBaseUrl();
  const pathname = window.location.pathname;

  return (
    <Helmet htmlAttributes={{ lang: langKey }}>
      <title>{seoConfig.title}</title>
      <meta name="description" content={seoConfig.description} />
      {seoConfig.keywords && (
        <meta name="keywords" content={seoConfig.keywords.join(', ')} />
      )}
      {seoConfig.canonical && <link rel="canonical" href={seoConfig.canonical} />}
      {seoConfig.canonical && <meta property="og:url" content={seoConfig.canonical} />}
      {seoConfig.noindex && <meta name="robots" content="noindex" />}

      {/* hreflang标签 - 仅公开页面 */}
      {isPublicPage && <link rel="alternate" hrefLang="zh-CN" href={`${baseUrl}${pathname}`} />}
      {isPublicPage && <link rel="alternate" hrefLang="en-US" href={`${baseUrl}${pathname}`} />}
      {isPublicPage && <link rel="alternate" hrefLang="x-default" href={`${baseUrl}${pathname}`} />}

      {/* Open Graph 标签 */}
      {seoConfig.og && <meta property="og:site_name" content="ZenStory AI" />}
      {seoConfig.og?.type && <meta property="og:type" content={seoConfig.og.type} />}
      {seoConfig.og?.title && <meta property="og:title" content={seoConfig.og.title} />}
      {seoConfig.og?.description && <meta property="og:description" content={seoConfig.og.description} />}
      {seoConfig.og?.image && <meta property="og:image" content={seoConfig.og.image} />}

      {/* JSON-LD Schema */}
      {seoConfig.schema && (
        <script type="application/ld+json">
          {JSON.stringify(seoConfig.schema)}
        </script>
      )}
    </Helmet>
  );
}
