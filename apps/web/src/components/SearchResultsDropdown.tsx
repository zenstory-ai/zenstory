/**
 * @fileoverview SearchResultsDropdown component - Dropdown for displaying file search results.
 *
 * This component provides a keyboard-navigable dropdown for search results with:
 * - File type icons matching the FileTree component
 * - Keyboard selection highlighting
 * - Mouse hover support for selection
 * - Loading and empty states
 * - Click-outside-to-close functionality
 * - Responsive sizing for mobile devices
 *
 * Used within the FileSearchContext to display filtered files.
 * Works with the useFileSearch hook for keyboard navigation integration.
 *
 * @module components/SearchResultsDropdown
 */
import React, { useMemo, useRef, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Folder,
  FileText,
  Users,
  BookOpen,
  Sparkles,
  FolderOpen,
} from 'lucide-react';
import type { FileSearchResult } from '../hooks/useFileSearch';

/**
 * Props for the SearchResultsDropdown component.
 *
 * @interface SearchResultsDropdownProps
 */
interface SearchResultsDropdownProps {
  /**
   * Array of search results to display.
   * Each result contains id, title, fileType, and optional parentPath.
   */
  results: FileSearchResult[];
  /**
   * Index of the currently selected item (for keyboard navigation).
   * The selected item is highlighted with accent color.
   */
  selectedIndex: number;
  /**
   * Callback fired when a result is selected (clicked or Enter key).
   * @param result - The selected FileSearchResult object
   */
  onSelect: (result: FileSearchResult) => void;
  /**
   * Callback fired when mouse hovers over a result item.
   * Used to sync selection state with keyboard navigation.
   * @param index - The index of the hovered item
   */
  onHover: (index: number) => void;
  /**
   * Whether the dropdown is visible.
   * When false, the component renders nothing.
   */
  visible: boolean;
  /**
   * Whether search is in progress.
   * Shows a loading indicator instead of results.
   * @default false
   */
  loading?: boolean;
  /**
   * Callback fired when the dropdown should close.
   * Triggered by clicking outside the dropdown.
   */
  onClose?: () => void;
}

/**
 * Dropdown component for displaying file search results with keyboard navigation.
 *
 * Renders a styled dropdown with:
 * - File type icons (folder, draft, character, lore, outline, snippet)
 * - Selected item highlighting for keyboard navigation
 * - Mouse hover interaction for selection
 * - Loading state with spinner text
 * - Empty state with "no results" message
 * - Click-outside-to-close behavior
 *
 * This component is controlled - the parent manages search state and selection.
 * Use with the FileSearchInput and useFileSearch hook for integrated search.
 *
 * Features:
 * - **Keyboard navigation**: ArrowUp/Down to navigate, Enter to select
 * - **Mouse support**: Hover highlights, click to select
 * - **Accessibility**: Uses role="listbox" and role="option" for screen readers
 * - **Responsive**: Max 50vh on mobile, 280px on desktop (approximately 8 items)
 * - **Click outside**: Automatically closes when clicking outside the dropdown
 *
 * @param props - The component props
 * @returns The dropdown JSX element, or null if not visible
 *
 * @example
 * // Basic usage with search results
 * const [results, setResults] = useState<FileSearchResult[]>([]);
 * const [selectedIndex, setSelectedIndex] = useState(0);
 *
 * <SearchResultsDropdown
 *   results={results}
 *   selectedIndex={selectedIndex}
 *   onSelect={(result) => {
 *     navigateToFile(result.id);
 *     setDropdownVisible(false);
 *   }}
 *   onHover={setSelectedIndex}
 *   visible={showDropdown}
 *   loading={isSearching}
 *   onClose={() => setDropdownVisible(false)}
 * />
 *
 * @example
 * // With useFileSearch hook integration
 * const { results, selectedIndex, handleKeyDown } = useFileSearch(query);
 *
 * <FileSearchInput
 *   value={query}
 *   onChange={setQuery}
 *   onKeyDown={handleKeyDown}
 * />
 * <SearchResultsDropdown
 *   results={results}
 *   selectedIndex={selectedIndex}
 *   onSelect={handleSelect}
 *   onHover={(i) => setSelectedIndex(i)}
 *   visible={query.length > 0}
 * />
 */
