import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, Link, useSearchParams } from "react-router-dom";
import {
  Book, FileText, Clapperboard, ArrowRight, Sparkles, Check, Zap,
  TrendingUp, Mic, Paperclip, Brain, History, Cloud, Download,
  Edit3, User, Users, PenTool, Star
} from "../components/icons";
import { useAuth } from "../contexts/AuthContext";
import { usePreloadRoute } from "../hooks/usePreloadRoute";
import { useMediaQuery } from "../hooks/useMediaQuery";
import { authConfig } from "../config/auth";
import { buildUpgradeUrl } from "../config/upgradeExperience";
import { PublicHeader } from "../components/PublicHeader";
import { AgentApiSection } from "../components/home/AgentApiSection";

// Scene definitions for the product preview carousel
const SCENES = [
  { id: 'create', duration: 5000 },
  { id: 'suggest', duration: 5000 },
  { id: 'edit', duration: 5000 },
] as const;

type SceneId = typeof SCENES[number]['id'];

const SOCIAL_PROOF_METRICS = {
  creators: 2000,
  wordsGenerated: 12000000,
  rating: 4.9,
} as const;

const HOME_CTA_SOURCES = {
  hero: "home_hero",
  pricingTeaser: "home_pricing_teaser",
  cta: "home_cta",
  projectTypeCard: "home_project_type_card",
} as const;

function resolveIntlLocale(language?: string): string {
  if (!language) return "en-US";
  const normalized = language.toLowerCase();
  if (normalized.startsWith("zh")) return "zh-CN";
  if (normalized.startsWith("en")) return "en-US";
  return language;
}

