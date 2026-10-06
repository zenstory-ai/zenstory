import { useState, useRef, useEffect, useMemo, useCallback, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { useNavigate, Link, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { LogOut, Menu, X, BookOpen, ChevronDown, BookText, CreditCard } from "lucide-react";
import { Logo } from "./Logo";
import { useAuth } from "../contexts/AuthContext";
import { LanguageSwitcher } from "./LanguageSwitcher";
import { useIsMobile } from "../hooks/useMediaQuery";
import { buildUpgradeUrl } from "../config/upgradeExperience";

type HeaderVariant = "home" | "dashboard" | "auth";

interface PublicHeaderProps {
  /** 页面类型：home=首页, dashboard=仪表盘, auth=登录/注册 */
  variant?: HeaderVariant;
  /** 最大宽度类名，默认 max-w-7xl */
  maxWidth?: string;
}

const HOME_HEADER_SOURCES = {
  pricing: "header_nav_pricing",
  login: "header_nav_login",
  getStarted: "header_nav_get_started",
} as const;

export function PublicHeader({
  variant = "home",
  maxWidth = "max-w-7xl"
}: PublicHeaderProps) {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { user, logout } = useAuth();
  const { t } = useTranslation(['common', 'home', 'dashboard']);
  const isMobile = useIsMobile();
  const isAuthVariant = variant === "auth";
  const [showMobileMenu, setShowMobileMenu] = useState(false);
  const [showProductMenu, setShowProductMenu] = useState(false);
  const productMenuRef = useRef<HTMLDivElement>(null);
  const productMenuTriggerRef = useRef<HTMLButtonElement>(null);
  const productMenuItemRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const mobileMenuToggleRef = useRef<HTMLButtonElement>(null);
  const mobileMenuId = "public-header-mobile-menu";
  const productMenuId = "public-header-product-menu";
  const planIntent = useMemo(() => {
    if (variant !== "home") {
      return undefined;
    }

    const rawPlan = searchParams.get("plan");
    if (!rawPlan) {
      return undefined;
    }

    const trimmedPlan = rawPlan.trim();
    return trimmedPlan.length > 0 ? trimmedPlan : undefined;
  }, [searchParams, variant]);
  const withPlanIntent = useCallback((path: string): string => {
    if (!planIntent) {
      return path;
    }

    const parsed = new URL(path, "https://zenstory.local");
    if (!parsed.searchParams.has("plan")) {
      parsed.searchParams.set("plan", planIntent);
    }

    return `${parsed.pathname}${parsed.search}${parsed.hash}`;
  }, [planIntent]);
  const withHomeAttributionSource = useCallback((path: string, source: string): string => {
    const pathWithPlanIntent = withPlanIntent(path);
    return variant === "home" ? buildUpgradeUrl(pathWithPlanIntent, source) : pathWithPlanIntent;
  }, [variant, withPlanIntent]);
  const pricingPath = withHomeAttributionSource("/pricing", HOME_HEADER_SOURCES.pricing);
  const loginPath = withHomeAttributionSource("/login", HOME_HEADER_SOURCES.login);
  const registerPath = withHomeAttributionSource("/register", HOME_HEADER_SOURCES.getStarted);

  // 点击外部关闭产品菜单
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (productMenuRef.current && !productMenuRef.current.contains(event.target as Node)) {
        setShowProductMenu(false);
      }
    };

    if (showProductMenu) {
      document.addEventListener('mousedown', handleClickOutside);
    }

    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [showProductMenu]);

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

  useEffect(() => {
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setShowProductMenu(false);
        productMenuTriggerRef.current?.focus();
      }
    };

    if (showProductMenu) {
      document.addEventListener("keydown", handleEscape);
    }

    return () => {
      document.removeEventListener("keydown", handleEscape);
    };
  }, [showProductMenu]);

  const handleLogout = async () => {
    await logout();
    navigate("/");
  };

  const focusProductMenuItem = (target: "first" | "last") => {
    requestAnimationFrame(() => {
      const refs = productMenuItemRefs.current.filter(
        (ref): ref is HTMLButtonElement => ref !== null
      );
      if (refs.length === 0) return;
      const nextTarget = target === "first" ? refs[0] : refs[refs.length - 1];
      nextTarget?.focus();
    });
  };

  const handleProductMenuKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (!showProductMenu) return;

    const refs = productMenuItemRefs.current.filter(
      (ref): ref is HTMLButtonElement => ref !== null
    );
    if (refs.length === 0) return;

    const activeIndex = refs.findIndex((ref) => ref === document.activeElement);

    switch (event.key) {
      case "ArrowDown": {
        event.preventDefault();
        const nextIndex = activeIndex < 0 ? 0 : (activeIndex + 1) % refs.length;
        refs[nextIndex]?.focus();
        break;
      }
      case "ArrowUp": {
        event.preventDefault();
        const nextIndex = activeIndex < 0 ? refs.length - 1 : (activeIndex - 1 + refs.length) % refs.length;
        refs[nextIndex]?.focus();
        break;
      }
      case "Home":
        event.preventDefault();
        refs[0]?.focus();
        break;
      case "End":
        event.preventDefault();
        refs[refs.length - 1]?.focus();
        break;
      default:
        break;
    }
  };

  return (
    <>
      <header className="sticky top-0 z-50 h-12 bg-[hsl(var(--bg-secondary))] shadow-sm">
        <div className={`${maxWidth} mx-auto px-4 h-full flex items-center`}>
          {/* Logo - 固定左侧 */}
          <button
            type="button"
            className="flex min-h-[44px] min-w-[44px] items-center shrink-0 hover:opacity-80 transition-opacity focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-secondary))] rounded"
            onClick={() => navigate("/")}
            aria-label={t('common:nav.home', '返回首页')}
            title={t('common:nav.home', '返回首页')}
          >
            <Logo className="h-7 w-auto" />
          </button>

          {/* 产品下拉菜单 - logo 右侧 */}
          <div className="relative ml-2 md:ml-4" ref={productMenuRef}>
            <button
              type="button"
              ref={productMenuTriggerRef}
              onClick={() => setShowProductMenu(!showProductMenu)}
              onKeyDown={(event) => {
                if (event.key === "ArrowDown") {
                  event.preventDefault();
                  if (!showProductMenu) {
                    setShowProductMenu(true);
                  }
                  focusProductMenuItem("first");
                } else if (event.key === "ArrowUp") {
                  event.preventDefault();
                  if (!showProductMenu) {
                    setShowProductMenu(true);
                  }
                  focusProductMenuItem("last");
                } else if (event.key === "Escape" && showProductMenu) {
                  event.preventDefault();
                  setShowProductMenu(false);
                }
              }}
              className="min-h-[44px] px-2.5 md:px-3 text-sm text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))] rounded transition-colors inline-flex items-center gap-1 md:gap-1.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-secondary))]"
              aria-expanded={showProductMenu}
              aria-haspopup="menu"
              aria-controls={productMenuId}
            >
              <span className="text-xs md:text-sm">{t('common:productMenu.title')}</span>
              <ChevronDown size={12} className={`transition-transform ${showProductMenu ? 'rotate-180' : ''}`} />
            </button>

            {/* 下拉菜单 */}
            {showProductMenu && (
              <div
                id={productMenuId}
                className="absolute top-full left-0 mt-1 py-1 min-w-[130px] md:min-w-[140px] bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--border-color))] rounded-lg shadow-lg z-50"
                role="menu"
                aria-label={t('common:productMenu.title')}
                onKeyDown={handleProductMenuKeyDown}
              >
                <button
                  type="button"
                  ref={(el) => {
                    productMenuItemRefs.current[0] = el;
                  }}
                  onClick={() => {
                    navigate("/dashboard");
                    setShowProductMenu(false);
                  }}
                  className="w-full min-h-[44px] px-3 py-2 text-left text-sm text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))] flex items-center gap-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-secondary))]"
                  role="menuitem"
                >
                  <BookOpen size={16} />
                  <span>{t('common:productMenu.aiStory')}</span>
                </button>
              </div>
            )}
          </div>

          {/* Spacer - 推送右侧内容到右边 */}
          <div className="flex-1" />

          {/* Desktop: Right side */}
          <div className="hidden md:flex items-center gap-1 shrink-0">
            {/* Language Switcher - always visible */}
            <LanguageSwitcher />

            {!isAuthVariant && (
              <>
                {/* Docs link - between language switcher and auth buttons */}
                <Link
                  to="/docs"
                  className="min-h-[44px] px-3 text-sm text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))] rounded transition-colors inline-flex items-center gap-1.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-secondary))]"
                >
                  <BookText className="w-4 h-4" />
                  <span>{t('common:nav.docs')}</span>
                </Link>
                <Link
                  to={pricingPath}
                  className="min-h-[44px] px-3 text-sm text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))] rounded transition-colors inline-flex items-center gap-1.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-secondary))]"
                >
                  <CreditCard className="w-4 h-4" />
                  <span>{t('home:nav.pricing')}</span>
                </Link>
              </>
            )}

            {variant === "home" && (
              // 首页：已登录显示"进入工作台"，未登录显示"登录"+"开始免费试用"
              user ? (
                <button
                  type="button"
                  onClick={() => navigate("/dashboard")}
                  className="btn-primary min-h-[44px] px-3 text-sm"
                >
                  {t('home:nav.goDashboard', '进入工作台')}
                </button>
              ) : (
                <>
                  <button
                    type="button"
                    onClick={() => navigate(loginPath)}
                    className="min-h-[44px] px-3 text-sm text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))] rounded transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-secondary))]"
                  >
                    {t('home:nav.login')}
                  </button>
                  <button
                    type="button"
                    onClick={() => navigate(registerPath)}
                    className="btn-primary min-h-[44px] px-3 text-sm"
                  >
                    {t('home:nav.getStarted')}
                  </button>
                </>
              )
            )}

            {variant === "dashboard" && user && (
              // 仪表盘：显示用户名 + 登出按钮
              <>
                <div className="h-6 w-px bg-[hsl(var(--separator-color))] mx-1" />
                <div className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-[hsl(var(--bg-tertiary))]">
                  <div className="w-2 h-2 rounded-full bg-[hsl(var(--success))]"></div>
                  <span className="text-xs font-medium text-[hsl(var(--text-secondary))]">{user.username}</span>
                </div>
                <button
                  type="button"
                  onClick={handleLogout}
                  className="min-h-[44px] min-w-[44px] flex items-center justify-center rounded text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-secondary))]"
                  title={t('dashboard:nav.logout')}
                  aria-label={t('dashboard:nav.logout')}
                >
                  <LogOut size={18} />
                </button>
              </>
            )}

            {/* auth 页面：只显示 Logo 和语言切换，右侧为空 */}
          </div>

          {/* Mobile: Right side */}
          <div className="flex md:hidden items-center gap-1 shrink-0">
            {/* Language Switcher */}
            <LanguageSwitcher />

            {variant === "home" ? (
              <button
                ref={mobileMenuToggleRef}
                type="button"
                onClick={() => setShowMobileMenu((prev) => !prev)}
                aria-expanded={showMobileMenu}
                aria-controls={mobileMenuId}
                aria-haspopup="menu"
                aria-label={
                  showMobileMenu
                    ? t("common:nav.closeMenu", "关闭菜单")
                    : t("common:nav.openMenu", "打开菜单")
                }
                className="min-h-[44px] min-w-[44px] p-2 hover:bg-[hsl(var(--bg-tertiary))] rounded text-[hsl(var(--text-primary))] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-secondary))]"
              >
                {showMobileMenu ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
              </button>
            ) : (
              <>
                {!isAuthVariant && (
                  <>
                    <Link
                      to="/docs"
                      aria-label={t('common:nav.docs')}
                      title={t('common:nav.docs')}
                      className={`min-h-[44px] px-3 text-sm text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))] rounded transition-colors inline-flex items-center gap-1.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-secondary))]`}
                    >
                      <BookText className="w-4 h-4" />
                      <span>{t('common:nav.docs')}</span>
                    </Link>
                    <Link
                      to={pricingPath}
                      aria-label={t('home:nav.pricing')}
                      title={t('home:nav.pricing')}
                      className={`min-h-[44px] px-3 text-sm text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))] rounded transition-colors inline-flex items-center gap-1.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-secondary))]`}
                    >
                      <CreditCard className="w-4 h-4" />
                      <span>{t('home:nav.pricing')}</span>
                    </Link>
                  </>
                )}

                {variant === "dashboard" && (
                  // Dashboard：显示菜单按钮
                  <button
                    ref={mobileMenuToggleRef}
                    type="button"
                    onClick={() => setShowMobileMenu((prev) => !prev)}
                    aria-expanded={showMobileMenu}
                    aria-controls={mobileMenuId}
                    aria-haspopup="menu"
                    aria-label={
                      showMobileMenu
                        ? t("common:nav.closeMenu", "关闭菜单")
                        : t("common:nav.openMenu", "打开菜单")
                    }
                    className="min-h-[44px] min-w-[44px] p-2 hover:bg-[hsl(var(--bg-tertiary))] rounded text-[hsl(var(--text-primary))] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-secondary))]"
                  >
                    {showMobileMenu ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
                  </button>
                )}
              </>
            )}
          </div>
        </div>
      </header>

      {/* Mobile Dropdown Menu */}
      {isMobile && showMobileMenu && (variant === "dashboard" || variant === "home") && (
        <div
          id={mobileMenuId}
          className="md:hidden absolute top-12 left-0 right-0 bg-[hsl(var(--bg-secondary))] border-b border-[hsl(var(--separator-color))] shadow-lg z-40"
        >
          <div className="flex flex-col p-2 gap-1" role="menu" aria-label={t("common:nav.mobileMenu", "移动端导航菜单")}>
            {variant === "home" && (
              <>
                <Link
                  to="/docs"
                  onClick={() => setShowMobileMenu(false)}
                  className="flex min-h-[44px] items-center gap-3 px-3 py-2.5 hover:bg-[hsl(var(--bg-tertiary))] rounded text-sm text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-secondary))]"
                  role="menuitem"
                >
                  <BookText size={16} />
                  <span>{t('common:nav.docs')}</span>
                </Link>
                <Link
                  to={pricingPath}
                  onClick={() => setShowMobileMenu(false)}
                  className="flex min-h-[44px] items-center gap-3 px-3 py-2.5 hover:bg-[hsl(var(--bg-tertiary))] rounded text-sm text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-secondary))]"
                  role="menuitem"
                >
                  <CreditCard size={16} />
                  <span>{t('home:nav.pricing')}</span>
                </Link>

                {user ? (
                  <button
                    type="button"
                    onClick={() => {
                      navigate("/dashboard");
                      setShowMobileMenu(false);
                    }}
                    className="btn-primary w-full min-h-[44px] text-sm"
                    role="menuitem"
                  >
                    {t('home:nav.goDashboard', '进入工作台')}
                  </button>
                ) : (
                  <>
                    <button
                      type="button"
                      onClick={() => {
                        navigate(loginPath);
                        setShowMobileMenu(false);
                      }}
                      className="flex min-h-[44px] items-center justify-start gap-3 px-3 py-2.5 hover:bg-[hsl(var(--bg-tertiary))] rounded text-sm text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-secondary))]"
                      role="menuitem"
                    >
                      {t('home:nav.login')}
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        navigate(registerPath);
                        setShowMobileMenu(false);
                      }}
                      className="btn-primary w-full min-h-[44px] text-sm"
                      role="menuitem"
                    >
                      {t('home:nav.getStarted')}
                    </button>
                  </>
                )}
              </>
            )}

            {variant === "dashboard" && user && (
              <>
                {/* User section in mobile menu */}
                <div className="flex items-center gap-3 px-3 py-2">
                  <div className="w-8 h-8 rounded-full bg-gradient-to-br from-[hsl(var(--accent-primary))] to-[hsl(var(--accent-dark))] flex items-center justify-center text-white text-sm font-medium shrink-0">
                    {user.username?.charAt(0).toUpperCase() || "U"}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium text-[hsl(var(--text-primary))] truncate">
                      {user.username}
                    </div>
                    <div className="text-xs text-[hsl(var(--text-secondary))]">
                      {user.email || ""}
                    </div>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={async () => {
                    await handleLogout();
                    setShowMobileMenu(false);
                  }}
                  className="flex min-h-[44px] items-center gap-3 px-3 py-2.5 hover:bg-[hsl(var(--error)/0.1)] rounded text-sm text-[hsl(var(--error))] transition-colors text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-secondary))]"
                  role="menuitem"
                >
                  <LogOut size={18} />
                  {t('dashboard:nav.logout')}
                </button>
              </>
            )}
          </div>
        </div>
      )}
    </>
  );
}
