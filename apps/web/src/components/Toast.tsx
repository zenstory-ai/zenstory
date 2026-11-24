/**
 * @fileoverview Toast notification container component.
 *
 * This module provides a toast notification system that displays transient
 * messages to users. It listens for messages dispatched by the toast
 * utility and renders them with appropriate styling based on type.
 *
 * Features:
 * - Event-based toast display via module-scoped pub/sub channel
 * - Three toast types: error (red), success (green), info (blue)
 * - Auto-dismiss after 3 seconds (TOAST_DURATION)
 * - Stacked display with multiple simultaneous toasts
 * - Fixed positioning at bottom center of viewport
 * - Slide-in animation for new toasts
 *
 * @module components/Toast
 * @see {@link ../lib/toast.ts} - Toast utility for dispatching toast events
 */
import { useState, useEffect, useCallback } from 'react';
import { subscribeToast, type ToastEvent } from '../lib/toast';

/**
 * Duration in milliseconds before a toast auto-dismisses.
 * @constant {number}
 */
const TOAST_DURATION = 3000;
const TOAST_EXIT_DURATION = 160;
const MAX_VISIBLE_TOASTS = 3;

interface RenderedToast extends ToastEvent {
  exiting?: boolean;
}

/**
 * Container component for displaying toast notifications.
 *
 * This component subscribes to toast events dispatched by
 * the toast utility ({@link ../lib/toast.ts}) and renders them as
 * transient notification messages. Toasts automatically dismiss after
 * the configured TOAST_DURATION.
 *
 * The component maintains internal state for active toasts and handles:
 * - Adding new toasts when events are received
 * - Auto-removing toasts after the duration expires
 * - Rendering with appropriate styling based on toast type
 *
 * @returns The toast container element, or null if no toasts are active
 *
 * @example
 * // The ToastContainer is typically rendered at the app root level
 * import { ToastContainer } from './components/Toast';
 * import { toast } from './lib/toast';
 *
 * function App() {
 *   return (
 *     <>
 *       <MainContent />
 *       <ToastContainer />
 *     </>
 *   );
 * }
 *
 * // Trigger toasts from anywhere in the app
 * toast.success('Operation completed!');
 * toast.error('Something went wrong');
 * toast.info('Processing your request...');
 *
 * @example
 * // Toast styling by type:
 * // - error: Red background (hsl(var(--error)))
 * // - success: Green background (hsl(var(--success)))
 * // - info: Blue background (hsl(var(--accent-primary)))
 */
export function ToastContainer() {
  const [toasts, setToasts] = useState<RenderedToast[]>([]);

  /**
   * Marks a toast as exiting so it plays its exit transition before unmounting.
   */
  const markExiting = useCallback((id: number) => {
    setToasts((prev) =>
      prev.map((t) => (t.id === id ? { ...t, exiting: true } : t))
    );
  }, []);

  /**
   * Removes a toast from the active list by ID.
   *
   * @param id - The unique identifier of the toast to remove
   */
  const remove = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  useEffect(() => {
    // Store timeout IDs for cleanup
    const timeoutIds: ReturnType<typeof setTimeout>[] = [];

    const unsubscribe = subscribeToast((toast) => {
      setToasts((prev) => {
        const deduped = prev.some((item) => !item.exiting && item.message === toast.message && item.type === toast.type);
        if (deduped) return prev;
        return [...prev.slice(-(MAX_VISIBLE_TOASTS - 1)), toast];
      });
      const exitTimeoutId = setTimeout(
        () => markExiting(toast.id),
        Math.max(0, TOAST_DURATION - TOAST_EXIT_DURATION),
      );
      const removeTimeoutId = setTimeout(() => remove(toast.id), TOAST_DURATION);
      timeoutIds.push(exitTimeoutId, removeTimeoutId);
    });

    return () => {
      unsubscribe();
      // Clear all pending timeouts on unmount
      timeoutIds.forEach((id) => clearTimeout(id));
    };
  }, [markExiting, remove]);

  // Don't render anything if no active toasts
  if (toasts.length === 0) return null;

  return (
    <div className="fixed bottom-20 left-1/2 -translate-x-1/2 z-[1100] flex flex-col gap-2 items-center pointer-events-none">
      {toasts.map((t) => (
        <div
          key={t.id}
          role={t.type === 'error' ? 'alert' : 'status'}
          aria-live={t.type === 'error' ? 'assertive' : 'polite'}
          aria-atomic="true"
          data-state={t.exiting ? 'exiting' : 'open'}
          className={`pointer-events-auto max-w-[calc(100vw-2rem)] px-4 py-2 rounded-xl shadow-lg text-sm text-[hsl(var(--primary-foreground))] ${
            t.exiting ? 'motion-toast-exit' : 'motion-toast-enter'
          } ${
            t.type === 'error'
              ? 'bg-[hsl(var(--danger-solid))]'
              : t.type === 'success'
                ? 'bg-[hsl(var(--success-solid))]'
                : 'bg-[hsl(var(--primary))]'
          }`}
        >
          {t.message}
        </div>
      ))}
    </div>
  );
}
