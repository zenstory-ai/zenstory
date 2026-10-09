/**
 * @fileoverview LoadingSpinner component - Universal loading indicators for the application.
 *
 * This module provides loading spinner components for displaying loading states
 * throughout the application. It supports multiple variants, sizes, and colors
 * to fit different UI contexts.
 *
 * Features:
 * - Two spinner variants: icon-based (lucide-react) and CSS-based animation
 * - Five size options: xs, sm, md, lg, xl
 * - Three color themes: primary, white, secondary
 * - Optional text labels with horizontal/vertical layout
 * - Page-level and inline loader convenience components
 *
 * @module components/LoadingSpinner
 */
import { useTranslation } from 'react-i18next';
import { Loader2 } from 'lucide-react';

/**
 * Props for the LoadingSpinner component.
 *
 * @interface LoadingSpinnerProps
 */
export interface LoadingSpinnerProps {
  /**
   * Size of the spinner.
   * - 'xs': 12px (extra small)
   * - 'sm': 16px (small)
   * - 'md': 20px (medium, default)
   * - 'lg': 32px (large)
   * - 'xl': 48px (extra large)
   */
  size?: 'xs' | 'sm' | 'md' | 'lg' | 'xl';

  /**
   * Visual variant of the spinner.
   * - 'icon': Uses lucide-react Loader2 icon with rotation animation
   * - 'css': Uses CSS border animation (better for white backgrounds)
   */
  variant?: 'icon' | 'css';

  /**
   * Optional text label to display alongside the spinner.
   * When provided, the spinner and label are wrapped in a flex container.
   */
  label?: string;

  /**
   * Whether to use vertical layout when a label is present.
   * - true: Label appears below the spinner
   * - false: Label appears to the right of the spinner (default)
   * @default false
   */
  vertical?: boolean;

  /**
   * Additional CSS classes to apply to the container element.
   */
  className?: string;

  /**
   * Color theme of the spinner.
   * - 'primary': Uses accent color (default)
   * - 'white': White color for dark backgrounds
   * - 'secondary': Uses secondary text color
   */
  color?: 'primary' | 'white' | 'secondary';
}

/**
 * Props for the PageLoader component.
 *
 * @interface PageLoaderProps
 */
export interface PageLoaderProps {
  /**
   * Optional text label to display below the spinner.
   * Defaults to "加载中..." (Loading...) if not provided.
   */
  label?: string;
}

/**
 * Props for the InlineLoader component.
 *
 * @interface InlineLoaderProps
 */
export interface InlineLoaderProps {
  /**
   * Optional text label to display next to the spinner.
   * Defaults to "加载中..." (Loading...) if not provided.
   */
  label?: string;
}

/**
 * Universal loading spinner component.
 *
 * Renders a rotating spinner indicator with optional text label.
 * Supports multiple visual variants and sizes for different use cases:
 * - Icon variant: Uses lucide-react's Loader2 icon with CSS rotation
 * - CSS variant: Uses border-based spinner (better visibility on colored backgrounds)
 *
 * The component automatically handles layout based on whether a label is provided:
 * - No label: Renders just the spinner in a span
 * - With label: Renders spinner + label in a flex container
 *
 * @param props - Component props
 * @param props.size - Spinner size ('xs' | 'sm' | 'md' | 'lg' | 'xl')
 * @param props.variant - Visual style ('icon' | 'css')
 * @param props.label - Optional text label
 * @param props.vertical - Use vertical layout with label
 * @param props.className - Additional container classes
 * @param props.color - Color theme ('primary' | 'white' | 'secondary')
 * @returns The rendered loading spinner component
 *
 * @example
 * // Basic usage - medium icon spinner
 * <LoadingSpinner />
 *
 * @example
 * // Small white CSS spinner in a button
 * <LoadingSpinner size="sm" variant="css" color="white" label="Loading..." />
 *
 * @example
 * // Large page loader with vertical label
 * <LoadingSpinner
 *   size="lg"
 *   variant="icon"
 *   color="primary"
 *   label="Loading content..."
 *   vertical
 * />
 */
