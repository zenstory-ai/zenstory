import { useIsMobile } from "../../hooks/useMediaQuery";

/**
 * Option type for filter pills
 */
export interface FilterPillOption<T extends string> {
  /** The value of this option */
  value: T;
  /** The display label for this option */
  label: string;
  /** Optional icon to display before the label */
  icon?: React.ComponentType<{ className?: string }>;
}

/**
 * Props for the DashboardFilterPills component
 */
export interface DashboardFilterPillsProps<T extends string> {
  /** Array of filter options to display as pills */
  options: FilterPillOption<T>[];
  /** The currently selected value */
  value: T;
  /** Callback fired when the selection changes */
  onChange: (value: T) => void;
  /** Optional additional CSS classes */
  className?: string;
}

/**
 * Unified filter pills component for dashboard pages
 *
 * Provides consistent styling and behavior for filter buttons across all dashboard pages:
 * - Border radius: rounded-lg (not rounded-full)
 * - Active state: the sidebar's selected state (accent 15% background, accent text).
 *   Solid accent is reserved for the page's primary action, so a selected filter never
 *   competes with it.
 * - Inactive state: bg-[hsl(var(--bg-secondary))] text-[hsl(var(--text-secondary))]
 * - Height: 32px on desktop, 44px touch target on phones
 * - Hover state for inactive: hover:text-[hsl(var(--text-primary))]
 * - Icon size: w-3.5 h-3.5
 * - Padding: px-3 py-1.5
 *
 * @example
 * ```tsx
 * // Basic usage with string values
 * <DashboardFilterPills
 *   options={[
 *     { value: "all", label: t('filterAll') },
 *     { value: "novel", label: t('novel'), icon: Book },
 *     { value: "short", label: t('short'), icon: FileText },
 *   ]}
 *   value={filterType}
 *   onChange={setFilterType}
 * />
 *
 * // With custom className
 * <DashboardFilterPills
 *   options={options}
 *   value={selectedCategory}
 *   onChange={setSelectedCategory}
 *   className="mb-4"
 * />
 *
 * // With TypeScript type safety
 * type FilterValue = "all" | "novel" | "short" | "screenplay";
 * <DashboardFilterPills<FilterValue>
 *   options={filterOptions}
 *   value={filterValue}
 *   onChange={setFilterValue}
 * />
 * ```
 */
export function DashboardFilterPills<T extends string>({
  options,
  value,
  onChange,
  className,
}: DashboardFilterPillsProps<T>) {
  const isMobile = useIsMobile();

  return (
    <div className={`flex max-w-full items-center gap-2 overflow-x-auto pb-1 ${className || ""}`}>
      {options.map((option) => {
        const isActive = value === option.value;
        const Icon = option.icon;

        return (
          <button
            key={option.value}
            type="button"
            onClick={() => onChange(option.value)}
            aria-pressed={isActive}
            className={`
              shrink-0 whitespace-nowrap flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors
              ${isMobile ? "min-h-11" : "min-h-8"}
              ${isActive
                ? "bg-[hsl(var(--accent-primary)/0.15)] text-[hsl(var(--accent-primary))]"
                : "bg-[hsl(var(--bg-secondary))] text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))]"
              }
            `}
          >
            {Icon && <Icon className="w-3.5 h-3.5" />}
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
