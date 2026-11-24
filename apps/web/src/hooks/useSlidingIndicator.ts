import { useCallback, useEffect, useLayoutEffect, useRef } from "react";

interface RectMetrics {
  x: number;
  y: number;
  width: number;
  height: number;
}

/**
 * Measures the active segmented pill / tab (`[data-segment-active="true"]`) inside a
 * relatively positioned container and drives a single GPU-composited sliding pill via
 * `transform: translate3d(x, y, 0)` directly on `indicatorRef` with zero extra React renders.
 *
 * - Snaps cleanly (`data-animate="false"`) on initial mount, DOM remount (e.g. reopened modals),
 *   container resize, and layout-only changes (locale/breakpoint switches) — never flying in from (0, 0).
 * - Glides smoothly (`data-animate="true"`) ONLY when `activeKey` changes on the same mounted pill.
 * - Never tears down or recreates `ResizeObserver` on `activeKey` changes, and ignores unchanged
 *   geometry deliveries so initial ResizeObserver callbacks cannot abort an active glide.
 * - Marks `data-indicator-ready="true"` on the container once real layout metrics exist,
 *   allowing static active backgrounds to stay active in zero-layout environments like happy-dom/jsdom.
 */
export function useSlidingIndicator<T extends HTMLElement = HTMLDivElement>(
  activeKey: string | number,
  layoutDeps: ReadonlyArray<unknown> = [],
) {
  const containerRef = useRef<T | null>(null);
  const indicatorRef = useRef<HTMLSpanElement | null>(null);
  const measuredPillRef = useRef<HTMLSpanElement | null>(null);
  const prevActiveKeyRef = useRef<string | number>(activeKey);
  const activeKeyRef = useRef<string | number>(activeKey);
  const lastRectRef = useRef<RectMetrics | null>(null);
  const observerRef = useRef<ResizeObserver | null>(null);
  const observedContainerRef = useRef<T | null>(null);

  const measure = useCallback((allowAnimate: boolean, currentKey: string | number) => {
    const container = containerRef.current;
    const pill = indicatorRef.current;
    if (!container || !pill) return;

    const activeEl = container.querySelector<HTMLElement>('[data-segment-active="true"]');
    if (!activeEl) {
      pill.style.opacity = "0";
      delete container.dataset.indicatorReady;
      measuredPillRef.current = null;
      lastRectRef.current = null;
      return;
    }

    const width = activeEl.offsetWidth;
    const height = activeEl.offsetHeight;
    if (width <= 0 || height <= 0) {
      pill.style.opacity = "0";
      delete container.dataset.indicatorReady;
      measuredPillRef.current = null;
      lastRectRef.current = null;
      return;
    }

    const x = activeEl.offsetLeft;
    const y = activeEl.offsetTop;
    const isSamePill = pill === measuredPillRef.current;
    const lastRect = lastRectRef.current;

    // ResizeObserver delivers an initial notification immediately after observe();
    // ignore resize callbacks when layout geometry has not changed so active glides are never aborted.
    if (
      !allowAnimate &&
      isSamePill &&
      lastRect &&
      lastRect.x === x &&
      lastRect.y === y &&
      lastRect.width === width &&
      lastRect.height === height
    ) {
      return;
    }

    const shouldAnimate = allowAnimate && isSamePill && prevActiveKeyRef.current !== currentKey;
    measuredPillRef.current = pill;
    prevActiveKeyRef.current = currentKey;
    lastRectRef.current = { x, y, width, height };

    pill.dataset.animate = shouldAnimate ? "true" : "false";
    pill.style.width = `${width}px`;
    pill.style.height = `${height}px`;
    pill.style.transform = `translate3d(${x}px, ${y}px, 0)`;
    pill.style.opacity = "1";
    container.dataset.indicatorReady = "true";
  }, []);

  const layoutSignature = layoutDeps.map(String).join("|");

  useLayoutEffect(() => {
    activeKeyRef.current = activeKey;
    measure(true, activeKey);

    // Attach ResizeObserver once per mounted container node; never recreate on activeKey switches.
    const container = containerRef.current;
    if (!container) {
      observerRef.current?.disconnect();
      observerRef.current = null;
      observedContainerRef.current = null;
      return;
    }

    if (typeof ResizeObserver !== "undefined" && observedContainerRef.current !== container) {
      observerRef.current?.disconnect();
      const observer = new ResizeObserver(() => {
        measure(false, activeKeyRef.current);
      });
      observer.observe(container);
      observerRef.current = observer;
      observedContainerRef.current = container;
    }
  }, [activeKey, layoutSignature, measure]);

  useEffect(() => {
    const handleWindowResize = () => {
      measure(false, activeKeyRef.current);
    };
    window.addEventListener("resize", handleWindowResize);
    return () => {
      window.removeEventListener("resize", handleWindowResize);
      observerRef.current?.disconnect();
      observerRef.current = null;
      observedContainerRef.current = null;
    };
  }, [measure]);

  return { containerRef, indicatorRef };
}
