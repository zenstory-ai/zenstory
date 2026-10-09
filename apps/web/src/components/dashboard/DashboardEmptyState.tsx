import { useIsMobile } from "../../hooks/useMediaQuery";

/**
 * Props for the DashboardEmptyState component
 */
export interface DashboardEmptyStateProps {
  /** Icon component to display in the empty state */
  icon: React.ElementType;
  /** Title text for the empty state */
  title: string;
  /** Description text displayed below the title */
  description?: string;
  /** Optional action element (typically a button) for the empty state */
  action?: React.ReactNode;
  /** Optional additional CSS classes */
  className?: string;
}

/**
 * Unified empty state component for dashboard pages
 *
 * Provides consistent styling and layout for empty data displays across all dashboard pages:
 * - Responsive icon sizing (w-10 h-10 mobile, w-12 h-12 desktop)
 * - Responsive padding (py-8 px-4 mobile, py-12 desktop)
 * - One text hierarchy at every width: title 16/600 (sans, like other section titles),
 *   description 14/400 secondary — never larger than the page subtitle
 * - Optional action button for user guidance
 *
 * @example
 * ```tsx
 * // Basic usage
 * <DashboardEmptyState
 *   icon={BookOpen}
 *   title={t('materials.noMaterials')}
 *   description={t('materials.emptyDescription')}
 * />
 *
 * // With action button
 * <DashboardEmptyState
 *   icon={BookOpen}
 *   title={t('materials.noMaterials')}
 *   description={t('materials.emptyDescription')}
 *   action={
 *     <button
 *       onClick={handleUpload}
 *       className="text-sm text-[hsl(var(--accent-primary))] hover:underline"
 *     >
 *       {t('materials.uploadFirst')}
 *     </button>
 *   }
 * />
 *
 * // Minimal usage
 * <DashboardEmptyState
 *   icon={FolderOpen}
 *   title={t('projects.noProjects')}
 * />
 * ```
 */
export function DashboardEmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
}: DashboardEmptyStateProps) {
  const isMobile = useIsMobile();

  return (
    <div
      className={`text-center bg-[hsl(var(--bg-secondary))] rounded-2xl border border-[hsl(var(--border-color))] ${
        isMobile ? "py-8 px-4" : "py-12"
      } ${className || ""}`}
    >
      <Icon
        className={`mx-auto mb-3 text-[hsl(var(--text-tertiary))] opacity-50 ${
          isMobile ? "w-10 h-10" : "w-12 h-12"
        }`}
      />
      <h3 className="text-base font-semibold text-[hsl(var(--text-primary))]">
        {title}
      </h3>
      {description && (
        <p className="mx-auto mt-1.5 max-w-md text-sm text-[hsl(var(--text-secondary))]">
          {description}
        </p>
      )}
      {action && <div className="mt-5 flex justify-center">{action}</div>}
    </div>
  );
}
