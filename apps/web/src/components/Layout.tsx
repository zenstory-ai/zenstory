/**
 * @fileoverview Layout component - Main application layout with responsive three-panel design.
 *
 * This component provides the primary layout structure for the application, featuring:
 * - Desktop: Three-panel layout (Sidebar, Editor, Chat) with resizable panels
 * - Mobile: Single-panel layout with bottom tab navigation and swipe gestures
 * - Global keyboard shortcuts (Cmd/Ctrl+K for file search)
 * - Scroll position memory for mobile panel switching
 * - Panel state preservation across navigation
 *
 * The layout automatically adapts between desktop and mobile views based on screen size,
 * using the useIsMobile hook for responsive behavior.
 *
 * @module components/Layout
 */
import React, { lazy, Suspense, useEffect, useLayoutEffect, useRef, useCallback, useState } from "react";
import { Panel, Group, Separator, type GroupImperativeHandle } from "react-resizable-panels";
import { Loader2 } from "lucide-react";
import { Header } from "./Header";
import { BottomTabs } from "./BottomTabs";
import type { MobilePanel } from "./BottomTabs";
import { Sidebar } from "./sidebar/Sidebar";

const MobileFileTree = lazy(() =>
  import("./MobileFileTree").then((m) => ({ default: m.MobileFileTree })),
);
import { useIsMobile, useIsTablet } from "../hooks/useMediaQuery";
import { useScrollMemory } from "../hooks/useScrollMemory";
import { useSwipeGestures } from "../hooks/useGestures";
import {
  MobileLayoutProvider,
  useMobileLayout,
} from "../contexts/MobileLayoutContext";
import { useFileSearchContext } from "../contexts/FileSearchContext";
import { useProject } from "../contexts/ProjectContext";
import { hasPendingInspiration, takeChatOnOpen } from "../lib/pendingInspiration";

/**
 * Props for the Layout component and its internal layouts.
 *
 * @interface LayoutProps
 */
interface LayoutProps {
  /**
   * Content for the middle panel (Editor).
   * Contains the main content editing interface.
   */
  middle: React.ReactNode;

  /**
   * Content for the right panel (Chat).
   * Contains the AI chat interface for conversations.
   */
  right: React.ReactNode;
}

const MOBILE_KEYBOARD_HEIGHT_THRESHOLD_PX = 120;
const MOBILE_VIEWPORT_NOISE_PX = 16;
const MOBILE_BOTTOM_TABS_HEIGHT_PX = 56;
const MOBILE_PANEL_ORDER: MobilePanel[] = ["files", "editor", "chat"];

const isEditableActiveElement = (element: Element | null): boolean => {
  if (!(element instanceof HTMLElement)) return false;
  if (element.isContentEditable) return true;

  const tagName = element.tagName;
  if (tagName === "TEXTAREA") return true;
  if (tagName !== "INPUT") return false;

  const inputType = (element as HTMLInputElement).type;
  const nonTextTypes = new Set(["button", "checkbox", "radio", "range", "submit", "reset", "file", "image"]);
  return !nonTextTypes.has(inputType);
};

type SplitLayoutVariant = "desktop" | "tablet";
type LayoutMode = "mobile" | SplitLayoutVariant;

const SPLIT_PANEL_IDS = {
  sidebar: "workbench-sidebar",
  editor: "workbench-editor",
  chat: "workbench-chat",
} as const;

interface SplitLayoutPreset {
  containerClassName: string;
  sidebar: { defaultSize: string; minSize: number | string };
  editor: { defaultSize: string; minSize: string };
  chat: { defaultSize: string; minSize: number };
  layout: Record<string, number>;
}

const SPLIT_LAYOUT_PRESETS: Record<SplitLayoutVariant, SplitLayoutPreset> = {
  desktop: {
    containerClassName:
      "h-dvh overflow-hidden bg-[hsl(var(--bg-primary))] text-[hsl(var(--text-primary))] flex flex-col fixed inset-0",
    sidebar: { defaultSize: "20%", minSize: 180 },
    editor: { defaultSize: "48%", minSize: "30%" },
    chat: { defaultSize: "32%", minSize: 300 },
    layout: {
      [SPLIT_PANEL_IDS.sidebar]: 20,
      [SPLIT_PANEL_IDS.editor]: 48,
      [SPLIT_PANEL_IDS.chat]: 32,
    },
  },
  tablet: {
    containerClassName:
      "viewport-shell h-dvh overflow-hidden bg-[hsl(var(--bg-primary))] text-[hsl(var(--text-primary))] flex flex-col fixed inset-0",
    sidebar: { defaultSize: "25%", minSize: "20%" },
    editor: { defaultSize: "40%", minSize: "30%" },
    chat: { defaultSize: "35%", minSize: 280 },
    layout: {
      [SPLIT_PANEL_IDS.sidebar]: 25,
      [SPLIT_PANEL_IDS.editor]: 40,
      [SPLIT_PANEL_IDS.chat]: 35,
    },
  },
};

