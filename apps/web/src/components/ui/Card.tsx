/**
 * @fileoverview Card component - A versatile container with variants, padding, and loading states.
 *
 * This module provides a reusable card component that supports multiple visual variants,
 * padding options, loading states, and header/footer slots. It follows the project's design
 * system using CSS custom properties for theming.
 *
 * Features:
 * - Three variants: default, elevated, outlined
 * - Five padding options: none, sm, md, lg, responsive
 * - Loading state with skeleton
 * - Header and footer slots
 * - Hoverable effect with shadow transition
 * - Customizable border color and border radius
 *
 * @module components/ui/Card
 */
import React from 'react';
import { cn } from '@/lib/utils';
import { CardSkeleton } from './Skeleton';

/**
 * Props for the Card component.
 *
 * @interface CardProps
 */
export interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  /**
   * Visual variant of the card.
   * - 'default': Basic background color
   * - 'elevated': Background with shadow
   * - 'outlined': Background with border
   * @default 'default'
   */
  variant?: 'default' | 'elevated' | 'outlined';

  /**
   * Padding size of the card.
   * - 'none': No padding
   * - 'sm': Small padding (12px)
   * - 'md': Medium padding (16px)
   * - 'lg': Large padding (24px)
   * - 'responsive': Small on mobile, medium on desktop
   * @default 'md'
   */
  padding?: 'none' | 'sm' | 'md' | 'lg' | 'responsive';

  /**
   * Whether the card has a hover effect.
   * When true, adds shadow on hover with transition.
   * @default false
   */
  hoverable?: boolean;

  /**
   * Whether the card is in a loading state.
   * When true, displays a skeleton placeholder.
   * @default false
   */
  isLoading?: boolean;

  /**
   * Content to display in the card header slot.
   * Rendered above the main children content.
   */
  header?: React.ReactNode;

  /**
   * Content to display in the card footer slot.
   * Rendered below the main children content.
   */
  footer?: React.ReactNode;

  /**
   * Border radius size.
   * - 'lg': Standard rounded corners (8px)
   * - 'xl': Larger rounded corners (12px)
   * - '2xl': Dashboard panel rounded corners (16px)
   * @default 'xl'
   */
  rounded?: 'lg' | 'xl' | '2xl';

  /**
   * Border color variant.
   * - 'default': Standard border color
   * - 'separator': Separator color for visual grouping
   * @default 'default'
   */
  borderColor?: 'default' | 'separator';

  /**
   * The main content of the card.
   */
  children: React.ReactNode;
}

/**
 * Get the CSS classes for a given card variant.
 *
 * @param variant - The card variant
 * @returns CSS class string for the variant
 */
const getVariantClasses = (variant: CardProps['variant']): string => {
  const variants = {
    default: 'bg-[hsl(var(--bg-secondary))]',
    elevated: 'bg-[hsl(var(--bg-secondary))] shadow-sm',
    outlined: 'bg-[hsl(var(--bg-secondary))] border',
  };
  return variants[variant || 'default'];
};

/**
 * Get the CSS classes for a given padding size.
 *
 * @param padding - The padding size
 * @returns CSS class string for the padding
 */
const getPaddingClasses = (padding: CardProps['padding']): string => {
  const paddings = {
    none: '',
    sm: 'p-3',
    md: 'p-4',
    lg: 'p-6',
    responsive: 'p-3 md:p-4',
  };
  return paddings[padding || 'md'];
};

/**
 * Get the CSS classes for a given border color.
 *
 * @param borderColor - The border color variant
 * @returns CSS class string for the border color
 */
const getBorderColorClasses = (borderColor: CardProps['borderColor']): string => {
  const borderColors = {
    default: 'border-[hsl(var(--border-color))]',
    separator: 'border-[hsl(var(--separator-color))]',
  };
  return borderColors[borderColor || 'default'];
};

/**
 * Get the CSS classes for a given border radius.
 *
 * @param rounded - The border radius size
 * @returns CSS class string for the border radius
 */
const getRoundedClasses = (rounded: CardProps['rounded']): string => {
  const roundedSizes = {
    lg: 'rounded-lg',
    xl: 'rounded-xl',
    '2xl': 'rounded-2xl',
  };
  return roundedSizes[rounded || 'xl'];
};

/**
 * A versatile card component with multiple variants, padding options, and states.
 *
 * Supports all standard HTML div attributes and extends them with:
 * - Visual variants for different contexts (default, elevated, outlined)
 * - Flexible padding options including responsive mode
 * - Loading state with integrated skeleton
 * - Header and footer slots for structured content
 * - Hoverable effect for interactive cards
 *
 * The card automatically handles:
 * - Border styling with customizable colors
 * - Border radius options
 * - Shadow transitions for hoverable state
 *
 * @param props - Component props
 * @param props.variant - Visual style variant
 * @param props.padding - Internal padding size
 * @param props.hoverable - Enable hover effect
 * @param props.isLoading - Loading state
 * @param props.header - Header content slot
 * @param props.footer - Footer content slot
 * @param props.rounded - Border radius size
 * @param props.borderColor - Border color variant
 * @param props.children - Main card content
 * @param props.className - Additional CSS classes
 * @returns The rendered card component
 *
 * @example
 * // Default card with content
 * <Card>
 *   <p>Card content here</p>
 * </Card>
 *
 * @example
 * // Elevated card with header and footer
 * <Card
 *   variant="elevated"
 *   header={<h3 className="font-semibold">Card Title</h3>}
 *   footer={<Button size="sm">Action</Button>}
 * >
 *   <p>Card body content</p>
 * </Card>
 *
 * @example
 * // Outlined hoverable card with responsive padding
 * <Card variant="outlined" hoverable padding="responsive">
 *   <p>Interactive card content</p>
 * </Card>
 *
 * @example
 * // Loading state
 * <Card isLoading>
 *   <p>This content won't render while loading</p>
 * </Card>
 */
export const Card: React.FC<CardProps> = ({
  variant = 'default',
  padding = 'md',
  hoverable = false,
  isLoading = false,
  header,
  footer,
  rounded = 'xl',
  borderColor = 'default',
  children,
  className = '',
  ...props
}) => {
  // Base classes: always include border and background
  const baseClasses = 'border';

  // Variant classes
  const variantClasses = getVariantClasses(variant);

  // Padding classes
  const paddingClasses = getPaddingClasses(padding);

  // Border color classes (only apply for outlined variant)
  const borderColorClasses = variant === 'outlined' ? getBorderColorClasses(borderColor) : '';

  // Rounded classes
  const roundedClasses = getRoundedClasses(rounded);

  // Hoverable classes
  const hoverableClasses = hoverable
    ? 'transition-[box-shadow,border-color,background-color] duration-[var(--duration-fast)] ease-[var(--ease-standard)] hover:shadow-md cursor-pointer'
    : '';

  // Show skeleton when loading
  if (isLoading) {
    return (
      <CardSkeleton
        className={cn(
          baseClasses,
          variantClasses,
          paddingClasses,
          borderColorClasses,
          roundedClasses,
          className
        )}
        showHeader={!!header}
        showFooter={!!footer}
      />
    );
  }

  return (
    <div
      className={cn(
        baseClasses,
        variantClasses,
        paddingClasses,
        borderColorClasses,
        roundedClasses,
        hoverableClasses,
        className
      )}
      {...props}
    >
      {header && <div className="mb-3">{header}</div>}
      {children}
      {footer && <div className="mt-3">{footer}</div>}
    </div>
  );
};

export default Card;
