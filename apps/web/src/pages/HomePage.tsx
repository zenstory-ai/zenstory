import { useState, useEffect, useCallback, useMemo, useRef, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, Link, useSearchParams } from "react-router-dom";
import {
  Book, FileText, Clapperboard, ArrowRight, Sparkles, Check, Zap,
  TrendingUp, Mic, Paperclip, Brain, History, Cloud, Download,
  Edit3, User, Loader2, FolderOpen
} from "../components/icons";
import { useAuth } from "../contexts/AuthContext";
import { usePreloadRoute } from "../hooks/usePreloadRoute";
import { useMediaQuery } from "../hooks/useMediaQuery";
import { authConfig } from "../config/auth";
import { buildUpgradeUrl } from "../config/upgradeExperience";
import { PublicHeader } from "../components/PublicHeader";
import { AgentApiSection } from "../components/home/AgentApiSection";
import { setPreferredProjectType } from "../lib/preferredProjectType";
import type { ProjectType } from "../types";

// Scene definitions for the product preview carousel
const SCENES = [
  { id: 'create', duration: 5000 },
  { id: 'suggest', duration: 5000 },
  { id: 'edit', duration: 5000 },
] as const;

type SceneId = typeof SCENES[number]['id'];

const HOME_CTA_SOURCES = {
  hero: "home_hero",
  pricingTeaser: "home_pricing_teaser",
  cta: "home_cta",
  projectTypeCard: "home_project_type_card",
} as const;

