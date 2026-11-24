import { useCallback, useLayoutEffect, useRef } from "react";

/**
 * Measures the active segmented pill / tab (`[data-segment-active="true"]`) inside a
 * relatively positioned container and drives a single GPU-composited sliding pill via
 * `transform: translate3d(x, y, 0)` directly on `indicatorRef` with zero extra React renders.
 *
 * - Snaps cleanly on initial mount and container resize (`data-animate="false"`),
 *   so there is never a fly-in from (0, 0).
 * - Glides smoothly on active-selection changes (`data-animate="true"`).
 * - Marks `data-indicator-ready="true"` on the container once real layout metrics exist,
 *   allowing static active backgrounds to stay active in zero-layout environments like jsdom.
 */
export function useSlidingIndicator<T extends HTMLElement = HTMLDivElement>(
  activeKey: string | number,
  layoutDeps: ReadonlyArray<unknown> = [],
) {
  const containerRef = useRef<T | null>(null);
  const indicatorRef = useRef<HTMLSpanElement | null>(null);
  const hasMeasuredRef = useRef(false);

  const measure = useCallback((shouldAnimate: boolean) => {
    const container = containerRef.current;
    const pill = indicatorRef.current;
    if (!container || !pill) return;

    const activeEl = container.querySelector<HTMLElement>('[data-segment-active="true"]');
    if (!activeEl) {
      pill.style.opacity = "0";
      delete container.dataset.indicatorReady;
      return;
    }

    const width = activeEl.offsetWidth;
    const height = activeEl.offsetHeight;
    if (width <= 0 || height <= 0) {
      pill.style.opacity = "0";
      delete container.dataset.indicatorReady;
      return;
    }

    const x = activeEl.offsetLeft;
    const y = activeEl.offsetTop;
    const animate = shouldAnimate && hasMeasuredRef.current;
    hasMeasuredRef.current = true;

    pill.dataset.animate = animate ? "true" : "false";
    pill.style.width = `${width}px`;
    pill.style.height = `${height}px`;
    pill.style.transform = `translate3d(${x}px, ${y}px, 0)`;
    pill.style.opacity = "1";
    container.dataset.indicatorReady = "true";
  }, []);

  const layoutSignature = layoutDeps.map(String).join("|");

  useLayoutEffect(() => {
    measure(true);
  }, [activeKey, layoutSignature, measure]);

  useLayoutEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const handleResize = () => measure(false);

    if (typeof ResizeObserver !== "undefined") {
      const observer = new ResizeObserver(handleResize);
      observer.observe(container);
      const activeEl = container.querySelector<HTMLElement>('[data-segment-active="true"]');
      if (activeEl) observer.observe(activeEl);
      return () => observer.disconnect();
    }

    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, [activeKey, layoutSignature, measure]);

  return { containerRef, indicatorRef };
}
