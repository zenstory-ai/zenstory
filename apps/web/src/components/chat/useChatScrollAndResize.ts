/**
 * Auto-scroll, 5s post-history ResizeObserver settle, JumpToLatest visibility,
 * and desktop pointer-drag input panel resize with localStorage persistence.
 *
 * @module components/chat/useChatScrollAndResize
 */

import type React from "react";
import { useCallback, useEffect, useRef, useState, type MutableRefObject } from "react";
import type { Message, MessageListRef } from "../MessageList";
import { useAnimatedPresence } from "../../hooks/useAnimatedPresence";
import { clampNumber, isNearBottom } from "./chatHistoryHelpers";

const CHAT_INPUT_PANEL_HEIGHT_KEY = "zenstory_chat_input_panel_height_px";
const CHAT_INPUT_PANEL_MIN_HEIGHT_PX = 188;
export const CHAT_INPUT_PANEL_MAX_HEIGHT_PX = 520;
const CHAT_INPUT_PANEL_MAX_RATIO = 0.45;
const CHAT_INPUT_PANEL_MIN_MESSAGES_PX = 160;
// 历史加载完后这么久之内，列表变高（懒加载的 Markdown 渲染完）还继续贴底。
const HISTORY_SETTLE_FOLLOW_MS = 5000;

export interface UseChatScrollAndResizeOptions {
  currentProjectId: string | null;
  messagesRef: MutableRefObject<Message[]>;
  messagesLength: number;
  streamContent: string;
  streamRenderItemCount: number;
  isStreaming: boolean;
  isThinking: boolean;
  isLoadingHistory: boolean;
}

