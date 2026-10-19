import React from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { FirstWorkOpenTracker } from "../components/FirstWorkGuidance";
import { Layout } from "../components/Layout";
import { Sidebar } from "../components/sidebar/Sidebar";
import { Editor } from "../components/Editor";
import { ChatPanel } from "../components/ChatPanel";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { PageLoader } from "../components/PageLoader";
import { useProject } from "../contexts/ProjectContext";
import { useAuth } from "../contexts/AuthContext";
import { fileApi } from "../lib/api";
import { ApiError } from "../lib/apiClient";
import { logger } from "../lib/logger";
import { trackEvent } from "../lib/analytics";
import { clearLastOpenedFile, getLastOpenedFile, setLastOpenedFile } from "../lib/lastOpenedFile";
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
  const { setCurrentProjectId, currentProject, loading, error, refreshProjects, projects, selectedItem, setSelectedItem } = useProject();
  const { user } = useAuth();
  const userId = user?.id ?? null;
  const { t } = useTranslation();

  const [isEnsuring, setIsEnsuring] = React.useState(false);
  const [notFound, setNotFound] = React.useState(false);
  const attemptedRefreshRef = React.useRef<string | null>(null);
  const selectedFromQueryRef = React.useRef<string | null>(null);
  const restoreAttemptedRef = React.useRef<string | null>(null);
  const selectedItemRef = React.useRef(selectedItem ?? null);
  selectedItemRef.current = selectedItem ?? null;

  const isRouteProjectActive = Boolean(currentProject && projectId && currentProject.id === projectId);
  const currentProjectType = currentProject?.project_type;
  // New-user funnel: the route's project resolved and the workspace renders (once per project).
  const trackedOpenRef = React.useRef<string | null>(null);
  React.useEffect(() => {
    if (!isRouteProjectActive || !projectId || trackedOpenRef.current === projectId) return;
    trackedOpenRef.current = projectId;
    trackEvent("project_opened", { project_id: projectId, project_type: currentProjectType });
  }, [isRouteProjectActive, projectId, currentProjectType]);

  // Reset per projectId
  React.useEffect(() => {
    attemptedRefreshRef.current = null;
    selectedFromQueryRef.current = null;
    restoreAttemptedRef.current = null;
    setNotFound(false);
  }, [projectId]);

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
  }, [isRouteProjectActive, location.search, projectId, setSelectedItem, userId]);

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
  }, [currentProject, location.search, projectId, setSelectedItem]);

  // Ensure project exists in ProjectContext before rendering editor
  React.useEffect(() => {
    let cancelled = false;

    const ensureProject = async () => {
      if (!projectId) return;

      setCurrentProjectId(projectId);

      const exists = projects.some((p) => p.id === projectId);
      if (exists) {
        if (!cancelled) {
          setIsEnsuring(false);
          setNotFound(false);
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
  }, [projectId, projects, refreshProjects, setCurrentProjectId]);

  // Show loading state while projects are being fetched or ensuring project exists
  if (loading || isEnsuring) {
    return <PageLoader />;
  }

  // Show error state
  if (error) {
    return (
      <div className="min-h-screen bg-[hsl(var(--bg-primary))] flex flex-col items-center justify-center gap-4">
        <div className="text-[hsl(var(--error))]">{t('projectLoadFailed')}</div>
        <button onClick={() => refreshProjects()} className="btn btn-primary">
          {t('retry')}
        </button>
      </div>
    );
  }

  // Avoid immediate redirect: show a not-found screen after one refresh attempt
  if (!currentProject) {
    if (!notFound) {
      return <PageLoader />;
    }

    return (
      <div className="min-h-screen bg-[hsl(var(--bg-primary))] flex flex-col items-center justify-center gap-4">
        <div className="text-[hsl(var(--text-primary))]">{t('projectNotFound')}</div>
        <div className="flex gap-3">
          <button
            onClick={async () => {
              attemptedRefreshRef.current = null;
              setNotFound(false);
              setIsEnsuring(true);
              try {
                await refreshProjects();
              } finally {
                setIsEnsuring(false);
              }
            }}
            className="btn btn-primary"
          >
            {t('tryAgain')}
          </button>
          <button onClick={() => navigate("/dashboard", { replace: true })} className="btn btn-ghost">
            {t('backToDashboard')}
          </button>
        </div>
      </div>
    );
  }

  return (
    <>
    <FirstWorkOpenTracker userId={userId} projectId={isRouteProjectActive ? projectId ?? null : null} />
    <Layout
      left={<Sidebar />}
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