const MOBILE_CONTAINER_CLASS =
  "viewport-shell h-dvh overflow-hidden bg-[hsl(var(--bg-primary))] text-[hsl(var(--text-primary))] flex flex-col fixed inset-0";
const MOBILE_PANEL_BASE_CLASS =
  "absolute inset-x-0 top-0 bg-[hsl(var(--bg-primary))] transition-opacity duration-[var(--duration-fast)] ease-[var(--ease-standard)] motion-reduce:transition-none";
const SEPARATOR_CLASS = "panel-resize-handle";

const getMobilePanelClassName = (isActive: boolean, overflowClass: "overflow-auto" | "overflow-hidden"): string =>
  `${MOBILE_PANEL_BASE_CLASS} ${overflowClass} ${
    isActive ? "opacity-100 z-10" : "opacity-0 z-0 pointer-events-none"
  }`;

/**
 * Unified responsive workbench layout for mobile (< 768px), tablet (768px-1023px),
 * and desktop (>= 1024px).
 *
 * Keeps a single stable React tree (`Group > Panel > wrapper`) across every breakpoint
 * so resizing or rotating across 768px or 1024px never unmounts `middle` (Editor) or
 * `right` (ChatPanel) and never aborts an active AI stream.
 */
const WorkbenchLayoutContent: React.FC<
  LayoutProps & { isMobile: boolean; splitVariant: SplitLayoutVariant }