export default function HomePage() {
  const { t } = useTranslation(['home', 'privacy']);
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { user } = useAuth();
  const prefersReducedMotion = useMediaQuery("(prefers-reduced-motion: reduce)");
  const [activeScene, setActiveScene] = useState(0);
  const [isTransitioning, setIsTransitioning] = useState(false);
  const [sceneProgress, setSceneProgress] = useState(0);
  const [isDemoPaused, setIsDemoPaused] = useState(false);
  // The sign-up route is lazy and navigation waits for its chunk, so the
  // clicked card shows an "opening" state instead of looking unresponsive.
  const [openingProjectType, setOpeningProjectType] = useState<ProjectType | null>(null);
  const sceneTransitionTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const demoSectionRef = useRef<HTMLDivElement | null>(null);
  const planIntent = useMemo(() => {
    const rawPlan = searchParams.get("plan");
    if (!rawPlan) {
      return undefined;
    }
    const trimmedPlan = rawPlan.trim();
    return trimmedPlan.length > 0 ? trimmedPlan : undefined;
  }, [searchParams]);
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
  const withAttributionSource = useCallback((path: string, source?: string): string => {
    const pathWithPlanIntent = withPlanIntent(path);
    return source ? buildUpgradeUrl(pathWithPlanIntent, source) : pathWithPlanIntent;
  }, [withPlanIntent]);
  const pricingTeaserLink = useMemo(
    () => withAttributionSource("/pricing", HOME_CTA_SOURCES.pricingTeaser),
    [withAttributionSource]
  );

  // 路由预加载 - 在用户鼠标 hover 时预加载 Dashboard
  const preloadDashboard = usePreloadRoute(
    () => import("../pages/Dashboard"),
    "/dashboard"
  );
  const preloadRegister = usePreloadRoute(
    () => import("../pages/Register"),
    "/register"
  );
  const preloadLogin = usePreloadRoute(
    () => import("../pages/Login"),
    "/login"
  );

  const handleGetStarted = () => {
    if (user) {
      navigate("/dashboard");
    } else if (authConfig.registrationEnabled) {
      navigate(withAttributionSource("/register", HOME_CTA_SOURCES.projectTypeCard));
    } else {
      navigate(withAttributionSource("/login", HOME_CTA_SOURCES.projectTypeCard));
    }
  };
  const handleGetStartedWithSource = (source: string) => {
    if (user) {
      navigate("/dashboard");
    } else if (authConfig.registrationEnabled) {
      navigate(withAttributionSource("/register", source));
    } else {
      navigate(withAttributionSource("/login", source));
    }
  };

  const handleGetStartedHover = () => {
    // 根据用户状态预加载相应路由
    if (user) {
      preloadDashboard();
    } else if (authConfig.registrationEnabled) {
      preloadRegister();
    } else {
      preloadLogin();
    }
  };

  const handleViewDemo = () => {
    demoSectionRef.current?.focus({ preventScroll: true });
    demoSectionRef.current?.scrollIntoView({
      behavior: prefersReducedMotion ? "auto" : "smooth",
      block: "start",
    });
  };

  // Auto-rotate scenes with progress
  useEffect(() => {
    if (sceneTransitionTimeoutRef.current !== null) {
      clearTimeout(sceneTransitionTimeoutRef.current);
      sceneTransitionTimeoutRef.current = null;
    }
    if (prefersReducedMotion || isDemoPaused) {
      setSceneProgress(0);
      setIsTransitioning(false);
      return;
    }

    setSceneProgress(0);
    const duration = SCENES[activeScene].duration;
    const startTime = Date.now();

    const timer = setInterval(() => {
      const elapsed = Date.now() - startTime;
      const progress = Math.min(100, (elapsed / duration) * 100);
      setSceneProgress(progress);

      if (elapsed >= duration) {
        clearInterval(timer);
        // A manual choice made near this deadline owns the pending transition.
        if (sceneTransitionTimeoutRef.current !== null) return;
        setIsTransitioning(true);
        sceneTransitionTimeoutRef.current = setTimeout(() => {
          sceneTransitionTimeoutRef.current = null;
          setActiveScene((prev) => (prev + 1) % SCENES.length);
          setIsTransitioning(false);
          setSceneProgress(0);
        }, 300);
      }
    }, 80);

    return () => {
      clearInterval(timer);
      if (sceneTransitionTimeoutRef.current !== null) {
        clearTimeout(sceneTransitionTimeoutRef.current);
        sceneTransitionTimeoutRef.current = null;
      }
    };
  }, [activeScene, prefersReducedMotion, isDemoPaused]);

  // Handle manual scene selection
  const handleSceneSelect = useCallback((index: number) => {
    if (index === activeScene) return;

    if (sceneTransitionTimeoutRef.current !== null) {
      clearTimeout(sceneTransitionTimeoutRef.current);
      sceneTransitionTimeoutRef.current = null;
    }

    if (prefersReducedMotion) {
      setActiveScene(index);
      setSceneProgress(0);
      return;
    }

    setIsTransitioning(true);
    sceneTransitionTimeoutRef.current = setTimeout(() => {
      sceneTransitionTimeoutRef.current = null;
      setActiveScene(index);
      setIsTransitioning(false);
      setSceneProgress(0);
    }, 300);
  }, [activeScene, prefersReducedMotion]);

  // Render scene content based on active scene
  const renderSceneContent = (sceneId: SceneId) => {
    switch (sceneId) {
      case 'create':
        return <SceneCreate />;
      case 'suggest':
        return <SceneSuggest />;
      case 'edit':
        return <SceneEdit />;
      default:
        return <SceneCreate />;
    }
  };

  return (
    <div className="public-page min-h-screen relative overflow-x-clip bg-[hsl(var(--bg-primary))]">
      {/* Modern grid background */}
      <div className="fixed inset-0 pointer-events-none">
        <div className="absolute inset-0 bg-[linear-gradient(rgba(255,255,255,0.02)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,0.02)_1px,transparent_1px)] bg-[size:60px_60px]" />
      </div>
      
      {/* Gradient orbs */}
      <div className="fixed inset-0 pointer-events-none overflow-hidden">
        <div className="absolute -top-[200px] md:-top-[300px] left-1/4 w-[400px] md:w-[600px] h-[400px] md:h-[600px] bg-[hsl(var(--accent-primary))] opacity-[0.08] blur-[100px] md:blur-[150px] rounded-full" />
        <div className="absolute top-1/2 -right-[150px] md:-right-[200px] w-[350px] md:w-[500px] h-[350px] md:h-[500px] bg-white opacity-[0.03] blur-[90px] md:blur-[120px] rounded-full" />
        <div className="absolute -bottom-[150px] md:-bottom-[200px] left-1/3 w-[300px] md:w-[400px] h-[300px] md:h-[400px] bg-white opacity-[0.02] blur-[80px] md:blur-[100px] rounded-full" />
      </div>

      {/* Header */}
      <PublicHeader maxWidth="max-w-6xl" />

      <main>
      {/* Hero Section */}
      <section className="pt-16 md:pt-24 lg:pt-28 pb-10 md:pb-14 lg:pb-16 px-4 md:px-6 relative">
        <div className="max-w-6xl mx-auto">
          <div className="grid lg:grid-cols-2 gap-8 md:gap-12 lg:gap-16 items-center">
            {/* Left - Content */}
            <div className="space-y-6 md:space-y-8">
              {/* Badge */}
              <div className="inline-flex items-center gap-2 h-8 md:h-9 px-3 md:px-4 rounded-full bg-[hsl(var(--accent-primary)/0.1)] border border-[hsl(var(--accent-primary)/0.2)]">
                <Zap className="w-3.5 h-3.5 md:w-4 md:h-4 text-[hsl(var(--accent-primary))]" />
                <span className="text-xs md:text-sm font-semibold text-[hsl(var(--accent-primary))]">{t('home:hero.badge')}</span>
              </div>
              
              <div>
                <h1 className="text-[32px] sm:text-4xl md:text-5xl lg:text-[52px] text-balance font-bold leading-[1.15] md:leading-[1.05] text-[hsl(var(--text-primary))] mb-4 md:mb-5">
                  {t('home:hero.titlePart1')}
                  <br />
                  <span className="text-[hsl(var(--text-primary))]">
                    {t('home:hero.titlePart2')}
                  </span>
                </h1>

                <p className="text-base md:text-lg text-[hsl(var(--text-secondary))] leading-relaxed mb-8 md:mb-10">
                  {t('home:hero.description')}
                </p>
              </div>

              {/* CTA Button - 只对未登录用户显示 */}
              {!user && (
                <div>
                  <div className="flex flex-wrap items-center gap-2.5 md:gap-3">
                    <button
                      onClick={() => handleGetStartedWithSource(HOME_CTA_SOURCES.hero)}
                      onMouseEnter={handleGetStartedHover}
                      onFocus={handleGetStartedHover}
                      className="group relative h-11 md:h-12 lg:h-13 px-5 md:px-6 lg:px-7 text-[14px] md:text-[15px] font-bold inline-flex items-center gap-2 md:gap-3 bg-[hsl(var(--accent-primary))] text-white rounded-xl overflow-hidden transition-all hover:shadow-[0_0_30px_rgba(74,158,255,0.4)] hover:scale-[1.02]"
                    >
                      <span className="relative z-10">{t('home:hero.cta')}</span>
                      <ArrowRight className="w-4 h-4 md:w-5 md:h-5 relative z-10 group-hover:translate-x-0.5 transition-transform" />
                      {/* Shine effect */}
                      <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/20 to-transparent -translate-x-full group-hover:translate-x-full transition-transform duration-700" />
                    </button>
                    <button
                      onClick={handleViewDemo}
                      className="h-11 md:h-12 lg:h-13 px-5 md:px-6 lg:px-7 text-[14px] md:text-[15px] font-semibold inline-flex items-center gap-2 rounded-xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))] transition-colors"
                    >
                      {t('home:hero.secondaryCta', '查看演示')}
                    </button>
                  </div>
                  <p className="text-[10px] md:text-xs text-[hsl(var(--text-secondary))] mt-2 md:mt-3">
                    {t('home:hero.ctaSubtext')}
                  </p>
                </div>
              )}
              
              {/* Features */}
              <div className="flex flex-wrap gap-x-6 gap-y-2.5 md:gap-x-8 md:gap-y-3 pt-4 md:pt-6">
                <div className="flex items-center gap-2 md:gap-2.5 text-xs md:text-sm text-[hsl(var(--text-secondary))]">
                  <Check className="w-4 h-4 md:w-4.5 md:h-4.5 text-[hsl(var(--success-text))]" strokeWidth={2.5} />
                  <span className="font-medium">{t('home:features.cloudSave')}</span>
                </div>
                <div className="flex items-center gap-2 md:gap-2.5 text-xs md:text-sm text-[hsl(var(--text-secondary))]">
                  <Check className="w-4 h-4 md:w-4.5 md:h-4.5 text-[hsl(var(--success-text))]" strokeWidth={2.5} />
                  <span className="font-medium">{t('home:features.multiDevice')}</span>
                </div>
                <div className="flex items-center gap-2 md:gap-2.5 text-xs md:text-sm text-[hsl(var(--text-secondary))]">
                  <Check className="w-4 h-4 md:w-4.5 md:h-4.5 text-[hsl(var(--success-text))]" strokeWidth={2.5} />
                  <span className="font-medium">{t('home:features.versionHistory')}</span>
                </div>
              </div>
            </div>

            {/* Right - Product Preview with Scene Carousel */}
            <div
              ref={demoSectionRef}
              role="region"
              tabIndex={-1}
              aria-label={t('home:hero.secondaryCta', '查看演示')}
              className="relative scroll-mt-16"
            >
              {/* Glow effect behind card */}
              <div aria-hidden="true" className="pointer-events-none absolute -inset-4 bg-[hsl(var(--accent-primary))] rounded-3xl opacity-12 blur-2xl" />
              
              {/* Main card */}
              <div className="relative rounded-2xl overflow-hidden">
                {/* Gradient border */}
                <div className="absolute -inset-[1px] bg-[hsl(var(--border-color))] rounded-2xl opacity-60" />
                
                {/* Card content */}
                <div className="relative bg-[hsl(var(--bg-secondary))] rounded-2xl overflow-hidden">
                  <div className="h-11 md:h-12 bg-[hsl(var(--bg-secondary))] border-b border-[hsl(var(--border-color))] flex items-center justify-between px-3 md:px-4 gap-3">
                    <div className="min-w-0 flex items-center gap-2 text-xs md:text-sm font-medium text-[hsl(var(--text-primary))]">
                      <Sparkles className="w-3.5 h-3.5 shrink-0 text-[hsl(var(--accent-primary))]" />
                      <span className="truncate">{t('home:demo.projectName')}</span>
                    </div>
                    <span className="shrink-0 rounded-full bg-[hsl(var(--accent-primary)/0.1)] px-2 py-1 text-[9px] md:text-[10px] font-semibold text-[hsl(var(--accent-primary))]">
                      {t('home:demo.badge')}
                    </span>
                  </div>
                  
                  {/* Scene content with transition */}
                  <div data-testid="home-demo-scene" className={prefersReducedMotion ? 'opacity-100' : `transition-opacity duration-300 ${isTransitioning ? 'opacity-0' : 'opacity-100'}`}>
                    {renderSceneContent(SCENES[activeScene].id)}
                  </div>
                </div>
              </div>
              
              {/* Scene indicator - text buttons */}
              <div className="flex flex-wrap gap-2 justify-center mt-4 md:mt-5">
                {SCENES.map((scene, index) => (
                  <button
                    key={scene.id}
                    type="button"
                    aria-pressed={index === activeScene}
                    onClick={() => handleSceneSelect(index)}
                    className={`min-h-11 text-xs px-3 md:px-4 rounded-full transition-colors ${
                      index === activeScene
                        ? 'bg-[hsl(var(--accent-primary))] text-white shadow-[0_0_15px_rgba(74,158,255,0.4)]'
                        : 'text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))]'
                    }`}
                  >
                    {t(`home:demo.scenes.${scene.id}`)}
                  </button>
                ))}
              </div>

              {!prefersReducedMotion && (
                <div data-testid="home-demo-playback-controls" className="mt-3 flex items-center justify-center gap-3">
                  <button
                    type="button"
                    onClick={() => setIsDemoPaused((paused) => !paused)}
                    className="h-11 shrink-0 px-3 text-xs rounded-lg border border-[hsl(var(--border-color))] text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--bg-tertiary))]"
                  >
                    {isDemoPaused ? t('home:demo.resume') : t('home:demo.pause')}
                  </button>
                  <div
                    role="progressbar"
                    aria-label={t('home:demo.progressLabel')}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={Math.round(sceneProgress)}
                    className="w-28 md:w-36 h-1 rounded-full bg-[hsl(var(--bg-tertiary))] overflow-hidden"
                  >
                    <div
                      className="h-full bg-[hsl(var(--accent-primary))] transition-[width] duration-100"
                      style={{ width: `${sceneProgress}%` }}
                    />
                  </div>
                  <span className="shrink-0 text-[11px] tabular-nums text-[hsl(var(--text-secondary))]">
                    {activeScene + 1} / {SCENES.length}
                  </span>
                </div>
              )}

            </div>
          </div>
        </div>
      </section>

      {/* Project Types */}
      <section className="py-16 md:py-20 px-4 md:px-6 bg-[hsl(var(--bg-secondary))]">
        <div className="max-w-6xl mx-auto">
          <div className="text-center mb-12 md:mb-14">
            <h2 className="text-2xl md:text-3xl lg:text-[36px] font-bold text-[hsl(var(--text-primary))] mb-3">{t('home:projectTypes.title')}</h2>
            <p className="text-sm md:text-base text-[hsl(var(--text-secondary))]">{t('home:projectTypes.subtitle')}</p>
          </div>
          
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 lg:auto-rows-fr gap-4 md:gap-5">
            {[
              {
                icon: Book,
                type: 'novel' as ProjectType,
                popular: true
              },
              {
                icon: FileText,
                type: 'short' as ProjectType,
                popular: false
              },
              {
                icon: Clapperboard,
                type: 'screenplay' as ProjectType,
                popular: false
              }
            ].map((item) => {
              const projectType = t(`home:projectTypes.${item.type}.name`);
              const projectDesc = t(`home:projectTypes.${item.type}.description`);
              const projectFeaturesRaw = t(`home:projectTypes.${item.type}.features`, { returnObjects: true }) as unknown;
              const projectFeatures = Array.isArray(projectFeaturesRaw)
                ? (projectFeaturesRaw as unknown[])
                : [];
              const isOpening = openingProjectType === item.type;
              return (
              <button
                key={item.type}
                type="button"
                data-testid={`home-project-type-card-${item.type}`}
                aria-busy={isOpening}
                onClick={() => {
                  // Remember the type first so the dashboard opens on it after sign-up.
                  setPreferredProjectType(item.type, user?.id);
                  setOpeningProjectType(item.type);
                  handleGetStarted();
                }}
                onFocus={handleGetStartedHover}
                onPointerEnter={handleGetStartedHover}
                className="group relative flex h-full w-full flex-col text-left p-4 md:p-5 rounded-xl bg-[hsl(var(--bg-primary))] border border-[hsl(var(--border-color))] hover:border-[hsl(var(--accent-primary)/0.5)] hover:shadow-[0_0_40px_rgba(74,158,255,0.15)] hover:-translate-y-1 transition-all duration-300 cursor-pointer overflow-hidden focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-primary))]"
              >
                <div data-card-slot="header" className="mb-4 md:mb-5 flex min-h-12 items-start justify-between gap-3">
                  <div className="w-12 h-12 md:w-14 md:h-14 rounded-2xl bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--border-color))] flex items-center justify-center group-hover:scale-105 transition-transform">
                    <item.icon className="w-5 h-5 md:w-6 md:h-6 text-[hsl(var(--text-primary))]" />
                  </div>
                  <div aria-hidden={!item.popular} className={`px-2 md:px-3 py-1 md:py-1.5 rounded-full bg-[hsl(var(--warning)/0.12)] text-[hsl(var(--warning-text))] text-[10px] md:text-[11px] font-bold flex items-center gap-0.5 md:gap-1 ${item.popular ? '' : 'invisible'}`}>
                    <TrendingUp className="w-2.5 h-2.5 md:w-3 md:h-3" />
                    {t('home:demo.popular')}
                  </div>
                </div>
                <h3 className="text-[17px] md:text-[19px] font-bold text-[hsl(var(--text-primary))] mb-1.5 md:mb-2 lg:min-h-15">{projectType}</h3>
                <p data-card-slot="description" className="text-xs md:text-sm text-[hsl(var(--text-secondary))] mb-3 md:mb-5 leading-relaxed lg:h-[4.5rem]">{projectDesc}</p>
                <div data-card-slot="features" className="mt-auto lg:mt-0 flex min-h-16 flex-wrap content-start gap-1.5 md:gap-2">
                  {projectFeatures.map((f, j) => (
                    <span key={j} className="text-[10px] md:text-xs px-2 md:px-3 py-1 md:py-1.5 rounded-full bg-[hsl(var(--bg-secondary))] text-[hsl(var(--text-secondary))] border border-[hsl(var(--border-color))] font-medium">
                      {String(f)}
                    </span>
                  ))}
                </div>

                {isOpening && (
                  <p
                    className="mt-3 text-xs font-medium text-[hsl(var(--accent-primary))]"
                    role="status"
                    data-testid="home-project-type-card-opening"
                  >
                    {t('home:projectTypes.opening', '正在打开…')}
                  </p>
                )}

                {/* Arrow */}
                <div className={`absolute right-4 md:right-5 bottom-4 md:bottom-5 w-8 h-8 md:w-9 md:h-9 rounded-full bg-[hsl(var(--accent-primary))] flex items-center justify-center transition-opacity shadow-lg ${isOpening ? "opacity-100" : "opacity-0 group-hover:opacity-100"}`}>
                  {isOpening ? (
                    <Loader2 className="w-3.5 h-3.5 md:w-4 md:h-4 text-white animate-spin" />
                  ) : (
                    <ArrowRight className="w-3.5 h-3.5 md:w-4 md:h-4 text-white" />
                  )}
                </div>
              </button>
            )})}
          </div>
        </div>
      </section>

      {/* Features */}
      <section className="py-12 md:py-20 px-4 md:px-6 bg-[hsl(var(--bg-primary))]">
        <div className="max-w-6xl mx-auto">
          <div className="text-center mb-14 md:mb-16">
            <h2 className="text-2xl md:text-3xl lg:text-[36px] font-bold text-[hsl(var(--text-primary))] mb-3">{t('home:whyChoose.title')}</h2>
            <p className="text-sm md:text-base text-[hsl(var(--text-secondary))]">{t('home:whyChoose.subtitle')}</p>
          </div>
          
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 md:gap-6">
            {[
              { icon: Mic, key: 'voiceInput' },
              { icon: Paperclip, key: 'materialUpload' },
              { icon: Brain, key: 'contextAware' },
              { icon: History, key: 'versionHistory' },
              { icon: Cloud, key: 'cloudSync' },
              { icon: Download, key: 'export' }
            ].map((item, i) => {
              const featureTitle = t(`home:whyChoose.${item.key}.title`);
              const featureDesc = t(`home:whyChoose.${item.key}.description`);
              return (
              <div key={i} className="group p-4 md:p-6 rounded-xl bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--border-color))] hover:border-[hsl(var(--accent-primary)/0.3)] hover:shadow-[0_0_30px_rgba(74,158,255,0.1)] hover:-translate-y-1 transition-all duration-300">
                <div className="w-10 h-10 md:w-12 md:h-12 rounded-xl bg-[hsl(var(--accent-primary)/0.1)] flex items-center justify-center mb-3 md:mb-4 group-hover:scale-110 group-hover:bg-[hsl(var(--accent-primary)/0.15)] transition-all duration-300">
                  <item.icon className="w-5 h-5 md:w-6 md:h-6 text-[hsl(var(--accent-primary))]" />
                </div>
                <h3 className="text-base md:text-lg font-bold text-[hsl(var(--text-primary))] mb-1.5 md:mb-2">{featureTitle}</h3>
                <p className="text-xs md:text-sm text-[hsl(var(--text-secondary))] leading-relaxed">{featureDesc}</p>
              </div>
            )})}
          </div>
        </div>
      </section>

      {/* Pricing Teaser */}
      <section className="py-12 md:py-16 px-4 md:px-6 bg-[hsl(var(--bg-secondary))] border-y border-[hsl(var(--border-color))]">
        <div className="max-w-6xl mx-auto">
          <div className="text-center mb-8 md:mb-10">
            <h2 className="text-2xl md:text-3xl font-bold text-[hsl(var(--text-primary))] mb-3">
              {t('home:pricingTeaser.title', '先免费开始，按需升级')}
            </h2>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-8 max-w-3xl mx-auto">
            {([
              {
                key: 'free',
                badge: t('home:pricingTeaser.free.badge', '免费'),
                accentClass: 'text-[hsl(var(--text-primary))]',
                bgClass: 'bg-[hsl(var(--bg-primary))]',
              },
              {
                key: 'pro',
                badge: t('home:pricingTeaser.pro.badge', 'Pro'),
                accentClass: 'text-[hsl(var(--accent-primary))]',
                bgClass: 'bg-[hsl(var(--accent-primary)/0.08)]',
              },
            ] as const).map((plan) => (
              <div
                key={plan.key}
                className={`rounded-xl border border-[hsl(var(--border-color))] p-4 md:p-5 ${plan.bgClass}`}
              >
                <div className={`mb-2 text-sm font-semibold ${plan.accentClass}`}>{plan.badge}</div>
                <h3 className="text-base md:text-lg font-bold text-[hsl(var(--text-primary))] mb-2">
                  {t(`home:pricingTeaser.${plan.key}.title`)}
                </h3>
                <p className="text-xs md:text-sm text-[hsl(var(--text-secondary))] leading-relaxed">
                  {t(`home:pricingTeaser.${plan.key}.desc`)}
                </p>
              </div>
            ))}
          </div>

          <div className="flex flex-wrap justify-center items-center gap-3">
            <Link
              to={pricingTeaserLink}
              className="h-10 px-5 inline-flex items-center rounded-xl border border-[hsl(var(--border-color))] text-sm font-medium text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))] transition-colors"
            >
              {t('home:pricingTeaser.viewPricing', '查看套餐权益')}
            </Link>
            {!user && (
              <button
                onClick={() => handleGetStartedWithSource(HOME_CTA_SOURCES.pricingTeaser)}
                onMouseEnter={handleGetStartedHover}
                onFocus={handleGetStartedHover}
                className="h-10 px-5 inline-flex items-center rounded-xl bg-[hsl(var(--accent-primary))] text-sm font-semibold text-white hover:opacity-90 transition-opacity"
              >
                {t('home:pricingTeaser.primaryCta', '免费创建项目')}
              </button>
            )}
          </div>
        </div>
      </section>

      {/* Agent API Section */}
      <AgentApiSection />

      {/* CTA Section - 只对未登录用户显示 */}
      {!user && (
        <section className="py-14 md:py-24 lg:py-28 px-4 md:px-6 relative overflow-hidden bg-[hsl(var(--bg-secondary))]">
          {/* Background glow */}
          <div className="absolute inset-0 pointer-events-none">
            <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[400px] md:w-[500px] lg:w-[600px] h-[400px] md:h-[500px] lg:h-[600px] bg-[hsl(var(--accent-primary))] opacity-[0.05] blur-[100px] md:blur-[120px] lg:blur-[150px] rounded-full" />
          </div>

          <div className="max-w-6xl mx-auto text-center relative z-10 px-4 md:px-6">
            <h2 className="text-3xl md:text-4xl lg:text-[40px] font-bold text-[hsl(var(--text-primary))] mb-4">{t('home:cta.title')}</h2>
            <p className="text-lg md:text-xl text-[hsl(var(--text-secondary))] mb-6 md:mb-8">{t('home:cta.subtitle')}</p>
            <button
              onClick={() => handleGetStartedWithSource(HOME_CTA_SOURCES.cta)}
              onMouseEnter={handleGetStartedHover}
              onFocus={handleGetStartedHover}
              className="group relative h-12 md:h-14 px-6 md:px-8 lg:px-10 text-[15px] md:text-[16px] font-bold inline-flex items-center gap-2 md:gap-2.5 bg-[hsl(var(--accent-primary))] text-white rounded-xl overflow-hidden transition-all hover:shadow-[0_0_40px_rgba(74,158,255,0.5)] hover:scale-[1.02]"
            >
              <span className="relative z-10">{t('home:cta.button')}</span>
              <ArrowRight className="w-4 h-4 md:w-5 md:h-5 relative z-10 group-hover:translate-x-1 transition-transform" />
              {/* Shine effect */}
              <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/20 to-transparent -translate-x-full group-hover:translate-x-full transition-transform duration-700" />
            </button>
          </div>
        </section>
      )}

      </main>
      {/* Footer */}
      <footer className="py-4 md:py-6 px-4 md:px-6 border-t border-[hsl(var(--border-color))] bg-[hsl(var(--bg-primary))]">
        <div className="max-w-6xl mx-auto text-center space-y-1 md:space-y-2">
          <div className="flex flex-wrap items-center justify-center gap-4 text-xs">
            <Link to="/privacy-policy" className="text-[hsl(var(--text-secondary))] hover:opacity-100 transition-opacity">
              {t('privacy:footer.privacy')}
            </Link>
            <Link to="/terms-of-service" className="text-[hsl(var(--text-secondary))] hover:opacity-100 transition-opacity">
              {t('privacy:footer.terms')}
            </Link>
          </div>
          <p className="text-xs text-[hsl(var(--text-secondary))]">{t('home:footer.copyright', { year: new Date().getFullYear() })}</p>
        </div>
      </footer>
    </div>
  );
}

