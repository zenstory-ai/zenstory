/**
 * @fileoverview Content editor component for the zenstory writing workbench.
 *
 * This component provides the main file editing interface, handling:
 * - Rich text editing for outlines, drafts, characters, and lore files
 * - Automatic version creation on file save
 * - Streaming content updates during AI generation
 * - Diff review mode for AI-suggested edits
 * - Material preview and import from reference library
 *
 * @module components/Editor
 */
import React, { useState, useEffect, useCallback, useRef } from "react";
import { useTranslation } from "react-i18next";
import { useProject } from "../contexts/ProjectContext";
import { useAuth } from "../contexts/AuthContext";
import { useMaterialLibraryContext } from "../contexts/MaterialLibraryContext";
import { useMaterialAttachment } from "../contexts/MaterialAttachmentContext";
import { fileApi } from "../lib/api";
import { ApiError } from "../lib/apiClient";
import { handleApiError } from "../lib/errorHandler";
import { toast } from "../lib/toast";
import { logger } from "../lib/logger";
import { SimpleEditor } from "./SimpleEditor";
import type { SaveOutcome, SaveResult, SaveSubmission } from "./SimpleEditor";
import { MaterialPreview } from "./MaterialPreview";
import { ImportMaterialDialog } from "./ImportMaterialDialog";
import type { File, FileTreeNode } from "../types";
import { FOLDER_TYPE_MAP } from "../lib/folderTypeMap";
import { FileText, Users, BookOpen, Sparkles, Folder, Zap, Keyboard, ChevronDown, Clapperboard } from "lucide-react";
import { LoadingSpinner } from "./LoadingSpinner";
import { UpgradePromptModal } from "./subscription/UpgradePromptModal";
import { buildUpgradeUrl, getUpgradePromptDefinition } from "../config/upgradeExperience";
import { captureException, trackEvent } from "../lib/analytics";
import { useMobileLayout } from "../contexts/MobileLayoutContext";
import {
  clearEditorDraftSnapshot,
  readEditorDraftSnapshot,
  resolveEditorDraftRecovery,
  type EditorDraftSnapshot,
} from "../lib/editorDraftRecovery";
import { notifyEditorContentSaved } from "../lib/editorSaveTracker";
import { rebaseLocalEdits } from "../lib/rebaseLocalEdits";
import { applyPendingEditsToDiffs, buildParagraphReviewData } from "../lib/diffReview";
import type { DiffReviewState } from "../types";

/** Retry delays for re-reading the open file after an external write. */
const SERVER_SYNC_RETRY_DELAYS_MS = [1000, 3000];

/**
 * Props interface for the Editor component.
 * Currently accepts no props as all state is managed through ProjectContext.
 */
// eslint-disable-next-line @typescript-eslint/no-empty-object-type
export interface EditorProps {}

type EmptyStateFileType = 'draft' | 'outline' | 'character' | 'lore' | 'script';

/** The comparison Editor opened for "the author wrote on an older copy". */
interface ConflictReview {
  fileId: string;
  originalContent: string;
  modifiedContent: string;
}

const isSameReview = (review: DiffReviewState | null, conflict: ConflictReview | null): boolean =>
  review !== null &&
  conflict !== null &&
  review.fileId === conflict.fileId &&
  review.originalContent === conflict.originalContent &&
  review.modifiedContent === conflict.modifiedContent;

/** Apple keyboards label the search shortcut ⌘ K; everything else uses Ctrl K. */
const isApplePlatform = (): boolean => {
  if (typeof navigator === 'undefined') return false;
  const uaDataPlatform = (navigator as Navigator & { userAgentData?: { platform?: string } }).userAgentData?.platform;
  return /mac|iphone|ipad|ipod/i.test(uaDataPlatform || navigator.platform || '');
};

/**
 * Internal Editor component implementation.
 *
 * Renders the appropriate editor based on context and file state:
 * - MaterialPreview when viewing reference library content
 * - Empty state when no file is selected
 * - Folder hint when a folder is selected
 * - Loading spinner during initial file load
 * - Error message on load failure
 * - SimpleEditor for all documents
 *
 * Handles AI streaming content display and diff review mode for
 * accepting/rejecting AI-suggested edits.
 *
 * Uses React.memo for the exported component to prevent unnecessary re-renders.
 *
 * @returns The appropriate editor UI based on current state
 */
