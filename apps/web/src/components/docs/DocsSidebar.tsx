/**
 * DocsSidebar component - Navigation sidebar for documentation pages.
 *
 * @module components/docs/DocsSidebar
 */

import React from 'react';
import { Link, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ChevronRight, X } from 'lucide-react';
import { docsNavigation, type DocNavItem } from '../../data/docsNavigation';
import { DocsSearchInput } from './DocsSearchInput';

interface DocsSidebarProps {
  isOpen?: boolean;
  onClose?: () => void;
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
        className={`flex items-center gap-1.5 py-1.5 px-3 rounded-lg text-sm transition-colors ${
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
export const DocsSidebar: React.FC<DocsSidebarProps> = ({ isOpen = true, onClose }) => {
  const location = useLocation();
  const { i18n, t } = useTranslation('docs');
  const currentPath = location.pathname;
  const showMobileDrawer = Boolean(isOpen);
  const closeMobile = () => onClose?.();

  return (
    <>
      <aside className="hidden lg:flex lg:flex-col lg:sticky lg:top-12 h-[calc(100vh-3rem)] w-72 shrink-0 border-r border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))]/95 backdrop-blur-sm">
        <div className="px-4 pt-4 pb-3 border-b border-[hsl(var(--border-color))] space-y-3">
          <div className="text-sm font-semibold text-[hsl(var(--text-primary))]">
            {t('documentation', '文档中心')}
          </div>
          <DocsSearchInput />
        </div>
        <nav className="p-4 space-y-1 overflow-y-auto flex-1">
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

      {showMobileDrawer && (
        <div className="lg:hidden fixed inset-x-0 top-12 bottom-0 z-40">
          <button
            type="button"
            className="absolute inset-0 bg-black/40"
            onClick={closeMobile}
            aria-label={t('menu', '目录')}
          />
          <aside className="absolute left-0 top-0 h-full w-72 border-r border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] shadow-xl">
            <div className="h-12 px-3 flex items-center justify-between border-b border-[hsl(var(--border-color))]">
              <span className="text-sm font-semibold text-[hsl(var(--text-primary))]">
                {t('documentation', '文档中心')}
              </span>
              <button
                type="button"
                onClick={closeMobile}
                className="p-1.5 rounded-md text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--bg-hover))] hover:text-[hsl(var(--text-primary))]"
                aria-label={t('closeMenu', '关闭目录')}
              >
                <X size={18} />
              </button>
            </div>
            <div className="px-3 pt-3 pb-2 border-b border-[hsl(var(--border-color))]">
              <DocsSearchInput onResultClick={closeMobile} />
            </div>
            <nav className="p-4 space-y-1 overflow-y-auto max-h-[calc(100vh-7.5rem)]">
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
        </div>
      )}
    </>
  );
};

export default DocsSidebar;
