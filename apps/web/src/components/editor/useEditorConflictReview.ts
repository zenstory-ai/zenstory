/**
 * Three-way conflict review session and reviewed-file commit hooks for Editor.
 *
 * Preserves optimistic concurrency (`base_updated_at` 409 -> re-enter conflict
 * review), undecided-conflict leave protection (omitting `baseUpdatedAt`), and
 * SWR cache + draft recovery cleanup on commit.
 *
 * @module components/editor/useEditorConflictReview
 */

import { useCallback, useEffect, useRef, type Dispatch, type MutableRefObject, type SetStateAction } from "react";
import type { QueryClient } from "@tanstack/react-query";
import type { TFunction } from "i18next";
import { fileApi } from "../../lib/api";
import { ApiError } from "../../lib/apiClient";
import { handleApiError } from "../../lib/errorHandler";
import { toast } from "../../lib/toast";
import { logger } from "../../lib/logger";
import { captureException, trackEvent } from "../../lib/analytics";
import { clearEditorDraftSnapshot } from "../../lib/editorDraftRecovery";
import {
  trackEditorDraftRecoveryResolved,
  trackEditorDraftRecoveryShown,
  trackEditorSaveConflict409,
} from "../../lib/editorSafetyTelemetry";
import { notifyEditorContentSaved } from "../../lib/editorSaveTracker";
import { rebaseLocalEdits } from "../../lib/rebaseLocalEdits";
import { applyPendingEditsToDiffs, computeParagraphReviewDiffs } from "../../lib/diffReview";
import { consumePreloadedFile } from "../../lib/preloadedFileCache";
import { updateWorkbenchSwrInitialFile } from "../../lib/workbenchSwrCache";
import type { DiffReviewState, File, FileType, TreeNodeType } from "../../types";
import {
  isSameReview,
  type ConflictReview,
  type ConflictReviewOutcome,
  type DraftRecoveryState,
} from "./editorFileHelpers";

export interface UseConflictReviewSessionOptions {
  currentReviewRef: MutableRefObject<DiffReviewState | null>;
  enterDiffReview: (
    fileId: string,
    originalContent: string,
    modifiedContent: string,
    origin?: "conflict",
  ) => void;
  acceptEdit: (editId: string) => void;
  rejectEdit: (editId: string) => void;
  resetEdit: (editId: string) => void;
  acceptAllEdits: () => void;
  rejectAllEdits: () => void;
  exitDiffReview: () => void;
}

export function useConflictReviewSession({
  currentReviewRef,
  enterDiffReview,
  acceptEdit,
  rejectEdit,
  resetEdit,
  acceptAllEdits,
  rejectAllEdits,
  exitDiffReview,
}: UseConflictReviewSessionOptions) {
  const conflictReviewRef = useRef<ConflictReview | null>(null);

  /**
   * Open the comparison for "the author has unsaved text written on an older
   * copy (`base`) and the server now holds a newer one".
   */
  const openConflictReview = useCallback(
    (
      fileId: string,
      base: string,
      serverContent: string,
      localContent: string,
    ): ConflictReviewOutcome => {
      const { merged, proposal, conflictEditIds } = rebaseLocalEdits(
        base,
        localContent,
        serverContent,
      );
      if (proposal === serverContent) return "server";
      if (merged === localContent) return "local";
      enterDiffReview(fileId, localContent, merged, "conflict");
      conflictReviewRef.current = {
        fileId,
        originalContent: localContent,
        modifiedContent: merged,
        serverContent,
        base,
        undecidedConflictIds: new Set(conflictEditIds),
      };
      for (const editId of conflictEditIds) rejectEdit(editId);
      return "review";
    },
    [enterDiffReview, rejectEdit],
  );

  // The author's own choices in a conflict comparison (see ConflictReview).
  const decideConflict = useCallback((editId?: string) => {
    const conflict = conflictReviewRef.current;
    if (!conflict) return;
    if (editId === undefined) conflict.undecidedConflictIds.clear();
    else conflict.undecidedConflictIds.delete(editId);
  }, []);

  const handleAcceptEdit = useCallback(
    (editId: string) => {
      decideConflict(editId);
      acceptEdit(editId);
    },
    [acceptEdit, decideConflict],
  );
  const handleRejectEdit = useCallback(
    (editId: string) => {
      decideConflict(editId);
      rejectEdit(editId);
    },
    [decideConflict, rejectEdit],
  );
  const handleResetEdit = useCallback(
    (editId: string) => {
      decideConflict(editId);
      resetEdit(editId);
    },
    [decideConflict, resetEdit],
  );
  const handleAcceptAllEdits = useCallback(() => {
    decideConflict();
    acceptAllEdits();
  }, [acceptAllEdits, decideConflict]);
  const handleRejectAllEdits = useCallback(() => {
    decideConflict();
    rejectAllEdits();
  }, [decideConflict, rejectAllEdits]);

  // Leaving the project with the comparison open: the draft is already kept
  // locally (getReviewLeaveDraft) and is offered again when the file reopens,
  // so the comparison must not reappear on top of it with an older token.
  useEffect(
    () => () => {
      if (isSameReview(currentReviewRef.current, conflictReviewRef.current)) exitDiffReview();
    },
    [currentReviewRef, exitDiffReview],
  );

  return {
    conflictReviewRef,
    openConflictReview,
    handleAcceptEdit,
    handleRejectEdit,
    handleResetEdit,
    handleAcceptAllEdits,
    handleRejectAllEdits,
  };
}