const SearchResultsDropdown: React.FC<SearchResultsDropdownProps> = ({
  results,
  selectedIndex,
  onSelect,
  onHover,
  visible,
  loading = false,
  onClose,
}) => {
  const { t } = useTranslation(['editor', 'common']);
  const dropdownRef = useRef<HTMLDivElement>(null);

  /**
   * Effect: Handle click-outside-to-close behavior.
   * Registers a mousedown listener when visible is true,
   * triggers onClose callback when click is outside the dropdown.
   */
  useEffect(() => {
    /**
     * Handles mousedown events to detect clicks outside the dropdown.
     * @param event - The mouse event from document
     */
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        onClose?.();
      }
    };

    if (visible) {
      document.addEventListener("mousedown", handleClickOutside);
    }

    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, [visible, onClose]);

  /**
   * Icon mapping for file types - matches the FileTree component icons.
   * Maps each fileType to its corresponding Lucide icon component.
   */
  const FILE_TYPE_ICONS = useMemo(
    (): Record<string, React.ReactNode> => ({
      folder: <Folder size={16} />,
      lore: <Sparkles size={16} />,
      character: <Users size={16} />,
      outline: <FileText size={16} />,
      snippet: <FolderOpen size={16} />,
      draft: <BookOpen size={16} />,
    }),
    []
  );

  // Don't render if not visible
  if (!visible) {
    return null;
  }

  // Render loading state with spinner text
  if (loading) {
    return (
      <div ref={dropdownRef} data-testid="search-results-dropdown" className="absolute top-full left-0 right-0 mt-1 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--border-primary))] rounded-md shadow-lg overflow-hidden z-50 max-h-[calc(var(--app-viewport-height)*0.5)] sm:max-h-[min(280px,calc(var(--app-viewport-height)*0.5))] overflow-y-auto">
        <div role="status" data-testid="search-results-loading" className="px-3 py-3 sm:py-2 text-sm text-[hsl(var(--text-secondary))]">
          {t('common:loading')}
        </div>
      </div>
    );
  }

  // Render empty state when no results found
  if (results.length === 0) {
    return (
      <div ref={dropdownRef} data-testid="search-results-dropdown" className="absolute top-full left-0 right-0 mt-1 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--border-primary))] rounded-md shadow-lg overflow-hidden z-50 max-h-[calc(var(--app-viewport-height)*0.5)] sm:max-h-[min(280px,calc(var(--app-viewport-height)*0.5))] overflow-y-auto">
        <div role="status" data-testid="search-results-empty" className="px-3 py-3 sm:py-2 text-sm text-[hsl(var(--text-secondary))]">
          {t('editor:fileTree.noSearchResults')}
        </div>
      </div>
    );
  }

  return (
    <div ref={dropdownRef} data-testid="search-results-dropdown" className="absolute top-full left-0 right-0 mt-1 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--border-primary))] rounded-md shadow-lg overflow-hidden z-50 max-h-[calc(var(--app-viewport-height)*0.5)] sm:max-h-[min(280px,calc(var(--app-viewport-height)*0.5))] overflow-y-auto">
      {/* Results list with max height for 8 items */}
      <div className="max-h-[calc(var(--app-viewport-height)*0.5)] sm:max-h-[min(280px,calc(var(--app-viewport-height)*0.5))] overflow-y-auto" role="listbox">
        {results.map((result, index) => {
          const isSelected = index === selectedIndex;

          return (
            <div
              key={result.id}
              data-testid={`search-result-item-${index}`}
              aria-selected={isSelected ? "true" : "false"}
              role="option"
              className={`flex items-center gap-2 px-3 py-3 sm:py-2 cursor-pointer transition-colors touch-manipulation ${
                isSelected
                  ? 'bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--accent-primary))]'
                  : 'text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))]'
              }`}
              onClick={() => onSelect(result)}
              onMouseEnter={() => onHover(index)}
            >
              {/* File type icon */}
              <span className="shrink-0">
                {FILE_TYPE_ICONS[result.fileType] || <FileText size={16} />}
              </span>

              {/* Content */}
              <div className="flex-1 min-w-0">
                {/* Title */}
                <div className="text-sm truncate">{result.title}</div>

                {/* Parent path */}
                {result.parentPath && (
                  <div className="text-xs text-[hsl(var(--text-secondary))] truncate">
                    {result.parentPath}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default SearchResultsDropdown;
