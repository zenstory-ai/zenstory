/**
 * Throttled ref-backed state hook for live StreamRenderItem sequences.
 *
 * Accumulates updates synchronously in a ref so snapshots never lag behind,
 * while throttling React state flushes to STREAM_UPDATE_THROTTLE_MS.
 *
 * @module hooks/streaming/useThrottledStreamItems
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { STREAM_UPDATE_THROTTLE_MS, type StreamRenderItem } from "./types";

export interface UseThrottledStreamItemsReturn {
  streamRenderItems: StreamRenderItem[];
  getStreamItemsSnapshot: () => StreamRenderItem[];
  updateStreamItems: (updater: (prev: StreamRenderItem[]) => StreamRenderItem[]) => void;
  clearStreamItems: () => void;
  forceFlushStreamItems: () => void;
}

export function useThrottledStreamItems(): UseThrottledStreamItemsReturn {
  const [streamRenderItems, setStreamRenderItems] = useState<StreamRenderItem[]>([]);
  const streamItemsRef = useRef<StreamRenderItem[]>([]);
  const throttleTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastFlushTimeRef = useRef(0);

  const getStreamItemsSnapshot = useCallback(() => streamItemsRef.current.slice(), []);

  const flushStreamItems = useMemo(() => {
    const flush = () => {
      const now = Date.now();
      if (now - lastFlushTimeRef.current >= STREAM_UPDATE_THROTTLE_MS) {
        // Enough time has passed, flush immediately
        lastFlushTimeRef.current = now;
        setStreamRenderItems([...streamItemsRef.current]);
      } else if (!throttleTimeoutRef.current) {
        // Schedule a flush for when throttle period ends
        throttleTimeoutRef.current = setTimeout(() => {
          throttleTimeoutRef.current = null;
          lastFlushTimeRef.current = Date.now();
          setStreamRenderItems([...streamItemsRef.current]);
        }, STREAM_UPDATE_THROTTLE_MS - (now - lastFlushTimeRef.current));
      }
    };

    return {
      flush,
      cancel: () => {
        if (throttleTimeoutRef.current) {
          clearTimeout(throttleTimeoutRef.current);
          throttleTimeoutRef.current = null;
        }
      },
      forceFlush: () => {
        if (throttleTimeoutRef.current) {
          clearTimeout(throttleTimeoutRef.current);
          throttleTimeoutRef.current = null;
        }
        lastFlushTimeRef.current = Date.now();
        setStreamRenderItems([...streamItemsRef.current]);
      },
    };
  }, []);

  const updateStreamItems = useCallback(
    (updater: (prev: StreamRenderItem[]) => StreamRenderItem[]) => {
      streamItemsRef.current = updater(streamItemsRef.current);
      flushStreamItems.flush();
    },
    [flushStreamItems],
  );

  const forceFlushStreamItems = useCallback(() => {
    flushStreamItems.forceFlush();
  }, [flushStreamItems]);

  const clearStreamItems = useCallback(() => {
    streamItemsRef.current = [];
    flushStreamItems.cancel();
    setStreamRenderItems([]);
  }, [flushStreamItems]);

  // Clear pending throttle timer on unmount so a queued flush cannot touch state after unmount.
  useEffect(() => {
    return () => {
      if (throttleTimeoutRef.current) {
        clearTimeout(throttleTimeoutRef.current);
        throttleTimeoutRef.current = null;
      }
    };
  }, []);

  return {
    streamRenderItems,
    getStreamItemsSnapshot,
    updateStreamItems,
    clearStreamItems,
    forceFlushStreamItems,
  };
}
