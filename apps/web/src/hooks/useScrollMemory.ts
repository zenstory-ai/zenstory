import { useRef, useCallback, useEffect } from 'react';
import { logger } from "../lib/logger";

/**
 * Map of panel IDs to their saved scroll positions
 */
interface ScrollPositions {
  [key: string]: number;
}

/**
 * Return type for useScrollMemory hook
 */
export interface ScrollMemoryHandlers {
  /** Save scroll position for a specific panel */
  saveScrollPosition: (panelId: string, scrollTop: number) => void;
  /** Get saved scroll position for a specific panel (returns 0 if not found) */
  getScrollPosition: (panelId: string) => number;
  /** Restore scroll position on an element using saved position */
  restoreScrollPosition: (panelId: string, element: HTMLElement | null) => void;
}

const SCROLL_POSITIONS_KEY = 'zenstory_mobile_scroll_positions';

/**
 * Hook to save and restore scroll positions for mobile panels.
 * Persists to sessionStorage to survive page refreshes within the same session.
 *
 * @param storageKey - Unique key to namespace scroll positions (default: 'default')
 * @returns Object with scroll position management functions
 *
 * @example
 * ```tsx
 * const { saveScrollPosition, restoreScrollPosition } = useScrollMemory('file-tree');
 *
 * // Save scroll position when leaving panel
 * const handleScroll = (e: React.UIEvent<HTMLElement>) => {
 *   saveScrollPosition('file-list', e.currentTarget.scrollTop);
 * };
 *
 * // Restore scroll position when returning to panel
 * useEffect(() => {
 *   restoreScrollPosition('file-list', listRef.current);
 * }, []);
 * ```
 */
export function useScrollMemory(storageKey: string = 'default'): ScrollMemoryHandlers {
  const scrollPositionsRef = useRef<ScrollPositions>({});

  // Load saved positions on mount
  useEffect(() => {
    scrollPositionsRef.current = {};
    try {
      const saved = sessionStorage.getItem(`${SCROLL_POSITIONS_KEY}_${storageKey}`);
      if (saved) {
        scrollPositionsRef.current = JSON.parse(saved);
      }
    } catch (e) {
      logger.warn('Failed to load scroll positions:', e);
    }
  }, [storageKey]);

  // Save positions to sessionStorage
  const persistPositions = useCallback(() => {
    try {
      sessionStorage.setItem(
        `${SCROLL_POSITIONS_KEY}_${storageKey}`,
        JSON.stringify(scrollPositionsRef.current)
      );
    } catch (e) {
      logger.warn('Failed to save scroll positions:', e);
    }
  }, [storageKey]);

  // Save scroll position for a specific panel
  const saveScrollPosition = useCallback((panelId: string, scrollTop: number) => {
    scrollPositionsRef.current[panelId] = scrollTop;
    persistPositions();
  }, [persistPositions]);

  // Get saved scroll position for a specific panel
  const getScrollPosition = useCallback((panelId: string): number => {
    return scrollPositionsRef.current[panelId] || 0;
  }, []);

  // Restore scroll position for a specific element
  const restoreScrollPosition = useCallback((panelId: string, element: HTMLElement | null) => {
    if (element) {
      const savedPosition = getScrollPosition(panelId);
      element.scrollTop = savedPosition;
    }
  }, [getScrollPosition]);

  return {
    saveScrollPosition,
    getScrollPosition,
    restoreScrollPosition,
  };
}