> = ({ isMobile, splitVariant, middle, right }) => {
  const { activePanel, setActivePanel } = useMobileLayout();
  const { currentProjectId } = useProject();
  const { saveScrollPosition, restoreScrollPosition } = useScrollMemory(currentProjectId || "default");
  const [isMobileKeyboardOpen, setIsMobileKeyboardOpen] = useState(false);
  const groupRef = useRef<GroupImperativeHandle | null>(null);
  const filesPanelRef = useRef<HTMLDivElement>(null);
  const editorPanelRef = useRef<HTMLDivElement>(null);
  const chatPanelRef = useRef<HTMLDivElement>(null);

  const preset = SPLIT_LAYOUT_PRESETS[splitVariant];
  const layoutMode: LayoutMode = isMobile ? "mobile" : splitVariant;
  const previousLayoutModeRef = useRef<LayoutMode>(layoutMode);
  const mobileKeyboardOpen = isMobile && isMobileKeyboardOpen;

  // Apply tablet/desktop default panel proportions imperatively when crossing
  // breakpoints (mobile -> split or tablet <-> desktop) without remounting Group.
  useEffect(() => {
    const previousMode = previousLayoutModeRef.current;
    previousLayoutModeRef.current = layoutMode;

    if (isMobile || previousMode === layoutMode) {
      return;
    }

    try {
      groupRef.current?.setLayout(SPLIT_LAYOUT_PRESETS[splitVariant].layout);
    } catch {
      // Group imperative handle can be unavailable in detached/mocked trees.
    }
  }, [isMobile, layoutMode, splitVariant]);

  // Opened from the dashboard with an idea (the first message is about to be sent) or just
  // created there without one (the starter cards are the next step): land on the AI tab
  // instead of the empty editor.
  useLayoutEffect(() => {
    if (!isMobile) return;
    if (hasPendingInspiration(currentProjectId) || takeChatOnOpen(currentProjectId)) {
      setActivePanel("chat");
    }
  }, [currentProjectId, isMobile, setActivePanel]);

  const mobilePanelBottomInset = mobileKeyboardOpen
    ? "0px"
    : `calc(${MOBILE_BOTTOM_TABS_HEIGHT_PX}px + env(safe-area-inset-bottom, 0px))`;

  // Panel order for swipe navigation: files -> editor -> chat
  // Handle swipe gestures for panel switching
  const handleSwipe = useCallback(
    ({ direction }: { direction: "left" | "right" | "up" | "down" }) => {
      const currentIndex = MOBILE_PANEL_ORDER.indexOf(activePanel);

      if (direction === "left") {
        // Swipe left: go to next panel (files -> editor -> chat)
        const nextIndex = Math.min(currentIndex + 1, MOBILE_PANEL_ORDER.length - 1);
        setActivePanel(MOBILE_PANEL_ORDER[nextIndex]);
      } else if (direction === "right") {
        // Swipe right: go to previous panel (chat -> editor -> files)
        const prevIndex = Math.max(currentIndex - 1, 0);
        setActivePanel(MOBILE_PANEL_ORDER[prevIndex]);
      }
    },
    [activePanel, setActivePanel]
  );

  // Initialize swipe gesture detection
  const { bind } = useSwipeGestures(handleSwipe, {
    swipeThreshold: 75,
    swipeVelocity: 0.2,
    preventDefaultTouch: false, // Allow scrolling to work normally
  });

  // Track visual viewport changes on mobile to avoid keyboard/input overlap.
  useEffect(() => {
    if (!isMobile) return;

    const getViewportHeight = () =>
      window.visualViewport
        ? window.visualViewport.height * window.visualViewport.scale
        : window.innerHeight;
    let baselineViewportHeight = getViewportHeight();

    const updateMobileViewportState = () => {
      const viewportHeight = getViewportHeight();

      const heightDelta = baselineViewportHeight - viewportHeight;
      const keyboardLikelyOpen =
        isEditableActiveElement(document.activeElement) &&
        heightDelta > MOBILE_KEYBOARD_HEIGHT_THRESHOLD_PX;

      setIsMobileKeyboardOpen(keyboardLikelyOpen);

      // Refresh baseline once keyboard is closed or viewport stabilizes.
      if (!keyboardLikelyOpen && viewportHeight > baselineViewportHeight - MOBILE_VIEWPORT_NOISE_PX) {
        baselineViewportHeight = viewportHeight;
      }
    };

    updateMobileViewportState();

    const visualViewport = window.visualViewport;
    visualViewport?.addEventListener("resize", updateMobileViewportState);
    visualViewport?.addEventListener("scroll", updateMobileViewportState);
    window.addEventListener("resize", updateMobileViewportState);
    document.addEventListener("focusin", updateMobileViewportState);
    document.addEventListener("focusout", updateMobileViewportState);

    return () => {
      visualViewport?.removeEventListener("resize", updateMobileViewportState);
      visualViewport?.removeEventListener("scroll", updateMobileViewportState);
      window.removeEventListener("resize", updateMobileViewportState);
      document.removeEventListener("focusin", updateMobileViewportState);
      document.removeEventListener("focusout", updateMobileViewportState);
    };
  }, [isMobile]);

  // Restore scroll when panel becomes active on mobile
  useEffect(() => {
    if (!isMobile) return;
    const refs: Record<MobilePanel, React.RefObject<HTMLDivElement | null>> = {
      files: filesPanelRef,
      editor: editorPanelRef,
      chat: chatPanelRef,
    };
    restoreScrollPosition(activePanel, refs[activePanel]?.current || null);
  }, [activePanel, currentProjectId, isMobile, restoreScrollPosition]);

  const handleScroll = useCallback(
    (panelId: string) => (e: React.UIEvent<HTMLDivElement>) => {
      saveScrollPosition(panelId, e.currentTarget.scrollTop);
    },
    [saveScrollPosition]
  );

  const containerClassName = isMobile ? MOBILE_CONTAINER_CLASS : preset.containerClassName;
  const mainClassName = isMobile
    ? `flex-1 overflow-hidden min-h-0 ${mobileKeyboardOpen ? "pb-safe-or-2" : "pb-14 safe-area-bottom"}`
    : "flex-1 overflow-hidden min-h-0";

  return (
    <div className={containerClassName} data-testid="app-layout">
      <Header />

      <main className={mainClassName}>
        <Group
          groupRef={groupRef}
          orientation="horizontal"
          disabled={isMobile}
          disableCursor={isMobile}
          className={isMobile ? "h-full relative" : "h-full"}
          {...(isMobile ? bind() : undefined)}
        >
          <Panel
            id={SPLIT_PANEL_IDS.sidebar}
            defaultSize={isMobile ? undefined : preset.sidebar.defaultSize}
            minSize={isMobile ? "0%" : preset.sidebar.minSize}
          >
            <div
              id={isMobile ? "files-panel" : undefined}
              ref={filesPanelRef}
              onScroll={isMobile ? handleScroll("files") : undefined}
              className={
                isMobile
                  ? getMobilePanelClassName(activePanel === "files", "overflow-auto")
                  : "h-full overflow-hidden"
              }
              style={isMobile ? { bottom: mobilePanelBottomInset } : undefined}
              aria-hidden={isMobile ? activePanel !== "files" : undefined}
              aria-labelledby={isMobile ? "files-tab" : undefined}
              role={isMobile ? "tabpanel" : undefined}
              inert={isMobile && activePanel !== "files" ? true : undefined}
            >
              {isMobile ? (
                <Suspense
                  fallback={
                    <div className="h-full flex items-center justify-center text-[hsl(var(--text-secondary))]">
                      <Loader2 size={24} className="animate-spin" />
                    </div>
                  }
                >
                  <MobileFileTree />
                </Suspense>
              ) : (
                <Sidebar />
              )}
            </div>
          </Panel>

          {!isMobile && <Separator className={SEPARATOR_CLASS} />}

          <Panel
            id={SPLIT_PANEL_IDS.editor}
            defaultSize={isMobile ? undefined : preset.editor.defaultSize}
            minSize={isMobile ? "0%" : preset.editor.minSize}
          >
            {/* data-testid: editor-panel - Editor panel for content editing tests */}
            <div
              id={isMobile ? "editor-panel" : undefined}
              data-testid={isMobile ? undefined : "editor-panel"}
              ref={editorPanelRef}
              onScroll={isMobile ? handleScroll("editor") : undefined}
              className={
                isMobile
                  ? getMobilePanelClassName(activePanel === "editor", "overflow-hidden")
                  : "h-full overflow-hidden"
              }
              style={isMobile ? { bottom: mobilePanelBottomInset } : undefined}
              aria-hidden={isMobile ? activePanel !== "editor" : undefined}
              aria-labelledby={isMobile ? "editor-tab" : undefined}
              role={isMobile ? "tabpanel" : undefined}
              inert={isMobile && activePanel !== "editor" ? true : undefined}
            >
              {middle}
            </div>
          </Panel>

          {!isMobile && <Separator className={SEPARATOR_CLASS} />}

          <Panel
            id={SPLIT_PANEL_IDS.chat}
            defaultSize={isMobile ? undefined : preset.chat.defaultSize}
            minSize={isMobile ? "0%" : preset.chat.minSize}
          >
            {/* data-testid: chat-panel - Chat panel for AI interaction tests */}
            <div
              id={isMobile ? "chat-panel" : undefined}
              data-testid={isMobile ? undefined : "chat-panel"}
              ref={chatPanelRef}
              onScroll={isMobile ? handleScroll("chat") : undefined}
              className={
                isMobile
                  ? getMobilePanelClassName(activePanel === "chat", "overflow-hidden")
                  : "h-full overflow-hidden"
              }
              style={isMobile ? { bottom: mobilePanelBottomInset } : undefined}
              aria-hidden={isMobile ? activePanel !== "chat" : undefined}
              aria-labelledby={isMobile ? "chat-tab" : undefined}
              role={isMobile ? "tabpanel" : undefined}
              inert={isMobile && activePanel !== "chat" ? true : undefined}
            >
              {right}
            </div>
          </Panel>
        </Group>
      </main>

      {isMobile && !mobileKeyboardOpen && (
        <BottomTabs activeTab={activePanel} onTabChange={setActivePanel} />
      )}
    </div>
  );
};

