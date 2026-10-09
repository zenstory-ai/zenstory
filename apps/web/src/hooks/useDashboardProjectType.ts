import { useCallback, useMemo, useState } from "react";
import type { Project, ProjectType } from "../types";
import { parseUTCDate } from "../lib/dateUtils";
import {
  getPreferredProjectType,
  isPreferableProjectType,
  setPreferredProjectType,
} from "../lib/preferredProjectType";

type Source = "preference" | "default" | "user";

function latestProjectType(projects: readonly Project[]): ProjectType | null {
  let latest: { type: ProjectType; time: number } | null = null;
  for (const project of projects) {
    if (!isPreferableProjectType(project.project_type)) continue;
    const time = project.updated_at ? parseUTCDate(project.updated_at).getTime() : 0;
    const comparable = Number.isFinite(time) ? time : 0;
    if (!latest || comparable > latest.time) {
      latest = { type: project.project_type, time: comparable };
    }
  }
  return latest?.type ?? null;
}

/**
 * Which project type the dashboard creation tabs open on.
 *
 * 1. A stored preference (landing-page card, onboarding persona, or the type
 *    of the last project created on this device).
 * 2. Otherwise the type of the author's most recently active project, so a
 *    screenwriter signing in on a new device does not land on 长篇小说.
 * 3. Otherwise 长篇小说.
 *
 * Once the author picks a tab, that choice wins for the rest of the visit.
 * `rememberCreatedType` keeps the type of a project that was just created as
 * the next default instead of clearing it.
 */
export function useDashboardProjectType(projects: readonly Project[]) {
  const [choice, setChoice] = useState<{ type: ProjectType; source: Source }>(() => {
    const preferred = getPreferredProjectType();
    return preferred ? { type: preferred, source: "preference" } : { type: "novel", source: "default" };
  });
  const recentType = useMemo(() => latestProjectType(projects), [projects]);

  const activeTab: ProjectType = choice.source === "default" && recentType ? recentType : choice.type;

  const setActiveTab = useCallback((type: ProjectType) => {
    setChoice({ type, source: "user" });
  }, []);

  const rememberCreatedType = useCallback((type: ProjectType) => {
    setPreferredProjectType(type);
  }, []);

  return { activeTab, setActiveTab, rememberCreatedType };
}
