/**
 * LazyMarkdown - A lazy-loaded wrapper for react-markdown with remark-gfm support.
 *
 * This component reduces initial bundle size by dynamically importing
 * react-markdown and remark-gfm only when they are needed.
 *
 * @example
 * ```tsx
 * import { LazyMarkdown } from './LazyMarkdown';
 *
 * function MyComponent() {
 *   return (
 *     <LazyMarkdown className="prose dark:prose-invert">
 *       # Hello World
 *       - List item 1
 *       - List item 2
 *     </LazyMarkdown>
 *   );
 * }
 * ```
 */

import React, { Suspense, useState, useEffect } from 'react';
import type { Components, Options } from 'react-markdown';
import { useTranslation } from 'react-i18next';
import { lazyRoute } from '../lib/chunkRecovery';
import { logger } from "../lib/logger";
import { prepareCjkEmphasis, remarkStripCjkEmphasisMarkers } from "../lib/cjkEmphasis";

type RemarkPlugins = NonNullable<Options['remarkPlugins']>;
type RehypePlugins = NonNullable<Options['rehypePlugins']>;

// Lazy load react-markdown to reduce initial bundle size
const ReactMarkdown = lazyRoute(() => import('react-markdown'), 'react-markdown');

/**
 * Props for the LazyMarkdown component.
 * Extends standard react-markdown props for compatibility.
 */
interface LazyMarkdownProps {
  /** The markdown content to render */
  children: string;
  /** Optional CSS class name to apply to the wrapper */
  className?: string;
  /** Optional custom components for rendering specific markdown elements */
  components?: Components;
  /** Optional additional remark plugins */
  remarkPlugins?: RemarkPlugins;
  /** Optional additional rehype plugins */
  rehypePlugins?: RehypePlugins;
  /** Optional skip HTML option */
  skipHtml?: boolean;
}

/**
 * Loading fallback component displayed while markdown is being loaded.
 */
function MarkdownFallback({ className }: { className?: string }) {
  const { t } = useTranslation();
  return (
    <div className={className}>
      <span className="opacity-50">{t('common:loading')}</span>
    </div>
  );
}

/**
 * LazyMarkdown component that lazy loads react-markdown and remark-gfm.
 *
 * Features:
 * - Reduces initial bundle size by code-splitting markdown dependencies
 * - Supports GitHub Flavored Markdown (GFM) via remark-gfm
 * - Fully compatible with existing react-markdown usage patterns
 * - Shows loading fallback while dependencies load
 *
 * @param props - The component props
 * @returns A lazy-loaded markdown renderer
 */
export function LazyMarkdown({
  children,
  className,
  components,
  remarkPlugins = [],
  rehypePlugins,
  skipHtml,
}: LazyMarkdownProps) {
  // State to hold the dynamically loaded remark-gfm plugin
  const [gfmPlugin, setGfmPlugin] = useState<RemarkPlugins[number] | null>(null);

  // Load remark-gfm plugin on mount
  useEffect(() => {
    import('remark-gfm')
      .then((module) => {
        setGfmPlugin(() => module.default);
      })
      .catch((error) => {
        logger.error('Failed to load remark-gfm:', error);
      });
  }, []);

  // Combine GFM plugin with any additional remark plugins. The CJK marker strip
  // pairs with `prepareCjkEmphasis` below (bold beside 《》“” etc.).
  const allRemarkPlugins: RemarkPlugins = [
    ...(gfmPlugin ? [gfmPlugin] : []),
    ...remarkPlugins,
    remarkStripCjkEmphasisMarkers,
  ];

  return (
    <Suspense fallback={<MarkdownFallback className={className} />}>
      <div className={className}>
        <ReactMarkdown
          remarkPlugins={allRemarkPlugins}
          rehypePlugins={rehypePlugins}
          components={components}
          skipHtml={skipHtml}
        >
          {prepareCjkEmphasis(children)}
        </ReactMarkdown>
      </div>
    </Suspense>
  );
}

export default LazyMarkdown;
