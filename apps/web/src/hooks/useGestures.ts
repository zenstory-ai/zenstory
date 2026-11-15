import { useRef, useCallback, useState } from "react";

/**
 * Swipe direction types
 */
export type SwipeDirection = "left" | "right" | "up" | "down";

/**
 * Gesture event details
 */
export interface SwipeGestureEvent {
  direction: SwipeDirection;
  deltaX: number;
  deltaY: number;
  velocity: number;
}

export interface PinchGestureEvent {
  scale: number;
  center: { x: number; y: number };
}

/**
 * Configuration options for gesture detection
 */
export interface GestureConfig {
  /** Minimum distance in pixels to trigger a swipe (default: 50) */
  swipeThreshold?: number;
  /** Maximum time in ms for a swipe gesture (default: 300) */
  swipeTimeout?: number;
  /** Minimum velocity for swipe (pixels/ms, default: 0.3) */
  swipeVelocity?: number;
  /** Minimum pinch scale change to trigger callback (default: 0.1) */
  pinchThreshold?: number;
  /** Enable vertical swipe detection (default: false) */
  enableVerticalSwipe?: boolean;
  /** Prevent default touch behavior during gestures (default: true) */
  preventDefaultTouch?: boolean;
}

const DEFAULT_CONFIG: Required<GestureConfig> = {
  swipeThreshold: 50,
  swipeTimeout: 300,
  swipeVelocity: 0.3,
  pinchThreshold: 0.1,
  enableVerticalSwipe: false,
  preventDefaultTouch: true,
};

/**
 * Get touch coordinates from an event
 */
function getTouchCoordinates(e: TouchEvent): { x: number; y: number } {
  const touch = e.touches[0] || e.changedTouches[0];
  return {
    x: touch.clientX,
    y: touch.clientY,
  };
}

/**
 * Calculate distance between two touch points
 */
function getTouchDistance(touches: React.TouchList): number {
  if (touches.length < 2) return 0;
  const dx = touches[0].clientX - touches[1].clientX;
  const dy = touches[0].clientY - touches[1].clientY;
  return Math.sqrt(dx * dx + dy * dy);
}

/**
 * Calculate center point between two touches
 */
function getTouchCenter(touches: React.TouchList): { x: number; y: number } {
  if (touches.length < 2) {
    const touch = touches[0];
    return { x: touch.clientX, y: touch.clientY };
  }
  return {
    x: (touches[0].clientX + touches[1].clientX) / 2,
    y: (touches[0].clientY + touches[1].clientY) / 2,
  };
}

/**
 * Determine swipe direction based on delta
 */
function getSwipeDirection(
  deltaX: number,
  deltaY: number,
  enableVertical: boolean
): SwipeDirection | null {
  const absX = Math.abs(deltaX);
  const absY = Math.abs(deltaY);

  // Horizontal swipe
  if (absX > absY && absX > 0) {
    return deltaX > 0 ? "right" : "left";
  }

  // Vertical swipe
  if (enableVertical && absY > absX && absY > 0) {
    return deltaY > 0 ? "down" : "up";
  }

  return null;
}

/**
 * Hook for detecting swipe gestures on touch devices
 *
 * @param onSwipe - Callback fired when a swipe gesture is detected
 * @param config - Configuration options for gesture detection
 * @returns Object with bind function to attach to element
 *
 * @example
 * ```tsx
 * const { bind } = useSwipeGestures(
 *   (e) => {
 *     if (e.direction === 'left') switchToNextPanel();
 *     if (e.direction === 'right') switchToPrevPanel();
 *   },
 *   { swipeThreshold: 75 }
 * );
 *
 * return <div {...bind()} />;
 * ```
 */
export function useSwipeGestures(
  onSwipe: (event: SwipeGestureEvent) => void,
  config: GestureConfig = {}
): { bind: () => { onTouchStart: (e: React.TouchEvent) => void; onTouchEnd: (e: React.TouchEvent) => void } } {
  const options = { ...DEFAULT_CONFIG, ...config };
  const touchStartRef = useRef<{ x: number; y: number; time: number } | null>(null);

  const handleTouchStart = useCallback(
    (e: React.TouchEvent) => {
      const coords = getTouchCoordinates(e.nativeEvent);
      touchStartRef.current = {
        x: coords.x,
        y: coords.y,
        time: Date.now(),
      };
    },
    []
  );

  const handleTouchEnd = useCallback(
    (e: React.TouchEvent) => {
      if (!touchStartRef.current) return;

      const endCoords = getTouchCoordinates(e.nativeEvent);
      const startX = touchStartRef.current.x;
      const startY = touchStartRef.current.y;
      const startTime = touchStartRef.current.time;
      const endTime = Date.now();

      const deltaX = endCoords.x - startX;
      const deltaY = endCoords.y - startY;
      const deltaTime = endTime - startTime;
      const distance = Math.sqrt(deltaX * deltaX + deltaY * deltaY);
      const velocity = distance / deltaTime;

      // Clear start position
      touchStartRef.current = null;

      // Check if gesture meets threshold requirements
      if (
        distance < options.swipeThreshold ||
        deltaTime > options.swipeTimeout ||
        velocity < options.swipeVelocity
      ) {
        return;
      }

      const direction = getSwipeDirection(
        deltaX,
        deltaY,
        options.enableVerticalSwipe
      );

      if (direction) {
        if (options.preventDefaultTouch) {
          e.preventDefault();
        }
        onSwipe({
          direction,
          deltaX,
          deltaY,
          velocity,
        });
      }
    },
    [
      onSwipe,
      options.swipeThreshold,
      options.swipeTimeout,
      options.swipeVelocity,
      options.enableVerticalSwipe,
      options.preventDefaultTouch,
    ]
  );

  return {
    bind: () => ({
      onTouchStart: handleTouchStart,
      onTouchEnd: handleTouchEnd,
    }),
  };
}