/**
 * Main Layout component that provides responsive application structure.
 *
 * Automatically switches between desktop/tablet split layout and mobile single-panel
 * layout based on screen size without unmounting editor or chat subtrees:
 * - Desktop (>= 1024px) & Tablet (768px-1023px): Three-panel resizable layout
 * - Mobile (< 768px): Single-panel layout with bottom tabs
 *
 * Both layouts include:
 * - Global file search shortcut (Cmd+K / Ctrl+K)
 * - Header component with navigation and user menu
 * - MobileLayoutProvider context for responsive state
 *
 * @param props - Component props
 * @param props.middle - Editor content (required)
 * @param props.right - Chat panel content (required)
 * @returns The responsive layout JSX element
 *
 * @example
 * // Typical usage in App.tsx
 * <Layout
 *   middle={<Editor />}
 *   right={<ChatPanel />}
 * />
 */
export const Layout: React.FC<LayoutProps> = ({ middle, right }) => {
  const isMobile = useIsMobile();
  const isTablet = useIsTablet();
  const { toggleSearch } = useFileSearchContext();

  // Global keyboard shortcut for file search (Cmd+K on Mac, Ctrl+K on Windows/Linux).
  // Pressing it a second time toggles search closed.
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        toggleSearch();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [toggleSearch]);

  return (
    <MobileLayoutProvider isMobile={isMobile}>
      <WorkbenchLayoutContent
        isMobile={isMobile}
        splitVariant={isTablet ? "tablet" : "desktop"}
        middle={middle}
        right={right}
      />
    </MobileLayoutProvider>
  );
};
