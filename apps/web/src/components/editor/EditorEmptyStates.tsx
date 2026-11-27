/**
 * Non-editing Editor views: Material Library preview, empty-state onboarding cards,
 * and folder-selected placeholder.
 *
 * @module components/editor/EditorEmptyStates
 */

import React, { lazy, Suspense, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  BookOpen,
  ChevronDown,
  Clapperboard,
  FileText,
  Folder,
  FolderOpen,
  Keyboard,
  Plus,
  Sparkles,
  Upload,
  Users,
  Zap,
} from "lucide-react";
import { Button } from "../ui/Button";
import { LoadingSpinner } from "../LoadingSpinner";
import { DRAFT_IMPORT_ACCEPT } from "../../hooks/useDraftImport";
import { FOLDER_TYPE_MAP } from "../../lib/folderTypeMap";
import { toast } from "../../lib/toast";
import type { MaterialLibraryState } from "../../hooks/useMaterialLibrary";
import { isApplePlatform, type EmptyStateFileType } from "./editorFileHelpers";

const MaterialPreview = lazy(() =>
  import("../MaterialPreview").then((m) => ({ default: m.MaterialPreview })),
);
const ImportMaterialDialog = lazy(() =>
  import("../ImportMaterialDialog").then((m) => ({ default: m.ImportMaterialDialog })),
);

export interface EditorMaterialPreviewProps {
  previewEntityInfo: NonNullable<MaterialLibraryState["previewEntityInfo"]>;
  preview: MaterialLibraryState["preview"];
  isPreviewLoading: boolean;
  isMaterialAttached?: (id: string) => boolean;
  addMaterial: (
    id: string,
    title: string,
    librarySource?: { novelId: number; entityType: string; entityId: number },
  ) => boolean;
  isAtLimit?: boolean;
  maxMaterials: number;
  clearPreview: () => void;
  triggerFileTreeRefresh: () => void;
}

export const EditorMaterialPreview: React.FC<EditorMaterialPreviewProps> = ({
  previewEntityInfo,
  preview,
  isPreviewLoading,
  isMaterialAttached,
  addMaterial,
  isAtLimit,
  maxMaterials,
  clearPreview,
  triggerFileTreeRefresh,
}) => {
  const { t } = useTranslation(["editor", "common"]);
  const [importDialogOpen, setImportDialogOpen] = useState(false);

  return (
    <>
      <Suspense
        fallback={
          <div className="flex flex-col items-center justify-center h-full gap-4">
            <LoadingSpinner size="lg" color="primary" />
            <p className="text-sm text-[hsl(var(--text-secondary))]">{t("common:loading")}</p>
          </div>
        }
      >
        <MaterialPreview
          preview={preview}
          isLoading={isPreviewLoading}
          onAddToProject={() => setImportDialogOpen(true)}
          onAttachToChat={() => {
            if (!preview) return;
            const virtualId = `lib_${previewEntityInfo.novelId}_${previewEntityInfo.entityType}_${previewEntityInfo.entityId}`;
            if (isMaterialAttached?.(virtualId)) {
              toast.info(t("editor:fileTree.alreadyAttached"));
              return;
            }
            const added = addMaterial(virtualId, preview.title, {
              novelId: previewEntityInfo.novelId,
              entityType: previewEntityInfo.entityType,
              entityId: previewEntityInfo.entityId,
            });
            if (!added) {
              toast.info(
                isAtLimit === false
                  ? t("editor:fileTree.alreadyAttached")
                  : t("editor:fileTree.maxMaterials", { max: maxMaterials }),
              );
              return;
            }
            clearPreview();
          }}
          onBack={clearPreview}
        />
      </Suspense>
      {importDialogOpen && preview && (
        <Suspense fallback={null}>
          <ImportMaterialDialog
            isOpen={importDialogOpen}
            onClose={() => setImportDialogOpen(false)}
            preview={preview}
            novelId={previewEntityInfo.novelId}
            entityType={previewEntityInfo.entityType}
            entityId={previewEntityInfo.entityId}
            onSuccess={() => {
              setImportDialogOpen(false);
              clearPreview();
              triggerFileTreeRefresh();
            }}
          />
        </Suspense>
      )}
    </>
  );
};

