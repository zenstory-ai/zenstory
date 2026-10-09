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
import React, { useEffect, useLayoutEffect, useRef, useCallback, useState } from "react";
import { Panel, Group, Separator } from "react-resizable-panels";
import { Header } from "./Header";
import { BottomTabs } from "./BottomTabs";
import type { MobilePanel } from "./BottomTabs";
import { Sidebar } from "./sidebar/Sidebar";
import { MobileFileTree } from "./MobileFileTree";
import { useIsMobile, useIsTablet } from "../hooks/useMediaQuery";
import { useScrollMemory } from "../hooks/useScrollMemory";
import { useSwipeGestures } from "../hooks/useGestures";
import {
  MobileLayoutProvider,
  useMobileLayout,
} from "../contexts/MobileLayoutContext";
import { useFileSearchContext } from "../contexts/FileSearchContext";
import { useProject } from "../contexts/ProjectContext";
import { hasPendingInspiration } from "../lib/pendingInspiration";

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

  /**
   * Content for the left panel (FileTree).
   * @deprecated Legacy prop - on mobile, MobileFileTree is rendered directly.
   * On desktop, Sidebar component handles file tree display.
   */
  left?: React.ReactNode;
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

/**
 * Desktop three-panel layout component.
 *
 * Renders a fixed three-column layout with resizable panels:
 * - Left: Sidebar (20% default, 180px minimum)
 * - Middle: Editor panel (48% default, 30% minimum)
 * - Right: Chat panel (32% default, 300px minimum)
 *
 * Features:
 * - Draggable panel separators for resizing
 * - Global Cmd/Ctrl+K keyboard shortcut for file search
 * - Header at the top of the viewport
 *
 * @param props - Component props
 * @param props.middle - Editor content to display in the center panel
 * @param props.right - Chat content to display in the right panel
 * @returns The desktop layout JSX element
 *
 * @example
 * <DesktopLayout
 *   middle={<Editor />}
 *   right={<ChatPanel />}
 * />
 */
const DesktopLayout: React.FC<LayoutProps> = ({ middle, right }) => {
  const { openSearch } = useFileSearchContext();

  // Global keyboard shortcut for file search
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Cmd+K on Mac, Ctrl+K on Windows/Linux
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        openSearch();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [openSearch]);

  return (
    <div className="h-dvh overflow-hidden bg-[hsl(var(--bg-primary))] text-[hsl(var(--text-primary))] flex flex-col fixed inset-0" data-testid="app-layout">
      <Header />
      <main className="flex-1 overflow-hidden min-h-0">
        <Group orientation="horizontal" className="h-full">
          <Panel defaultSize="20%" minSize={180}>
            <Sidebar />
          </Panel>
          <Separator className="w-px bg-[hsl(var(--separator-color))] hover:bg-[hsl(var(--accent-primary)/0.5)] transition-all hover:w-1" />
          <Panel defaultSize="48%" minSize="30%">
            {/* data-testid: editor-panel - Editor panel for content editing tests */}
            <div className="h-full overflow-hidden" data-testid="editor-panel">{middle}</div>
          </Panel>
          <Separator className="w-px bg-[hsl(var(--separator-color))] hover:bg-[hsl(var(--accent-primary)/0.5)] transition-all hover:w-1" />
          <Panel defaultSize="32%" minSize={300}>
            {/* data-testid: chat-panel - Chat panel for AI interaction tests */}
            <div className="h-full overflow-hidden p-2" data-testid="chat-panel">{right}</div>
          </Panel>
        </Group>
      </main>
    </div>
  );
};

/**
 * Tablet three-panel layout with optimized proportions.
 *
 * Renders a three-column layout optimized for tablet screens (768px-1024px):
 * - Left: Sidebar (25% default, 20% minimum) - larger for touch interaction
 * - Middle: Editor panel (40% default, 30% minimum)
 * - Right: Chat panel (35% default, 280px minimum)
 *
 * Features:
 * - Adjusted panel proportions for better touch interaction
 * - Reduced padding to maximize screen real estate
 * - Same keyboard shortcuts and functionality as desktop
 * - Draggable panel separators for resizing
 *
 * @param props - Component props
 * @param props.middle - Editor content to display in the center panel
 * @param props.right - Chat content to display in the right panel
 * @returns The tablet layout JSX element
 *
 * @example
 * <TabletLayout
 *   middle={<Editor />}
 *   right={<ChatPanel />}
 * />
 */
