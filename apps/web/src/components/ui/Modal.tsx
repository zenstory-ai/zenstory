/**
 * @fileoverview Modal component - A versatile dialog with portal rendering, accessibility, and compound components.
 *
 * This module provides a reusable modal component with:
 * - Portal rendering to document.body
 * - Keyboard navigation (Escape to close)
 * - Click outside to close
 * - Scroll lock when open
 * - Focus management
 * - Accessible dialog attributes
 * - Compound components (ModalHeader, ModalBody, ModalFooter)
 *
 * @module components/ui/Modal
 */
import React, { createContext, useContext, useEffect, useCallback, useRef, useId } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useDialogInteractions } from './dialogFocus';
import { IconButton } from './IconButton';

/**
 * Props for the Modal component.
 *
 * @interface ModalProps
 */
export interface ModalProps {
  /**
   * Whether the modal is open.
   */
  open: boolean;

  /**
   * Callback fired when the modal requests to be closed.
   */
  onClose: () => void;

  /**
   * Title content for the modal (can be string or React node).
   * Used for accessibility labeling.
   */
  title?: React.ReactNode;

  /**
   * Description content for the modal.
   * Used for accessibility labeling.
   */
  description?: React.ReactNode;

  /**
   * Main content of the modal.
   */
  children: React.ReactNode;

  /**
   * Footer content (typically action buttons).
   */
  footer?: React.ReactNode;

  /**
   * Size variant of the modal.
   * - 'sm': 360px max-width
   * - 'md': 480px max-width
   * - 'lg': 640px max-width
   * - 'xl': 800px max-width
   * - 'full': calc(100vw - 48px) max-width
   * @default 'md'
   */
  size?: 'sm' | 'md' | 'lg' | 'xl' | 'full';

  /**
   * Whether to show the close button in the top-right corner.
   * @default true
   */
  showCloseButton?: boolean;

  /**
   * Whether clicking the backdrop should close the modal.
   * @default true
   */
  closeOnBackdropClick?: boolean;

  /**
   * Whether pressing Escape should close the modal.
   * @default true
   */
  closeOnEscape?: boolean;

  /**
   * Additional CSS classes for the modal content.
   */
  className?: string;

  /**
   * Additional CSS classes for the overlay.
   */
  overlayClassName?: string;
}

/**
 * Props for Modal sub-components.
 */
interface ModalSubcomponentProps {
  children: React.ReactNode;
  className?: string;
}

/**
 * Context for sharing modal state between compound components.
 */
interface ModalContextValue {
  onClose: () => void;
  titleId: string;
  descriptionId: string;
}

const ModalContext = createContext<ModalContextValue | null>(null);

/**
 * Hook to access modal context.
 * @throws Error if used outside of Modal component
 */
const useModalContext = (): ModalContextValue => {
  const context = useContext(ModalContext);
  if (!context) {
    throw new Error('Modal sub-components must be used within a Modal');
  }
  return context;
};

/**
 * Get the max-width class for a given modal size.
 */
const getSizeClasses = (size: ModalProps['size']): string => {
  const sizes = {
    sm: 'max-w-[360px]',
    md: 'max-w-[480px]',
    lg: 'max-w-[640px]',
    xl: 'max-w-[800px]',
    full: 'max-w-[calc(100vw-48px)]',
  };
  return sizes[size || 'md'];
};

/**
 * ModalHeader component - Header section of the modal.
 *
 * @param props.children - Header content
 * @param props.className - Additional CSS classes
 */
export const ModalHeader: React.FC<ModalSubcomponentProps> = ({ children, className = '' }) => {
  const { titleId } = useModalContext();
  return (
    <div
      id={titleId}
      className={`text-lg font-semibold text-[hsl(var(--text-primary))] ${className}`}
    >
      {children}
    </div>
  );
};

/**
 * ModalBody component - Main content section of the modal.
 *
 * @param props.children - Body content
 * @param props.className - Additional CSS classes
 */
export const ModalBody: React.FC<ModalSubcomponentProps> = ({ children, className = '' }) => {
  const { descriptionId } = useModalContext();
  return (
    <div
      id={descriptionId}
      className={`text-sm text-[hsl(var(--text-secondary))] ${className}`}
    >
      {children}
    </div>
  );
};

/**
 * ModalFooter component - Footer section with action buttons.
 *
 * @param props.children - Footer content (typically buttons)
 * @param props.className - Additional CSS classes
 */
export const ModalFooter: React.FC<ModalSubcomponentProps> = ({ children, className = '' }) => {
  return (
    <div
      className={`flex items-center justify-end gap-3 mt-4 pt-4 border-t border-[hsl(var(--border-color))] ${className}`}
    >
      {children}
    </div>
  );
};

