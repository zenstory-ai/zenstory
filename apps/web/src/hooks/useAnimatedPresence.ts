import { useEffect, useState } from "react";

export type PresenceState = "entered" | "exiting";

export interface AnimatedPresenceResult {
  /** Whether the surface should remain mounted in the DOM. */
  isMounted: boolean;
  /** Whether the surface is currently playing its exit transition. */
  isExiting: boolean;
  /** Convenience state for data attributes. */
  state: PresenceState;
}

/**
 * Returns whether exit transitions should hold the element mounted.
 * - `false` under `prefers-reduced-motion: reduce` (0ms unmount)
 * - `false` in headless DOM test runtimes (happy-dom / jsdom) where `document.getAnimations`
 *   is absent, so synchronous close assertions keep passing without artificial timers.
 */
export function canAnimatePresence(): boolean {
  if (typeof window === "undefined" || typeof document === "undefined") return false;
  if (typeof document.getAnimations !== "function") {
    return false;
  }
  if (
    typeof window.matchMedia === "function" &&
    Boolean(window.matchMedia("(prefers-reduced-motion: reduce)")?.matches)
  ) {
    return false;
  }
  return true;
}

/**
 * Keeps an overlay, sheet, popover, banner, or collapsible region mounted for its
 * short exit duration (`durationMs`) so enter and exit transitions feel symmetric.
 * Re-opening mid-exit cancels the unmount immediately (100% interruptible).
 */
export function useAnimatedPresence(open: boolean, durationMs = 150): AnimatedPresenceResult {
  const [exiting, setExiting] = useState(false);
  const [prevOpen, setPrevOpen] = useState(open);

  if (open !== prevOpen) {
    setPrevOpen(open);
    if (open) {
      setExiting(false);
    } else if (durationMs > 0 && canAnimatePresence()) {
      setExiting(true);
    } else {
      setExiting(false);
    }
  }

  useEffect(() => {
    if (!exiting) return;
    const timer = window.setTimeout(() => {
      setExiting(false);
    }, durationMs);
    return () => window.clearTimeout(timer);
  }, [exiting, durationMs]);

  const isMounted = open || exiting;
  const isExiting = !open && exiting;
  return {
    isMounted,
    isExiting,
    state: isExiting ? "exiting" : "entered",
  };
}