const TabletLayout: React.FC<LayoutProps> = ({ middle, right }) => {
  const { openSearch } = useFileSearchContext();

  // Global keyboard shortcut for file search
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Cmd+K on Mac, Ctrl+K on Windows/Linux
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        openSearch();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [openSearch]);

  return (
    <div className="h-dvh overflow-hidden bg-[hsl(var(--bg-primary))] text-[hsl(var(--text-primary))] flex flex-col fixed inset-0" data-testid="app-layout">
      <Header />
      <main className="flex-1 overflow-hidden min-h-0">
        <Group orientation="horizontal" className="h-full">
          <Panel defaultSize="25%" minSize="20%">
            <Sidebar />
          </Panel>
          <Separator className="w-px bg-[hsl(var(--separator-color))] hover:bg-[hsl(var(--accent-primary)/0.5)] transition-all hover:w-1" />
          <Panel defaultSize="40%" minSize="30%">
            <div className="h-full overflow-hidden" data-testid="editor-panel">{middle}</div>
          </Panel>
          <Separator className="w-px bg-[hsl(var(--separator-color))] hover:bg-[hsl(var(--accent-primary)/0.5)] transition-all hover:w-1" />
          <Panel defaultSize="35%" minSize={280}>
            <div className="h-full overflow-hidden p-2" data-testid="chat-panel">{right}</div>
          </Panel>
        </Group>
      </main>
    </div>
  );
};

/**
 * Mobile single-panel layout with bottom tab navigation.
 *
 * Renders a mobile-optimized layout with:
 * - Single visible panel at a time (files, editor, or chat)
 * - Bottom tab navigation for panel switching
 * - Swipe gestures for navigating between panels
 * - Scroll position memory when switching panels
 * - All panels mounted but hidden (preserves state)
 *
 * Panel Navigation:
 * - Swipe left: files → editor → chat
 * - Swipe right: chat → editor → files
 * - Tap bottom tabs for direct navigation
 *
 * @param props - Component props
 * @param props.middle - Editor content to display in editor panel
 * @param props.right - Chat content to display in chat panel
 * @returns The mobile layout JSX element
 *
 * @example
 * <MobileLayoutContent
 *   middle={<Editor />}
 *   right={<ChatPanel />}
 * />
 */
