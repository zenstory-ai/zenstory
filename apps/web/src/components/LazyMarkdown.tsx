/**
 * LazyMarkdown - A lazy-loaded wrapper for react-markdown with remark-gfm support.
 *
 * This component reduces initial bundle size by dynamically importing
 * react-markdown and remark-gfm only when they are needed, while caching the
 * remark-gfm plugin at module scope and memoizing plugin arrays so mounted
 * chat bubbles never double-parse on initial render or parent re-renders.
 */

import React, { Suspense, useState, useEffect, useMemo } from 'react';
import type { Components, Options } from 'react-markdown';
import { useTranslation } from 'react-i18next';
import { lazyRoute } from '../lib/chunkRecovery';
import { logger } from "../lib/logger";
import { prepareCjkEmphasis, remarkStripCjkEmphasisMarkers } from "../lib/cjkEmphasis";

type RemarkPlugins = NonNullable<Options['remarkPlugins']>;
type RehypePlugins = NonNullable<Options['rehypePlugins']>;

const EMPTY_REMARK_PLUGINS: RemarkPlugins = [];

let cachedGfmPlugin: RemarkPlugins[number] | null = null;
let gfmLoadPromise: Promise<RemarkPlugins[number] | null> | null = null;

function loadGfmPlugin(): Promise<RemarkPlugins[number] | null> {
  if (cachedGfmPlugin) return Promise.resolve(cachedGfmPlugin);
  if (!gfmLoadPromise) {
    gfmLoadPromise = import('remark-gfm')
      .then((module) => {
        cachedGfmPlugin = module.default;
        return cachedGfmPlugin;
      })
      .catch((error) => {
        gfmLoadPromise = null;
        logger.error('Failed to load remark-gfm:', error);
        return null;
      });
  }
  return gfmLoadPromise;
}

// Lazy load react-markdown in parallel with remark-gfm so the GFM plugin is
// already cached by the time the first ReactMarkdown render commits.
const ReactMarkdown = lazyRoute(
  async () => {
    const [markdownModule] = await Promise.all([
      import('react-markdown'),
      loadGfmPlugin(),
    ]);
    return markdownModule;
  },
  'react-markdown',
);

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
 */
export function LazyMarkdown({
  children,
  className,
  components,
  remarkPlugins = EMPTY_REMARK_PLUGINS,
  rehypePlugins,
  skipHtml,
}: LazyMarkdownProps) {
  const [gfmPlugin, setGfmPlugin] = useState<RemarkPlugins[number] | null>(() => cachedGfmPlugin);

  useEffect(() => {
    if (gfmPlugin) return;
    let cancelled = false;
    void loadGfmPlugin().then((plugin) => {
      if (!cancelled && plugin) {
        setGfmPlugin(() => plugin);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [gfmPlugin]);

  // Combine GFM plugin with any additional remark plugins. The CJK marker strip
  // pairs with `prepareCjkEmphasis` below (bold beside 《》“” etc.).
  const allRemarkPlugins = useMemo<RemarkPlugins>(
    () => [
      ...(gfmPlugin ? [gfmPlugin] : []),
      ...remarkPlugins,
      remarkStripCjkEmphasisMarkers,
    ],
    [gfmPlugin, remarkPlugins],
  );

  const preparedChildren = useMemo(() => prepareCjkEmphasis(children), [children]);

  return (
    <Suspense fallback={<MarkdownFallback className={className} />}>
      <div className={className}>
        <ReactMarkdown
          remarkPlugins={allRemarkPlugins}
          rehypePlugins={rehypePlugins}
          components={components}
          skipHtml={skipHtml}
        >
          {preparedChildren}
        </ReactMarkdown>
      </div>
    </Suspense>
  );
}

export default LazyMarkdown;
