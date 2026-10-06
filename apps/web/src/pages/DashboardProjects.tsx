import { useState, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import {
  Book, FileText, Clapperboard, Clock, Trash2, Plus
} from "../components/icons";
import { DashboardSearchBar } from "../components/dashboard/DashboardSearchBar";
import { useProject } from "../contexts/ProjectContext";
import { formatRelativeTime, parseUTCDate } from "../lib/dateUtils";
import type { ProjectType } from "../types";
import { useIsMobile, useIsTablet } from "../hooks/useMediaQuery";
import { toast } from "../lib/toast";
import { handleApiError } from "../lib/errorHandler";
import { ApiError } from "../lib/apiClient";
import { DashboardPageHeader } from "../components/dashboard/DashboardPageHeader";
import { DashboardFilterPills, type FilterPillOption } from "../components/dashboard/DashboardFilterPills";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";

const SUPPORTED_PROJECT_TYPES: ProjectType[] = ["novel", "short", "screenplay"];

function isProjectType(value: string): value is ProjectType {
  return SUPPORTED_PROJECT_TYPES.includes(value as ProjectType);
}

export default function DashboardProjects() {
  const { t } = useTranslation(['dashboard']);
  const navigate = useNavigate();
  const {
    projects,
    loading,
    error,
    refreshProjects,
    deleteProject: contextDeleteProject,
  } = useProject();

  const isMobile = useIsMobile();
  const isTablet = useIsTablet();
  const showDeleteAction = isMobile || isTablet;

  const [searchQuery, setSearchQuery] = useState("");
  const [deleting, setDeleting] = useState<string | null>(null);
  const [filterType, setFilterType] = useState<ProjectType | "all">("all");

  // Project type config
  const PROJECT_TYPE_CONFIG: Record<
    ProjectType,
    {
      icon: React.ComponentType<{ className?: string }>;
      labelKey: string;
      colorClass: string;
      bgClass: string;
      gradientFrom: string;
      gradientTo: string;
    }
  > = useMemo(() => ({
    novel: {
      icon: Book,
      labelKey: 'projectType.novel.name',
      colorClass: 'text-[hsl(var(--text-secondary))]',
      bgClass: 'bg-white/5',
      gradientFrom: 'from-white/5',
      gradientTo: 'to-white/0',
    },
    short: {
      icon: FileText,
      labelKey: 'projectType.short.name',
      colorClass: 'text-emerald-500',
      bgClass: 'bg-emerald-500/10',
      gradientFrom: 'from-emerald-500/20',
      gradientTo: 'to-teal-500/20',
    },
    screenplay: {
      icon: Clapperboard,
      labelKey: 'projectType.screenplay.name',
      colorClass: 'text-amber-500',
      bgClass: 'bg-amber-500/10',
      gradientFrom: 'from-amber-500/20',
      gradientTo: 'to-orange-500/20',
    },
  }), []);

  const getTranslatedConfig = (type: string | undefined) => {
    const rawType = type ?? "";
    const safeType = isProjectType(rawType) ? rawType : "novel";
    const config = PROJECT_TYPE_CONFIG[safeType];
    return {
      ...config,
      label: t(config.labelKey),
    };
  };

  // Filter options for DashboardFilterPills
  const filterOptions: FilterPillOption<ProjectType | "all">[] = useMemo(() => {
    return [
      { value: "all", label: t('projects.filterAll') },
      ...SUPPORTED_PROJECT_TYPES.map((type) => ({
        value: type,
        label: t(PROJECT_TYPE_CONFIG[type].labelKey),
        icon: PROJECT_TYPE_CONFIG[type].icon,
      })),
    ];
  }, [PROJECT_TYPE_CONFIG, t]);

  const handleDeleteProject = async (projectId: string) => {
    try {
      await contextDeleteProject(projectId);
      setDeleting(null);
    } catch (error) {
      setDeleting(null);
      if (!(error instanceof ApiError && error.status === 401)) {
        toast.error(handleApiError(error));
      }
    }
  };

  // Filter and sort projects
  const filteredProjects = projects
    .filter((p) => {
      const matchesSearch =
        p.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        p.description?.toLowerCase().includes(searchQuery.toLowerCase());
      const matchesType = filterType === "all" || p.project_type === filterType;
      return matchesSearch && matchesType;
    })
    .sort((a, b) => {
      const tb = parseUTCDate(b.updated_at ?? '').getTime() || 0;
      const ta = parseUTCDate(a.updated_at ?? '').getTime() || 0;
      return tb - ta;
    });

  return (
    <>
      {/* Header */}
      <DashboardPageHeader
        title={t('projects.all')}
        action={
          <button
            onClick={() => navigate('/dashboard')}
            className="btn-primary flex items-center gap-2"
          >
            <Plus className="w-4 h-4" />
            <span className={isMobile ? "hidden" : ""}>{t('projects.new')}</span>
          </button>
        }
      />

      {/* Search and Filter */}
      {!loading && !error && <div className={`flex ${isMobile ? "flex-col gap-3" : "items-center gap-4"} mb-6`}>
        <DashboardSearchBar
          value={searchQuery}
          onChange={setSearchQuery}
          placeholder={t('projects.searchPlaceholder')}
          className="flex-1"
        />
        <DashboardFilterPills
          options={filterOptions}
          value={filterType}
          onChange={setFilterType}
        />
      </div>}

      {/* Projects Count */}
      {!loading && !error && <div className="text-sm text-[hsl(var(--text-secondary))] mb-4">
        {t('projects.count', { count: filteredProjects.length })}
      </div>}

      {loading && (
        <div data-testid="projects-loading" aria-label={t('common.loading')} className="grid gap-3.5 lg:grid-cols-3">
          {[0, 1, 2].map((item) => (
            <div key={item} className="h-32 animate-pulse rounded-lg bg-[hsl(var(--bg-secondary))]" />
          ))}
        </div>
      )}

      {!loading && error && (
        <div role="alert" className="rounded-lg border border-[hsl(var(--error)/0.3)] bg-[hsl(var(--error)/0.08)] p-6 text-center">
          <p className="mb-4 text-[hsl(var(--text-primary))]">{error}</p>
          <button type="button" className="btn-primary" onClick={() => void refreshProjects()}>
            {t('common.retry')}
          </button>
        </div>
      )}

      {/* Empty State */}
      {!loading && !error && filteredProjects.length === 0 && (
        <div className={`text-center ${isMobile ? "py-16" : "py-20"}`}>
          <div className={`rounded-2xl flex items-center justify-center mx-auto mb-5 shadow-lg bg-[hsl(var(--bg-secondary))] ${isMobile ? "w-16 h-16" : "w-20 h-20"}`}>
            <Book className={`${isMobile ? "w-8 h-8" : "w-10 h-10"} text-[hsl(var(--text-secondary))]`} />
          </div>
          <h3 className={`${isMobile ? "text-lg" : "text-xl"} font-semibold mb-2 text-[hsl(var(--text-primary))]`}>
            {searchQuery || filterType !== "all" ? t('projects.noMatch') : t('projects.empty')}
          </h3>
          <p className={`${isMobile ? "text-sm" : "text-base"} text-[hsl(var(--text-secondary))]`}>
            {searchQuery || filterType !== "all" ? t('projects.tryDifferent') : t('projects.emptyHint')}
          </p>
        </div>
      )}

      {/* Project Cards Grid */}
      {!loading && !error && filteredProjects.length > 0 && (
        <div className={`grid ${isMobile ? "grid-cols-1" : isTablet ? "grid-cols-2" : "lg:grid-cols-3"} gap-3.5`}>
          {filteredProjects.map((project) => {
            const config = getTranslatedConfig(project.project_type);

            return (
              <div
                key={project.id}
                onClick={() => navigate(`/project/${project.id}`)}
                tabIndex={0}
                role="button"
                aria-label={`Open project ${project.name}`}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    navigate(`/project/${project.id}`);
                  }
                }}
                className={`group relative flex flex-col bg-[hsl(var(--bg-secondary))] rounded-lg border border-[hsl(var(--border-color))] cursor-pointer hover:border-[hsl(var(--accent-primary)/0.3)] hover:shadow-lg transition-all focus:outline-none focus:ring-2 focus:ring-[hsl(var(--accent-primary)/0.5)] ${isMobile ? "p-4" : "p-4"}`}
              >
                {/* Gradient Overlay */}
                <div
                  className={`absolute inset-0 rounded-lg bg-gradient-to-br ${config.gradientFrom} ${config.gradientTo} opacity-0 group-hover:opacity-100 transition-opacity`}
                />

                <div className="relative flex flex-1 flex-col">
                  {/* Header */}
                  <div className="flex items-start justify-between mb-2">
                    <div
                      className={`w-9 h-9 rounded-lg ${config.bgClass} flex items-center justify-center group-hover:scale-110 transition-transform`}
                    >
                      <config.icon className={`w-4.5 h-4.5 ${config.colorClass}`} />
                    </div>
                    <div className="flex-1 min-w-0 ml-3">
                      <h3 className={`font-semibold text-[hsl(var(--text-primary))] truncate leading-snug ${isMobile ? "text-base" : "text-sm"}`}>
                        {project.name}
                      </h3>
                    </div>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        setDeleting(project.id || null);
                      }}
                      className={`p-1.5 rounded-lg hover:bg-[hsl(var(--error)/0.1)] text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--error))] transition-all shrink-0 ${
                        showDeleteAction
                          ? "opacity-100"
                          : "opacity-0 group-hover:opacity-100 group-focus-within:opacity-100"
                      }`}
                      title={t('projects.deleteProject')}
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>

                  {/* Content */}
                  {project.description && (
                    <p className={`${isMobile ? "text-sm" : "text-xs"} text-[hsl(var(--text-secondary))] line-clamp-2 mb-3`}>
                      {project.description}
                    </p>
                  )}

                  {/* Footer */}
                  <div className="mt-auto flex items-center justify-between">
                    <span
                      className={`text-xs px-2 py-0.5 rounded-md ${config.bgClass} ${config.colorClass} font-medium`}
                    >
                      {config.label}
                    </span>
                    <div className="flex items-center gap-1 text-xs text-[hsl(var(--text-secondary))]">
                      <Clock className="w-3 h-3" />
                      {project.updated_at ? formatRelativeTime(project.updated_at) : '-'}
                    </div>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Delete Confirmation Dialog */}
      <ConfirmDialog
        open={!!deleting}
        onClose={() => setDeleting(null)}
        onConfirm={() => {
          if (deleting) {
            return handleDeleteProject(deleting);
          }
        }}
        title={t('projects.confirmDeleteTitle')}
        message={t('projects.confirmDeleteMessage')}
        variant="danger"
        confirmLabel={t('projects.confirmDeleteButton')}
        cancelLabel={t('projects.cancel')}
      />
    </>
  );
}