export default function HomePage() {
  const { t, i18n } = useTranslation(['home', 'privacy']);
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { user } = useAuth();
  const prefersReducedMotion = useMediaQuery("(prefers-reduced-motion: reduce)");
  const [activeScene, setActiveScene] = useState(0);
  const [isTransitioning, setIsTransitioning] = useState(false);
  const [sceneProgress, setSceneProgress] = useState(0);
  const demoSectionRef = useRef<HTMLDivElement | null>(null);
  const intlLocale = useMemo(
    () => resolveIntlLocale(i18n.resolvedLanguage ?? i18n.language),
    [i18n.language, i18n.resolvedLanguage]
  );
  const creatorsMetric = useMemo(() => {
    const formatter = new Intl.NumberFormat(intlLocale);
    return `${formatter.format(SOCIAL_PROOF_METRICS.creators)}+`;
  }, [intlLocale]);
  const wordsGeneratedMetric = useMemo(() => {
    const formatter = new Intl.NumberFormat(intlLocale, {
      notation: "compact",
      compactDisplay: "short",
      maximumFractionDigits: 1,
    });
    return `${formatter.format(SOCIAL_PROOF_METRICS.wordsGenerated)}+`;
  }, [intlLocale]);
  const ratingMetric = useMemo(() => {
    const formatter = new Intl.NumberFormat(intlLocale, {
      minimumFractionDigits: 1,
      maximumFractionDigits: 1,
    });
    return formatter.format(SOCIAL_PROOF_METRICS.rating);
  }, [intlLocale]);
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
    demoSectionRef.current?.scrollIntoView({
      behavior: prefersReducedMotion ? "auto" : "smooth",
      block: "center",
    });
  };

  // Auto-rotate scenes with progress
  useEffect(() => {
    if (prefersReducedMotion) {
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
        setIsTransitioning(true);
        setTimeout(() => {
          setActiveScene((prev) => (prev + 1) % SCENES.length);
          setIsTransitioning(false);
          setSceneProgress(0);
        }, 300);
      }
    }, 80);

    return () => clearInterval(timer);
  }, [activeScene, prefersReducedMotion]);

  // Handle manual scene selection
  const handleSceneSelect = useCallback((index: number) => {
    if (index === activeScene) return;

    if (prefersReducedMotion) {
      setActiveScene(index);
      setSceneProgress(0);
      return;
    }

    setIsTransitioning(true);
    setTimeout(() => {
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
    <div className="min-h-screen relative overflow-hidden bg-[hsl(var(--bg-primary))]">
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
                <h1 className="text-4xl md:text-5xl lg:text-[52px] font-bold leading-[1.1] md:leading-[1.05] text-[hsl(var(--text-primary))] mb-4 md:mb-5">
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
                  <p className="text-[10px] md:text-xs text-[hsl(var(--text-secondary))] mt-2 md:mt-3 opacity-70">
                    {t('home:hero.ctaSubtext')}
                  </p>
                </div>
              )}
              
              {/* Features */}
              <div className="flex flex-wrap gap-x-6 gap-y-2.5 md:gap-x-8 md:gap-y-3 pt-4 md:pt-6">
                <div className="flex items-center gap-2 md:gap-2.5 text-xs md:text-sm text-[hsl(var(--text-secondary))]">
                  <Check className="w-4 h-4 md:w-4.5 md:h-4.5 text-[hsl(var(--success))]" strokeWidth={2.5} />
                  <span className="font-medium">{t('home:features.cloudSave')}</span>
                </div>
                <div className="flex items-center gap-2 md:gap-2.5 text-xs md:text-sm text-[hsl(var(--text-secondary))]">
                  <Check className="w-4 h-4 md:w-4.5 md:h-4.5 text-[hsl(var(--success))]" strokeWidth={2.5} />
                  <span className="font-medium">{t('home:features.multiDevice')}</span>
                </div>
                <div className="flex items-center gap-2 md:gap-2.5 text-xs md:text-sm text-[hsl(var(--text-secondary))]">
                  <Check className="w-4 h-4 md:w-4.5 md:h-4.5 text-[hsl(var(--success))]" strokeWidth={2.5} />
                  <span className="font-medium">{t('home:features.versionHistory')}</span>
                </div>
              </div>
            </div>

            {/* Right - Product Preview with Scene Carousel */}
            <div
              ref={demoSectionRef}
              className="relative"
            >
              {/* Glow effect behind card */}
              <div className="absolute -inset-4 bg-[hsl(var(--accent-primary))] rounded-3xl opacity-12 blur-2xl" />
              
              {/* Main card */}
              <div className="relative rounded-2xl overflow-hidden">
                {/* Gradient border */}
                <div className="absolute -inset-[1px] bg-[hsl(var(--border-color))] rounded-2xl opacity-60" />
                
                {/* Card content */}
                <div className="relative bg-[hsl(var(--bg-secondary))] rounded-2xl overflow-hidden">
                  {/* Window bar */}
                  <div className="h-10 md:h-12 bg-[hsl(var(--bg-secondary))] border-b border-[hsl(var(--border-color))] flex items-center px-3 md:px-4 gap-2 md:gap-3">
                    <div className="flex gap-1.5 md:gap-2">
                      <div className="w-2.5 h-2.5 md:w-3 md:h-3 rounded-full bg-red-400" />
                      <div className="w-2.5 h-2.5 md:w-3 md:h-3 rounded-full bg-yellow-400" />
                      <div className="w-2.5 h-2.5 md:w-3 md:h-3 rounded-full bg-green-400" />
                    </div>
                    <div className="flex-1 flex justify-center">
                      <div className="h-6 md:h-7 px-3 md:px-5 rounded-lg bg-[hsl(var(--bg-tertiary))] border border-[hsl(var(--border-color))] text-[10px] md:text-xs text-[hsl(var(--text-secondary))] flex items-center gap-1.5 md:gap-2">
                        <Sparkles className="w-3 h-3 md:w-3.5 md:h-3.5 text-[hsl(var(--accent-primary))]" />
                        {t('home:demo.projectName')}
                      </div>
                    </div>
                  </div>
                  
                  {/* Scene content with transition */}
                  <div className={prefersReducedMotion ? 'opacity-100' : `transition-opacity duration-300 ${isTransitioning ? 'opacity-0' : 'opacity-100'}`}>
                    {renderSceneContent(SCENES[activeScene].id)}
                  </div>
                </div>
              </div>
              
              {/* Scene indicator - text buttons */}
              <div className="flex gap-2 md:gap-3 justify-center mt-4 md:mt-5">
                {SCENES.map((scene, index) => (
                  <button
                    key={scene.id}
                    onClick={() => handleSceneSelect(index)}
                    className={`text-[10px] md:text-xs px-3 md:px-4 py-1 md:py-1.5 rounded-full transition-all duration-300 ${
                      index === activeScene
                        ? 'bg-[hsl(var(--accent-primary))] text-white shadow-[0_0_15px_rgba(74,158,255,0.4)]'
                        : 'text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))]'
                    }`}
                  >
                    {t(`home:demo.scenes.${scene.id}`)}
                  </button>
                ))}
              </div>

              {/* Carousel controls */}
              {!prefersReducedMotion && (
                <div className="mt-3 flex justify-center">
                  <div className="w-28 md:w-36 h-1 rounded-full bg-[hsl(var(--bg-tertiary))] overflow-hidden">
                    <div
                      className="h-full bg-[hsl(var(--accent-primary))] transition-[width] duration-100"
                      style={{ width: `${sceneProgress}%` }}
                    />
                  </div>
                </div>
              )}

            </div>
          </div>
          
          {/* Social Proof */}
          <div className="flex items-center justify-center gap-6 sm:gap-8 md:gap-12 mt-12 md:mt-16 pt-8 md:pt-10 border-t border-[hsl(var(--border-color))]">
            <div className="text-center">
              <div className="text-2xl md:text-3xl font-bold text-[hsl(var(--text-primary))]">{creatorsMetric}</div>
              <div className="text-[10px] md:text-xs text-[hsl(var(--text-secondary))] mt-1 flex items-center gap-1 justify-center">
                <Users className="w-3 h-3" />
                {t('home:stats.creators')}
              </div>
            </div>
            <div className="w-px h-8 md:h-10 bg-[hsl(var(--border-color))]" />
            <div className="text-center">
              <div className="text-2xl md:text-3xl font-bold text-[hsl(var(--text-primary))]">{wordsGeneratedMetric}</div>
              <div className="text-[10px] md:text-xs text-[hsl(var(--text-secondary))] mt-1 flex items-center gap-1 justify-center">
                <PenTool className="w-3 h-3" />
                {t('home:stats.wordsGenerated')}
              </div>
            </div>
            <div className="w-px h-8 md:h-10 bg-[hsl(var(--border-color))]" />
            <div className="text-center">
              <div className="text-2xl md:text-3xl font-bold text-[hsl(var(--text-primary))]">{ratingMetric}</div>
              <div className="text-[10px] md:text-xs text-[hsl(var(--text-secondary))] mt-1 flex items-center gap-1 justify-center">
                <Star className="w-3 h-3" />
                {t('home:stats.rating')}
              </div>
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
          
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 md:gap-5">
            {[
              {
                icon: Book,
                type: 'novel',
                colorClass: "text-[hsl(var(--text-primary))]",
                bgClass: "bg-white/5",
                popular: true
              },
              {
                icon: FileText,
                type: 'short',
                colorClass: "text-emerald-500",
                bgClass: "bg-emerald-500/10",
                popular: false
              },
              {
                icon: Clapperboard,
                type: 'screenplay',
                colorClass: "text-[hsl(var(--text-primary))]",
                bgClass: "bg-white/5",
                popular: false
              }
            ].map((item) => {
              const projectType = t(`home:projectTypes.${item.type}.name`);
              const projectDesc = t(`home:projectTypes.${item.type}.description`);
              const projectFeaturesRaw = t(`home:projectTypes.${item.type}.features`, { returnObjects: true }) as unknown;
              const projectFeatures = Array.isArray(projectFeaturesRaw)
                ? (projectFeaturesRaw as unknown[])
                : [];
              return (
              <button
                key={item.type}
                type="button"
                onClick={handleGetStarted}
                onFocus={handleGetStartedHover}
                className="group relative w-full text-left p-4 md:p-5 rounded-xl bg-[hsl(var(--bg-primary))] border border-[hsl(var(--border-color))] hover:border-[hsl(var(--accent-primary)/0.5)] hover:shadow-[0_0_40px_rgba(74,158,255,0.15)] hover:-translate-y-1 transition-all duration-300 cursor-pointer overflow-hidden focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.6)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-primary))]"
              >
                {/* Popular badge */}
                {item.popular && (
                  <div className="absolute top-3 md:top-4 right-3 md:right-4 px-2 md:px-3 py-1 md:py-1.5 rounded-full bg-[hsl(var(--warning))] text-white text-[10px] md:text-[11px] font-bold shadow-md flex items-center gap-0.5 md:gap-1">
                    <TrendingUp className="w-2.5 h-2.5 md:w-3 md:h-3" />
                    {t('home:demo.popular')}
                  </div>
                )}

                <div className={`w-12 h-12 md:w-14 md:h-14 rounded-2xl ${item.bgClass} flex items-center justify-center mb-4 md:mb-5 group-hover:scale-105 transition-transform`}>
                  <item.icon className={`w-5 h-5 md:w-6 md:h-6 ${item.colorClass}`} />
                </div>
                <h3 className="text-[17px] md:text-[19px] font-bold text-[hsl(var(--text-primary))] mb-1.5 md:mb-2">{projectType}</h3>
                <p className="text-xs md:text-sm text-[hsl(var(--text-secondary))] mb-3 md:mb-5 leading-relaxed">{projectDesc}</p>
                <div className="flex flex-wrap gap-1.5 md:gap-2">
                  {projectFeatures.map((f, j) => (
                    <span key={j} className={`text-[10px] md:text-xs px-2 md:px-3 py-1 md:py-1.5 rounded-full ${item.bgClass} ${item.colorClass} font-medium`}>
                      {String(f)}
                    </span>
                  ))}
                </div>

                {/* Arrow */}
                <div className="absolute right-4 md:right-5 bottom-4 md:bottom-5 w-8 h-8 md:w-9 md:h-9 rounded-full bg-[hsl(var(--accent-primary))] flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity shadow-lg">
                  <ArrowRight className="w-3.5 h-3.5 md:w-4 md:h-4 text-white" />
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
            <p className="text-sm md:text-base text-[hsl(var(--text-secondary))]">
              {t('home:pricingTeaser.subtitle', '免费版即可完整体验创作流程，升级后解锁更高额度与效率。')}
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-8">
            {([
              {
                key: 'free',
                badge: t('home:pricingTeaser.free.badge', '免费'),
                accentClass: 'text-[hsl(var(--text-primary))]',
                bgClass: 'bg-[hsl(var(--bg-primary))]',
              },
              {
                key: 'pro',
                badge: t('home:pricingTeaser.pro.badge', '专业版'),
                accentClass: 'text-[hsl(var(--accent-primary))]',
                bgClass: 'bg-[hsl(var(--accent-primary)/0.08)]',
              },
              {
                key: 'team',
                badge: t('home:pricingTeaser.team.badge', '团队协作'),
                accentClass: 'text-[hsl(var(--success))]',
                bgClass: 'bg-[hsl(var(--success)/0.08)]',
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

      {/* Footer */}
      <footer className="py-4 md:py-6 px-4 md:px-6 border-t border-[hsl(var(--border-color))] bg-[hsl(var(--bg-primary))]">
        <div className="max-w-6xl mx-auto text-center space-y-1 md:space-y-2">
          <div className="flex flex-wrap items-center justify-center gap-4 text-xs">
            {/* Static org pages (generated by scripts/build-org-pages.mjs) — plain anchors, not SPA routes */}
            <a href="/projects" className="text-[hsl(var(--text-secondary))] opacity-60 hover:opacity-100 transition-opacity">
              {t('home:footer.projects')}
            </a>
            <span className="text-[hsl(var(--text-secondary))] opacity-40">|</span>
            <a href="/glossary" className="text-[hsl(var(--text-secondary))] opacity-60 hover:opacity-100 transition-opacity">
              {t('home:footer.glossary')}
            </a>
            <span className="text-[hsl(var(--text-secondary))] opacity-40">|</span>
            <a href="https://github.com/zenstory-ai" className="text-[hsl(var(--text-secondary))] opacity-60 hover:opacity-100 transition-opacity">
              {t('home:footer.github')}
            </a>
            <span className="text-[hsl(var(--text-secondary))] opacity-40">|</span>
            <Link to="/privacy-policy" className="text-[hsl(var(--text-secondary))] opacity-60 hover:opacity-100 transition-opacity">
              {t('privacy:footer.privacy')}
            </Link>
            <span className="text-[hsl(var(--text-secondary))] opacity-40">|</span>
            <Link to="/terms-of-service" className="text-[hsl(var(--text-secondary))] opacity-60 hover:opacity-100 transition-opacity">
              {t('privacy:footer.terms')}
            </Link>
          </div>
          <p className="text-xs text-[hsl(var(--text-secondary))] opacity-60">{t('home:footer.copyright')}</p>
        </div>
      </footer>
    </div>
  );
}

// ============================================
// Scene Components
// ============================================

// Scene 1: Create Character - Shows tool call card
function SceneCreate() {
  const { t } = useTranslation(['home']);
  return (
    <div className="flex flex-col md:flex-row h-auto md:h-[380px]">
      {/* Sidebar - 20% */}
      <div className="w-full md:w-[20%] min-w-[120px] bg-[hsl(var(--bg-secondary))] border-b md:border-b-0 md:border-r border-[hsl(var(--border-color))] p-3 shrink-0">
        <div className="text-[10px] text-[hsl(var(--text-secondary))] uppercase tracking-wider font-semibold mb-2">{t('home:preview.projectFiles')}</div>
        <div className="space-y-0.5">
          <div className="flex items-center gap-2 h-8 px-2 rounded-md text-[hsl(var(--text-secondary))] text-xs">
            <span>📁</span>
            <span className="truncate">{t('home:preview.worldBuilding')}</span>
          </div>
          <div className="flex items-center gap-2 h-8 px-2 rounded-md bg-[hsl(var(--accent-primary)/0.1)] border border-[hsl(var(--accent-primary)/0.2)] text-[hsl(var(--accent-primary))] text-xs font-medium">
            <span>👤</span>
            <span className="truncate">{t('home:preview.characters')}</span>
          </div>
          <div className="flex items-center gap-2 h-8 px-2 rounded-md text-[hsl(var(--text-secondary))] text-xs">
            <span>📝</span>
            <span className="truncate">{t('home:preview.outline')}</span>
          </div>
          <div className="flex items-center gap-2 h-8 px-2 rounded-md text-[hsl(var(--text-secondary))] text-xs">
            <span>📖</span>
            <span className="truncate">{t('home:preview.draft')}</span>
          </div>
        </div>
      </div>

      {/* Editor content - 45% */}
      <div className="w-full md:w-[45%] p-4 bg-[hsl(var(--bg-primary))] overflow-hidden">
        <div className="text-sm font-semibold text-[hsl(var(--text-primary))] mb-3 flex items-center gap-2">
          <User className="w-4 h-4 text-[hsl(var(--accent-primary))]" />
          {t('home:preview.sceneCreate.character.name')}
        </div>
        <div className="space-y-2 text-xs leading-relaxed">
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-[hsl(var(--text-secondary))]">
            <span><span className="opacity-60">{t('home:preview.sceneCreate.character.gender')}</span> {t('home:preview.sceneCreate.character.genderValue')}</span>
            <span><span className="opacity-60">{t('home:preview.sceneCreate.character.age')}</span> {t('home:preview.sceneCreate.character.ageValue')}</span>
            <span><span className="opacity-60">{t('home:preview.sceneCreate.character.identity')}</span> {t('home:preview.sceneCreate.character.identityValue')}</span>
          </div>
          <div className="text-[hsl(var(--text-secondary))] pt-2 border-t border-[hsl(var(--border-color))]">
            <span className="opacity-60 mb-1 block text-[10px]">{t('home:preview.sceneCreate.character.personality')}</span>
            {t('home:preview.sceneCreate.character.personalityDesc')}
          </div>
          <div className="text-[hsl(var(--text-primary))]">
            <span className="text-[hsl(var(--text-secondary))] opacity-60 mb-1 block text-[10px]">{t('home:preview.sceneCreate.character.secret')}</span>
            <span className="text-[hsl(var(--accent-primary))] font-medium">{t('home:preview.sceneCreate.character.secretDesc')}</span>
            <span className="inline-block w-0.5 h-3.5 bg-[hsl(var(--accent-primary))] ml-1 align-middle animate-pulse" />
          </div>
        </div>
      </div>

      {/* Chat panel - 35% */}
      <div className="w-full md:w-[35%] min-w-[180px] bg-[hsl(var(--bg-secondary))] border-l border-[hsl(var(--border-color))] flex flex-col shrink-0">
        <div className="h-10 px-3 border-b border-[hsl(var(--border-color))] flex items-center gap-2">
          <Sparkles className="w-3.5 h-3.5 text-[hsl(var(--accent-primary))]" />
          <span className="text-[10px] text-[hsl(var(--text-secondary))] font-semibold uppercase tracking-wider">{t('home:preview.sceneCreate.ai.title')}</span>
        </div>
        <div className="flex-1 p-2.5 space-y-2 overflow-hidden">
          {/* User message */}
          <div className="flex justify-end">
            <div className="max-w-[90%] px-3 py-1.5 rounded-xl rounded-br-sm bg-[hsl(var(--accent-primary))] text-[11px] text-white">
              {t('home:preview.sceneCreate.ai.input')}
            </div>
          </div>

          {/* AI reply */}
          <div className="flex justify-start">
            <div className="max-w-[90%] px-3 py-1.5 rounded-xl rounded-bl-sm bg-[hsl(var(--bg-tertiary))] border border-[hsl(var(--border-color))] text-[11px] text-[hsl(var(--text-secondary))]">
              {t('home:preview.sceneCreate.ai.response')}
            </div>
          </div>

          {/* Tool call card */}
          <div className="rounded-lg bg-[hsl(var(--success)/0.08)] border border-[hsl(var(--success)/0.25)] p-2.5">
            <div className="flex items-center gap-2 mb-1.5">
              <div className="w-4 h-4 rounded-full bg-[hsl(var(--success))] flex items-center justify-center">
                <Check className="w-2.5 h-2.5 text-white" />
              </div>
              <span className="text-[11px] font-semibold text-[hsl(var(--success))]">{t('home:preview.sceneCreate.ai.created')}</span>
            </div>
            <div className="text-[11px] text-[hsl(var(--text-primary))] font-medium">{t('home:preview.sceneCreate.character.name')}</div>
            <div className="text-[10px] text-[hsl(var(--text-secondary))] mt-1">{t('home:preview.sceneCreate.ai.desc')}</div>
          </div>
        </div>

        {/* Input area */}
        <div className="p-2.5 border-t border-[hsl(var(--border-color))]">
          <div className="h-8 px-3 rounded-lg bg-[hsl(var(--bg-tertiary))] border border-[hsl(var(--border-color))] flex items-center justify-between">
            <span className="text-[11px] text-[hsl(var(--text-secondary))]">{t('home:preview.sceneCreate.ai.placeholder')}</span>
            <Mic className="w-3.5 h-3.5 text-[hsl(var(--text-secondary))]" />
          </div>
        </div>
      </div>
    </div>
  );
}

// Scene 2: Smart Suggestion - Shows suggestion bubble + voice button
function SceneSuggest() {
  const { t } = useTranslation(['home']);
  return (
    <div className="flex flex-col md:flex-row h-auto md:h-[380px]">
      {/* Sidebar - 20% */}
      <div className="w-full md:w-[20%] min-w-[120px] bg-[hsl(var(--bg-secondary))] border-b md:border-b-0 md:border-r border-[hsl(var(--border-color))] p-3 shrink-0">
        <div className="text-[10px] text-[hsl(var(--text-secondary))] uppercase tracking-wider font-semibold mb-2">{t('home:preview.projectFiles')}</div>
        <div className="space-y-0.5">
          <div className="flex items-center gap-2 h-8 px-2 rounded-md text-[hsl(var(--text-secondary))] text-xs">
            <span>📁</span>
            <span className="truncate">{t('home:preview.worldBuilding')}</span>
          </div>
          <div className="flex items-center gap-2 h-8 px-2 rounded-md text-[hsl(var(--text-secondary))] text-xs">
            <span>👤</span>
            <span className="truncate">{t('home:preview.characters')}</span>
          </div>
          <div className="flex items-center gap-2 h-8 px-2 rounded-md text-[hsl(var(--text-secondary))] text-xs">
            <span>📝</span>
            <span className="truncate">{t('home:preview.outline')}</span>
          </div>
          <div className="flex items-center gap-2 h-8 px-2 rounded-md bg-[hsl(var(--accent-primary)/0.1)] border border-[hsl(var(--accent-primary)/0.2)] text-[hsl(var(--accent-primary))] text-xs font-medium">
            <span>📖</span>
            <span className="truncate">{t('home:preview.sceneSuggest.chapter').split(' · ')[1]}</span>
          </div>
        </div>
      </div>

      {/* Editor content - 45% */}
      <div className="w-full md:w-[45%] p-4 bg-[hsl(var(--bg-primary))] relative overflow-hidden">
        {/* AI writing progress bar */}
        <div className="absolute top-0 left-0 right-0 h-0.5 bg-[hsl(var(--accent-primary)/0.2)] overflow-hidden">
          <div className="h-full w-full" style={{
            background: 'linear-gradient(90deg, transparent 0%, hsl(var(--accent-primary)) 50%, transparent 100%)',
            backgroundSize: '200% 100%',
            animation: 'shimmer 2s ease-in-out infinite'
          }} />
        </div>

        <div className="text-sm font-semibold text-[hsl(var(--text-primary))] mb-3">{t('home:preview.sceneSuggest.chapter')}</div>
        <div className="space-y-2 text-xs leading-relaxed text-[hsl(var(--text-secondary))]">
          <p>{t('home:preview.sceneSuggest.content1')}</p>
          <p>{t('home:preview.sceneSuggest.content2')}</p>
          <p className="text-[hsl(var(--text-primary))]">
            {t('home:preview.sceneSuggest.content3')}
            <span className="inline-block w-0.5 h-3.5 bg-[hsl(var(--accent-primary))] ml-1 align-middle animate-pulse" />
          </p>
        </div>
      </div>

      {/* Chat panel - 35% */}
      <div className="w-full md:w-[35%] min-w-[180px] bg-[hsl(var(--bg-secondary))] border-l border-[hsl(var(--border-color))] flex flex-col shrink-0">
        <div className="h-10 px-3 border-b border-[hsl(var(--border-color))] flex items-center gap-2">
          <Sparkles className="w-3.5 h-3.5 text-[hsl(var(--accent-primary))]" />
          <span className="text-[10px] text-[hsl(var(--text-secondary))] font-semibold uppercase tracking-wider">{t('home:preview.sceneSuggest.ai.title')}</span>
        </div>
        <div className="flex-1 p-2.5 space-y-2 overflow-hidden">
          {/* User message */}
          <div className="flex justify-end">
            <div className="max-w-[90%] px-3 py-1.5 rounded-xl rounded-br-sm bg-[hsl(var(--accent-primary))] text-[11px] text-white">
              {t('home:preview.sceneSuggest.ai.input')}
            </div>
          </div>

          {/* AI reply */}
          <div className="flex justify-start">
            <div className="max-w-[90%] px-3 py-1.5 rounded-xl rounded-bl-sm bg-[hsl(var(--bg-tertiary))] border border-[hsl(var(--border-color))] text-[11px] text-[hsl(var(--text-secondary))]">
              {t('home:preview.sceneSuggest.ai.response')}
            </div>
          </div>

          {/* Tool call - writing in progress */}
          <div className="rounded-lg bg-[hsl(var(--accent-primary)/0.08)] border border-[hsl(var(--accent-primary)/0.25)] p-2.5">
            <div className="flex items-center gap-2">
              <div className="w-4 h-4 rounded-full bg-[hsl(var(--accent-primary))] flex items-center justify-center">
                <Edit3 className="w-2.5 h-2.5 text-white" />
              </div>
              <span className="text-[11px] font-semibold text-[hsl(var(--accent-primary))]">{t('home:preview.sceneSuggest.ai.writing')}</span>
            </div>
            <div className="mt-2 flex items-center gap-2">
              <div className="flex-1 h-1 bg-[hsl(var(--bg-tertiary))] rounded-full overflow-hidden">
                <div className="h-full w-3/4 bg-[hsl(var(--accent-primary))] rounded-full" />
              </div>
              <span className="text-[10px] text-[hsl(var(--text-secondary))]">75%</span>
            </div>
          </div>
        </div>

        {/* Smart suggestion bubble */}
        <div className="px-2.5 pb-1.5">
          <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-[hsl(var(--accent-primary)/0.08)] border border-[hsl(var(--accent-primary)/0.15)] w-fit">
            <Sparkles className="w-3 h-3 text-[hsl(var(--accent-primary))]" />
            <span className="text-[10px] text-[hsl(var(--accent-primary))] font-medium">{t('home:preview.sceneSuggest.ai.action')}</span>
            <span className="text-[9px] text-[hsl(var(--text-secondary))] opacity-70 px-1 py-0.5 bg-[hsl(var(--bg-tertiary))] rounded text-[8px]">Tab</span>
          </div>
        </div>

        {/* Input area */}
        <div className="p-2.5 border-t border-[hsl(var(--border-color))]">
          <div className="h-8 px-3 rounded-lg bg-[hsl(var(--bg-tertiary))] border border-[hsl(var(--border-color))] flex items-center justify-between">
            <span className="text-[11px] text-[hsl(var(--text-secondary))]">{t('home:preview.sceneSuggest.ai.placeholder')}</span>
            <Mic className="w-4 h-4 text-[hsl(var(--accent-primary))]" />
          </div>
        </div>
      </div>
    </div>
  );
}

// Scene 3: Edit/Expand - Shows edit tool call card
function SceneEdit() {
  const { t } = useTranslation(['home']);
  return (
    <div className="flex flex-col md:flex-row h-auto md:h-[380px]">
      {/* Sidebar - 20% */}
      <div className="w-full md:w-[20%] min-w-[120px] bg-[hsl(var(--bg-secondary))] border-b md:border-b-0 md:border-r border-[hsl(var(--border-color))] p-3 shrink-0">
        <div className="text-[10px] text-[hsl(var(--text-secondary))] uppercase tracking-wider font-semibold mb-2">{t('home:preview.projectFiles')}</div>
        <div className="space-y-0.5">
          <div className="flex items-center gap-2 h-8 px-2 rounded-md text-[hsl(var(--text-secondary))] text-xs">
            <span>📁</span>
            <span className="truncate">{t('home:preview.worldBuilding')}</span>
          </div>
          <div className="flex items-center gap-2 h-8 px-2 rounded-md text-[hsl(var(--text-secondary))] text-xs">
            <span>👤</span>
            <span className="truncate">{t('home:preview.characters')}</span>
          </div>
          <div className="flex items-center gap-2 h-8 px-2 rounded-md bg-[hsl(var(--accent-primary)/0.1)] border border-[hsl(var(--accent-primary)/0.2)] text-[hsl(var(--accent-primary))] text-xs font-medium">
            <span>📝</span>
            <span className="truncate">{t('home:preview.outline')}</span>
          </div>
          <div className="flex items-center gap-2 h-8 px-2 rounded-md text-[hsl(var(--text-secondary))] text-xs">
            <span>📖</span>
            <span className="truncate">{t('home:preview.draft')}</span>
          </div>
        </div>
      </div>

      {/* Editor content - 45% */}
      <div className="w-full md:w-[45%] p-4 bg-[hsl(var(--bg-primary))] overflow-hidden">
        <div className="text-sm font-semibold text-[hsl(var(--text-primary))] mb-3">{t('home:preview.sceneEdit.chapter')}</div>
        <div className="space-y-2 text-xs leading-relaxed">
          <div className="p-2.5 rounded-lg bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--border-color))]">
            <div className="text-[10px] text-[hsl(var(--text-secondary))] mb-0.5">{t('home:preview.sceneEdit.scene1')}</div>
            <div className="text-[hsl(var(--text-primary))]">{t('home:preview.sceneEdit.scene1Desc')}</div>
          </div>
          <div className="p-2.5 rounded-lg bg-[hsl(var(--accent-primary)/0.05)] border border-[hsl(var(--accent-primary)/0.2)]">
            <div className="text-[10px] text-[hsl(var(--accent-primary))] mb-0.5">{t('home:preview.sceneEdit.scene2')}</div>
            <div className="text-[hsl(var(--text-primary))]">{t('home:preview.sceneEdit.scene2Desc')}</div>
          </div>
          <div className="p-2.5 rounded-lg bg-[hsl(var(--accent-primary)/0.05)] border border-[hsl(var(--accent-primary)/0.2)]">
            <div className="text-[10px] text-[hsl(var(--accent-primary))] mb-0.5">{t('home:preview.sceneEdit.scene3')}</div>
            <div className="text-[hsl(var(--text-primary))]">
              {t('home:preview.sceneEdit.scene3Desc')}
              <span className="inline-block w-0.5 h-3.5 bg-[hsl(var(--accent-primary))] ml-1 align-middle animate-pulse" />
            </div>
          </div>
        </div>
      </div>

      {/* Chat panel - 35% */}
      <div className="w-full md:w-[35%] min-w-[180px] bg-[hsl(var(--bg-secondary))] border-l border-[hsl(var(--border-color))] flex flex-col shrink-0">
        <div className="h-10 px-3 border-b border-[hsl(var(--border-color))] flex items-center gap-2">
          <Sparkles className="w-3.5 h-3.5 text-[hsl(var(--accent-primary))]" />
          <span className="text-[10px] text-[hsl(var(--text-secondary))] font-semibold uppercase tracking-wider">{t('home:preview.sceneEdit.ai.title')}</span>
        </div>
        <div className="flex-1 p-2.5 space-y-2 overflow-hidden">
          {/* User message */}
          <div className="flex justify-end">
            <div className="max-w-[90%] px-3 py-1.5 rounded-xl rounded-br-sm bg-[hsl(var(--accent-primary))] text-[11px] text-white">
              {t('home:preview.sceneEdit.ai.input')}
            </div>
          </div>

          {/* AI reply */}
          <div className="flex justify-start">
            <div className="max-w-[90%] px-3 py-1.5 rounded-xl rounded-bl-sm bg-[hsl(var(--bg-tertiary))] border border-[hsl(var(--border-color))] text-[11px] text-[hsl(var(--text-secondary))]">
              {t('home:preview.sceneEdit.ai.response')}
            </div>
          </div>

          {/* Tool call card */}
          <div className="rounded-lg bg-[hsl(var(--accent-primary)/0.08)] border border-[hsl(var(--accent-primary)/0.25)] p-2.5">
            <div className="flex items-center gap-2 mb-1.5">
              <div className="w-4 h-4 rounded-full bg-[hsl(var(--accent-primary))] flex items-center justify-center">
                <Edit3 className="w-2.5 h-2.5 text-white" />
              </div>
              <span className="text-[11px] font-semibold text-[hsl(var(--accent-primary))]">{t('home:preview.sceneEdit.ai.updated')}</span>
            </div>
            <div className="text-[11px] text-[hsl(var(--text-primary))] font-medium">{t('home:preview.sceneEdit.ai.updatedFile')}</div>
            <div className="text-[10px] text-[hsl(var(--text-secondary))] mt-1">{t('home:preview.sceneEdit.ai.newScenes')}</div>

            {/* Undo hint */}
            <div className="mt-2 pt-1.5 border-t border-[hsl(var(--border-color))] flex items-center gap-1">
              <History className="w-3 h-3 text-[hsl(var(--text-secondary))]" />
              <span className="text-[9px] text-[hsl(var(--text-secondary))]">{t('home:preview.sceneEdit.ai.undo')}</span>
            </div>
          </div>
        </div>

        {/* Input area */}
        <div className="p-2.5 border-t border-[hsl(var(--border-color))]">
          <div className="h-8 px-3 rounded-lg bg-[hsl(var(--bg-tertiary))] border border-[hsl(var(--border-color))] flex items-center justify-between">
            <span className="text-[11px] text-[hsl(var(--text-secondary))]">{t('home:preview.sceneEdit.ai.placeholder')}</span>
            <Mic className="w-3.5 h-3.5 text-[hsl(var(--text-secondary))]" />
          </div>
        </div>
      </div>
    </div>
  );
}
