import { useTranslation } from "react-i18next";
import { Trash2 } from "../icons";
import { IconButton } from "../ui/IconButton";
import { ProjectProgressLine } from "../ProjectProgressLine";
import { RelativeTime } from "../RelativeTime";
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
 * Card type: quiet type eyebrow on top, title 14/600, description 12/400 (same at every
 * width). Progress and relative time sit together in one hairline-separated `mt-auto`
 * footer, so cards with and without a description line their footers up along the bottom
 * of a grid row.
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
    <article
      onClick={onOpen}
      className="group relative flex flex-col rounded-2xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] p-5 cursor-pointer transition-[border-color,box-shadow,background-color] duration-[var(--duration-fast)] ease-[var(--ease-standard)] hover:border-[hsl(var(--accent-primary)/0.3)] hover:shadow-sm"
      data-testid={testId}
    >
      <button
        type="button"
        onClick={(event) => { event.stopPropagation(); onOpen(); }}
        aria-label={t("dashboard:projects.openProject", { defaultValue: "打开项目「{{name}}」", name: project.name })}
        className="absolute inset-0 rounded-2xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary)/0.5)]"
      />
      <div className="relative pointer-events-none flex flex-1 flex-col">
        <div className="flex items-center justify-between gap-2">
          <span className={`inline-flex items-center gap-1.5 text-xs font-medium ${style.colorClass}`}>
            <TypeIcon className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            <span>{t(style.labelKey)}</span>
          </span>
          <IconButton
            label={t("projects.deleteProject")}
            tone="danger"
            icon={<Trash2 className="w-4 h-4" />}
            onClick={(e) => {
              e.stopPropagation();
              onDelete();
            }}
            className={`relative pointer-events-auto -mr-1.5 -my-1.5 ${
              alwaysShowDelete ? "opacity-100" : "opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 focus-visible:opacity-100"
            }`}
          />
        </div>

        <h3
          title={project.name}
          className="mt-2 line-clamp-2 text-sm font-semibold leading-snug tracking-[-0.01em] text-[hsl(var(--text-primary))]"
        >
          {project.name}
        </h3>

        {project.description && (
          <p className="mt-1.5 line-clamp-2 text-xs leading-relaxed text-[hsl(var(--text-secondary))]">
            {project.description}
          </p>
        )}

        <div
          data-testid="project-card-footer"
          className="mt-auto pt-4"
        >
          <div className="flex items-center justify-between gap-3 border-t border-[hsl(var(--separator-color)/0.55)] pt-3">
            <div className="min-w-0 flex-1">
              <ProjectProgressLine progress={progress} projectType={project.project_type} />
            </div>
            <span className="shrink-0 text-xs tabular-nums text-[hsl(var(--text-secondary))]">
              <RelativeTime value={project.updated_at} />
            </span>
          </div>
        </div>
      </div>
    </article>
  );
}
