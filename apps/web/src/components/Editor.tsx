/**
 * @fileoverview Content editor component for the zenstory writing workbench.
 *
 * Thin container shell composing:
 * - Conflict review session & reviewed-file commit (`useEditorConflictReview`)
 * - File loading, SWR monotonic sync, draft recovery & save (`useEditorFileSync`)
 * - Safety banners (`EditorBanners`) and non-editing states (`EditorEmptyStates`)
 * - Rich text / markdown editing (`SimpleEditor`)
 *
 * @module components/Editor
 */

import React, { useState, useEffect, useCallback, useRef, useContext } from "react";
import { QueryClientContext } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useProject } from "../contexts/ProjectContext";
import { useAuth } from "../contexts/AuthContext";
import { useMaterialLibraryContext } from "../contexts/MaterialLibraryContext";
import { useMaterialAttachment } from "../contexts/MaterialAttachmentContext";
import { fileApi } from "../lib/api";
import { toast } from "../lib/toast";
import { logger } from "../lib/logger";
import { FirstWorkGuidance } from "./FirstWorkGuidance";
import { Button } from "./ui/Button";
import { useDraftImport } from "../hooks/useDraftImport";
import { SimpleEditor } from "./SimpleEditor";
import type { TreeNodeType } from "../types";
import { LoadingSpinner } from "./LoadingSpinner";
import { UpgradePromptModal } from "./subscription/UpgradePromptModal";
import { useMobileLayout } from "../contexts/MobileLayoutContext";
import {
  resolveCreateParentId,
  type EmptyStateFileType,
} from "./editor/editorFileHelpers";
import {
  DraftRecoveryBanner,
  RemovedDraftsBanner,
  ServerSyncIssueBanner,
} from "./editor/EditorBanners";
import {
  EditorEmptyState,
  EditorFolderState,
  EditorMaterialPreview,
} from "./editor/EditorEmptyStates";
import {
  useConflictReviewSession,
  useReviewedFileCommit,
} from "./editor/useEditorConflictReview";
import { useEditorFileSync } from "./editor/useEditorFileSync";

/**
 * Props interface for the Editor component.
 * Currently accepts no props as all state is managed through ProjectContext.
 */
// eslint-disable-next-line @typescript-eslint/no-empty-object-type
export interface EditorProps {}

