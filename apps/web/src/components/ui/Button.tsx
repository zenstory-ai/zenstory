/**
 * @fileoverview Button component - A versatile button with variants, sizes, and loading states.
 *
 * This module provides a reusable button component that supports multiple visual variants,
 * sizes, loading states, and icon placements. It follows the project's design system
 * using CSS custom properties for theming.
 *
 * Features:
 * - Five variants: primary, secondary, ghost, danger, outline
 * - Four sizes: sm (32px), md (40px), touch (44px), lg (48px)
 * - Loading state with spinner
 * - Left and right icon support
 * - Full HTML button attribute support
 *
 * @module components/ui/Button
 */
import React from 'react';
import { LoadingSpinner } from '../LoadingSpinner';

/**
 * Props for the Button component.
 *
 * @interface ButtonProps
 */
export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  /**
   * Visual variant of the button.
   * - 'primary': Accent color background with white text
   * - 'secondary': Subtle background with border
   * - 'ghost': No background, text only
   * - 'danger': Error color background for destructive actions
   * @default 'primary'
   */
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger' | 'outline';

  /**
   * Size of the button.
   * - 'sm': Small (32px min-height) for dense rows inside cards
   * - 'md': Medium (40px min-height) — page-header and dialog actions on desktop
   * - 'touch': Same type as md at the 44px touch target (phones)
   * - 'lg': Large (48px min-height)
   * @default 'md'
   */
  size?: 'sm' | 'md' | 'touch' | 'lg';

  /**
   * Whether the button is in a loading state.
   * When true, displays a spinner and disables interactions.
   * @default false
   */
  isLoading?: boolean;

  /**
   * Text to display during loading state.
   * If not provided, shows children text.
   */
  loadingText?: string;

  /**
   * Icon to display on the left side of the button text.
   */
  leftIcon?: React.ReactNode;

  /**
   * Icon to display on the right side of the button text.
   */
  rightIcon?: React.ReactNode;
}

/**
 * Get the CSS classes for a given button variant.
 *
 * @param variant - The button variant
 * @returns CSS class string for the variant
 */
const getVariantClasses = (variant: ButtonProps['variant']): string => {
  const variants = {
    primary:
      'border-transparent bg-[hsl(var(--accent-primary))] text-white hover:opacity-90 active:scale-[0.98] focus-visible:ring-[hsl(var(--accent-light))] focus-visible:ring-offset-[hsl(var(--bg-secondary))]',
    secondary:
      'bg-[hsl(var(--bg-secondary))] text-[hsl(var(--text-primary))] border-[hsl(var(--border-color))] hover:bg-[hsl(var(--bg-tertiary))] active:scale-[0.98] focus-visible:ring-[hsl(var(--accent-primary)/0.5)] focus-visible:ring-offset-[hsl(var(--bg-secondary))]',
    ghost:
      'border-transparent text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))] active:scale-[0.98] focus-visible:ring-[hsl(var(--accent-primary)/0.5)] focus-visible:ring-offset-[hsl(var(--bg-secondary))]',
    danger:
      'border-transparent bg-[hsl(var(--error))] text-white hover:opacity-90 active:scale-[0.98] focus-visible:ring-[hsl(var(--error)/0.5)] focus-visible:ring-offset-[hsl(var(--bg-secondary))]',
    outline:
      'bg-transparent text-[hsl(var(--accent-primary))] border-[hsl(var(--accent-primary))] hover:bg-[hsl(var(--accent-primary)/0.1)] active:scale-[0.98] focus-visible:ring-[hsl(var(--accent-primary))] focus-visible:ring-offset-[hsl(var(--bg-secondary))]',
  };
  return variants[variant || 'primary'];
};

/**
 * Get the CSS classes for a given button size.
 *
 * @param size - The button size
 * @returns CSS class string for the size
 */
const getSizeClasses = (size: ButtonProps['size']): string => {
  const sizes = {
    sm: 'px-3 py-1.5 text-sm min-h-[32px]',
    md: 'px-4 py-2 text-sm min-h-[40px]',
    touch: 'px-4 py-2 text-sm min-h-[44px]',
    lg: 'px-6 py-3 text-base min-h-[48px]',
  };
  return sizes[size || 'md'];
};

/**
 * Get the spinner color based on button variant.
 *
 * @param variant - The button variant
 * @returns Color theme for the loading spinner
 */
const getSpinnerColor = (variant: ButtonProps['variant']): 'white' | 'primary' => {
  return variant === 'primary' || variant === 'danger' ? 'white' : 'primary';
};

/**
 * A versatile button component with multiple variants, sizes, and states.
 *
 * Supports all standard HTML button attributes and extends them with:
 * - Visual variants for different contexts (primary, secondary, ghost, danger)
 * - Consistent sizing across the application
 * - Loading state with integrated spinner
 * - Icon placement on left or right side
 *
 * The button automatically handles:
 * - Disabled state styling (opacity and cursor)
 * - Focus visible ring for accessibility
 * - Scale animation on active state
 *
 * @param props - Component props
 * @param props.variant - Visual style variant
 * @param props.size - Button size
 * @param props.isLoading - Loading state
 * @param props.loadingText - Text to show during loading
 * @param props.leftIcon - Icon element for left side
 * @param props.rightIcon - Icon element for right side
 * @param props.children - Button content
 * @param props.className - Additional CSS classes
 * @param props.disabled - Disabled state
 * @returns The rendered button component
 *
 * @example
 * // Primary button (default)
 * <Button onClick={handleClick}>Click me</Button>
 *
 * @example
 * // Secondary button with left icon
 * <Button variant="secondary" leftIcon={<Plus size={16} />}>
 *   Add Item
 * </Button>
 *
 * @example
 * // Loading state
 * <Button isLoading loadingText="Saving...">
 *   Save
 * </Button>
 *
 * @example
 * // Danger button with confirmation
 * <Button variant="danger" size="sm" onClick={handleDelete}>
 *   Delete
 * </Button>
 */
export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(function Button({
  variant = 'primary',
  size = 'md',
  isLoading = false,
  loadingText,
  leftIcon,
  rightIcon,
  children,
  className = '',
  disabled,
  ...props
}, ref) {
  // `border` on every variant: a bordered secondary next to a primary keeps one height.
  const baseClasses =
    'inline-flex items-center justify-center gap-2 border font-medium rounded-lg transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed';

  const variantClasses = getVariantClasses(variant);
  const sizeClasses = getSizeClasses(size);

  const isDisabled = disabled || isLoading;

  return (
    <button
      ref={ref}
      className={`${baseClasses} ${variantClasses} ${sizeClasses} ${className}`}
      disabled={isDisabled}
      {...props}
    >
      {isLoading ? (
        <>
          <LoadingSpinner size="sm" color={getSpinnerColor(variant)} />
          <span>{loadingText || children}</span>
        </>
      ) : (
        <>
          {leftIcon && <span className="flex-shrink-0">{leftIcon}</span>}
          <span>{children}</span>
          {rightIcon && <span className="flex-shrink-0">{rightIcon}</span>}
        </>
      )}
    </button>
  );
});

export default Button;