export function buildReviewLeaveDraft(
  review: DiffReviewState | null,
  opened: File | null,
  conflict: ConflictReview | null,
  editTitle: string,
) {
  if (!review || !opened || review.fileId !== opened.id || !conflict || !isSameReview(review, conflict)) {
    return null;
  }
  const undecided = review.pendingEdits.some(
    (edit) => edit.status === "rejected" && conflict.undecidedConflictIds.has(edit.id),
  );
  const diffs = computeParagraphReviewDiffs(review.originalContent, review.modifiedContent);
  return {
    title: editTitle,
    content: applyPendingEditsToDiffs(diffs, review.pendingEdits),
    baseUpdatedAt: undecided ? undefined : opened.updated_at,
  };
}

export interface UseReviewedFileCommitOptions {
  queryClient: QueryClient | undefined;
  t: TFunction;
  currentUserId: string | null;
  currentProjectId: string | null;
  selectedItem: { id: string; type: TreeNodeType | FileType; title: string } | null;
  setSelectedItem: (item: { id: string; type: TreeNodeType; title: string } | null) => void;
  triggerFileTreeRefresh: () => void;
  diffReviewState: DiffReviewState | null;
  exitDiffReview: () => void;
  applyDiffReviewChanges: () => string;
  file: File | null;
  setFile: Dispatch<SetStateAction<File | null>>;
  setEditContent: Dispatch<SetStateAction<string>>;
  draftRecovery: DraftRecoveryState | null;
  setDraftRecovery: Dispatch<SetStateAction<DraftRecoveryState | null>>;
  adoptServerCopy: (data: File, nextTitle: string) => void;
  markServerBaseline: (data: File) => void;
  openConflictReview: (
    fileId: string,
    base: string,
    serverContent: string,
    localContent: string,
  ) => ConflictReviewOutcome;
  conflictReviewRef: MutableRefObject<ConflictReview | null>;
  activeProjectIdRef: MutableRefObject<string | null>;
  selectedIdRef: MutableRefObject<string | undefined>;
  currentFileRef: MutableRefObject<File | null>;
  currentReviewRef: MutableRefObject<DiffReviewState | null>;
  editTitleRef: MutableRefObject<string>;
  serverSyncSeqRef: MutableRefObject<number>;
  openContextRef: MutableRefObject<{ isMobile: boolean; streamingFileId: string | null }>;
}

