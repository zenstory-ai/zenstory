/* eslint-disable react-refresh/only-export-components */
import { useState, useEffect, useMemo } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Menu, FileText } from 'lucide-react';
import { LazyMarkdown } from '../components/LazyMarkdown';
import { PublicHeader } from '../components/PublicHeader';
import { DocsSidebar } from '../components/docs/DocsSidebar';
import { docsNavigation, flattenDocs, type DocNavItem } from '../data/docsNavigation';
import { logger } from "../lib/logger";

import { normalizeDocsHref } from '../lib/docs-links.mjs';
export { isExternalHref, getDocsDirectory, normalizeDocsHref } from '../lib/docs-links.mjs';

const DOCS_ROOT = '../../docs';

// Markdown 文件映射 - 使用 Vite 的 import.meta.glob 动态导入
const docsModules = import.meta.glob('../../docs/**/*.md', { query: '?raw', import: 'default', eager: false });

/**
 * 将文件路径转换为 docs 目录下的路径
 * 例如: '/docs/getting-started/quick-start' -> 'getting-started/quick-start'
 */
export function extractDocPath(urlPath: string): string {
  const match = urlPath.match(/^\/docs\/(.+)$/);
  const rawPath = match ? match[1] : '';
  return rawPath.replace(/\.md$/i, '');
}

/**
 * 根据文档路径和语言获取对应的 markdown 文件内容
 * @param docPath 文档路径（不含 /docs 前缀和 .md 后缀）
 * @param language 当前语言（'zh' 或 'en'）
 * @returns Markdown 内容，如果英文文档不存在则 fallback 到中文
 */
export async function loadMarkdownContent(docPath: string, language: string): Promise<string | null> {
  const isEnglish = language === 'en';

  // 如果是英文，尝试加载英文文档
  if (isEnglish) {
    // 如果是文档首页，尝试加载 en/README.md
    if (!docPath || docPath === '') {
      const enReadmePath = `${DOCS_ROOT}/en/README.md`;
      if (docsModules[enReadmePath]) {
        const content = await docsModules[enReadmePath]() as string;
        return content;
      }
      // Fallback to Chinese README
      const zhReadmePath = `${DOCS_ROOT}/README.md`;
      if (docsModules[zhReadmePath]) {
        const content = await docsModules[zhReadmePath]() as string;
        return content;
      }
      return null;
    }

    // 尝试加载英文文档
    const enMdPath = `${DOCS_ROOT}/en/${docPath}.md`;
    if (docsModules[enMdPath]) {
      const content = await docsModules[enMdPath]() as string;
      return content;
    }

    // Fallback to Chinese document
    const zhMdPath = `${DOCS_ROOT}/${docPath}.md`;
    if (docsModules[zhMdPath]) {
      const content = await docsModules[zhMdPath]() as string;
      return content;
    }

    return null;
  }

  // 中文：加载中文文档
  // 如果是文档首页，加载 README.md
  if (!docPath || docPath === '') {
    const readmePath = `${DOCS_ROOT}/README.md`;
    if (docsModules[readmePath]) {
      const content = await docsModules[readmePath]() as string;
      return content;
    }
    return null;
  }

  // 尝试加载对应的 markdown 文件
  const mdPath = `${DOCS_ROOT}/${docPath}.md`;
  if (docsModules[mdPath]) {
    const content = await docsModules[mdPath]() as string;
    return content;
  }

  return null;
}

/**
 * 根据路径查找文档项
 */
export function findDocItem(path: string): DocNavItem | undefined {
  const flatDocs = flattenDocs(docsNavigation);
  return flatDocs.find(item => item.path === path);
}

export function resolveCategoryToFirstDoc(path: string): string | null {
  const item = findDocItem(path);
  if (!item?.children || item.children.length === 0) {
    return null;
  }
  return item.children[0].path;
}

/**
 * Markdown 渲染组件的自定义样式
 */
