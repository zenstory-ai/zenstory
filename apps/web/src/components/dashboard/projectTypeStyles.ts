import { Book, Clapperboard, FileText } from "../icons";
import type { ProjectType } from "../../types";

export interface ProjectTypeStyle {
  icon: React.ComponentType<{ className?: string }>;
  labelKey: string;
  /** Icon / badge text color. */
  colorClass: string;
  /** Icon tile / badge background. */
  bgClass: string;
  gradientFrom: string;
  gradientTo: string;
}

/**
 * One look per project type wherever a project is shown (home, project list).
 * Long-form novels use the accent tint: a translucent white tint vanished on the light theme.
 */
export const PROJECT_TYPE_STYLES: Record<ProjectType, ProjectTypeStyle> = {
  novel: {
    icon: Book,
    labelKey: "projectType.novel.name",
    colorClass: "text-[hsl(var(--accent-primary))]",
    bgClass: "bg-[hsl(var(--accent-primary)/0.1)]",
    gradientFrom: "from-[hsl(var(--accent-primary)/0.08)]",
    gradientTo: "to-transparent",
  },
  short: {
    icon: FileText,
    labelKey: "projectType.short.name",
    colorClass: "text-emerald-500",
    bgClass: "bg-emerald-500/10",
    gradientFrom: "from-emerald-500/20",
    gradientTo: "to-teal-500/20",
  },
  screenplay: {
    icon: Clapperboard,
    labelKey: "projectType.screenplay.name",
    colorClass: "text-amber-500",
    bgClass: "bg-amber-500/10",
    gradientFrom: "from-amber-500/20",
    gradientTo: "to-orange-500/20",
  },
};

export const SUPPORTED_PROJECT_TYPES: ProjectType[] = ["novel", "short", "screenplay"];

export function isProjectType(value: string): value is ProjectType {
  return SUPPORTED_PROJECT_TYPES.includes(value as ProjectType);
}

export function getProjectTypeStyle(type: string | undefined): ProjectTypeStyle {
  const raw = type ?? "";
  return PROJECT_TYPE_STYLES[isProjectType(raw) ? raw : "novel"];
}
