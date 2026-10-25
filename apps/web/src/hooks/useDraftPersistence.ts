import { useState, useCallback, useEffect } from 'react';
import { logger } from "../lib/logger";

/**
 * Storage structure for persisting chat drafts.
 * Organized by project ID, then by chat session ID.
 */
interface DraftStore {
  [projectId: string]: {
    [chatSessionId: string]: string;
  };
}

/** LocalStorage key for persisting chat drafts */
const DRAFT_STORAGE_KEY = 'zenstory_chat_drafts';

/**
 * Load draft content from localStorage for a given project.
 *
 * Retrieves the most recently saved draft for the specified project.
 * Falls back to the first available session draft if 'default' is not found.
 *
 * @param projectId - The ID of the project to load drafts for, or null
 * @returns The draft content string, or empty string if not found
 */
function loadDraftFromStorage(projectId: string | null): string {
  if (!projectId) return '';

  try {
    const stored = localStorage.getItem(DRAFT_STORAGE_KEY);
    if (stored) {
      const drafts: DraftStore = JSON.parse(stored);
      const projectDrafts = drafts[projectId];
      if (projectDrafts) {
        const sessionIds = Object.keys(projectDrafts);
        if (sessionIds.length > 0) {
          return projectDrafts['default'] || projectDrafts[sessionIds[0]] || '';
        }
      }
    }
  } catch (e) {
    logger.warn('Failed to load draft:', e);
  }
  return '';
}

/**
 * Append a line to another project's persisted draft (the panel may already show a
 * different project, so its in-memory draft cannot be used).
 *
 * @returns Whether the draft was stored
 */
export function appendDraftForProject(projectId: string, text: string): boolean {
  try {
    const stored = localStorage.getItem(DRAFT_STORAGE_KEY);
    const drafts: DraftStore = stored ? JSON.parse(stored) : {};
    const current = loadDraftFromStorage(projectId);
    drafts[projectId] = { ...drafts[projectId], default: current.trim() ? `${current.trimEnd()}\n${text}` : text };
    localStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify(drafts));
    return true;
  } catch (e) {
    logger.warn('Failed to save draft:', e);
    return false;
  }
}

/**
 * Return type for the useDraftPersistence hook.
 */
export interface DraftPersistenceResult {
  /** Current draft content */
  draft: string;
  /** Keep the draft in memory; return whether persistence also succeeded. */
  saveDraft: (content: string) => boolean;
  /** Clear the draft for the current project */
  clearDraft: () => void;
}

/**
 * Hook to persist chat draft messages across navigation.
 *
 * Uses localStorage to persist drafts between sessions. Drafts are
 * automatically synced when the project context changes.
 *
 * Note: We intentionally sync state in an effect when projectId changes.
 * This is necessary because draft persistence is tied to project context.
 *
 * @param projectId - The ID of the current project, or null if no project is selected
 * @returns Object containing draft state and management functions
 *
 * @example
 * ```tsx
 * function ChatInput({ projectId }: { projectId: string | null }) {
 *   const { draft, saveDraft, clearDraft } = useDraftPersistence(projectId);
 *
 *   const handleSubmit = () => {
 *     sendMessage(draft);
 *     clearDraft();
 *   };
 *
 *   return (
 *     <textarea
 *       value={draft}
 *       onChange={(e) => saveDraft(e.target.value)}
 *     />
 *   );
 * }
 * ```
 */
export function useDraftPersistence(projectId: string | null): DraftPersistenceResult {
  const [draft, setDraft] = useState<string>(() => loadDraftFromStorage(projectId));

  // Sync draft when project changes - this is intentional behavior
  useEffect(() => {
    setDraft(loadDraftFromStorage(projectId));
  }, [projectId]);

  /**
   * Save draft content to localStorage.
   * Updates both local state and persists to storage.
   *
   * @param content - The draft content to save
   */
  const saveDraft = useCallback((content: string) => {
    setDraft(content);

    if (!projectId) return false;

    try {
      const stored = localStorage.getItem(DRAFT_STORAGE_KEY);
      const drafts: DraftStore = stored ? JSON.parse(stored) : {};

      if (!drafts[projectId]) {
        drafts[projectId] = {};
      }
      drafts[projectId]['default'] = content;

      localStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify(drafts));
      return true;
    } catch (e) {
      logger.warn('Failed to save draft:', e);
      return false;
    }
  }, [projectId]);

  /**
   * Clear the draft for the current project.
   * Removes the 'default' session draft from localStorage.
   */
  const clearDraft = useCallback(() => {
    setDraft('');

    if (!projectId) return;

    try {
      const stored = localStorage.getItem(DRAFT_STORAGE_KEY);
      if (stored) {
        const drafts: DraftStore = JSON.parse(stored);
        if (drafts[projectId]) {
          delete drafts[projectId]['default'];
          localStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify(drafts));
        }
      }
    } catch (e) {
      logger.warn('Failed to clear draft:', e);
    }
  }, [projectId]);

  return {
    draft,
    saveDraft,
    clearDraft,
  };
}