export function LoadingSpinner({
  size = 'md',
  variant = 'icon',
  label,
  vertical = false,
  className = '',
  color = 'primary',
}: LoadingSpinnerProps) {
  // Size class mapping for CSS spinner variant
  const sizeClasses = {
    xs: 'w-3 h-3',
    sm: 'w-4 h-4',
    md: 'w-5 h-5',
    lg: 'w-8 h-8',
    xl: 'w-12 h-12',
  };

  // Icon size mapping for lucide-react Loader2
  const iconSizes = {
    xs: 12,
    sm: 16,
    md: 20,
    lg: 32,
    xl: 48,
  };

  // Color class mapping for icon variant
  const colorClasses = {
    primary: 'text-[hsl(var(--accent-primary))]',
    white: 'text-white',
    secondary: 'text-[hsl(var(--text-secondary))]',
  };

  // Color class mapping for CSS variant (border colors)
  const cssSpinnerColor = {
    primary: 'border-[hsl(var(--border-color))] border-t-[hsl(var(--accent-primary))]',
    white: 'border-white/30 border-t-white',
    secondary: 'border-[hsl(var(--border-color))] border-t-[hsl(var(--text-secondary))]',
  };

  /**
   * Renders the icon-based spinner using lucide-react's Loader2.
   * @returns The icon spinner element
   */
  const renderIconSpinner = () => (
    <Loader2
      size={iconSizes[size]}
      className={`animate-spin ${colorClasses[color]}`}
    />
  );

  /**
   * Renders the CSS-based spinner using border animation.
   * Better visibility on colored/white backgrounds.
   * @returns The CSS spinner element
   */
  const renderCssSpinner = () => (
    <div
      className={`${sizeClasses[size]} border-2 ${cssSpinnerColor[color]} rounded-full animate-spin`}
    />
  );

  const spinner = variant === 'icon' ? renderIconSpinner() : renderCssSpinner();

  // Render spinner only if no label provided
  if (!label) {
    return <span className={className}>{spinner}</span>;
  }

  // Render spinner with label in flex container
  const containerClassName = vertical
    ? 'flex flex-col items-center justify-center gap-4'
    : 'flex items-center justify-center gap-2';

  return (
    <span className={`${containerClassName} ${className}`}>
      {spinner}
      <span className="text-sm">{label}</span>
    </span>
  );
}

/**
 * Page-level loading component with full-screen centered layout.
 *
 * Renders a large spinner centered on the page with optional label.
 * Use this for initial page loads or major content transitions.
 *
 * @param props - Component props
 * @param props.label - Optional text label (defaults to "加载中...")
 * @returns A full-page centered loading indicator
 *
 * @example
 * // Basic page loader
 * <PageLoader />
 *
 * @example
 * // With custom label
 * <PageLoader label="Loading your workspace..." />
 */
export function PageLoader({ label }: PageLoaderProps) {
  const { t } = useTranslation();
  return (
    <div className="min-h-screen bg-[hsl(var(--bg-primary))] flex items-center justify-center">
      <LoadingSpinner size="lg" label={label || t('common:loading', '加载中...')} vertical />
    </div>
  );
}

/**
 * Inline loading component for embedding in forms, cards, etc.
 *
 * Renders a small spinner with horizontal padding, suitable for
 * inline loading states within content areas.
 *
 * @param props - Component props
 * @param props.label - Optional text label (defaults to "加载中...")
 * @returns An inline loading indicator with padding
 *
 * @example
 * // In a card content area
 * <InlineLoader />
 *
 * @example
 * // With custom label
 * <InlineLoader label="Fetching data..." />
 */
export function InlineLoader({ label }: InlineLoaderProps) {
  const { t } = useTranslation();
  return (
    <div className="flex items-center gap-2 py-8">
      <LoadingSpinner size="sm" label={label || t('common:loading', '加载中...')} />
    </div>
  );
}
