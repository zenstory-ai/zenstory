import { useCallback, useMemo, useState } from "react";
import type { Project, ProjectType } from "../types";
import { parseUTCDate } from "../lib/dateUtils";
import { useAuth } from "../contexts/AuthContext";
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
 * 1. The current account's preference (claimed public entry, onboarding
 *    persona, or the type of its last created project).
 * 2. Otherwise the type of the author's most recently active project, so a
 *    screenwriter signing in on a new device does not land on 长篇小说.
 * 3. Otherwise `fallbackType` (the type the server gave the auto-created first
 *    work, which follows the onboarding persona even on a device that never saw it).
 * 4. Otherwise 长篇小说. `hasChoice` is false in this case only, so a caller can
 *    leave the type to the server instead of sending a guess.
 *
 * Once the author picks a tab, that choice wins for the rest of the visit.
 * `rememberCreatedType` keeps the type of a project that was just created as
 * the next default instead of clearing it.
 */
export function useDashboardProjectType(projects: readonly Project[], fallbackType: ProjectType | null = null) {
  const { user } = useAuth();
  const userId = user?.id;
  const [choice, setChoice] = useState<{ type: ProjectType; source: Source }>(() => {
    const preferred = getPreferredProjectType(userId);
    return preferred ? { type: preferred, source: "preference" } : { type: "novel", source: "default" };
  });
  const recentType = useMemo(() => latestProjectType(projects), [projects]);

  const derived = choice.source === "default" ? recentType ?? (isPreferableProjectType(fallbackType) ? fallbackType : null) : null;
  const activeTab: ProjectType = derived ?? choice.type;
  const hasChoice = choice.source !== "default" || derived !== null;

  const setActiveTab = useCallback((type: ProjectType) => {
    setChoice({ type, source: "user" });
  }, []);

  const rememberCreatedType = useCallback((type: ProjectType) => {
    setPreferredProjectType(type, userId);
  }, [userId]);

  return { activeTab, hasChoice, setActiveTab, rememberCreatedType };
}
