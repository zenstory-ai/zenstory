import React, { createContext, useContext, useState, useEffect, useCallback, useRef, useMemo } from 'react';
import type { ReactNode } from 'react';
import type { Project, SelectedItem, ProjectType, DiffReviewState } from '../types';
import { projectApi } from '../lib/api';
import { useAuth } from './AuthContext';
import { applyPendingEditsToDiffs, buildParagraphReviewData } from '../lib/diffReview';
import { logger } from '../lib/logger';
import { trackEvent } from '../lib/analytics';

const STORAGE_KEY_PREFIX = 'zenstory_current_project_id';
const STREAMING_CONTENT_FLUSH_MS = 40;

function getProjectStorageKey(userId?: string | null): string {
  return userId ? `${STORAGE_KEY_PREFIX}:${userId}` : STORAGE_KEY_PREFIX;
}

function toMillis(ts?: string): number {
  const n = ts ? new Date(ts).getTime() : 0;
  return Number.isFinite(n) ? n : 0;
}

function pickMostRecentlyUpdated(projects: ProjectWithId[]): ProjectWithId {
  return projects.reduce((best, p) => {
    const bestTs = Math.max(toMillis(best.updated_at), toMillis(best.created_at));
    const pTs = Math.max(toMillis(p.updated_at), toMillis(p.created_at));
    return pTs > bestTs ? p : best;
  }, projects[0]);
}

type ProjectWithId = Project & { id: string };

export interface ProjectContextType {
  projects: Project[];
  currentProject: Project | null;
  currentProjectId: string | null;
  setCurrentProjectId: (id: string | null) => void;
  loading: boolean;
  error: string | null;
  selectedItem: SelectedItem | null;
  setSelectedItem: (item: SelectedItem | null) => void;
  switchProject: (projectId: string) => void;
  createProject: (name: string, description?: string, projectType?: ProjectType) => Promise<Project>;
  updateProject: (projectId: string, updates: Partial<Project>) => Promise<Project>;
  deleteProject: (projectId: string) => Promise<void>;
  refreshProjects: () => Promise<void>;
  /**
   * Re-read one project and patch it into `projects` in place. Unlike
   * `refreshProjects` it never touches `loading` (ProjectEditor swaps the whole
   * workspace for a page loader while that is true) and never re-picks the
   * current project, so it is safe to call mid-chat, e.g. after the agent
   * renamed the project. Failures are logged and leave the list unchanged.
   */
  refreshProject: (projectId: string) => Promise<void>;
  fileTreeVersion: number;
  triggerFileTreeRefresh: () => void;
  // Editor refresh state (for edit_file updates)
  editorRefreshVersion: number;
  triggerEditorRefresh: (fileId: string) => void;
  lastEditedFileId: string | null;
  /**
   * File the AI is editing right now (between `file_edit_start` and
   * `file_edit_end`). The editor must suspend its debounced auto-save for this
   * file while it is set — otherwise a snapshot taken before the AI's edit
   * lands gets PUT afterwards and silently overwrites it.
   */
  aiEditingFileId: string | null;
  setAiEditingFileId: (fileId: string | null) => void;
  // File streaming state
  streamingFileId: string | null;
  streamingContent: string;
  appendFileContent: (fileId: string, chunk: string) => void;
  finishFileStreaming: (fileId: string) => void;
  startFileStreaming: (fileId: string) => void;
  // Diff review state (for Cursor-style inline diff)
  diffReviewState: DiffReviewState | null;
  enterDiffReview: (fileId: string, originalContent: string, newContent: string) => void;
  acceptEdit: (editId: string) => void;
  rejectEdit: (editId: string) => void;
  resetEdit: (editId: string) => void;
  acceptAllEdits: () => void;
  rejectAllEdits: () => void;
  exitDiffReview: () => void;
  applyDiffReviewChanges: () => string; // Returns final content after applying accepted/rejected edits
}

const ProjectContext = createContext<ProjectContextType | undefined>(undefined);