/**
 * Modal component - A fully accessible dialog with portal rendering.
 *
 * Features:
 * - Renders to document.body via React Portal
 * - Locks body scroll when open
 * - Traps focus within the modal
 * - Closes on Escape key press
 * - Closes on backdrop click
 * - Accessible with proper ARIA attributes
 *
 * @param props - Component props
 * @returns The rendered modal portal or null if closed
 *
 * @example
 * // Basic usage
 * <Modal open={isOpen} onClose={() => setIsOpen(false)} title="Confirm Action">
 *   <p>Are you sure you want to proceed?</p>
 * </Modal>
 *
 * @example
 * // With compound components
 * <Modal open={isOpen} onClose={handleClose} size="lg">
 *   <ModalHeader>Edit Settings</ModalHeader>
 *   <ModalBody>
 *     <p>Settings content here...</p>
 *   </ModalBody>
 *   <ModalFooter>
 *     <Button variant="ghost" onClick={handleClose}>Cancel</Button>
 *     <Button onClick={handleSave}>Save</Button>
 *   </ModalFooter>
 * </Modal>
 */
export const Modal: React.FC<ModalProps> & {
  Header: typeof ModalHeader;
  Body: typeof ModalBody;
  Footer: typeof ModalFooter;
} = ({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  size = 'md',
  showCloseButton = true,
  closeOnBackdropClick = true,
  closeOnEscape = true,
  className = '',
  overlayClassName = '',
}) => {
  const titleId = useId();
  const descriptionId = useId();
  const { t } = useTranslation();
  const modalRef = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  const closeOnEscapeRef = useRef(closeOnEscape);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    closeOnEscapeRef.current = closeOnEscape;
  }, [closeOnEscape]);

  // Handle backdrop click
  const handleBackdropClick = useCallback(
    (event: React.MouseEvent) => {
      if (closeOnBackdropClick && event.target === event.currentTarget) {
        onClose();
      }
    },
    [closeOnBackdropClick, onClose]
  );

  // Handle content click (prevent propagation to backdrop)
  const handleContentClick = useCallback((event: React.MouseEvent) => {
    event.stopPropagation();
  }, []);

  useDialogInteractions({
    open,
    dialogRef: modalRef,
    initialFocusSelector: '[data-modal-autofocus], input, textarea, select',
    onEscape: () => {
      if (closeOnEscapeRef.current) onCloseRef.current();
    },
  });

  // Don't render if not open
  if (!open) {
    return null;
  }

  const contextValue: ModalContextValue = {
    onClose,
    titleId,
    descriptionId,
  };

  const sizeClasses = getSizeClasses(size);

  const overlayClasses = `fixed inset-0 z-[1000] bg-black/60 backdrop-blur-[4px] flex items-center justify-center p-6 ${overlayClassName}`;

  const contentClasses = `bg-[hsl(var(--bg-secondary))] rounded-2xl shadow-2xl w-full ${sizeClasses} animate-scale-in flex flex-col max-h-[90vh] ${className}`;

  return createPortal(
    <ModalContext.Provider value={contextValue}>
      <div
        className={overlayClasses}
        onClick={handleBackdropClick}
        role="presentation"
      >
        <div
          ref={modalRef}
          className={contentClasses}
          onClick={handleContentClick}
          role="dialog"
          tabIndex={-1}
          aria-modal="true"
          aria-labelledby={title ? titleId : undefined}
          aria-describedby={description ? descriptionId : undefined}
        >
          {/* Header with optional title and close button */}
          {(title || showCloseButton) && (
            <div className="flex items-start justify-between mb-4 px-6 pt-6 shrink-0">
              {title && (
                <div
                  id={titleId}
                  className="text-lg font-semibold text-[hsl(var(--text-primary))]"
                >
                  {title}
                </div>
              )}
              {showCloseButton && (
                <IconButton
                  onClick={onClose}
                  label={t('common:closeModal')}
                  icon={<X size={20} />}
                  className="-mr-2 -mt-1 ml-auto"
                />
              )}
            </div>
          )}

          {/* Description */}
          {description && (
            <div
              id={descriptionId}
              className="text-sm text-[hsl(var(--text-secondary))] mb-4 px-6 shrink-0"
            >
              {description}
            </div>
          )}

          {/* Main content - scrollable */}
          <div className="flex-1 overflow-y-auto px-6 pb-4">{children}</div>

          {/* Footer */}
          {footer && (
            <div className="flex items-center justify-end gap-3 pt-4 px-6 pb-6 border-t border-[hsl(var(--border-color))] shrink-0">
              {footer}
            </div>
          )}
        </div>
      </div>
    </ModalContext.Provider>,
    document.body
  );
};

// Attach compound components
Modal.Header = ModalHeader;
Modal.Body = ModalBody;
Modal.Footer = ModalFooter;

export default Modal;