// ============================================
// Scene Components
// ============================================

type DemoScene = 'create' | 'suggest' | 'edit';

const DEMO_SCENE_TRANSLATION_KEY = {
  create: 'sceneCreate',
  suggest: 'sceneSuggest',
  edit: 'sceneEdit',
} as const;

const DEMO_SCENE_STATUS_KEY = {
  create: 'created',
  suggest: 'writing',
  edit: 'updated',
} as const;

const DEMO_FILES = [
  { key: 'worldBuilding', icon: FolderOpen },
  { key: 'characters', icon: User },
  { key: 'outline', icon: FileText },
  { key: 'currentFile', icon: Book },
] as const;

function DemoFileTree() {
  const { t } = useTranslation(['home']);
  return (
    <aside className="hidden md:block md:w-[21%] min-w-[128px] bg-[hsl(var(--bg-secondary))] border-r border-[hsl(var(--border-color))] p-3 shrink-0">
      <div className="text-[10px] text-[hsl(var(--text-secondary))] uppercase tracking-wider font-semibold mb-2">
        {t('home:preview.projectFiles')}
      </div>
      <div className="space-y-0.5">
        {DEMO_FILES.map(({ key, icon: Icon }) => {
          const selected = key === 'currentFile';
          return (
            <div
              key={key}
              className={`flex items-center gap-2 min-h-8 px-2 rounded-md text-[11px] ${selected
                ? 'bg-[hsl(var(--accent-primary)/0.1)] text-[hsl(var(--accent-primary))] font-medium'
                : 'text-[hsl(var(--text-secondary))]'}`}
            >
              <Icon className="w-3.5 h-3.5 shrink-0" />
              <span className="truncate">{t(`home:preview.${key}`)}</span>
            </div>
          );
        })}
      </div>
    </aside>
  );
}

