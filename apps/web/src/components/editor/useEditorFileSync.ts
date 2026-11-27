/**
 * File loading, SWR monotonic server sync, draft recovery, deleted-file
 * protection, and optimistic-concurrency saving for Editor.
 *
 * @module components/editor/useEditorFileSync
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type MutableRefObject,
} from "react";
import type { QueryClient } from "@tanstack/react-query";
import type { TFunction } from "i18next";
import { fileApi } from "../../lib/api";
import { ApiError } from "../../lib/apiClient";
import { handleApiError } from "../../lib/errorHandler";
import { toast } from "../../lib/toast";
import { isFileTitleTooLong } from "../../lib/fileTitle";
import { logger } from "../../lib/logger";
import { getUpgradePromptDefinition } from "../../config/upgradeExperience";
import { claimAutoUpgradePrompt } from "../../lib/upgradeTriggerStrategy";
import { rearmAiFileOpened, reportActivationEvent } from "../../lib/activationEvents";
import {
  BEFORE_CHUNK_RELOAD_EVENT,
  clearEditorDraftSnapshot,
  createEditorDraftSnapshot,
  readDeletedEditorDraftSnapshots,
  readEditorDraftSnapshot,
  resolveEditorDraftRecovery,
  writeEditorDraftSnapshot,
  type EditorDraftSnapshot,
} from "../../lib/editorDraftRecovery";
import {
  trackEditorDraftRecoveryResolved,
  trackEditorDraftRecoveryShown,
  trackEditorSaveConflict409,
} from "../../lib/editorSafetyTelemetry";
import { notifyEditorContentSaved, setOpenEditorFlush } from "../../lib/editorSaveTracker";
import {
  FILE_REMOVED_EVENT,
  clearFileRemoved,
  isFileRemoved,
  notifyFileRemoved,
  setUnprotectedDeletedDraft,
  type FileRemovedDetail,
} from "../../lib/fileRemovalEvents";
import { clearLastOpenedFile } from "../../lib/lastOpenedFile";
import { consumePreloadedFile, peekPreloadedFile } from "../../lib/preloadedFileCache";
import {
  WORKBENCH_BOOTSTRAP_FAILED_EVENT,
  WORKBENCH_FRESH_FILE_EVENT,
  consumeWorkbenchSwrSyncRequired,
  type WorkbenchBootstrapFailedDetail,
  type WorkbenchFreshFileDetail,
} from "../../lib/workbenchBootstrap";
import {
  invalidateWorkbenchSwrInitialFile,
  updateWorkbenchSwrInitialFile,
} from "../../lib/workbenchSwrCache";
import type { SaveOutcome, SaveResult, SaveSubmission } from "../SimpleEditor";
import type { DiffReviewState, File, TreeNodeType } from "../../types";
import {
  SERVER_SYNC_RETRY_DELAYS_MS,
  isSameReview,
  type ConflictReview,
  type ConflictReviewOutcome,
  type DraftRecoveryState,
  type RemovedDraftEntry,
  type ServerBaselineState,
  type ServerSyncIssueState,
} from "./editorFileHelpers";
import { buildReviewLeaveDraft } from "./useEditorConflictReview";

export interface UseEditorFileSyncOptions {
  t: TFunction;
  queryClient: QueryClient | undefined;
  currentUserId: string | null;
  currentProjectId: string | null;
  selectedItem: { id: string; type: TreeNodeType; title: string } | null;
  setSelectedItem: (item: { id: string; type: TreeNodeType; title: string } | null) => void;
  streamingFileId: string | null;
  editorRefreshVersion: number;
  lastEditedFileId: string | null;
  triggerFileTreeRefresh: () => void;
  isMobile: boolean;
  diffReviewState: DiffReviewState | null;
  exitDiffReview: () => void;
  conflictReviewRef: MutableRefObject<ConflictReview | null>;
  openConflictReview: (
    fileId: string,
    base: string,
    serverContent: string,
    localContent: string,
  ) => ConflictReviewOutcome;
  onProjectChange?: () => void;
}

export function useEditorFileSync({
  t,
  queryClient,
  currentUserId,
  currentProjectId,
  selectedItem,
  setSelectedItem,
  streamingFileId,
  editorRefreshVersion,
  lastEditedFileId,
  triggerFileTreeRefresh,
  isMobile,
  diffReviewState,
  exitDiffReview,
  conflictReviewRef,
  openConflictReview,
  onProjectChange,
}: UseEditorFileSyncOptions) {
  const fileVersionUpgradePrompt = getUpgradePromptDefinition("file_version_quota_blocked");

  // Data states
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showFileVersionUpgradeModal, setShowFileVersionUpgradeModal] = useState(false);

  // Local editing states
  const [editTitle, setEditTitle] = useState("");
  const [editContent, setEditContent] = useState("");
  const editTitleRef = useRef(editTitle);
  const longTitleWarnedRef = useRef<string | null>(null);
  const editContentRef = useRef(editContent);
  editTitleRef.current = editTitle;
  editContentRef.current = editContent;

  const [serverBaseline, setServerBaseline] = useState<ServerBaselineState | null>(null);
  const serverBaselineVersionRef = useRef(0);
  const [serverSyncIssue, setServerSyncIssue] = useState<ServerSyncIssueState | null>(null);

  const removedFileHandlerRef = useRef<(fileId: string) => void>(() => {});
  const [removedDrafts, setRemovedDrafts] = useState<RemovedDraftEntry[]>([]);
  const removedDraftsRef = useRef(removedDrafts);
  removedDraftsRef.current = removedDrafts;

  const clearRemovedDraftEntry = useCallback(
    (scope: { userId: string; projectId: string; fileId: string }) => {
      setRemovedDrafts((previous) =>
        previous.filter(
          (item) =>
            item.snapshot.userId !== scope.userId ||
            item.snapshot.projectId !== scope.projectId ||
            item.snapshot.fileId !== scope.fileId,
        ),
      );
    },
    [],
  );

  // syncOpenFileFromServer (defined below), for reloads of the open file in loadData.
  const syncOpenFileFromServerRef = useRef<() => Promise<void>>(async () => {});
  const [draftRecovery, setDraftRecovery] = useState<DraftRecoveryState | null>(null);

  const hasLoadedRef = useRef(false);
  const loadGenerationRef = useRef(0);
  // Counts every server read/save that can advance the open editor copy, so an older
  // loadData/syncOpenFileFromServer/bootstrap response answering late can never roll it back.
  const serverSyncSeqRef = useRef(0);
  const initialLoadSyncSeqRef = useRef<{ fileId: string; seq: number } | null>(null);
  const restoredSelectionRef = useRef<string | null>(null);
  const currentFileRef = useRef<File | null>(file);
  const translateRef = useRef(t);
  const activeProjectIdRef = useRef<string | null>(currentProjectId);
  const selectedIdRef = useRef(selectedItem?.id);
  const currentReviewRef = useRef(diffReviewState);
  selectedIdRef.current = selectedItem?.id;
  currentReviewRef.current = diffReviewState;

  // Read by loadData for the new-user funnel without making it reload when these change.
  const openContextRef = useRef({ isMobile, streamingFileId });
  openContextRef.current = { isMobile, streamingFileId };
  // The open file was selected by the AI's <file> stream (desktop auto-open), not by the author.
  const autoOpenedFileIdRef = useRef<string | null>(null);

  const onProjectChangeRef = useRef(onProjectChange);
  onProjectChangeRef.current = onProjectChange;

  useEffect(() => {
    activeProjectIdRef.current = currentProjectId;
    onProjectChangeRef.current?.();
    return () => {
      activeProjectIdRef.current = null;
      loadGenerationRef.current += 1;
    };
  }, [currentProjectId]);

  currentFileRef.current = file;
  useEffect(() => {
    translateRef.current = t;
  }, [t]);

  const editorFlushRef = useRef<(() => Promise<SaveOutcome>) | null>(null);
  const registerEditorFlush = useCallback((flush: (() => Promise<SaveOutcome>) | null) => {
    const previous = editorFlushRef.current;
    editorFlushRef.current = flush;
    // The chat saves the author's typed text before it stops a round (see ChatPanel).
    setOpenEditorFlush(flush, previous, () => currentFileRef.current?.id ?? null);
  }, []);

  /**
   * Make `data` SimpleEditor's clean baseline (draft, save token, dirty flag and
   * word-count base), for server copies that arrive outside its own save flow.
   */
  const markServerBaseline = useCallback((data: File) => {
    serverBaselineVersionRef.current += 1;
    setServerBaseline({
      version: serverBaselineVersionRef.current,
      fileId: data.id,
      title: data.title,
      content: data.content || "",
      updatedAt: data.updated_at,
    });
  }, []);

  const loadData = useCallback(
    async (options?: { followServer?: boolean; discardUnsaved?: boolean }) => {
      if (activeProjectIdRef.current !== currentProjectId || selectedIdRef.current !== selectedItem?.id) return;
      const generation = ++loadGenerationRef.current;
      const currentFile = currentFileRef.current;
      const restoredId = restoredSelectionRef.current;
      restoredSelectionRef.current = null;
      // A failed switch only restores navigation; refetching would replace the retained draft.
      if (restoredId && restoredId === selectedItem?.id && restoredId === currentFile?.id) return;
      if (
        currentFile &&
        !isFileRemoved(currentProjectId, currentFile.id) &&
        currentFile.id !== selectedItem?.id &&
        editorFlushRef.current
      ) {
        const outcome = await editorFlushRef.current();
        if (generation !== loadGenerationRef.current) return;
        if (outcome !== "saved") {
          if (currentProjectId && selectedItem?.id) {
            consumePreloadedFile(queryClient, currentUserId, currentProjectId, selectedItem.id);
          }
          toast.error(translateRef.current("editor:saveFailed"));
          restoredSelectionRef.current = currentFile.id;
          setSelectedItem({
            id: currentFile.id,
            type: currentFile.file_type,
            title: currentFile.title,
          });
          return;
        }
      }

      if (!selectedItem || !currentProjectId) {
        setFile(null);
        setDraftRecovery(null);
        hasLoadedRef.current = false;
        return;
      }

      // Don't load folder content
      if (selectedItem.type === "folder") {
        setFile(null);
        setDraftRecovery(null);
        hasLoadedRef.current = false;
        return;
      }

      if (
        !options?.discardUnsaved &&
        currentFile?.id === selectedItem.id &&
        (editTitleRef.current !== currentFile.title ||
          editContentRef.current !== (currentFile.content || ""))
      ) {
        void syncOpenFileFromServerRef.current();
        return;
      }

      // Only show loading state if no file is currently loaded (initial load)
      if (!hasLoadedRef.current) {
        setLoading(true);
      }
      setError(null);
      const followServer = options?.followServer === true && currentFile?.id === selectedItem.id;
      const serverReadSeq = ++serverSyncSeqRef.current;

      try {
        const preloaded =
          !options?.followServer &&
          !options?.discardUnsaved &&
          currentFile?.id !== selectedItem.id
            ? peekPreloadedFile(queryClient, currentUserId, currentProjectId, selectedItem.id)
            : null;
        const usedPreloaded = Boolean(preloaded);
        let data: File;
        if (preloaded) {
          // Yield a microtask before consuming so StrictMode double-mounts do not
          // drop the one-shot handoff before the surviving effect commits.
          await Promise.resolve();
          if (generation !== loadGenerationRef.current || serverReadSeq !== serverSyncSeqRef.current) return;
          if (isFileRemoved(currentProjectId, selectedItem.id)) return;
          // Re-read after yield so a fresher /bootstrap overwrite written during the yield wins over stale SWR.
          const latestPreloaded = peekPreloadedFile(
            queryClient,
            currentUserId,
            currentProjectId,
            selectedItem.id,
          );
          consumePreloadedFile(queryClient, currentUserId, currentProjectId, selectedItem.id);
          if (!latestPreloaded) {
            // Entry was explicitly evicted (e.g. deleted-file notification from /bootstrap).
            return;
          }
          data = latestPreloaded;
        } else {
          data = await fileApi.get(selectedItem.id);
          if (generation !== loadGenerationRef.current || serverReadSeq !== serverSyncSeqRef.current) return;
        }
        if (
          !options?.discardUnsaved &&
          currentFileRef.current?.id === data.id &&
          (editTitleRef.current !== currentFileRef.current.title ||
            editContentRef.current !== (currentFileRef.current.content || ""))
        ) {
          void syncOpenFileFromServerRef.current();
          return;
        }
        clearFileRemoved(currentProjectId, data.id);
        if (!usedPreloaded) {
          updateWorkbenchSwrInitialFile(currentUserId, currentProjectId, data);
        }
        initialLoadSyncSeqRef.current = { fileId: data.id, seq: serverReadSeq };
        currentFileRef.current = data;
        setFile(data);
        if (
          usedPreloaded &&
          consumeWorkbenchSwrSyncRequired(queryClient, currentUserId, currentProjectId, data.id)
        ) {
          // /bootstrap failed transiently before this SWR-painted Editor finished mounting:
          // trigger an authoritative server sync (or surface serverSyncIssue if still unreachable).
          void syncOpenFileFromServerRef.current();
        }
        // New-user funnel: the author opened a file, or the AI finished writing the file the author
        // had open. An open made by the AI's own stream does not count.
        const isNewOpen = !followServer && currentFile?.id !== data.id;
        if (isNewOpen) {
          autoOpenedFileIdRef.current =
            openContextRef.current.streamingFileId === data.id ? data.id : null;
        }
        if ((isNewOpen || followServer) && autoOpenedFileIdRef.current !== data.id) {
          void reportActivationEvent(currentUserId, {
            event_name: "ai_file_opened",
            file_id: data.id,
            is_mobile: openContextRef.current.isMobile,
            while_ai_writing: followServer,
          });
        }
        hasLoadedRef.current = true;
        setServerSyncIssue((prev) => (prev?.fileId === data.id ? null : prev));
        const serverContent = data.content || "";
        const scope = currentUserId
          ? {
              userId: currentUserId,
              projectId: currentProjectId,
              fileId: data.id,
            }
          : null;
        const memoryRemoved = scope
          ? removedDraftsRef.current.find(
              (item) =>
                item.snapshot.userId === scope.userId &&
                item.snapshot.projectId === scope.projectId &&
                item.snapshot.fileId === scope.fileId,
            )
          : undefined;
        const snapshot =
          (scope ? readEditorDraftSnapshot(localStorage, scope) : null) ??
          memoryRemoved?.snapshot ??
          null;
        if (!snapshot) {
          if (scope) clearRemovedDraftEntry(scope);
          setDraftRecovery(null);
          editTitleRef.current = data.title;
          editContentRef.current = serverContent;
          setEditTitle(data.title);
          setEditContent(serverContent);
          if (followServer) markServerBaseline(data);
        } else {
          const resolution = resolveEditorDraftRecovery(snapshot, {
            title: data.title,
            content: serverContent,
            updatedAt: data.updated_at,
          });
          if (resolution === "identical") {
            if (scope) {
              clearEditorDraftSnapshot(localStorage, scope);
              clearRemovedDraftEntry(scope);
            }
            setDraftRecovery(null);
            editTitleRef.current = data.title;
            editContentRef.current = serverContent;
            setEditTitle(data.title);
            setEditContent(serverContent);
            if (followServer) markServerBaseline(data);
          } else {
            const liveSnapshot: EditorDraftSnapshot =
              snapshot.reason === "file-deleted" ? { ...snapshot, reason: "page-exit" } : snapshot;
            let storageAvailable = !memoryRemoved || memoryRemoved.persisted;
            if (scope && (snapshot.reason === "file-deleted" || memoryRemoved)) {
              const persistedNow = writeEditorDraftSnapshot(localStorage, liveSnapshot);
              storageAvailable = persistedNow;
              if (persistedNow && !liveSnapshot.recoveryDraft) {
                clearRemovedDraftEntry(scope);
              }
            }
            if (resolution === "recover") {
              trackEditorDraftRecoveryShown({
                surface: "draft_recovery",
                isMobile: openContextRef.current.isMobile,
                reason: "recover",
                storageAvailable,
              });
              setDraftRecovery({
                snapshot: liveSnapshot,
                serverTitle: data.title,
                serverContent,
                kind: "recover",
                applied: true,
              });
              setEditTitle(liveSnapshot.title);
              setEditContent(liveSnapshot.content);
            } else {
              trackEditorDraftRecoveryShown({
                surface: "draft_recovery",
                isMobile: openContextRef.current.isMobile,
                reason: "conflict",
                storageAvailable,
              });
              setDraftRecovery({
                snapshot: liveSnapshot,
                serverTitle: data.title,
                serverContent,
                kind: "conflict",
                applied: false,
              });
              setEditTitle(data.title);
              setEditContent(serverContent);
              if (followServer) markServerBaseline(data);
            }
          }
        }
      } catch (err: unknown) {
        if (generation !== loadGenerationRef.current || serverReadSeq !== serverSyncSeqRef.current) return;
        const loadErr = err as { status?: number; errorCode?: string };
        if (loadErr?.status === 404 || loadErr?.errorCode === "ERR_FILE_NOT_FOUND") {
          notifyFileRemoved(currentProjectId, selectedItem.id);
          return;
        }
        if (loadErr?.status !== 401) {
          logger.error("Failed to load data:", err);
          if (followServer) {
            setServerSyncIssue({ fileId: selectedItem.id, retrying: false });
          } else {
            setError(translateRef.current("editor:placeholder.loadFailed"));
          }
        }
      } finally {
        if (generation === loadGenerationRef.current) setLoading(false);
      }
    },
    [
      selectedItem,
      currentProjectId,
      setSelectedItem,
      currentUserId,
      queryClient,
      markServerBaseline,
      clearRemovedDraftEntry,
    ],
  );

  useEffect(() => {
    loadData();
    return () => {
      loadGenerationRef.current += 1;
    };
  }, [loadData]);

  // Track previous streaming file id to detect when streaming ends
  const prevStreamingFileIdRef = useRef<string | null>(null);

  // Reload file content when streaming ends for this file
  useEffect(() => {
    const prevId = prevStreamingFileIdRef.current;
    prevStreamingFileIdRef.current = streamingFileId;
    if (prevId && prevId !== streamingFileId) rearmAiFileOpened(currentUserId, prevId);

    if (prevId && prevId === file?.id && prevId === selectedItem?.id && streamingFileId === null) {
      const timer = setTimeout(() => {
        void loadData({ followServer: true });
      }, 100);
      return () => clearTimeout(timer);
    }
  }, [streamingFileId, file?.id, selectedItem?.id, loadData, currentUserId]);

  const adoptServerCopy = useCallback(
    (data: File, nextTitle: string) => {
      currentFileRef.current = data;
      setFile(data);
      setEditTitle(nextTitle);
      setEditContent(data.content || "");
      markServerBaseline(data);
      updateWorkbenchSwrInitialFile(currentUserId, data.project_id, data);
    },
    [currentUserId, markServerBaseline],
  );

  const keepLocalOnServerCopy = useCallback(
    (data: File, nextTitle: string) => {
      currentFileRef.current = data;
      setFile(data);
      setEditTitle(nextTitle);
      markServerBaseline(data);
      updateWorkbenchSwrInitialFile(currentUserId, data.project_id, data);
    },
    [currentUserId, markServerBaseline],
  );

  const getReviewLeaveDraft = useCallback(
    () =>
      buildReviewLeaveDraft(
        currentReviewRef.current,
        currentFileRef.current,
        conflictReviewRef.current,
        editTitleRef.current,
      ),
    [conflictReviewRef],
  );

  useEffect(() => {
    setRemovedDrafts((previous) => {
      const memory = previous.filter(
        ({ snapshot, persisted }) => !persisted && snapshot.userId === currentUserId,
      );
      const stored =
        currentUserId && currentProjectId
          ? readDeletedEditorDraftSnapshots(localStorage, {
              userId: currentUserId,
              projectId: currentProjectId,
            }).map((snapshot) => ({ snapshot, persisted: true }))
          : [];
      return [
        ...memory,
        ...stored.filter(
          ({ snapshot }) =>
            !memory.some(
              (item) =>
                item.snapshot.projectId === snapshot.projectId &&
                item.snapshot.fileId === snapshot.fileId,
            ),
        ),
      ];
    });
  }, [currentUserId, currentProjectId]);

  const unprotectedProjectIds = useMemo(
    () => [
      ...new Set(
        removedDrafts
          .filter(({ snapshot, persisted }) => !persisted && snapshot.userId === currentUserId)
          .map(({ snapshot }) => snapshot.projectId),
      ),
    ],
    [removedDrafts, currentUserId],
  );
  useEffect(() => {
    if (unprotectedProjectIds.length === 0) return;
    unprotectedProjectIds.forEach((projectId) => setUnprotectedDeletedDraft(projectId, true));
    const protect = (event: Event) => {
      event.preventDefault();
      if (event.type === "beforeunload") (event as BeforeUnloadEvent).returnValue = "";
    };
    window.addEventListener("beforeunload", protect);
    window.addEventListener(BEFORE_CHUNK_RELOAD_EVENT, protect);
    return () => {
      unprotectedProjectIds.forEach((projectId) => setUnprotectedDeletedDraft(projectId, false));
      window.removeEventListener("beforeunload", protect);
      window.removeEventListener(BEFORE_CHUNK_RELOAD_EVENT, protect);
    };
  }, [unprotectedProjectIds]);

  removedFileHandlerRef.current = (fileId) => {
    if (!currentProjectId || selectedIdRef.current !== fileId) return;
    const opened = currentFileRef.current;
    const review = currentReviewRef.current;
    const existing =
      draftRecovery?.snapshot.fileId === fileId
        ? draftRecovery.snapshot
        : currentUserId
          ? readEditorDraftSnapshot(localStorage, {
              userId: currentUserId,
              projectId: currentProjectId,
              fileId,
            })
          : null;
    if (opened?.id === fileId && currentUserId) {
      const reviewDraft = getReviewLeaveDraft();
      const pendingRecovery =
        draftRecovery?.snapshot.fileId === fileId && !draftRecovery.applied
          ? draftRecovery.snapshot
          : null;
      const hasCurrentEdits =
        review?.fileId === fileId ||
        editTitleRef.current !== opened.title ||
        editContentRef.current !== (opened.content || "");
      if (hasCurrentEdits || pendingRecovery || existing) {
        const current = {
          title: editTitleRef.current,
          content: reviewDraft?.content ?? editContentRef.current,
          baseUpdatedAt: reviewDraft ? reviewDraft.baseUpdatedAt : opened.updated_at,
        };
        const retained = !hasCurrentEdits && pendingRecovery ? pendingRecovery : current;
        const recoveryDraft =
          pendingRecovery &&
          hasCurrentEdits &&
          (pendingRecovery.title !== current.title || pendingRecovery.content !== current.content)
            ? {
                title: pendingRecovery.title,
                content: pendingRecovery.content,
                baseUpdatedAt: pendingRecovery.baseUpdatedAt,
                capturedAt: pendingRecovery.capturedAt,
              }
            : undefined;
        const snapshot = createEditorDraftSnapshot({
          userId: currentUserId,
          projectId: currentProjectId,
          fileId,
          ...retained,
          recoveryDraft,
          reason: "file-deleted",
        });
        const persisted = writeEditorDraftSnapshot(localStorage, snapshot);
        setRemovedDrafts((previous) => [
          ...previous.filter(
            (item) =>
              item.snapshot.fileId !== fileId ||
              item.snapshot.projectId !== currentProjectId ||
              item.snapshot.userId !== currentUserId,
          ),
          { snapshot, persisted },
        ]);
      }
    }
    if (opened?.id !== fileId && existing) {
      const snapshot: EditorDraftSnapshot = { ...existing, reason: "file-deleted" };
      const persisted = writeEditorDraftSnapshot(localStorage, snapshot);
      setRemovedDrafts((previous) => [
        ...previous.filter(
          (item) =>
            item.snapshot.fileId !== fileId ||
            item.snapshot.projectId !== currentProjectId ||
            item.snapshot.userId !== currentUserId,
        ),
        { snapshot, persisted },
      ]);
    }
    loadGenerationRef.current += 1;
    currentFileRef.current = null;
    editorFlushRef.current = null;
    hasLoadedRef.current = false;
    setFile(null);
    setLoading(false);
    setServerSyncIssue(null);
    consumePreloadedFile(queryClient, currentUserId, currentProjectId, fileId);
    if (currentUserId) {
      clearLastOpenedFile(currentUserId, currentProjectId);
      invalidateWorkbenchSwrInitialFile(currentUserId, currentProjectId, fileId);
    }
    if (review?.fileId === fileId) exitDiffReview();
    setSelectedItem(null);
    toast.info(translateRef.current("editor:deletedFile.message"));
  };

  useEffect(() => {
    const onRemoved = (event: Event) => {
      const { projectId, fileId } = (event as CustomEvent<FileRemovedDetail>).detail;
      if (projectId === activeProjectIdRef.current) removedFileHandlerRef.current(fileId);
    };
    window.addEventListener(FILE_REMOVED_EVENT, onRemoved);
    return () => window.removeEventListener(FILE_REMOVED_EVENT, onRemoved);
  }, []);

  const applyServerCopyToOpenFile = useCallback(
    (data: File, syncSeq: number, generation: number) => {
      if (!data) return;
      const fileId = data.id;
      const current = currentFileRef.current;
      if (
        !current ||
        generation !== loadGenerationRef.current ||
        current.id !== fileId ||
        selectedIdRef.current !== fileId ||
        activeProjectIdRef.current !== data.project_id
      ) {
        return;
      }
      if (syncSeq !== serverSyncSeqRef.current) return;
      consumePreloadedFile(queryClient, currentUserId, data.project_id, fileId);
      setServerSyncIssue((prev) => (prev?.fileId === fileId ? null : prev));
      const serverContent = data.content || "";
      const savedContent = current.content || "";
      const localContent = editContentRef.current;
      const localTitle = editTitleRef.current;
      const nextTitle = localTitle === current.title ? data.title : localTitle;

      const review = currentReviewRef.current;
      if (review?.fileId === fileId) {
        return;
      }

      if (
        data.updated_at === current.updated_at &&
        serverContent === savedContent &&
        data.title === current.title
      ) {
        return;
      }

      if (localContent === savedContent || localContent === serverContent) {
        adoptServerCopy(data, nextTitle);
        return;
      }

      const outcome = openConflictReview(fileId, savedContent, serverContent, localContent);
      if (outcome === "server") {
        adoptServerCopy(data, nextTitle);
        return;
      }
      if (outcome === "local") {
        keepLocalOnServerCopy(data, nextTitle);
        return;
      }
      const merged = {
        ...current,
        title: data.title,
        content: serverContent,
        updated_at: data.updated_at,
      };
      currentFileRef.current = merged;
      setFile((prev) => (prev?.id === fileId ? merged : prev));
      setEditTitle(nextTitle);
      updateWorkbenchSwrInitialFile(currentUserId, data.project_id, merged);
      trackEditorDraftRecoveryShown({
        surface: "conflict_review",
        isMobile: openContextRef.current.isMobile,
        reason: "external_edit",
      });
      toast.error(translateRef.current("editor:aiEditedWhileDirty"));
    },
    [adoptServerCopy, currentUserId, keepLocalOnServerCopy, openConflictReview, queryClient],
  );

  const syncOpenFileFromServer = useCallback(async () => {
    const opened = currentFileRef.current;
    if (!opened || selectedIdRef.current !== opened.id || isFileRemoved(activeProjectIdRef.current, opened.id)) {
      return;
    }
    const fileId = opened.id;
    const generation = loadGenerationRef.current;
    const syncSeq = ++serverSyncSeqRef.current;
    const isStillOpen = () =>
      generation === loadGenerationRef.current &&
      currentFileRef.current?.id === fileId &&
      selectedIdRef.current === fileId;
    let data: File | null = null;
    for (let attempt = 0; data === null; attempt += 1) {
      try {
        data = await fileApi.get(fileId);
      } catch (err) {
        const failure = err as { status?: number; errorCode?: string };
        if (failure?.status === 404 || failure?.errorCode === "ERR_FILE_NOT_FOUND") {
          if (isStillOpen() && activeProjectIdRef.current) {
            notifyFileRemoved(activeProjectIdRef.current, fileId);
          }
          return;
        }
        if (attempt >= SERVER_SYNC_RETRY_DELAYS_MS.length) {
          logger.warn("Failed to re-read file after an external write:", err);
          if (isStillOpen()) setServerSyncIssue({ fileId, retrying: false });
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, SERVER_SYNC_RETRY_DELAYS_MS[attempt]));
        if (!isStillOpen()) return;
      }
    }
    applyServerCopyToOpenFile(data, syncSeq, generation);
  }, [applyServerCopyToOpenFile]);
  syncOpenFileFromServerRef.current = syncOpenFileFromServer;

  // Reconcile an SWR-painted open file when fresh /bootstrap returns without a duplicate GET.
  // Never rolls back a newer saved or server-synced version.
  useEffect(() => {
    const onFreshFile = (event: Event) => {
      const detail = (event as CustomEvent<WorkbenchFreshFileDetail>).detail;
      if (!detail || detail.userId !== currentUserId || detail.projectId !== activeProjectIdRef.current) {
        return;
      }
      const current = currentFileRef.current;
      if (!current || current.id !== detail.file.id || selectedIdRef.current !== detail.file.id) {
        return;
      }
      consumePreloadedFile(queryClient, currentUserId, detail.projectId, detail.file.id);
      const loadedAt = initialLoadSyncSeqRef.current;
      if (!loadedAt || loadedAt.fileId !== current.id || serverSyncSeqRef.current !== loadedAt.seq) {
        return;
      }
      const incomingMs = Date.parse(detail.file.updated_at);
      const currentMs = Date.parse(current.updated_at);
      if (Number.isFinite(incomingMs) && Number.isFinite(currentMs) && incomingMs < currentMs) {
        return;
      }
      const generation = loadGenerationRef.current;
      const syncSeq = ++serverSyncSeqRef.current;
      applyServerCopyToOpenFile(detail.file, syncSeq, generation);
    };
    const onBootstrapFailed = (event: Event) => {
      const detail = (event as CustomEvent<WorkbenchBootstrapFailedDetail>).detail;
      if (!detail || detail.userId !== currentUserId || detail.projectId !== activeProjectIdRef.current) {
        return;
      }
      const current = currentFileRef.current;
      if (!current || current.id !== detail.fileId || selectedIdRef.current !== detail.fileId) {
        return;
      }
      consumeWorkbenchSwrSyncRequired(queryClient, currentUserId, detail.projectId, detail.fileId);
      const loadedAt = initialLoadSyncSeqRef.current;
      if (!loadedAt || loadedAt.fileId !== current.id || serverSyncSeqRef.current !== loadedAt.seq) {
        return;
      }
      void syncOpenFileFromServerRef.current();
    };
    window.addEventListener(WORKBENCH_FRESH_FILE_EVENT, onFreshFile);
    window.addEventListener(WORKBENCH_BOOTSTRAP_FAILED_EVENT, onBootstrapFailed);
    return () => {
      window.removeEventListener(WORKBENCH_FRESH_FILE_EVENT, onFreshFile);
      window.removeEventListener(WORKBENCH_BOOTSTRAP_FAILED_EVENT, onBootstrapFailed);
    };
  }, [applyServerCopyToOpenFile, currentUserId, queryClient]);

  const retryServerSync = useCallback(() => {
    const fileId = currentFileRef.current?.id;
    if (!fileId) return;
    setServerSyncIssue({ fileId, retrying: true });
    void syncOpenFileFromServer();
  }, [syncOpenFileFromServer]);

  // A file the AI (or an undo) wrote may now have an AI version: check it again when opened.
  useEffect(() => {
    if (lastEditedFileId && editorRefreshVersion > 0) {
      rearmAiFileOpened(currentUserId, lastEditedFileId);
    }
  }, [editorRefreshVersion, lastEditedFileId, currentUserId]);

  // Re-read the open file when the AI (or an undo) wrote it
  useEffect(() => {
    if (
      lastEditedFileId &&
      lastEditedFileId === file?.id &&
      lastEditedFileId === selectedItem?.id &&
      editorRefreshVersion > 0
    ) {
      const timer = setTimeout(() => {
        void syncOpenFileFromServer();
      }, 100);
      return () => clearTimeout(timer);
    }
  }, [editorRefreshVersion, lastEditedFileId, file?.id, selectedItem?.id, syncOpenFileFromServer]);

  const handleSaveFile = async (submission: SaveSubmission): Promise<SaveResult> => {
    if (!submission.fileId || isFileRemoved(currentProjectId, submission.fileId)) {
      return { outcome: "failed" };
    }

    const targetFileId = submission.fileId;
    const titleChanged = submission.title !== submission.previousTitle;

    let updated;
    try {
      updated = await fileApi.update(targetFileId, {
        ...(titleChanged || !isFileTitleTooLong(submission.title) ? { title: submission.title } : {}),
        content: submission.content,
        base_updated_at: submission.baseUpdatedAt,
        ...submission.versionIntent,
      });
    } catch (saveError) {
      if (isFileRemoved(currentProjectId, targetFileId)) return { outcome: "failed" };
      if (
        saveError instanceof ApiError &&
        (saveError.status === 404 || saveError.errorCode === "ERR_FILE_NOT_FOUND")
      ) {
        if (currentProjectId && selectedIdRef.current === targetFileId) {
          notifyFileRemoved(currentProjectId, targetFileId);
        }
        return { outcome: "failed" };
      }
      if (saveError instanceof ApiError && saveError.errorCode === "ERR_FILE_TITLE_TOO_LONG") {
        if (longTitleWarnedRef.current !== submission.title) {
          longTitleWarnedRef.current = submission.title;
          toast.error(t("editor:fileTree.titleTooLong"));
        }
        return { outcome: "failed" };
      }
      if (
        saveError instanceof ApiError &&
        saveError.errorCode === "ERR_QUOTA_FILE_VERSIONS_EXCEEDED"
      ) {
        if (fileVersionUpgradePrompt.surface === "modal") {
          setShowFileVersionUpgradeModal(true);
        } else {
          toast.error(handleApiError(saveError));
        }
        return { outcome: "failed" };
      }
      if (
        saveError instanceof ApiError &&
        saveError.status === 409 &&
        saveError.details?.reason === "stale_write"
      ) {
        const saveSurface =
          submission.versionIntent?.change_type === "auto_save" ||
          submission.versionIntent?.skip_version
            ? "auto_save"
            : "manual";
        trackEditorSaveConflict409({
          surface: saveSurface,
          isMobile: openContextRef.current.isMobile,
          reason: "stale_write",
        });
        const currentContent = saveError.details.current_content;
        const currentUpdatedAt = saveError.details.current_updated_at;
        const localContent = submission.content;
        const openedFile = currentFileRef.current;
        const isStillOpen =
          activeProjectIdRef.current !== null &&
          openedFile?.id === targetFileId &&
          selectedIdRef.current === targetFileId;
        if (typeof currentContent === "string") {
          const openConflict = conflictReviewRef.current;
          const queuedBehindConflict =
            openConflict?.fileId === targetFileId &&
            isSameReview(currentReviewRef.current, openConflict);
          const baseContent = queuedBehindConflict
            ? openConflict.base
            : openedFile?.id === targetFileId
              ? openedFile.content || ""
              : currentContent;
          const nextFile = (prev: File) => ({
            ...prev,
            content: currentContent,
            updated_at:
              typeof currentUpdatedAt === "string" ? currentUpdatedAt : prev.updated_at,
          });
          if (openedFile?.id === targetFileId) {
            const refreshedServerCopy = nextFile(openedFile);
            currentFileRef.current = refreshedServerCopy;
            consumePreloadedFile(
              queryClient,
              currentUserId,
              refreshedServerCopy.project_id,
              targetFileId,
            );
            updateWorkbenchSwrInitialFile(
              currentUserId,
              refreshedServerCopy.project_id,
              refreshedServerCopy,
            );
          }
          setFile((prev) => (prev?.id === targetFileId ? nextFile(prev) : prev));
          if (currentContent !== localContent) {
            const outcome =
              isStillOpen && openedFile
                ? openConflictReview(targetFileId, baseContent, currentContent, localContent)
                : null;
            if (outcome === "server" && openedFile) {
              adoptServerCopy(nextFile(openedFile), editTitleRef.current);
              return { outcome: "conflict" };
            }
            if (outcome === "local" && openedFile) {
              keepLocalOnServerCopy(nextFile(openedFile), editTitleRef.current);
              return { outcome: "conflict" };
            }
            if (outcome === "review") {
              trackEditorDraftRecoveryShown({
                surface: "conflict_review",
                isMobile: openContextRef.current.isMobile,
                reason: "stale_write",
              });
            }
            toast.error(t("editor:saveStaleWriteConflict"));
            return { outcome: "conflict" };
          }
        }
        toast.error(t("editor:saveStaleWrite"));
        return { outcome: "conflict" };
      }
      throw saveError;
    }

    if (isFileRemoved(currentProjectId, targetFileId)) return { outcome: "failed" };

    if (
      updated?.version_quota_exceeded &&
      claimAutoUpgradePrompt(currentUserId, fileVersionUpgradePrompt.source)
    ) {
      if (fileVersionUpgradePrompt.surface === "modal") {
        setShowFileVersionUpgradeModal(true);
      } else {
        toast.error(t("editor:saveVersionQuotaExceeded"));
      }
    }

    const currentTargetFile = currentFileRef.current;
    const savedSnapshot: File | null =
      currentTargetFile?.id === targetFileId
        ? {
            ...currentTargetFile,
            title: submission.title,
            content: submission.content,
            updated_at: updated?.updated_at ?? currentTargetFile.updated_at,
          }
        : updated && typeof updated.id === "string"
          ? {
              ...updated,
              title: submission.title,
              content: submission.content,
            }
          : null;
    if (currentTargetFile?.id === targetFileId && savedSnapshot) {
      serverSyncSeqRef.current += 1;
      currentFileRef.current = savedSnapshot;
    }
    const savedProjectId = updated?.project_id ?? savedSnapshot?.project_id ?? currentProjectId;
    if (savedProjectId) {
      consumePreloadedFile(queryClient, currentUserId, savedProjectId, targetFileId);
    }
    if (currentUserId && savedProjectId) {
      if (savedSnapshot) {
        updateWorkbenchSwrInitialFile(currentUserId, savedProjectId, savedSnapshot);
      } else {
        invalidateWorkbenchSwrInitialFile(currentUserId, savedProjectId, targetFileId);
      }
    }
    setFile((prev) => {
      if (prev?.id !== targetFileId) return prev;
      return {
        ...prev,
        title: submission.title,
        content: submission.content,
        updated_at: updated?.updated_at ?? prev.updated_at,
      };
    });

    if (titleChanged) {
      triggerFileTreeRefresh();
      if (
        activeProjectIdRef.current === updated.project_id &&
        selectedIdRef.current === targetFileId &&
        currentFileRef.current?.id === targetFileId &&
        selectedItem?.id === targetFileId
      ) {
        setSelectedItem({ ...selectedItem, title: submission.title });
      }
    }
    if (
      draftRecovery?.snapshot.fileId === targetFileId &&
      draftRecovery.applied &&
      currentUserId &&
      currentProjectId
    ) {
      const scope = {
        userId: currentUserId,
        projectId: currentProjectId,
        fileId: targetFileId,
      };
      const hasMemoryRemoved = removedDraftsRef.current.some(
        (item) =>
          item.snapshot.userId === scope.userId &&
          item.snapshot.projectId === scope.projectId &&
          item.snapshot.fileId === scope.fileId,
      );
      const cleared = clearEditorDraftSnapshot(localStorage, scope);
      clearRemovedDraftEntry(scope);
      if (cleared || hasMemoryRemoved) {
        if (draftRecovery.kind === "recover") {
          trackEditorDraftRecoveryResolved({
            surface: "draft_recovery",
            isMobile: openContextRef.current.isMobile,
            reason: "recover",
            resolution: "restore_local",
          });
        }
        setDraftRecovery(null);
      }
    }
    notifyEditorContentSaved(updated?.project_id ?? currentFileRef.current?.project_id ?? "");
    return { outcome: "saved", updatedAt: updated.updated_at };
  };

  const applyRecoveredDraft = useCallback(() => {
    if (!draftRecovery) return;
    trackEditorDraftRecoveryResolved({
      surface: "draft_recovery",
      isMobile: openContextRef.current.isMobile,
      reason: draftRecovery.kind,
      resolution: "restore_local",
    });
    setEditTitle(draftRecovery.snapshot.title);
    setEditContent(draftRecovery.snapshot.content);
    setDraftRecovery((current) => (current ? { ...current, applied: true } : null));
  }, [draftRecovery]);

  const discardRecoveredDraft = useCallback(() => {
    if (!draftRecovery || !currentUserId || !currentProjectId) return;
    const scope = {
      userId: currentUserId,
      projectId: currentProjectId,
      fileId: draftRecovery.snapshot.fileId,
    };
    const cleared = clearEditorDraftSnapshot(localStorage, scope);
    const hasMemoryRemoved = removedDraftsRef.current.some(
      (item) =>
        item.snapshot.userId === scope.userId &&
        item.snapshot.projectId === scope.projectId &&
        item.snapshot.fileId === scope.fileId,
    );
    if (!cleared && !hasMemoryRemoved) return;
    trackEditorDraftRecoveryResolved({
      surface: "draft_recovery",
      isMobile: openContextRef.current.isMobile,
      reason: draftRecovery.kind,
      resolution: draftRecovery.applied ? "discard" : "use_server",
    });
    clearRemovedDraftEntry(scope);
    setEditTitle(draftRecovery.serverTitle);
    setEditContent(draftRecovery.serverContent);
    setDraftRecovery(null);
  }, [draftRecovery, currentUserId, currentProjectId, clearRemovedDraftEntry]);

  return {
    file,
    setFile,
    loading,
    error,
    editTitle,
    setEditTitle,
    editContent,
    setEditContent,
    serverBaseline,
    serverSyncIssue,
    removedDrafts,
    draftRecovery,
    setDraftRecovery,
    showFileVersionUpgradeModal,
    setShowFileVersionUpgradeModal,
    loadData,
    handleSaveFile,
    applyRecoveredDraft,
    discardRecoveredDraft,
    retryServerSync,
    registerEditorFlush,
    getReviewLeaveDraft,
    adoptServerCopy,
    markServerBaseline,
    activeProjectIdRef,
    selectedIdRef,
    currentFileRef,
    currentReviewRef,
    editTitleRef,
    serverSyncSeqRef,
    openContextRef,
  };
}
