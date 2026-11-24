import { useRef } from "react";
import type React from "react";
import { useTranslation } from "react-i18next";
import { Search, X } from "../icons";

/**
 * Props for the DashboardSearchBar component
 */
export interface DashboardSearchBarProps {
  /** The current value of the search input */
  value: string;
  /** Callback fired when the search value changes */
  onChange: (value: string) => void;
  /** Placeholder text for the search input */
  placeholder: string;
  /** Optional additional CSS classes */
  className?: string;
  /** Optional IME composition start handler */
  onCompositionStart?: React.CompositionEventHandler<HTMLInputElement>;
  /** Optional IME composition end handler */
  onCompositionEnd?: React.CompositionEventHandler<HTMLInputElement>;
  /** Optional data-testid applied to the search input */
  inputTestId?: string;
}

/**
 * Unified search bar component for dashboard pages
 *
 * Provides consistent styling and behavior across all dashboard pages:
 * - Consistent height: h-10
 * - Border radius: rounded-xl
 * - Icon positioning: left-4 top-1/2 -translate-y-1/2
 * - Input padding: pl-10 pr-4
 * - Focus state: focus:border-[hsl(var(--accent-primary)/0.5)]
 *
 * @example
 * ```tsx
 * // Basic usage
 * <DashboardSearchBar
 *   value={searchQuery}
 *   onChange={setSearchQuery}
 *   placeholder={t('projects.searchPlaceholder')}
 * />
 *
 * // With custom className
 * <DashboardSearchBar
 *   value={searchQuery}
 *   onChange={setSearchQuery}
 *   placeholder="Search..."
 *   className="max-w-md"
 * />
 *
 * // In a flex container
 * <div className="flex items-center gap-4">
 *   <DashboardSearchBar
 *     value={searchQuery}
 *     onChange={setSearchQuery}
 *     placeholder={t('search')}
 *     className="flex-1"
 *   />
 * </div>
 * ```
 */
export function DashboardSearchBar({
  value,
  onChange,
  placeholder,
  className,
  onCompositionStart,
  onCompositionEnd,
  inputTestId,
}: DashboardSearchBarProps) {
  const { t } = useTranslation("common");
  const inputRef = useRef<HTMLInputElement>(null);

  const handleClear = () => {
    onChange("");
    inputRef.current?.focus();
  };

  return (
    <div className={`relative ${className || ""}`}>
      <Search
        aria-hidden="true"
        className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[hsl(var(--text-secondary))]"
      />
      <input
        ref={inputRef}
        type="text"
        role="searchbox"
        aria-label={placeholder}
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape" && value && !e.nativeEvent.isComposing && e.nativeEvent.keyCode !== 229) {
            e.preventDefault();
            onChange("");
          }
        }}
        onCompositionStart={onCompositionStart}
        onCompositionEnd={onCompositionEnd}
        data-testid={inputTestId}
        className={`w-full bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--border-color))] rounded-xl min-h-[44px] pl-9 text-sm text-[hsl(var(--text-primary))] placeholder:text-[hsl(var(--text-secondary))] focus:outline-none focus:border-[hsl(var(--accent-primary)/0.5)] focus:ring-2 focus:ring-[hsl(var(--accent-primary)/0.12)] ${value ? "pr-11" : "pr-4"}`}
      />
      {value && (
        <button
          type="button"
          onClick={handleClear}
          aria-label={t("common:clearSearch")}
          title={t("common:clearSearch")}
          className="absolute right-0 top-1/2 -translate-y-1/2 min-h-[44px] min-w-[44px] flex items-center justify-center rounded-xl text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.5)]"
        >
          <X className="w-4 h-4" aria-hidden="true" />
        </button>
      )}
    </div>
  );
}
