import { useIsMobile, useIsTablet } from "../../hooks/useMediaQuery";

/**
 * Props for the DashboardPageHeader component
 */
export interface DashboardPageHeaderProps {
  /** The main title text for the page */
  title: string;
  /** Optional subtitle text displayed below the title */
  subtitle?: string;
  /** Optional action element (typically a button) displayed on the right side */
  action?: React.ReactNode;
  /** Optional badge showing a count with optional label */
  badge?: { count: number; label?: string };
  /** Optional additional CSS classes */
  className?: string;
}

/**
 * Unified page header component for dashboard pages
 *
 * Provides consistent styling and layout for all dashboard sub-pages:
 * - Responsive title sizing (text-xl mobile, text-2xl tablet+)
 * - Consistent margin-bottom pattern (mb-4/6/8 for mobile/tablet/desktop)
 * - Optional badge with accent color styling
 * - Optional action buttons aligned to the right
 *
 * @example
 * ```tsx
 * // Basic usage
 * <DashboardPageHeader
 *   title={t('billing.title')}
 *   subtitle={t('billing.subtitle')}
 * />
 *
 * // With action button
 * <DashboardPageHeader
 *   title={t('billing.title')}
 *   subtitle={t('billing.subtitle')}
 *   action={
 *     <button className="btn-primary flex items-center gap-2">
 *       <Plus className="w-4 h-4" />
 *       <span>{t('projects.new')}</span>
 *     </button>
 *   }
 * />
 *
 * // With badge
 * <DashboardPageHeader
 *   title={t('projects.all')}
 *   badge={{ count: 5 }}
 * />
 * ```
 */
export function DashboardPageHeader({
  title,
  subtitle,
  action,
  badge,
  className,
}: DashboardPageHeaderProps) {
  const isMobile = useIsMobile();
  const isTablet = useIsTablet();

  return (
    <div
      className={`${
        isMobile ? "mb-4" : isTablet ? "mb-6" : "mb-8"
      } ${className || ""}`}
    >
      <div className={`flex ${isMobile ? "flex-col items-start gap-3" : "items-center justify-between"}`}>
        <div className="flex items-center gap-3 min-w-0">
          <div>
            <div className="flex items-center gap-2">
              <h1
                className={`font-bold text-[hsl(var(--text-primary))] ${
                  isMobile ? "text-xl" : "text-2xl"
                }`}
              >
                {title}
              </h1>
              {badge && (
                <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-[hsl(var(--accent-primary)/0.1)] text-[hsl(var(--accent-primary))]">
                  {badge.count}
                </span>
              )}
            </div>
            {subtitle && (
              <p className="text-[hsl(var(--text-secondary))] text-sm mt-1">
                {subtitle}
              </p>
            )}
          </div>
        </div>
        <div className={`flex items-center gap-2 ${isMobile ? "w-full flex-wrap" : "shrink-0"}`}>
          {action}
        </div>
      </div>
    </div>
  );
}
