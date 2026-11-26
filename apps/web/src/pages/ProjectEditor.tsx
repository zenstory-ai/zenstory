import React from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { QueryClientContext } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { FirstWorkOpenTracker } from "../components/FirstWorkGuidance";
import { Layout } from "../components/Layout";
import { Editor } from "../components/Editor";
import { ChatPanel } from "../components/ChatPanel";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { PageLoader } from "../components/PageLoader";
import { Button } from "../components/ui/Button";
import { useProject } from "../contexts/ProjectContext";
import { useAuth } from "../contexts/AuthContext";
import { fileApi } from "../lib/api";
import { ApiError, clearAuthStorage } from "../lib/apiClient";
import { logger } from "../lib/logger";
import { trackEvent } from "../lib/analytics";
import { clearLastOpenedFile, getLastOpenedFile, setLastOpenedFile } from "../lib/lastOpenedFile";
import { consumePreloadedFile, seedPreloadedFile } from "../lib/preloadedFileCache";
import { readEditorDraftSnapshot } from "../lib/editorDraftRecovery";
import { notifyFileRemoved } from "../lib/fileRemovalEvents";
import { readWorkbenchSwr, removeWorkbenchSwr } from "../lib/workbenchSwrCache";
import {
  BOOTSTRAP_DEFAULT_CHAT_LIMIT,
  clearWorkbenchSwrSyncRequired,
  dispatchWorkbenchBootstrapFailed,
  dispatchWorkbenchFreshFile,
  evictProjectBootstrapHandoff,
  isProjectBootstrapAvailable,
  markWorkbenchSwrSyncRequired,
  startProjectBootstrap,
} from "../lib/workbenchBootstrap";
import type { SelectedItem, TreeNodeType } from "../types";

function toSelectedItem(file: { id: string; title: string; file_type: string }): SelectedItem {
  const fileType = file.file_type as string;
  const normalizedType: TreeNodeType =
    fileType === "snippet" ? "material" : (fileType as TreeNodeType);
  return { id: file.id, title: file.title, type: normalizedType };
}