const EditorComponent: React.FC<EditorProps> = () => {
  const { t } = useTranslation(["editor", "common"]);
  const queryClient = useContext(QueryClientContext);
  const { user } = useAuth();
  const currentUserId = user?.id ?? null;
  const { isMobile, switchToChat, focusChatInput } = useMobileLayout();
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
    isChatStreaming,
    requestAgentUiAction,
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

  const materialLib = useMaterialLibraryContext();
  const { addMaterial, isMaterialAttached, isAtLimit, maxMaterials = 5 } = useMaterialAttachment();

  // Draft import from empty state
  const { importFiles: importDrafts, isImporting: isImportingDrafts } = useDraftImport();
  const importProjectIdRef = useRef(currentProjectId);
  useEffect(() => {
    importProjectIdRef.current = currentProjectId;
  }, [currentProjectId]);

  const handleImportDrafts = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const picked = Array.from(event.target.files ?? []);
    event.target.value = "";
    const projectId = currentProjectId;
    if (!projectId || picked.length === 0) return;
    const { files: created } = await importDrafts(projectId, picked, "editor_empty");
    if (!created.length || importProjectIdRef.current !== projectId) return;
    triggerFileTreeRefresh();
    const first = created[0];
    setSelectedItem({ id: first.id, title: first.title, type: first.file_type as TreeNodeType });
  };

  // Creation loading state & newly created desktop title focus
  const [isCreating, setIsCreating] = useState<string | null>(null);
  const [newlyCreatedFileId, setNewlyCreatedFileId] = useState<string | null>(null);

  const currentReviewRef = useRef(diffReviewState);
  currentReviewRef.current = diffReviewState;

  const {
    conflictReviewRef,
    openConflictReview,
    handleAcceptEdit,
    handleRejectEdit,
    handleResetEdit,
    handleAcceptAllEdits,
    handleRejectAllEdits,
  } = useConflictReviewSession({
    currentReviewRef,
    enterDiffReview,
    acceptEdit,
    rejectEdit,
    resetEdit,
    acceptAllEdits,
    rejectAllEdits,
    exitDiffReview,
  });

  const handleProjectChange = useCallback(() => {
    setIsCreating(null);
  }, []);

  const fileSync = useEditorFileSync({
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
    onProjectChange: handleProjectChange,
  });

  const { handleFinishReview } = useReviewedFileCommit({
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
    file: fileSync.file,
    setFile: fileSync.setFile,
    setEditContent: fileSync.setEditContent,
    draftRecovery: fileSync.draftRecovery,
    setDraftRecovery: fileSync.setDraftRecovery,
    adoptServerCopy: fileSync.adoptServerCopy,
    markServerBaseline: fileSync.markServerBaseline,
    openConflictReview,
    conflictReviewRef,
    activeProjectIdRef: fileSync.activeProjectIdRef,
    selectedIdRef: fileSync.selectedIdRef,
    currentFileRef: fileSync.currentFileRef,
    currentReviewRef: fileSync.currentReviewRef,
    editTitleRef: fileSync.editTitleRef,
    serverSyncSeqRef: fileSync.serverSyncSeqRef,
    openContextRef: fileSync.openContextRef,
  });

  const handleRequestNaturalPolish = useCallback(
    (params: {
      projectId: string;
      fileId: string;
      fileType?: string;
      fileTitle?: string;
      selectedText?: string;
    }) => {
      requestAgentUiAction?.({
        projectId: params.projectId,
        action: "natural_polish",
        fileId: params.fileId,
        fileType: params.fileType,
        fileTitle: params.fileTitle,
        selectedText: params.selectedText,
      });
    },
    [requestAgentUiAction],
  );

  const handleCreateFromEmptyState = useCallback(
    async (fileType: EmptyStateFileType, explicitParentId?: string) => {
      if (!currentProjectId) {
        toast.error(t("editor:error.noProject"));
        return;
      }

      setIsCreating(fileType);

      const defaultTitles: Record<EmptyStateFileType, string> = {
        draft: t("editor:fileTree.newDraft"),
        outline: t("editor:fileTree.newOutline"),
        character: t("editor:fileTree.newCharacter"),
        lore: t("editor:fileTree.newLore"),
        snippet: t("editor:fileTree.newSnippet"),
        script: t("editor:fileTree.newScript"),
      };

      try {
        const parentId = explicitParentId ?? (await resolveCreateParentId(currentProjectId, fileType));
        if (fileSync.activeProjectIdRef.current !== currentProjectId) return;
        const newFile = await fileApi.create(currentProjectId, {
          title: defaultTitles[fileType],
          file_type: fileType,
          parent_id: parentId,
          content: "",
        });

        if (fileSync.activeProjectIdRef.current === currentProjectId) {
          triggerFileTreeRefresh();
          setNewlyCreatedFileId(newFile.id);
          setSelectedItem({
            id: newFile.id,
            type: newFile.file_type,
            title: newFile.title,
          });
        }

        toast.success(t("editor:success.fileCreated"));
      } catch (err) {
        toast.error(t("editor:error.createFailed"));
        logger.error("Failed to create file:", err);
      } finally {
        if (fileSync.activeProjectIdRef.current === currentProjectId) setIsCreating(null);
      }
    },
    [currentProjectId, fileSync.activeProjectIdRef, triggerFileTreeRefresh, setSelectedItem, t],
  );

  const renderWithUpgradeModal = (content: React.ReactNode) => (
    <>
      <div className="flex h-full min-h-0 flex-col">
        <div className="max-h-[45%] min-w-0 shrink-0 overflow-y-auto">
          <RemovedDraftsBanner
            removedDrafts={fileSync.removedDrafts}
            currentUserId={currentUserId}
            currentProjectId={currentProjectId}
          />
        </div>
        <div className="min-h-0 flex-1">{content}</div>
      </div>
      <UpgradePromptModal
        open={fileSync.showFileVersionUpgradeModal}
        onClose={() => fileSync.setShowFileVersionUpgradeModal(false)}
        promptId="file_version_quota_blocked"
        title={t("editor:versionHistory.fileVersionLimitTitle")}
        description={t("editor:versionHistory.fileVersionLimitUpgrade")}
        paidDescription={t("editor:versionHistory.fileVersionLimitPaid", {
          defaultValue: "正文照常保存，只是这个文件不再生成新版本。",
        })}
        primaryLabel={t("common:viewUpgrade")}
        secondaryLabel={t("common:viewPlans")}
      />
    </>
  );

  // Render material preview mode
  if (materialLib.previewEntityInfo && (materialLib.isPreviewLoading || materialLib.preview)) {
    return renderWithUpgradeModal(
      <EditorMaterialPreview
        previewEntityInfo={materialLib.previewEntityInfo}
        preview={materialLib.preview}
        isPreviewLoading={materialLib.isPreviewLoading}
        isMaterialAttached={isMaterialAttached}
        addMaterial={addMaterial}
        isAtLimit={isAtLimit}
        maxMaterials={maxMaterials}
        clearPreview={materialLib.clearPreview}
        triggerFileTreeRefresh={triggerFileTreeRefresh}
      />,
    );
  }

  // Render empty state
  if (!selectedItem) {
    return renderWithUpgradeModal(
      <EditorEmptyState
        isScreenplay={currentProject?.project_type === "screenplay"}
        isMobile={isMobile}
        isCreating={isCreating}
        isImportingDrafts={isImportingDrafts}
        currentProjectId={currentProjectId}
        onAskAi={isMobile ? switchToChat : focusChatInput}
        onCreateFile={(fileType) => void handleCreateFromEmptyState(fileType)}
        onImportDrafts={handleImportDrafts}
      />,
    );
  }

  // Render folder selected state
  if (selectedItem.type === "folder") {
    return renderWithUpgradeModal(
      <EditorFolderState
        folderId={selectedItem.id}
        folderTitle={selectedItem.title}
        isScreenplay={currentProject?.project_type === "screenplay"}
        isMobile={isMobile}
        isCreating={isCreating}
        onCreateInFolder={(fileType, folderId) => void handleCreateFromEmptyState(fileType, folderId)}
      />,
    );
  }

  // Render loading state
  if (fileSync.loading) {
    return renderWithUpgradeModal(
      <div className="flex flex-col items-center justify-center h-full gap-4">
        <LoadingSpinner size="lg" color="primary" />
        <p className="text-sm text-[hsl(var(--text-secondary))]">{t("common:loading")}</p>
      </div>,
    );
  }

  // Render error state
  if (fileSync.error) {
    return renderWithUpgradeModal(
      <div
        role="alert"
        className="flex flex-col items-center justify-center h-full gap-3 p-6 text-center text-[hsl(var(--error-text))]"
      >
        <p className="text-sm">{fileSync.error}</p>
        <Button
          type="button"
          variant="secondary"
          size={isMobile ? "touch" : "sm"}
          onClick={() => void fileSync.loadData()}
        >
          {t("common:retry")}
        </Button>
      </div>,
    );
  }

  // Render file editor for all file types
  if (fileSync.file) {
    const file = fileSync.file;
    const isStreaming = file.id === streamingFileId;
    const isInReviewMode = diffReviewState?.isReviewing && diffReviewState.fileId === file.id;

    const editorProps = {
      emptyHint: (focusEditor: () => void) => (
        <FirstWorkGuidance
          userId={currentUserId || null}
          projectId={currentProjectId}
          fileId={file.id}
          focusEditor={focusEditor}
        />
      ),
      userId: currentUserId || undefined,
      fileId: file.id,
      projectId: currentProjectId || undefined,
      fileType: file.file_type,
      fileTitle: fileSync.editTitle,
      baseUpdatedAt: file.updated_at,
      savedContent: file.content ?? "",
      title: fileSync.editTitle,
      content: fileSync.editContent,
      streamingContent: isStreaming ? streamingContent : undefined,
      onTitleChange: fileSync.setEditTitle,
      onContentChange: fileSync.setEditContent,
      onSave: fileSync.handleSaveFile,
      onHistoryRestore: () => fileSync.loadData({ discardUnsaved: true }),
      onFlushReady: fileSync.registerEditorFlush,
      serverBaseline:
        fileSync.serverBaseline?.fileId === file.id ? fileSync.serverBaseline : undefined,
      getReviewLeaveDraft: fileSync.getReviewLeaveDraft,
      isStreaming,
      isChatStreaming: Boolean(isChatStreaming),
      isAiEditing: aiEditingFileId === file.id,
      autoFocusTitle: !isMobile && newlyCreatedFileId === file.id,
      onAutoFocusConsumed:
        newlyCreatedFileId === file.id ? () => setNewlyCreatedFileId(null) : undefined,
      onNaturalPolish: handleRequestNaturalPolish,
      diffReviewState: isInReviewMode ? diffReviewState : null,
      onAcceptEdit: handleAcceptEdit,
      onRejectEdit: handleRejectEdit,
      onResetEdit: handleResetEdit,
      onAcceptAllEdits: handleAcceptAllEdits,
      onRejectAllEdits: handleRejectAllEdits,
      onFinishReview: handleFinishReview,
      recoveredDraft:
        fileSync.draftRecovery?.applied && fileSync.draftRecovery.snapshot.fileId === file.id
          ? {
              capturedAt: fileSync.draftRecovery.snapshot.capturedAt,
              serverTitle: fileSync.draftRecovery.serverTitle,
              serverContent: fileSync.draftRecovery.serverContent,
            }
          : undefined,
    };

    return renderWithUpgradeModal(
      <div className="flex h-full min-h-0 flex-col">
        <div className="max-h-[45%] min-w-0 shrink-0 overflow-y-auto">
          {fileSync.draftRecovery?.snapshot.fileId === file.id && (
            <DraftRecoveryBanner
              draftRecovery={fileSync.draftRecovery}
              isMobile={isMobile}
              onApply={fileSync.applyRecoveredDraft}
              onDiscard={fileSync.discardRecoveredDraft}
            />
          )}
          {fileSync.serverSyncIssue?.fileId === file.id && (
            <ServerSyncIssueBanner
              serverSyncIssue={fileSync.serverSyncIssue}
              isMobile={isMobile}
              onRetry={fileSync.retryServerSync}
            />
          )}
        </div>
        <div className="min-h-0 flex-1">
          <SimpleEditor {...editorProps} />
        </div>
      </div>,
    );
  }

  return renderWithUpgradeModal(null);
};

export const Editor = React.memo(EditorComponent);
