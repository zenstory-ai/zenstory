import { useState, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { Book, Plus } from "../components/icons";
import { DashboardSearchBar } from "../components/dashboard/DashboardSearchBar";
import { DashboardEmptyState } from "../components/dashboard/DashboardEmptyState";
import { ProjectCard } from "../components/dashboard/ProjectCard";
import { PROJECT_TYPE_STYLES, SUPPORTED_PROJECT_TYPES } from "../components/dashboard/projectTypeStyles";
import { Button } from "../components/ui/Button";
import { useProject } from "../contexts/ProjectContext";
import { useProjectsProgress } from "../hooks/useProjectsProgress";
import { parseUTCDate } from "../lib/dateUtils";
import type { ProjectType } from "../types";
import { useIsMobile, useIsTablet } from "../hooks/useMediaQuery";
import { toast } from "../lib/toast";
import { handleApiError } from "../lib/errorHandler";
import { ApiError } from "../lib/apiClient";
import { DashboardPageHeader } from "../components/dashboard/DashboardPageHeader";
import { DashboardFilterPills, type FilterPillOption } from "../components/dashboard/DashboardFilterPills";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";

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
  const projectProgress = useProjectsProgress(projects.length);

  const isMobile = useIsMobile();
  const isTablet = useIsTablet();
  const showDeleteAction = isMobile || isTablet;

  const [searchQuery, setSearchQuery] = useState("");
  const [deleting, setDeleting] = useState<string | null>(null);
  const [filterType, setFilterType] = useState<ProjectType | "all">("all");

  // Filter options for DashboardFilterPills
  const filterOptions: FilterPillOption<ProjectType | "all">[] = useMemo(() => {
    return [
      { value: "all", label: t('projects.filterAll') },
      ...SUPPORTED_PROJECT_TYPES.map((type) => ({
        value: type,
        label: t(PROJECT_TYPE_STYLES[type].labelKey),
        icon: PROJECT_TYPE_STYLES[type].icon,
      })),
    ];
  }, [t]);

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
          <Button
            size={isMobile ? "touch" : "md"}
            onClick={() => navigate('/dashboard')}
            leftIcon={<Plus className="w-4 h-4" />}
          >
            {t('projects.new')}
          </Button>
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
          <Button variant="secondary" onClick={() => void refreshProjects()}>
            {t('common.retry')}
          </Button>
        </div>
      )}

      {/* Empty State */}
      {!loading && !error && filteredProjects.length === 0 && (
        <DashboardEmptyState
          icon={Book}
          title={searchQuery || filterType !== "all" ? t('projects.noMatch') : t('projects.empty')}
          description={searchQuery || filterType !== "all" ? t('projects.tryDifferent') : t('projects.emptyHintProjectsPage')}
        />
      )}

      {/* Project Cards Grid */}
      {!loading && !error && filteredProjects.length > 0 && (
        <div className={`grid ${isMobile ? "grid-cols-1" : isTablet ? "grid-cols-2" : "lg:grid-cols-3"} gap-3.5`}>
          {filteredProjects.map((project) => (
            <ProjectCard
              key={project.id}
              project={project}
              progress={project.id ? projectProgress.get(project.id) : undefined}
              onOpen={() => navigate(`/project/${project.id}`)}
              onDelete={() => setDeleting(project.id || null)}
              alwaysShowDelete={showDeleteAction}
            />
          ))}
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
