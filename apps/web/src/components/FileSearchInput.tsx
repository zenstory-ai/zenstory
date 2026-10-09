/**
 * @fileoverview FileSearchInput component - Search input for file search functionality.
 *
 * This component provides a controlled search input with:
 * - Real-time search with controlled value binding
 * - Clear button to reset the search
 * - Full keyboard event support for navigation integration
 * - Responsive sizing (larger on mobile for touch targets)
 * - IME (Input Method Editor) support for Chinese/Japanese input
 *
 * Used within the FileSearchContext to filter files in the current project.
 * Supports fuzzy matching when combined with the useFileSearch hook.
 *
 * @module components/FileSearchInput
 */
import React, { useRef } from "react";
import { Search, X } from "lucide-react";
import { useTranslation } from "react-i18next";

/**
 * Props for the FileSearchInput component.
 *
 * @interface FileSearchInputProps
 */
export interface FileSearchInputProps {
  /**
   * Current search value (controlled input).
   * Should be bound to a state variable in the parent component.
   */
  value: string;
  /**
   * Callback fired when the search value changes.
   * Called on every keystroke for real-time filtering.
   * @param value - The new search input value
   */
  onChange: (value: string) => void;
  /**
   * Callback fired when the clear button is clicked.
   * Should reset the search value to an empty string.
   */
  onClear: () => void;
  /**
   * Callback fired when the input receives focus.
   * Useful for showing search results dropdown.
   */
  onFocus?: () => void;
  /**
   * Callback fired when the input loses focus.
   * Useful for hiding search results dropdown with delay.
   */
  onBlur?: () => void;
  /**
   * Callback fired on keyboard events.
   * Enables keyboard navigation within search results.
   * @param e - The keyboard event from the input element
   */
  onKeyDown?: (e: React.KeyboardEvent<HTMLInputElement>) => void;
  /**
   * Placeholder text shown when input is empty.
   * Defaults to "Search" if not provided (via aria-label).
   */
  placeholder?: string;
  /**
   * Whether the input should auto-focus on mount.
   * Useful when search is triggered via keyboard shortcut (Cmd+K).
   * @default false
   */
  autoFocus?: boolean;
  /**
   * Whether the input is disabled.
   * When disabled, the input cannot be focused or edited.
   * @default false
   */
  disabled?: boolean;
  /**
   * Additional CSS classes to apply to the container.
   * The component has responsive height: h-11 on mobile, h-8 on desktop.
   */
  className?: string;
}

/**
 * Controlled search input component for file search functionality.
 *
 * Renders a styled text input with:
 * - Search icon on the left
 * - Clear button (X) on the right when value is non-empty
 * - Responsive touch-friendly sizing on mobile devices
 *
 * This is a controlled component - the parent manages the search state.
 * Use with the useFileSearch hook or FileSearchContext for integrated search.
 *
 * Features:
 * - **Responsive sizing**: h-11 (44px) on mobile for touch targets, h-8 (32px) on desktop
 * - **Keyboard support**: Pass onKeyDown for ArrowUp/Down navigation, Enter selection
 * - **Accessibility**: Uses role="searchbox" and aria-label for screen readers
 * - **Touch optimization**: Uses touch-manipulation class for responsive tap handling
 *
 * @param props - The component props
 * @returns The search input JSX element
 *
 * @example
 * // Basic usage with local state
 * const [query, setQuery] = useState("");
 *
 * <FileSearchInput
 *   value={query}
 *   onChange={setQuery}
 *   onClear={() => setQuery("")}
 *   placeholder="Search files..."
 * />
 *
 * @example
 * // With keyboard navigation for dropdown
 * <FileSearchInput
 *   value={query}
 *   onChange={setQuery}
 *   onClear={() => setQuery("")}
 *   onFocus={() => setShowDropdown(true)}
 *   onBlur={() => setTimeout(() => setShowDropdown(false), 200)}
 *   onKeyDown={(e) => {
 *     if (e.key === 'ArrowDown') setSelectedIndex(i => i + 1);
 *     if (e.key === 'Enter') handleSelect(selectedItem);
 *   }}
 *   autoFocus // Focus on mount (useful for Cmd+K shortcut)
 * />
 */
export const FileSearchInput: React.FC<FileSearchInputProps> = ({
  value,
  onChange,
  onClear,
  onFocus,
  onBlur,
  onKeyDown,
  placeholder,
  autoFocus = false,
  disabled = false,
  className = "",
}) => {
  const { t } = useTranslation("common");
  const inputRef = useRef<HTMLInputElement>(null);
  const composingRef = useRef(false);

  /**
   * Handles input change events and propagates to parent onChange callback.
   * Always updates the input value for controlled component behavior,
   * supporting IME (Input Method Editor) composition for languages like Chinese/Japanese.
   * @param e - The change event from the input element
   */
  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    // Always update the input value for controlled component behavior
    onChange(e.target.value);
  };

  return (
    <div className={`relative min-h-[44px] sm:h-9 ${className}`}>
      {/* Search icon */}
      <Search
        className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[hsl(var(--text-secondary))] pointer-events-none"
        aria-hidden="true"
      />

      {/* Input field */}
      <input
        ref={inputRef}
        type="text"
        value={value}
        onChange={handleChange}
        onFocus={onFocus}
        onBlur={onBlur}
        onCompositionStart={() => { composingRef.current = true; }}
        onCompositionEnd={() => { composingRef.current = false; }}
        onKeyDown={(event) => {
          if (composingRef.current || event.nativeEvent.isComposing || event.keyCode === 229) {
            // Let the IME confirm its candidate without triggering global navigation.
            event.stopPropagation();
            return;
          }
          onKeyDown?.(event);
        }}
        placeholder={placeholder}
        autoFocus={autoFocus}
        disabled={disabled}
        role="searchbox"
        aria-label={placeholder || "Search"}
        data-testid="file-search-input"
        className="w-full min-h-[44px] sm:h-9 pl-9 pr-8 text-sm rounded-lg border border-[hsl(var(--border-primary))] bg-[hsl(var(--bg-secondary))] text-[hsl(var(--text-primary))] placeholder-[hsl(var(--text-secondary))] focus:outline-none focus:border-[hsl(var(--accent-primary))] disabled:opacity-50 disabled:cursor-not-allowed"
      />

      {/* Clear button - larger touch target on mobile */}
      {value && !disabled && (
        <button
          type="button"
          onClick={onClear}
          data-testid="file-search-clear-button"
          className="absolute right-2 top-1/2 -translate-y-1/2 p-1.5 text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] transition-colors touch-manipulation"
          aria-label={t("common:clearSearch", "清空搜索")}
        >
          <X className="w-4 h-4" />
        </button>
      )}
    </div>
  );
};