export interface EditorEmptyStateProps {
  isScreenplay: boolean;
  isMobile: boolean;
  isCreating: string | null;
  isImportingDrafts: boolean;
  currentProjectId: string | null;
  onAskAi: () => void;
  onCreateFile: (fileType: EmptyStateFileType) => void;
  onImportDrafts: (event: React.ChangeEvent<HTMLInputElement>) => Promise<void>;
}

export const EditorEmptyState: React.FC<EditorEmptyStateProps> = ({
  isScreenplay,
  isMobile,
  isCreating,
  isImportingDrafts,
  currentProjectId,
  onAskAi,
  onCreateFile,
  onImportDrafts,
}) => {
  const { t } = useTranslation(["editor"]);
  const [showMore, setShowMore] = useState(false);
  const draftInputRef = useRef<HTMLInputElement>(null);
  const primaryFileType: EmptyStateFileType = isScreenplay ? "script" : "draft";

  return (
    // Centred while it fits; on a short window it scrolls from the top instead of pushing the
    // heading under the header and the last hints out of reach.
    <div className="h-full overflow-y-auto">
      <div className="relative flex min-h-full flex-col items-center justify-center p-6 md:p-8">
        <div className="max-w-md w-full space-y-6">
          {/* Headline */}
          <div className="text-center space-y-3">
            <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-[hsl(var(--accent-primary)/0.1)] mb-3">
              <Sparkles className="w-7 h-7 text-[hsl(var(--accent-primary))]" />
            </div>
            <h2 className="text-xl md:text-2xl font-semibold text-[hsl(var(--text-primary))]">
              {t("editor:emptyStateTitle")}
            </h2>
            <p className="text-sm text-[hsl(var(--text-secondary))]">
              {isScreenplay ? t("editor:emptyStateDescriptionScript") : t("editor:emptyStateDescription")}
            </p>
          </div>

          {/* The view's one primary action: the chat, where an empty first conversation offers
              starter cards. Phones switch panels without raising the keyboard over those cards. */}
          <Button
            type="button"
            data-testid="empty-state-ask-ai"
            size={isMobile ? "touch" : "md"}
            className="w-full"
            leftIcon={<Zap className="w-4 h-4" />}
            onClick={onAskAi}
          >
            {t("editor:emptyStateHint")}
          </Button>

          {/* Write it yourself - 2 cards */}
          <div className="grid grid-cols-2 gap-3">
            {/* Create Draft (screenplay projects: Script) */}
            <button
              type="button"
              disabled={isCreating === primaryFileType}
              onClick={() => onCreateFile(primaryFileType)}
              className="group flex flex-col items-center gap-2.5 p-4 rounded-xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] hover:border-[hsl(var(--accent-primary)/0.4)] hover:bg-[hsl(var(--bg-tertiary)/0.4)] transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-primary))] disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <div className="w-11 h-11 rounded-xl bg-[hsl(var(--accent-primary)/0.1)] flex items-center justify-center">
                {isCreating === primaryFileType ? (
                  <LoadingSpinner size="md" color="primary" />
                ) : isScreenplay ? (
                  <Clapperboard className="w-5 h-5 text-[hsl(var(--accent-primary))]" />
                ) : (
                  <BookOpen className="w-5 h-5 text-[hsl(var(--accent-primary))]" />
                )}
              </div>
              <span className="text-sm font-medium text-[hsl(var(--text-primary))] text-center">
                {isScreenplay ? t("editor:fileTree.newScript") : t("editor:fileTree.newDraft")}
              </span>
            </button>

            {/* Create Outline */}
            <button
              type="button"
              disabled={isCreating === "outline"}
              onClick={() => onCreateFile("outline")}
              className="group flex flex-col items-center gap-2.5 p-4 rounded-xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] hover:border-[hsl(var(--accent-primary)/0.4)] hover:bg-[hsl(var(--bg-tertiary)/0.4)] transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-primary))] disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <div className="w-11 h-11 rounded-xl bg-[hsl(var(--accent-primary)/0.1)] flex items-center justify-center">
                {isCreating === "outline" ? (
                  <LoadingSpinner size="md" color="primary" />
                ) : (
                  <FileText className="w-5 h-5 text-[hsl(var(--accent-primary))]" />
                )}
              </div>
              <span className="text-sm font-medium text-[hsl(var(--text-primary))] text-center">
                {t("editor:fileTree.newOutline")}
              </span>
            </button>
          </div>

          {/* More Options Toggle */}
          <div className="space-y-2">
            <button
              type="button"
              aria-expanded={showMore}
              onClick={() => setShowMore(!showMore)}
              className="w-full min-h-11 md:min-h-0 flex items-center justify-center gap-1.5 rounded-lg text-xs text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--accent-primary))] transition-colors py-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))] group"
            >
              <span>{showMore ? t("editor:showLess") : t("editor:showMore")}</span>
              <ChevronDown className={`w-3.5 h-3.5 motion-chevron ${showMore ? "rotate-180" : ""}`} />
            </button>

            {/* Secondary Actions - collapsible */}
            {showMore && (
              <div className="grid grid-cols-3 gap-3 motion-disclosure-enter">
                {/* Create Character */}
                <button
                  type="button"
                  disabled={isCreating === "character"}
                  onClick={() => onCreateFile("character")}
                  className="group flex flex-col items-center gap-2 p-3 rounded-xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] hover:border-[hsl(var(--accent-primary)/0.4)] hover:bg-[hsl(var(--bg-tertiary)/0.4)] transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-primary))] disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <div className="w-9 h-9 rounded-lg bg-[hsl(var(--accent-primary)/0.1)] flex items-center justify-center">
                    {isCreating === "character" ? (
                      <LoadingSpinner size="sm" color="primary" />
                    ) : (
                      <Users className="w-4 h-4 text-[hsl(var(--accent-primary))]" />
                    )}
                  </div>
                  <span className="text-xs font-medium text-[hsl(var(--text-primary))] text-center">
                    {t("editor:fileTree.newCharacter")}
                  </span>
                </button>

                {/* Create Lore */}
                <button
                  type="button"
                  disabled={isCreating === "lore"}
                  onClick={() => onCreateFile("lore")}
                  className="group flex flex-col items-center gap-2 p-3 rounded-xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] hover:border-[hsl(var(--accent-primary)/0.4)] hover:bg-[hsl(var(--bg-tertiary)/0.4)] transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-primary))] disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <div className="w-9 h-9 rounded-lg bg-[hsl(var(--accent-primary)/0.1)] flex items-center justify-center">
                    {isCreating === "lore" ? (
                      <LoadingSpinner size="sm" color="primary" />
                    ) : (
                      <Sparkles className="w-4 h-4 text-[hsl(var(--accent-primary))]" />
                    )}
                  </div>
                  <span className="text-xs font-medium text-[hsl(var(--text-primary))] text-center">
                    {t("editor:fileTree.newLore")}
                  </span>
                </button>

                {/* Create Snippet / Material */}
                <button
                  type="button"
                  disabled={isCreating === "snippet"}
                  onClick={() => onCreateFile("snippet")}
                  className="group flex flex-col items-center gap-2 p-3 rounded-xl border border-[hsl(var(--border-color))] bg-[hsl(var(--bg-secondary))] hover:border-[hsl(var(--accent-primary)/0.4)] hover:bg-[hsl(var(--bg-tertiary)/0.4)] transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[hsl(var(--accent-primary))] focus-visible:ring-offset-2 focus-visible:ring-offset-[hsl(var(--bg-primary))] disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <div className="w-9 h-9 rounded-lg bg-[hsl(var(--accent-primary)/0.1)] flex items-center justify-center">
                    {isCreating === "snippet" ? (
                      <LoadingSpinner size="sm" color="primary" />
                    ) : (
                      <FolderOpen className="w-4 h-4 text-[hsl(var(--accent-primary))]" />
                    )}
                  </div>
                  <span className="text-xs font-medium text-[hsl(var(--text-primary))] text-center">
                    {t("editor:fileTree.newSnippet")}
                  </span>
                </button>
              </div>
            )}
          </div>

          {/* Hints Section */}
          <div className="space-y-3 pt-2">
            <input
              ref={draftInputRef}
              type="file"
              accept={DRAFT_IMPORT_ACCEPT}
              multiple
              className="hidden"
              aria-label={t("editor:emptyStateImportDrafts")}
              data-testid="empty-state-import-input"
              onChange={(event) => {
                void onImportDrafts(event);
              }}
            />
            <button
              type="button"
              data-testid="empty-state-import-drafts"
              onClick={() => draftInputRef.current?.click()}
              disabled={isImportingDrafts || !currentProjectId}
              className="mx-auto flex min-h-[44px] md:min-h-0 items-center justify-center gap-2 rounded-md px-2 py-1 text-xs text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--text-primary))] disabled:opacity-50 group"
            >
              <span className="p-1.5 rounded-md bg-[hsl(var(--bg-tertiary))] group-hover:bg-[hsl(var(--accent-primary)/0.08)] transition-colors">
                {isImportingDrafts ? (
                  <LoadingSpinner size="sm" color="primary" />
                ) : (
                  <Upload className="w-3.5 h-3.5" />
                )}
              </span>
              <span>{t("editor:emptyStateImportDrafts")}</span>
            </button>

            {/* Keyboard Shortcut Hint (no physical keyboard on phones) */}
            {!isMobile && (
              <div className="flex items-center justify-center gap-2 text-xs text-[hsl(var(--text-secondary))] group cursor-default">
                <div className="p-1.5 rounded-md bg-[hsl(var(--bg-tertiary))] group-hover:bg-[hsl(var(--accent-primary)/0.08)] transition-colors">
                  <Keyboard className="w-3.5 h-3.5" />
                </div>
                <span className="flex items-center gap-1.5">
                  <kbd className="px-1.5 py-0.5 rounded bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--border-color))] text-[10px] font-mono shadow-sm">
                    {isApplePlatform() ? "⌘" : "Ctrl"}
                  </kbd>
                  <kbd className="px-1.5 py-0.5 rounded bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--border-color))] text-[10px] font-mono shadow-sm">
                    K
                  </kbd>
                  <span className="text-[hsl(var(--text-tertiary))]">
                    {t("editor:fileTree.searchFiles")}
                  </span>
                </span>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export interface EditorFolderStateProps {
  folderId: string;
  folderTitle: string;
  isScreenplay: boolean;
  isMobile: boolean;
  isCreating: string | null;
  onCreateInFolder: (fileType: EmptyStateFileType, folderId: string) => void;
}

export const EditorFolderState: React.FC<EditorFolderStateProps> = ({
  folderId,
  folderTitle,
  isScreenplay,
  isMobile,
  isCreating,
  onCreateInFolder,
}) => {
  const { t } = useTranslation(["editor"]);
  const mappedType = FOLDER_TYPE_MAP[folderTitle];
  const folderFileType: EmptyStateFileType =
    mappedType === "outline" ||
    mappedType === "character" ||
    mappedType === "lore" ||
    mappedType === "snippet" ||
    mappedType === "script" ||
    mappedType === "draft"
      ? mappedType
      : isScreenplay
        ? "script"
        : "draft";

  const folderCreateLabels: Record<EmptyStateFileType, string> = {
    draft: t("editor:fileTree.newDraft"),
    outline: t("editor:fileTree.newOutline"),
    character: t("editor:fileTree.newCharacter"),
    lore: t("editor:fileTree.newLore"),
    snippet: t("editor:fileTree.newSnippet"),
    script: t("editor:fileTree.newScript"),
  };

  return (
    <div className="flex flex-col items-center justify-center h-full text-[hsl(var(--text-secondary))] gap-3 p-6 text-center">
      <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-[hsl(var(--bg-secondary))] border border-[hsl(var(--border-color))]">
        <Folder size={28} className="text-[hsl(var(--text-secondary))]" />
      </div>
      <div className="space-y-1">
        <p className="text-sm font-medium text-[hsl(var(--text-primary))]">
          {t("editor:placeholder.folderSelected")}
          {folderTitle}
        </p>
        <p className="text-xs text-[hsl(var(--text-secondary))]">
          {t("editor:placeholder.folderHint")}
        </p>
      </div>
      <Button
        type="button"
        size={isMobile ? "touch" : "md"}
        isLoading={isCreating === folderFileType}
        leftIcon={<Plus className="w-4 h-4" />}
        onClick={() => onCreateInFolder(folderFileType, folderId)}
        data-testid="folder-empty-create-button"
        className="mt-1"
      >
        {folderCreateLabels[folderFileType]}
      </Button>
    </div>
  );
};
