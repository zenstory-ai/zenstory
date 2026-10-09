/**
 * @fileoverview Badge component - A compact label with variants, sizes, and status indicators.
 *
 * This module provides a reusable badge component that supports multiple visual variants,
 * sizes, icon placement, and a dot status indicator. It follows the project's design system
 * using Tailwind CSS for consistent styling.
 *
 * Features:
 * - Seven variants: neutral, success, warning, error, info, purple, cyan
 * - Two sizes: sm, md
 * - Optional left icon support
 * - Optional dot status indicator
 * - Dark mode support
 *
 * @module components/ui/Badge
 */
import React from 'react';
import { cn } from '@/lib/utils';

/**
 * Props for the Badge component.
 *
 * @interface BadgeProps
 */
export interface BadgeProps {
  /**
   * Visual variant of the badge.
   * - 'neutral': Gray background for neutral/default states
   * - 'success': Emerald/green for success states
   * - 'warning': Orange for warning states
   * - 'error': Red for error states
   * - 'info': Blue for informational states
   * - 'purple': Purple for special states
   * - 'cyan': Cyan for alternative states
   * @default 'neutral'
   */
  variant?: 'neutral' | 'success' | 'warning' | 'error' | 'info' | 'purple' | 'cyan';

  /**
   * Size of the badge.
   * - 'sm': Small with less padding
   * - 'md': Medium with standard padding
   * @default 'sm'
   */
  size?: 'sm' | 'md';

  /**
   * Icon to display on the left side of the badge text.
   */
  icon?: React.ReactNode;

  /**
   * Whether to show a small dot indicator on the left side.
   * The dot uses the current text color.
   * @default false
   */
  dot?: boolean;

  /**
   * The content to display inside the badge.
   */
  children: React.ReactNode;

  /**
   * Additional CSS classes to apply to the badge.
   */
  className?: string;
}

/**
 * Get the CSS classes for a given badge variant.
 *
 * @param variant - The badge variant
 * @returns CSS class string for the variant
 */
const getVariantClasses = (variant: BadgeProps['variant']): string => {
  const variants = {
    neutral: 'bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-secondary))]',
    success: 'bg-[hsl(var(--success)/0.15)] text-[hsl(var(--success-text))]',
    warning: 'bg-[hsl(var(--warning)/0.15)] text-[hsl(var(--warning-text))]',
    error: 'bg-[hsl(var(--error)/0.15)] text-[hsl(var(--error-text))]',
    info: 'bg-[hsl(var(--info)/0.15)] text-[hsl(var(--info))]',
    purple: 'bg-[hsl(var(--purple)/0.15)] text-[hsl(var(--purple-light))]',
    cyan: 'bg-[hsl(var(--cyan)/0.15)] text-[hsl(var(--cyan-light))]',
  };
  return variants[variant || 'neutral'];
};

/**
 * Get the CSS classes for a given badge size.
 *
 * @param size - The badge size
 * @returns CSS class string for the size
 */
const getSizeClasses = (size: BadgeProps['size']): string => {
  const sizes = {
    sm: 'px-1.5 py-0.5 text-xs',
    md: 'px-2 py-0.5 text-xs',
  };
  return sizes[size || 'sm'];
};

/**
 * A compact badge component for labels, tags, and status indicators.
 *
 * Supports multiple visual variants for different contexts and states.
 * Can display an optional icon or dot indicator alongside the text content.
 *
 * The badge features:
 * - Inline-flex layout for easy integration with text and other elements
 * - Consistent sizing and spacing across the application
 * - Dark mode support with appropriate color variations
 * - Flexible content via children prop
 *
 * @param props - Component props
 * @param props.variant - Visual style variant
 * @param props.size - Badge size
 * @param props.icon - Icon element for left side
 * @param props.dot - Whether to show dot indicator
 * @param props.children - Badge content
 * @param props.className - Additional CSS classes
 * @returns The rendered badge component
 *
 * @example
 * // Neutral badge (default)
 * <Badge>Draft</Badge>
 *
 * @example
 * // Success badge with icon
 * <Badge variant="success" icon={<Check size={12} />}>
 *   Completed
 * </Badge>
 *
 * @example
 * // Warning badge with dot indicator
 * <Badge variant="warning" dot>
 *   Pending Review
 * </Badge>
 *
 * @example
 * // Error badge, medium size
 * <Badge variant="error" size="md">
 *   Failed
 * </Badge>
 *
 * @example
 * // Purple badge with custom class
 * <Badge variant="purple" className="font-semibold">
 *   Premium
 * </Badge>
 */
export const Badge: React.FC<BadgeProps> = ({
  variant = 'neutral',
  size = 'sm',
  icon,
  dot = false,
  children,
  className,
}) => {
  const baseClasses = 'inline-flex items-center gap-1 rounded';

  const variantClasses = getVariantClasses(variant);
  const sizeClasses = getSizeClasses(size);

  return (
    <span
      className={cn(
        baseClasses,
        variantClasses,
        sizeClasses,
        className
      )}
    >
      {dot && (
        <span className="w-1.5 h-1.5 rounded-full bg-current flex-shrink-0" />
      )}
      {icon && <span className="flex-shrink-0">{icon}</span>}
      <span>{children}</span>
    </span>
  );
};

export default Badge;
