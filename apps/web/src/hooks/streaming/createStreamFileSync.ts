/**
 * File-tree, open-editor, and autosave-lock synchronization during AI chat streams.
 *
 * Encapsulates debounced tree refreshes, deletion race checks, AI autosave locking
 * (`aiEditingFileId`), batch edit notifications, and end-of-round open-file sync.
 *
 * @module hooks/streaming/createStreamFileSync
 */

import type { Dispatch, MutableRefObject, SetStateAction } from "react";
import type { FileEditOutcome, FileType } from "../../types";
import { logger } from "../../lib/logger";
import { toast } from "../../lib/toast";
import { fileApi } from "../../lib/api";
import { findAncestorFolderIds } from "../../lib/lastOpenedFile";
import { isFileRemoved, notifyFileRemoved } from "../../lib/fileRemovalEvents";
import { rearmAiFileOpened } from "../../lib/activationEvents";
import { dispatchProjectStatusUpdated } from "../../lib/projectStatusEvents";
import type { EditProgress, UseChatStreamingDependencies } from "./types";

const FILE_TREE_REFRESH_DEBOUNCE_MS = 180;
const AI_MEMORY_STATUS_FIELDS = new Set([
  "summary",
  "current_phase",
  "writing_style",
  "notes",
]);

export interface StreamFileSyncRefs {
  fileContentEndByFileIdRef: MutableRefObject<Set<string>>;
  fileEditMetaByIdRef: MutableRefObject<Map<string, { title: string; fileType?: string }>>;
  /** Tracks which file currently owns the AI autosave lock (`aiEditingFileId`). */
  aiEditingTargetRef: MutableRefObject<string | null>;
  pendingFileTreeRefreshRef: MutableRefObject<boolean>;
  /** Whether the current round has written any file (reset on stream start). */
  roundWroteFilesRef: MutableRefObject<boolean>;
  roundMayDeleteFilesRef: MutableRefObject<boolean>;
  deletionTreeCheckRef: MutableRefObject<ReturnType<typeof fileApi.getTree> | null>;
  fileTreeRefreshTimerRef: MutableRefObject<ReturnType<typeof setTimeout> | null>;
}

export interface StreamFileSyncHandlers {
  resetFileSyncState: () => void;
  cleanupStreamFileState: (params: {
    missingEndReason: string;
    forceRefreshOpenFile: boolean;
  }) => void;
  onToolResult: (
    toolName: string,
    status: string,
    result?: Record<string, unknown>,
    error?: string,
  ) => void;
  onFileCreated: (fileId: string, fileType: string, title: string) => void;
  onFileContent: (fileId: string, chunk: string) => void;
  onFileContentEnd: (fileId: string) => void;
  onFileEditStart: (
    fileId: string,
    title: string,
    totalEdits: number,
    fileType?: string,
  ) => void;
  onFileEditApplied: (editIndex: number, op: string) => void;
  onFileEditEnd: (
    fileId: string,
    editsApplied: number,
    newContent?: string,
    originalContent?: string,
    fileType?: string,
    title?: string,
    outcome?: FileEditOutcome,
  ) => void;
}

