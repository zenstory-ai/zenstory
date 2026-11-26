import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { DiffReviewState } from "../../types";
import { countWords } from "../../lib/documentChunker";
import { logger } from "../../lib/logger";
import {
  BEFORE_CHUNK_RELOAD_EVENT,
  createEditorDraftSnapshot,
  writeEditorDraftSnapshot,
} from "../../lib/editorDraftRecovery";
import { trackEditorSave } from "../../lib/editorSaveTracker";
import { isFileRemoved } from "../../lib/fileRemovalEvents";
import type {
  EditorRecoveredDraft,
  EditorServerBaseline,
  SaveOutcome,
  SaveResult,
  SaveSubmission,
} from "./types";

interface UseEditorPersistenceOptions {
  userId?: string;
  fileId?: string;
  projectId?: string;
  baseUpdatedAt?: string;
  savedContent?: string;
  title: string;
  content: string;
  onTitleChange: (title: string) => void;
  onContentChange: (content: string) => void;
  onSave: (submission: SaveSubmission) => Promise<SaveResult>;
  onHistoryRestore?: () => Promise<void>;
  onFlushReady?: (flush: (() => Promise<SaveOutcome>) | null) => void;
  serverBaseline?: EditorServerBaseline;
  getReviewLeaveDraft?: () => { title: string; content: string; baseUpdatedAt?: string } | null;
  isStreaming: boolean;
  isAiEditing: boolean;
  isReviewMode: boolean;
  diffReviewState?: DiffReviewState | null;
  recoveredDraft?: EditorRecoveredDraft;
  showVersionHistory: boolean;
  onFileSwitch: (didFileChange: boolean) => void;
  onReviewExitHeightAdjust: () => void;
}

