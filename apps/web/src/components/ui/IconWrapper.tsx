import { cn } from '@/lib/utils';
import React from 'react';

/**
 * Size mapping for icon wrapper dimensions.
 * Maps size keys to Tailwind CSS height and width classes.
 */
const sizeMap = {
  xs: 'h-4 w-4', // 16px
  sm: 'h-5 w-5', // 20px
  md: 'h-6 w-6', // 24px
  lg: 'h-7 w-7', // 28px
  xl: 'h-8 w-8', // 32px
  '2xl': 'h-9 w-9', // 36px
  '3xl': 'h-10 w-10', // 40px
  '4xl': 'h-12 w-12', // 48px
} as const;

/**
 * Variant styles mapping for icon wrapper appearance.
 * Each variant defines background and text color styles.
 */
const variantStyles = {
  default: '',
  primary: 'bg-[hsl(var(--accent-primary)/0.15)] text-[hsl(var(--accent-primary))]',
  success: 'bg-[hsl(var(--success)/0.15)] text-[hsl(var(--success-text))]',
  warning: 'bg-[hsl(var(--warning)/0.15)] text-[hsl(var(--warning-text))]',
  error: 'bg-[hsl(var(--error)/0.15)] text-[hsl(var(--error-text))]',
  purple: 'bg-[hsl(var(--accent-primary)/0.15)] text-[hsl(var(--accent-primary))]',
  gray: 'bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-secondary))]',
} as const;

/**
 * Rounded styles mapping for icon wrapper border radius.
 */
const roundedStyles = {
  md: 'rounded-lg',
  full: 'rounded-full',
} as const;

/**
 * Props for the IconWrapper component.
 */
export interface IconWrapperProps {
  /**
   * Size of the icon wrapper.
   * @default 'md'
   */
  size?: 'xs' | 'sm' | 'md' | 'lg' | 'xl' | '2xl' | '3xl' | '4xl';
  /**
   * Visual variant style of the icon wrapper.
   * @default 'default'
   */
  variant?: 'default' | 'primary' | 'success' | 'warning' | 'error' | 'purple' | 'gray';
  /**
   * Border radius style.
   * @default 'md'
   */
  rounded?: 'md' | 'full';
  /**
   * Whether to show the background color.
   * When false, only the icon color is applied.
   * @default true
   */
  background?: boolean;
  /**
   * The icon element to wrap.
   */
  children: React.ReactNode;
  /**
   * Additional CSS classes to apply.
   */
  className?: string;
}

/**
 * A flexible wrapper component for icons that provides consistent sizing,
 * color variants, and background styling.
 *
 * @example
 * // Basic usage with default styles
 * <IconWrapper>
 *   <PlusIcon />
 * </IconWrapper>
 *
 * @example
 * // Primary variant with large size
 * <IconWrapper size="lg" variant="primary">
 *   <StarIcon />
 * </IconWrapper>
 *
 * @example
 * // Success variant with full rounded corners
 * <IconWrapper variant="success" rounded="full">
 *   <CheckIcon />
 * </IconWrapper>
 *
 * @example
 * // No background, just colored icon
 * <IconWrapper variant="error" background={false}>
 *   <XIcon />
 * </IconWrapper>
 */
export function IconWrapper({
  size = 'md',
  variant = 'default',
  rounded = 'md',
  background = true,
  children,
  className,
}: IconWrapperProps) {
  return (
    <div
      className={cn(
        'flex items-center justify-center',
        sizeMap[size],
        background && variantStyles[variant],
        roundedStyles[rounded],
        className
      )}
    >
      {children}
    </div>
  );
}

export default IconWrapper;
