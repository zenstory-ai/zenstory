/**
 * @fileoverview ConfirmDialog component - A confirmation dialog for user actions.
 *
 * This module provides a reusable confirmation dialog component that supports
 * multiple variants (danger, warning, info), loading states, and keyboard interactions.
 *
 * Features:
 * - Three variants: danger, warning, info (with matching icons and colors)
 * - Loading state with disabled buttons
 * - ESC key to close (disabled during loading)
 * - Customizable button labels
 * - Async onConfirm support
 *
 * @module components/ui/ConfirmDialog
 */
import React, { useEffect, useRef, useId } from 'react';
import { createPortal } from 'react-dom';
import { AlertTriangle, AlertCircle, Info } from 'lucide-react';
import { Button } from './Button';
import { useDialogInteractions } from './dialogFocus';

/**
 * Props for the ConfirmDialog component.
 *
 * @interface ConfirmDialogProps
 */
export interface ConfirmDialogProps {
  /**
   * Whether the dialog is open/visible.
   */
  open: boolean;

  /**
   * Callback when the dialog is requested to close (via Cancel or ESC).
   */
  onClose: () => void;

  /**
   * Callback when the user confirms the action.
   * Can be async (returns Promise).
   */
  onConfirm: () => void | Promise<void>;

  /**
   * Title text displayed in the dialog header.
   */
  title: string;

  /**
   * Message text displayed below the title.
   */
  message: string;

  /**
   * Label for the confirm button.
   * @default "Confirm"
   */
  confirmLabel?: string;

  /**
   * Label for the cancel button.
   * @default "Cancel"
   */
  cancelLabel?: string;

  /**
   * Visual variant of the dialog.
   * - 'danger': Red styling, AlertTriangle icon (for destructive actions)
   * - 'warning': Yellow styling, AlertCircle icon (for caution actions)
   * - 'info': Blue styling, Info icon (for informational confirmations)
   * @default 'info'
   */
  variant?: 'danger' | 'warning' | 'info';

  /**
   * Whether the confirm action is in progress.
   * When true, both buttons are disabled and confirm shows a spinner.
   * @default false
   */
  loading?: boolean;

  /**
   * Whether the dialog buttons are disabled.
   * @default false
   */
  disabled?: boolean;
}

/**
 * Get the icon component and color class based on variant.
 *
 * @param variant - The dialog variant
 * @returns Object containing icon component and color class
 */
const getVariantConfig = (variant: ConfirmDialogProps['variant']) => {
  const configs = {
    danger: {
      Icon: AlertTriangle,
      iconColor: 'text-[hsl(var(--error))]',
      confirmVariant: 'danger' as const,
    },
    warning: {
      Icon: AlertCircle,
      iconColor: 'text-[hsl(var(--warning))]',
      confirmVariant: 'primary' as const,
    },
    info: {
      Icon: Info,
      iconColor: 'text-[hsl(var(--accent-primary))]',
      confirmVariant: 'primary' as const,
    },
  };
  return configs[variant || 'info'];
};

/**
 * A confirmation dialog component for user actions.
 *
 * Renders a modal dialog with an icon, title, message, and two buttons
 * (Cancel and Confirm). Supports different variants for different contexts:
 * - Danger: For destructive actions like delete
 * - Warning: For caution-inducing actions
 * - Info: For general confirmations
 *
 * The dialog handles:
 * - ESC key to close (disabled during loading)
 * - Loading state with spinner on confirm button
 * - Disabled state for both buttons
 * - Click outside to close (via modal-overlay)
 *
 * @param props - Component props
 * @param props.open - Whether the dialog is visible
 * @param props.onClose - Callback when dialog closes
 * @param props.onConfirm - Callback when user confirms
 * @param props.title - Dialog title
 * @param props.message - Dialog message
 * @param props.confirmLabel - Confirm button text
 * @param props.cancelLabel - Cancel button text
 * @param props.variant - Visual style variant
 * @param props.loading - Loading state
 * @param props.disabled - Disabled state
 * @returns The rendered confirm dialog component (or null if closed)
 *
 * @example
 * // Danger dialog for delete action
 * <ConfirmDialog
 *   open={showDeleteDialog}
 *   onClose={() => setShowDeleteDialog(false)}
 *   onConfirm={handleDelete}
 *   title="Delete File"
 *   message="Are you sure you want to delete this file? This action cannot be undone."
 *   variant="danger"
 *   confirmLabel="Delete"
 * />
 *
 * @example
 * // Info dialog with loading state
 * <ConfirmDialog
 *   open={showConfirm}
 *   onClose={() => setShowConfirm(false)}
 *   onConfirm={async () => await saveChanges()}
 *   title="Save Changes"
 *   message="Do you want to save your changes before leaving?"
 *   variant="info"
 *   loading={isSaving}
 *   confirmLabel="Save"
 * />
 */
export const ConfirmDialog: React.FC<ConfirmDialogProps> = ({
  open,
  onClose,
  onConfirm,
  title,
  message,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  variant = 'info',
  loading = false,
  disabled = false,
}) => {
  const { Icon, iconColor, confirmVariant } = getVariantConfig(variant);
  const titleId = useId();
  const descriptionId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  const loadingRef = useRef(loading);
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    loadingRef.current = loading;
    onCloseRef.current = onClose;
  }, [loading, onClose]);

  useDialogInteractions({
    open,
    dialogRef,
    onEscape: () => {
      if (!loadingRef.current) onCloseRef.current();
    },
  });

  /**
   * Handle confirm button click.
   */
  const handleConfirm = async () => {
    await onConfirm();
  };

  /**
   * Handle overlay click to close.
   * Disabled during loading state.
   */
  const handleOverlayClick = () => {
    if (!loading) {
      onClose();
    }
  };

  if (!open) {
    return null;
  }

  return createPortal(
    <div
      className="modal-overlay flex items-center justify-center p-4"
      onClick={handleOverlayClick}
      role="presentation"
    >
      <div
        ref={dialogRef}
        className="modal w-full max-w-[400px] animate-scale-in"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        tabIndex={-1}
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
      >
        {/* Header with Icon and Title */}
        <div className="flex items-start gap-3 mb-4">
          <div className={`flex-shrink-0 ${iconColor}`}>
            <Icon className="w-10 h-10" />
          </div>
          <div className="flex-1 pt-1">
            <h2 id={titleId} className="ui-sans-headings text-lg font-semibold text-[hsl(var(--text-primary))]">
              {title}
            </h2>
          </div>
        </div>

        {/* Message */}
        <div className="mb-6">
          <p id={descriptionId} className="text-sm text-[hsl(var(--text-secondary))] leading-relaxed">
            {message}
          </p>
        </div>

        {/* Footer with Buttons */}
        <div className="flex justify-end gap-3">
          <Button
            variant="ghost"
            onClick={onClose}
            disabled={loading || disabled}
          >
            {cancelLabel}
          </Button>
          <Button
            variant={confirmVariant}
            onClick={handleConfirm}
            disabled={loading || disabled}
            isLoading={loading}
          >
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>,
    document.body
  );
};

export default ConfirmDialog;
