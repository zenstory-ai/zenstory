import { useEffect, useState } from "react";
import { projectApi } from "../lib/api";
import { EDITOR_CONTENT_SAVED_EVENT, waitForEditorSaves } from "../lib/editorSaveTracker";
import { logger } from "../lib/logger";
import type { ProjectProgress } from "../types";

/**
 * Writing progress for the author's project cards, keyed by project id.
 *
 * Fetched on mount, when the number of projects changes, and whenever the
 * editor finishes saving content. Before each fetch it waits for editor saves
 * that are still in flight (leaving the editor flushes the last edit as the
 * dashboard mounts), so the cards never show the count from before that save.
 * A failure just leaves the cards without progress.
 */
export function useProjectsProgress(projectCount: number): ReadonlyMap<string, ProjectProgress> {
  const [progress, setProgress] = useState<ReadonlyMap<string, ProjectProgress>>(() => new Map());
  const [savedVersion, setSavedVersion] = useState(0);

  useEffect(() => {
    const onSaved = () => setSavedVersion((version) => version + 1);
    window.addEventListener(EDITOR_CONTENT_SAVED_EVENT, onSaved);
    return () => window.removeEventListener(EDITOR_CONTENT_SAVED_EVENT, onSaved);
  }, []);

  useEffect(() => {
    if (projectCount === 0) return;
    let cancelled = false;
    waitForEditorSaves()
      .then(() => (cancelled ? null : projectApi.getProgress()))
      .then((items) => {
        if (cancelled || !Array.isArray(items)) return;
        setProgress(new Map(items.map((item) => [item.project_id, item])));
      })
      .catch((error) => logger.warn("Failed to load project progress:", error));
    return () => {
      cancelled = true;
    };
  }, [projectCount, savedVersion]);

  return progress;
}