// Project editor wrapper - loads project from URL param
export default function ProjectEditor() {
  const { projectId } = useParams<{ projectId: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = React.useContext(QueryClientContext);
  const {
    setCurrentProjectId,
    currentProject,
    loading,
    error,
    refreshProjects,
    hydrateProject,
    projects,
    selectedItem,
    setSelectedItem,
  } = useProject();
  const { user } = useAuth();
  const userId = user?.id ?? null;
  const { t } = useTranslation();

  const [isEnsuring, setIsEnsuring] = React.useState(false);
  const [notFound, setNotFound] = React.useState(false);
  const [bootstrapRetryNonce, setBootstrapRetryNonce] = React.useState(0);
  const attemptedRefreshRef = React.useRef<string | null>(null);
  const selectedFromQueryRef = React.useRef<string | null>(null);
  const restoreAttemptedRef = React.useRef<string | null>(null);
  const bootstrappedProjectRef = React.useRef<string | null>(null);
  const bootstrapNotFoundProjectRef = React.useRef<string | null>(null);
  const previousBootstrapProjectRef = React.useRef<string | null>(null);
  const selectedItemRef = React.useRef(selectedItem ?? null);
  selectedItemRef.current = selectedItem ?? null;
  const locationSearchRef = React.useRef(location.search);
  locationSearchRef.current = location.search;

  const isRouteProjectActive = Boolean(currentProject && projectId && currentProject.id === projectId);
  const isRouteProjectActiveRef = React.useRef(isRouteProjectActive);
  isRouteProjectActiveRef.current = isRouteProjectActive;
  const currentProjectType = currentProject?.project_type;
  // New-user funnel: the route's project resolved and the workspace renders (once per project).
  const trackedOpenRef = React.useRef<string | null>(null);
  React.useEffect(() => {
    if (!isRouteProjectActive || !projectId || trackedOpenRef.current === projectId) return;
    trackedOpenRef.current = projectId;
    trackEvent("project_opened", { project_id: projectId, project_type: currentProjectType });
  }, [isRouteProjectActive, projectId, currentProjectType]);

  // Reset per projectId synchronously before the bootstrap layout effect registers new refs.
  React.useLayoutEffect(() => {
    attemptedRefreshRef.current = null;
    selectedFromQueryRef.current = null;
    restoreAttemptedRef.current = null;
    bootstrappedProjectRef.current = null;
    bootstrapNotFoundProjectRef.current = null;
    setNotFound(false);
  }, [projectId]);

  // Single-round-trip workbench bootstrap + user-scoped SWR instant paint.
  // Runs in useLayoutEffect so startProjectBootstrap registers the handoff in QueryClient
  // synchronously BEFORE child useEffects (useFileTree / ChatPanel / Editor) fire, even when
  // navigating from Dashboard to an already-active project where children mount on frame 1.
  React.useLayoutEffect(() => {
    if (!userId || !projectId || !queryClient || !isProjectBootstrapAvailable()) {
      return;
    }

    // Evict the previous project's settled bootstrap handoff on real project switch
    // (never during StrictMode double-mount of the same projectId) so A→B→A always fetches fresh data.
    const previousProjectId = previousBootstrapProjectRef.current;
    if (previousProjectId && previousProjectId !== projectId) {
      evictProjectBootstrapHandoff(queryClient, userId, previousProjectId);
    }
    previousBootstrapProjectRef.current = projectId;

    let cancelled = false;
    const searchParams = new URLSearchParams(locationSearchRef.current);
    const linkedFileId = searchParams.get("file");
    const rememberedFileId = linkedFileId ? null : getLastOpenedFile(userId, projectId);
    const requestedFileId = linkedFileId || rememberedFileId || null;
    // During an A→B project switch, selectedItemRef can still hold A's file until
    // setCurrentProjectId(B) commits; treat selection as belonging to this project
    // only once this route project is active.
    const hasSelectionForThisProject = () =>
      isRouteProjectActiveRef.current && Boolean(selectedItemRef.current);

    // Fence legacy separate fileApi.get effects so boot does not duplicate the file read.
    bootstrappedProjectRef.current = projectId;
    restoreAttemptedRef.current = projectId;
    if (linkedFileId) {
      selectedFromQueryRef.current = linkedFileId;
    }
    setCurrentProjectId(projectId);

    // 1) User-scoped SWR instant paint (never touches unsaved editorDraftRecovery snapshots).
    let swrPaintedFileId: string | null = null;
    const swrEntry = readWorkbenchSwr(userId, projectId);
    if (swrEntry) {
      hydrateProject?.(swrEntry.project);
      if (
        requestedFileId &&
        swrEntry.initial_file &&
        swrEntry.initial_file.id === requestedFileId &&
        !hasSelectionForThisProject()
      ) {
        // CRITICAL draft protection invariant: if an unsaved recovery snapshot exists in
        // localStorage for this file, wait for authoritative server updated_at from /bootstrap
        // instead of resolving draft recovery against a potentially stale SWR copy.
        let hasPendingRecoveryDraft = false;
        try {
          hasPendingRecoveryDraft =
            readEditorDraftSnapshot(window.localStorage, {
              userId,
              projectId,
              fileId: requestedFileId,
            }) !== null;
        } catch {
          // Storage unavailable: proceed without throwing or claiming draft protection.
          hasPendingRecoveryDraft = false;
        }
        if (!hasPendingRecoveryDraft) {
          seedPreloadedFile(queryClient, userId, projectId, swrEntry.initial_file);
          swrPaintedFileId = swrEntry.initial_file.id;
          setSelectedItem(toSelectedItem(swrEntry.initial_file));
        }
      }
    }

    // 2) Live aggregated /bootstrap (automatically falls back to legacy endpoints on 404/405).
    void startProjectBootstrap(queryClient, {
      userId,
      projectId,
      fileId: requestedFileId,
      chatLimit: BOOTSTRAP_DEFAULT_CHAT_LIMIT,
    })
      .then((result) => {
        if (cancelled || !result) return;
        const { project, initial_file: initialFile } = result.data;
        bootstrapNotFoundProjectRef.current = null;
        clearWorkbenchSwrSyncRequired(queryClient, userId, projectId, requestedFileId);
        hydrateProject?.(project);
        setNotFound(false);

        if (requestedFileId) {
          if (initialFile && initialFile.id === requestedFileId) {
            if (
              swrPaintedFileId === requestedFileId &&
              selectedItemRef.current?.id === requestedFileId
            ) {
              // Always overwrite preloadedFileCache with the fresh server copy BEFORE dispatching
              // the event so a late-mounting or microtask-yielded Editor never commits stale SWR data.
              seedPreloadedFile(queryClient, userId, projectId, initialFile);
              dispatchWorkbenchFreshFile({ userId, projectId, file: initialFile });
            } else if (!hasSelectionForThisProject()) {
              seedPreloadedFile(queryClient, userId, projectId, initialFile);
              setSelectedItem(toSelectedItem(initialFile));
            }
          } else if (result.initialFileStatus !== "transient_error") {
            // Definitive answer: requested file was deleted, moved, or is a folder.
            // Transient legacy fallback errors preserve lastOpenedFile and avoid false removal.
            if (rememberedFileId && !linkedFileId) {
              clearLastOpenedFile(userId, projectId);
            }
            if (swrPaintedFileId === requestedFileId) {
              consumePreloadedFile(queryClient, userId, projectId, requestedFileId);
              notifyFileRemoved(projectId, requestedFileId);
            }
          } else if (swrPaintedFileId === requestedFileId) {
            markWorkbenchSwrSyncRequired(queryClient, userId, projectId, swrPaintedFileId);
            dispatchWorkbenchBootstrapFailed({ userId, projectId, fileId: swrPaintedFileId });
          }
        } else if (initialFile) {
          // Warm the one-shot cache for the most recently updated editable file without
          // altering empty-state selection behavior.
          seedPreloadedFile(queryClient, userId, projectId, initialFile);
        }
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (
          err instanceof ApiError &&
          err.status === 400 &&
          err.errorCode === "ERR_AUTH_INACTIVE_USER"
        ) {
          clearAuthStorage("background_validation_failed");
          return;
        }
        if (err instanceof ApiError && (err.status === 404 || err.status === 403)) {
          bootstrapNotFoundProjectRef.current = projectId;
          removeWorkbenchSwr(userId, projectId);
          setNotFound(true);
          return;
        }
        // Transient /bootstrap failure: allow legacy restore effects to retry once ProjectContext loads.
        if (restoreAttemptedRef.current === projectId) {
          restoreAttemptedRef.current = null;
        }
        if (linkedFileId && selectedFromQueryRef.current === linkedFileId) {
          selectedFromQueryRef.current = null;
        }
        // If SWR already painted this file, do not strand the editor on the stale cached copy:
        // trigger a fresh server sync (or surface the Editor retry banner if the server stays down).
        if (swrPaintedFileId && swrPaintedFileId === requestedFileId) {
          markWorkbenchSwrSyncRequired(queryClient, userId, projectId, swrPaintedFileId);
          dispatchWorkbenchBootstrapFailed({ userId, projectId, fileId: swrPaintedFileId });
        }
        logger.warn("[ProjectEditor] Bootstrap load failed, falling back to ProjectContext", {
          projectId,
          error: err instanceof Error ? err.message : String(err),
        });
      });

    return () => {
      cancelled = true;
    };
  }, [bootstrapRetryNonce, hydrateProject, projectId, queryClient, setCurrentProjectId, setSelectedItem, userId]);

  // Remember the open file so reopening this project lands on it.
  const selectedFileId = selectedItem && selectedItem.type !== "folder" ? selectedItem.id : null;
  React.useEffect(() => {
    if (!userId || !projectId || !isRouteProjectActive || !selectedFileId) return;
    setLastOpenedFile(userId, projectId, selectedFileId);

    // A file link chooses the entry file once. After the author opens another
    // file, refresh should restore that choice rather than the old entry link.
    const searchParams = new URLSearchParams(location.search);
    const linkedFileId = searchParams.get("file");
    if (linkedFileId && selectedFromQueryRef.current === linkedFileId && selectedFileId !== linkedFileId) {
      searchParams.delete("file");
      navigate({ pathname: location.pathname, search: searchParams.toString(), hash: location.hash }, { replace: true });
    }
  }, [isRouteProjectActive, location.hash, location.pathname, location.search, navigate, projectId, selectedFileId, userId]);

  // Reopen the last file when the project is entered without a ?file= link and
  // nothing is selected yet. The id is re-validated: it may have been deleted
  // or moved to another project since.
  React.useEffect(() => {
    if (!userId || !projectId || !isRouteProjectActive) return;
    if (new URLSearchParams(location.search).get("file")) return;
    if (restoreAttemptedRef.current === projectId) return;
    if (selectedItemRef.current) return;

    const lastFileId = getLastOpenedFile(userId, projectId);
    restoreAttemptedRef.current = projectId;
    if (!lastFileId) return;

    let cancelled = false;
    let settled = false;

    const restoreLastOpenedFile = async () => {
      try {
        const file = await fileApi.get(lastFileId);
        if (cancelled) return;
        const isDeleted = (file as { is_deleted?: boolean }).is_deleted === true;
        if (file.project_id !== projectId || isDeleted || (file.file_type as string) === "folder") {
          clearLastOpenedFile(userId, projectId);
          return;
        }
        // The author may have opened something while this was loading.
        if (selectedItemRef.current) return;
        seedPreloadedFile(queryClient, userId, projectId, file);
        setSelectedItem(toSelectedItem(file));
      } catch (err) {
        if (cancelled) return;
        // Only a definitive answer (gone / not yours) forgets the record; a
        // network blip should not lose it.
        if (err instanceof ApiError && err.status >= 400 && err.status < 500) {
          clearLastOpenedFile(userId, projectId);
        }
        logger.warn("[ProjectEditor] Failed to restore last opened file", {
          fileId: lastFileId,
          projectId,
          error: err instanceof Error ? err.message : String(err),
        });
      } finally {
        settled = true;
      }
    };

    void restoreLastOpenedFile();

    return () => {
      cancelled = true;
      // Interrupted before an answer: allow another attempt.
      if (!settled && restoreAttemptedRef.current === projectId) {
        restoreAttemptedRef.current = null;
      }
    };
  }, [isRouteProjectActive, location.search, projectId, queryClient, setSelectedItem, userId]);

  // Select target file from query parameter: /project/:projectId?file=:fileId
  React.useEffect(() => {
    // Wait until the route's project is active. On a direct link the provider
    // can briefly expose the previously selected project; selecting the file
    // during that window is then cleared by setCurrentProjectId().
    if (!currentProject || !projectId || currentProject.id !== projectId) return;

    const searchParams = new URLSearchParams(location.search);
    const fileId = searchParams.get("file");

    if (!fileId) {
      selectedFromQueryRef.current = null;
      return;
    }

    // Prevent repeated network requests for the same query value.
    if (selectedFromQueryRef.current === fileId) return;

    let cancelled = false;

    const selectFileFromQuery = async () => {
      try {
        const file = await fileApi.get(fileId);
        if (cancelled) return;

        // Guard against cross-project query values.
        if (file.project_id !== projectId) {
          logger.warn("[ProjectEditor] Ignored file query param from different project", {
            projectId,
            fileProjectId: file.project_id,
            fileId,
          });
          return;
        }

        seedPreloadedFile(queryClient, userId, projectId, file);
        setSelectedItem(toSelectedItem(file));
        selectedFromQueryRef.current = fileId;
      } catch (err) {
        logger.warn("[ProjectEditor] Failed to load file from query param", {
          fileId,
          projectId,
          error: err instanceof Error ? err.message : String(err),
        });
      }
    };

    void selectFileFromQuery();

    return () => {
      cancelled = true;
    };
  }, [currentProject, location.search, projectId, queryClient, setSelectedItem, userId]);

  // Ensure project exists in ProjectContext before rendering editor
  React.useEffect(() => {
    let cancelled = false;

    const ensureProject = async () => {
      if (!projectId) return;

      setCurrentProjectId(projectId);

      // ProjectProvider is already fetching the project list (or just surfaced an error):
      // wait for it rather than cancelling and duplicating GET /api/v1/projects.
      if (loading || error) {
        return;
      }

      const exists = projects.some((p) => p.id === projectId);
      if (exists) {
        if (!cancelled) {
          setIsEnsuring(false);
          if (bootstrapNotFoundProjectRef.current !== projectId) {
            setNotFound(false);
          }
        }
        return;
      }

      // Only attempt refresh once per projectId
      if (attemptedRefreshRef.current === projectId) {
        if (!cancelled) {
          setIsEnsuring(false);
          setNotFound(true);
        }
        return;
      }

      attemptedRefreshRef.current = projectId;
      if (!cancelled) setIsEnsuring(true);

      try {
        await refreshProjects();
      } finally {
        if (!cancelled) setIsEnsuring(false);
      }
    };

    void ensureProject();

    return () => {
      cancelled = true;
    };
  }, [projectId, projects, loading, error, refreshProjects, setCurrentProjectId]);

  // Render definitive not-found BEFORE loading/currentProject checks so an SWR-hydrated
  // or stale project entry never hides a 404/403 project-not-found state.
  if (notFound) {
    return (
      <div className="min-h-screen bg-[hsl(var(--bg-primary))] flex flex-col items-center justify-center gap-4">
        <div className="text-[hsl(var(--text-primary))]">{t('projectNotFound')}</div>
        <div className="flex gap-3">
          <Button
            className="max-md:min-h-11"
            onClick={async () => {
              attemptedRefreshRef.current = null;
              bootstrapNotFoundProjectRef.current = null;
              evictProjectBootstrapHandoff(queryClient ?? undefined, userId, projectId);
              setNotFound(false);
              setBootstrapRetryNonce((n) => n + 1);
              setIsEnsuring(true);
              try {
                await refreshProjects();
              } finally {
                setIsEnsuring(false);
              }
            }}
          >
            {t('tryAgain')}
          </Button>
          <Button variant="secondary" className="max-md:min-h-11" onClick={() => navigate("/dashboard", { replace: true })}>
            {t('backToDashboard')}
          </Button>
        </div>
      </div>
    );
  }

  // Show loading state while projects are being fetched or ensuring project exists
  // (never block an already-bootstrapped or SWR-hydrated active route project).
  if (!isRouteProjectActive && (loading || isEnsuring)) {
    return <PageLoader />;
  }

  // Show error state only when the active route project is not available.
  if (error && !isRouteProjectActive) {
    return (
      <div className="min-h-screen bg-[hsl(var(--bg-primary))] flex flex-col items-center justify-center gap-4">
        <div role="alert" className="text-[hsl(var(--error-text))]">{t('projectLoadFailed')}</div>
        <Button onClick={() => refreshProjects()} className="max-md:min-h-11">
          {t('retry')}
        </Button>
      </div>
    );
  }

  // Avoid immediate redirect: wait for initial load / ensure attempt before showing not-found
  if (!currentProject) {
    return <PageLoader />;
  }

  return (
    <>
    <FirstWorkOpenTracker userId={userId} projectId={isRouteProjectActive ? projectId ?? null : null} />
    <Layout
      middle={(
        // ph-no-capture: manuscript text must never reach analytics capture.
        <ErrorBoundary area="editor" className="ph-no-capture">
          <Editor />
        </ErrorBoundary>
      )}
      right={(
        <ErrorBoundary area="chat" className="ph-no-capture">
          <ChatPanel />
        </ErrorBoundary>
      )}
    />
    </>
  );
}
