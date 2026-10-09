/**
 * @fileoverview Header component - Application navigation and actions bar.
 *
 * This component provides the main application header with:
 * - Logo and branding (responsive: icon-only on mobile, full logo on desktop)
 * - Project switcher dropdown for navigating between projects
 * - Project actions (export, version history) - only shown when a project is open
 * - Settings dialog trigger
 * - User menu with authentication options
 * - Mobile-responsive dropdown menu for smaller screens
 *
 * The header is displayed at the top of the application in all views
 * and provides quick access to common application actions.
 *
 * @module components/Header
 */
import React, { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { History, Settings, Menu, X, Download, Bug, CreditCard, BarChart3, BookOpen } from "lucide-react";
import { Logo, LogoMark } from "./Logo";
import { ProjectSwitcher } from "./ProjectSwitcher";
import { VersionHistoryPanel } from "./VersionHistoryPanel";
import { DropdownMenu } from "./ui/DropdownMenu";
import { SettingsDialog } from "./SettingsDialog";
import { UserMenu, UserMenuMobile } from "./UserMenu";
import { FeedbackDialog } from "./feedback/FeedbackDialog";
import { useProject } from "../contexts/ProjectContext";
import { useIsMobile } from "../hooks/useMediaQuery";
import { useExport } from "../hooks/useExport";
import { ApiError } from "../lib/apiClient";
import { handleApiError } from "../lib/errorHandler";
import { toast } from "../lib/toast";
import { logger } from "../lib/logger";
import { UpgradePromptModal } from "./subscription/UpgradePromptModal";
import { buildUpgradeUrl, getUpgradePromptDefinition } from "../config/upgradeExperience";

/**
 * Props for the Header component.
 *
 * Currently empty as Header is a self-contained component that
 * derives all necessary state from React contexts (ProjectContext).
 *
 * @interface HeaderProps
 */
// eslint-disable-next-line @typescript-eslint/no-empty-object-type
interface HeaderProps {}

/**
 * Main application header component with navigation and actions.
 *
 * Renders a responsive header bar containing:
 * - **Left section**: Logo (clickable to navigate to dashboard) and ProjectSwitcher
 * - **Right section (desktop)**: Export button, version history, settings, user menu
 * - **Right section (mobile)**: Hamburger menu toggle button
 *
 * Desktop Layout:
 * ```
 * [Logo] | [ProjectSwitcher]          [Export] [History] [Settings] | [UserMenu]
 * ```
 *
 * Mobile Layout:
 * ```
 * [LogoMark] [ProjectSwitcher...]                              [MenuToggle]
 * ```
 *
 * The mobile menu dropdown includes all action buttons vertically stacked
 * with the user menu at the bottom.
 *
 * @returns The header JSX element with modals attached
 *
 * @example
 * // Basic usage (typically rendered in Layout component)
 * <Header />
 *
 * @example
 * // Header appears at top of Layout
 * <div className="app">
 *   <Header />
 *   <main>{content}</main>
 * </div>
 */
export const Header: React.FC<HeaderProps> = () => {
  const navigate = useNavigate();
  const { t } = useTranslation(['editor', 'common', 'dashboard', 'home']);
  const exportUpgradePrompt = getUpgradePromptDefinition("export_format_quota_blocked");
  const { currentProjectId, triggerFileTreeRefresh, setSelectedItem } = useProject();
  const [showSettings, setShowSettings] = useState(false);
  const [showVersionHistory, setShowVersionHistory] = useState(false);
  const [showFeedbackDialog, setShowFeedbackDialog] = useState(false);
  const [showExportUpgradeModal, setShowExportUpgradeModal] = useState(false);
  const [showMobileMenu, setShowMobileMenu] = useState(false);
  const mobileMenuToggleRef = useRef<HTMLButtonElement>(null);
  const isMobile = useIsMobile();
  const { exportDrafts } = useExport(currentProjectId);
  const desktopIconButtonClass = "p-2.5 min-h-[44px] min-w-[44px] flex items-center justify-center hover:bg-[hsl(var(--bg-tertiary))] rounded text-[hsl(var(--text-primary))] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-secondary))]";
  const mobileMenuId = "header-mobile-menu";

  const handleOpenBilling = (closeMobileMenu = false) => {
    navigate('/dashboard/billing');
    if (closeMobileMenu) {
      setShowMobileMenu(false);
    }
  };

  const handleOpenProjectDashboard = (closeMobileMenu = false) => {
    if (!currentProjectId) return;
    navigate(`/project/${currentProjectId}/dashboard`);
    if (closeMobileMenu) {
      setShowMobileMenu(false);
    }
  };

  const handleOpenDocs = (closeMobileMenu = false) => {
    navigate('/docs');
    if (closeMobileMenu) {
      setShowMobileMenu(false);
    }
  };

  const handleExport = async (closeMobileMenu = false) => {
    if (!currentProjectId) return;
    try {
      await exportDrafts();
      toast.success(t('editor:header.exportSuccess', '正文已导出为 TXT 文件'));
      if (closeMobileMenu) {
        setShowMobileMenu(false);
      }
    } catch (error) {
      logger.error("Export failed", error);
      toast.error(handleApiError(error));
      if (
        error instanceof ApiError &&
        error.errorCode === "ERR_QUOTA_EXPORT_FORMAT_RESTRICTED" &&
        exportUpgradePrompt.surface === "modal"
      ) {
        setShowExportUpgradeModal(true);
      }
    }
  };

  const handleOpenFeedback = (closeMobileMenu = false) => {
    setShowFeedbackDialog(true);
    if (closeMobileMenu) {
      setShowMobileMenu(false);
    }
  };
  const feedbackSourceRoute = (
    typeof window !== "undefined" ? `${window.location.pathname}${window.location.search}` : ""
  ).slice(0, 255);

  const secondaryActionItems = [
    ...(currentProjectId
      ? [{
          icon: BarChart3,
          label: t('editor:header.projectDashboard'),
          onClick: () => handleOpenProjectDashboard(),
        }]
      : []),
    {
      icon: BookOpen,
      label: t('editor:header.helpDocs'),
      onClick: () => handleOpenDocs(),
    },
    {
      icon: Bug,
      label: t('common:feedback.entry'),
      onClick: () => handleOpenFeedback(),
    },
  ];

  useEffect(() => {
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setShowMobileMenu(false);
        mobileMenuToggleRef.current?.focus();
      }
    };

    if (showMobileMenu) {
      document.addEventListener("keydown", handleEscape);
    }

    return () => {
      document.removeEventListener("keydown", handleEscape);
    };
  }, [showMobileMenu]);

  return (
    <>
      <header className="h-12 bg-[hsl(var(--bg-secondary))] border-b border-[hsl(var(--separator-color))] flex items-center px-2 md:px-4 justify-between shrink-0">
        {/* Left: Website Name and Project Switcher */}
        <div className="flex items-center gap-2 md:gap-4 min-w-0 flex-1">
          <button
            type="button"
            className="flex min-h-[44px] min-w-[44px] items-center justify-start hover:bg-[hsl(var(--bg-tertiary))] rounded-lg px-1.5 py-1 md:px-0 md:py-0 transition-colors shrink-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-secondary))]"
            onClick={() => navigate("/dashboard")}
            aria-label={t('editor:header.goDashboard')}
            title={t('editor:header.goDashboard')}
            data-testid="header-logo-button"
          >
            {/* 优先展示带文字的品牌 Logo，超窄屏回退为图标 */}
            <div className="max-[380px]:block hidden">
              <LogoMark className="h-7 w-7" />
            </div>
            <div className="max-[380px]:hidden">
              <Logo className="h-7 w-auto" />
            </div>
          </button>
          {/* ProjectSwitcher takes the remaining width and truncates; no ancestor clips its dropdown. */}
          <div className="min-w-0 flex-1 md:flex-none md:max-w-[420px]">
            <ProjectSwitcher />
          </div>
        </div>

        {/* Right: Actions - Desktop */}
        <div className="hidden md:flex items-center gap-1">
          {/* Subscription entry */}
          <button
            type="button"
            onClick={() => handleOpenBilling()}
            data-testid="header-subscription-entry"
            className={desktopIconButtonClass}
            title={t('editor:header.manageSubscription')}
            aria-label={t('editor:header.manageSubscription')}
          >
            <CreditCard size={18} />
          </button>

          {/* Project actions */}
          {currentProjectId && (
            <>
              <button
                type="button"
                onClick={() => {
                  void handleExport();
                }}
                className={desktopIconButtonClass}
                title={t('editor:header.export')}
                aria-label={t('editor:header.export')}
              >
                <Download size={18} />
              </button>
              <button
                type="button"
                onClick={() => setShowVersionHistory(true)}
                className={desktopIconButtonClass}
                title={t('editor:header.versionHistory')}
                aria-label={t('editor:header.versionHistory')}
              >
                <History size={18} />
              </button>
            </>
          )}

          {/* Secondary actions (non-primary) */}
          <DropdownMenu
            items={secondaryActionItems}
            triggerTitle={t('editor:header.moreActions')}
            triggerTestId="header-more-actions"
            menuTestId="header-more-actions-menu"
          />

          <button
            type="button"
            onClick={() => setShowSettings(true)}
            data-testid="settings-button"
            className={desktopIconButtonClass}
            title={t('editor:header.settings')}
            aria-label={t('editor:header.settings')}
          >
            <Settings size={18} />
          </button>

          {/* User menu */}
          <div className="h-6 w-px bg-[hsl(var(--separator-color))] mx-1" />
          <UserMenu />
        </div>

        {/* Right: Mobile Menu Button */}
        <div className="flex md:hidden items-center shrink-0">
          <button
            ref={mobileMenuToggleRef}
            type="button"
            onClick={() => setShowMobileMenu((prev) => !prev)}
            className="p-2 min-h-[44px] min-w-[44px] flex items-center justify-center hover:bg-[hsl(var(--bg-tertiary))] rounded text-[hsl(var(--text-primary))] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-secondary))]"
            aria-label={showMobileMenu ? t('common:close') : t('common:menu')}
            aria-expanded={showMobileMenu}
            aria-controls={mobileMenuId}
            aria-haspopup="menu"
          >
            {showMobileMenu ? <X size={20} /> : <Menu size={20} />}
          </button>
        </div>
      </header>

      {/* Mobile Dropdown Menu */}
      {isMobile && showMobileMenu && (
        <div className="md:hidden absolute top-12 left-0 right-0 bg-[hsl(var(--bg-secondary))] border-b border-[hsl(var(--separator-color))] shadow-lg z-40">
          <div
            id={mobileMenuId}
            className="flex flex-col p-2 gap-1"
            role="menu"
            aria-label={t('editor:header.mobileMenu')}
          >
            <button
              type="button"
              onClick={() => handleOpenBilling(true)}
              data-testid="header-subscription-entry-mobile"
              className="flex min-h-[44px] items-center gap-3 px-3 py-2.5 hover:bg-[hsl(var(--bg-tertiary))] rounded text-sm text-[hsl(var(--text-primary))] transition-colors text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-secondary))]"
              role="menuitem"
            >
              <CreditCard size={18} />
              {t('editor:header.manageSubscription')}
            </button>
            {currentProjectId && (
              <button
                type="button"
                onClick={() => handleOpenProjectDashboard(true)}
                data-testid="project-dashboard-button-mobile"
                className="flex min-h-[44px] items-center gap-3 px-3 py-2.5 hover:bg-[hsl(var(--bg-tertiary))] rounded text-sm text-[hsl(var(--text-primary))] transition-colors text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-secondary))]"
                role="menuitem"
              >
                <BarChart3 size={18} />
                {t('editor:header.projectDashboard')}
              </button>
            )}
            {currentProjectId && (
              <button
                type="button"
                onClick={() => {
                  void handleExport(true);
                }}
                className="flex min-h-[44px] items-center gap-3 px-3 py-2.5 hover:bg-[hsl(var(--bg-tertiary))] rounded text-sm text-[hsl(var(--text-primary))] transition-colors text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-secondary))]"
                role="menuitem"
              >
                <Download size={18} />
                {t('editor:header.export')}
              </button>
            )}
            {currentProjectId && (
              <button
                type="button"
                onClick={() => {
                  setShowVersionHistory(true);
                  setShowMobileMenu(false);
                }}
                className="flex min-h-[44px] items-center gap-3 px-3 py-2.5 hover:bg-[hsl(var(--bg-tertiary))] rounded text-sm text-[hsl(var(--text-primary))] transition-colors text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-secondary))]"
                role="menuitem"
              >
                <History size={18} />
                {t('editor:header.versionHistory')}
              </button>
            )}
            <button
              type="button"
              onClick={() => handleOpenDocs(true)}
              data-testid="help-docs-button-mobile"
              className="flex min-h-[44px] items-center gap-3 px-3 py-2.5 hover:bg-[hsl(var(--bg-tertiary))] rounded text-sm text-[hsl(var(--text-primary))] transition-colors text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-secondary))]"
              role="menuitem"
            >
              <BookOpen size={18} />
              {t('editor:header.helpDocs')}
            </button>
            <button
              type="button"
              onClick={() => handleOpenFeedback(true)}
              className="flex min-h-[44px] items-center gap-3 px-3 py-2.5 hover:bg-[hsl(var(--bg-tertiary))] rounded text-sm text-[hsl(var(--text-primary))] transition-colors text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-secondary))]"
              role="menuitem"
            >
              <Bug size={18} />
              {t('common:feedback.entry')}
            </button>
            <button
              type="button"
              onClick={() => {
                setShowSettings(true);
                setShowMobileMenu(false);
              }}
              data-testid="settings-button"
              className="flex min-h-[44px] items-center gap-3 px-3 py-2.5 hover:bg-[hsl(var(--bg-tertiary))] rounded text-sm text-[hsl(var(--text-primary))] transition-colors text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-secondary))]"
              role="menuitem"
            >
              <Settings size={18} />
              {t('editor:header.settings')}
            </button>

            {/* User section in mobile menu */}
            <div className="h-px bg-[hsl(var(--separator-color))] my-1" />
            <UserMenuMobile onLogout={() => setShowMobileMenu(false)} />
          </div>
        </div>
      )}

      {/* Version History Modal */}
      {showVersionHistory && currentProjectId && (
        <VersionHistoryPanel
          projectId={currentProjectId}
          onClose={() => setShowVersionHistory(false)}
          onRollback={() => {
            // Refresh file tree and clear selection after rollback
            triggerFileTreeRefresh();
            setSelectedItem(null);
            setShowVersionHistory(false);
          }}
          onCompare={(id1, id2) => {
            logger.log("Compare snapshots:", id1, id2);
          }}
        />
      )}

      {/* Settings Modal */}
      <SettingsDialog
        isOpen={showSettings}
        onClose={() => setShowSettings(false)}
      />

      <FeedbackDialog
        open={showFeedbackDialog}
        onClose={() => setShowFeedbackDialog(false)}
        sourcePage="editor"
        sourceRoute={feedbackSourceRoute}
      />

      <UpgradePromptModal
        open={showExportUpgradeModal}
        onClose={() => setShowExportUpgradeModal(false)}
        source={exportUpgradePrompt.source}
        primaryDestination="billing"
        secondaryDestination="pricing"
        title={t('editor:header.exportQuotaBlockedTitle')}
        description={t('editor:header.exportQuotaBlockedDesc')}
        primaryLabel={t('dashboard:billing.ctaUpgradePro')}
        onPrimary={() => {
          window.location.assign(
            buildUpgradeUrl(exportUpgradePrompt.billingPath, exportUpgradePrompt.source)
          );
        }}
        secondaryLabel={t('home:pricingTeaser.viewPricing')}
        onSecondary={() => {
          window.location.assign(
            buildUpgradeUrl(exportUpgradePrompt.pricingPath, exportUpgradePrompt.source)
          );
        }}
      />
    </>
  );
};

export default Header;
