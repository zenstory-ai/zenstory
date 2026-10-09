import { useState, useEffect, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { logger } from "../lib/logger";
import {
  Book, FileText, Clapperboard,
  Sparkles, Zap, CheckSquare, Square, ChevronRight
} from "../components/icons";
import { Modal } from "../components/ui/Modal";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { QuotaBadge } from "../components/subscription/QuotaBadge";
import { DashboardPageHeader } from "../components/dashboard/DashboardPageHeader";
import { DashboardSearchBar } from "../components/dashboard/DashboardSearchBar";
import { DashboardEmptyState } from "../components/dashboard/DashboardEmptyState";
import { ProjectCard } from "../components/dashboard/ProjectCard";
import { PROJECT_TYPE_STYLES } from "../components/dashboard/projectTypeStyles";
import { Button } from "../components/ui/Button";
import {
  DashboardInspirationSuggestions,
  FeaturedInspirationsSection,
} from "../components/inspirations";
import { projectApi } from "../lib/api";
import type { ProjectTemplate } from "../lib/api";
import { ApiError } from "../lib/apiClient";
import { handleApiError } from "../lib/errorHandler";
import { toast } from "../lib/toast";
import { useAuth } from "../contexts/AuthContext";
import { useProject } from "../contexts/ProjectContext";
import { useProjectsProgress } from "../hooks/useProjectsProgress";
import type { ProjectType } from "../types";
import { useIsMobile, useIsTablet } from "../hooks/useMediaQuery";
import { parseUTCDate } from "../lib/dateUtils";
import { UpgradePromptModal } from "../components/subscription/UpgradePromptModal";
import { IdeaQuotaWallModal } from "../components/subscription/IdeaQuotaWallModal";
import { useAiMessageQuota } from "../hooks/useAiMessageQuota";
import { readHeldDashboardIdea, useHeldDashboardIdea } from "../hooks/useHeldDashboardIdea";
import { buildUpgradeUrl, getUpgradePromptDefinition } from "../config/upgradeExperience";
import { writingStatsApi } from "../lib/writingStatsApi";
import { onboardingPersonaApi, type PersonaRecommendation } from "../lib/onboardingPersonaApi";
import { buildTodayActionPlan, type TodayActionPlanItem } from "../lib/dashboardActionPlan";
import type { ActivationGuideResponse } from "../types/writingStats";
import { dashboardOnboardingFlags } from "../config/dashboardOnboarding";
import { inspirationsConfig } from "../config/inspirations";
import { useDashboardProjectType } from "../hooks/useDashboardProjectType";

const SUPPORTED_PROJECT_TYPES: ProjectType[] = ["novel", "short", "screenplay"];

function isProjectType(value: string): value is ProjectType {
  return SUPPORTED_PROJECT_TYPES.includes(value as ProjectType);
}

export default function DashboardHome() {
  const { t, i18n } = useTranslation(['dashboard', 'home', 'common']);
  const projectQuotaUpgradePrompt = getUpgradePromptDefinition("project_quota_blocked");
  const navigate = useNavigate();
  const { user } = useAuth();
  const {
    projects,
    loading: projectsLoading,
    createProject: contextCreateProject,
    deleteProject: contextDeleteProject,
  } = useProject();
  const projectProgress = useProjectsProgress(projects.length);

  // Mobile and tablet detection
  const isMobile = useIsMobile();
  const isTablet = useIsTablet();
  const showDeleteAction = isMobile || isTablet;
  const userId = user?.id ?? null;
  const showTodayActionPlanEntry = dashboardOnboardingFlags.todayActionPlanEnabled;
  const showFirstDayActivationGuide = dashboardOnboardingFlags.firstDayActivationGuideEnabled;

  const [templates, setTemplates] = useState<Record<string, ProjectTemplate> | null>(null);
  const [creating, setCreating] = useState<ProjectType | null>(null);
  const [isQuickCreating, setIsQuickCreating] = useState(false);
  // An idea held back while today's AI messages were used up comes back after a reload.
  const [inspiration, setInspiration] = useState(() => readHeldDashboardIdea(user?.id));
  const aiMessageQuota = useAiMessageQuota();
  useHeldDashboardIdea(user?.id, inspiration, aiMessageQuota.exhausted);
  const [showIdeaQuotaModal, setShowIdeaQuotaModal] = useState(false);
  const [newProjectName, setNewProjectName] = useState("");
  // The landing page type card (or a short-story / screenwriter onboarding answer)
  // decides which tab a new author starts on.
  const { activeTab, setActiveTab, rememberCreatedType } = useDashboardProjectType(projects);
  const [pendingDeleteProjectId, setPendingDeleteProjectId] = useState<string | null>(null);
  const [deletingProject, setDeletingProject] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [showProjectQuotaUpgradeModal, setShowProjectQuotaUpgradeModal] = useState(false);
  const [activationGuide, setActivationGuide] = useState<ActivationGuideResponse | null>(null);
  const [activationGuideLoaded, setActivationGuideLoaded] = useState(false);
  const [personaRecommendations, setPersonaRecommendations] = useState<PersonaRecommendation[]>([]);
  const [executingTodayActionId, setExecutingTodayActionId] = useState<string | null>(null);
  const [todayActionPlanExpanded, setTodayActionPlanExpanded] = useState(false);

  // Dynamic project type config based on language
  const PROJECT_TYPE_CONFIG: Record<
    ProjectType,
    {
      icon: React.ComponentType<{ className?: string }>;
      labelKey: string;
      colorClass: string;
      bgClass: string;
      gradientFrom: string;
      gradientTo: string;
      placeholderKey: string;
      descriptionKey: string;
    }
  > = useMemo(() => ({
    novel: {
      icon: Book,
      labelKey: 'projectType.novel.name',
      colorClass: PROJECT_TYPE_STYLES.novel.colorClass,
      bgClass: PROJECT_TYPE_STYLES.novel.bgClass,
      gradientFrom: PROJECT_TYPE_STYLES.novel.gradientFrom,
      gradientTo: PROJECT_TYPE_STYLES.novel.gradientTo,
      placeholderKey: 'inspiration.novelPlaceholder',
      descriptionKey: 'inspiration.novelDesc',
    },
    short: {
      icon: FileText,
      labelKey: 'projectType.short.name',
      colorClass: 'text-emerald-500',
      bgClass: 'bg-emerald-500/10',
      gradientFrom: 'from-emerald-500/20',
      gradientTo: 'to-teal-500/20',
      placeholderKey: 'inspiration.shortPlaceholder',
      descriptionKey: 'inspiration.shortDesc',
    },
    screenplay: {
      icon: Clapperboard,
      labelKey: 'projectType.screenplay.name',
      colorClass: 'text-amber-500',
      bgClass: 'bg-amber-500/10',
      gradientFrom: 'from-amber-500/20',
      gradientTo: 'to-orange-500/20',
      placeholderKey: 'inspiration.screenplayPlaceholder',
      descriptionKey: 'inspiration.screenplayDesc',
    },
  }), []);

  // Helper to get translated config
  const getTranslatedConfig = (type: string | undefined) => {
    const rawType = type ?? "";
    const safeType = isProjectType(rawType) ? rawType : "novel";
    const config = PROJECT_TYPE_CONFIG[safeType];
    return {
      ...config,
      label: t(config.labelKey),
      placeholder: t(config.placeholderKey),
      description: t(config.descriptionKey),
    };
  };

  const availableProjectTypes = useMemo(() => {
    if (!templates) {
      return SUPPORTED_PROJECT_TYPES;
    }
    const available = Object.keys(templates).filter(isProjectType);
    return available.length > 0 ? available : SUPPORTED_PROJECT_TYPES;
  }, [templates]);

  // Filter and sort projects for recent display
  const recentProjects = useMemo(() => {
    return projects
      .filter((p) =>
        p.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        p.description?.toLowerCase().includes(searchQuery.toLowerCase())
      )
      .sort((a, b) => {
        const tb = parseUTCDate(b.updated_at ?? '').getTime() || 0;
        const ta = parseUTCDate(a.updated_at ?? '').getTime() || 0;
        return tb - ta;
      });
  }, [projects, searchQuery]);

  const latestProjectId = useMemo(() => {
    if (projects.length === 0) {
      return null;
    }
    return [...projects]
      .sort((a, b) => {
        const tb = parseUTCDate(b.updated_at ?? '').getTime() || 0;
        const ta = parseUTCDate(a.updated_at ?? '').getTime() || 0;
        return tb - ta;
      })[0]?.id ?? null;
  }, [projects]);

  const todayActionPlan = useMemo(
    () =>
      buildTodayActionPlan({
        activationGuide,
        personaRecommendations,
        projectsCount: projects.length,
        latestProjectId,
        activeProjectType: activeTab,
        t,
      }),
    [activationGuide, personaRecommendations, projects.length, latestProjectId, activeTab, t],
  );

  const isLikelyFirstDayUser = useMemo(() => {
    if (!user?.created_at) return false;
    const createdAtMs = parseUTCDate(user.created_at).getTime();
    if (!Number.isFinite(createdAtMs)) return false;
    const ageMs = Date.now() - createdAtMs;
    return ageMs >= 0 && ageMs <= 24 * 60 * 60 * 1000;
  }, [user?.created_at]);

  const isNewUserWithinFirstWeek = useMemo(() => {
    if (!user?.created_at) return false;
    const createdAtMs = parseUTCDate(user.created_at).getTime();
    if (!Number.isFinite(createdAtMs)) return false;
    const ageMs = Date.now() - createdAtMs;
    return ageMs >= 0 && ageMs <= 7 * 24 * 60 * 60 * 1000;
  }, [user?.created_at]);

  const shouldShowActivationGuideCard = Boolean(
    showFirstDayActivationGuide
      && activationGuide
      && activationGuide.within_first_day
      && !activationGuide.is_activated,
  );

  // Preference A: only show one onboarding module on first day.
  // If activation guide is visible, hide today-action-plan to avoid duplication.
  // Additionally: avoid "flash" (show action-plan briefly, then replace with activation-guide)
  // for brand-new users while activation guide is still loading.
  const shouldHoldTodayActionPlanUntilGuideLoaded = Boolean(
    showFirstDayActivationGuide && isLikelyFirstDayUser && !activationGuideLoaded,
  );
  const shouldShowTodayActionPlanCard = Boolean(
    showTodayActionPlanEntry
      && !projectsLoading
      && !shouldShowActivationGuideCard
      && !shouldHoldTodayActionPlanUntilGuideLoaded
      && (projects.length === 0 || isNewUserWithinFirstWeek)
      && todayActionPlan.length > 0,
  );

  const handleActivationGuideNextAction = () => {
    if (!activationGuide) {
      return;
    }

    const nextEvent = activationGuide.next_event_name;

    if (nextEvent === "project_created") {
      setCreating(activeTab);
      return;
    }

    if ((nextEvent === "first_file_saved" || nextEvent === "first_ai_action_accepted") && latestProjectId) {
      navigate(`/project/${latestProjectId}`);
      return;
    }

    if (activationGuide.next_action) {
      navigate(activationGuide.next_action);
      return;
    }

    navigate("/dashboard");
  };

  const handleExecuteTodayAction = async (actionItem: TodayActionPlanItem) => {
    if (actionItem.action.type === "navigate") {
      navigate(actionItem.action.path);
      return;
    }

    const targetProjectType = actionItem.action.projectType ?? activeTab;
    const defaultName = templates?.[targetProjectType]?.default_project_name || t('defaults.untitled');

    setExecutingTodayActionId(actionItem.id);
    try {
      const project = await contextCreateProject(defaultName, undefined, targetProjectType);
      const projectId = project.id;

      if (!projectId) {
        console.error("Create project succeeded but missing project id:", project);
        return;
      }

      rememberCreatedType(targetProjectType);
      navigate(`/project/${projectId}`);
    } catch (error) {
      if (!(error instanceof ApiError && error.status === 401)) {
        toast.error(handleApiError(error));
        if (
          error instanceof ApiError &&
          error.errorCode === "ERR_QUOTA_PROJECTS_EXCEEDED" &&
          projectQuotaUpgradePrompt.surface === "modal"
        ) {
          setShowProjectQuotaUpgradeModal(true);
        }
      }
    } finally {
      setExecutingTodayActionId((current) => (current === actionItem.id ? null : current));
    }
  };

  const handleDeleteProject = async (projectId: string) => {
    if (!projectId || deletingProject) {
      return;
    }

    setDeletingProject(true);
    try {
      await contextDeleteProject(projectId);
      setPendingDeleteProjectId(null);
    } catch (error) {
      setPendingDeleteProjectId(null);
      if (!(error instanceof ApiError && error.status === 401)) {
        toast.error(handleApiError(error));
        if (
          error instanceof ApiError &&
          error.errorCode === "ERR_QUOTA_PROJECTS_EXCEEDED" &&
          projectQuotaUpgradePrompt.surface === "modal"
        ) {
          setShowProjectQuotaUpgradeModal(true);
        }
      }
    } finally {
      setDeletingProject(false);
    }
  };

  useEffect(() => {
    let cancelled = false;

    // 先用本地 fallback 模板渲染（避免进入 Dashboard 还要再挡一层全屏 loading）
    const buildFallbackTemplates = (): Record<string, ProjectTemplate> => {
      const novelFolders = [
        { id: "lore-folder", title: t('folders.worldBuilding'), file_type: "folder", order: 0 },
        { id: "character-folder", title: t('folders.characters'), file_type: "folder", order: 1 },
        { id: "material-folder", title: t('folders.materials'), file_type: "folder", order: 2 },
        { id: "outline-folder", title: t('folders.outlines'), file_type: "folder", order: 3 },
        { id: "draft-folder", title: t('folders.drafts'), file_type: "folder", order: 4 },
      ];

      const shortFolders = [
        { id: "character-folder", title: t('folders.people'), file_type: "folder", order: 0 },
        { id: "outline-folder", title: t('folders.concept'), file_type: "folder", order: 1 },
        { id: "material-folder", title: t('folders.materials'), file_type: "folder", order: 2 },
        { id: "draft-folder", title: t('folders.drafts'), file_type: "folder", order: 3 },
      ];

      const screenplayFolders = [
        { id: "character-folder", title: t('folders.characters'), file_type: "folder", order: 0 },
        { id: "lore-folder", title: t('folders.worldBuilding'), file_type: "folder", order: 1 },
        { id: "material-folder", title: t('folders.materials'), file_type: "folder", order: 2 },
        { id: "outline-folder", title: t('folders.episodeOutlines'), file_type: "folder", order: 3 },
        { id: "script-folder", title: t('folders.scripts'), file_type: "folder", order: 4 },
      ];

      return {
        novel: {
          name: t("projectType.novel.name"),
          description: t("inspiration.novelDesc"),
          icon: "book",
          folders: novelFolders,
          file_type_mapping: {
            [t('folders.worldBuilding')]: "lore",
            [t('folders.characters')]: "character",
            [t('folders.materials')]: "snippet",
            [t('folders.outlines')]: "outline",
            [t('folders.drafts')]: "draft",
          },
          default_project_name: t('defaults.novel'),
        },
        short: {
          name: t("projectType.short.name"),
          description: t("inspiration.shortDesc"),
          icon: "file-text",
          folders: shortFolders,
          file_type_mapping: {
            [t('folders.people')]: "character",
            [t('folders.concept')]: "outline",
            [t('folders.materials')]: "snippet",
            [t('folders.drafts')]: "draft",
          },
          default_project_name: t('defaults.short'),
        },
        screenplay: {
          name: t("projectType.screenplay.name"),
          description: t("inspiration.screenplayDesc"),
          icon: "clapperboard",
          folders: screenplayFolders,
          file_type_mapping: {
            [t('folders.characters')]: "character",
            [t('folders.worldBuilding')]: "lore",
            [t('folders.materials')]: "snippet",
            [t('folders.episodeOutlines')]: "outline",
            [t('folders.scripts')]: "script",
          },
          default_project_name: t('defaults.screenplay'),
        },
      };
    };

    setTemplates(buildFallbackTemplates());

    const loadTemplates = async () => {
      try {
        // Projects are now managed by ProjectContext, just load templates
        const templatesData = await projectApi.getTemplates();
        if (!cancelled && templatesData) {
          setTemplates(templatesData);
        }
      } catch (error) {
        logger.error("Failed to load templates:", error);
      }
    };

    void loadTemplates();

    return () => {
      cancelled = true;
    };
  }, [i18n.language, t]);

  useEffect(() => {
    if (!userId || (!showFirstDayActivationGuide && !showTodayActionPlanEntry)) return;
    let cancelled = false;
    setActivationGuideLoaded(false);

    void writingStatsApi
      .getActivationGuide()
      .then((guide) => {
        if (!cancelled) {
          setActivationGuide(guide);
          setActivationGuideLoaded(true);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setActivationGuide(null);
          setActivationGuideLoaded(true);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [showFirstDayActivationGuide, showTodayActionPlanEntry, userId]);

  useEffect(() => {
    if (!userId || !showTodayActionPlanEntry) return;
    let cancelled = false;

    void onboardingPersonaApi
      .getRecommendations()
      .then((items) => {
        if (!cancelled) {
          setPersonaRecommendations(items);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setPersonaRecommendations([]);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [showTodayActionPlanEntry, userId]);

  useEffect(() => {
    if (!availableProjectTypes.includes(activeTab)) {
      setActiveTab(availableProjectTypes[0] ?? "novel");
    }
  }, [activeTab, availableProjectTypes, setActiveTab]);

  const handleCreateProject = async (useInspiration = false) => {
    if (!creating) return;

    // The idea would be the first AI message, which cannot go out today: keep it and
    // explain, instead of spending a project slot on an empty project.
    if (useInspiration && inspiration.trim() && aiMessageQuota.exhausted) {
      setCreating(null);
      setShowIdeaQuotaModal(true);
      return;
    }

    const fallbackTemplates = templates;
    const defaultName = fallbackTemplates?.[creating]?.default_project_name || t('defaults.untitled');
    const projectName = newProjectName.trim() || defaultName;

    try {
      // Use ProjectContext's createProject for unified state management
      const project = await contextCreateProject(projectName, undefined, creating);
      const projectId = project.id;

      if (!projectId) {
        logger.error("Create project succeeded but missing project id:", project);
        return;
      }

      // Store inspiration to localStorage if provided
      if (useInspiration && inspiration.trim()) {
        localStorage.setItem(
          `zenstory_inspiration_${projectId}`,
          JSON.stringify({
            content: inspiration.trim(),
            projectType: creating,
            timestamp: Date.now(),
          })
        );
      }

      rememberCreatedType(creating);

      // Clean up form state
      setCreating(null);
      setInspiration("");
      setNewProjectName("");

      navigate(`/project/${projectId}`);
    } catch (error) {
      if (!(error instanceof ApiError && error.status === 401)) {
        toast.error(handleApiError(error));
        if (
          error instanceof ApiError &&
          error.errorCode === "ERR_QUOTA_PROJECTS_EXCEEDED" &&
          projectQuotaUpgradePrompt.surface === "modal"
        ) {
          setShowProjectQuotaUpgradeModal(true);
        }
      }
    }
  };

  const handleQuickCreate = async () => {
    if (isQuickCreating) return;

    const insp = inspiration.trim();
    if (insp && aiMessageQuota.exhausted) {
      setShowIdeaQuotaModal(true);
      return;
    }

    setIsQuickCreating(true);
    const fallbackTemplates = templates;
    const defaultName = fallbackTemplates?.[activeTab]?.default_project_name || t('defaults.untitled');

    try {
      // Use ProjectContext's createProject for unified state management
      const project = await contextCreateProject(defaultName, undefined, activeTab);
      const projectId = project.id;

      if (!projectId) {
        logger.error("Create project succeeded but missing project id:", project);
        return;
      }

      // Store inspiration to localStorage for ChatPanel auto-send (if provided)
      if (insp) {
        localStorage.setItem(
          `zenstory_inspiration_${projectId}`,
          JSON.stringify({
            content: insp,
            projectType: activeTab,
            timestamp: Date.now(),
          })
        );
      }

      rememberCreatedType(activeTab);

      // Clean up form state
      setInspiration("");
      setNewProjectName("");
      setCreating(null);

      navigate(`/project/${projectId}`);
    } catch (error) {
      if (!(error instanceof ApiError && error.status === 401)) {
        toast.error(handleApiError(error));
        if (
          error instanceof ApiError &&
          error.errorCode === "ERR_QUOTA_PROJECTS_EXCEEDED" &&
          projectQuotaUpgradePrompt.surface === "modal"
        ) {
          setShowProjectQuotaUpgradeModal(true);
        }
      }
    } finally {
      setIsQuickCreating(false);
    }
  };

  // A complete example brief (premise + what to produce first) shows what a good request looks like;
  // a rule such as "enter a core conflict" reads as homework.
  const resolvedInspirationPlaceholder = t(`dashboard:inspiration.example.${activeTab}`);
  return (
    <>
      {/* Header Section */}
      <DashboardPageHeader
        title={t('hero.greeting', { name: user?.nickname || user?.username || t('hero.defaultName') })}
        subtitle={t('hero.question')}
        action={<QuotaBadge />}
      />

      {shouldShowActivationGuideCard && activationGuide && (
        <div
          className="mb-5 rounded-2xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] p-4 shadow-sm"
          data-testid="activation-guide-card"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-start gap-3">
              <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-[hsl(var(--accent-primary)/0.12)] text-[hsl(var(--accent-primary))]">
                <Zap className="h-4 w-4" />
              </div>
              <div>
                <h2 className="text-sm font-semibold text-[hsl(var(--text-primary))]">
                  {t("activationGuide.title", { defaultValue: "新手上手清单" })}
                </h2>
                <p className="mt-1 text-xs text-[hsl(var(--text-secondary))]">
                  {t("activationGuide.progress", {
                    defaultValue: "已完成 {{done}} / {{total}}",
                    done: activationGuide.completed_steps,
                    total: activationGuide.total_steps,
                  })}
                </p>
              </div>
            </div>

            <button
              type="button"
              className="btn-secondary h-8 px-3 text-xs"
              onClick={handleActivationGuideNextAction}
            >
              {t("activationGuide.nextAction", { defaultValue: "继续下一步" })}
              <ChevronRight className="h-3.5 w-3.5" />
            </button>
          </div>

          <div className="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-[hsl(var(--bg-tertiary))]">
            <div
              className="h-full rounded-full bg-[hsl(var(--accent-primary))] transition-[width] duration-300"
              style={{
                width: `${Math.min(
                  100,
                  Math.max(0, Math.round((activationGuide.completion_rate ?? 0) * 100)),
                )}%`,
              }}
            />
          </div>

          <div className="mt-3 grid gap-1">
            {activationGuide.steps.map((step) => (
              <div
                key={step.event_name}
                className="flex items-center gap-2 text-xs"
              >
                {step.completed ? (
                  <CheckSquare className="h-4 w-4 text-[hsl(var(--success))]" />
                ) : (
                  <Square className="h-4 w-4 text-[hsl(var(--text-tertiary))]" />
                )}
                <span
                  className={
                    step.completed
                      ? "text-[hsl(var(--text-secondary))]"
                      : step.event_name === activationGuide.next_event_name
                        ? "font-medium text-[hsl(var(--text-primary))]"
                        : "text-[hsl(var(--text-secondary))]"
                  }
                >
                  {t(`activationGuide.steps.${step.event_name}`, { defaultValue: step.label })}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Project Type Tabs */}
      <div className={`flex justify-center mb-5 ${isMobile ? "gap-2" : "gap-2.5"}`}>
        <div
          className={`inline-flex items-center ${isMobile ? "gap-2" : "gap-2.5"}`}
          data-tour-id="dashboard-project-type-tabs"
        >
          {availableProjectTypes.map((type) => {
            const config = getTranslatedConfig(type);
            const isActive = activeTab === type;
            return (
              <button
                key={type}
                type="button"
                onClick={() => setActiveTab(type)}
                aria-pressed={isActive}
                className={`
                  flex items-center gap-2 rounded-full font-medium transition-all border touch-target
                  ${isMobile
                    ? "px-3 py-2.5 text-xs"
                    : isTablet
                      ? "px-3.5 py-2 text-sm"
                      : "px-4 py-2 text-sm"
                  }
                  ${isActive
                    ? "bg-[hsl(var(--accent-primary)/0.15)] text-[hsl(var(--accent-primary))] border-transparent!"
                    : "text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-secondary)/0.5)] border-transparent"
                  }
                `}
              >
                <config.icon className={isMobile ? "w-3.5 h-3.5" : "w-4 h-4"} />
                {config.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* Create Project Card */}
      <div className="mb-8 rounded-[30px] border border-[hsl(var(--border-color)/0.22)] bg-[linear-gradient(180deg,hsl(var(--bg-secondary)/0.88),hsl(var(--bg-secondary)/0.82))] p-6 shadow-[0_16px_32px_hsl(0_0%_0%_/_0.18)] backdrop-blur-sm">
        <div className={`${isMobile ? "flex flex-col gap-3" : "relative"}`}>
          <textarea
            placeholder={resolvedInspirationPlaceholder}
            data-testid="dashboard-inspiration-input"
            data-tour-id="dashboard-inspiration-input"
            disabled={isQuickCreating}
            className={`w-full resize-none rounded-[24px] border border-[hsl(var(--border-color)/0.12)] bg-[linear-gradient(180deg,hsl(var(--bg-tertiary)/0.96),hsl(var(--bg-secondary)/0.99))] text-[15px] leading-7 text-[hsl(var(--text-primary))] placeholder:text-[hsl(var(--text-secondary)/0.7)] shadow-[inset_0_1px_0_hsl(0_0%_100%_/_0.015)] transition-all focus:border-[hsl(var(--accent-primary)/0.18)] focus:outline-none focus:ring-2 focus:ring-[hsl(var(--accent-primary)/0.05)] disabled:cursor-not-allowed disabled:opacity-60 ${isMobile ? "min-h-[248px] px-4 py-4" : "min-h-[136px] px-6 py-5 pr-[180px]"}`}
            value={inspiration}
            onChange={(e) => setInspiration(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                handleQuickCreate();
              }
            }}
          />
          <Button
            onClick={handleQuickCreate}
            isLoading={isQuickCreating}
            loadingText={t('common.creatingProject', { defaultValue: '正在创建项目…' })}
            size={isMobile ? "touch" : "md"}
            leftIcon={<Sparkles className="h-4 w-4" />}
            className={isMobile ? "w-full" : "absolute bottom-4 right-4 min-w-[128px]"}
            data-testid="create-project-button"
            data-tour-id="dashboard-create-project"
          >
            {t('common.createButton')}
          </Button>
        </div>

        {inspirationsConfig.enabled && (
          <DashboardInspirationSuggestions
            projectType={activeTab}
            onSelect={setInspiration}
          />
        )}
      </div>

      {/* Recent Projects Section */}
      <div className="mb-7">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <h2 className="text-base font-semibold text-[hsl(var(--text-primary))]">
              {t('projects.recent')}
            </h2>
            <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-[hsl(var(--accent-primary)/0.1)] text-[hsl(var(--accent-primary))]">
              {projects.length}
            </span>
          </div>
        </div>

        {projectsLoading ? (
          <div className={`grid ${isMobile ? "grid-cols-1" : isTablet ? "grid-cols-2" : "lg:grid-cols-3"} gap-3.5`}>
            {Array.from({ length: isMobile ? 2 : 3 }).map((_, index) => (
              <div
                key={`project-skeleton-${index}`}
                className="rounded-lg border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] p-4"
              >
                <div className="h-4 w-2/3 rounded bg-[hsl(var(--bg-tertiary))] animate-pulse mb-3" />
                <div className="h-3 w-full rounded bg-[hsl(var(--bg-tertiary))] animate-pulse mb-2" />
                <div className="h-3 w-4/5 rounded bg-[hsl(var(--bg-tertiary))] animate-pulse" />
              </div>
            ))}
          </div>
        ) : projects.length === 0 ? (
          <DashboardEmptyState
            icon={Book}
            title={t('projects.empty')}
            description={t('projects.emptyHint')}
          />
        ) : (
          <>
            {/* Search Bar (only show if more than 6 projects) */}
            {projects.length > 6 && (
              <DashboardSearchBar
                value={searchQuery}
                onChange={setSearchQuery}
                placeholder={t('projects.searchPlaceholder')}
                className="mb-5"
              />
            )}

            {/* Project Cards Grid */}
            <div className={`grid ${isMobile ? "grid-cols-1" : isTablet ? "grid-cols-2" : "lg:grid-cols-3"} gap-3.5`}>
              {recentProjects.map((project) => (
                <ProjectCard
                  key={project.id}
                  project={project}
                  progress={project.id ? projectProgress.get(project.id) : undefined}
                  onOpen={() => navigate(`/project/${project.id}`)}
                  onDelete={() => {
                    if (project.id) {
                      setPendingDeleteProjectId(project.id);
                    }
                  }}
                  alwaysShowDelete={showDeleteAction}
                  data-testid="project-card"
                />
              ))}
            </div>
          </>
        )}
      </div>

      {shouldShowTodayActionPlanCard && (
        <div
          className="mb-7 rounded-2xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] p-4 shadow-sm"
          data-testid="today-action-plan-card"
        >
          <div className="flex items-start justify-between gap-3">
            <div className="flex items-start gap-2">
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-[hsl(var(--accent-primary)/0.12)] text-[hsl(var(--accent-primary))]">
                <Sparkles className="h-4 w-4" />
              </div>
              <div className="min-w-0">
                <h2 className="text-sm font-semibold text-[hsl(var(--text-primary))]">
                  {t("todayActionPlan.title", { defaultValue: "今天可以做的事" })}
                </h2>
                {todayActionPlanExpanded && (
                  <p className="mt-1 text-xs text-[hsl(var(--text-secondary))]">
                    {t("todayActionPlan.subtitle", {
                      defaultValue: "根据你的写作进度推荐。",
                    })}
                  </p>
                )}
              </div>
            </div>

            <button
              type="button"
              className="btn-ghost h-8 px-2 text-xs"
              onClick={() => {
                setTodayActionPlanExpanded((current) => !current);
              }}
              data-testid="today-action-plan-toggle"
            >
              {todayActionPlanExpanded
                ? t("todayActionPlan.collapse", { defaultValue: "收起" })
                : t("todayActionPlan.expand", { defaultValue: "查看全部" })}
            </button>
          </div>

          <div className="mt-3 grid gap-2">
            {todayActionPlan
              .slice(0, todayActionPlanExpanded ? 3 : 1)
              .map((item, index) => (
                <div
                  key={item.id}
                  className="rounded-lg border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-tertiary))] px-3 py-2"
                  data-testid={`today-action-item-${index + 1}`}
                >
                  <div className="flex items-start gap-3">
                    <div className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-[hsl(var(--accent-primary)/0.18)] text-[10px] font-semibold text-[hsl(var(--accent-primary))]">
                      {index + 1}
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="text-xs font-medium text-[hsl(var(--text-primary))]">{item.title}</p>
                      <p className="mt-1 text-xs text-[hsl(var(--text-secondary))]">{item.description}</p>
                    </div>
                    <button
                      type="button"
                      className="shrink-0 rounded-md border border-[hsl(var(--accent-primary)/0.35)] px-2.5 py-1 text-xs font-medium text-[hsl(var(--accent-primary))] hover:bg-[hsl(var(--accent-primary)/0.10)] disabled:cursor-not-allowed disabled:opacity-60 focus:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.45)] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-primary))]"
                      onClick={() => {
                        void handleExecuteTodayAction(item);
                      }}
                      disabled={executingTodayActionId === item.id}
                      data-testid={`today-action-execute-${index + 1}`}
                    >
                      {executingTodayActionId === item.id
                        ? t("todayActionPlan.executing", { defaultValue: "处理中..." })
                        : item.ctaLabel}
                    </button>
                  </div>
                </div>
              ))}
          </div>
        </div>
      )}

      {inspirationsConfig.enabled && (
        <FeaturedInspirationsSection isMobile={isMobile} isTablet={isTablet} />
      )}

      {/* Create Modal */}
      <Modal
        open={!!creating && !!templates}
        onClose={() => {
          setCreating(null);
          setInspiration("");
          setNewProjectName("");
        }}
        size="md"
        showCloseButton={isMobile ? false : true}
      >
        {creating && templates && (() => {
          const config = getTranslatedConfig(creating);
          return (
            <>
              {/* Mobile Header */}
              {isMobile && (
                <div className="flex items-center justify-between mb-4 pb-3 border-b border-[hsl(var(--border-color))]">
                  <h2 className="text-lg font-semibold text-[hsl(var(--text-primary))]">
                    {t('projects.createNew')}
                  </h2>
                  <button
                    onClick={() => {
                      setCreating(null);
                      setInspiration("");
                      setNewProjectName("");
                    }}
                    className="p-2 rounded-lg text-[hsl(var(--text-secondary))] hover:bg-[hsl(var(--bg-tertiary))] active:bg-[hsl(var(--bg-hover))] transition-all"
                  >
                    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                    </svg>
                  </button>
                </div>
              )}

              <div className={`flex items-center gap-4 ${isMobile ? "mb-4" : "mb-6"}`}>
                <div
                  className={`${isMobile ? "w-12 h-12" : "w-14 h-14"} rounded-2xl ${config.bgClass} flex items-center justify-center shadow-lg`}
                >
                  <config.icon className={`${isMobile ? "w-5 h-5" : "w-6 h-6"} ${config.colorClass}`} />
                </div>
                <div>
                  <h3 className={`${isMobile ? "text-lg" : "text-xl"} font-semibold text-[hsl(var(--text-primary))]`}>
                    {templates[creating].name}
                  </h3>
                  <p className={`text-[hsl(var(--text-secondary))] ${isMobile ? "text-xs" : "text-sm"}`}>
                    {templates[creating].description}
                  </p>
                </div>
              </div>

              {/* Inspiration Preview */}
              {inspiration.trim() && (
                <div className={`p-3 bg-[hsl(var(--bg-tertiary))] rounded-lg ${isMobile ? "mb-3" : "mb-4"}`}>
                  <div className="text-xs text-[hsl(var(--text-secondary))] mb-1">{t('inspiration.title')}</div>
                  <div className="text-sm text-[hsl(var(--text-primary))] line-clamp-2">
                    {inspiration}
                  </div>
                </div>
              )}

              <div className={`${isMobile ? "mb-4" : "mb-6"}`}>
                <label
                  htmlFor="dashboard-new-project-name"
                  className={`block font-medium text-[hsl(var(--text-secondary))] ${isMobile ? "text-xs mb-1.5" : "text-sm mb-2"}`}
                >
                  {t('projects.nameLabel')}
                </label>
                <input
                  id="dashboard-new-project-name"
                  type="text"
                  value={newProjectName}
                  onChange={(e) => setNewProjectName(e.target.value)}
                  placeholder={t('projects.namePlaceholder', {
                    name: templates[creating].default_project_name || t('defaults.untitled'),
                  })}
                  className="input"
                  autoFocus
                  onKeyDown={(e) => {
                    if (e.key === "Enter") handleCreateProject(true);
                    if (e.key === "Escape") {
                      setCreating(null);
                      setInspiration("");
                      setNewProjectName("");
                    }
                  }}
                />
              </div>

              <div className={`flex gap-3 ${isMobile ? "fixed bottom-0 left-0 right-0 p-4 bg-[hsl(var(--bg-secondary))] border-t border-[hsl(var(--border-color))] mobile-safe-bottom" : ""}`}>
                <button onClick={() => {
                  setCreating(null);
                  setInspiration("");
                  setNewProjectName("");
                }} className={`btn-ghost ${isMobile ? "flex-1 h-12" : "flex-1 h-11"}`}>
                  {t('projects.cancel')}
                </button>
                <button
                  onClick={() => handleCreateProject(true)}
                  className={`btn-primary flex items-center justify-center gap-2 ${isMobile ? "flex-1 h-12" : "flex-1 h-11"}`}
                >
                  <Sparkles className="w-4 h-4" />
                  {t('common.createButton')}
                </button>
              </div>
            </>
          );
        })()}
      </Modal>

      <ConfirmDialog
        open={pendingDeleteProjectId !== null}
        onClose={() => {
          if (!deletingProject) setPendingDeleteProjectId(null);
        }}
        onConfirm={() => {
          if (pendingDeleteProjectId) {
            return handleDeleteProject(pendingDeleteProjectId);
          }
        }}
        title={t('projects.deleteProject')}
        message={t('projects.deleteConfirm')}
        variant="danger"
        loading={deletingProject}
        confirmLabel={t('common:delete')}
        cancelLabel={t('common:cancel')}
      />

      <IdeaQuotaWallModal
        open={showIdeaQuotaModal}
        onClose={() => setShowIdeaQuotaModal(false)}
        resetAt={aiMessageQuota.resetAt}
      />

      <UpgradePromptModal
        open={showProjectQuotaUpgradeModal}
        onClose={() => setShowProjectQuotaUpgradeModal(false)}
        source={projectQuotaUpgradePrompt.source}
        primaryDestination="billing"
        secondaryDestination="pricing"
        title={t('projects.quotaExceededTitle', {
          defaultValue: '项目数已达上限',
        })}
        description={t('projects.quotaExceededDesc', {
          defaultValue: '当前套餐的项目数已用完。升级 Pro 可以建更多项目，也可以先删除不再需要的项目。',
        })}
        primaryLabel={t('dashboard:billing.ctaUpgradePro', '开通 Pro')}
        onPrimary={() => {
          window.location.assign(
            buildUpgradeUrl(projectQuotaUpgradePrompt.billingPath, projectQuotaUpgradePrompt.source)
          );
        }}
        secondaryLabel={t('home:pricingTeaser.viewPricing', '查看套餐权益')}
        onSecondary={() => {
          window.location.assign(
            buildUpgradeUrl(projectQuotaUpgradePrompt.pricingPath, projectQuotaUpgradePrompt.source)
          );
        }}
      />
    </>
  );
}