/**
 * Hook for detecting pinch-to-zoom gestures
 *
 * @param onPinch - Callback fired when a pinch gesture changes scale
 * @param config - Configuration options for gesture detection
 * @returns Object with bind function to attach to element
 *
 * @example
 * ```tsx
 * const [scale, setScale] = useState(1);
 * const { bind } = usePinchGesture((e) => {
 *   setScale(prev => Math.max(0.5, Math.min(2, prev * e.scale)));
 * });
 *
 * return <div {...bind()} style={{ transform: `scale(${scale})` }} />;
 * ```
 */
export function usePinchGesture(
  onPinch: (event: PinchGestureEvent) => void,
  config: GestureConfig = {}
): { bind: () => { onTouchStart: (e: React.TouchEvent) => void; onTouchMove: (e: React.TouchEvent) => void; onTouchEnd: (e: React.TouchEvent) => void } } {
  const options = { ...DEFAULT_CONFIG, ...config };
  const initialDistanceRef = useRef<number>(0);
  const lastScaleRef = useRef<number>(1);

  const handleTouchStart = useCallback((e: React.TouchEvent) => {
    if (e.touches.length === 2) {
      initialDistanceRef.current = getTouchDistance(e.touches);
      lastScaleRef.current = 1;
    }
  }, []);

  const handleTouchMove = useCallback(
    (e: React.TouchEvent) => {
      if (e.touches.length !== 2 || initialDistanceRef.current === 0) return;

      const currentDistance = getTouchDistance(e.touches);
      const scale = currentDistance / initialDistanceRef.current;

      // Only trigger callback if scale change exceeds threshold
      if (Math.abs(scale - lastScaleRef.current) >= options.pinchThreshold) {
        e.preventDefault();
        lastScaleRef.current = scale;

        onPinch({
          scale,
          center: getTouchCenter(e.touches),
        });
      }
    },
    [onPinch, options.pinchThreshold]
  );

  const handleTouchEnd = useCallback(() => {
    initialDistanceRef.current = 0;
  }, []);

  return {
    bind: () => ({
      onTouchStart: handleTouchStart,
      onTouchMove: handleTouchMove,
      onTouchEnd: handleTouchEnd,
    }),
  };
}

/**
 * Hook for managing editor zoom with pinch gesture
 *
 * @param initialZoom - Initial zoom level (default: 1)
 * @param minZoom - Minimum zoom level (default: 0.5)
 * @param maxZoom - Maximum zoom level (default: 2)
 * @returns Object with zoom state and bind function
 *
 * @example
 * ```tsx
 * const { zoom, bind, resetZoom } = usePinchZoom();
 *
 * return (
 *   <div {...bind()} style={{ fontSize: `${zoom}em` }}>
 *     Content
 *   </div>
 * );
 * ```
 */
export function usePinchZoom(
  initialZoom: number = 1,
  minZoom: number = 0.5,
  maxZoom: number = 2
): {
  zoom: number;
  bind: () => {
    onTouchStart: (e: React.TouchEvent) => void;
    onTouchMove: (e: React.TouchEvent) => void;
    onTouchEnd: (e: React.TouchEvent) => void;
  };
  setZoom: (zoom: number) => void;
  resetZoom: () => void;
} {
  const [zoom, setZoom] = useState(initialZoom);
  const baseZoomRef = useRef(initialZoom);

  const handlePinch = useCallback(
    (event: PinchGestureEvent) => {
      const newZoom = Math.max(minZoom, Math.min(maxZoom, baseZoomRef.current * event.scale));
      setZoom(newZoom);
    },
    [minZoom, maxZoom]
  );

  const handleTouchEnd = useCallback(() => {
    // Store the new base zoom for subsequent pinch gestures
    baseZoomRef.current = zoom;
  }, [zoom]);

  const { bind: pinchBind } = usePinchGesture(handlePinch);

  const originalBind = pinchBind();
  const bind = () => ({
    ...originalBind,
    onTouchEnd: (e: React.TouchEvent) => {
      originalBind.onTouchEnd(e);
      handleTouchEnd();
    },
  });

  const resetZoom = useCallback(() => {
    setZoom(initialZoom);
    baseZoomRef.current = initialZoom;
  }, [initialZoom]);

  return {
    zoom,
    bind,
    setZoom,
    resetZoom,
  };
}
