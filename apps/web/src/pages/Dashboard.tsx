import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useLocation, Outlet } from "react-router-dom";
import {
  Home, LogOut, Menu, X, Zap, Library, FolderOpen, Compass
} from "../components/icons";
import {
  ChevronDown,
  CreditCard,
  Languages,
  Mail,
  Moon,
  Settings,
  Shield,
  Sun,
} from "lucide-react";
import { Logo, LogoMark } from "../components/Logo";
import { useAuth } from "../contexts/AuthContext";
import { useProject } from "../contexts/ProjectContext";
import { useTheme } from "../contexts/ThemeContext";
import { ProductTourProvider } from "../contexts/ProductTourContext";
import { useIsMobile, useIsTablet, useIsDesktop } from "../hooks/useMediaQuery";
import { useProductTour } from "../hooks/useProductTour";
import { getPersonaOnboardingData } from "../lib/onboardingPersona";
import { SettingsDialog, type SettingsTab } from "../components/SettingsDialog";
import { UserAvatar } from "../components/UserMenu";
import { normalizeLocale } from "../lib/i18n-helpers";
import { CoachmarkLayer } from "../components/onboarding/CoachmarkLayer";
import { DASHBOARD_FIRST_RUN_TOUR } from "../config/productTours/dashboardFirstRun";
import { dashboardOnboardingFlags } from "../config/dashboardOnboarding";
import { inspirationsConfig } from "../config/inspirations";
import { SUPPORT_EMAIL, SUPPORT_MAILTO } from "../config/support";

function hasExplicitTimezone(value: string): boolean {
  return /([zZ]|[+-]\d{2}:?\d{2})$/.test(value.trim());
}

function parseCreatedAtTimestamp(createdAt: string | null | undefined): number | null {
  if (!createdAt) return null;
  const trimmed = createdAt.trim();
  if (!trimmed) return null;
  const normalized = hasExplicitTimezone(trimmed) ? trimmed : `${trimmed}Z`;
  const timestamp = Date.parse(normalized);
  return Number.isFinite(timestamp) ? timestamp : null;
}

function isNewUserWithinDays(createdAt: string | null | undefined, days: number): boolean {
  const createdTimestamp = parseCreatedAtTimestamp(createdAt);
  if (createdTimestamp === null) return false;
  const ageMs = Date.now() - createdTimestamp;
  const windowMs = days * 24 * 60 * 60 * 1000;
  return ageMs >= 0 && ageMs <= windowMs;
}

// Sidebar navigation items - will be dynamic based on language
const NAV_ITEMS = [
  { id: "home", icon: Home, labelKey: "nav.home", path: "/dashboard" },
  { id: "projects", icon: FolderOpen, labelKey: "nav.projects", path: "/dashboard/projects" },
  { id: "materials", icon: Library, labelKey: "nav.materials", path: "/dashboard/materials" },
  { id: "inspirations", icon: Compass, labelKey: "nav.inspirations", path: "/dashboard/inspirations" },
  { id: "skills", icon: Zap, labelKey: "nav.skills", path: "/dashboard/skills" },
  { id: "billing", icon: CreditCard, labelKey: "nav.billing", path: "/dashboard/billing" },
];

