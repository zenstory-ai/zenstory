/**
 * @fileoverview User menu components for displaying user avatar and dropdown menu
 * @module components/UserMenu
 *
 * This module provides components for user authentication UI:
 * - UserAvatar: Displays user avatar with image fallback to initials
 * - UserMenu: Desktop dropdown menu with user info and actions
 * - UserMenuMobile: Mobile-friendly user menu for sidebar
 *
 * Features:
 * - Consistent avatar colors based on username
 * - Image fallback to initials on error
 * - Dropdown with click-outside and escape key dismissal
 * - Admin panel link for superusers
 * - Logout functionality
 * - i18n support via react-i18next
 */

import React, { useState, useRef, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { LogOut, ChevronDown, Shield, CalendarCheck, Mail } from "lucide-react";
import { useAuth } from "../contexts/AuthContext";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { pointsApi } from "../lib/pointsApi";
import { SUPPORT_EMAIL, SUPPORT_MAILTO } from "../config/support";
import { getUserDisplayName } from "../lib/userDisplayName";

/**
 * Predefined colors for avatar background
 * @constant {string[]} AVATAR_COLORS
 * @description Array of 9 colors used for avatar backgrounds when no image is available.
 * Colors are selected based on the first character of the username for consistency.
 */
const AVATAR_COLORS = [
  "#f87171", // red
  "#fb923c", // orange
  "#fbbf24", // amber
  "#a3e635", // lime
  "#34d399", // emerald
  "#22d3ee", // cyan
  "#60a5fa", // blue
  "#94a3b8", // slate
  "#f472b6", // pink
];

/**
 * Generate a consistent avatar color based on username
 *
 * @param {string} username - The username to generate color for
 * @returns {string} A hex color string from AVATAR_COLORS
 *
 * @example
 * getAvatarColor('alice'); // Returns color based on 'a' char code
 * getAvatarColor('Bob'); // Returns color based on 'B' char code
 */
function getAvatarColor(username: string): string {
  const charCode = username.charCodeAt(0) || 0;
  return AVATAR_COLORS[charCode % AVATAR_COLORS.length];
}

/**
 * Get initials from username (first character, uppercase)
 *
 * @param {string} username - The username to extract initials from
 * @returns {string} The first character of the username, uppercase
 *
 * @example
 * getInitials('alice'); // Returns 'A'
 * getInitials('Bob'); // Returns 'B'
 */
function getInitials(username: string): string {
  return username.charAt(0).toUpperCase();
}

/**
 * Format login method display
 *
 * @param {Object} user - User object containing contact information
 * @param {string} user.email - User's email address
 * @param {string} [user.phone] - User's phone number (future support)
 * @param {string} [user.wechat_id] - User's WeChat ID (future support)
 * @returns {string} The formatted login method display string
 *
 * @description
 * Reserved for future: phone, wechat login support.
 * Currently returns email, but can be extended to show phone or WeChat.
 *
 * @example
 * getLoginMethodLabel({ email: 'user@example.com' }); // Returns 'user@example.com'
 */
function getLoginMethodLabel(user: { email: string; phone?: string; wechat_id?: string }): string {
  // Future: check login method and display accordingly
  // if (user.phone) return user.phone;
  // if (user.wechat_id) return 'WeChat: ' + user.wechat_id;
  return user.email;
}

/**
 * Props for the UserAvatar component
 * @interface UserAvatarProps
 */
interface UserAvatarProps {
  /** The username to display (used for initials and color generation) */
  username: string;
  /** Optional URL for the avatar image */
  avatarUrl?: string;
  /** Size of the avatar in pixels (default: 32) */
  size?: number;
}

/**
 * UserAvatar component - displays user avatar with image fallback to initials
 *
 * @param {UserAvatarProps} props - Component props
 * @returns {React.ReactElement | null} The rendered avatar element
 *
 * @description
 * Renders a circular avatar that:
 * - Shows the provided avatar image if available and loads successfully
 * - Falls back to showing initials with a colored background on image error
 * - Uses a consistent color based on username for the fallback
 *
 * @example
 * // Basic usage with image
 * <UserAvatar username="alice" avatarUrl="https://example.com/avatar.jpg" />
 *
 * @example
 * // Without image (shows initials)
 * <UserAvatar username="alice" />
 *
 * @example
 * // Custom size
 * <UserAvatar username="alice" size={48} />
 */
export const UserAvatar: React.FC<UserAvatarProps> = ({ username, avatarUrl, size = 32 }) => {
  const [imageError, setImageError] = useState(false);
  const showImage = avatarUrl && !imageError;

  return (
    <div
      className="rounded-full flex items-center justify-center font-medium text-white overflow-hidden"
      style={{
        width: size,
        height: size,
        backgroundColor: showImage ? "transparent" : getAvatarColor(username),
        fontSize: size * 0.45,
      }}
    >
      {showImage ? (
        <img
          src={avatarUrl}
          alt={username}
          className="w-full h-full object-cover"
          onError={() => setImageError(true)}
        />
      ) : (
        getInitials(username)
      )}
    </div>
  );
};

/**
 * Props for the UserMenu component
 * @interface UserMenuProps
 * @description UserMenu has no props - it reads user state from AuthContext
 */
// eslint-disable-next-line @typescript-eslint/no-empty-object-type
interface UserMenuProps {
  // No props - user state comes from AuthContext
}

/**
 * UserMenu component - displays user avatar with dropdown menu
 *
 * @param {UserMenuProps} _props - Component props (unused, user comes from context)
 * @returns {React.ReactElement | null} The rendered menu or null if no user
 *
 * @description
 * Desktop user menu component that provides:
 * - Avatar button with dropdown arrow indicator
 * - Click-outside to close functionality
 * - Escape key to close functionality
 * - User info display (avatar, username, email)
 * - Admin panel link (superusers only)
 * - Logout button
 *
 * Features:
 * - Keyboard accessible (Escape to close)
 * - Click outside to dismiss
 * - Responsive dropdown positioning
 * - i18n support for menu labels
 * - Graceful null return when no user logged in
 *
 * @example
 * // In a header component
 * <Header>
 *   <Logo />
 *   <nav>...</nav>
 *   <UserMenu />
 * </Header>
 */
export const UserMenu: React.FC<UserMenuProps> = () => {
  const { t } = useTranslation(['common', 'settings']);
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [isOpen, setIsOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const queryClient = useQueryClient();

  // Check-in status query
  const { data: checkInStatus } = useQuery({
    queryKey: ['check-in-status'],
    queryFn: () => pointsApi.getCheckInStatus(),
    enabled: !!user,
  });

  // Check-in mutation
  const checkInMutation = useMutation({
    mutationFn: () => pointsApi.checkIn(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['check-in-status'] });
      queryClient.invalidateQueries({ queryKey: ['points-balance'] });
      queryClient.invalidateQueries({ queryKey: ['points-transactions'] });
    },
  });

  // Close menu when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    };

    if (isOpen) {
      document.addEventListener("mousedown", handleClickOutside);
    }

    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, [isOpen]);

  // Close menu on escape key
  useEffect(() => {
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setIsOpen(false);
      }
    };

    if (isOpen) {
      document.addEventListener("keydown", handleEscape);
    }

    return () => {
      document.removeEventListener("keydown", handleEscape);
    };
  }, [isOpen]);

  if (!user) return null;

  const handleLogout = () => {
    setIsOpen(false);
    logout();
  };

  const handleNavigateToAdmin = () => {
    setIsOpen(false);
    navigate("/admin");
  };

  const handleCheckIn = () => {
    checkInMutation.mutate();
  };

  const isCheckedIn = checkInStatus?.checked_in;

  return (
    <div className="relative" ref={menuRef}>
      {/* Avatar button */}
      {/* data-testid: user-menu-button - User menu trigger for user menu tests */}
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className="flex min-h-[44px] min-w-[44px] items-center gap-1 px-2 hover:bg-[hsl(var(--bg-tertiary))] rounded transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-secondary))]"
        aria-expanded={isOpen}
        aria-haspopup="true"
        aria-label={isOpen ? t('userMenu.close', '关闭用户菜单') : t('userMenu.open', '打开用户菜单')}
        data-testid="user-menu-button"
      >
        <UserAvatar
          username={getUserDisplayName(user)}
          avatarUrl={user.avatar_url}
          size={28}
        />
        <ChevronDown
          size={14}
          className={`text-[hsl(var(--text-secondary))] transition-transform ${
            isOpen ? "rotate-180" : ""
          }`}
        />
      </button>

      {/* Dropdown menu */}
      {isOpen && (
        <div className="absolute right-0 top-full mt-1 w-56 bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--separator-color))] rounded-lg shadow-lg z-50 overflow-hidden">
          {/* User info section */}
          <div className="px-3 py-3 border-b border-[hsl(var(--separator-color))]">
            <div className="flex items-center gap-3">
              <UserAvatar
                username={getUserDisplayName(user)}
                avatarUrl={user.avatar_url}
                size={40}
              />
              <div className="flex-1 min-w-0">
                <div className="font-medium text-[hsl(var(--text-primary))] truncate">
                  {getUserDisplayName(user)}
                </div>
                <div className="text-xs text-[hsl(var(--text-secondary))] truncate">
                  {getLoginMethodLabel(user)}
                </div>
              </div>
            </div>
          </div>

          {/* Menu items */}
          <div className="py-1">
            {/* Admin panel link - only for superusers */}
            {user.is_superuser && (
              <button
                type="button"
                onClick={handleNavigateToAdmin}
                className="w-full min-h-[44px] px-3 py-2 text-left text-sm text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))] flex items-center gap-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-secondary))]"
              >
                <Shield size={16} />
                {t('userMenu.adminPanel')}
              </button>
            )}

            {/* Check-in button */}
            <button
              type="button"
              onClick={handleCheckIn}
              disabled={isCheckedIn || checkInMutation.isPending}
              className="w-full min-h-[44px] px-3 py-2 text-left text-sm text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))] flex items-center gap-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-secondary))] disabled:opacity-50 disabled:cursor-not-allowed"
              data-testid="check-in-button"
            >
              <CalendarCheck size={16} className={isCheckedIn ? "text-[hsl(var(--success))]" : "text-[hsl(var(--warning))]"} />
              {isCheckedIn
                ? t('settings:points.alreadyCheckedIn', '今日已签到')
                : checkInMutation.isPending
                  ? t('common:processing', '处理中...')
                  : t('settings:points.checkIn', '签到领积分')}
            </button>

            {/* Future: Profile settings link */}
            {/* <button className="w-full px-3 py-2 text-left text-sm text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))] flex items-center gap-2">
              <User size={16} />
              {t('common:userMenu.profile')}
            </button> */}

            {/* Contact support */}
            <a
              href={SUPPORT_MAILTO}
              aria-label={`${t('common:userMenu.contactSupportAria', '发邮件给客服')} ${SUPPORT_EMAIL}`}
              className="w-full min-h-[44px] px-3 py-2 text-left text-sm text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))] flex items-center gap-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-secondary))]"
              data-testid="contact-support-link"
            >
              <Mail size={16} />
              <span className="flex flex-col min-w-0">
                <span>{t('common:userMenu.contactSupport', '联系客服')}</span>
                <span className="text-xs text-[hsl(var(--text-secondary))] truncate">{SUPPORT_EMAIL}</span>
              </span>
            </a>

            {/* data-testid: logout-button - Logout button for logout tests */}
            <button
              type="button"
              onClick={handleLogout}
              className="w-full min-h-[44px] px-3 py-2 text-left text-sm text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))] flex items-center gap-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-secondary))]"
              data-testid="logout-button"
            >
              <LogOut size={16} />
              {t('common:userMenu.logout')}
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

/**
 * Props for the UserMenuMobile component
 * @interface UserMenuMobileProps
 */
interface UserMenuMobileProps {
  /** Optional callback fired when logout is triggered (useful for closing parent menu) */
  onLogout?: () => void;
}

/**
 * UserMenuMobile - Simplified user display for mobile menu
 *
 * @param {UserMenuMobileProps} props - Component props
 * @returns {React.ReactElement | null} The rendered mobile menu or null if no user
 *
 * @description
 * Mobile-friendly user menu component designed for sidebar integration.
 * Unlike the desktop UserMenu, this is not a dropdown but a static display
 * suitable for placement in a slide-out sidebar.
 *
 * Features:
 * - Horizontal user info row with avatar and details
 * - Admin panel link (superusers only)
 * - Logout button with optional callback
 * - Compact layout for mobile viewports
 * - i18n support for menu labels
 *
 * @example
 * // In a mobile sidebar
 * <MobileSidebar isOpen={isOpen} onClose={onClose}>
 *   <nav>...</nav>
 *   <UserMenuMobile onLogout={onClose} />
 * </MobileSidebar>
 *
 * @example
 * // Without logout callback
 * <UserMenuMobile />
 */
export const UserMenuMobile: React.FC<UserMenuMobileProps> = ({ onLogout }) => {
  const { t } = useTranslation(['common', 'settings']);
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  // Check-in status query
  const { data: checkInStatus } = useQuery({
    queryKey: ['check-in-status'],
    queryFn: () => pointsApi.getCheckInStatus(),
    enabled: !!user,
  });

  // Check-in mutation
  const checkInMutation = useMutation({
    mutationFn: () => pointsApi.checkIn(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['check-in-status'] });
      queryClient.invalidateQueries({ queryKey: ['points-balance'] });
      queryClient.invalidateQueries({ queryKey: ['points-transactions'] });
    },
  });

  if (!user) return null;

  const handleLogout = () => {
    onLogout?.();
    logout();
  };

  const handleNavigateToAdmin = () => {
    onLogout?.();
    navigate("/admin");
  };

  const handleCheckIn = () => {
    checkInMutation.mutate();
  };

  const isCheckedIn = checkInStatus?.checked_in;

  return (
    <>
      {/* User info row */}
      <div className="flex items-center gap-3 px-3 py-2.5">
        <UserAvatar
          username={getUserDisplayName(user)}
          avatarUrl={user.avatar_url}
          size={32}
        />
        <div className="flex-1 min-w-0">
          <div className="font-medium text-sm text-[hsl(var(--text-primary))] truncate">
            {getUserDisplayName(user)}
          </div>
          <div className="text-xs text-[hsl(var(--text-secondary))] truncate">
            {getLoginMethodLabel(user)}
          </div>
        </div>
      </div>

      {/* Divider */}
      <div className="h-px bg-[hsl(var(--separator-color))] mx-2 my-1" />

      {/* Admin panel link - only for superusers */}
      {user.is_superuser && (
        <button
          type="button"
          onClick={handleNavigateToAdmin}
          className="flex min-h-[44px] items-center gap-3 px-3 py-2.5 hover:bg-[hsl(var(--bg-tertiary))] rounded text-sm text-[hsl(var(--text-primary))] transition-colors text-left w-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-secondary))]"
        >
          <Shield size={18} />
          <span>{t('userMenu.adminPanel')}</span>
        </button>
      )}

      {/* Check-in button */}
      <button
        type="button"
        onClick={handleCheckIn}
        disabled={isCheckedIn || checkInMutation.isPending}
        className="flex min-h-[44px] items-center gap-3 px-3 py-2.5 hover:bg-[hsl(var(--bg-tertiary))] rounded text-sm text-[hsl(var(--text-primary))] transition-colors text-left w-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-secondary))] disabled:opacity-50 disabled:cursor-not-allowed"
        data-testid="check-in-button-mobile"
      >
        <CalendarCheck size={18} className={isCheckedIn ? "text-[hsl(var(--success))]" : "text-[hsl(var(--warning))]"} />
        <span>
          {isCheckedIn
            ? t('settings:points.alreadyCheckedIn', '今日已签到')
            : checkInMutation.isPending
              ? t('common:processing', '处理中...')
              : t('settings:points.checkIn', '签到领积分')}
        </span>
      </button>

      {/* Contact support */}
      <a
        href={SUPPORT_MAILTO}
        aria-label={`${t('userMenu.contactSupportAria', '发邮件给客服')} ${SUPPORT_EMAIL}`}
        className="flex min-h-[44px] items-center gap-3 px-3 py-2.5 hover:bg-[hsl(var(--bg-tertiary))] rounded text-sm text-[hsl(var(--text-primary))] transition-colors text-left w-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-secondary))]"
        data-testid="contact-support-link-mobile"
      >
        <Mail size={18} />
        <span className="flex flex-col min-w-0">
          <span>{t('userMenu.contactSupport', '联系客服')}</span>
          <span className="text-xs text-[hsl(var(--text-secondary))] truncate">{SUPPORT_EMAIL}</span>
        </span>
      </a>

      {/* Logout button */}
      <button
        type="button"
        onClick={handleLogout}
        className="flex min-h-[44px] items-center gap-3 px-3 py-2.5 hover:bg-[hsl(var(--bg-tertiary))] rounded text-sm text-[hsl(var(--text-primary))] transition-colors text-left w-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-secondary))]"
      >
        <LogOut size={18} />
        <span>{t('userMenu.logout')}</span>
      </button>
    </>
  );
};

export default UserMenu;