const EditorComponent: React.FC<EditorProps> = () => {
  const { t } = useTranslation(['editor', 'common']);
  const { user } = useAuth();
  const currentUserId = user?.id ?? null;
  const fileVersionUpgradePrompt = getUpgradePromptDefinition("file_version_quota_blocked");
  // Layout 在手机宽度下用 MobileLayoutProvider 包住编辑器，这里和它用同一个判断。
  const { isMobile } = useMobileLayout();
  const {
    selectedItem,
    currentProject,
    currentProjectId,
    streamingFileId,
    streamingContent,
    triggerFileTreeRefresh,
    setSelectedItem,
    editorRefreshVersion,
    lastEditedFileId,
    aiEditingFileId,
    // Diff review state
    diffReviewState,
    enterDiffReview,
    acceptEdit,
    rejectEdit,
    resetEdit,
    acceptAllEdits,
    rejectAllEdits,
    exitDiffReview,
    applyDiffReviewChanges,
  } = useProject();

  // Material library context
  const materialLib = useMaterialLibraryContext();
  const { addMaterial } = useMaterialAttachment();
  const [importDialogOpen, setImportDialogOpen] = useState(false);

  // Data states
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Creation loading state
  const [isCreating, setIsCreating] = useState<string | null>(null);

  // Empty state more options toggle
  const [showMore, setShowMore] = useState(false);
  const [showFileVersionUpgradeModal, setShowFileVersionUpgradeModal] = useState(false);

  // Local editing states
  const [editTitle, setEditTitle] = useState("");
  const [editContent, setEditContent] = useState("");
  const editTitleRef = useRef(editTitle);
  const editContentRef = useRef(editContent);
  editTitleRef.current = editTitle;
  editContentRef.current = editContent;
  /**
   * Bumped whenever the editor switches to a server copy outside SimpleEditor's
   * own save flow, so SimpleEditor resets its draft, save token, dirty flag and
   * word-count baseline to that copy instead of keeping an older one.
   */
  const [serverBaseline, setServerBaseline] = useState<{
    version: number;
    fileId: string;
    title: string;
    content: string;
    updatedAt?: string;
  } | null>(null);
  const serverBaselineVersionRef = useRef(0);
  /**
   * Re-reading the open file after an AI write failed even after retries: the
   * editor may show an older copy, so it says so and offers to load again.
   */
  const [serverSyncIssue, setServerSyncIssue] = useState<{ fileId: string; retrying: boolean } | null>(null);
  const conflictReviewRef = useRef<ConflictReview | null>(null);
  const [draftRecovery, setDraftRecovery] = useState<{
    snapshot: EditorDraftSnapshot;
    serverTitle: string;
    serverContent: string;
    kind: "recover" | "conflict";
    applied: boolean;
  } | null>(null);
  const hasLoadedRef = useRef(false);
  const loadGenerationRef = useRef(0);
  const restoredSelectionRef = useRef<string | null>(null);
  const currentFileRef = useRef<File | null>(file);
  const translateRef = useRef(t);
  const activeProjectIdRef = useRef<string | null>(currentProjectId);
  const selectedIdRef = useRef(selectedItem?.id);
  const currentReviewRef = useRef(diffReviewState);
  selectedIdRef.current = selectedItem?.id;
  currentReviewRef.current = diffReviewState;
  useEffect(() => {
    activeProjectIdRef.current = currentProjectId;
    setIsCreating(null);
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
    editorFlushRef.current = flush;
  }, []);

  /**
   * Resolve the best parent folder for a new file based on file type.
   *
   * Priority:
   * 1) Canonical deterministic folder ID (<projectId>-<type>-folder)
   * 2) Folder title mapping fallback (for legacy/migrated projects)
   */
  const resolveCreateParentId = useCallback(async (fileType: EmptyStateFileType): Promise<string | undefined> => {
    if (!currentProjectId) return undefined;

    try {
      const { tree } = await fileApi.getTree(currentProjectId);

      const rootFolders = (tree || []).filter((node) => node.file_type === 'folder');
      const rootFolderIds = new Set(rootFolders.map((node) => node.id));

      const canonicalSuffixByType: Record<EmptyStateFileType, string> = {
        draft: 'draft-folder',
        outline: 'outline-folder',
        character: 'character-folder',
        lore: 'lore-folder',
        script: 'script-folder',
      };

      const canonicalFolderId = `${currentProjectId}-${canonicalSuffixByType[fileType]}`;
      if (rootFolderIds.has(canonicalFolderId)) {
        return canonicalFolderId;
      }

      const queue: FileTreeNode[] = [...(tree || [])];
      while (queue.length > 0) {
        const node = queue.shift();
        if (!node) continue;

        if (node.file_type === 'folder' && FOLDER_TYPE_MAP[node.title] === fileType) {
          return node.id;
        }

        if (node.children?.length) {
          queue.push(...node.children);
        }
      }
    } catch (err) {
      logger.warn('Failed to resolve create parent folder, falling back to root create', err);
    }

    return undefined;
  }, [currentProjectId]);

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

  /**
   * Loads the file data for the currently selected item.
   *
   * Fetches file content from the API and updates local editing state. Only shows
   * the loading spinner on initial load to prevent UI flicker when switching files.
   *
   * `followServer`: a reload of the open file after the AI finished writing it
   * (the <file> stream). The loaded copy also becomes SimpleEditor's baseline,
   * so the AI's words are not counted as the author's on the next save.
   */
  const loadData = useCallback(async (options?: { followServer?: boolean }) => {
    if (activeProjectIdRef.current !== currentProjectId || selectedIdRef.current !== selectedItem?.id) return;
    const generation = ++loadGenerationRef.current;
    const currentFile = currentFileRef.current;
    const restoredId = restoredSelectionRef.current;
    restoredSelectionRef.current = null;
    // A failed switch only restores navigation; refetching would replace the retained draft.
    if (restoredId && restoredId === selectedItem?.id && restoredId === currentFile?.id) return;
    if (currentFile && currentFile.id !== selectedItem?.id && editorFlushRef.current) {
      const outcome = await editorFlushRef.current();
      if (generation !== loadGenerationRef.current) return;
      if (outcome !== "saved") {
        toast.error(translateRef.current('editor:saveFailed'));
        restoredSelectionRef.current = currentFile.id;
        setSelectedItem({id:currentFile.id,type:currentFile.file_type,title:currentFile.title});
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

    // Only show loading state if no file is currently loaded (initial load)
    // This prevents flicker when switching between files
    if (!hasLoadedRef.current) {
      setLoading(true);
    }
    setError(null);

    try {
      const data = await fileApi.get(selectedItem.id);
      if (generation !== loadGenerationRef.current) return;
      setFile(data);
      hasLoadedRef.current = true;
      setServerSyncIssue((prev) => (prev?.fileId === data.id ? null : prev));
      const followServer = options?.followServer === true && currentFile?.id === data.id;
      const serverContent = data.content || "";
      const scope = currentUserId ? {
        userId: currentUserId,
        projectId: currentProjectId,
        fileId: data.id,
      } : null;
      const snapshot = scope ? readEditorDraftSnapshot(localStorage, scope) : null;
      if (!snapshot) {
        setDraftRecovery(null);
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
          if (scope) clearEditorDraftSnapshot(localStorage, scope);
          setDraftRecovery(null);
          setEditTitle(data.title);
          setEditContent(serverContent);
          if (followServer) markServerBaseline(data);
        } else if (resolution === "recover") {
          setDraftRecovery({
            snapshot,
            serverTitle: data.title,
            serverContent,
            kind: "recover",
            applied: true,
          });
          setEditTitle(snapshot.title);
          setEditContent(snapshot.content);
        } else {
          setDraftRecovery({
            snapshot,
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
    } catch (err: unknown) {
      if (generation !== loadGenerationRef.current) return;
      const error = err as { status?: number };
      if (error?.status !== 401) {
        logger.error("Failed to load data:", err);
        setError(translateRef.current('editor:placeholder.loadFailed'));
      }
    } finally {
      if (generation === loadGenerationRef.current) setLoading(false);
    }
  }, [
    selectedItem,
    currentProjectId,
    setSelectedItem,
    currentUserId,
    markServerBaseline,
  ]);

  useEffect(() => {
    loadData();
    return () => { loadGenerationRef.current += 1; };
  }, [loadData]);

  // Track previous streaming file id to detect when streaming ends
  const prevStreamingFileIdRef = useRef<string | null>(null);
  
  // Reload file content when streaming ends for this file
  useEffect(() => {
    const prevId = prevStreamingFileIdRef.current;
    prevStreamingFileIdRef.current = streamingFileId;
    
    // If streaming just ended for the current file, reload to get final content
    if (prevId && prevId === file?.id && prevId === selectedItem?.id && streamingFileId === null) {
      // Small delay to ensure backend has updated the content
      const timer = setTimeout(() => {
        void loadData({ followServer: true });
      }, 100);
      return () => clearTimeout(timer);
    }
  }, [streamingFileId, file?.id, selectedItem?.id, loadData]);

  /**
   * Show a newer server copy and make it the editor's clean baseline. A title
   * the author renamed but has not saved yet is kept (and saved on top of the
   * new copy by the next autosave); everything else follows the server.
   */
  const adoptServerCopy = useCallback((data: File, nextTitle: string) => {
    currentFileRef.current = data;
    setFile(data);
    setEditTitle(nextTitle);
    setEditContent(data.content || "");
    markServerBaseline(data);
  }, [markServerBaseline]);

  /**
   * Open the comparison for "the author has unsaved text written on an older
   * copy (`base`) and the server now holds a newer one".
   *
   * A two-way comparison of the server copy against the author's whole text
   * would present every change the AI made since `base` as an author edit,
   * and finishing would undo them. Instead the author's own edits are replayed
   * on top of the server copy, so the proposals are exactly what the author
   * wrote; regions both sides changed start rejected, so finishing without
   * choosing keeps the server side there.
   *
   * Returns false when the author has nothing beyond the server copy.
   */
  const openConflictReview = useCallback((
    fileId: string,
    base: string,
    serverContent: string,
    localContent: string,
  ): boolean => {
    const { proposal, conflictEditIds } = rebaseLocalEdits(base, localContent, serverContent);
    if (proposal === serverContent) return false;
    enterDiffReview(fileId, serverContent, proposal);
    conflictReviewRef.current = { fileId, originalContent: serverContent, modifiedContent: proposal };
    for (const editId of conflictEditIds) rejectEdit(editId);
    return true;
  }, [enterDiffReview, rejectEdit]);

  /**
   * What SimpleEditor keeps locally when the author leaves (or the page
   * unloads) while this comparison is open: the text finishing it would save
   * right now, on the server copy's token. The raw draft would be the author's
   * whole text on the older copy, and restoring it would undo the AI's write.
   */
  const getReviewLeaveDraft = useCallback(() => {
    const review = currentReviewRef.current;
    const opened = currentFileRef.current;
    if (!review || !opened || review.fileId !== opened.id || !isSameReview(review, conflictReviewRef.current)) {
      return null;
    }
    const { diffs } = buildParagraphReviewData(review.originalContent, review.modifiedContent);
    return {
      title: editTitleRef.current,
      content: applyPendingEditsToDiffs(diffs, review.pendingEdits),
      baseUpdatedAt: opened.updated_at,
    };
  }, []);

  // Leaving the project with the comparison open: the draft is already kept
  // locally (getReviewLeaveDraft) and is offered again when the file reopens,
  // so the comparison must not reappear on top of it with an older token.
  useEffect(() => () => {
    if (isSameReview(currentReviewRef.current, conflictReviewRef.current)) exitDiffReview();
  }, [exitDiffReview]);

  /**
   * Re-read the open file after someone else (the AI, an undo) wrote it.
   *
   * - No unsaved body text: follow the server copy (keeping an unsaved title
   *   rename) and reset SimpleEditor's draft, save token and dirty state to it.
   * - Unsaved body text written on top of an older copy: never replace it.
   *   The server copy becomes the comparison baseline and the author's own
   *   edits, replayed onto it, are the proposals.
   */
  const syncOpenFileFromServer = useCallback(async () => {
    const opened = currentFileRef.current;
    if (!opened || selectedIdRef.current !== opened.id) return;
    const fileId = opened.id;
    const generation = loadGenerationRef.current;
    const isStillOpen = () =>
      generation === loadGenerationRef.current &&
      currentFileRef.current?.id === fileId &&
      selectedIdRef.current === fileId;
    let data: File | null = null;
    for (let attempt = 0; data === null; attempt += 1) {
      try {
        data = await fileApi.get(fileId);
      } catch (err) {
        if (attempt >= SERVER_SYNC_RETRY_DELAYS_MS.length) {
          // The save token still guards the server copy (saving stale text
          // gets a 409, which opens the same comparison), but the author must
          // not keep reading an older copy without knowing it.
          logger.warn("Failed to re-read file after an external write:", err);
          if (isStillOpen()) setServerSyncIssue({ fileId, retrying: false });
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, SERVER_SYNC_RETRY_DELAYS_MS[attempt]));
        if (!isStillOpen()) return;
      }
    }
    const current = currentFileRef.current;
    if (!current || !isStillOpen() || activeProjectIdRef.current !== data.project_id) return;
    setServerSyncIssue((prev) => (prev?.fileId === fileId ? null : prev));
    // A comparison is already open for this file. Its save carries the token
    // it was opened with, so a newer server copy surfaces there as a conflict.
    if (currentReviewRef.current?.fileId === fileId) return;

    const serverContent = data.content || "";
    const savedContent = current.content || "";
    if (
      data.updated_at === current.updated_at &&
      serverContent === savedContent &&
      data.title === current.title
    ) return;

    const localContent = editContentRef.current;
    const localTitle = editTitleRef.current;
    // A title the author has not renamed follows the server (the AI may have renamed it).
    const nextTitle = localTitle === current.title ? data.title : localTitle;
    if (localContent === savedContent || localContent === serverContent) {
      adoptServerCopy(data, nextTitle);
      return;
    }

    if (!openConflictReview(fileId, savedContent, serverContent, localContent)) {
      adoptServerCopy(data, nextTitle);
      return;
    }
    const merged = { ...current, title: data.title, content: serverContent, updated_at: data.updated_at };
    currentFileRef.current = merged;
    setFile((prev) => (prev?.id === fileId ? merged : prev));
    setEditTitle(nextTitle);
    toast.error(translateRef.current('editor:aiEditedWhileDirty'));
  }, [adoptServerCopy, openConflictReview]);

  const retryServerSync = useCallback(() => {
    const fileId = currentFileRef.current?.id;
    if (!fileId) return;
    setServerSyncIssue({ fileId, retrying: true });
    void syncOpenFileFromServer();
  }, [syncOpenFileFromServer]);

  // Re-read the open file when the AI (or an undo) wrote it
  useEffect(() => {
    if (lastEditedFileId && lastEditedFileId === file?.id && lastEditedFileId === selectedItem?.id && editorRefreshVersion > 0) {
      // Small delay to ensure backend has committed the changes
      const timer = setTimeout(() => {
        void syncOpenFileFromServer();
      }, 100);
      return () => clearTimeout(timer);
    }
  }, [editorRefreshVersion, lastEditedFileId, file?.id, selectedItem?.id, syncOpenFileFromServer]);

  const renderWithUpgradeModal = (content: React.ReactNode) => (
    <>
      {content}
      <UpgradePromptModal
        open={showFileVersionUpgradeModal}
        onClose={() => setShowFileVersionUpgradeModal(false)}
        source={fileVersionUpgradePrompt.source}
        primaryDestination="billing"
        secondaryDestination="pricing"
        title={t("editor:versionHistory.fileVersionLimitTitle")}
        description={t("editor:versionHistory.fileVersionLimitUpgrade")}
        paidDescription={t("editor:versionHistory.fileVersionLimitPaid", {
          defaultValue: "正文照常保存，只是这个文件不再生成新版本。",
        })}
        primaryLabel={t("common:viewUpgrade")}
        onPrimary={() => {
          window.location.assign(
            buildUpgradeUrl(fileVersionUpgradePrompt.billingPath, fileVersionUpgradePrompt.source)
          );
        }}
        secondaryLabel={t("common:viewPlans")}
        onSecondary={() => {
          window.location.assign(
            buildUpgradeUrl(fileVersionUpgradePrompt.pricingPath, fileVersionUpgradePrompt.source)
          );
        }}
      />
    </>
  );

  /**
   * Saves the current file with updated title and content.
   *
   * Updates the file via API, syncs local state, and triggers file tree
   * refresh if the title has changed to keep the navigation in sync.
   */
  const handleSaveFile = async (submission: SaveSubmission): Promise<SaveResult> => {
    if (!submission.fileId) return { outcome: "failed" };

    const targetFileId = submission.fileId;
    const titleChanged = submission.title !== submission.previousTitle;

    let updated;
    try {
      updated = await fileApi.update(targetFileId, {
        title: submission.title,
        content: submission.content,
        // 乐观并发令牌：带上加载这份正文时的 updated_at。
        // 编辑器提交的是整篇快照，光加锁挡不住丢更新——3 秒防抖期间 AI 的
        // edit_file 先落库时，陈旧快照拿到锁后照样会原样覆盖它。
        base_updated_at: submission.baseUpdatedAt,
        ...submission.versionIntent,
      });
    } catch (error) {
      if (
        error instanceof ApiError &&
        error.errorCode === "ERR_QUOTA_FILE_VERSIONS_EXCEEDED"
      ) {
        toast.error(handleApiError(error));
        if (fileVersionUpgradePrompt.surface === "modal") {
          setShowFileVersionUpgradeModal(true);
        }
        return { outcome: "failed" };
      }
      if (
        error instanceof ApiError &&
        error.status === 409 &&
        error.details?.reason === "stale_write"
      ) {
        // 文件在本次编辑期间被别人（AI 或另一个标签页）改过。
        //
        // 绝不能直接 setEditContent(服务端正文)：那会把用户尚未保存的本地编辑
        // 整段替换掉，且没有任何备份/撤销入口——只是把「AI 被用户覆盖」换成了
        // 「用户被服务端覆盖」，同样是不可逆的数据丢失。
        //
        // 正确做法：本地编辑原样留在编辑器里（一个字都不动），把服务端正文作为
        // diff review 的基线送进现成的审阅通道，由用户逐处决定保留哪一份；
        // 同时把 updated_at 同步成服务端的最新值，让审阅完成后的写回不会再撞 409。
        const currentContent = error.details.current_content;
        const currentUpdatedAt = error.details.current_updated_at;
        const localContent = submission.content;
        // The copy this draft was written on: what the editor last loaded or saved.
        const openedFile = currentFileRef.current;
        const isStillOpen =
          activeProjectIdRef.current !== null &&
          openedFile?.id === targetFileId &&
          selectedIdRef.current === targetFileId;
        if (typeof currentContent === "string") {
          const baseContent = openedFile?.id === targetFileId ? openedFile.content || "" : currentContent;
          const nextFile = (prev: File) => ({
            ...prev,
            content: currentContent,
            updated_at:
              typeof currentUpdatedAt === "string" ? currentUpdatedAt : prev.updated_at,
          });
          if (openedFile?.id === targetFileId) currentFileRef.current = nextFile(openedFile);
          setFile((prev) => (prev?.id === targetFileId ? nextFile(prev) : prev));
          if (currentContent !== localContent) {
            // Only an editor that still shows this file may open the comparison;
            // a save sent while leaving keeps its draft as a local snapshot.
            if (
              isStillOpen &&
              openedFile &&
              !openConflictReview(targetFileId, baseContent, currentContent, localContent)
            ) {
              // Everything the author wrote is already in the server copy.
              adoptServerCopy(nextFile(openedFile), editTitleRef.current);
              return { outcome: "conflict" };
            }
            toast.error(t('editor:saveStaleWriteConflict'));
            return { outcome: "conflict" };
          }
        }
        toast.error(t('editor:saveStaleWrite'));
        return { outcome: "conflict" };
      }
      throw error;
    }

    if (updated?.version_quota_exceeded) {
      // 正文已保存，只是版本快照没生成——提示升级，不能报成保存失败。
      toast.error(t('editor:saveVersionQuotaExceeded'));
      if (fileVersionUpgradePrompt.surface === "modal") {
        setShowFileVersionUpgradeModal(true);
      }
    }

    // Update local state（同步 updated_at，作为下一次保存的并发令牌）
    const currentTargetFile = currentFileRef.current;
    if (currentTargetFile?.id === targetFileId) {
      currentFileRef.current = {
        ...currentTargetFile,
        title: submission.title,
        content: submission.content,
        updated_at: updated?.updated_at ?? currentTargetFile.updated_at,
      };
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

    // If title changed, refresh file tree and update selected item
    if (titleChanged) {
      triggerFileTreeRefresh();
      // Update the selected item title to keep it in sync
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
      draftRecovery?.snapshot.fileId === targetFileId
      && draftRecovery.applied
      && currentUserId
      && currentProjectId
    ) {
      const cleared = clearEditorDraftSnapshot(localStorage, {
        userId: currentUserId,
        projectId: currentProjectId,
        fileId: targetFileId,
      });
      if (cleared) setDraftRecovery(null);
    }
    notifyEditorContentSaved(updated?.project_id ?? currentFileRef.current?.project_id ?? "");
    return { outcome: "saved", updatedAt: updated.updated_at };
  };

  const applyRecoveredDraft = useCallback(() => {
    if (!draftRecovery) return;
    setEditTitle(draftRecovery.snapshot.title);
    setEditContent(draftRecovery.snapshot.content);
    setDraftRecovery((current) => current ? { ...current, applied: true } : null);
  }, [draftRecovery]);

  const discardRecoveredDraft = useCallback(() => {
    if (!draftRecovery || !currentUserId || !currentProjectId) return;
    const cleared = clearEditorDraftSnapshot(localStorage, {
      userId: currentUserId,
      projectId: currentProjectId,
      fileId: draftRecovery.snapshot.fileId,
    });
    if (!cleared) return;
    setEditTitle(draftRecovery.serverTitle);
    setEditContent(draftRecovery.serverContent);
    setDraftRecovery(null);
  }, [draftRecovery, currentUserId, currentProjectId]);

  // 只有「去AI味」进入的审阅才允许在全部拒绝时跳过写库。另外两条审阅入口里
  // originalContent 并不等于服务端/编辑器当前持有的正文：Agent 的 edit_file 已经把
  // AI 文本落库，全部拒绝必须把原文 PUT 回去；保存冲突审阅的 originalContent 是服务端
  // 正文、编辑器里还是本地正文，必须 setEditContent 并带新令牌写回。这里按审阅的
  // 三元组记下去AI味审阅的来源，完成审阅时逐项比对。
  const naturalPolishReviewRef = useRef<{
    fileId: string;
    originalContent: string;
    modifiedContent: string;
  } | null>(null);

  const enterNaturalPolishReview = useCallback(
    (fileId: string, originalContent: string, newContent: string) => {
      naturalPolishReviewRef.current = { fileId, originalContent, modifiedContent: newContent };
      enterDiffReview(fileId, originalContent, newContent);
    },
    [enterDiffReview],
  );

  /**
   * Completes the diff review process and applies accepted changes.
   *
   * Gets the final content after all accept/reject decisions, updates the file,
   * creates a new version for the reviewed changes, and exits review mode.
   */
  const handleFinishReview = useCallback(async () => {
    if (!diffReviewState || !file?.id || diffReviewState.fileId !== file.id) return;
    const canCompleteReview = () =>
      activeProjectIdRef.current === currentProjectId &&
      selectedIdRef.current === file.id &&
      currentFileRef.current?.id === file.id &&
      currentReviewRef.current === diffReviewState;
    if (!canCompleteReview()) return;

    // Get the final content based on accept/reject decisions
    const finalContent = applyDiffReviewChanges();

    const polishReview = naturalPolishReviewRef.current;
    naturalPolishReviewRef.current = null;
    const isNaturalPolishReview =
      polishReview !== null &&
      polishReview.fileId === diffReviewState.fileId &&
      polishReview.originalContent === diffReviewState.originalContent &&
      polishReview.modifiedContent === diffReviewState.modifiedContent;

    // 去AI味审阅全部拒绝（或接受的改动都只是空白差异）时，定稿和审阅前、
    // 服务端已存的正文、编辑器里的正文三者逐字节相同：屏幕和服务端都不会变，
    // 直接退出审阅，不写库，也不生成一条「AI 编辑」版本。
    if (
      isNaturalPolishReview &&
      finalContent === diffReviewState.originalContent &&
      finalContent === (file.content ?? "") &&
      finalContent === editContent
    ) {
      exitDiffReview();
      return;
    }
    
    // 审阅期间标题输入框是禁用的，但进入审阅前作者可能改了标题还没保存。
    // 完成审阅后编辑器会被标记为已保存，所以标题必须随这次写回一起保存。
    const reviewedTitle = editTitleRef.current;
    const titleChanged = reviewedTitle !== file.title;

    // Update the file with the final content
    try {
      const updated = await fileApi.update(file.id, {
        content: finalContent,
        ...(titleChanged ? { title: reviewedTitle } : {}),
        change_type: "ai_edit",
        change_summary: "AI edit (reviewed)",
        // 与 handleSaveFile 对齐的乐观并发令牌。这同样是一次整篇覆盖写，
        // 不带令牌就会无声盖掉审阅期间落库的其它改动。
        base_updated_at: file.updated_at,
      });

      if (!canCompleteReview()) return;

      // Update local state。必须把返回的 updated_at 一并回填：
      // 这次 PUT 已经把服务端的 updated_at 推进了，本地若还停在审阅前的值，
      // 用户接着敲一个字触发的 3 秒防抖自动保存就会带着陈旧令牌命中 409，
      // 这一轮输入随即被 409 分支处理掉——「防丢 AI 的更新」换成「稳定丢用户的更新」。
      setEditContent(finalContent);
      const reviewedFile = (prev: File): File => ({
        ...prev,
        content: finalContent,
        ...(titleChanged ? { title: reviewedTitle } : {}),
        updated_at: updated?.updated_at ?? prev.updated_at,
      });
      if (currentFileRef.current) currentFileRef.current = reviewedFile(currentFileRef.current);
      setFile((prev) => (prev ? reviewedFile(prev) : null));
      if (titleChanged && selectedItem?.id === file.id) {
        setSelectedItem({ ...selectedItem, title: reviewedTitle });
      }

      // A copy kept locally while this comparison was open (the editor was
      // hidden mid-review) is superseded by this save. A recovery banner the
      // author has not answered yet keeps its draft.
      if (currentUserId && currentProjectId && draftRecovery?.snapshot.fileId !== file.id) {
        clearEditorDraftSnapshot(localStorage, {
          userId: currentUserId,
          projectId: currentProjectId,
          fileId: file.id,
        });
      }

      // Exit diff review mode
      exitDiffReview();

      trackEvent("ai_edit_review_applied", {
        project_id: file.project_id,
        file_id: file.id,
        accepted_edit_count: diffReviewState.pendingEdits.filter(
          (edit) => edit.status !== "rejected"
        ).length,
        rejected_edit_count: diffReviewState.pendingEdits.filter(
          (edit) => edit.status === "rejected"
        ).length,
      });
      
      // Refresh file tree
      triggerFileTreeRefresh();
      notifyEditorContentSaved(file.project_id);
    } catch (err) {
      if (!canCompleteReview()) return;
      if (
        err instanceof ApiError &&
        err.status === 409 &&
        err.details?.reason === "stale_write"
      ) {
        // 审阅结果绝不能因为并发写而丢：把服务端最新正文当作新基线，
        // 连同本次审阅得到的定稿重新进入审阅通道，由用户决定最终版本。
        const serverContent =
          typeof err.details.current_content === "string" ? err.details.current_content : "";
        const serverUpdatedAt = err.details.current_updated_at;
        const conflictFile = (prev: File): File => ({
          ...prev,
          content: serverContent,
          updated_at:
            typeof serverUpdatedAt === "string" ? serverUpdatedAt : prev.updated_at,
        });
        if (currentFileRef.current) currentFileRef.current = conflictFile(currentFileRef.current);
        setFile((prev) => (prev ? conflictFile(prev) : null));
        // The reviewed text was built on this review's baseline; replay only
        // what the review changed on top of the newer server copy.
        if (!openConflictReview(file.id, diffReviewState.originalContent, serverContent, finalContent)) {
          exitDiffReview();
          setEditContent(serverContent);
          return;
        }
        toast.error(t('editor:saveStaleWriteConflict'));
        return;
      }
      logger.error("Failed to save reviewed changes:", err);
      captureException(err, {
        feature_area: "editor",
        action: "ai_edit_review_apply",
        file_id: file.id,
      });
    }
  }, [
    diffReviewState,
    currentProjectId,
    currentUserId,
    draftRecovery,
    file?.id,
    file?.project_id,
    file?.updated_at,
    file?.content,
    file?.title,
    editContent,
    selectedItem,
    setSelectedItem,
    applyDiffReviewChanges,
    openConflictReview,
    exitDiffReview,
    triggerFileTreeRefresh,
    t,
  ]);

  /**
   * Creates a new file from the empty state action cards.
   *
   * Creates a file with the specified type, refreshes the file tree,
   * selects the new file in the editor, and shows success/error feedback.
   *
   * @param fileType - The type of file to create ('draft', 'outline', 'character', 'lore', 'script')
   */
  const handleCreateFromEmptyState = useCallback(async (fileType: EmptyStateFileType) => {
    if (!currentProjectId) {
      toast.error(t('editor:error.noProject'));
      return;
    }

    // Set loading state to prevent multiple clicks
    setIsCreating(fileType);

    // Default titles based on type
    const defaultTitles: Record<string, string> = {
      draft: t('editor:fileTree.newDraft'),
      outline: t('editor:fileTree.newOutline'),
      character: t('editor:fileTree.newCharacter'),
      lore: t('editor:fileTree.newLore'),
      script: t('editor:fileTree.newScript'),
    };

    try {
      const parentId = await resolveCreateParentId(fileType);
      if (activeProjectIdRef.current !== currentProjectId) return;
      const newFile = await fileApi.create(currentProjectId, {
        title: defaultTitles[fileType],
        file_type: fileType,
        parent_id: parentId,
        content: '',
      });

      if (activeProjectIdRef.current === currentProjectId) {
        triggerFileTreeRefresh();
        setSelectedItem({
          id: newFile.id,
          type: newFile.file_type,
          title: newFile.title,
        });
      }

      toast.success(t('editor:success.fileCreated'));
    } catch (error) {
      toast.error(t('editor:error.createFailed'));
      logger.error('Failed to create file:', error);
    } finally {
      if (activeProjectIdRef.current === currentProjectId) setIsCreating(null);
    }
  }, [currentProjectId, triggerFileTreeRefresh, setSelectedItem, t, resolveCreateParentId]);

  // Render material preview mode
  if (materialLib.preview && materialLib.previewEntityInfo) {
    return renderWithUpgradeModal(
      <>
        <MaterialPreview
          preview={materialLib.preview}
          isLoading={materialLib.isPreviewLoading}
          onAddToProject={() => setImportDialogOpen(true)}
          onAttachToChat={() => {
            if (materialLib.preview && materialLib.previewEntityInfo) {
              addMaterial(
                `material_${Date.now()}`,
                materialLib.preview.title,
                {
                  novelId: materialLib.previewEntityInfo.novelId,
                  entityType: materialLib.previewEntityInfo.entityType,
                  entityId: materialLib.previewEntityInfo.entityId,
                }
              );
            }
            materialLib.clearPreview();
          }}
          onBack={() => materialLib.clearPreview()}
        />
        {importDialogOpen && (
          <ImportMaterialDialog
            isOpen={importDialogOpen}
            onClose={() => setImportDialogOpen(false)}
            preview={materialLib.preview}
            novelId={materialLib.previewEntityInfo.novelId}
            entityType={materialLib.previewEntityInfo.entityType}
            entityId={materialLib.previewEntityInfo.entityId}
            onSuccess={() => {
              setImportDialogOpen(false);
              materialLib.clearPreview();
              triggerFileTreeRefresh();
            }}
          />
        )}
      </>
    );
  }

  // Render empty state
  if (!selectedItem) {
    const isScreenplay = currentProject?.project_type === 'screenplay';
    const primaryFileType: EmptyStateFileType = isScreenplay ? 'script' : 'draft';
    return renderWithUpgradeModal(
      <div className="flex flex-col items-center justify-center h-full p-6 md:p-8 relative overflow-hidden">
        {/* Background ambient glow */}
        <div className="absolute inset-0 bg-gradient-to-br from-[hsl(var(--accent-primary)/0.03)] via-transparent to-[hsl(var(--accent-primary)/0.02)] pointer-events-none" />

        {/* Floating orbs for depth */}
        <div className="absolute top-1/4 left-1/4 w-64 h-64 rounded-full bg-[hsl(var(--accent-primary)/0.05)] blur-3xl pointer-events-none animate-pulse will-change-transform" style={{ animationDuration: '4s' }} />
        <div className="absolute bottom-1/4 right-1/4 w-48 h-48 rounded-full bg-[hsl(var(--accent-primary)/0.04)] blur-2xl pointer-events-none animate-pulse will-change-transform" style={{ animationDuration: '5s', animationDelay: '1s' }} />

        <div className="max-w-md w-full space-y-6 relative z-10">
          {/* Headline */}
          <div className="text-center space-y-3">
            <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-gradient-to-br from-[hsl(var(--accent-primary)/0.15)] to-[hsl(var(--accent-primary)/0.05)] mb-3 shadow-lg shadow-[hsl(var(--accent-primary)/0.1)] transform hover:scale-105 transition-transform duration-300">
              <Sparkles className="w-7 h-7 text-[hsl(var(--accent-primary))]" />
            </div>
            <h2 className="text-xl md:text-2xl font-semibold text-[hsl(var(--text-primary))]">
              {t('editor:emptyStateTitle')}
            </h2>
            <p className="text-sm text-[hsl(var(--text-secondary))]">
              {isScreenplay ? t('editor:emptyStateDescriptionScript') : t('editor:emptyStateDescription')}
            </p>
          </div>

          {/* Primary Actions - 2 cards */}
          <div className="grid grid-cols-2 gap-3">
            {/* Create Draft (screenplay projects: Script) */}
            <div
              onClick={() => handleCreateFromEmptyState(primaryFileType)}
              className={`group relative flex flex-col items-center gap-2.5 p-4 rounded-xl bg-gradient-to-br from-[hsl(var(--bg-secondary))] to-[hsl(var(--bg-tertiary)/0.5)] border border-[hsl(var(--border-color))] hover:border-[hsl(var(--accent-primary)/0.4)] hover:shadow-xl hover:shadow-[hsl(var(--accent-primary)/0.08)] transition-all duration-300 cursor-pointer transform hover:-translate-y-0.5 ${isCreating === primaryFileType ? 'opacity-50 pointer-events-none' : ''}`}
            >
              <div className="absolute inset-0 rounded-xl bg-gradient-to-br from-[hsl(var(--accent-primary)/0.1)] to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-300" />
              <div className="relative w-11 h-11 rounded-xl bg-gradient-to-br from-[hsl(var(--accent-primary)/0.12)] to-[hsl(var(--accent-primary)/0.06)] flex items-center justify-center group-hover:scale-110 transition-transform duration-300 shadow-sm">
                {isCreating === primaryFileType ? (
                  <LoadingSpinner size="md" color="primary" />
                ) : isScreenplay ? (
                  <Clapperboard className="w-5 h-5 text-[hsl(var(--accent-primary))]" />
                ) : (
                  <BookOpen className="w-5 h-5 text-[hsl(var(--accent-primary))]" />
                )}
              </div>
              <span className="relative text-sm font-medium text-[hsl(var(--text-primary))] text-center">
                {isScreenplay ? t('editor:fileTree.newScript') : t('editor:fileTree.newDraft')}
              </span>
            </div>

            {/* Create Outline */}
            <div
              onClick={() => handleCreateFromEmptyState('outline')}
              className={`group relative flex flex-col items-center gap-2.5 p-4 rounded-xl bg-gradient-to-br from-[hsl(var(--bg-secondary))] to-[hsl(var(--bg-tertiary)/0.5)] border border-[hsl(var(--border-color))] hover:border-[hsl(var(--accent-primary)/0.4)] hover:shadow-xl hover:shadow-[hsl(var(--accent-primary)/0.08)] transition-all duration-300 cursor-pointer transform hover:-translate-y-0.5 ${isCreating === 'outline' ? 'opacity-50 pointer-events-none' : ''}`}
            >
              <div className="absolute inset-0 rounded-xl bg-gradient-to-br from-[hsl(var(--accent-primary)/0.1)] to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-300" />
              <div className="relative w-11 h-11 rounded-xl bg-gradient-to-br from-[hsl(var(--accent-primary)/0.12)] to-[hsl(var(--accent-primary)/0.06)] flex items-center justify-center group-hover:scale-110 transition-transform duration-300 shadow-sm">
                {isCreating === 'outline' ? <LoadingSpinner size="md" color="primary" /> : <FileText className="w-5 h-5 text-[hsl(var(--accent-primary))]" />}
              </div>
              <span className="relative text-sm font-medium text-[hsl(var(--text-primary))] text-center">
                {t('editor:fileTree.newOutline')}
              </span>
            </div>
          </div>

          {/* More Options Toggle */}
          <div className="space-y-2">
            <button
              onClick={() => setShowMore(!showMore)}
              className="w-full flex items-center justify-center gap-1.5 text-xs text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--accent-primary))] transition-colors py-2 group"
            >
              <span>{showMore ? t('editor:showLess') : t('editor:showMore')}</span>
              <ChevronDown className={`w-3.5 h-3.5 transition-transform duration-300 ${showMore ? 'rotate-180' : ''}`} />
            </button>

            {/* Secondary Actions - collapsible */}
            {showMore && (
              <div className="grid grid-cols-2 gap-3 animate-fade-in" style={{ animationDuration: '200ms' }}>
                {/* Create Character */}
                <div
                  onClick={() => handleCreateFromEmptyState('character')}
                  className={`group relative flex flex-col items-center gap-2 p-3 rounded-xl bg-gradient-to-br from-[hsl(var(--bg-secondary))] to-[hsl(var(--bg-tertiary)/0.5)] border border-[hsl(var(--border-color))] hover:border-[hsl(var(--accent-primary)/0.4)] hover:shadow-xl hover:shadow-[hsl(var(--accent-primary)/0.08)] transition-all duration-300 cursor-pointer transform hover:-translate-y-0.5 ${isCreating === 'character' ? 'opacity-50 pointer-events-none' : ''}`}
                >
                  <div className="absolute inset-0 rounded-xl bg-gradient-to-br from-[hsl(var(--accent-primary)/0.1)] to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-300" />
                  <div className="relative w-9 h-9 rounded-lg bg-gradient-to-br from-[hsl(var(--accent-primary)/0.12)] to-[hsl(var(--accent-primary)/0.06)] flex items-center justify-center group-hover:scale-110 transition-transform duration-300">
                    {isCreating === 'character' ? <LoadingSpinner size="sm" color="primary" /> : <Users className="w-4 h-4 text-[hsl(var(--accent-primary))]" />}
                  </div>
                  <span className="relative text-xs font-medium text-[hsl(var(--text-primary))] text-center">
                    {t('editor:fileTree.newCharacter')}
                  </span>
                </div>

                {/* Create Lore */}
                <div
                  onClick={() => handleCreateFromEmptyState('lore')}
                  className={`group relative flex flex-col items-center gap-2 p-3 rounded-xl bg-gradient-to-br from-[hsl(var(--bg-secondary))] to-[hsl(var(--bg-tertiary)/0.5)] border border-[hsl(var(--border-color))] hover:border-[hsl(var(--accent-primary)/0.4)] hover:shadow-xl hover:shadow-[hsl(var(--accent-primary)/0.08)] transition-all duration-300 cursor-pointer transform hover:-translate-y-0.5 ${isCreating === 'lore' ? 'opacity-50 pointer-events-none' : ''}`}
                >
                  <div className="absolute inset-0 rounded-xl bg-gradient-to-br from-[hsl(var(--accent-primary)/0.1)] to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-300" />
                  <div className="relative w-9 h-9 rounded-lg bg-gradient-to-br from-[hsl(var(--accent-primary)/0.12)] to-[hsl(var(--accent-primary)/0.06)] flex items-center justify-center group-hover:scale-110 transition-transform duration-300">
                    {isCreating === 'lore' ? <LoadingSpinner size="sm" color="primary" /> : <Sparkles className="w-4 h-4 text-[hsl(var(--accent-primary))]" />}
                  </div>
                  <span className="relative text-xs font-medium text-[hsl(var(--text-primary))] text-center">
                    {t('editor:fileTree.newLore')}
                  </span>
                </div>
              </div>
            )}
          </div>

          {/* Hints Section */}
          <div className="space-y-3 pt-2">
            {/* AI Hint */}
            <div className="flex items-center justify-center gap-2 text-xs text-[hsl(var(--text-secondary))] group cursor-default">
              <div className="p-1.5 rounded-md bg-[hsl(var(--accent-primary)/0.08)] group-hover:bg-[hsl(var(--accent-primary)/0.12)] transition-colors">
                <Zap className="w-3.5 h-3.5 text-[hsl(var(--accent-primary))]" />
              </div>
              <span>{t('editor:emptyStateHint')}</span>
            </div>

            {/* Keyboard Shortcut Hint (no physical keyboard on phones) */}
            {!isMobile && (
              <div className="flex items-center justify-center gap-2 text-xs text-[hsl(var(--text-secondary))] group cursor-default">
                <div className="p-1.5 rounded-md bg-[hsl(var(--bg-tertiary))] group-hover:bg-[hsl(var(--accent-primary)/0.08)] transition-colors">
                  <Keyboard className="w-3.5 h-3.5" />
                </div>
                <span className="flex items-center gap-1.5">
                  <kbd className="px-1.5 py-0.5 rounded bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--border-color))] text-[10px] font-mono shadow-sm">{isApplePlatform() ? '⌘' : 'Ctrl'}</kbd>
                  <kbd className="px-1.5 py-0.5 rounded bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--border-color))] text-[10px] font-mono shadow-sm">K</kbd>
                  <span className="text-[hsl(var(--text-tertiary))]">{t('editor:fileTree.searchFiles')}</span>
                </span>
              </div>
            )}
          </div>
        </div>
      </div>
    );
  }

  // Render folder selected state
  if (selectedItem.type === "folder") {
    return renderWithUpgradeModal(
      <div className="flex flex-col items-center justify-center h-full text-[hsl(var(--text-secondary))] gap-4">
        <Folder size={48} className="opacity-50" />
        <p className="text-sm">{t('editor:placeholder.folderSelected')}{selectedItem.title}</p>
        <p className="text-xs">{t('editor:placeholder.folderHint')}</p>
      </div>
    );
  }

  // Render loading state
  if (loading) {
    return renderWithUpgradeModal(
      <div className="flex flex-col items-center justify-center h-full gap-4">
        <LoadingSpinner size="lg" color="primary" />
        <p className="text-sm text-[hsl(var(--text-secondary))]">{t('common:loading')}</p>
      </div>
    );
  }

  // Render error state
  if (error) {
    return renderWithUpgradeModal(
      <div className="flex items-center justify-center h-full text-[hsl(var(--error))]">
        <p className="text-sm">{error}</p>
      </div>
    );
  }

  // Render file editor for all file types
  if (file) {
    // Check if this file is currently being streamed
    const isStreaming = file.id === streamingFileId;
    const displayContent = isStreaming ? streamingContent : editContent;

    // Check if this file is in diff review mode
    const isInReviewMode = diffReviewState?.isReviewing && diffReviewState.fileId === file.id;

    // Common editor props
    const editorProps = {
      userId: currentUserId || undefined,
      fileId: file.id,
      projectId: currentProjectId || undefined,
      fileType: file.file_type,
      fileTitle: editTitle,
      baseUpdatedAt: file.updated_at,
      title: editTitle,
      content: displayContent,
      onTitleChange: setEditTitle,
      onContentChange: setEditContent,
      onSave: handleSaveFile,
      onHistoryRestore: loadData,
      onFlushReady: registerEditorFlush,
      serverBaseline: serverBaseline?.fileId === file.id ? serverBaseline : undefined,
      getReviewLeaveDraft,
      isStreaming,
      // AI 正在编辑这份文件时挂起自动保存，避免过期整篇快照覆盖 AI 的改动
      isAiEditing: aiEditingFileId === file.id,
      onEnterDiffReview: enterNaturalPolishReview,
      // Diff review props
      diffReviewState: isInReviewMode ? diffReviewState : null,
      onAcceptEdit: acceptEdit,
      onRejectEdit: rejectEdit,
      onResetEdit: resetEdit,
      onAcceptAllEdits: acceptAllEdits,
      onRejectAllEdits: rejectAllEdits,
      onFinishReview: handleFinishReview,
      recoveredDraft: draftRecovery?.applied && draftRecovery.snapshot.fileId === file.id
        ? {
            capturedAt: draftRecovery.snapshot.capturedAt,
            serverTitle: draftRecovery.serverTitle,
            serverContent: draftRecovery.serverContent,
          }
        : undefined,
    };

    return renderWithUpgradeModal(
      <>
        {draftRecovery?.snapshot.fileId === file.id && (
          <div
            role={draftRecovery.kind === "conflict" && !draftRecovery.applied ? "alert" : "status"}
            className="border-b border-[hsl(var(--warning)/0.35)] bg-[hsl(var(--warning)/0.08)] px-4 py-3 text-sm text-[hsl(var(--text-primary))]"
          >
            <div className="flex flex-wrap items-center justify-between gap-3">
              <p>
                {t(draftRecovery.kind === "conflict" && !draftRecovery.applied
                  ? "editor:draftRecovery.conflict"
                  : "editor:draftRecovery.restored")}
              </p>
              <div className="flex flex-wrap gap-2">
                {draftRecovery.kind === "conflict" && !draftRecovery.applied && (
                  <button
                    type="button"
                    onClick={applyRecoveredDraft}
                    className="min-h-11 rounded bg-[hsl(var(--accent-primary))] px-3 py-1.5 text-xs font-medium text-white"
                  >
                    {t("editor:draftRecovery.restore")}
                  </button>
                )}
                <button
                  type="button"
                  onClick={discardRecoveredDraft}
                  className="min-h-11 rounded border border-[hsl(var(--border-color))] px-3 py-1.5 text-xs"
                >
                  {t("editor:draftRecovery.discard")}
                </button>
              </div>
            </div>
            {draftRecovery.kind === "conflict" && !draftRecovery.applied && (
              <details className="mt-2">
                <summary className="inline-flex min-h-11 cursor-pointer items-center text-xs font-medium">
                  {t("editor:draftRecovery.compare")}
                </summary>
                <div className="mt-2 grid gap-2 md:grid-cols-2">
                  <div>
                    <p className="mb-1 text-xs font-medium">{t("editor:draftRecovery.serverVersion")}</p>
                    <p className="mb-1 text-xs text-[hsl(var(--text-secondary))]">{draftRecovery.serverTitle}</p>
                    <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded bg-[hsl(var(--bg-primary))] p-2 text-xs">{draftRecovery.serverContent}</pre>
                  </div>
                  <div>
                    <p className="mb-1 text-xs font-medium">{t("editor:draftRecovery.localVersion")}</p>
                    <p className="mb-1 text-xs text-[hsl(var(--text-secondary))]">{draftRecovery.snapshot.title}</p>
                    <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded bg-[hsl(var(--bg-primary))] p-2 text-xs">{draftRecovery.snapshot.content}</pre>
                  </div>
                </div>
              </details>
            )}
          </div>
        )}
        {serverSyncIssue?.fileId === file.id && (
          <div
            role="alert"
            className="border-b border-[hsl(var(--warning)/0.35)] bg-[hsl(var(--warning)/0.08)] px-4 py-3 text-sm text-[hsl(var(--text-primary))]"
          >
            <div className="flex flex-wrap items-center justify-between gap-3">
              <p>{t("editor:serverSyncFailed.message")}</p>
              <button
                type="button"
                onClick={retryServerSync}
                disabled={serverSyncIssue.retrying}
                className="min-h-11 rounded bg-[hsl(var(--accent-primary))] px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
              >
                {t(serverSyncIssue.retrying ? "editor:serverSyncFailed.retrying" : "editor:serverSyncFailed.retry")}
              </button>
            </div>
          </div>
        )}
        <SimpleEditor {...editorProps} />
      </>
    );
  }

  return renderWithUpgradeModal(null);
};

export const Editor = React.memo(EditorComponent);
