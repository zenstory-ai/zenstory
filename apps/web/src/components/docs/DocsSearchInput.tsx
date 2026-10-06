import { useState, useRef, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Search, X } from 'lucide-react';
import { useDocsSearch, type DocsSearchResult } from '../../hooks/useDocsSearch';

interface DocsSearchInputProps {
  onResultClick?: () => void;
}

export function DocsSearchInput({ onResultClick }: DocsSearchInputProps) {
  const { t, i18n } = useTranslation('docs');
  const navigate = useNavigate();
  const [query, setQuery] = useState('');
  const [showResults, setShowResults] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const { results, isSearching, clearSearch } = useDocsSearch({ query });

  // Handle Cmd+K / Ctrl+K shortcut
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        inputRef.current?.focus();
      }
      if (e.key === 'Escape') {
        clearSearch();
        setQuery('');
        inputRef.current?.blur();
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [clearSearch]);

  // Click outside to close results
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setShowResults(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const handleResultClick = (result: DocsSearchResult) => {
    navigate(result.path);
    setQuery('');
    clearSearch();
    setShowResults(false);
    onResultClick?.();
  };

  const getTitle = (result: DocsSearchResult) => {
    return i18n.language === 'zh' ? result.titleZh : result.title;
  };

  const getParentTitle = (result: DocsSearchResult) => {
    if (!result.parentTitle) return null;
    return i18n.language === 'zh' ? result.parentTitleZh : result.parentTitle;
  };

  return (
    <div ref={containerRef} className="relative">
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[hsl(var(--text-tertiary))]" />
        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setShowResults(true);
          }}
          onFocus={() => setShowResults(true)}
          placeholder={t('searchPlaceholder', '搜索文档...')}
          className="w-full min-h-[44px] sm:h-9 pl-9 pr-8 text-sm bg-[hsl(var(--bg-tertiary))]
                     border border-[hsl(var(--border-color))] rounded-lg
                     text-[hsl(var(--text-primary))] placeholder:text-[hsl(var(--text-tertiary))]
                     focus:outline-none focus:ring-2 focus:ring-[hsl(var(--accent-primary)/0.5)]
                     transition-shadow"
        />
        {query && (
          <button
            onClick={() => {
              setQuery('');
              clearSearch();
            }}
            className="absolute right-2 top-1/2 -translate-y-1/2 p-1
                       text-[hsl(var(--text-tertiary))] hover:text-[hsl(var(--text-primary))]
                       rounded hover:bg-[hsl(var(--bg-secondary))] transition-colors"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        )}
      </div>

      {/* Search Results Dropdown */}
      {showResults && query && (
        <div className="absolute top-full left-0 right-0 mt-1
                        bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--border-color))]
                        rounded-lg shadow-lg max-h-80 overflow-y-auto z-50">
          {isSearching ? (
            <div className="px-4 py-3 text-sm text-[hsl(var(--text-tertiary))]">
              {t('loading', '正在加载...')}
            </div>
          ) : results.length > 0 ? (
            <ul className="py-1">
              {results.map((result) => (
                <li key={result.path}>
                  <button
                    onClick={() => handleResultClick(result)}
                    className="w-full px-3 py-2 text-left hover:bg-[hsl(var(--bg-tertiary))]
                               transition-colors focus:outline-none focus:bg-[hsl(var(--bg-tertiary))]"
                  >
                    <div className="text-sm text-[hsl(var(--text-primary))]">
                      {getTitle(result)}
                    </div>
                    {getParentTitle(result) && (
                      <div className="text-xs text-[hsl(var(--text-tertiary))] mt-0.5">
                        {getParentTitle(result)}
                      </div>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <div className="px-4 py-3 text-sm text-[hsl(var(--text-tertiary))]">
              {t('noResults', '没有找到相关文档')}
            </div>
          )}
        </div>
      )}

      {/* Keyboard shortcut hint */}
      <div className="absolute right-2 top-1/2 -translate-y-1/2 pointer-events-none">
        {!query && (
          <kbd className="hidden sm:inline-flex items-center px-1.5 py-0.5
                          text-xs text-[hsl(var(--text-tertiary))]
                          bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--border-color))]
                          rounded">
            ⌘K
          </kbd>
        )}
      </div>
    </div>
  );
}