function DemoAssistant({ scene }: { scene: DemoScene }) {
  const { t } = useTranslation(['home']);
  const isWriting = scene === 'suggest';
  const statusIcon = scene === 'create' ? Check : scene === 'edit' ? History : Edit3;
  const StatusIcon = statusIcon;
  const translationKey = DEMO_SCENE_TRANSLATION_KEY[scene];

  return (
    <section className="w-full md:w-[35%] min-w-[180px] bg-[hsl(var(--bg-secondary))] border-t md:border-t-0 md:border-l border-[hsl(var(--border-color))] flex flex-col shrink-0">
      <div className="h-10 px-3 border-b border-[hsl(var(--border-color))] flex items-center gap-2">
        <Sparkles className="w-3.5 h-3.5 text-[hsl(var(--accent-primary))]" />
        <span className="text-[10px] text-[hsl(var(--text-secondary))] font-semibold uppercase tracking-wider">
          {t('home:preview.assistant')}
        </span>
      </div>
      <div className="flex-1 p-2.5 space-y-2 overflow-hidden">
        <div className="flex justify-end">
          <div className="max-w-[92%] px-3 py-1.5 rounded-xl rounded-br-sm bg-[hsl(var(--accent-primary))] text-[11px] text-white">
            {t(`home:preview.${translationKey}.ai.input`)}
          </div>
        </div>
        <div className="flex justify-start">
          <div className="max-w-[92%] px-3 py-1.5 rounded-xl rounded-bl-sm bg-[hsl(var(--bg-tertiary))] text-[11px] text-[hsl(var(--text-secondary))]">
            {t(`home:preview.${translationKey}.ai.response`)}
          </div>
        </div>
        <div className="rounded-lg bg-[hsl(var(--accent-primary)/0.07)] p-2.5">
          <div className="flex items-center gap-2">
            <StatusIcon className={`w-3.5 h-3.5 text-[hsl(var(--accent-primary))] ${isWriting ? 'animate-pulse motion-reduce:animate-none' : ''}`} />
            <span className="text-[11px] font-semibold text-[hsl(var(--accent-primary))]">
              {t(`home:preview.${translationKey}.ai.${DEMO_SCENE_STATUS_KEY[scene]}`)}
            </span>
          </div>
          <p className="mt-1.5 text-[10px] leading-relaxed text-[hsl(var(--text-secondary))]">
            {t(`home:preview.${translationKey}.ai.desc`)}
          </p>
        </div>
      </div>
      <div className="p-2.5 border-t border-[hsl(var(--border-color))]">
        <div className="h-8 px-3 rounded-lg bg-[hsl(var(--bg-tertiary))] flex items-center justify-between">
          <span className="text-[11px] text-[hsl(var(--text-secondary))]">{t('home:preview.inputPlaceholder')}</span>
          <Mic className="w-3.5 h-3.5 text-[hsl(var(--text-secondary))]" />
        </div>
      </div>
    </section>
  );
}