export const ProjectProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const { user } = useAuth();
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectsOwnerId, setProjectsOwnerId] = useState<string | null>(null);
  const projectsOwnerIdRef = useRef<string | null>(null);
  const authUserIdRef = useRef<string | null>(user?.id ?? null);
  const loadGenerationRef = useRef(0);
  authUserIdRef.current = user?.id ?? null;
  const [currentProjectId, setCurrentProjectIdState] = useState<string | null>(null);
  // 与 currentProjectId 同步的 ref：用于在同一批更新里判断「项目是否真的换了」。
  const currentProjectIdRef = useRef<string | null>(null);
  const [selectedItem, setSelectedItem] = useState<SelectedItem | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [fileTreeVersion, setFileTreeVersion] = useState(0);
  
  // Editor refresh state (for edit_file updates)
  const [editorRefreshVersion, setEditorRefreshVersion] = useState(0);
  const [lastEditedFileId, setLastEditedFileId] = useState<string | null>(null);
  // AI 正在编辑的文件（file_edit_start ~ file_edit_end 之间）
  const [aiEditingFileId, setAiEditingFileId] = useState<string | null>(null);

  // File streaming state (for AI-generated content)
  const [streamingFileId, setStreamingFileId] = useState<string | null>(null);
  const [streamingContent, setStreamingContent] = useState('');
  const streamingContentBufferRef = useRef('');
  const streamingContentFlushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Diff review state (for Cursor-style inline diff)
  const [diffReviewState, setDiffReviewState] = useState<DiffReviewState | null>(null);

  // Use a ref to avoid event-order / state-update races:
  // SSE chunks can arrive before React state updates are visible to callbacks.
  const streamingFileIdRef = useRef<string | null>(null);
  useEffect(() => {
    streamingFileIdRef.current = streamingFileId;
  }, [streamingFileId]);

  // 兜住那些绕过 setCurrentProjectId 直接调 setCurrentProjectIdState 的路径
  // （登出清理、loadProjects 的无用户分支），保证 ref 不会漂移。
  useEffect(() => {
    currentProjectIdRef.current = currentProjectId;
  }, [currentProjectId]);

  useEffect(() => {
    return () => {
      if (streamingContentFlushTimerRef.current) {
        clearTimeout(streamingContentFlushTimerRef.current);
      }
    };
  }, []);

  const scheduleStreamingContentFlush = useCallback(() => {
    if (streamingContentFlushTimerRef.current) {
      return;
    }
    streamingContentFlushTimerRef.current = setTimeout(() => {
      streamingContentFlushTimerRef.current = null;
      setStreamingContent(streamingContentBufferRef.current);
    }, STREAMING_CONTENT_FLUSH_MS);
  }, []);

  const resetStreamingContent = useCallback(() => {
    if (streamingContentFlushTimerRef.current) {
      clearTimeout(streamingContentFlushTimerRef.current);
      streamingContentFlushTimerRef.current = null;
    }
    streamingContentBufferRef.current = '';
    setStreamingContent('');
  }, []);

  // Trigger file tree refresh (increment version to signal FileTree to reload)
  const triggerFileTreeRefresh = useCallback(() => {
    setFileTreeVersion(v => v + 1);
  }, []);

  // Trigger editor refresh (called when file is edited by AI)
  const triggerEditorRefresh = useCallback((fileId: string) => {
    setLastEditedFileId(fileId);
    setEditorRefreshVersion(v => v + 1);
  }, []);

  // Start file streaming (called when file_created event received)
  const startFileStreaming = useCallback((fileId: string) => {
    const current = streamingFileIdRef.current;

    // If we already started streaming for this file (e.g. content arrived first), don't reset.
    if (current === fileId) {
      logger.log(`[Stream] Stream already active for file ${fileId}`);
      return;
    }

    // If there's already an active stream for another file, warn.
    if (current && current !== fileId) {
      logger.warn(`[Stream Safety] Switching stream from ${current} to ${fileId}`);
    }

    streamingFileIdRef.current = fileId;
    logger.log(`[Stream] Starting stream for file ${fileId}`);
    setStreamingFileId(fileId);
    resetStreamingContent();
  }, [resetStreamingContent]);

  // Append content chunk to streaming file
  const appendFileContent = useCallback((fileId: string, chunk: string) => {
    const current = streamingFileIdRef.current;

    // Handle event-order / state-update race:
    // It's possible to receive file_content before file_created sets the stream.
    // IMPORTANT: update the ref synchronously so multiple fast chunks don't overwrite.
    if (current === null) {
      logger.warn(`[Stream] Received content for ${fileId} while no active stream; auto-starting`);
      streamingFileIdRef.current = fileId;
      setStreamingFileId(fileId);
      streamingContentBufferRef.current += chunk;
      scheduleStreamingContentFlush();
      return;
    }

    // Normal case: append only to the currently streaming file
    if (fileId === current) {
      streamingContentBufferRef.current += chunk;
      scheduleStreamingContentFlush();
      return;
    }

    // Different fileId: ignore to prevent cross-file contamination
    logger.warn(`[Stream] Ignoring content for ${fileId}, currently streaming to ${current}`);
  }, [scheduleStreamingContentFlush]);

  // Finish file streaming - clears streaming state
  const finishFileStreaming = useCallback((fileId: string): void => {
    const current = streamingFileIdRef.current;
    logger.log(`[Stream] Finishing stream for file ${fileId}`);

    // Only clear when the end event matches the active stream.
    if (current !== fileId) {
      logger.warn(`[Stream] Ignoring finish for ${fileId}, currently streaming to ${current}`);
      return;
    }

    streamingFileIdRef.current = null;
    setStreamingFileId(null);
    resetStreamingContent();
  }, [resetStreamingContent]);

  // Enter diff review mode - compute diffs and create pending edits
  const enterDiffReview = useCallback((fileId: string, originalContent: string, newContent: string) => {
    const { pendingEdits } = buildParagraphReviewData(originalContent, newContent);
    
    logger.log(`[DiffReview] Entering review mode for file ${fileId} with ${pendingEdits.length} edits`);
    
    setDiffReviewState({
      isReviewing: true,
      fileId,
      originalContent,
      modifiedContent: newContent,
      pendingEdits,
    });
  }, []);

  // Accept a single edit
  const acceptEdit = useCallback((editId: string) => {
    setDiffReviewState(prev => {
      if (!prev) return null;
      return {
        ...prev,
        pendingEdits: prev.pendingEdits.map(edit =>
          edit.id === editId ? { ...edit, status: 'accepted' as const } : edit
        ),
      };
    });
  }, []);

  // Reject a single edit
  const rejectEdit = useCallback((editId: string) => {
    setDiffReviewState(prev => {
      if (!prev) return null;
      return {
        ...prev,
        pendingEdits: prev.pendingEdits.map(edit =>
          edit.id === editId ? { ...edit, status: 'rejected' as const } : edit
        ),
      };
    });
  }, []);

  // Reset a single edit back to pending
  const resetEdit = useCallback((editId: string) => {
    setDiffReviewState(prev => {
      if (!prev) return null;
      return {
        ...prev,
        pendingEdits: prev.pendingEdits.map(edit =>
          edit.id === editId ? { ...edit, status: 'pending' as const } : edit
        ),
      };
    });
  }, []);

  // Accept all pending edits
  const acceptAllEdits = useCallback(() => {
    setDiffReviewState(prev => {
      if (!prev) return null;
      return {
        ...prev,
        pendingEdits: prev.pendingEdits.map(edit => ({ ...edit, status: 'accepted' as const })),
      };
    });
  }, []);

  // Reject all pending edits
  const rejectAllEdits = useCallback(() => {
    setDiffReviewState(prev => {
      if (!prev) return null;
      return {
        ...prev,
        pendingEdits: prev.pendingEdits.map(edit => ({ ...edit, status: 'rejected' as const })),
      };
    });
  }, []);

  // Exit diff review mode
  const exitDiffReview = useCallback(() => {
    logger.log('[DiffReview] Exiting review mode');
    setDiffReviewState(null);
  }, []);

  // Apply diff review changes and return the final content
  // This reconstructs the text based on accepted/rejected edits
  // Note: pending edits are treated as "accepted" (default behavior when finishing review)
  const applyDiffReviewChanges = useCallback((): string => {
    if (!diffReviewState) return '';

    const { originalContent, pendingEdits } = diffReviewState;
    const { diffs } = buildParagraphReviewData(
      originalContent,
      diffReviewState.modifiedContent
    );

    return applyPendingEditsToDiffs(diffs, pendingEdits);
  }, [diffReviewState]);

  // Derive current project from projects list
  const identityMatchesProjects = projectsOwnerId === (user?.id ?? null);
  const visibleProjects = useMemo(
    () => identityMatchesProjects ? projects : [],
    [identityMatchesProjects, projects],
  );
  const visibleCurrentProjectId = identityMatchesProjects ? currentProjectId : null;
  const currentProject = visibleProjects.find(p => p.id === visibleCurrentProjectId) || null;

  const setCurrentProjectId = useCallback((projectId: string | null) => {
    // 同步维护 ref：loadProjects 会在同一批更新里多次调用本函数，
    // 依赖 state 判断「项目是否真的变了」会读到过期值。
    const previousProjectId = currentProjectIdRef.current;
    currentProjectIdRef.current = projectId;

    setCurrentProjectIdState(projectId);

    // 真正切换项目时必须释放上一个项目遗留的流式写入态。
    // 旧流已被 useAgentStream 的项目切换 effect 直接 abort，不会再有
    // file_content_end / done，也就不会再有人来调 finishFileStreaming；
    // 若不在这里兜底清掉，那个文件的编辑器会一直被锁成只读并显示半截缓冲。
    //
    // 注意必须先比对 previousProjectId：loadProjects 在初始化时会用当前 id
    // 再调一次本函数，无差别重置会把正在进行的流式写入误伤掉。
    if (previousProjectId !== projectId) {
      setSelectedItem(null);
      streamingFileIdRef.current = null;
      setStreamingFileId(null);
      resetStreamingContent();
      // The old stream is aborted on project change, so its file_edit_end
      // callback will never arrive. Do not let that stale ID suspend saves in
      // another project (or when the user later returns).
      setAiEditingFileId(null);
    }

    const storageKey = getProjectStorageKey(user?.id);
    if (projectId) {
      localStorage.setItem(storageKey, projectId);
      if (storageKey !== STORAGE_KEY_PREFIX) {
        localStorage.removeItem(STORAGE_KEY_PREFIX);
      }
    } else {
      localStorage.removeItem(storageKey);
      localStorage.removeItem(STORAGE_KEY_PREFIX);
    }
  }, [resetStreamingContent, user]);

  // Load projects when user is authenticated
  const loadProjects = useCallback(async () => {
    if (!user) {
      loadGenerationRef.current += 1;
      projectsOwnerIdRef.current = null;
      setProjectsOwnerId(null);
      setProjects([]);
      setCurrentProjectIdState(null);
      setLoading(false);
      return;
    }

    const requestedUserId = user.id;
    const generation = ++loadGenerationRef.current;
    const ownsRequest = () => (
      loadGenerationRef.current === generation &&
      authUserIdRef.current === requestedUserId
    );

    if (projectsOwnerIdRef.current !== requestedUserId) {
      projectsOwnerIdRef.current = requestedUserId;
      setProjectsOwnerId(requestedUserId);
      setProjects([]);
      setCurrentProjectIdState(null);
      currentProjectIdRef.current = null;
      setSelectedItem(null);
    }

    setLoading(true);
    setError(null);

    try {
      const projectList = await projectApi.getAll();
      if (!ownsRequest()) return;

      // Filter out any invalid projects (defensive programming)
      const validProjects = (projectList || []).filter(
        (p): p is ProjectWithId =>
          p != null &&
          typeof p.id === 'string' &&
          p.id.length > 0 &&
          typeof p.name === 'string'
      );

      // Admin support: when the route forces a specific projectId (e.g. `/project/:id`)
      // that isn't returned by the "my projects" list, we still want the editor to load
      // it for superusers. Otherwise the ProjectEditor guard treats it as not found.
      //
      // NOTE: We intentionally do NOT expand the full project list for admins here;
      // we only hydrate the currently-selected projectId.
      const routeChosenId = currentProjectIdRef.current;
      const shouldHydrateRouteProject =
        Boolean(user?.is_superuser) &&
        typeof routeChosenId === 'string' &&
        routeChosenId.length > 0 &&
        !validProjects.some((p) => p.id === routeChosenId);

      if (shouldHydrateRouteProject) {
        try {
          const hydrated = await projectApi.get(routeChosenId);
          if (!ownsRequest()) return;
          if (
            hydrated &&
            typeof hydrated.id === 'string' &&
            hydrated.id.length > 0 &&
            typeof hydrated.name === 'string'
          ) {
            validProjects.push(hydrated as ProjectWithId);
          }
        } catch (err) {
          logger.warn('[ProjectContext] Failed to hydrate route project for admin', {
            projectId: routeChosenId,
            error: err instanceof Error ? err.message : String(err),
          });
        }
      }

      // Set projects list
      if (!ownsRequest()) return;
      setProjects(validProjects);

      // Choose current project with correct priority:
      // 1) If route already set a currentProjectId, keep it if it exists.
      // 2) Else restore last used project from localStorage.
      // 3) Else pick most recently updated.
      if (validProjects.length > 0) {
        const routeChosenExists =
          typeof routeChosenId === 'string' &&
          routeChosenId.length > 0 &&
          validProjects.some((p) => p.id === routeChosenId);

        if (routeChosenExists) {
          // Keep currentProjectId untouched to avoid overwriting URL-selected project.
        } else {
          const storageKey = getProjectStorageKey(user?.id);

          // Backward compatible: read legacy key if needed.
          const savedProjectId =
            localStorage.getItem(storageKey) ?? localStorage.getItem(STORAGE_KEY_PREFIX);

          const savedExists =
            typeof savedProjectId === 'string' &&
            savedProjectId.length > 0 &&
            validProjects.some((p) => p.id === savedProjectId);

          if (savedExists) {
            setCurrentProjectId(savedProjectId);
          } else {
            const mostRecentProject = pickMostRecentlyUpdated(validProjects);
            setCurrentProjectId(mostRecentProject.id);
          }
        }
      } else {
        setCurrentProjectId(null);
      }

    } catch (err) {
      if (!ownsRequest()) return;
      logger.error('Failed to load projects:', err);
      setError(err instanceof Error ? err.message : 'Failed to load projects');
    } finally {
      if (ownsRequest()) setLoading(false);
    }
  }, [user, setCurrentProjectId]);

  // Load projects when user changes
  // Use user as direct dependency to ensure immediate reload on login/register
  useEffect(() => {
    if (user) {
      // User logged in or registered - load projects
      loadProjects();
    } else {
      // User logged out - clear project data
      loadGenerationRef.current += 1;
      projectsOwnerIdRef.current = null;
      setProjectsOwnerId(null);
      setProjects([]);
      setCurrentProjectIdState(null);
      currentProjectIdRef.current = null;
      setSelectedItem(null);
      setAiEditingFileId(null);
      setLoading(false);
    }
  }, [user, loadProjects]);

  const switchProject = useCallback((projectId: string) => {
    const projectExists = visibleProjects.some(p => p.id === projectId);
    if (projectExists) {
      setCurrentProjectId(projectId);
    } else {
      logger.error(`Project ${projectId} not found`);
    }
  }, [visibleProjects, setCurrentProjectId]);

  const createProject = useCallback(async (name: string, description?: string, projectType?: ProjectType): Promise<Project> => {
    const newProject = await projectApi.create({ 
      name, 
      description,
      project_type: projectType || 'novel'
    });
    
    // Validate the returned project
    if (!newProject || typeof newProject.name !== 'string') {
      throw new Error('Invalid project data returned from server');
    }
    
    setProjects(prev => [...prev, newProject]);
    
    // Automatically switch to the new project
    if (newProject.id) {
      setCurrentProjectId(newProject.id);
    }

    trackEvent('project_created', {
      project_id: newProject.id,
      project_type: newProject.project_type ?? projectType ?? 'novel',
    });
    
    return newProject;
  }, [setCurrentProjectId]);

  const updateProject = useCallback(async (projectId: string, updates: Partial<Project>): Promise<Project> => {
    const updatedProject = await projectApi.update(projectId, updates);
    setProjects(prev => prev.map(p => p.id === projectId ? updatedProject : p));
    return updatedProject;
  }, []);

  const deleteProject = useCallback(async (projectId: string): Promise<void> => {
    await projectApi.delete(projectId);
    
    setProjects(prev => {
      const newProjects = prev.filter(p => p.id !== projectId);
      const remainingProjects = newProjects as ProjectWithId[];

      // If we deleted the current project, switch to most recently updated one.
      if (currentProjectId === projectId && remainingProjects.length > 0) {
        const mostRecentProject = pickMostRecentlyUpdated(remainingProjects);
        setCurrentProjectId(mostRecentProject.id);
      } else if (remainingProjects.length === 0) {
        // No projects left, clear state (user will be redirected to dashboard)
        setCurrentProjectId(null);
      }
      
      return newProjects;
    });
  }, [currentProjectId, setCurrentProjectId]);

  const refreshProjects = useCallback(async () => {
    await loadProjects();
  }, [loadProjects]);

  const refreshProject = useCallback(async (projectId: string): Promise<void> => {
    const requestedUserId = authUserIdRef.current;
    if (!requestedUserId || !projectId) return;
    try {
      const fresh = await projectApi.get(projectId);
      // The account may have switched while the request was in flight.
      if (
        authUserIdRef.current !== requestedUserId ||
        projectsOwnerIdRef.current !== requestedUserId
      ) {
        return;
      }
      if (!fresh || fresh.id !== projectId || typeof fresh.name !== 'string') return;
      setProjects(prev => (
        prev.some(p => p.id === projectId)
          ? prev.map(p => (p.id === projectId ? { ...p, ...fresh } : p))
          : prev
      ));
    } catch (err) {
      logger.warn('[ProjectContext] Failed to refresh project', {
        projectId,
        error: err instanceof Error ? err.message : String(err),
      });
    }
  }, []);

  const contextValue = useMemo<ProjectContextType>(() => ({
    projects: visibleProjects,
    currentProject,
    currentProjectId: visibleCurrentProjectId,
    setCurrentProjectId,
    loading: identityMatchesProjects ? loading : Boolean(user),
    error: identityMatchesProjects ? error : null,
    selectedItem: identityMatchesProjects ? selectedItem : null,
    setSelectedItem,
    switchProject,
    createProject,
    updateProject,
    deleteProject,
    refreshProjects,
    refreshProject,
    fileTreeVersion,
    triggerFileTreeRefresh,
    editorRefreshVersion,
    triggerEditorRefresh,
    lastEditedFileId,
    aiEditingFileId: identityMatchesProjects ? aiEditingFileId : null,
    setAiEditingFileId,
    streamingFileId: identityMatchesProjects ? streamingFileId : null,
    streamingContent: identityMatchesProjects ? streamingContent : '',
    appendFileContent,
    finishFileStreaming,
    startFileStreaming,
    diffReviewState: identityMatchesProjects ? diffReviewState : null,
    enterDiffReview,
    acceptEdit,
    rejectEdit,
    resetEdit,
    acceptAllEdits,
    rejectAllEdits,
    exitDiffReview,
    applyDiffReviewChanges,
  }), [
    visibleProjects,
    currentProject,
    visibleCurrentProjectId,
    setCurrentProjectId,
    loading,
    identityMatchesProjects,
    user,
    error,
    selectedItem,
    setSelectedItem,
    switchProject,
    createProject,
    updateProject,
    deleteProject,
    refreshProjects,
    refreshProject,
    fileTreeVersion,
    triggerFileTreeRefresh,
    editorRefreshVersion,
    triggerEditorRefresh,
    lastEditedFileId,
    aiEditingFileId,
    setAiEditingFileId,
    streamingFileId,
    streamingContent,
    appendFileContent,
    finishFileStreaming,
    startFileStreaming,
    diffReviewState,
    enterDiffReview,
    acceptEdit,
    rejectEdit,
    resetEdit,
    acceptAllEdits,
    rejectAllEdits,
    exitDiffReview,
    applyDiffReviewChanges,
  ]);

  return (
    <ProjectContext.Provider value={contextValue}>
      {children}
    </ProjectContext.Provider>
  );
};

// eslint-disable-next-line react-refresh/only-export-components
export const useProject = () => {
  const context = useContext(ProjectContext);
  if (context === undefined) {
    throw new Error('useProject must be used within a ProjectProvider');
  }
  return context;
};
