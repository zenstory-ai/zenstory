import { useCallback, useEffect, useRef, useState } from "react";
import type { RefObject } from "react";
import { useAnimatedPresence } from "../../hooks/useAnimatedPresence";

interface UseEditorSelectionOptions {
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  fileId?: string;
  fileTitle?: string;
  title: string;
  isMobile: boolean;
  activePanel: string;
  addQuote: (text: string, fileId: string, title: string) => void;
}

export function useEditorSelection({
  textareaRef,
  fileId,
  fileTitle,
  title,
  isMobile,
  activePanel,
  addQuote,
}: UseEditorSelectionOptions) {
  const [selectedText, setSelectedText] = useState("");
  const [selectionPosition, setSelectionPosition] = useState<{ x: number; y: number } | null>(null);
  const mirrorRef = useRef<HTMLDivElement>(null);
  const selectionTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const selectionRangeRef = useRef<{ start: number; end: number } | null>(null);

  const isSelectionToolbarOpen = Boolean(selectedText && selectionPosition && fileId);
  const { isMounted: isSelectionToolbarMounted } = useAnimatedPresence(isSelectionToolbarOpen, 120);

  /** Forget the selection the toolbar and 去AI味 would act on. */
  const clearSelectionState = useCallback(() => {
    if (selectionTimeoutRef.current) {
      clearTimeout(selectionTimeoutRef.current);
      selectionTimeoutRef.current = null;
    }
    setSelectedText("");
    setSelectionPosition(null);
    selectionRangeRef.current = null;
  }, []);

  /**
   * The selection is done with (quoted to chat, or the mobile editor panel was left): collapse
   * it in the textarea too, so a later 去AI味 never acts on text the author no longer sees selected.
   */
  const dropSelection = useCallback(() => {
    const textarea = textareaRef.current;
    if (textarea && textarea.selectionStart !== textarea.selectionEnd) {
      textarea.setSelectionRange(textarea.selectionEnd, textarea.selectionEnd);
    }
    clearSelectionState();
  }, [clearSelectionState, textareaRef]);

  // Calculate selection position using mirror div technique
  const getSelectionPosition = useCallback(() => {
    const textarea = textareaRef.current;
    const mirror = mirrorRef.current;
    if (!textarea || !mirror) return null;

    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;
    if (start === end) return null;

    const styles = window.getComputedStyle(textarea);
    mirror.style.cssText = `
      position: absolute;
      visibility: hidden;
      white-space: pre-wrap;
      word-wrap: break-word;
      overflow-wrap: break-word;
      width: ${styles.width};
      font: ${styles.font};
      padding: ${styles.padding};
      border: ${styles.border};
      line-height: ${styles.lineHeight};
      letter-spacing: ${styles.letterSpacing};
    `;

    const textBefore = textarea.value.substring(0, start);
    const currentSelected = textarea.value.substring(start, end);

    mirror.innerHTML = "";
    mirror.appendChild(document.createTextNode(textBefore));
    const span = document.createElement("span");
    span.textContent = currentSelected;
    mirror.appendChild(span);

    const textareaRect = textarea.getBoundingClientRect();
    const spanRect = span.getBoundingClientRect();
    const mirrorRect = mirror.getBoundingClientRect();

    const x = textareaRect.left + (spanRect.left - mirrorRect.left) + spanRect.width / 2;
    const y = textareaRect.top + (spanRect.top - mirrorRect.top) - textarea.scrollTop - 8;

    return { x, y };
  }, [textareaRef]);

  const handleSelection = useCallback(
    (target?: HTMLTextAreaElement | null) => {
      if (selectionTimeoutRef.current) {
        clearTimeout(selectionTimeoutRef.current);
      }

      const textarea = target ?? textareaRef.current;
      if (!textarea) return;

      const start = textarea.selectionStart;
      const end = textarea.selectionEnd;
      const text = textarea.value.substring(start, end).trim();

      if (!text) {
        setSelectedText("");
        setSelectionPosition(null);
        selectionRangeRef.current = null;
        return;
      }

      // Keep a "selection exists" signal immediate so action buttons can respond quickly.
      setSelectedText(text);
      selectionRangeRef.current = { start, end };

      // Delay showing toolbar by 300ms
      selectionTimeoutRef.current = setTimeout(() => {
        const position = getSelectionPosition();
        if (position) {
          setSelectionPosition(position);
        }
      }, 300);
    },
    [getSelectionPosition, textareaRef]
  );

  // Close selection toolbar without disturbing scroll or selection
  const closeSelectionToolbar = useCallback(
    (restoreFocus?: boolean) => {
      setSelectionPosition(null);
      if (restoreFocus) {
        textareaRef.current?.focus({ preventScroll: true });
      }
    },
    [textareaRef]
  );

  const handleAddQuote = useCallback(() => {
    if (!selectedText || !fileId) return;
    const displayTitle = fileTitle || title;
    addQuote(selectedText, fileId, displayTitle);
    // The quoted text is used; it is no longer the target of 去AI味.
    dropSelection();
  }, [selectedText, fileId, fileTitle, title, addQuote, dropSelection]);

  // Mobile keeps every panel mounted (hidden with inert), so a selection made before
  // switching to 文件 / AI would otherwise survive unseen and be what 去AI味 polishes.
  const panelHiddenOnMobile = isMobile && activePanel !== "editor";
  const [prevPanelHiddenOnMobile, setPrevPanelHiddenOnMobile] = useState(panelHiddenOnMobile);
  if (prevPanelHiddenOnMobile !== panelHiddenOnMobile) {
    setPrevPanelHiddenOnMobile(panelHiddenOnMobile);
    if (panelHiddenOnMobile) {
      setSelectedText("");
      setSelectionPosition(null);
    }
  }
  useEffect(() => {
    if (!panelHiddenOnMobile) return;
    if (selectionTimeoutRef.current) {
      clearTimeout(selectionTimeoutRef.current);
      selectionTimeoutRef.current = null;
    }
    selectionRangeRef.current = null;
    const textarea = textareaRef.current;
    if (textarea && textarea.selectionStart !== textarea.selectionEnd) {
      textarea.setSelectionRange(textarea.selectionEnd, textarea.selectionEnd);
    }
  }, [panelHiddenOnMobile, textareaRef]);

  // Cleanup selection timeout
  useEffect(() => {
    return () => {
      if (selectionTimeoutRef.current) {
        clearTimeout(selectionTimeoutRef.current);
      }
    };
  }, []);

  return {
    selectedText,
    selectionPosition,
    mirrorRef,
    selectionRangeRef,
    isSelectionToolbarOpen,
    isSelectionToolbarMounted,
    clearSelectionState,
    dropSelection,
    handleSelection,
    closeSelectionToolbar,
    handleAddQuote,
  };
}
