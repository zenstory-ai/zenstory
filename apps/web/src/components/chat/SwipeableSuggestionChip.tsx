import React, { useState } from "react";
import { useSwipeGestures } from "../../hooks/useGestures";

export interface SwipeableSuggestionChipProps {
  /** The suggestion text to display */
  suggestion: string;
  /** Callback when the chip is clicked/tapped */
  onClick: () => void;
  /** Callback when the chip is dismissed via swipe gesture */
  onDismiss: () => void;
  /** Whether to use mobile-optimized styling */
  isMobile: boolean;
}

/**
 * A suggestion chip component with swipe-to-dismiss support for mobile.
 */
export const SwipeableSuggestionChip: React.FC<SwipeableSuggestionChipProps> = ({
  suggestion,
  onClick,
  onDismiss,
  isMobile,
}) => {
  const [isDismissing, setIsDismissing] = useState(false);
  const [translateX, setTranslateX] = useState(0);

  const { bind } = useSwipeGestures(
    (e) => {
      if (e.direction === "left" || e.direction === "right") {
        setIsDismissing(true);
        setTranslateX(e.direction === "left" ? -150 : 150);
        setTimeout(() => {
          onDismiss();
        }, 200);
      }
    },
    {
      swipeThreshold: 40,
      swipeTimeout: 250,
      swipeVelocity: 0.2,
      enableVerticalSwipe: false,
      preventDefaultTouch: false,
    }
  );

  return (
    <button
      {...bind()}
      type="button"
      onClick={onClick}
      aria-label={suggestion}
      className={`shrink-0 border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] hover:bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] rounded-full text-xs whitespace-nowrap transition-[background-color,color,border-color,opacity] duration-[var(--duration-instant)] ease-[var(--ease-standard)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-primary))] ${isMobile ? "px-3 py-1.5 min-h-11 md:min-h-[32px]" : "px-2.5 py-1"} ${isDismissing ? "opacity-0 h-0 py-0 my-0 overflow-hidden" : ""}`}
      style={{
        transform: `translate3d(${translateX}px, 0, 0)`,
        transition: isDismissing
          ? "transform var(--duration-fast) var(--ease-out-expo), opacity var(--duration-fast) var(--ease-standard)"
          : "none",
      }}
    >
      {suggestion}
    </button>
  );
};
