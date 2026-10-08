/**
 * @fileoverview BottomTabs component - Mobile bottom navigation bar.
 *
 * This component provides a fixed bottom navigation bar for mobile devices with:
 * - Three main navigation tabs: Files, Editor, and AI Chat
 * - Active tab highlighting with accent color and bold icon
 * - Full accessibility support with ARIA attributes (role, aria-selected, aria-controls)
 * - Internationalization support for tab labels
 * - Safe area inset handling for notched devices
 * - Touch-optimized tap targets (min-height 44px)
 *
 * The component is displayed only on mobile viewports and works in conjunction
 * with the Layout component's MobileLayoutContent to switch between panels.
 *
 * @module components/BottomTabs
 */
import React from "react";
import { useTranslation } from "react-i18next";
import { FolderOpen, Edit3, MessageSquare } from "lucide-react";

/**
 * Type representing the three main mobile panels.
 *
 * These panels correspond to the primary navigation destinations
 * in the mobile layout:
 * - `files`: File tree panel showing project files
 * - `editor`: Content editor panel for editing selected files
 * - `chat`: AI chat panel for conversing with the writing assistant
 *
 * @typedef {('files' | 'editor' | 'chat')} MobilePanel
 */
export type MobilePanel = "files" | "editor" | "chat";

/**
 * Internal interface representing a navigation tab.
 *
 * Each tab has a unique identifier and an associated icon component
 * from the lucide-react icon library.
 *
 * @interface Tab
 * @property {MobilePanel} id - The unique identifier for the tab
 * @property {React.ElementType} icon - The Lucide icon component to display
 */
interface Tab {
  id: MobilePanel;
  icon: React.ElementType;
}

/**
 * Array of navigation tabs configuration.
 *
 * Defines the three main navigation tabs in order:
 * 1. Files (FolderOpen icon) - File tree panel
 * 2. Editor (Edit3 icon) - Content editor panel
 * 3. Chat (MessageSquare icon) - AI assistant panel
 *
 * @constant {Tab[]}
 */
const tabs: Tab[] = [
  { id: "files", icon: FolderOpen },
  { id: "editor", icon: Edit3 },
  { id: "chat", icon: MessageSquare },
];

/**
 * Props for the BottomTabs component.
 *
 * @interface BottomTabsProps
 * @property {MobilePanel} activeTab - The currently active tab identifier
 * @property {(tab: MobilePanel) => void} onTabChange - Callback fired when a tab is clicked
 */
interface BottomTabsProps {
  activeTab: MobilePanel;
  onTabChange: (tab: MobilePanel) => void;
}

/**
 * Mobile bottom navigation tabs component.
 *
 * Renders a fixed bottom navigation bar with three tabs for switching
 * between the main application panels on mobile devices.
 *
 * Features:
 * - **Fixed positioning**: Always visible at the bottom of the screen
 * - **Active state**: Highlights the current tab with accent color
 * - **Accessibility**: Full ARIA support with tablist, tab, and aria-selected
 * - **Touch-friendly**: 44px minimum touch targets for all tabs
 * - **Safe area**: Handles device notches with safe-area-bottom class
 * - **i18n**: Tab labels are internationalized via react-i18next
 *
 * The component uses CSS custom properties for theming:
 * - `--bg-secondary`: Background color
 * - `--separator-color`: Top border color
 * - `--accent-primary`: Active tab color
 * - `--text-secondary`: Inactive tab text color
 * - `--text-primary`: Active/pressed text color
 *
 * @param {BottomTabsProps} props - Component props
 * @param {MobilePanel} props.activeTab - Currently active tab
 * @param {(tab: MobilePanel) => void} props.onTabChange - Tab change callback
 * @returns The bottom navigation bar JSX element
 *
 * @example
 * // Basic usage with active tab state
 * const [activePanel, setActivePanel] = useState<MobilePanel>('editor');
 *
 * <BottomTabs
 *   activeTab={activePanel}
 *   onTabChange={setActivePanel}
 * />
 *
 * @example
 * // Integration with Layout component
 * function MobileLayout() {
 *   const [mobilePanel, setMobilePanel] = useState<MobilePanel>('files');
 *
 *   return (
 *     <>
 *       <main className="pb-14">
 *         {mobilePanel === 'files' && <FileTree />}
 *         {mobilePanel === 'editor' && <Editor />}
 *         {mobilePanel === 'chat' && <ChatPanel />}
 *       </main>
 *       <BottomTabs activeTab={mobilePanel} onTabChange={setMobilePanel} />
 *     </>
 *   );
 * }
 */
export const BottomTabs: React.FC<BottomTabsProps> = ({
  activeTab,
  onTabChange,
}) => {
  const { t } = useTranslation(['editor']);
  return (
    <nav
      data-testid="bottom-tabs"
      className="fixed bottom-0 left-0 right-0 h-14 bg-[hsl(var(--bg-secondary))] border-t border-[hsl(var(--separator-color))] flex z-50 safe-area-bottom overflow-x-hidden"
      role="tablist"
      aria-label={t('editor:bottomTabs.ariaLabel', '底部导航')}
    >
      {tabs.map((tab, index) => {
        const isActive = activeTab === tab.id;
        const Icon = tab.icon;

        return (
          <button
            key={tab.id}
            id={`${tab.id}-tab`}
            onClick={() => onTabChange(tab.id)}
            onKeyDown={(event) => {
              const nextIndex = event.key === 'ArrowRight' ? (index + 1) % tabs.length
                : event.key === 'ArrowLeft' ? (index - 1 + tabs.length) % tabs.length
                : event.key === 'Home' ? 0
                : event.key === 'End' ? tabs.length - 1 : null;
              if (nextIndex === null) return;
              event.preventDefault();
              onTabChange(tabs[nextIndex].id);
              event.currentTarget.parentElement
                ?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[nextIndex]?.focus();
            }}
            className={`mobile-bottom-tab flex-1 flex flex-col items-center justify-center gap-1.5 transition-colors transition-transform min-h-[44px] no-select active:scale-95 active:opacity-80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-secondary))] ${
              isActive
                ? "text-[hsl(var(--accent-primary))] bg-[hsl(var(--accent-primary)/0.1)]"
                : "text-[hsl(var(--text-secondary))] active:text-[hsl(var(--text-primary))]"
            }`}
            role="tab"
            aria-selected={isActive}
            aria-controls={`${tab.id}-panel`}
            tabIndex={isActive ? 0 : -1}
          >
            <Icon
              size={20}
              className={`bottom-tab-icon ${isActive ? "stroke-[2.5]" : "stroke-[1.5]"}`}
            />
            <span className={`bottom-tab-label text-xs ${isActive ? "font-medium" : ""}`}>
              {tab.id === 'files' && t('editor:bottomTabs.files')}
              {tab.id === 'editor' && t('editor:bottomTabs.editor')}
              {tab.id === 'chat' && t('editor:bottomTabs.ai')}
            </span>
          </button>
        );
      })}
    </nav>
  );
};

export default BottomTabs;
