import { useState, useEffect, useMemo, useRef } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { logger } from "../lib/logger";
import { Book, FileText, Clapperboard, Sparkles } from "../components/icons";
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
import { inspirationsConfig } from "../config/inspirations";
import { FirstWorkEntry } from "../components/dashboard/FirstWorkEntry";
import { useDashboardProjectType } from "../hooks/useDashboardProjectType";
import { MAX_AGENT_MESSAGE_CHARS } from "../lib/agentLimits";
import { DRAFT_IMPORT_ACCEPT, useDraftImport } from "../hooks/useDraftImport";
import { requestChatOnOpen } from "../lib/pendingInspiration";
import { Upload } from "lucide-react";
import { AnnouncementBanner } from "../components/announcements/AnnouncementBanner";

const SUPPORTED_PROJECT_TYPES: ProjectType[] = ["novel", "short", "screenplay"];

function isProjectType(value: string): value is ProjectType {
  return SUPPORTED_PROJECT_TYPES.includes(value as ProjectType);
}

export default function DashboardHome() {
  const { t, i18n } = useTranslation(['dashboard', 'home', 'common', 'chat']);
  const projectQuotaUpgradePrompt = getUpgradePromptDefinition("project_quota_blocked");
  const navigate = useNavigate();
  const { user } = useAuth();
  const {
    projects,
    loading: projectsLoading,
    createProject: contextCreateProject,
    deleteProject: contextDeleteProject,
    refreshProjects,
  } = useProject();
  const projectProgress = useProjectsProgress(projects.length);

  // Mobile and tablet detection
  const isMobile = useIsMobile();
  const isTablet = useIsTablet();
  const showDeleteAction = isMobile || isTablet;
  const userId = user?.id ?? null;

  const [templates, setTemplates] = useState<Record<string, ProjectTemplate> | null>(null);
  const [isQuickCreating, setIsQuickCreating] = useState(false);
  const draftInputRef = useRef<HTMLInputElement>(null);
  const { importFiles: importDrafts, isImporting: isImportingDrafts } = useDraftImport();
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);
  // An idea held back while today's AI messages were used up comes back after a reload.
  const [inspiration, setInspiration] = useState(() => readHeldDashboardIdea(user?.id));
  const inspirationTooLong = inspiration.length > MAX_AGENT_MESSAGE_CHARS;
  const aiMessageQuota = useAiMessageQuota();
  useHeldDashboardIdea(user?.id, inspiration, aiMessageQuota.exhausted);
  const [showIdeaQuotaModal, setShowIdeaQuotaModal] = useState(false);
  // The landing page type card (or a short-story / screenwriter onboarding answer)
  // decides which tab a new author starts on.
  const { activeTab, setActiveTab, rememberCreatedType } = useDashboardProjectType(projects);
  const [pendingDeleteProjectId, setPendingDeleteProjectId] = useState<string | null>(null);
  const [deletingProject, setDeletingProject] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [showProjectQuotaUpgradeModal, setShowProjectQuotaUpgradeModal] = useState(false);

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
    if (!availableProjectTypes.includes(activeTab)) {
      setActiveTab(availableProjectTypes[0] ?? "novel");
    }
  }, [activeTab, availableProjectTypes, setActiveTab]);

  /**
   * 「已有稿子？导入」: a new work of the selected type named after the file, its chapters
   * imported (no model call), opened on chapter 1. Picking happens first, so cancelling the
   * file picker creates nothing.
   */
  const handleImportDrafts = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const picked = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (picked.length === 0 || isQuickCreating || isImportingDrafts) return;
    setIsQuickCreating(true);
    const stem = picked[0].name.replace(/\.(txt|md)$/i, "").trim().slice(0, 40);
    const name = stem || templates?.[activeTab]?.default_project_name || t('defaults.untitled');
    try {
      const project = await contextCreateProject(name, undefined, activeTab);
      if (!project.id) {
        logger.error("Create project succeeded but missing project id:", project);
        return;
      }
      rememberCreatedType(activeTab);
      const { files: created } = await importDrafts(project.id, picked, "dashboard");
      // The author already went elsewhere: do not pull them back to the new work.
      if (!mountedRef.current) return;
      // A failed upload still opens the new work, where the empty state offers the import again.
      navigate(created.length ? `/project/${project.id}?file=${encodeURIComponent(created[0].id)}` : `/project/${project.id}`);
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

  const handleQuickCreate = async () => {
    if (isQuickCreating || inspirationTooLong) return;

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

      // No idea: phones open the new work on the AI tab, where its starter cards are.
      if (!insp && isMobile) requestChatOnOpen(projectId);

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

  // One example premise, marked 「例如：」 so it never reads as text already filled in; the first
  // reply writes an opening from even a vague idea, so it needs no instructions about what to produce.
  const resolvedInspirationPlaceholder = t(`dashboard:inspiration.example.${activeTab}`);
  return (
    <>
      <section className="dashboard-start" aria-label={t("inspiration.title")}>
      {userId && <AnnouncementBanner accountId={userId} />}
      {/* Header Section */}
      <DashboardPageHeader
        title={t('hero.greeting', { name: user?.nickname || user?.username || t('hero.defaultName') })}
        subtitle={t('hero.question')}
        action={<QuotaBadge />}
      />

      <FirstWorkEntry key={userId} userId={userId} preferredType={activeTab} onCreated={refreshProjects} />

      {/* One surface owns the idea, its format and the creation action. */}
      <div className="dashboard-composer mb-10" data-testid="dashboard-composer">
          <textarea
            placeholder={resolvedInspirationPlaceholder}
            data-testid="dashboard-inspiration-input"
            disabled={isQuickCreating}
            aria-label={t('inspiration.title')}
            aria-invalid={inspirationTooLong}
            aria-describedby={inspirationTooLong ? 'dashboard-inspiration-too-long' : undefined}
            className="dashboard-idea w-full resize-none bg-transparent text-base leading-7 text-[hsl(var(--text-primary))] placeholder:text-[hsl(var(--text-secondary))] disabled:cursor-not-allowed disabled:opacity-60"
            value={inspiration}
            onChange={(e) => setInspiration(e.target.value)}
            onKeyDown={(e) => {
              // Phones: the keyboard's return key adds a line instead of creating a half-written work.
              if (!isMobile && e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing && e.nativeEvent.keyCode !== 229) {
                e.preventDefault();
                handleQuickCreate();
              }
            }}
          />
          {inspirationTooLong && (
            <p id="dashboard-inspiration-too-long" role="alert" className="px-1 pb-3 text-sm text-[hsl(var(--error-text))]">
              {t('chat:input.tooLong', { count: inspiration.length, max: MAX_AGENT_MESSAGE_CHARS })}
            </p>
          )}
        <div className="dashboard-composer-footer">
        <div className="flex flex-wrap items-center gap-1" data-testid="dashboard-project-type-tabs">
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
                  dashboard-type-button flex items-center gap-2 rounded-lg font-medium transition-colors border-transparent! border touch-target
                  ${isMobile
                    ? "px-2.5 py-2 text-xs"
                    : isTablet
                      ? "px-3 py-2 text-sm"
                      : "px-3 py-2 text-sm"
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
          <Button
            onClick={handleQuickCreate}
            isLoading={isQuickCreating}
            disabled={inspirationTooLong}
            loadingText={t('common.creatingProject', { defaultValue: '正在创建项目…' })}
            size={isMobile ? "touch" : "md"}
            leftIcon={<Sparkles className="h-4 w-4" />}
            className="dashboard-create-action shrink-0 min-w-[128px]"
            data-testid="create-project-button"
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
        {/* Authors who arrive with a manuscript start from it instead of an idea. */}
        <input
          ref={draftInputRef}
          type="file"
          accept={DRAFT_IMPORT_ACCEPT}
          multiple
          className="hidden"
          aria-label={t('importDrafts')}
          data-testid="dashboard-import-input"
          onChange={(event) => { void handleImportDrafts(event); }}
        />
        <button
          type="button"
          data-testid="dashboard-import-drafts"
          onClick={() => draftInputRef.current?.click()}
          disabled={isQuickCreating || isImportingDrafts}
          className="mt-1 inline-flex min-h-[44px] md:min-h-0 md:py-1 items-center gap-1.5 rounded-md px-1 text-xs text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] disabled:opacity-50"
        >
          <Upload className="h-3.5 w-3.5 shrink-0" />
          {t('importDrafts')}
        </button>
      </div>

      </section>

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

      {inspirationsConfig.enabled && (
        <FeaturedInspirationsSection isMobile={isMobile} isTablet={isTablet} />
      )}

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