const MobileLayoutContent: React.FC<LayoutProps> = ({ middle, right }) => {
  const { activePanel, setActivePanel } = useMobileLayout();
  const { openSearch } = useFileSearchContext();
  const { currentProjectId } = useProject();
  const { saveScrollPosition, restoreScrollPosition } = useScrollMemory(currentProjectId || 'default');
  const [mobileViewportHeight, setMobileViewportHeight] = useState<number | null>(null);
  const [isMobileKeyboardOpen, setIsMobileKeyboardOpen] = useState(false);
  const filesPanelRef = useRef<HTMLDivElement>(null);
  const editorPanelRef = useRef<HTMLDivElement>(null);
  const chatPanelRef = useRef<HTMLDivElement>(null);
  // Opened from the dashboard with an idea: the first message is about to be sent,
  // so land on the AI tab where it shows up instead of the empty editor.
  useLayoutEffect(() => {
    if (hasPendingInspiration(currentProjectId)) setActivePanel("chat");
  }, [currentProjectId, setActivePanel]);
  const mobilePanelBottomInset = isMobileKeyboardOpen
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

  // Global keyboard shortcut for file search (mobile)
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Cmd+K on Mac, Ctrl+K on Windows/Linux
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        openSearch();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [openSearch]);

  // Track visual viewport changes on mobile to avoid keyboard/input overlap.
  useEffect(() => {
    let baselineViewportHeight = window.visualViewport?.height ?? window.innerHeight;

    const updateMobileViewportState = () => {
      const viewportHeight = window.visualViewport?.height ?? window.innerHeight;
      const roundedViewportHeight = Math.max(0, Math.round(viewportHeight));
      setMobileViewportHeight(roundedViewportHeight);

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
  }, []);

  // Restore scroll when panel becomes active
  useEffect(() => {
    const refs: Record<string, React.RefObject<HTMLDivElement | null>> = {
      files: filesPanelRef,
      editor: editorPanelRef,
      chat: chatPanelRef,
    };
    restoreScrollPosition(activePanel, refs[activePanel]?.current || null);
  }, [activePanel, currentProjectId, restoreScrollPosition]);

  const handleScroll = useCallback((panelId: string) => (e: React.UIEvent<HTMLDivElement>) => {
    saveScrollPosition(panelId, e.currentTarget.scrollTop);
  }, [saveScrollPosition]);

  // Render all panels with visibility toggle (preserves state)
  const renderContent = () => {
    return (
      <div className="h-full relative" {...bind()}>
        {/* Files Panel - always mounted */}
        <div
          id="files-panel"
          ref={filesPanelRef}
          onScroll={handleScroll('files')}
          className={`absolute inset-x-0 top-0 overflow-auto bg-[hsl(var(--bg-primary))] transition-opacity duration-200 ${
            activePanel === "files" ? "opacity-100 z-10" : "opacity-0 z-0 pointer-events-none"
          }`}
          style={{ bottom: mobilePanelBottomInset }}
          aria-hidden={activePanel !== "files"}
          aria-labelledby="files-tab"
          role="tabpanel"
          inert={activePanel !== "files" ? true : undefined}
        >
          <MobileFileTree />
        </div>

        {/* Editor Panel - always mounted */}
        <div
          id="editor-panel"
          ref={editorPanelRef}
          onScroll={handleScroll('editor')}
          className={`absolute inset-x-0 top-0 overflow-hidden bg-[hsl(var(--bg-primary))] transition-opacity duration-200 ${
            activePanel === "editor" ? "opacity-100 z-10" : "opacity-0 z-0 pointer-events-none"
          }`}
          style={{ bottom: mobilePanelBottomInset }}
          aria-hidden={activePanel !== "editor"}
          aria-labelledby="editor-tab"
          role="tabpanel"
          inert={activePanel !== "editor" ? true : undefined}
        >
          {middle}
        </div>

        {/* Chat Panel - always mounted */}
        <div
          id="chat-panel"
          ref={chatPanelRef}
          onScroll={handleScroll('chat')}
          className={`absolute inset-x-0 top-0 overflow-hidden bg-[hsl(var(--bg-primary))] transition-opacity duration-200 ${
            activePanel === "chat" ? "opacity-100 z-10" : "opacity-0 z-0 pointer-events-none"
          }`}
          style={{ bottom: mobilePanelBottomInset }}
          aria-hidden={activePanel !== "chat"}
          aria-labelledby="chat-tab"
          role="tabpanel"
          inert={activePanel !== "chat" ? true : undefined}
        >
          {right}
        </div>
      </div>
    );
  };

  return (
    <div
      className="h-dvh overflow-hidden bg-[hsl(var(--bg-primary))] text-[hsl(var(--text-primary))] flex flex-col fixed inset-0"
      style={mobileViewportHeight ? { height: `${mobileViewportHeight}px` } : undefined}
    >
      {/* Header */}
      <Header />

      {/* Main content area - account for bottom tabs height (56px) */}
      <main
        className={`flex-1 overflow-hidden min-h-0 ${
          isMobileKeyboardOpen ? "pb-safe-or-2" : "pb-14 safe-area-bottom"
        }`}
      >
        {renderContent()}
      </main>

      {/* Bottom navigation */}
      {!isMobileKeyboardOpen && (
        <BottomTabs activeTab={activePanel} onTabChange={setActivePanel} />
      )}
    </div>
  );
};

/**
 * Main Layout component that provides responsive application structure.
 *
 * Automatically switches between desktop and mobile layouts based on screen size:
 * - Desktop (>= 768px): Three-panel layout with resizable panels
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
 * @param props.left - Legacy left panel content (optional, deprecated)
 * @returns The appropriate layout JSX element based on screen size
 *
 * @example
 * // Typical usage in App.tsx
 * <Layout
 *   middle={<Editor />}
 *   right={<ChatPanel />}
 * />
 *
 * @example
 * // With legacy left prop (deprecated)
 * <Layout
 *   left={<FileTree />}
 *   middle={<Editor />}
 *   right={<ChatPanel />}
 * />
 */
export const Layout: React.FC<LayoutProps> = ({ left, middle, right }) => {
  const isMobile = useIsMobile();
  const isTablet = useIsTablet();

  if (isMobile) {
    return (
      <MobileLayoutProvider isMobile={true}>
        <MobileLayoutContent left={left} middle={middle} right={right} />
      </MobileLayoutProvider>
    );
  }

  if (isTablet) {
    return (
      <MobileLayoutProvider isMobile={false}>
        <TabletLayout middle={middle} right={right} />
      </MobileLayoutProvider>
    );
  }

  return (
    <MobileLayoutProvider isMobile={false}>
      <DesktopLayout middle={middle} right={right} />
    </MobileLayoutProvider>
  );
};
