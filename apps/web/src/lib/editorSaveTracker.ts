/**
 * Cross-page bookkeeping for editor saves.
 *
 * Leaving the editor flushes the last unsaved text in the unmount cleanup, at
 * the same moment the next page (the dashboard) mounts and asks for project
 * progress. Without coordination the progress request can be answered before
 * the save lands, and the card shows the old word count. Pages that show
 * numbers derived from file content wait for in-flight saves first and
 * refetch when a save completes.
 */

const pendingSaves = new Set<Promise<unknown>>();

export const EDITOR_CONTENT_SAVED_EVENT = "zenstory:editor-content-saved";

export interface EditorContentSavedDetail {
  projectId: string;
}

/** Registers an in-flight editor save. Returns the same promise. */
export function trackEditorSave<T>(save: Promise<T>): Promise<T> {
  pendingSaves.add(save);
  const forget = () => {
    pendingSaves.delete(save);
  };
  save.then(forget, forget);
  return save;
}

/**
 * Resolves once every editor save that is in flight right now has settled,
 * or after `timeoutMs`, whichever comes first. Never rejects.
 */
export async function waitForEditorSaves(timeoutMs = 5000): Promise<void> {
  if (pendingSaves.size === 0) return;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<void>((resolve) => {
    timer = setTimeout(resolve, timeoutMs);
  });
  try {
    await Promise.race([Promise.allSettled([...pendingSaves]), timeout]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

/** Announces that the editor stored new content for a project. */
export function notifyEditorContentSaved(projectId: string): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent<EditorContentSavedDetail>(EDITOR_CONTENT_SAVED_EVENT, {
      detail: { projectId },
    }),
  );
}
