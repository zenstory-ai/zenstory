/**
 * @fileoverview MobileSidebar component - Slide-out navigation drawer for mobile devices.
 *
 * This component provides a responsive sidebar navigation experience optimized
 * for mobile devices with:
 * - Slide-in/out animation from the left side
 * - Overlay backdrop that dismisses the sidebar on tap
 * - Navigation items with icons and i18n labels
 * - External link to experimental features (Lab section)
 * - User profile display with avatar and email
 * - Logout functionality with loading state
 *
 * The sidebar is controlled via `isOpen` and `onClose` props and renders
 * nothing when closed (returns null).
 *
 * @module components/MobileSidebar
 */
import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import { X, Home, LogOut, Library, Zap, FolderOpen } from "lucide-react";
import { useNavigate, useLocation } from "react-router-dom";
import { Logo } from "./Logo";
import { useAuth } from "../contexts/AuthContext";

/**
 * Navigation item configuration for the sidebar menu.
 *
 * @interface SidebarItem
 */
interface SidebarItem {
  /** Unique identifier for the navigation item, used for active state matching */
  id: string;
  /** Lucide icon component to display */
  icon: React.ElementType;
  /** i18n translation key for the label (e.g., "nav.home") */
  labelKey: string;
  /** React Router path to navigate to when clicked */
  path: string;
}

/**
 * Array of navigation items displayed in the main navigation section.
 * Each item includes an icon, translation key, and route path.
 */
const SIDEBAR_ITEMS: SidebarItem[] = [
  { id: "home", icon: Home, labelKey: "nav.home", path: "/dashboard" },
  { id: "projects", icon: FolderOpen, labelKey: "nav.projects", path: "/dashboard/projects" },
  { id: "materials", icon: Library, labelKey: "nav.materials", path: "/dashboard/materials" },
  { id: "skills", icon: Zap, labelKey: "nav.skills", path: "/dashboard/skills" },
];

/**
 * Props for the MobileSidebar component.
 *
 * @interface MobileSidebarProps
 */
interface MobileSidebarProps {
  /** Whether the sidebar drawer is currently visible/open */
  isOpen: boolean;
  /** Callback fired when the sidebar should close (overlay click, nav item click, or X button) */
  onClose: () => void;
}

/**
 * Mobile sidebar navigation component providing a slide-out drawer for mobile devices.
 *
 * Renders a full-height sidebar with:
 * - **Header**: Logo and close button
 * - **Navigation section**: Main nav items with active state highlighting
 * - **Lab section**: External links to experimental features
 * - **User section**: Profile display and logout button
 *
 * Features:
 * - Smooth slide-in/out animations
 * - Overlay backdrop for dismissal
 * - Active route detection and highlighting
 * - Loading state during logout
 * - i18n support via react-i18next
 *
 * @param props - Component props
 * @param props.isOpen - Whether the sidebar is visible
 * @param props.onClose - Callback to close the sidebar
 * @returns The sidebar JSX element or null when closed
 *
 * @example
 * // Basic usage with state control
 * const [sidebarOpen, setSidebarOpen] = useState(false);
 *
 * <MobileSidebar
 *   isOpen={sidebarOpen}
 *   onClose={() => setSidebarOpen(false)}
 * />
 *
 * @example
 * // Integration with hamburger menu button
 * <button onClick={() => setSidebarOpen(true)}>
 *   <MenuIcon />
 * </button>
 * <MobileSidebar
 *   isOpen={sidebarOpen}
 *   onClose={() => setSidebarOpen(false)}
 * />
 */