function DemoWorkspace({ scene, children }: { scene: DemoScene; children: ReactNode }) {
  return (
    <div className="flex flex-col md:flex-row h-auto md:h-[380px]">
      <DemoFileTree />
      <div className="w-full md:w-[44%] min-h-[220px] p-4 bg-[hsl(var(--bg-primary))] overflow-hidden">
        {children}
      </div>
      <DemoAssistant scene={scene} />
    </div>
  );
}

function SceneCreate() {
  const { t } = useTranslation(['home']);
  return (
    <DemoWorkspace scene="create">
      <div className="text-sm font-semibold text-[hsl(var(--text-primary))] mb-3">{t('home:preview.currentFile')}</div>
      <div className="space-y-3 text-xs leading-relaxed text-[hsl(var(--text-secondary))]">
        <p>{t('home:preview.sceneCreate.content1')}</p>
        <p>{t('home:preview.sceneCreate.content2')}</p>
        <div className="rounded-lg bg-[hsl(var(--bg-secondary))] p-3">
          <div className="mb-2 text-[10px] font-semibold text-[hsl(var(--text-secondary))]">{t('home:preview.sceneCreate.contextTitle')}</div>
          <div className="flex flex-wrap gap-1.5">
            {[t('home:preview.outline'), t('home:preview.characters'), t('home:preview.worldBuilding')].map(label => (
              <span key={label} className="rounded-full bg-[hsl(var(--bg-tertiary))] px-2 py-1 text-[10px] text-[hsl(var(--text-primary))]">{label}</span>
            ))}
          </div>
        </div>
      </div>
    </DemoWorkspace>
  );
}