export function MarkdownComponents(currentPathname: string) {
  return {
    h1: ({ children }: { children?: React.ReactNode }) => (
      <h1 className="text-3xl md:text-4xl font-bold text-[hsl(var(--text-primary))] mb-6 md:mb-8 pb-4 border-b-2 border-[hsl(var(--border-color))] leading-tight">
        {children}
      </h1>
    ),
    h2: ({ children }: { children?: React.ReactNode }) => (
      <h2 className="text-2xl md:text-3xl font-bold text-[hsl(var(--text-primary))] mt-10 md:mt-12 mb-4 md:mb-5 leading-tight">
        {children}
      </h2>
    ),
    h3: ({ children }: { children?: React.ReactNode }) => (
      <h3 className="text-xl md:text-2xl font-semibold text-[hsl(var(--text-primary))] mt-8 md:mt-10 mb-3 md:mb-4 leading-tight">
        {children}
      </h3>
    ),
    h4: ({ children }: { children?: React.ReactNode }) => (
      <h4 className="text-lg md:text-xl font-semibold text-[hsl(var(--text-primary))] mt-6 md:mt-8 mb-3 leading-tight">
        {children}
      </h4>
    ),
    p: ({ children }: { children?: React.ReactNode }) => (
      <p className="text-[15px] md:text-base text-[hsl(var(--text-primary))] leading-relaxed mb-4 md:mb-5">
        {children}
      </p>
    ),
    ul: ({ children }: { children?: React.ReactNode }) => (
      <ul className="list-disc list-inside text-[hsl(var(--text-primary))] space-y-2 md:space-y-2.5 mb-5 pl-4 md:pl-6">
        {children}
      </ul>
    ),
    ol: ({ children }: { children?: React.ReactNode }) => (
      <ol className="list-decimal list-inside text-[hsl(var(--text-primary))] space-y-2 md:space-y-2.5 mb-5 pl-4 md:pl-6">
        {children}
      </ol>
    ),
    li: ({ children }: { children?: React.ReactNode }) => (
      <li className="text-[15px] md:text-base text-[hsl(var(--text-primary))] leading-relaxed pl-1">
        {children}
      </li>
    ),
    a: ({ href, children }: { href?: string; children?: React.ReactNode }) => {
      const internalHref = normalizeDocsHref(href ?? '', currentPathname);
      if (internalHref) {
        return (
          <Link
            to={internalHref}
            className="text-[hsl(var(--accent-primary))] hover:underline underline-offset-2 decoration-2 font-medium transition-colors"
          >
            {children}
          </Link>
        );
      }

      return (
        <a
          href={href}
          className="text-[hsl(var(--accent-primary))] hover:underline underline-offset-2 decoration-2 font-medium transition-colors"
          target={href?.startsWith('http') ? '_blank' : undefined}
          rel={href?.startsWith('http') ? 'noopener noreferrer' : undefined}
        >
          {children}
        </a>
      );
    },
    blockquote: ({ children }: { children?: React.ReactNode }) => (
      <blockquote className="border-l-4 border-[hsl(var(--accent-primary))] pl-4 md:pl-6 py-3 md:py-4 my-6 md:my-8 bg-[hsl(var(--bg-secondary))] rounded-r-lg text-[hsl(var(--text-secondary))] italic">
        {children}
      </blockquote>
    ),
    code: ({ className, children, inline }: { className?: string; children?: React.ReactNode; inline?: boolean }) => {
      if (inline) {
        return (
          <code className="bg-[hsl(var(--bg-secondary))] text-[hsl(var(--accent-primary))] px-2 py-0.5 md:py-1 rounded-md text-sm font-mono border border-[hsl(var(--border-color))]">
            {children}
          </code>
        );
      }
      return (
        <code className={className}>
          {children}
        </code>
      );
    },
    pre: ({ children }: { children?: React.ReactNode }) => (
      <pre className="bg-[hsl(var(--bg-secondary))] text-[hsl(var(--text-primary))] p-4 md:p-6 rounded-xl overflow-x-auto my-6 md:my-8 font-mono text-sm border border-[hsl(var(--border-color))]">
        {children}
      </pre>
    ),
    table: ({ children }: { children?: React.ReactNode }) => (
      <div className="overflow-x-auto my-6 md:my-8">
        <table className="min-w-full border border-[hsl(var(--border-color))] rounded-xl overflow-hidden">
          {children}
        </table>
      </div>
    ),
    thead: ({ children }: { children?: React.ReactNode }) => (
      <thead className="bg-[hsl(var(--bg-secondary))]">{children}</thead>
    ),
    th: ({ children }: { children?: React.ReactNode }) => (
      <th className="px-4 md:px-6 py-3 md:py-4 text-left text-sm font-semibold text-[hsl(var(--text-primary))] border-b border-[hsl(var(--border-color))]">
        {children}
      </th>
    ),
    td: ({ children }: { children?: React.ReactNode }) => (
      <td className="px-4 md:px-6 py-3 md:py-4 text-sm text-[hsl(var(--text-primary))] border-b border-[hsl(var(--border-color))]">
        {children}
      </td>
    ),
    hr: () => (
      <hr className="my-10 md:my-12 border-t-2 border-[hsl(var(--border-color))]" />
    ),
    img: ({ src, alt }: { src?: string; alt?: string }) => (
      <img src={src} alt={alt} className="max-w-full h-auto rounded-xl my-6 md:my-8 border border-[hsl(var(--border-color))] shadow-lg" />
    ),
  };
}