export const MobileSidebar: React.FC<MobileSidebarProps> = ({
  isOpen,
  onClose,
}) => {
  const { t } = useTranslation(['dashboard', 'common']);
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [isLoggingOut, setIsLoggingOut] = useState(false);
  const [isClosing, setIsClosing] = useState(false);

  /**
   * Determines the active navigation item based on the current URL path.
   *
   * @returns The active navigation item ID ("home", "projects", "materials", or "skills")
   */
  const getActiveNav = () => {
    if (location.pathname === "/dashboard/projects") return "projects";
    if (location.pathname === "/dashboard/materials") return "materials";
    if (location.pathname === "/dashboard/skills") return "skills";
    return "home";
  };
  const activeNav = getActiveNav();

  /**
   * Handles user logout with loading state management.
   * Prevents multiple simultaneous logout attempts.
   */
  const handleLogout = async () => {
    if (isLoggingOut) return;
    setIsLoggingOut(true);
    try {
      await logout();
    } finally {
      setIsLoggingOut(false);
    }
  };

  /**
   * Handles navigation item click - navigates to the item's path and closes the sidebar.
   *
   * @param item - The navigation item that was clicked
   */
  const handleNavClick = (item: SidebarItem) => {
    navigate(item.path);
    handleClose();
  };

  /**
   * Handles closing the sidebar with animation.
   * Triggers the slide-out animation before actually closing.
   */
  const handleClose = () => {
    setIsClosing(true);
    // Wait for animation to complete before actually closing
    setTimeout(() => {
      setIsClosing(false);
      onClose();
    }, 200); // Match animation duration
  };

  if (!isOpen) return null;

  return (
    <>
      {/* Overlay */}
      <div
        className={`mobile-drawer-overlay ${isClosing ? 'animate-fade-out' : 'animate-fade-in'}`}
        onClick={handleClose}
      />

      {/* Drawer */}
      <aside
        className={`
          mobile-drawer
          ${isClosing ? "animate-slide-out-left" : "animate-slide-in-left"}
        `}
      >
        {/* Header */}
        <div className="h-16 px-4 flex items-center justify-between border-b border-[hsl(var(--border-color))]">
          <Logo className="h-7 w-auto" />
          <button
            onClick={handleClose}
            className="p-2 min-h-[44px] min-w-[44px] rounded-lg text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--bg-tertiary))] active:bg-[hsl(var(--bg-hover))] transition-all flex items-center justify-center"
            aria-label={t('common:closeSidebar', '关闭侧边栏')}
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Navigation */}
        <nav className="flex-1 p-2 space-y-1">
          {SIDEBAR_ITEMS.map((item) => {
            const Icon = item.icon;
            const isActive = activeNav === item.id;

            return (
              <button
                key={item.id}
                onClick={() => handleNavClick(item)}
                className={`
                  mobile-nav-item
                  ${isActive ? "active" : ""}
                `}
              >
                <Icon className="w-5 h-5" />
                <span className="font-medium">{t(item.labelKey)}</span>
              </button>
            );
          })}

        </nav>

        {/* Separator */}
        <div className="mobile-separator" />

        {/* User Info */}
        <div className="p-2">
          <div className="mobile-nav-item">
            <div className="w-9 h-9 rounded-full bg-gradient-to-br from-[hsl(var(--accent-primary))] to-[hsl(var(--accent-dark))] flex items-center justify-center text-white text-sm font-medium shrink-0">
              {user?.username?.charAt(0).toUpperCase() || "U"}
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-sm font-medium text-[hsl(var(--text-primary))] truncate">
                {user?.username}
              </div>
              <div className="text-xs text-[hsl(var(--text-secondary))]">
                {user?.email || ""}
              </div>
            </div>
          </div>
          <button
            onClick={handleLogout}
            disabled={isLoggingOut}
            className="mobile-nav-item text-[hsl(var(--error))] hover:bg-[hsl(var(--error)/0.1)] disabled:opacity-50"
          >
            <LogOut className="w-5 h-5" />
            <span className="font-medium">
              {isLoggingOut ? t('common.loading') : t('nav.logout')}
            </span>
          </button>
        </div>
      </aside>
    </>
  );
};

export default MobileSidebar;
