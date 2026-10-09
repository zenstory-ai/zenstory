/**
 * @fileoverview IconButton - the one square, icon-only button used across the dashboard.
 *
 * - 32px on desktop, 44px touch target on phones (`size="auto"`, the default), or pinned
 *   with `size="md"` (32px) / `size="touch"` (44px).
 * - `label` is required: it becomes the accessible name and the tooltip, so an icon-only
 *   control is never announced as an empty button.
 * - Tones: `default` (grey → primary text on hover), `danger` (hover turns red, for delete).
 *
 * @module components/ui/IconButton
 */
import React from 'react';

export interface IconButtonProps
  extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, 'aria-label' | 'children'> {
  /** Accessible name and tooltip. */
  label: string;
  /** The icon element, sized by the caller (16px on desktop is the norm). */
  icon: React.ReactNode;
  /**
   * - 'auto': 32px, growing to 44px below the 768px breakpoint
   * - 'md': always 32px
   * - 'touch': always 44px
   * @default 'auto'
   */
  size?: 'auto' | 'md' | 'touch';
  /** @default 'default' */
  tone?: 'default' | 'danger';
}

const SIZE_CLASSES: Record<NonNullable<IconButtonProps['size']>, string> = {
  auto: 'h-8 w-8 max-md:h-11 max-md:w-11',
  md: 'h-8 w-8',
  touch: 'h-11 w-11',
};

const TONE_CLASSES: Record<NonNullable<IconButtonProps['tone']>, string> = {
  default:
    'text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--bg-tertiary))] hover:text-[hsl(var(--text-primary))]',
  danger:
    'text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--error)/0.1)] hover:text-[hsl(var(--error))]',
};

export const IconButton = React.forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, icon, size = 'auto', tone = 'default', className = '', title, type = 'button', ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      aria-label={label}
      title={title ?? label}
      className={`inline-flex shrink-0 items-center justify-center rounded-lg transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.5)] disabled:cursor-not-allowed disabled:opacity-50 ${SIZE_CLASSES[size]} ${TONE_CLASSES[tone]} ${className}`}
      {...props}
    >
      {icon}
    </button>
  );
});

export default IconButton;