export function useEditorPersistence({
  userId,
  fileId,
  projectId,
  baseUpdatedAt,
  savedContent,
  title,
  content,
  onTitleChange,
  onContentChange,
  onSave,
  onHistoryRestore,
  onFlushReady,
  serverBaseline,
  getReviewLeaveDraft,
  isStreaming,
  isAiEditing,
  isReviewMode,
  diffReviewState,
  recoveredDraft,
  showVersionHistory,
  onFileSwitch,
  onReviewExitHeightAdjust,
}: UseEditorPersistenceOptions) {
  const isReviewModeRef = useRef(isReviewMode);
  useLayoutEffect(() => {
    isReviewModeRef.current = isReviewMode;
  }, [isReviewMode]);
  const [isSaving, setIsSaving] = useState(false);
  const [lastSaved, setLastSaved] = useState<Date | null>(null);
  const [isDirty, setIsDirty] = useState(false);
  // Bumped when a new server baseline leaves the draft unsaved, so the
  // autosave is scheduled again on the new token (see the baseline effect).
  const [autosaveNudge, setAutosaveNudge] = useState(0);

  const saveTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastSavedContentRef = useRef<string>(content);
  const lastSavedTitleRef = useRef<string>(title);
  const latestContentRef = useRef(content);
  const latestTitleRef = useRef(title);
  const previousFileIdRef = useRef(fileId);
  const pendingBaselineSyncRef = useRef(false);
  const pendingHistoryRestoreTokenRef = useRef<string | undefined | null>(null);
  const handleSaveRef = useRef<(submission?: SaveSubmission) => Promise<SaveOutcome>>(
    async () => "failed"
  );
  const latestFileIdRef = useRef<string | undefined>(fileId);
  const latestBaseUpdatedAtRef = useRef(baseUpdatedAt);
  const dirtyRef = useRef(false);
  const pendingSaveCountRef = useRef(0);
  const saveChainRef = useRef<Promise<unknown>>(Promise.resolve());
  const activeSaveRef = useRef<{ key: string; promise: Promise<SaveOutcome> } | null>(null);
  const draftRef = useRef({ fileId, title, content, baseUpdatedAt });
  // Only clean boundaries or this queue's confirmed PUT advance the draft token.
  const persistedBaseRef = useRef({ fileId, updatedAt: baseUpdatedAt });
  const isMountedRef = useRef(true);
  const suppressRecoveredAutoSaveRef = useRef(false);
  const appliedRecoveryRef = useRef<string | null>(null);

  latestContentRef.current = content;
  latestTitleRef.current = title;
  const draftScopeRef = useRef({ userId, projectId });
  draftScopeRef.current = { userId, projectId };
  latestFileIdRef.current = fileId;
  latestBaseUpdatedAtRef.current = baseUpdatedAt;
  const reviewLeaveDraftRef = useRef(getReviewLeaveDraft);
  reviewLeaveDraftRef.current = getReviewLeaveDraft;

  useEffect(() => {
    if (!dirtyRef.current && draftRef.current.fileId === fileId) {
      draftRef.current = { fileId, title, content, baseUpdatedAt };
      persistedBaseRef.current = { fileId, updatedAt: baseUpdatedAt };
    }
  }, [fileId, title, content, baseUpdatedAt]);

  const appliedServerBaselineRef = useRef(serverBaseline?.version ?? 0);
  useEffect(() => {
    if (!serverBaseline || serverBaseline.version === appliedServerBaselineRef.current) return;
    appliedServerBaselineRef.current = serverBaseline.version;
    if (serverBaseline.fileId !== fileId) return;
    if (saveTimeoutRef.current) {
      clearTimeout(saveTimeoutRef.current);
      saveTimeoutRef.current = null;
    }
    // 编辑器换成了服务端的新版本：草稿、保存令牌、字数基线都要跟着换，
    // 否则下一次保存会带着旧令牌和旧草稿发出（撞 409 后把旧稿当成提案），
    // 或把 AI 写的字算成作者新增的字数。
    lastSavedContentRef.current = serverBaseline.content;
    lastSavedTitleRef.current = serverBaseline.title;
    persistedBaseRef.current = { fileId, updatedAt: serverBaseline.updatedAt };
    draftRef.current = {
      fileId,
      title: latestTitleRef.current,
      content: latestContentRef.current,
      baseUpdatedAt: serverBaseline.updatedAt,
    };
    pendingBaselineSyncRef.current = false;
    suppressRecoveredAutoSaveRef.current = false;
    const dirty =
      latestTitleRef.current !== serverBaseline.title ||
      latestContentRef.current !== serverBaseline.content;
    dirtyRef.current = dirty;
    setIsDirty(dirty);
    // Text the author has not saved yet (kept on top of the newer copy):
    // schedule its save again, the pending one was cleared above.
    if (dirty) setAutosaveNudge((n) => n + 1);
  }, [serverBaseline, fileId]);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    if (!userId || !projectId || !fileId) return;
    let lastCapturedDraft: typeof draftRef.current | null = null;
    const captureDirtyDraft = (reason: "chunk-reload" | "page-exit") => {
      // With a comparison open, keep what finishing it would save (see getReviewLeaveDraft).
      const reviewDraft = isReviewModeRef.current ? reviewLeaveDraftRef.current?.() ?? null : null;
      if (!reviewDraft) {
        if (!dirtyRef.current || draftRef.current.fileId !== fileId) return true;
        if (lastCapturedDraft === draftRef.current) return true;
      }
      const currentDraft = reviewDraft ?? draftRef.current;
      const captured = writeEditorDraftSnapshot(
        localStorage,
        createEditorDraftSnapshot({
          userId,
          projectId,
          fileId,
          title: currentDraft.title,
          content: currentDraft.content,
          baseUpdatedAt: currentDraft.baseUpdatedAt,
          reason,
        })
      );
      if (captured && !reviewDraft) lastCapturedDraft = draftRef.current;
      return captured;
    };
    const captureBeforeChunkReload = (event: Event) => {
      if (!captureDirtyDraft("chunk-reload")) event.preventDefault();
    };
    const captureBeforeUnload = (event: BeforeUnloadEvent) => {
      if (captureDirtyDraft("page-exit")) return;
      event.preventDefault();
      event.returnValue = "";
    };
    const capturePageHide = () => {
      captureDirtyDraft("page-exit");
    };
    window.addEventListener(BEFORE_CHUNK_RELOAD_EVENT, captureBeforeChunkReload);
    window.addEventListener("beforeunload", captureBeforeUnload);
    window.addEventListener("pagehide", capturePageHide);
    return () => {
      window.removeEventListener(BEFORE_CHUNK_RELOAD_EVENT, captureBeforeChunkReload);
      window.removeEventListener("beforeunload", captureBeforeUnload);
      window.removeEventListener("pagehide", capturePageHide);
    };
  }, [userId, projectId, fileId]);

  // When switching files, flush the previous dirty draft and reset baselines.
  useEffect(() => {
    const didFileChange = previousFileIdRef.current !== fileId;
    previousFileIdRef.current = fileId;

    if (didFileChange && dirtyRef.current) {
      const previousDraft = draftRef.current;
      void handleSaveRef.current({
        ...previousDraft,
        previousTitle: lastSavedTitleRef.current,
      });
    }

    draftRef.current = {
      fileId,
      title: latestTitleRef.current,
      content: latestContentRef.current,
      baseUpdatedAt: latestBaseUpdatedAtRef.current,
    };

    persistedBaseRef.current = { fileId, updatedAt: latestBaseUpdatedAtRef.current };
    // Reset save/dirty state for the new file and sync baseline on first content load.
    setIsDirty(false);
    dirtyRef.current = false;
    setLastSaved(null);
    lastSavedContentRef.current = latestContentRef.current;
    lastSavedTitleRef.current = latestTitleRef.current;
    pendingBaselineSyncRef.current = true;
    pendingHistoryRestoreTokenRef.current = null;
    if (saveTimeoutRef.current) {
      clearTimeout(saveTimeoutRef.current);
      saveTimeoutRef.current = null;
    }

    onFileSwitch(didFileChange);
  }, [fileId, onFileSwitch]);

  useEffect(() => {
    if (!recoveredDraft || !fileId) {
      if (appliedRecoveryRef.current === null) return;
      appliedRecoveryRef.current = null;
      lastSavedTitleRef.current = title;
      lastSavedContentRef.current = content;
      draftRef.current = { fileId, title, content, baseUpdatedAt };
      persistedBaseRef.current = { fileId, updatedAt: baseUpdatedAt };
      suppressRecoveredAutoSaveRef.current = false;
      dirtyRef.current = false;
      setIsDirty(false);
      return;
    }
    const recoveryKey = `${fileId}:${recoveredDraft.capturedAt}`;
    if (appliedRecoveryRef.current === recoveryKey) return;
    appliedRecoveryRef.current = recoveryKey;
    lastSavedTitleRef.current = recoveredDraft.serverTitle;
    lastSavedContentRef.current = recoveredDraft.serverContent;
    draftRef.current = { fileId, title, content, baseUpdatedAt };
    persistedBaseRef.current = { fileId, updatedAt: baseUpdatedAt };
    pendingBaselineSyncRef.current = false;
    suppressRecoveredAutoSaveRef.current = true;
    dirtyRef.current = true;
    setIsDirty(true);
  }, [recoveredDraft, fileId, title, content, baseUpdatedAt]);

  // Some file switches load content asynchronously after fileId changes.
  // Sync baseline once when the new content arrives so dirty/version diff is correct.
  useEffect(() => {
    if (!pendingBaselineSyncRef.current) return;
    if (isDirty) return;
    if (pendingHistoryRestoreTokenRef.current !== null) return;
    lastSavedContentRef.current = content;
    pendingBaselineSyncRef.current = false;
  }, [content, isDirty]);

  // A history restore has a separate server-token boundary. Don't consume the
  // old draft while its refresh is pending or alter async file-switch syncing.
  useEffect(() => {
    if (pendingHistoryRestoreTokenRef.current === null || isDirty) return;
    if (baseUpdatedAt === pendingHistoryRestoreTokenRef.current) return;
    lastSavedContentRef.current = content;
    lastSavedTitleRef.current = title;
    pendingBaselineSyncRef.current = false;
    pendingHistoryRestoreTokenRef.current = null;
    persistedBaseRef.current = { fileId, updatedAt: baseUpdatedAt };
  }, [content, title, fileId, isDirty, baseUpdatedAt]);

  // Re-adjust textarea height and sync baseline when exiting review mode
  const prevReviewModeRef = useRef(isReviewMode);
  useEffect(() => {
    const wasInReviewMode = prevReviewModeRef.current;
    prevReviewModeRef.current = isReviewMode;

    if (wasInReviewMode && !isReviewMode) {
      // The review closed. When it saved the editor's text (it now matches the saved copy),
      // sync the local save baseline so "unsaved" status doesn't get stuck. A review that
      // closed without saving it (the author's unsaved text was kept) leaves it unsaved:
      // autosave, the leave flush and the page-exit draft still cover it.
      if (!diffReviewState && savedContent !== undefined && content !== savedContent) {
        dirtyRef.current = true;
        setIsDirty(true);
        setAutosaveNudge((n) => n + 1);
      } else if (!diffReviewState) {
        lastSavedContentRef.current = content;
        lastSavedTitleRef.current = title;
        dirtyRef.current = false;
        persistedBaseRef.current = { fileId, updatedAt: baseUpdatedAt };
        draftRef.current = { fileId, title, content, baseUpdatedAt };
        setIsDirty(false);
        setLastSaved(new Date());
      }

      setTimeout(() => {
        onReviewExitHeightAdjust();
      }, 50);
    }
  }, [
    isReviewMode,
    diffReviewState,
    fileId,
    title,
    content,
    baseUpdatedAt,
    savedContent,
    onReviewExitHeightAdjust,
  ]);

  // Auto-save with debounce
  useEffect(() => {
    if (saveTimeoutRef.current) {
      clearTimeout(saveTimeoutRef.current);
      saveTimeoutRef.current = null;
    }
    if (!isDirty || showVersionHistory) return;
    if (suppressRecoveredAutoSaveRef.current) return;
    // AI 正在改这份文件：先不排自动保存。标记清除后本 effect 会重新跑，
    // 届时再按新的基线保存，用户的本地改动不会丢。
    if (isAiEditing) return;
    // AI 正在把正文流式写进这份文件（<file> 整章替换 / 新正文）：同样先不排自动保存。
    // 离开 / 切换文件时的最后一次保存（onFlushReady）不受影响。
    if (isStreaming) return;
    // 对比审阅进行中：由作者在审阅里决定保留哪一份，审阅完成时整体写回。
    if (isReviewMode) return;

    saveTimeoutRef.current = setTimeout(async () => {
      await handleSaveRef.current();
    }, 3000);

    return () => {
      if (saveTimeoutRef.current) {
        clearTimeout(saveTimeoutRef.current);
        saveTimeoutRef.current = null;
      }
    };
  }, [isDirty, title, content, isAiEditing, isStreaming, showVersionHistory, isReviewMode, autosaveNudge]);

  // Handle save
  const handleSave = async (providedSubmission?: SaveSubmission): Promise<SaveOutcome> => {
    if (
      isFileRemoved(
        draftScopeRef.current.projectId,
        providedSubmission?.fileId ?? draftRef.current.fileId
      )
    ) {
      return "failed";
    }
    if ((!dirtyRef.current && !providedSubmission) || isAiEditing || showVersionHistory) {
      return "failed";
    }

    const draft: SaveSubmission = providedSubmission ?? {
      ...draftRef.current,
      previousTitle: lastSavedTitleRef.current,
    };
    // Retain the old-file baseline when a switch flushes its immutable draft.
    const queuedBaselineContent = lastSavedContentRef.current;
    const queuedBaselineTitle = lastSavedTitleRef.current;
    const queuedBaseUpdatedAt =
      persistedBaseRef.current.fileId === draft.fileId
        ? persistedBaseRef.current.updatedAt
        : draft.baseUpdatedAt;
    const submissionKey = JSON.stringify([
      draft.fileId,
      draft.title,
      draft.content,
      draft.baseUpdatedAt,
    ]);
    if (activeSaveRef.current?.key === submissionKey) {
      return activeSaveRef.current.promise;
    }

    pendingSaveCountRef.current += 1;
    if (isMountedRef.current) setIsSaving(true);
    const execute = async (): Promise<SaveOutcome> => {
      if (isFileRemoved(draftScopeRef.current.projectId, draft.fileId)) return "failed";
      // A preceding successful save advances this baseline. Resolve it only
      // when this immutable draft reaches the front of the serialized queue.
      const isCurrentFile = latestFileIdRef.current === draft.fileId;
      const previousContent = isCurrentFile ? lastSavedContentRef.current : queuedBaselineContent;
      const previousTitle = isCurrentFile ? lastSavedTitleRef.current : queuedBaselineTitle;
      const contentChanged = draft.content !== previousContent;
      let versionIntent = draft.versionIntent;
      if (!versionIntent && contentChanged) {
        const wordCount = countWords(draft.content);
        versionIntent =
          Math.abs(draft.content.length - previousContent.length) > 10
            ? { change_type: "edit", change_source: "user", word_count: wordCount }
            : { skip_version: true, word_count: wordCount };
      }
      const submission: SaveSubmission = {
        ...draft,
        previousTitle,
        versionIntent,
        baseUpdatedAt:
          persistedBaseRef.current.fileId === draft.fileId
            ? persistedBaseRef.current.updatedAt
            : queuedBaseUpdatedAt,
      };
      const result = await onSave(submission);
      if (result.outcome !== "saved") {
        // Conflict/failure paths deliberately keep the old baseline, dirty
        // indicator and pending local edits. The parent may have opened a
        // diff review, but no save has completed yet.
        return result.outcome;
      }

      if (persistedBaseRef.current.fileId === submission.fileId) {
        persistedBaseRef.current = { fileId: submission.fileId, updatedAt: result.updatedAt };
      }
      if (latestFileIdRef.current === submission.fileId) {
        lastSavedContentRef.current = submission.content;
        lastSavedTitleRef.current = submission.title;
      }

      if (
        latestFileIdRef.current === submission.fileId &&
        latestTitleRef.current === submission.title &&
        latestContentRef.current === submission.content
      ) {
        dirtyRef.current = false;
        if (isMountedRef.current) {
          setIsDirty(false);
          setLastSaved(new Date());
        }
      } else if (latestFileIdRef.current === submission.fileId) {
        dirtyRef.current = true;
        if (isMountedRef.current) setIsDirty(true);
      }
      return "saved";
    };

    const queued = saveChainRef.current.then(execute, execute);
    saveChainRef.current = queued.catch(() => undefined);
    const result = (async (): Promise<SaveOutcome> => {
      try {
        return await queued;
      } catch (error) {
        logger.error("Failed to save:", error);
        return "failed";
      } finally {
        if (activeSaveRef.current?.key === submissionKey) activeSaveRef.current = null;
        pendingSaveCountRef.current -= 1;
        if (pendingSaveCountRef.current === 0 && isMountedRef.current) setIsSaving(false);
      }
    })();
    activeSaveRef.current = { key: submissionKey, promise: result };
    // 离开编辑器时的最后一次保存在卸载时才发出；工作台据此先等它落库再拉进度。
    trackEditorSave(result);
    return result;
  };

  handleSaveRef.current = handleSave;

  useEffect(() => {
    if (!onFlushReady) return;
    onFlushReady(async () => {
      if (!dirtyRef.current) return "saved";
      if (suppressRecoveredAutoSaveRef.current) return "failed";
      return handleSaveRef.current();
    });
    return () => {
      onFlushReady(null);
      // Editor has already synchronously retained the deleted file's draft.
      // Never PUT it, or overwrite that recovery snapshot during unmount.
      if (isFileRemoved(draftScopeRef.current.projectId, draftRef.current.fileId)) return;
      // 对比开着时，留下的应是「现在完成对比会保存的内容」（基于服务端新版本和它的令牌），
      // 而不是作者在旧版本上的整篇草稿：恢复那份草稿会把 AI 的新版本改回去。
      const reviewDraft = isReviewModeRef.current ? reviewLeaveDraftRef.current?.() ?? null : null;
      if (!dirtyRef.current && !reviewDraft) return;
      // 离开编辑器时最后一次保存。没保存成功（例如对比还开着、令牌已过期而撞 409）
      // 就把草稿写进本地快照，下次打开这份文件时会提示恢复，作者的字不会丢。
      const draft = reviewDraft ? { ...draftRef.current, ...reviewDraft } : draftRef.current;
      const { userId: ownerId, projectId: draftProjectId } = draftScopeRef.current;
      const keepDraftLocally = () => {
        if (!ownerId || !draftProjectId || !draft.fileId) return;
        writeEditorDraftSnapshot(
          localStorage,
          createEditorDraftSnapshot({
            userId: ownerId,
            projectId: draftProjectId,
            fileId: draft.fileId,
            title: draft.title,
            content: draft.content,
            baseUpdatedAt: draft.baseUpdatedAt,
            reason: "page-exit",
          })
        );
      };
      // 对比审阅或恢复后尚未编辑/保存的草稿只留本地，不在退出时自动写回。
      if (isReviewModeRef.current || suppressRecoveredAutoSaveRef.current) {
        keepDraftLocally();
        return;
      }
      void handleSaveRef.current().then((outcome) => {
        if (outcome !== "saved") keepDraftLocally();
      });
    };
  }, [onFlushReady]);

  const markTitleChanged = useCallback(
    (nextTitle: string) => {
      suppressRecoveredAutoSaveRef.current = false;
      draftRef.current = { ...draftRef.current, title: nextTitle };
      onTitleChange(nextTitle);
      dirtyRef.current = true;
      setIsDirty(true);
    },
    [onTitleChange]
  );

  const markContentChanged = useCallback(
    (nextContent: string) => {
      suppressRecoveredAutoSaveRef.current = false;
      draftRef.current = { ...draftRef.current, content: nextContent };
      onContentChange(nextContent);
      dirtyRef.current = true;
      setIsDirty(true);
    },
    [onContentChange]
  );

  const prepareHistoryRollback = useCallback(async () => {
    if (saveTimeoutRef.current) {
      clearTimeout(saveTimeoutRef.current);
      saveTimeoutRef.current = null;
    }
    // Finish writes already queued before the dialog opened; don't flush an
    // unscheduled dirty draft that the restore confirmation will discard.
    await saveChainRef.current;
  }, []);

  const handleRollback = useCallback(async () => {
    if (saveTimeoutRef.current) {
      clearTimeout(saveTimeoutRef.current);
      saveTimeoutRef.current = null;
    }
    dirtyRef.current = false;
    setIsDirty(false);
    pendingBaselineSyncRef.current = true;
    pendingHistoryRestoreTokenRef.current = latestBaseUpdatedAtRef.current;
    // Refresh through the existing file owner without unmounting history's
    // quota prompt or clearing the omission toast with a page reload.
    await onHistoryRestore?.();
  }, [onHistoryRestore]);

  return {
    isSaving,
    lastSaved,
    isDirty,
    dirtyRef,
    suppressRecoveredAutoSaveRef,
    latestContentRef,
    latestTitleRef,
    latestFileIdRef,
    isMountedRef,
    handleSave,
    handleSaveRef,
    markTitleChanged,
    markContentChanged,
    prepareHistoryRollback,
    handleRollback,
  };
}