export function DocsPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const { t, i18n } = useTranslation('docs');
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [content, setContent] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // 获取完整的文档路径
  const fullDocPath = extractDocPath(location.pathname);

  const redirectTarget = useMemo(
    () => resolveCategoryToFirstDoc(location.pathname),
    [location.pathname]
  );

  // 加载 Markdown 内容
  useEffect(() => {
    let cancelled = false;

    async function loadContent() {
      if (redirectTarget && redirectTarget !== location.pathname) {
        navigate(redirectTarget, { replace: true });
        return;
      }

      setLoading(true);
      setError(null);

      try {
        const mdContent = await loadMarkdownContent(fullDocPath, i18n.language);

        if (!cancelled) {
          if (mdContent) {
            setContent(mdContent);
          } else {
            setError(t('notFound', 'Document Not Found'));
            setContent(null);
          }
        }
      } catch (err) {
        if (!cancelled) {
          logger.error('Failed to load markdown:', err);
          setError(t('loadError', 'Failed to Load Document'));
          setContent(null);
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    loadContent();

    return () => {
      cancelled = true;
    };
  }, [fullDocPath, i18n.language, t, location.pathname, navigate, redirectTarget]);

  // 页面加载时滚动到顶部
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [fullDocPath]);

  const markdownComponents = MarkdownComponents(location.pathname);

  return (
    <div className="min-h-screen bg-[hsl(var(--bg-primary))] flex flex-col">
      <PublicHeader variant="home" maxWidth="max-w-6xl" />

      <div className="flex-1 w-full">
        <div className="mx-auto flex h-full w-full max-w-6xl">
          {/* Sidebar */}
          <DocsSidebar isOpen={sidebarOpen} onClose={() => setSidebarOpen(false)} />

          {/* Main content */}
          <main className="flex-1 min-w-0">
            {/* Mobile menu button */}
            <div className="lg:hidden sticky top-12 z-30 bg-[hsl(var(--bg-secondary))] border-b border-[hsl(var(--border-color))] px-4 py-2">
              <button
                onClick={() => setSidebarOpen(true)}
                className="flex items-center gap-2 text-sm text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] transition-colors"
              >
                <Menu className="w-5 h-5" />
                <span>{t('menu', 'Menu')}</span>
              </button>
            </div>

            {/* Content area */}
            <div className="max-w-5xl mx-auto w-full px-4 md:px-6 lg:px-8 py-8 md:py-10 lg:py-12">
              <div className="rounded-2xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))]/70 backdrop-blur-sm p-5 md:p-8 lg:p-10">
                {loading ? (
                  <div className="flex flex-col items-center justify-center py-20 md:py-28">
                    <div className="relative w-16 h-16 md:w-20 md:h-20">
                      <div className="absolute inset-0 rounded-full border-2 border-[hsl(var(--accent-primary)/0.2)]" />
                      <div className="absolute inset-0 rounded-full border-2 border-transparent border-t-[hsl(var(--accent-primary))] animate-spin" />
                      <div className="absolute inset-2 rounded-full bg-[hsl(var(--accent-primary)/0.05)] blur-sm" />
                    </div>
                    <p className="mt-4 text-sm text-[hsl(var(--text-secondary))]">{t('loading', 'Loading...')}</p>
                  </div>
                ) : error ? (
                  <div className="text-center py-20 md:py-28 animate-fade-in">
                    <div className="inline-block p-4 md:p-5 rounded-2xl bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--border-color))] mb-6">
                      <FileText className="w-16 h-16 md:w-20 md:h-20 text-[hsl(var(--text-secondary))] opacity-50" />
                    </div>
                    <h2 className="text-2xl md:text-3xl font-bold text-[hsl(var(--text-primary))] mb-3">
                      {error}
                    </h2>
                    <p className="text-sm md:text-base text-[hsl(var(--text-secondary))] mb-6 max-w-md mx-auto leading-relaxed">
                      {t('notFoundDesc', 'The document you are looking for does not exist or has been removed.')}
                    </p>
                    <Link
                      to="/docs"
                      className="group relative h-11 md:h-12 px-6 md:px-8 text-[14px] md:text-[15px] font-bold inline-flex items-center gap-2 bg-[hsl(var(--accent-primary))] text-white rounded-xl overflow-hidden transition-all hover:shadow-[0_0_30px_rgba(74,158,255,0.4)] hover:scale-[1.02]"
                    >
                      <span className="relative z-10">{t('backToHome', 'Back to Documentation Home')}</span>
                      <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/20 to-transparent -translate-x-full group-hover:translate-x-full transition-transform duration-700" />
                    </Link>
                  </div>
                ) : content ? (
                  <article className="prose prose-lg max-w-none animate-fade-in">
                    <LazyMarkdown components={markdownComponents}>
                      {content}
                    </LazyMarkdown>
                  </article>
                ) : null}
              </div>
            </div>
          </main>
        </div>
      </div>
    </div>
  );
}

export default DocsPage;
