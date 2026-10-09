import { useEffect, useState } from "react";
import { projectApi } from "../lib/api";
import { logger } from "../lib/logger";
import type { ProjectProgress } from "../types";

/**
 * Writing progress for the author's project cards, keyed by project id. Refetched
 * when the number of projects changes; a failure just leaves the cards without it.
 */
export function useProjectsProgress(projectCount: number): ReadonlyMap<string, ProjectProgress> {
  const [progress, setProgress] = useState<ReadonlyMap<string, ProjectProgress>>(() => new Map());

  useEffect(() => {
    if (projectCount === 0) return;
    let cancelled = false;
    Promise.resolve()
      .then(() => projectApi.getProgress())
      .then((items) => {
        if (cancelled || !Array.isArray(items)) return;
        setProgress(new Map(items.map((item) => [item.project_id, item])));
      })
      .catch((error) => logger.warn("Failed to load project progress:", error));
    return () => {
      cancelled = true;
    };
  }, [projectCount]);

  return progress;
}
