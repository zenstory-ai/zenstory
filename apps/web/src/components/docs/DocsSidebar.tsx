/**
 * DocsSidebar component - Navigation sidebar for documentation pages.
 *
 * @module components/docs/DocsSidebar
 */

import React, { useRef, type RefObject } from 'react';
import { createPortal } from 'react-dom';
import { Link, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ChevronRight, X } from 'lucide-react';
import { docsNavigation, type DocNavItem } from '../../data/docsNavigation';
import { DocsSearchInput } from './DocsSearchInput';
import { useDialogInteractions } from '../ui/dialogFocus';
import { useIsDesktop } from '../../hooks/useMediaQuery';

interface DocsSidebarProps {
  isOpen?: boolean;
  onClose?: () => void;
  returnFocusRef?: RefObject<HTMLElement | null>;
}

/**
 * Render a navigation item and its children.
 */
function NavItem({
  item,
  currentPath,
  language,
  depth = 0,
  onNavigate,
}: {
  item: DocNavItem;
  currentPath: string;
  language: string;
  depth?: number;
  onNavigate?: () => void;
}) {
  const hasChildren = item.children && item.children.length > 0;
  const hasActiveChild = Boolean(
    item.children?.some(child => currentPath === child.path || currentPath.startsWith(`${child.path}/`))
  );
  const isActive = currentPath === item.path || hasActiveChild;
  const targetPath = hasChildren && item.children?.[0]?.path ? item.children[0].path : item.path;
  const title = language === 'en' ? item.title : item.titleZh;

  return (
    <div>
      <Link
        to={targetPath}
        onClick={onNavigate}
        aria-current={!hasChildren && isActive ? 'page' : undefined}
        className={`flex min-h-11 lg:min-h-9 items-center gap-1.5 py-1.5 px-3 rounded-lg text-sm transition-colors ${
          depth > 0 ? 'pl-6' : ''
        } ${
          isActive
            ? 'bg-[hsl(var(--accent-primary)/0.15)] text-[hsl(var(--accent-primary))] font-medium'
            : 'text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--bg-hover))] hover:text-[hsl(var(--text-primary))]'
        }`}
      >
        {hasChildren && !isActive && (
          <ChevronRight size={14} className="shrink-0 opacity-50" />
        )}
        <span className={hasChildren && !isActive ? '' : 'ml-5'}>{title}</span>
      </Link>

      {/* Render children if parent is active or a child is active */}
      {hasChildren && (isActive || hasActiveChild) && (
        <div className="mt-1 space-y-0.5">
          {item.children!.map((child) => (
            <NavItem
              key={child.path}
              item={child}
              currentPath={currentPath}
              language={language}
              depth={depth + 1}
              onNavigate={onNavigate}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * Documentation sidebar with navigation links.
 */
export const DocsSidebar: React.FC<DocsSidebarProps> = ({ isOpen = true, onClose, returnFocusRef }) => {
  const location = useLocation();
  const { i18n, t } = useTranslation('docs');
  const currentPath = location.pathname;
  const isDesktop = useIsDesktop();
  const showMobileDrawer = Boolean(isOpen) && !isDesktop;
  const drawerRef = useRef<HTMLElement>(null);
  const closeMobile = () => onClose?.();
  useDialogInteractions({
    open: showMobileDrawer,
    dialogRef: drawerRef,
    onEscape: closeMobile,
    returnFocusRef,
  });

  return (
    <>
      <aside className="hidden lg:flex lg:flex-col lg:sticky lg:top-12 h-[calc(100vh-3rem)] w-72 shrink-0 border-r border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))]/95 backdrop-blur-sm">
        <div className="px-4 pt-4 pb-3 border-b border-[hsl(var(--border-color))] space-y-3">
          <div className="text-sm font-semibold text-[hsl(var(--text-primary))]">
            {t('documentation', '文档中心')}
          </div>
          <DocsSearchInput active={isDesktop} />
        </div>
        <nav aria-label={t('documentation', '文档中心')} className="p-4 space-y-1 overflow-y-auto flex-1">
          {docsNavigation.map((item) => (
            <NavItem
              key={item.path}
              item={item}
              currentPath={currentPath}
              language={i18n.language}
            />
          ))}
        </nav>
      </aside>

      {showMobileDrawer && createPortal(
        <div className="viewport-overlay lg:hidden fixed inset-0 z-50">
          <div
            className="absolute inset-0 bg-black/40"
            onClick={closeMobile}
            aria-hidden="true"
          />
          <aside
            ref={drawerRef}
            id="docs-mobile-navigation"
            role="dialog"
            aria-modal="true"
            aria-label={t('documentation', '文档中心')}
            tabIndex={-1}
            className="absolute left-0 top-0 flex h-full w-80 max-w-[calc(100%-2rem)] flex-col border-r border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] shadow-xl"
          >
            <div className="min-h-14 shrink-0 px-3 flex items-center justify-between border-b border-[hsl(var(--border-color))]">
              <span className="text-sm font-semibold text-[hsl(var(--text-primary))]">
                {t('documentation', '文档中心')}
              </span>
              <button
                type="button"
                onClick={closeMobile}
                className="inline-flex h-11 w-11 items-center justify-center rounded-lg text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--bg-hover))] hover:text-[hsl(var(--text-primary))]"
                aria-label={t('closeMenu', '关闭目录')}
              >
                <X size={18} />
              </button>
            </div>
            <div className="shrink-0 px-3 pt-3 pb-2 border-b border-[hsl(var(--border-color))]">
              <DocsSearchInput onResultClick={closeMobile} />
            </div>
            <nav aria-label={t('documentation', '文档中心')} className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-4 space-y-1">
              {docsNavigation.map((item) => (
                <NavItem
                  key={item.path}
                  item={item}
                  currentPath={currentPath}
                  language={i18n.language}
                  onNavigate={closeMobile}
                />
              ))}
            </nav>
          </aside>
        </div>, document.body
      )}
    </>
  );
};

export default DocsSidebar;
