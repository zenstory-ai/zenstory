import { useTranslation } from "react-i18next";
import { Clock, Trash2 } from "../icons";
import { IconButton } from "../ui/IconButton";
import { ProjectProgressLine } from "../ProjectProgressLine";
import { formatRelativeTime } from "../../lib/dateUtils";
import { getProjectTypeStyle } from "./projectTypeStyles";
import type { Project, ProjectProgress } from "../../types";

export interface ProjectCardProps {
  project: Project;
  progress: ProjectProgress | undefined;
  onOpen: () => void;
  onDelete: () => void;
  /** Phones and tablets have no hover, so the delete button stays visible there. */
  alwaysShowDelete: boolean;
  "data-testid"?: string;
}

/**
 * The project card on the dashboard home and on "我的项目".
 *
 * Card type: title 14/600, description 12/400 (same at every width). Progress and the
 * type/time row sit together in one `mt-auto` footer, so cards with and without a
 * description line their footers up along the bottom of a grid row.
 */
export function ProjectCard({
  project,
  progress,
  onOpen,
  onDelete,
  alwaysShowDelete,
  "data-testid": testId,
}: ProjectCardProps) {
  const { t } = useTranslation(["dashboard"]);
  const style = getProjectTypeStyle(project.project_type);
  const TypeIcon = style.icon;

  return (
    <div
      onClick={onOpen}
      tabIndex={0}
      role="button"
      aria-label={t("dashboard:projects.openProject", { defaultValue: "打开项目「{{name}}」", name: project.name })}
      onKeyDown={(e) => {
        // Only the card itself: Enter on the delete button must not also open the project.
        if (e.key === "Enter" && e.target === e.currentTarget) {
          onOpen();
        }
      }}
      className="group relative flex flex-col rounded-lg border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] p-4 cursor-pointer transition-all hover:border-[hsl(var(--accent-primary)/0.3)] hover:shadow-lg focus:outline-none focus:ring-2 focus:ring-[hsl(var(--accent-primary)/0.5)]"
      data-testid={testId}
    >
      <div
        className={`absolute inset-0 rounded-lg bg-gradient-to-br ${style.gradientFrom} ${style.gradientTo} opacity-0 transition-opacity group-hover:opacity-100`}
      />

      <div className="relative flex flex-1 flex-col">
        <div className="mb-2 flex items-start justify-between">
          <div
            className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${style.bgClass} transition-transform group-hover:scale-110`}
          >
            <TypeIcon className={`w-4.5 h-4.5 ${style.colorClass}`} />
          </div>
          <div className="ml-3 min-w-0 flex-1 self-center">
            <h3 className="truncate text-sm font-semibold leading-snug text-[hsl(var(--text-primary))]">
              {project.name}
            </h3>
          </div>
          <IconButton
            label={t("projects.deleteProject")}
            tone="danger"
            icon={<Trash2 className="w-4 h-4" />}
            onClick={(e) => {
              e.stopPropagation();
              onDelete();
            }}
            className={`-mr-1 -mt-1 ${
              alwaysShowDelete ? "opacity-100" : "opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 focus-visible:opacity-100"
            }`}
          />
        </div>

        {project.description && (
          <p className="mb-3 line-clamp-2 text-xs text-[hsl(var(--text-secondary))]">
            {project.description}
          </p>
        )}

        <div data-testid="project-card-footer" className="mt-auto">
          <ProjectProgressLine progress={progress} projectType={project.project_type} />
          <div className="flex items-center justify-between">
            <span className={`rounded-md px-2 py-0.5 text-xs font-medium ${style.bgClass} ${style.colorClass}`}>
              {t(style.labelKey)}
            </span>
            <div className="flex items-center gap-1 text-xs text-[hsl(var(--text-secondary))]">
              <Clock className="w-3 h-3" />
              {project.updated_at ? formatRelativeTime(project.updated_at) : "-"}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