export function useChatScrollAndResize({
  currentProjectId,
  messagesRef,
  messagesLength,
  streamContent,
  streamRenderItemCount,
  isStreaming,
  isThinking,
  isLoadingHistory,
}: UseChatScrollAndResizeOptions) {
  const chatPanelRef = useRef<HTMLDivElement>(null);
  const headerRef = useRef<HTMLDivElement>(null);
  const inputPanelRef = useRef<HTMLDivElement>(null);
  const messageListRef = useRef<MessageListRef>(null);
  const messagesScrollContainerRef = useRef<HTMLDivElement>(null);

  const shouldAutoScrollRef = useRef(true);
  const followLoadedHistoryUntilRef = useRef(0);
  const autoScrollFrameRef = useRef<number | null>(null);
  const [showJumpToLatest, setShowJumpToLatest] = useState(false);
  const { isMounted: isJumpToLatestMounted, isExiting: isJumpToLatestExiting } = useAnimatedPresence(
    showJumpToLatest,
    130,
  );

  const resizeDragRef = useRef<{ startY: number; startHeight: number } | null>(null);
  const [inputPanelHeight, setInputPanelHeight] = useState<number | null>(() => {
    const saved = localStorage.getItem(CHAT_INPUT_PANEL_HEIGHT_KEY);
    const height = saved ? Number(saved) : Number.NaN;
    if (!Number.isFinite(height)) return null;
    return clampNumber(height, CHAT_INPUT_PANEL_MIN_HEIGHT_PX, CHAT_INPUT_PANEL_MAX_HEIGHT_PX);
  });
  const latestInputPanelHeightRef = useRef<number | null>(inputPanelHeight);
  useEffect(() => {
    latestInputPanelHeightRef.current = inputPanelHeight;
  }, [inputPanelHeight]);

  // Auto-scroll to bottom when new messages arrive or streaming content meaningfully changes.
  useEffect(() => {
    if (shouldAutoScrollRef.current) {
      if (autoScrollFrameRef.current !== null) {
        cancelAnimationFrame(autoScrollFrameRef.current);
      }
      autoScrollFrameRef.current = requestAnimationFrame(() => {
        autoScrollFrameRef.current = null;
        messageListRef.current?.scrollToBottom(false);
      });
      return;
    }
    // eslint-disable-next-line react-hooks/set-state-in-effect -- show jump-to-latest pill when new content arrives while scrolled up.
    setShowJumpToLatest(true);
  }, [messagesLength, streamContent, streamRenderItemCount, isStreaming, isThinking]);

  useEffect(() => {
    return () => {
      if (autoScrollFrameRef.current !== null) {
        cancelAnimationFrame(autoScrollFrameRef.current);
      }
    };
  }, []);

  useEffect(() => {
    const scrollEl = messagesScrollContainerRef.current;
    if (!scrollEl) return;

    const handleScroll = () => {
      const nearBottom = isNearBottom(scrollEl);
      shouldAutoScrollRef.current = nearBottom;
      if (nearBottom) {
        setShowJumpToLatest(false);
      } else if (
        messagesRef.current.length > 0 &&
        scrollEl.scrollHeight - scrollEl.scrollTop - scrollEl.clientHeight > 120
      ) {
        setShowJumpToLatest(true);
      }
    };

    scrollEl.addEventListener("scroll", handleScroll, { passive: true });
    handleScroll();
    return () => {
      scrollEl.removeEventListener("scroll", handleScroll);
    };
  }, [currentProjectId, messagesRef]);

  useEffect(() => {
    if (!isLoadingHistory) {
      followLoadedHistoryUntilRef.current = performance.now() + HISTORY_SETTLE_FOLLOW_MS;
    }
  }, [isLoadingHistory]);

  useEffect(() => {
    const scrollEl = messagesScrollContainerRef.current;
    const contentEl = scrollEl?.firstElementChild;
    if (!scrollEl || !contentEl || typeof ResizeObserver === "undefined") {
      return;
    }
    const stopFollowing = () => {
      followLoadedHistoryUntilRef.current = 0;
    };
    const observer = new ResizeObserver((entries) => {
      if (!shouldAutoScrollRef.current) return;
      const listResized = entries.some((entry) => entry.target === scrollEl);
      if (listResized || performance.now() < followLoadedHistoryUntilRef.current) {
        messageListRef.current?.scrollToBottom(false);
      }
    });
    observer.observe(contentEl);
    observer.observe(scrollEl);
    const interactions = ["wheel", "touchstart", "pointerdown", "keydown"] as const;
    for (const type of interactions) {
      scrollEl.addEventListener(type, stopFollowing, { passive: true });
    }
    return () => {
      observer.disconnect();
      for (const type of interactions) {
        scrollEl.removeEventListener(type, stopFollowing);
      }
    };
  }, [currentProjectId]);

  const handleJumpToLatest = useCallback(() => {
    shouldAutoScrollRef.current = true;
    setShowJumpToLatest(false);
    messageListRef.current?.scrollToBottom(true);
  }, []);

  const followLatestMessage = useCallback(() => {
    shouldAutoScrollRef.current = true;
    setShowJumpToLatest(false);
    if (autoScrollFrameRef.current !== null) {
      cancelAnimationFrame(autoScrollFrameRef.current);
    }
    autoScrollFrameRef.current = requestAnimationFrame(() => {
      autoScrollFrameRef.current = null;
      messageListRef.current?.scrollToBottom(false);
    });
  }, []);

  const resetScrollState = useCallback(() => {
    shouldAutoScrollRef.current = true;
    setShowJumpToLatest(false);
  }, []);

  const getMaxInputPanelHeight = useCallback(() => {
    const rootEl = chatPanelRef.current;
    if (!rootEl) return CHAT_INPUT_PANEL_MAX_HEIGHT_PX;

    const rootHeight = rootEl.getBoundingClientRect().height;
    const headerHeight = headerRef.current?.getBoundingClientRect().height || 48;

    const maxByLayout = rootHeight - headerHeight - CHAT_INPUT_PANEL_MIN_MESSAGES_PX;
    const maxByRatio = rootHeight * CHAT_INPUT_PANEL_MAX_RATIO;

    const max = Math.min(CHAT_INPUT_PANEL_MAX_HEIGHT_PX, maxByLayout, maxByRatio);
    const safeMax = Math.max(max, CHAT_INPUT_PANEL_MIN_HEIGHT_PX);

    return clampNumber(safeMax, CHAT_INPUT_PANEL_MIN_HEIGHT_PX, CHAT_INPUT_PANEL_MAX_HEIGHT_PX);
  }, []);

  const handleInputPanelResizeStart = (e: React.PointerEvent<HTMLDivElement>) => {
    const panelEl = inputPanelRef.current;
    if (!panelEl) return;

    const startHeight = panelEl.getBoundingClientRect().height;
    resizeDragRef.current = { startY: e.clientY, startHeight };

    e.currentTarget.setPointerCapture(e.pointerId);

    if (inputPanelHeight === null) {
      const maxHeight = getMaxInputPanelHeight();
      setInputPanelHeight(clampNumber(startHeight, CHAT_INPUT_PANEL_MIN_HEIGHT_PX, maxHeight));
    }
  };

  const handleInputPanelResizeMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const drag = resizeDragRef.current;
    if (!drag) return;

    const deltaY = e.clientY - drag.startY;
    const maxHeight = getMaxInputPanelHeight();
    const nextHeight = clampNumber(
      drag.startHeight - deltaY,
      CHAT_INPUT_PANEL_MIN_HEIGHT_PX,
      maxHeight,
    );

    setInputPanelHeight(nextHeight);
  };

  const handleInputPanelResizeEnd = () => {
    if (!resizeDragRef.current) return;
    resizeDragRef.current = null;

    const height = latestInputPanelHeightRef.current;
    if (height === null) return;
    localStorage.setItem(CHAT_INPUT_PANEL_HEIGHT_KEY, String(Math.round(height)));
  };

  const resetInputPanelHeight = () => {
    setInputPanelHeight(null);
    localStorage.removeItem(CHAT_INPUT_PANEL_HEIGHT_KEY);
  };

  useEffect(() => {
    const clampToMax = () => {
      const h = latestInputPanelHeightRef.current;
      if (h === null) return;
      const max = getMaxInputPanelHeight();
      if (h > max) setInputPanelHeight(max);
    };

    clampToMax();
    window.addEventListener("resize", clampToMax);
    return () => window.removeEventListener("resize", clampToMax);
  }, [getMaxInputPanelHeight]);

  return {
    chatPanelRef,
    headerRef,
    inputPanelRef,
    messageListRef,
    messagesScrollContainerRef,
    shouldAutoScrollRef,
    isJumpToLatestMounted,
    isJumpToLatestExiting,
    resizeDragRef,
    inputPanelHeight,
    handleJumpToLatest,
    followLatestMessage,
    resetScrollState,
    handleInputPanelResizeStart,
    handleInputPanelResizeMove,
    handleInputPanelResizeEnd,
    resetInputPanelHeight,
  };
}
