/**
 * @fileoverview SettingsDialog component - Application settings modal with user profile and preferences.
 *
 * This component provides a comprehensive settings dialog with:
 * - **Profile tab**: User information display, avatar, and logout functionality
 * - **General tab**: Language selection (Chinese/English), theme mode (dark/light), accent color picker
 * - **Subscription tab**: Subscription status display and quota information with redeem code support
 * - **Points tab**: Points balance, daily check-in, earn opportunities, and transaction history
 * - **Referral tab**: Referral program for inviting friends and earning rewards
 *
 * The dialog features:
 * - Responsive layout (horizontal tabs on desktop, vertical on mobile)
 * - Click-outside-to-close functionality
 * - Real-time theme and language switching
 * - Integration with ThemeContext and AuthContext
 * - Support for subscription management via RedeemCodeModal
 * - Uses Modal component for accessibility and consistent styling
 *
 * @module components/SettingsDialog
 */

import React, { useState, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { useIsFetching } from '@tanstack/react-query';
import { User, Settings, Moon, Sun, LogOut, CreditCard, Coins, Users, Key } from 'lucide-react';
import { useTheme } from '../contexts/ThemeContext';
import { useAuth } from '../contexts/AuthContext';
import { useIsMobile } from '../hooks/useMediaQuery';
import { normalizeLocale } from '../lib/i18n-helpers';
import { pointsConfig } from '../config/points';
import Modal from './ui/Modal';
import { UserAvatar } from './UserMenu';
import { SubscriptionStatus } from './subscription/SubscriptionStatus';
import { QuotaBadge } from './subscription/QuotaBadge';
import { RedeemCodeModal } from './subscription/RedeemCodeModal';
import { RedeemProModal } from './points/RedeemProModal';
import { DailyCheckIn } from './points/DailyCheckIn';
import { PointsBalance } from './points/PointsBalance';
import { PointsHistory } from './points/PointsHistory';
import { EarnOpportunities } from './points/EarnOpportunities';
import { ReferralStats } from './referral/ReferralStats';
import { InviteCodeList } from './referral/InviteCodeList';
import { AgentApiKeysPanel } from './settings/AgentApiKeysPanel';
import { buildUpgradeUrl, getUpgradePromptDefinition } from '../config/upgradeExperience';
import { trackUpgradeClick } from '../lib/upgradeAnalytics';
import { isAnalyticsOptedOut, setAnalyticsOptOut } from '../lib/analytics';

/**
 * Props for the SettingsDialog component.
 *
 * @interface SettingsDialogProps
 * @property {boolean} isOpen - Whether the settings dialog is currently visible
 * @property {() => void} onClose - Callback fired when the dialog should be closed
 */
interface SettingsDialogProps {
  /** Controls the visibility of the settings dialog */
  isOpen: boolean;
  /** Callback function invoked when the dialog needs to be closed */
  onClose: () => void;
  /** Tab to open when dialog becomes visible (consumed only on open transition) */
  defaultTab?: SettingsTab;
}

/**
 * Union type representing the available settings tabs.
 *
 * - `'profile'` - User profile and account settings
 * - `'general'` - General application preferences (language, theme, colors)
 * - `'subscription'` - Subscription status and quota information
 * - `'points'` - Points balance, daily check-in, and earn opportunities
 * - `'referral'` - Referral program for inviting friends
 */
export type SettingsTab = 'profile' | 'general' | 'subscription' | 'points' | 'referral' | 'agent';

/**
 * Unified skeleton for the Points tab loading state.
 * Mimics the layout of PointsBalance, DailyCheckIn, EarnOpportunities, and PointsHistory.
 */
function PointsTabSkeleton() {
  return (
    <div className="space-y-4 animate-pulse">
      {/* Points Balance skeleton */}
      <div className="p-4 border border-[hsl(var(--border-color))] rounded-xl bg-[hsl(var(--bg-secondary))]">
        <div className="flex items-center gap-2 mb-2">
          <div className="w-5 h-5 rounded-full bg-[hsl(var(--bg-tertiary))]" />
          <div className="w-16 h-4 rounded bg-[hsl(var(--bg-tertiary))]" />
        </div>
        <div className="w-24 h-8 rounded bg-[hsl(var(--bg-tertiary))]" />
        <div className="w-32 h-3 rounded bg-[hsl(var(--bg-tertiary))] mt-1" />
      </div>

      {/* Redeem button skeleton */}
      <div className="w-full h-10 rounded-xl bg-[hsl(var(--bg-tertiary))]" />

      {/* Daily Check-in skeleton */}
      <div className="p-4 border border-[hsl(var(--border-color))] rounded-xl bg-[hsl(var(--bg-secondary))]">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <div className="w-5 h-5 rounded-full bg-[hsl(var(--bg-tertiary))]" />
            <div className="w-16 h-4 rounded bg-[hsl(var(--bg-tertiary))]" />
          </div>
        </div>
        <div className="w-full h-10 rounded-lg bg-[hsl(var(--bg-tertiary))]" />
      </div>

      {/* Earn Opportunities skeleton */}
      <div className="p-4 border border-[hsl(var(--border-color))] rounded-xl bg-[hsl(var(--bg-secondary))]">
        <div className="w-24 h-4 rounded bg-[hsl(var(--bg-tertiary))] mb-3" />
        <div className="space-y-2">
          {[1, 2, 3].map((i) => (
            <div key={i} className="flex items-center justify-between p-2 rounded-lg bg-[hsl(var(--bg-tertiary))]">
              <div className="flex items-center gap-3">
                <div className="w-5 h-5 rounded-full bg-[hsl(var(--bg-hover))]" />
                <div className="w-24 h-3 rounded bg-[hsl(var(--bg-hover))]" />
              </div>
              <div className="w-8 h-3 rounded bg-[hsl(var(--bg-hover))]" />
            </div>
          ))}
        </div>
      </div>

      {/* Points History skeleton */}
      <div className="border border-[hsl(var(--border-color))] rounded-xl bg-[hsl(var(--bg-secondary))]">
        <div className="p-4 border-b border-[hsl(var(--border-color))]">
          <div className="w-16 h-4 rounded bg-[hsl(var(--bg-tertiary))] mb-1" />
          <div className="w-24 h-3 rounded bg-[hsl(var(--bg-tertiary))]" />
        </div>
        <div className="divide-y divide-[hsl(var(--border-color))]">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="p-3 flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-full bg-[hsl(var(--bg-tertiary))]" />
                <div>
                  <div className="w-20 h-3 rounded bg-[hsl(var(--bg-tertiary))] mb-1" />
                  <div className="w-12 h-2 rounded bg-[hsl(var(--bg-tertiary))]" />
                </div>
              </div>
              <div className="w-8 h-3 rounded bg-[hsl(var(--bg-tertiary))]" />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/**
 * Settings dialog component for managing user preferences and account settings.
 *
 * Provides a tabbed interface with five sections:
 * - **Profile**: Displays user avatar, nickname, email, and logout button
 * - **General**: Language selection, theme mode (dark/light), and accent color picker
 * - **Subscription**: Subscription status display and quota badges with redeem code support
 * - **Points**: Points balance, daily check-in, earn opportunities, and transaction history
 * - **Referral**: Referral program for inviting friends and earning rewards
 *
 * Features:
 * - Responsive layout with horizontal tabs on desktop, vertical on mobile
 * - Click-outside-to-close handled by Modal component
 * - Real-time theme and language changes without page reload
 * - Graceful logout with loading state to prevent double-clicks
 * - Integration with subscription system for quota management
 * - Uses Modal component for accessibility and consistent styling
 *
 * @param {SettingsDialogProps} props - Component props
 * @param {boolean} props.isOpen - Whether the dialog is visible
 * @param {() => void} props.onClose - Callback to close the dialog
 * @returns {React.ReactElement | null} The dialog element or null if closed
 *
 * @example
 * // Basic usage with state control
 * const [isOpen, setIsOpen] = useState(false);
 *
 * return (
 *   <>
 *     <button onClick={() => setIsOpen(true)}>Open Settings</button>
 *     <SettingsDialog isOpen={isOpen} onClose={() => setIsOpen(false)} />
 *   </>
 * );
 *
 * @example
 * // Integration with header menu
 * const [showSettings, setShowSettings] = useState(false);
 *
 * <UserMenu onSettingsClick={() => setShowSettings(true)} />
 * <SettingsDialog isOpen={showSettings} onClose={() => setShowSettings(false)} />
 */
export const SettingsDialog: React.FC<SettingsDialogProps> = ({ isOpen, onClose, defaultTab }) => {
  const isMobile = useIsMobile();
  const isPointsPanelEnabled = pointsConfig.panelEnabled;
  const { t: tSettings } = useTranslation('settings');
  const { t: tAuth } = useTranslation('auth');
  const { i18n } = useTranslation();
  const { theme, accentColor, setTheme, setAccentColor } = useTheme();
  const { user, logout } = useAuth();
  const [activeTab, setActiveTab] = useState<SettingsTab>('profile');
  const [analyticsEnabled, setAnalyticsEnabled] = useState(() => !isAnalyticsOptedOut());
  const [isLoggingOut, setIsLoggingOut] = useState(false);
  const prevIsOpenRef = useRef(false);
  const [showRedeemCodeModal, setShowRedeemCodeModal] = useState(false);
  const [showRedeemProModal, setShowRedeemProModal] = useState(false);
  const currentLanguage = normalizeLocale(i18n.language || i18n.resolvedLanguage);
  const settingsUpgradePrompt = getUpgradePromptDefinition('settings_subscription_upgrade');

  const handleLanguageChange = async (lang: 'zh' | 'en') => {
    if (currentLanguage === lang) return;

    if (typeof window !== 'undefined') {
      localStorage.setItem('zenstory-language', lang);
    }

    await i18n.changeLanguage(lang);
  };

  // Track all points-related query fetching states
  const isPointsFetching = useIsFetching({ queryKey: ['points-balance'] }) +
                         useIsFetching({ queryKey: ['check-in-status'] }) +
                         useIsFetching({ queryKey: ['points-transactions'] }) +
                         useIsFetching({ queryKey: ['earn-opportunities'] });

  // Track if we've ever had data (to distinguish initial load from refetch)
  const [hasLoadedOnce, setHasLoadedOnce] = useState(false);

  // Show skeleton only during initial load, not refetch
  const showPointsSkeleton = !hasLoadedOnce && isPointsFetching > 0;

  // Set hasLoadedOnce when data arrives (isFetching goes to 0)
  useEffect(() => {
    if (activeTab !== 'points') return;

    // When fetching completes and we haven't marked as loaded yet
    if (isPointsFetching === 0 && !hasLoadedOnce) {
      // Small delay to ensure data is rendered
      const timer = setTimeout(() => setHasLoadedOnce(true), 50);
      return () => clearTimeout(timer);
    }
  }, [activeTab, isPointsFetching, hasLoadedOnce]);

  // Reset hasLoadedOnce when dialog closes; consume defaultTab on open
  useEffect(() => {
    if (!isOpen) {
      setHasLoadedOnce(false);
      prevIsOpenRef.current = false;
    } else if (!prevIsOpenRef.current && defaultTab) {
      setActiveTab(defaultTab);
      prevIsOpenRef.current = true;
    } else {
      prevIsOpenRef.current = true;
    }
  }, [isOpen, defaultTab]);

  // Fallback to profile tab if points panel is disabled.
  useEffect(() => {
    if (!isPointsPanelEnabled && activeTab === 'points') {
      setActiveTab('profile');
    }
  }, [activeTab, isPointsPanelEnabled]);

  /**
   * Handles user logout with loading state protection.
   *
   * Prevents multiple logout requests by checking `isLoggingOut` state.
   * After successful logout, closes the dialog. Errors are silently caught
   * and loading state is always reset in the finally block.
   *
   * @returns {Promise<void>}
   */
  const handleLogout = async () => {
    if (isLoggingOut) return;
    setIsLoggingOut(true);
    try {
      await logout();
      onClose();
    } finally {
      setIsLoggingOut(false);
    }
  };

  /**
   * Navigation items configuration for the settings sidebar.
   *
   * Each item contains:
   * - `id`: Tab identifier matching SettingsTab type
   * - `icon`: Lucide icon component
   * - `label`: Translated label text via i18n
   */
  const navItems = [
    { id: 'profile' as const, icon: User, label: tSettings('nav.profile') },
    { id: 'general' as const, icon: Settings, label: tSettings('nav.general') },
    { id: 'subscription' as const, icon: CreditCard, label: tSettings('nav.subscription', '订阅') },
    ...(isPointsPanelEnabled
      ? [{ id: 'points' as const, icon: Coins, label: tSettings('nav.points', '积分') }]
      : []),
    { id: 'agent' as const, icon: Key, label: tSettings('nav.agent', 'API 密钥') },
    { id: 'referral' as const, icon: Users, label: tSettings('nav.referral', '邀请') },
  ];

  const handleTabKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>, index: number) => {
    let nextIndex: number | null = null;
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
      nextIndex = (index + 1) % navItems.length;
    } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
      nextIndex = (index - 1 + navItems.length) % navItems.length;
    } else if (event.key === 'Home') {
      nextIndex = 0;
    } else if (event.key === 'End') {
      nextIndex = navItems.length - 1;
    }

    if (nextIndex === null) return;
    event.preventDefault();
    const nextTab = navItems[nextIndex];
    setActiveTab(nextTab.id);
    document.getElementById(`settings-tab-${nextTab.id}`)?.focus();
  };

  return (
    <>
      {/* data-testid: modal-overlay - Modal backdrop for modal interaction tests */}
      <Modal
        open={isOpen}
        onClose={onClose}
        title={tSettings('title')}
        size="xl"
        className="h-[min(80vh,760px)]"
        showCloseButton={true}
        closeOnBackdropClick={true}
        closeOnEscape={true}
      >
        <div className={`flex h-full min-h-0 ${isMobile ? 'flex-col' : ''}`}>
          {/* Left navigation */}
          <div className={`${isMobile ? 'px-1 pb-2' : 'w-40 px-3 pb-4 shrink-0'}`}>
            <nav
              className={`${isMobile ? 'grid grid-cols-3 gap-1' : 'space-y-0.5'}`}
              role="tablist"
              aria-orientation={isMobile ? 'horizontal' : 'vertical'}
            >
              {navItems.map((item, index) => {
                const Icon = item.icon;
                const isActive = activeTab === item.id;
                return (
                  <button
                    key={item.id}
                    id={`settings-tab-${item.id}`}
                    type="button"
                    role="tab"
                    aria-selected={isActive}
                    aria-controls="settings-panel"
                    tabIndex={isActive ? 0 : -1}
                    onClick={() => setActiveTab(item.id)}
                    onKeyDown={(event) => handleTabKeyDown(event, index)}
                    data-testid={`settings-tab-${item.id}`}
                    className={`flex items-center text-sm transition-colors ${
                      isMobile ? 'flex-col gap-1 justify-center min-h-11 py-2 px-1' : 'w-full gap-2 py-2 px-3'
                    } rounded-lg ${
                      isActive
                        ? 'bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-primary))]'
                        : 'text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--bg-hover))]'
                    }`}
                  >
                    <Icon size={16} strokeWidth={1.5} />
                    <span>{item.label}</span>
                  </button>
                );
              })}
            </nav>
          </div>

          {/* Divider */}
          <div className={`${isMobile ? 'border-t' : 'border-l'} border-[hsl(var(--border-color))]`} />

          {/* Right content area */}
          <div
            className="flex-1 min-h-0 p-4 overflow-y-auto"
            id="settings-panel"
            role="tabpanel"
            aria-labelledby={`settings-tab-${activeTab}`}
            tabIndex={0}
          >
            {activeTab === 'profile' && (
              <div className="space-y-3">
                {/* User card */}
                <div className="flex items-center gap-3 p-3 rounded-xl border border-[hsl(var(--border-color))]">
                  <div className="w-12 h-12 rounded-full overflow-hidden bg-[hsl(var(--bg-tertiary))] shrink-0">
                    {user?.avatar_url ? (
                      <img src={user.avatar_url} alt="" className="w-full h-full object-cover" />
                    ) : (
                      <UserAvatar username={user?.nickname || user?.email || ''} size={48} />
                    )}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="font-medium text-[hsl(var(--text-primary))] truncate">
                      {user?.nickname || user?.email?.split('@')[0] || tSettings('profile.anonymous')}
                    </div>
                    <div className="text-sm text-[hsl(var(--text-secondary))] truncate">
                      {user?.email}
                    </div>
                  </div>
                </div>

                {/* Logout button */}
                <button
                  onClick={handleLogout}
                  disabled={isLoggingOut}
                  data-testid="settings-logout-button"
                  className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl border border-[hsl(var(--border-color))] text-sm text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-hover))] transition-colors disabled:opacity-50"
                >
                  <LogOut size={14} />
                  <span>{isLoggingOut ? tAuth('logout.loading') : tAuth('logout.button')}</span>
                </button>
              </div>
            )}

            {activeTab === 'general' && (
              <div className="space-y-5">
                {/* Language */}
                <div>
                  <div id="settings-language-label" className="text-xs font-medium text-[hsl(var(--text-secondary))] mb-2">
                    {tSettings('language.title')}
                  </div>
                  <div className="grid grid-cols-2 gap-2" role="radiogroup" aria-labelledby="settings-language-label">
                    {['zh', 'en'].map((lang) => (
                      <button
                        key={lang}
                        type="button"
                        role="radio"
                        aria-checked={currentLanguage === lang}
                        onClick={() => {
                          void handleLanguageChange(lang as 'zh' | 'en');
                        }}
                        data-testid={`language-button-${lang}`}
                        className={`p-2.5 rounded-lg border text-left text-sm transition-all ${
                          currentLanguage === lang
                            ? 'border-[hsl(var(--accent-primary))] bg-[hsl(var(--accent-primary)/0.08)]'
                            : 'border-[hsl(var(--border-color))] hover:bg-[hsl(var(--bg-hover))]'
                        }`}
                      >
                        <div className="font-medium text-[hsl(var(--text-primary))]">
                          {tSettings(`language.${lang}`, lang === 'zh' ? '中文' : 'English')}
                        </div>
                      </button>
                    ))}
                  </div>
                </div>

                {/* Privacy: product analytics opt-out (stored per browser) */}
                <div>
                  <div className="text-xs font-medium text-[hsl(var(--text-secondary))] mb-2">
                    {tSettings('privacy.title')}
                  </div>
                  <div className="flex items-start justify-between gap-3 p-2.5 rounded-lg border border-[hsl(var(--border-color))]">
                    <div className="min-w-0">
                      <div id="settings-analytics-label" className="text-sm font-medium text-[hsl(var(--text-primary))]">
                        {tSettings('privacy.analytics')}
                      </div>
                      <p className="mt-1 text-xs text-[hsl(var(--text-secondary))]">
                        {tSettings('privacy.analyticsDescription')}{' '}
                        <a
                          href="/privacy-policy"
                          target="_blank"
                          rel="noopener noreferrer"
                          className="underline hover:text-[hsl(var(--text-primary))]"
                        >
                          {tSettings('privacy.privacyLink')}
                        </a>
                      </p>
                    </div>
                    <button
                      type="button"
                      role="switch"
                      aria-checked={analyticsEnabled}
                      aria-labelledby="settings-analytics-label"
                      data-testid="analytics-toggle"
                      onClick={() => {
                        const nextEnabled = !analyticsEnabled;
                        setAnalyticsOptOut(!nextEnabled);
                        setAnalyticsEnabled(nextEnabled);
                      }}
                      className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors ${
                        analyticsEnabled
                          ? 'bg-[hsl(var(--accent-primary))]'
                          : 'bg-[hsl(var(--bg-tertiary))] border border-[hsl(var(--border-color))]'
                      }`}
                    >
                      <span
                        className={`inline-block h-4 w-4 rounded-full bg-white shadow transition-transform ${
                          analyticsEnabled ? 'translate-x-4' : 'translate-x-0.5'
                        }`}
                      />
                    </button>
                  </div>
                </div>

                {/* Theme */}
                <div>
                  <div id="settings-theme-label" className="text-xs font-medium text-[hsl(var(--text-secondary))] mb-2">
                    {tSettings('theme.mode')}
                  </div>
                  <div className="grid grid-cols-2 gap-2" role="radiogroup" aria-labelledby="settings-theme-label">
                    {[
                      { id: 'dark', icon: Moon, label: tSettings('theme.dark') },
                      { id: 'light', icon: Sun, label: tSettings('theme.light') },
                    ].map((item) => (
                      <button
                        key={item.id}
                        type="button"
                        role="radio"
                        aria-checked={theme === item.id}
                        onClick={() => setTheme(item.id as 'dark' | 'light')}
                        data-testid={`theme-button-${item.id}`}
                        className={`p-2.5 rounded-lg border text-center text-sm transition-all ${
                          theme === item.id
                            ? 'border-[hsl(var(--accent-primary))] bg-[hsl(var(--accent-primary)/0.08)]'
                            : 'border-[hsl(var(--border-color))] hover:bg-[hsl(var(--bg-hover))]'
                        }`}
                      >
                        <item.icon className="w-4 h-4 mx-auto mb-1" />
                        <div className="text-[hsl(var(--text-primary))]">{item.label}</div>
                      </button>
                    ))}
                  </div>
                </div>

                {/* Accent color */}
                <div>
                  <div id="settings-accent-label" className="text-xs font-medium text-[hsl(var(--text-secondary))] mb-2">
                    {tSettings('theme.color')}
                  </div>
                  <div className="flex gap-2" role="radiogroup" aria-labelledby="settings-accent-label">
                    {[
                      { value: '#4a9eff', name: tSettings('theme.colors.blue', '蓝色') },
                      { value: '#22c55e', name: tSettings('theme.colors.green', '绿色') },
                      { value: '#fbbf24', name: tSettings('theme.colors.yellow', '黄色') },
                      { value: '#f87171', name: tSettings('theme.colors.red', '红色') },
                      { value: '#ec4899', name: tSettings('theme.colors.pink', '粉色') },
                      { value: '#8b5cf6', name: tSettings('theme.colors.purple', '紫色') },
                    ].map(({ value: color, name }) => (
                      <button
                        key={color}
                        type="button"
                        role="radio"
                        aria-label={name}
                        aria-checked={accentColor === color}
                        onClick={() => setAccentColor(color)}
                        data-testid="accent-color-button"
                        data-color={color}
                        className={`w-7 h-7 rounded-full transition-transform hover:scale-110 ${
                          accentColor === color ? 'ring-2 ring-offset-2 ring-offset-[hsl(var(--bg-card))]' : ''
                        }`}
                        style={{ backgroundColor: color, '--tw-ring-color': color } as React.CSSProperties}
                      />
                    ))}
                  </div>
                </div>
              </div>
            )}

            {activeTab === 'subscription' && (
              <div className="space-y-4">
                <SubscriptionStatus
                  onRedeemClick={() => setShowRedeemCodeModal(true)}
                  onUpgradeClick={() => {
                    onClose();
                    trackUpgradeClick(
                      settingsUpgradePrompt.source,
                      'direct',
                      'billing',
                      'page'
                    );
                    window.location.assign(
                      buildUpgradeUrl(settingsUpgradePrompt.billingPath, settingsUpgradePrompt.source)
                    );
                  }}
                />
                <div className="mt-4">
                  <h4 className="text-xs font-medium text-[hsl(var(--text-secondary))] mb-2">
                    {tSettings('subscription.usage', '使用量')}
                  </h4>
                  <QuotaBadge />
                </div>
              </div>
            )}

            {activeTab === 'points' && isPointsPanelEnabled && (
              <div className="relative">
                {/* Skeleton overlay - only shown during initial load */}
                <div
                  className={`absolute inset-0 z-10 transition-opacity duration-200 ${
                    showPointsSkeleton ? 'opacity-100' : 'opacity-0 pointer-events-none'
                  }`}
                >
                  <PointsTabSkeleton />
                </div>

                {/* Actual content - visible when not loading or when refetching */}
                <div
                  className={`space-y-4 transition-opacity duration-200 ${
                    showPointsSkeleton ? 'opacity-0' : 'opacity-100'
                  }`}
                >
                  <PointsBalance />
                  <button
                    onClick={() => setShowRedeemProModal(true)}
                    className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl border border-[hsl(var(--border-color))] text-sm text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-hover))] transition-colors"
                  >
                    <CreditCard size={14} />
                    <span>{tSettings('points.redeemPro', '用积分兑换 Pro 会员')}</span>
                  </button>
                  <DailyCheckIn />
                  <EarnOpportunities />
                  <PointsHistory />
                </div>
              </div>
            )}

            {activeTab === 'agent' && (
              <div className="space-y-4">
                <AgentApiKeysPanel />
              </div>
            )}

            {activeTab === 'referral' && (
              <div className="space-y-4">
                <ReferralStats />
                <InviteCodeList />
              </div>
            )}
          </div>
        </div>
      </Modal>
      <RedeemCodeModal
        isOpen={showRedeemCodeModal}
        onClose={() => setShowRedeemCodeModal(false)}
        source={settingsUpgradePrompt.source}
      />
      <RedeemProModal
        isOpen={showRedeemProModal}
        onClose={() => setShowRedeemProModal(false)}
      />
    </>
  );
};

export default SettingsDialog;