function SceneSuggest() {
  const { t } = useTranslation(['home']);
  return (
    <DemoWorkspace scene="suggest">
      <div className="text-sm font-semibold text-[hsl(var(--text-primary))] mb-3">{t('home:preview.currentFile')}</div>
      <div className="space-y-2 text-xs leading-relaxed text-[hsl(var(--text-secondary))]">
        <p>{t('home:preview.sceneSuggest.content1')}</p>
        <p>{t('home:preview.sceneSuggest.content2')}</p>
        <p className="text-[hsl(var(--text-primary))]">
          {t('home:preview.sceneSuggest.content3')}
          <span className="inline-block w-0.5 h-3.5 bg-[hsl(var(--accent-primary))] ml-1 align-middle animate-pulse motion-reduce:animate-none" />
        </p>
      </div>
    </DemoWorkspace>
  );
}

function SceneEdit() {
  const { t } = useTranslation(['home']);
  return (
    <DemoWorkspace scene="edit">
      <div className="flex items-center justify-between gap-2 mb-3">
        <div className="text-sm font-semibold text-[hsl(var(--text-primary))]">{t('home:preview.currentFile')}</div>
        <span className="text-[10px] text-[hsl(var(--text-secondary))]">{t('home:preview.sceneEdit.version')}</span>
      </div>
      <div className="space-y-2.5 text-xs leading-relaxed">
        <div className="rounded-lg bg-[hsl(var(--bg-secondary))] p-3">
          <div className="mb-1 text-[10px] text-[hsl(var(--text-secondary))]">{t('home:preview.sceneEdit.original')}</div>
          <p className="text-[hsl(var(--text-secondary))]">{t('home:preview.sceneEdit.originalText')}</p>
        </div>
        <div className="rounded-lg bg-[hsl(var(--accent-primary)/0.07)] p-3">
          <div className="mb-1 text-[10px] font-medium text-[hsl(var(--accent-primary))]">{t('home:preview.sceneEdit.suggestion')}</div>
          <p className="text-[hsl(var(--text-primary))]">{t('home:preview.sceneEdit.suggestionText')}</p>
        </div>
      </div>
    </DemoWorkspace>
  );
}
