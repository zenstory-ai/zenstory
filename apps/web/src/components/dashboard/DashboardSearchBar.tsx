import type React from "react";
import { Search } from "../icons";

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
  return (
    <div className={`relative ${className || ""}`}>
      <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[hsl(var(--text-secondary))]" />
      <input
        type="search"
        aria-label={placeholder}
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onCompositionStart={onCompositionStart}
        onCompositionEnd={onCompositionEnd}
        data-testid={inputTestId}
        className="w-full bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--border-color))] rounded-xl min-h-[44px] pl-9 pr-4 text-sm text-[hsl(var(--text-primary))] placeholder:text-[hsl(var(--text-secondary))] focus:outline-none focus:border-[hsl(var(--accent-primary)/0.5)] focus:ring-2 focus:ring-[hsl(var(--accent-primary)/0.12)]"
      />
    </div>
  );
}