// Inner Dashboard component that uses mobile context
function DashboardContent() {
  const { t, i18n } = useTranslation(['dashboard', 'settings']);
  const navigate = useNavigate();
  const location = useLocation();
  const { user, logout } = useAuth();
  const { theme, setTheme } = useTheme();

  // Mobile and desktop detection
  const isMobile = useIsMobile();
  const isTablet = useIsTablet();
  const isDesktop = useIsDesktop();

  const [showMobileMenu, setShowMobileMenu] = useState(false);
  const mobileMenuRef = useRef<HTMLElement | null>(null);
  const mobileMenuTriggerRef = useRef<HTMLButtonElement | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [settingsDefaultTab, setSettingsDefaultTab] = useState<string | undefined>(undefined);

  // Deep-link: auto-open settings to a specific tab via location.state.openSettingsTab
  useEffect(() => {
    const state = location.state as { openSettingsTab?: string } | null;
    if (state?.openSettingsTab) {
      setSettingsDefaultTab(state.openSettingsTab); // eslint-disable-line react-hooks/set-state-in-effect
      setShowSettings(true);
      navigate(location.pathname, { replace: true, state: {} });
    }
  }, [location.state, navigate, location.pathname]);
  const [showUserPanel, setShowUserPanel] = useState(false);
  const userPanelRef = useRef<HTMLDivElement | null>(null);
  const userPanelTriggerRef = useRef<HTMLButtonElement | null>(null);
  const currentLanguage: 'zh' | 'en' = normalizeLocale(i18n.language || i18n.resolvedLanguage);
  const quickSettingsLabel = t('dashboard:userPanel.quickSettings');
  const openUserPanelLabel = t('dashboard:userPanel.openPanel');
  const openSettingsLabel = t('dashboard:userPanel.openSettings');
  const adminPanelLabel = t('dashboard:userPanel.adminPanel');
  const replayTourLabel = t('dashboard:userPanel.replayTour');
  const contactSupportLabel = t('dashboard:userPanel.contactSupport');
  const { restartTour, isEnabled: isTourEnabled } = useProductTour();
  const navItems = inspirationsConfig.enabled
    ? NAV_ITEMS
    : NAV_ITEMS.filter((item) => item.id !== "inspirations");

  // Determine active nav based on current route
  const getActiveNav = () => {
    if (location.pathname.startsWith("/dashboard/inspirations")) return "inspirations";
    if (location.pathname === "/dashboard/projects") return "projects";
    if (location.pathname === "/dashboard/materials") return "materials";
    if (location.pathname === "/dashboard/skills") return "skills";
    if (location.pathname.startsWith("/dashboard/billing")) return "billing";
    return "home";
  };
  const activeNav = getActiveNav();


  const handleLogout = () => {
    logout();
    setShowUserPanel(false);
    setShowMobileMenu(false);
    navigate("/");
  };

  const handleNavItemClick = (item: typeof NAV_ITEMS[0]) => {
    navigate(item.path);
    setShowMobileMenu(false);
    setShowUserPanel(false);
  };

  const handleThemeSwitch = (nextTheme: 'dark' | 'light') => {
    setTheme(nextTheme);
  };

  const handleLanguageSwitch = async (lang: 'zh' | 'en') => {
    if (currentLanguage === lang) return;

    if (typeof window !== 'undefined') {
      localStorage.setItem('zenstory-language', lang);
    }

    await i18n.changeLanguage(lang);
  };

  const handleOpenSettings = () => {
    setShowSettings(true);
    setShowUserPanel(false);
    setShowMobileMenu(false);
  };

  const handleOpenAdmin = () => {
    setShowUserPanel(false);
    setShowMobileMenu(false);
    navigate('/admin');
  };

  useEffect(() => {
    if (!showUserPanel) return;

    const handleClickOutside = (event: MouseEvent) => {
      const target = event.target as Node;
      const clickedPanel = userPanelRef.current?.contains(target);
      const clickedTrigger = userPanelTriggerRef.current?.contains(target);

      if (!clickedPanel && !clickedTrigger) {
        setShowUserPanel(false);
      }
    };

    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setShowUserPanel(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    document.addEventListener('keydown', handleEscape);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleEscape);
    };
  }, [showUserPanel]);

  useEffect(() => {
    if (!showMobileMenu) return;

    const menuTrigger = mobileMenuTriggerRef.current;
    mobileMenuRef.current?.querySelector<HTMLElement>('button')?.focus();
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setShowMobileMenu(false);
    };
    document.addEventListener('keydown', handleEscape);
    return () => {
      document.removeEventListener('keydown', handleEscape);
      menuTrigger?.focus();
    };
  }, [showMobileMenu]);

  const renderUserPanel = (alignment: 'desktop' | 'tablet') => (
    <div
      ref={userPanelRef}
      className={`absolute w-72 rounded-2xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] shadow-xl z-[1200] pointer-events-auto p-3 space-y-3 ${
        alignment === 'desktop' ? 'left-0 bottom-full mb-3' : 'left-full ml-2 bottom-0'
      }`}
    >
      <div className="flex items-center gap-3 rounded-xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-tertiary)/0.45)] p-2.5">
        <UserAvatar
          username={user?.username || "User"}
          avatarUrl={user?.avatar_url}
          size={36}
        />
        <div className="min-w-0">
          <div className="text-sm font-semibold text-[hsl(var(--text-primary))] truncate">
            {user?.username || "User"}
          </div>
          <div className="text-xs text-[hsl(var(--text-secondary))] truncate">
            {user?.email || ""}
          </div>
        </div>
      </div>

      <div className="rounded-xl border border-[hsl(var(--border-color))] p-2.5 space-y-2">
        <div className="text-[11px] uppercase tracking-wide font-semibold text-[hsl(var(--text-tertiary))]">
          {quickSettingsLabel}
        </div>

        <div className="flex items-center justify-between gap-3">
          <div className="text-xs text-[hsl(var(--text-secondary))] flex items-center gap-1.5">
            <Sun className="w-3.5 h-3.5" />
            {t('settings:theme.mode')}
          </div>
          <div className="inline-flex rounded-lg border border-[hsl(var(--border-color))] p-0.5 bg-[hsl(var(--bg-primary))]">
            <button
              onClick={() => handleThemeSwitch('dark')}
              aria-label={t('settings:theme.dark')}
              className={`px-2 py-1 rounded text-xs transition-colors ${
                theme === 'dark'
                  ? 'bg-[hsl(var(--accent-primary)/0.18)] text-[hsl(var(--accent-primary))]'
                  : 'text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))]'
              }`}
              title={t('settings:theme.dark')}
            >
              <Moon className="w-3.5 h-3.5" />
            </button>
            <button
              onClick={() => handleThemeSwitch('light')}
              aria-label={t('settings:theme.light')}
              className={`px-2 py-1 rounded text-xs transition-colors ${
                theme === 'light'
                  ? 'bg-[hsl(var(--accent-primary)/0.18)] text-[hsl(var(--accent-primary))]'
                  : 'text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))]'
              }`}
              title={t('settings:theme.light')}
            >
              <Sun className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>

        <div className="flex items-center justify-between gap-3">
          <div className="text-xs text-[hsl(var(--text-secondary))] flex items-center gap-1.5">
            <Languages className="w-3.5 h-3.5" />
            {t('settings:language.label')}
          </div>
          <div className="inline-flex rounded-lg border border-[hsl(var(--border-color))] p-0.5 bg-[hsl(var(--bg-primary))]">
            <button
              onClick={() => {
                void handleLanguageSwitch('zh');
              }}
              aria-label="Switch to Chinese"
              data-testid="dashboard-quick-language-zh"
              className={`px-2 py-1 rounded text-xs transition-colors ${
                currentLanguage === 'zh'
                  ? 'bg-[hsl(var(--accent-primary)/0.18)] text-[hsl(var(--accent-primary))]'
                  : 'text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))]'
              }`}
            >
              中
            </button>
            <button
              onClick={() => {
                void handleLanguageSwitch('en');
              }}
              aria-label="Switch to English"
              data-testid="dashboard-quick-language-en"
              className={`px-2 py-1 rounded text-xs transition-colors ${
                currentLanguage === 'en'
                  ? 'bg-[hsl(var(--accent-primary)/0.18)] text-[hsl(var(--accent-primary))]'
                  : 'text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))]'
              }`}
            >
              EN
            </button>
          </div>
        </div>
      </div>

      <div className="space-y-1">
        <button
          onClick={handleOpenSettings}
          data-testid="dashboard-open-settings-button"
          className="w-full flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-sm text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))] transition-colors"
        >
          <Settings className="w-4 h-4" />
          {openSettingsLabel}
        </button>

        {isTourEnabled && (
          <button
            type="button"
            onClick={() => {
              setShowUserPanel(false);
              if (location.pathname !== '/dashboard') {
                navigate('/dashboard');
                window.setTimeout(() => {
                  restartTour();
                }, 0);
                return;
              }
              restartTour();
            }}
            data-testid="dashboard-replay-tour-button"
            className="w-full flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-sm text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))] transition-colors"
          >
            <Compass className="w-4 h-4" />
            {replayTourLabel}
          </button>
        )}

        {user?.is_superuser && (
          <button
            onClick={handleOpenAdmin}
            className="w-full flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-sm text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))] transition-colors"
          >
            <Shield className="w-4 h-4" />
            {adminPanelLabel}
          </button>
        )}

        <a
          href={SUPPORT_MAILTO}
          data-testid="dashboard-contact-support-link"
          className="w-full flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-sm text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))] transition-colors"
        >
          <Mail className="w-4 h-4 shrink-0" />
          <span className="flex flex-col min-w-0">
            <span>{contactSupportLabel}</span>
            <span className="text-xs text-[hsl(var(--text-secondary))] truncate">{SUPPORT_EMAIL}</span>
          </span>
        </a>

        <button
          onClick={handleLogout}
          className="w-full flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-sm text-[hsl(var(--error))] hover:bg-[hsl(var(--error)/0.1)] transition-colors"
        >
          <LogOut className="w-4 h-4" />
          {t('nav.logout')}
        </button>
      </div>
    </div>
  );

  return (
    <div className="h-dvh min-h-0 overflow-hidden bg-[hsl(var(--bg-primary))] flex">
      {/* Tablet: Collapsed Sidebar (icons only) */}
      {isTablet && (
        <aside className="w-20 flex-shrink-0 bg-[hsl(var(--bg-secondary))] border-r border-[hsl(var(--border-color))] flex flex-col h-full min-h-0 z-40">
          {/* Logo */}
          <div className="h-12 flex items-center justify-center border-b border-[hsl(var(--border-color))]">
            <LogoMark className="w-7 h-7" />
          </div>

          {/* Navigation */}
          <nav className="flex-1 p-2 space-y-1">
            {navItems.map((item) => (
              <button
                key={item.id}
                onClick={() => handleNavItemClick(item)}
                className={`w-full flex items-center justify-center gap-2 px-2 py-3 rounded-lg transition-all touch-target ${
                  activeNav === item.id
                    ? "bg-[hsl(var(--accent-primary)/0.15)] text-[hsl(var(--accent-primary))]"
                    : "text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--bg-tertiary))] hover:text-[hsl(var(--text-primary))]"
                }`}
                title={t(item.labelKey)}
              >
                <item.icon className="w-5 h-5" />
              </button>
            ))}

          </nav>

          {/* User Info */}
          <div className="p-2 border-t border-[hsl(var(--border-color))] relative">
            <div className="flex items-center justify-center">
              <button
                ref={userPanelTriggerRef}
                onClick={() => setShowUserPanel((prev) => !prev)}
                data-testid="dashboard-user-panel-toggle"
                className="w-full flex flex-col items-center justify-center gap-1 px-2 py-2.5 rounded-lg text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--bg-tertiary))] hover:text-[hsl(var(--text-primary))] transition-all"
                title={openUserPanelLabel}
                aria-label={openUserPanelLabel}
              >
                <UserAvatar
                  username={user?.username || "User"}
                  avatarUrl={user?.avatar_url}
                  size={32}
                />
                <ChevronDown
                  className={`w-3.5 h-3.5 transition-transform ${showUserPanel ? 'rotate-180' : ''}`}
                />
              </button>
            </div>
            {showUserPanel && (
              renderUserPanel('tablet')
            )}
          </div>
        </aside>
      )}

      {/* Desktop: Full Sidebar */}
      {isDesktop && (
        <aside className="w-56 flex-shrink-0 bg-[hsl(var(--bg-secondary))] border-r border-[hsl(var(--border-color))] flex flex-col h-full min-h-0 z-40">
          {/* Logo */}
          <div className="h-12 px-4 flex items-center border-b border-[hsl(var(--border-color))]">
            <Logo className="h-7 w-auto" />
          </div>

          {/* Navigation */}
          <nav className="flex-1 p-3 space-y-1">
            {navItems.map((item) => (
              <button
                key={item.id}
                onClick={() => handleNavItemClick(item)}
                className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all ${
                  activeNav === item.id
                    ? "bg-[hsl(var(--accent-primary)/0.15)] text-[hsl(var(--accent-primary))]"
                    : "text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--bg-tertiary))] hover:text-[hsl(var(--text-primary))]"
                }`}
              >
                <item.icon className="w-4 h-4" />
                {t(item.labelKey)}
              </button>
            ))}

          </nav>

        {/* User Info */}
        <div className="p-3 border-t border-[hsl(var(--border-color))] relative">
          <button
            ref={userPanelTriggerRef}
            onClick={() => setShowUserPanel((prev) => !prev)}
            data-testid="dashboard-user-panel-toggle"
            className="w-full flex items-center gap-3 px-3 py-2 rounded-xl border border-transparent hover:border-[hsl(var(--border-color))] hover:bg-[hsl(var(--bg-tertiary)/0.45)] transition-all"
            title={openUserPanelLabel}
            aria-label={openUserPanelLabel}
          >
            <UserAvatar
              username={user?.username || "User"}
              avatarUrl={user?.avatar_url}
              size={32}
            />
            <div className="flex-1 min-w-0 text-left">
              <div className="text-sm font-medium text-[hsl(var(--text-primary))] truncate">
                {user?.username}
              </div>
              <div className="text-xs text-[hsl(var(--text-secondary))] truncate">
                {user?.email || ""}
              </div>
            </div>
            <ChevronDown className={`w-4 h-4 text-[hsl(var(--text-secondary))] transition-transform ${showUserPanel ? 'rotate-180' : ''}`} />
          </button>
          {showUserPanel && renderUserPanel('desktop')}
        </div>
      </aside>
      )}

      {/* Main Content */}
      <main className="flex-1 min-w-0 min-h-0 overflow-auto" style={{ contain: 'layout style paint', scrollbarGutter: 'stable' }}>
        {/* Mobile Header */}
        {isMobile && (
          <header className="h-12 bg-[hsl(var(--bg-secondary))] flex items-center px-4 justify-between shrink-0 shadow-sm sticky top-0 z-30">
            {/* Left: Logo */}
            <div className="flex items-center">
              <Logo className="h-7 w-auto" />
            </div>

            {/* Right: Menu Button */}
            <div className="flex items-center gap-1">
              <button
                ref={mobileMenuTriggerRef}
                onClick={() => setShowMobileMenu(!showMobileMenu)}
                aria-label={showMobileMenu ? 'Close mobile menu' : 'Open mobile menu'}
                aria-expanded={showMobileMenu}
                aria-controls="dashboard-mobile-navigation"
                className="p-2 hover:bg-[hsl(var(--bg-tertiary))] rounded text-[hsl(var(--text-primary))] transition-colors"
              >
                {showMobileMenu ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
              </button>
            </div>
          </header>
        )}

        {/* Mobile Dropdown Menu */}
        {isMobile && showMobileMenu && (
          <nav
            ref={mobileMenuRef}
            id="dashboard-mobile-navigation"
            aria-label="Mobile navigation"
            className="absolute top-12 left-0 right-0 bg-[hsl(var(--bg-secondary))] border-b border-[hsl(var(--separator-color))] shadow-lg z-40"
          >
            <div className="flex flex-col p-2 gap-1">
              {navItems.map((item) => {
                const Icon = item.icon;
                return (
                  <button
                    key={item.id}
                    onClick={() => {
                      handleNavItemClick(item);
                      setShowMobileMenu(false);
                    }}
                    className="flex items-center gap-3 px-3 py-2.5 hover:bg-[hsl(var(--bg-tertiary))] rounded text-sm text-[hsl(var(--text-primary))] transition-colors text-left"
                  >
                    <Icon size={18} />
                    {t(item.labelKey)}
                  </button>
                );
              })}

              <div className="h-px bg-[hsl(var(--separator-color))] my-1" />

              {/* User section in mobile menu */}
              <div className="flex items-center gap-3 px-3 py-2">
                <UserAvatar
                  username={user?.username || "User"}
                  avatarUrl={user?.avatar_url}
                  size={32}
                />
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-medium text-[hsl(var(--text-primary))] truncate">
                    {user?.username}
                  </div>
                  <div className="text-xs text-[hsl(var(--text-secondary))]">
                    {user?.email || ""}
                  </div>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-2 px-3 pb-2">
                <button
                  onClick={() => handleThemeSwitch(theme === 'dark' ? 'light' : 'dark')}
                  aria-label="Toggle theme"
                  className="flex items-center justify-center gap-1.5 px-2 py-2 rounded-lg border border-[hsl(var(--border-color))] text-xs text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))] transition-colors"
                >
                  {theme === 'dark' ? <Sun className="w-3.5 h-3.5" /> : <Moon className="w-3.5 h-3.5" />}
                  {theme === 'dark' ? t('settings:theme.light') : t('settings:theme.dark')}
                </button>
                <button
                  onClick={() => {
                    void handleLanguageSwitch(currentLanguage === 'zh' ? 'en' : 'zh');
                  }}
                  aria-label="Toggle language"
                  className="flex items-center justify-center gap-1.5 px-2 py-2 rounded-lg border border-[hsl(var(--border-color))] text-xs text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))] transition-colors"
                >
                  <Languages className="w-3.5 h-3.5" />
                  {currentLanguage === 'zh' ? 'EN' : '中文'}
                </button>
              </div>
              <button
                onClick={handleOpenSettings}
                className="flex items-center gap-3 px-3 py-2.5 hover:bg-[hsl(var(--bg-tertiary))] rounded text-sm text-[hsl(var(--text-primary))] transition-colors text-left"
              >
                <Settings size={18} />
                {openSettingsLabel}
              </button>
              {isTourEnabled && (
                <button
                  type="button"
                  onClick={() => {
                    if (location.pathname !== '/dashboard') {
                      navigate('/dashboard');
                      window.setTimeout(() => {
                        restartTour();
                      }, 0);
                    } else {
                      restartTour();
                    }
                    setShowMobileMenu(false);
                  }}
                  className="flex items-center gap-3 px-3 py-2.5 hover:bg-[hsl(var(--bg-tertiary))] rounded text-sm text-[hsl(var(--text-primary))] transition-colors text-left"
                >
                  <Compass size={18} />
                  {replayTourLabel}
                </button>
              )}
              <a
                href={SUPPORT_MAILTO}
                data-testid="dashboard-contact-support-link-mobile"
                className="flex items-center gap-3 px-3 py-2.5 hover:bg-[hsl(var(--bg-tertiary))] rounded text-sm text-[hsl(var(--text-primary))] transition-colors text-left"
              >
                <Mail size={18} className="shrink-0" />
                <span className="flex flex-col min-w-0">
                  <span>{contactSupportLabel}</span>
                  <span className="text-xs text-[hsl(var(--text-secondary))] truncate">{SUPPORT_EMAIL}</span>
                </span>
              </a>
              <button
                onClick={() => {
                  handleLogout();
                  setShowMobileMenu(false);
                }}
                className="flex items-center gap-3 px-3 py-2.5 hover:bg-[hsl(var(--error)/0.1)] rounded text-sm text-[hsl(var(--error))] transition-colors text-left"
              >
                <LogOut size={18} />
                {t('nav.logout')}
              </button>
            </div>
          </nav>
        )}

        <div className={`max-w-5xl mx-auto ${isMobile ? "px-4 py-6" : "px-6 py-8"}`}>
          <Outlet />
        </div>
      </main>
      <SettingsDialog
        isOpen={showSettings}
        onClose={() => { setShowSettings(false); setSettingsDefaultTab(undefined); }}
        defaultTab={settingsDefaultTab as SettingsTab | undefined}
      />
      <CoachmarkLayer />
    </div>
  );
}

// Main Dashboard component
export default function Dashboard() {
  const { user } = useAuth();
  const { projects, loading: projectsLoading } = useProject();
  const location = useLocation();
  const hasPersonaOnboarding = Boolean(user?.id && getPersonaOnboardingData(user.id));
  const isDashboardHome = location.pathname === '/dashboard';
  const hasZeroProjects = !projectsLoading && projects.length === 0;
  const isNewUser = isNewUserWithinDays(user?.created_at, 7);
  const isOnboardingReturn = Boolean(
    hasPersonaOnboarding
      && location.state
      && typeof location.state === 'object'
      && 'startDashboardCoachmark' in location.state
      && (location.state as { startDashboardCoachmark?: boolean }).startDashboardCoachmark,
  );
  const isTourEligible = Boolean(
    hasPersonaOnboarding
      && isDashboardHome
      && hasZeroProjects
      && (isOnboardingReturn || isNewUser),
  );
  const autoStartReason = isOnboardingReturn
    ? 'onboarding_return'
    : isTourEligible
      ? 'new_user_zero_project'
      : null;

  return (
    <ProductTourProvider
      key={user?.id ?? 'anon'}
      tour={DASHBOARD_FIRST_RUN_TOUR}
      userId={user?.id ?? null}
      enabled={dashboardOnboardingFlags.coachmarkTourEnabled}
      eligible={isTourEligible}
      autoStartReason={autoStartReason}
    >
      <DashboardContent />
    </ProductTourProvider>
  );
}