export function useReviewedFileCommit({
  queryClient,
  t,
  currentUserId,
  currentProjectId,
  selectedItem,
  setSelectedItem,
  triggerFileTreeRefresh,
  diffReviewState,
  exitDiffReview,
  applyDiffReviewChanges,
  file,
  setFile,
  setEditContent,
  draftRecovery,
  setDraftRecovery,
  adoptServerCopy,
  markServerBaseline,
  openConflictReview,
  conflictReviewRef,
  activeProjectIdRef,
  selectedIdRef,
  currentFileRef,
  currentReviewRef,
  editTitleRef,
  serverSyncSeqRef,
  openContextRef,
}: UseReviewedFileCommitOptions) {
  const commitReviewedFile = useCallback(
    async (params: {
      opened: File;
      review: NonNullable<DiffReviewState>;
      content: string;
      conflictBase: string;
      updateMeta: {
        change_type: "ai_edit" | "edit";
        change_summary?: string;
        change_source?: "user";
      };
      errorLogMessage: string;
      errorAction: string;
      onSuccess?: () => void;
      onStaleWrite?: (latest: File, outcome: ConflictReviewOutcome) => void;
    }) => {
      const {
        opened,
        review,
        content,
        conflictBase,
        updateMeta,
        errorLogMessage,
        errorAction,
        onSuccess,
        onStaleWrite,
      } = params;
      const fileId = opened.id;
      const projectId = opened.project_id;
      // 审阅期间标题输入框是禁用的，但进入审阅前作者可能改了标题还没保存。
      // 完成审阅后编辑器会被标记为已保存，所以标题必须随这次写回一起保存。
      const reviewedTitle = editTitleRef.current;
      const titleChanged = reviewedTitle !== opened.title;
      const isStillCurrent = () =>
        activeProjectIdRef.current === currentProjectId &&
        selectedIdRef.current === fileId &&
        currentFileRef.current?.id === fileId &&
        currentReviewRef.current === review;

      if (!isStillCurrent()) return;

      try {
        // 与 handleSaveFile 对齐的乐观并发令牌。这是一次整篇覆盖写，
        // 不带令牌就会无声盖掉审阅期间落库的其它改动。
        const updated = await fileApi.update(fileId, {
          content,
          ...(titleChanged ? { title: reviewedTitle } : {}),
          ...updateMeta,
          base_updated_at: opened.updated_at,
        });
        if (!isStillCurrent()) return;

        const base = currentFileRef.current ?? opened;
        const saved: File = {
          ...base,
          content,
          ...(titleChanged ? { title: reviewedTitle } : {}),
          updated_at: updated?.updated_at ?? base.updated_at,
        };
        serverSyncSeqRef.current += 1;
        currentFileRef.current = saved;
        setFile(saved);
        setEditContent(content);
        markServerBaseline(saved);
        consumePreloadedFile(queryClient, currentUserId, projectId, fileId);
        updateWorkbenchSwrInitialFile(currentUserId, projectId, saved);
        if (titleChanged && selectedItem?.id === fileId) {
          setSelectedItem({ ...selectedItem, title: reviewedTitle });
        }
        // A copy kept locally while this comparison was open (or an applied
        // recovered draft) is superseded by this save. An unanswered recovery
        // banner keeps its draft; storage failures must not falsely clear state.
        if (currentUserId && currentProjectId) {
          if (draftRecovery?.snapshot.fileId === fileId && draftRecovery.applied) {
            const cleared = clearEditorDraftSnapshot(localStorage, {
              userId: currentUserId,
              projectId: currentProjectId,
              fileId,
            });
            if (cleared) {
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
          } else if (draftRecovery?.snapshot.fileId !== fileId) {
            clearEditorDraftSnapshot(localStorage, {
              userId: currentUserId,
              projectId: currentProjectId,
              fileId,
            });
          }
        }
        onSuccess?.();
        exitDiffReview();
        triggerFileTreeRefresh();
        notifyEditorContentSaved(projectId);
      } catch (err) {
        if (!isStillCurrent()) return;
        if (err instanceof ApiError && err.status === 409 && err.details?.reason === "stale_write") {
          trackEditorSaveConflict409({
            surface: "manual",
            isMobile: openContextRef.current.isMobile,
            reason: "stale_write",
          });
          const current = currentFileRef.current;
          if (!current) return;
          const serverContent =
            typeof err.details.current_content === "string" ? err.details.current_content : "";
          const serverUpdatedAt =
            typeof err.details.current_updated_at === "string"
              ? err.details.current_updated_at
              : current.updated_at;
          const latest: File = { ...current, content: serverContent, updated_at: serverUpdatedAt };
          currentFileRef.current = latest;
          setFile(latest);
          consumePreloadedFile(queryClient, currentUserId, projectId, fileId);
          const outcome = openConflictReview(fileId, conflictBase, serverContent, content);
          onStaleWrite?.(latest, outcome);
          if (outcome === "server") {
            exitDiffReview();
            adoptServerCopy(latest, reviewedTitle);
          } else if (outcome === "local") {
            // The newer copy adds nothing to the reviewed text: keep this review
            // open so retrying saves on top of the fresh server token.
            toast.error(t("editor:saveStaleWrite"));
          } else {
            trackEditorDraftRecoveryShown({
              surface: "conflict_review",
              isMobile: openContextRef.current.isMobile,
              reason: "stale_write",
            });
            toast.error(t("editor:saveStaleWriteConflict"));
          }
          return;
        }
        logger.error(errorLogMessage, err);
        toast.error(handleApiError(err));
        captureException(err, {
          feature_area: "editor",
          action: errorAction,
          file_id: fileId,
        });
      }
    },
    [
      activeProjectIdRef,
      adoptServerCopy,
      currentFileRef,
      currentProjectId,
      currentReviewRef,
      currentUserId,
      draftRecovery,
      editTitleRef,
      exitDiffReview,
      markServerBaseline,
      openConflictReview,
      openContextRef,
      queryClient,
      selectedIdRef,
      selectedItem,
      serverSyncSeqRef,
      setDraftRecovery,
      setEditContent,
      setFile,
      setSelectedItem,
      t,
      triggerFileTreeRefresh,
    ],
  );

  /**
   * Completes the diff review process and applies accepted changes.
   */
  const handleFinishReview = useCallback(async () => {
    if (!diffReviewState || !file?.id || diffReviewState.fileId !== file.id) return;
    if (
      activeProjectIdRef.current !== currentProjectId ||
      selectedIdRef.current !== file.id ||
      currentFileRef.current?.id !== file.id ||
      currentReviewRef.current !== diffReviewState
    ) {
      return;
    }

    const finalContent = applyDiffReviewChanges();
    const conflictReview = isSameReview(diffReviewState, conflictReviewRef.current)
      ? conflictReviewRef.current
      : null;
    const reviewBase = conflictReview
      ? conflictReview.serverContent
      : diffReviewState.originalContent;

    await commitReviewedFile({
      opened: file,
      review: diffReviewState,
      content: finalContent,
      conflictBase: reviewBase,
      updateMeta: {
        change_type: "edit",
        change_source: "user",
      },
      errorLogMessage: "Failed to save reviewed changes:",
      errorAction: "ai_edit_review_apply",
      onSuccess: () => {
        const acceptedEditCount = diffReviewState.pendingEdits.filter(
          (edit) => edit.status !== "rejected",
        ).length;
        const rejectedEditCount = diffReviewState.pendingEdits.filter(
          (edit) => edit.status === "rejected",
        ).length;
        const resolution =
          acceptedEditCount > 0 && rejectedEditCount > 0
            ? "merge"
            : acceptedEditCount > 0
              ? "use_server"
              : rejectedEditCount > 0
                ? "restore_local"
                : "merge";
        trackEditorDraftRecoveryResolved({
          surface: "conflict_review",
          isMobile: openContextRef.current.isMobile,
          reason: "stale_write",
          resolution,
        });
        trackEvent("ai_edit_review_applied", {
          project_id: file.project_id,
          file_id: file.id,
          accepted_edit_count: acceptedEditCount,
          rejected_edit_count: rejectedEditCount,
        });
      },
    });
  }, [
    activeProjectIdRef,
    applyDiffReviewChanges,
    commitReviewedFile,
    conflictReviewRef,
    currentFileRef,
    currentProjectId,
    currentReviewRef,
    diffReviewState,
    file,
    openContextRef,
    selectedIdRef,
  ]);

  return { handleFinishReview };
}