export function createStreamFileSyncHandlers(
  refs: StreamFileSyncRefs,
  deps: UseChatStreamingDependencies,
  setEditProgress: Dispatch<SetStateAction<EditProgress | null>>,
): StreamFileSyncHandlers {
  const {
    triggerFileTreeRefresh,
    triggerEditorRefresh,
    setAiEditingFileId,
    setSelectedItem,
    getCurrentSelectedItem,
    appendFileContent,
    finishFileStreaming,
    startFileStreaming,
    getCurrentStreamingFileId,
    activeProjectId,
    currentUserId,
    onProjectRenamed,
    t,
  } = deps;

  const scheduleFileTreeRefresh = (delayMs = FILE_TREE_REFRESH_DEBOUNCE_MS) => {
    refs.pendingFileTreeRefreshRef.current = true;
    if (refs.fileTreeRefreshTimerRef.current) {
      clearTimeout(refs.fileTreeRefreshTimerRef.current);
    }
    refs.fileTreeRefreshTimerRef.current = setTimeout(() => {
      refs.fileTreeRefreshTimerRef.current = null;
      if (!refs.pendingFileTreeRefreshRef.current) return;
      refs.pendingFileTreeRefreshRef.current = false;
      triggerFileTreeRefresh();
    }, delayMs);
  };

  /**
   * Ask the editor to re-read the file the author has open. The editor
   * follows the server copy when it has no unsaved text and opens a
   * comparison otherwise, so this is safe to call after any AI write.
   * The file whose <file> stream is still running is skipped: its own
   * stream-end reload already brings in the saved text.
   */
  const refreshOpenFileFromServer = () => {
    const selected = getCurrentSelectedItem?.() ?? null;
    if (!selected || selected.type === "folder" || isFileRemoved(activeProjectId, selected.id)) return;
    if (refs.roundMayDeleteFilesRef.current && activeProjectId) {
      // Older delete_file results contain only true. Validate the selection
      // against a committed tree before asking the editor to GET this id.
      const pending = refs.deletionTreeCheckRef.current ?? fileApi.getTree(activeProjectId);
      refs.deletionTreeCheckRef.current = pending;
      void pending.then((response) => {
        if (getCurrentSelectedItem?.()?.id !== selected.id || isFileRemoved(activeProjectId, selected.id)) return;
        if (findAncestorFolderIds(response.tree || [], selected.id) === null) {
          notifyFileRemoved(activeProjectId, selected.id);
          if (getCurrentSelectedItem?.()?.id === selected.id) setSelectedItem(null);
        } else {
          triggerEditorRefresh(selected.id);
        }
      }).catch(() => {
        // A failed tree request is not deletion evidence; the editor still
        // distinguishes one terminal 404 from recoverable read failures.
        if (getCurrentSelectedItem?.()?.id === selected.id && !isFileRemoved(activeProjectId, selected.id)) {
          triggerEditorRefresh(selected.id);
        }
      }).finally(() => {
        if (refs.deletionTreeCheckRef.current === pending) refs.deletionTreeCheckRef.current = null;
      });
      return;
    }
    const activeStreamFileId = getCurrentStreamingFileId?.() ?? null;
    if (activeStreamFileId && activeStreamFileId === selected.id) return;
    triggerEditorRefresh(selected.id);
  };

  const flushFileTreeRefresh = () => {
    if (refs.fileTreeRefreshTimerRef.current) {
      clearTimeout(refs.fileTreeRefreshTimerRef.current);
      refs.fileTreeRefreshTimerRef.current = null;
    }
    if (!refs.pendingFileTreeRefreshRef.current) return;
    refs.pendingFileTreeRefreshRef.current = false;
    triggerFileTreeRefresh();
  };

  const resetFileSyncState = () => {
    refs.fileContentEndByFileIdRef.current.clear();
    refs.fileEditMetaByIdRef.current.clear();
    refs.aiEditingTargetRef.current = null;
    refs.roundWroteFilesRef.current = false;
    refs.roundMayDeleteFilesRef.current = false;
    refs.deletionTreeCheckRef.current = null;
    refs.pendingFileTreeRefreshRef.current = false;
    if (refs.fileTreeRefreshTimerRef.current) {
      clearTimeout(refs.fileTreeRefreshTimerRef.current);
      refs.fileTreeRefreshTimerRef.current = null;
    }
  };

  const cleanupStreamFileState = ({
    missingEndReason,
    forceRefreshOpenFile,
  }: {
    missingEndReason: string;
    forceRefreshOpenFile: boolean;
  }) => {
    setEditProgress(null);
    // 流结束兜底：file_edit_end 可能因取消/报错永远不到，
    // 不清掉标记会让编辑器的自动保存被永久挂起。
    refs.aiEditingTargetRef.current = null;
    setAiEditingFileId?.(null);

    const latestStreamingFileId = getCurrentStreamingFileId?.() ?? null;
    if (latestStreamingFileId) {
      if (!refs.fileContentEndByFileIdRef.current.has(latestStreamingFileId)) {
        logger.warn(
          `[Stream] Missing file_content_end for ${latestStreamingFileId}, forcing final refresh ${missingEndReason}`,
        );
        refs.pendingFileTreeRefreshRef.current = true;
      }
      finishFileStreaming(latestStreamingFileId);
      refs.fileContentEndByFileIdRef.current.delete(latestStreamingFileId);
    }
    flushFileTreeRefresh();
    // 收尾兜底：本轮写过文件（或服务端确认有改动）时，让打开着的文件再对一次
    // 服务端版本，保证编辑器停在本轮最后一次写入之后。
    if (refs.roundWroteFilesRef.current || forceRefreshOpenFile) {
      refreshOpenFileFromServer();
    }
  };

  const onToolResult = (
    toolName: string,
    status: string,
    result?: Record<string, unknown>,
    _error?: string,
  ) => {
    if (
      status === "success" &&
      [
        "create_file",
        "delete_file",
        "edit_file",
        "rename_file",
        "write_chapter",
      ].includes(toolName)
    ) {
      // Coalesce multiple tool/file events into one refresh.
      scheduleFileTreeRefresh();
      refs.roundWroteFilesRef.current = true;
    }

    if (status === "success" && toolName === "batch_replace_files") {
      const payload = (result?.data ?? result) as Record<string, unknown> | undefined;
      const isDryRun = payload?.dry_run === true;
      const modifiedFiles = Array.isArray(payload?.files)
        ? (payload.files as Array<Record<string, unknown>>)
        : Array.isArray(payload?.modified_files)
          ? (payload.modified_files as Array<Record<string, unknown>>)
          : [];
      const filesModified = typeof payload?.files_modified === "number"
        ? payload.files_modified
        : typeof payload?.modified_files === "number"
          ? payload.modified_files
          : modifiedFiles.length;
      const mutated = !isDryRun && (payload?.mutation_applied === true || filesModified > 0);
      if (mutated) {
        scheduleFileTreeRefresh();
        refs.roundWroteFilesRef.current = true;
        const selectedId = getCurrentSelectedItem?.()?.id;
        if (
          selectedId &&
          (modifiedFiles.length === 0 ||
            modifiedFiles.some((file) => file?.id === selectedId))
        ) {
          triggerEditorRefresh(selectedId);
        }
      }
    }

    if (status === "success" && toolName === "delete_file") {
      refs.roundMayDeleteFilesRef.current = true;
      const payload = (result?.data ?? result) as Record<string, unknown> | undefined;
      if (payload && typeof payload === "object" && activeProjectId) {
        const id = payload.id ?? payload.file_id;
        if (typeof id === "string") {
          notifyFileRemoved(activeProjectId, id);
          if (getCurrentSelectedItem?.()?.id === id) setSelectedItem(null);
        }
      }
      refreshOpenFileFromServer();
    }

    // 改名不发 file_edit_* 事件：打开着的正是这份文件时重读一次，标题跟上服务端
    //（作者没改过的标题跟随服务端，未保存的正文保留在新的版本令牌上）。
    if (status === "success" && toolName === "rename_file") {
      const renamed = (result?.data ?? result) as Record<string, unknown> | undefined;
      if (typeof renamed?.id === "string" && getCurrentSelectedItem?.()?.id === renamed.id) {
        refreshOpenFileFromServer();
      }
    }

    if (status === "success" && toolName === "update_project") {
      const payload = (result?.data ?? result) as
        | Record<string, unknown>
        | undefined;
      const projectStatus = payload?.project_status as
        | Record<string, unknown>
        | undefined;
      const payloadProjectIdRaw =
        payload?.project_id ?? projectStatus?.project_id;
      const payloadProjectId =
        typeof payloadProjectIdRaw === "string" &&
        payloadProjectIdRaw.trim().length > 0
          ? payloadProjectIdRaw
          : null;
      const targetProjectId = payloadProjectId ?? activeProjectId;

      const projectRenamed =
        payload?.project_name_updated === true ||
        projectStatus?.project_name_updated === true;
      if (projectRenamed && targetProjectId) {
        onProjectRenamed?.(targetProjectId);
      }

      const rawUpdatedFields =
        payload?.updated_fields ?? projectStatus?.updated_fields;
      const updatedFields = Array.isArray(rawUpdatedFields)
        ? rawUpdatedFields.filter(
            (field): field is string => typeof field === "string",
          )
        : [];
      const hasAiMemoryFieldUpdates = updatedFields.some((field) =>
        AI_MEMORY_STATUS_FIELDS.has(field),
      );

      if (!hasAiMemoryFieldUpdates) {
        return;
      }

      if (!targetProjectId) {
        return;
      }

      dispatchProjectStatusUpdated({
        projectId: targetProjectId,
        updatedFields,
      });
    }
  };

  const onFileCreated = (fileId: string, fileType: string, title: string) => {
    // Auto-select newly created file so the editor can show streaming output
    refs.roundWroteFilesRef.current = true;
    scheduleFileTreeRefresh();
    startFileStreaming(fileId);
    refs.fileContentEndByFileIdRef.current.delete(fileId);
    setSelectedItem({ id: fileId, type: fileType as FileType, title });
  };

  const onFileContent = (fileId: string, chunk: string) => {
    // Append content chunk to streaming state
    appendFileContent(fileId, chunk);
  };

  const onFileContentEnd = (fileId: string) => {
    refs.fileContentEndByFileIdRef.current.add(fileId);
    // Always ask ProjectContext to finish the stream for this file.
    // ProjectContext guards mismatched file IDs internally.
    finishFileStreaming(fileId);
    scheduleFileTreeRefresh();
  };

  const onFileEditStart = (fileId: string, title: string, totalEdits: number, fileType?: string) => {
    refs.fileEditMetaByIdRef.current.set(fileId, { title, fileType });
    // 仅当目标文件是当前打开文件（或尚未选中文件）时挂起自动保存，
    // 避免 batch_replace_files 批量回放未打开文件时闪烁 aiEditingFileId。
    const currentSelected = getCurrentSelectedItem?.() ?? null;
    if (!currentSelected || currentSelected.id === fileId) {
      refs.aiEditingTargetRef.current = fileId;
      setAiEditingFileId?.(fileId);
    }
    setEditProgress({
      fileId,
      title,
      totalEdits,
      completedEdits: 0,
    });
  };

  const onFileEditApplied = (editIndex: number, op: string) => {
    setEditProgress((prev) =>
      prev
        ? {
            ...prev,
            completedEdits: editIndex + 1,
            currentOp: op,
          }
        : null,
    );
  };

  const onFileEditEnd = (
    fileId: string,
    editsApplied: number,
    newContent?: string,
    originalContent?: string,
    fileType?: string,
    title?: string,
    outcome?: FileEditOutcome,
  ) => {
    // Clear edit progress and mark tree refresh once.
    refs.roundWroteFilesRef.current = true;
    setEditProgress(null);
    if (
      refs.aiEditingTargetRef.current === fileId
      || (refs.aiEditingTargetRef.current === null && !getCurrentSelectedItem)
    ) {
      refs.aiEditingTargetRef.current = null;
      setAiEditingFileId?.(null);
    }
    scheduleFileTreeRefresh();

    // Direct edit_file can fill a create_file placeholder without <file>
    // chunks or file_content_end. Its committed body now owns the editor.
    if (editsApplied > 0 && !outcome?.allFailed) finishFileStreaming(fileId);

    // 批量编辑可以部分失败：不提示的话用户只看到"编辑完成"，
    // 实际上有若干条 edit 根本没落地（失败原因只藏在 data 里）。
    if (outcome && (outcome.failedCount > 0 || outcome.allFailed)) {
      const displayTitle = title || refs.fileEditMetaByIdRef.current.get(fileId)?.title || "";
      toast.error(
        outcome.allFailed
          ? t("chat:fileEdit.allFailed", {
              title: displayTitle,
              count: outcome.failedCount,
            })
          : t("chat:fileEdit.partialFailed", {
              title: displayTitle,
              count: outcome.failedCount,
            }),
      );
    }
    for (const warning of outcome?.warnings ?? []) {
      if (warning) {
        logger.warn(`[FileEdit] ${fileId}: ${warning}`);
      }
    }

    // Chat edits are already committed and recoverable through the edit card's undo
    // action and file history. Show the latest saved copy directly; the editor's
    // server-sync path still opens a conflict comparison when the author has unsaved text.
    const currentSelected = getCurrentSelectedItem?.() ?? null;
    let switchedSelection = false;
    if (newContent && originalContent && newContent !== originalContent) {
      const trackedMeta = refs.fileEditMetaByIdRef.current.get(fileId);
      const resolvedType =
        (fileType as FileType | undefined)
        || (trackedMeta?.fileType as FileType | undefined)
        || (currentSelected?.id === fileId ? currentSelected.type : undefined)
        || "draft";
      const resolvedTitle =
        title
        || trackedMeta?.title
        || (currentSelected?.id === fileId ? currentSelected.title : "")
        || "";
      setSelectedItem({
        id: fileId,
        type: resolvedType,
        title: resolvedTitle,
      });
      switchedSelection = true;
    }
    // 多文件 batch_replace_files 会连续发出 [f1, f2, ..., fN] 的 file_edit_end；
    // 如果当前已打开 f1，不能让后续未选中的 f2..fN 覆写 lastEditedFileId 而取消 f1 的 100ms 刷新定时器。
    if (!currentSelected || currentSelected.id === fileId || switchedSelection) {
      triggerEditorRefresh(fileId);
    } else {
      rearmAiFileOpened(currentUserId, fileId);
    }
    refs.fileEditMetaByIdRef.current.delete(fileId);
  };

  return {
    resetFileSyncState,
    cleanupStreamFileState,
    onToolResult,
    onFileCreated,
    onFileContent,
    onFileContentEnd,
    onFileEditStart,
    onFileEditApplied,
    onFileEditEnd,
  };
}
