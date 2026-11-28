import { useCallback, useEffect, useRef } from "react";
import type { RefObject } from "react";

export const SIMPLE_EDITOR_MIN_HEIGHT_PX = 200;

export const isNearBottom = (el: HTMLElement, thresholdPx = 32) => {
  return el.scrollHeight - el.scrollTop - el.clientHeight < thresholdPx;
};

const restoreContainerScrollTop = (container: HTMLElement | null, prevScrollTop: number | null) => {
  if (!container || prevScrollTop === null) return;

  const restore = () => {
    const maxTop = Math.max(0, container.scrollHeight - container.clientHeight);
    container.scrollTop = Math.min(prevScrollTop, maxTop);
  };

  restore();
  requestAnimationFrame(restore);
};

interface UseEditorTextareaLayoutOptions {
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  contentAreaRef: RefObject<HTMLDivElement | null>;
  displayContent: string;
  isStreaming: boolean;
  isReviewMode?: boolean;
  zoom?: number;
}

export function useEditorTextareaLayout({
  textareaRef,
  contentAreaRef,
  displayContent,
  isStreaming,
  isReviewMode = false,
  zoom = 1,
}: UseEditorTextareaLayoutOptions) {
  const shouldAutoScrollRef = useRef(true);
  const lastWidthRef = useRef<number | null>(null);
  const prevZoomRef = useRef(zoom);

  // Auto-resize textarea
  // NOTE: Avoid a full `height="auto"` reflow on every keystroke.
  // That reintroduces the "jump back to editor top" bug for hardware keyboard input.
  // While the textarea is focused we only grow; when focus leaves (or on file switches)
  // we do a full recalculation so the editor can shrink safely.
  const adjustTextareaHeight = useCallback(
    (force = false) => {
      const textarea = textareaRef.current;
      if (!textarea) return;

      const container = contentAreaRef.current;
      const prevScrollTop = container ? container.scrollTop : null;
      const currentHeightPx =
        Number.parseFloat(textarea.style.height || "0") || SIMPLE_EDITOR_MIN_HEIGHT_PX;
      const isFocused = document.activeElement === textarea;

      let nextHeightPx = Math.max(textarea.scrollHeight, SIMPLE_EDITOR_MIN_HEIGHT_PX);

      if (force || !isFocused) {
        const previousHeight = textarea.style.height;
        const previousMinHeight = textarea.style.minHeight;
        textarea.style.height = "0px";
        textarea.style.minHeight = "0";
        nextHeightPx = Math.max(textarea.scrollHeight, SIMPLE_EDITOR_MIN_HEIGHT_PX);
        textarea.style.minHeight = previousMinHeight;
        textarea.style.height = previousHeight;
      } else if (nextHeightPx < currentHeightPx) {
        nextHeightPx = currentHeightPx;
      }

      if (force || Math.abs(nextHeightPx - currentHeightPx) > 1) {
        textarea.style.height = `${nextHeightPx}px`;
      }

      restoreContainerScrollTop(container, prevScrollTop);
    },
    [contentAreaRef, textareaRef]
  );

  useEffect(() => {
    const isFocused = document.activeElement === textareaRef.current;
    adjustTextareaHeight(!isFocused);
  }, [displayContent, adjustTextareaHeight, textareaRef]);

  // Re-measure when zoom changes (e.g. pinch zoom out or reset) so stale tall heights shrink.
  useEffect(() => {
    if (prevZoomRef.current === zoom) return;
    prevZoomRef.current = zoom;
    adjustTextareaHeight(true);
  }, [zoom, adjustTextareaHeight]);

  // Re-measure when editor width changes (sidebar toggle, pane/viewport resize) without looping on height changes.
  // Re-binds when exiting review mode because the textarea unmounts while DiffReviewSplitView is shown.
  useEffect(() => {
    if (isReviewMode) {
      lastWidthRef.current = null;
      return;
    }
    const textarea = textareaRef.current;
    if (!textarea || typeof ResizeObserver === "undefined") return;

    lastWidthRef.current = textarea.getBoundingClientRect().width || textarea.clientWidth;

    const observer = new ResizeObserver((entries) => {
      const entry = entries[entries.length - 1];
      if (!entry) return;
      const nextWidth = entry.contentRect.width;
      if (nextWidth <= 0) {
        lastWidthRef.current = 0;
        return;
      }
      const prevWidth = lastWidthRef.current;
      if (prevWidth === null || Math.abs(nextWidth - prevWidth) > 0.5) {
        lastWidthRef.current = nextWidth;
        // Avoid forced 0px height collapse when a focused textarea narrows (e.g. scrollbar appearing while typing).
        const shouldForceShrink =
          prevWidth === null || nextWidth > prevWidth || document.activeElement !== textarea;
        adjustTextareaHeight(shouldForceShrink);
      }
    });

    observer.observe(textarea);
    return () => observer.disconnect();
  }, [adjustTextareaHeight, isReviewMode, textareaRef]);

  const handleContentScroll = useCallback(() => {
    const el = contentAreaRef.current;
    if (!el) return;
    shouldAutoScrollRef.current = isNearBottom(el);
  }, [contentAreaRef]);

  // Auto-scroll to bottom when streaming (only if user is already near bottom)
  useEffect(() => {
    const el = contentAreaRef.current;
    if (!el) return;

    if (isStreaming) {
      if (shouldAutoScrollRef.current || isNearBottom(el)) {
        requestAnimationFrame(() => {
          el.scrollTop = el.scrollHeight;
        });
      }
    }
  }, [displayContent, isStreaming, contentAreaRef]);

  const resetAutoScroll = useCallback(() => {
    shouldAutoScrollRef.current = true;
  }, []);

  return {
    adjustTextareaHeight,
    handleContentScroll,
    resetAutoScroll,
  };
}
