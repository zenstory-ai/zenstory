import React, { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { useMobileLayout } from "../../contexts/MobileLayoutContext";
import { useMaterialAttachment, MAX_ATTACHED_MATERIALS } from "../../contexts/MaterialAttachmentContext";
import { toast } from "../../lib/toast";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { Button } from "../ui/Button";
import type { FileTreeNode } from "../../types";
import { useFileSearch } from "../../hooks/useFileSearch";
import { DRAFT_IMPORT_ACCEPT, useDraftImport } from "../../hooks/useDraftImport";
import { getCreateFileType, isMaterialFolder, useFileTree } from "../../hooks/useFileTree";
import { useFileSearchContext } from "../../contexts/FileSearchContext";
import { FileSearchInput } from "../FileSearchInput";
import SearchResultsDropdown from "../SearchResultsDropdown";
import {
  ChevronDown,
  ChevronRight,
  FileText,
  Users,
  BookOpen,
  Sparkles,
  FolderOpen,
  Plus,
  Upload,
  Trash2,
  Folder,
  MessageSquarePlus,
  Check,
} from "lucide-react";

// Draft folder names (both zh and en)
const DRAFT_FOLDER_NAMES = ["正文", "Drafts", "Draft"];

/**
 * Arrow-key focus movement between the visible rows of the file tree (WAI-ARIA tree pattern).
 * Collapsed folders render no children, so the rendered rows are exactly the visible ones.
 * Returns whether the key is a tree navigation key.
 */
function moveTreeFocus(row: HTMLElement, key: string): boolean {
  const rows = Array.from(row.closest('[role="tree"]')?.querySelectorAll<HTMLElement>('[role="treeitem"]') ?? []);
  const index = rows.indexOf(row);
  const level = (item: HTMLElement | undefined) => Number(item?.getAttribute("aria-level"));
  let target: HTMLElement | undefined;
  switch (key) {
    case "ArrowDown": target = rows[index + 1]; break;
    case "ArrowUp": target = rows[index - 1]; break;
    case "Home": target = rows[0]; break;
    case "End": target = rows[rows.length - 1]; break;
    // An open folder's first child is the next row, one level deeper.
    case "ArrowRight": target = level(rows[index + 1]) === level(row) + 1 ? rows[index + 1] : undefined; break;
    // A row inside a folder moves to that folder: the nearest earlier row one level up.
    case "ArrowLeft": target = rows.slice(0, index).reverse().find((item) => level(item) === level(row) - 1); break;
    default: return false;
  }
  target?.focus();
  return true;
}

// FileTreePane component - file tree pane for sidebar
export const FileTreePane: React.FC = () => {
  const { t } = useTranslation(['editor', 'common']);
  const { switchToEditor, isMobile } = useMobileLayout();
  const { addMaterial, removeMaterial, isMaterialAttached, isAtLimit } = useMaterialAttachment();

  const {
    currentProjectId,
    selectedItem,
    triggerFileTreeRefresh,
    activeProjectIdRef,
    mountedRef,
    tree,
    loading,
    isInitialLoad,
    loadError,
    expandedFolders,
    isCreating,
    newItemName,
    setNewItemName,
    newItemType,
    pendingDelete,
    setPendingDelete,
    deletingFile,
    isUploading,
    fileInputRef,
    loadData,
    toggleFolder,
    handleSelect,
    startCreate,
    cancelCreate,
    handleCreate,
    handleDelete,
    handleConfirmDelete,
    getPlaceholder,
    triggerUpload,
    handleFileUpload,
  } = useFileTree({
    uploadErrorMode: "toast",
    onSelectItem: () => {
      // On mobile, automatically switch to editor panel after selecting a file
      if (isMobile) {
        switchToEditor();
      }
    },
  });

  // A brand-new work has no file anywhere: one "empty" hint per folder would only repeat itself
  // beside the editor's own start. The hints return once the work has any file.
  const treeHasFiles = useMemo(() => {
    const hasFile = (nodes: FileTreeNode[]): boolean =>
      nodes.some((node) => node.file_type !== "folder" || hasFile(node.children ?? []));
    return hasFile(tree);
  }, [tree]);

  // Draft import state (desktop/sidebar only)
  const { importFiles: importDrafts, isImporting: isImportingDrafts } = useDraftImport();
  const draftFileInputRef = useRef<HTMLInputElement>(null);

  const { isSearchOpen: isFileSearchOpen, openSearch, closeSearch } = useFileSearchContext();

  const [fileSearchQuery, setFileSearchQuery] = useState('');
  const [searchSelection, setSearchSelection] = useState<{ projectId: string | null; index: number }>({
    projectId: currentProjectId,
    index: 0,
  });
  const selectedResultIndex =
    searchSelection.projectId === currentProjectId ? searchSelection.index : 0;
  const setSelectedResultIndex = useCallback(
    (updater: number | ((prev: number) => number)) => {
      setSearchSelection((prev) => {
        const current = prev.projectId === currentProjectId ? prev.index : 0;
        const next = typeof updater === 'function' ? updater(current) : updater;
        return { projectId: currentProjectId, index: next };
      });
    },
    [currentProjectId],
  );

  // Icon mapping for file types
  const FILE_TYPE_ICONS = useMemo<Record<string, React.ReactNode>>(() => ({
    folder: <Folder size={16} />,
    lore: <Sparkles size={16} />,
    character: <Users size={16} />,
    outline: <FileText size={16} />,
    snippet: <FolderOpen size={16} />,
    draft: <BookOpen size={16} />,
  }), []);

  // Use file search hook
  const { results: fileSearchResults, isSearching: isFileSearching, clearSearch: clearFileSearch } = useFileSearch({
    tree,
    query: fileSearchQuery,
  });

  // Clear search when project changes
  useEffect(() => {
    clearFileSearch();
    closeSearch();
  }, [currentProjectId, clearFileSearch, closeSearch]);

  // Focus search input when opened via keyboard shortcut
  useEffect(() => {
    if (isFileSearchOpen) {
      // Focus the search input by finding it in the DOM
      const searchInput = document.querySelector('input[placeholder*="搜索"], input[placeholder*="search"]') as HTMLInputElement;
      if (searchInput) {
        searchInput.focus();
      }
    }
  }, [isFileSearchOpen]);

  // Handle search result selection
  const handleSearchResultSelect = useCallback((result: { id: string; fileType: string; title: string }) => {
    handleSelect(result.id, result.fileType, result.title);
    closeSearch();
    clearFileSearch();
  }, [clearFileSearch, handleSelect, closeSearch]);

  // Keyboard navigation for search results
  useEffect(() => {
    if (!isFileSearchOpen || fileSearchResults.length === 0) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.isComposing || e.keyCode === 229) return;
      switch (e.key) {
        case 'ArrowDown':
          e.preventDefault();
          setSelectedResultIndex((prev) =>
            prev < fileSearchResults.length - 1 ? prev + 1 : 0
          );
          break;
        case 'ArrowUp':
          e.preventDefault();
          setSelectedResultIndex((prev) =>
            prev > 0 ? prev - 1 : fileSearchResults.length - 1
          );
          break;
        case 'Enter':
          e.preventDefault();
          if (fileSearchResults[selectedResultIndex]) {
            handleSearchResultSelect(fileSearchResults[selectedResultIndex]);
          }
          break;
        case 'Escape':
          e.preventDefault();
          closeSearch();
          clearFileSearch();
          break;
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isFileSearchOpen, fileSearchResults, selectedResultIndex, setSelectedResultIndex, handleSearchResultSelect, clearFileSearch, closeSearch]);

  // Check if a folder is the draft folder
  const isDraftFolder = (node: FileTreeNode) => {
    return node.file_type === "folder" && DRAFT_FOLDER_NAMES.includes(node.title);
  };

  // Trigger draft file input click
  const triggerDraftUpload = (e: React.MouseEvent) => {
    e.stopPropagation();
    draftFileInputRef.current?.click();
  };

  // Handle draft file upload
  const handleDraftUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files || files.length === 0 || !currentProjectId) return;
    const projectId = currentProjectId;
    const picked = Array.from(files);
    // Clear right away so picking the same file again still fires a change.
    e.target.value = "";
    const { files: created, failed } = await importDrafts(projectId, picked, "file_tree");
    // A batch that failed outright left nothing new to show; a partial one did. The shared
    // refresh reloads this tree and tells the rest of the workbench (the first chat's starter
    // cards follow what the work now holds), as the editor's own import does.
    if ((!failed || created.length > 0) && mountedRef.current && projectId === activeProjectIdRef.current) {
      triggerFileTreeRefresh();
    }
  };

  // Get file type display name
  const getFileTypeName = (fileType: string): string => {
    return t(`common:fileTypes.${fileType}`, { defaultValue: fileType });
  };

  // Render a tree node
  const renderNode = (node: FileTreeNode, depth: number = 0) => {
    const isFolder = node.file_type === "folder";
    const isExpanded = expandedFolders.has(node.id);
    const isSelected =
      selectedItem?.id === node.id;

    const createFileType = getCreateFileType(node.title);
    const activateNode = () => {
      if (isFolder) {
        toggleFolder(node.id);
      } else {
        handleSelect(node.id, node.file_type, node.title);
      }
    };

    return (
      <div key={node.id} style={{ marginLeft: depth > 0 ? 12 : 0 }}>
        {/* Node header */}
        <div
          data-testid="file-tree-node"
          data-file-id={node.id}
          data-file-type={node.file_type}
          data-file-title={node.title}
          role="treeitem"
          tabIndex={0}
          aria-level={depth + 1}
          aria-expanded={isFolder ? isExpanded : undefined}
          aria-selected={!isFolder ? isSelected : undefined}
          className={`flex items-center gap-2 px-2 py-1.5 rounded-lg cursor-pointer group transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[hsl(var(--accent-primary))] ${
            isSelected && !isFolder
              ? "bg-[hsl(var(--accent-primary)/0.12)] text-[hsl(var(--accent-primary))] font-medium"
              : "text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))]"
          }`}
          onClick={activateNode}
          onKeyDown={(e) => {
            // Keys pressed on the row's own action buttons stay with those buttons.
            if (e.target !== e.currentTarget) return;
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              activateNode();
            } else if (isFolder && ((e.key === "ArrowRight" && !isExpanded) || (e.key === "ArrowLeft" && isExpanded))) {
              e.preventDefault();
              toggleFolder(node.id);
            } else if (moveTreeFocus(e.currentTarget, e.key)) {
              e.preventDefault();
            }
          }}
        >
          {/* Expand/collapse icon for folders */}
          {isFolder ? (
            isExpanded ? (
              <ChevronDown
                size={14}
                className="text-[hsl(var(--text-secondary))] shrink-0"
              />
            ) : (
              <ChevronRight
                size={14}
                className="text-[hsl(var(--text-secondary))] shrink-0"
              />
            )
          ) : (
            <span className="w-3.5" /> /* Spacer for non-folders */
          )}

          {/* Icon */}
          <span className={isSelected && !isFolder ? "text-[hsl(var(--accent-primary))]" : "text-[hsl(var(--text-secondary))]"}>
            {FILE_TYPE_ICONS[node.file_type] || <FileText size={16} />}
          </span>

          {/* Title */}
          <span className="flex-1 truncate">{node.title}</span>

          {/* Children count for folders */}
          {isFolder && (
            <span className="text-[hsl(var(--text-secondary))] text-xs">
              {node.children?.length || 0}
            </span>
          )}

          {/* Action buttons */}
          {/* Phones have no hover: keep draft/material folder actions visible there. */}
          <div className={`${isMobile && (isDraftFolder(node) || isMaterialFolder(node)) ? "opacity-100" : "opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 [@media(hover:none)]:opacity-100"} flex items-center gap-0.5 transition-opacity`}>
            {/* Inline create button for every folder */}
            {isFolder && (
              <button
                type="button"
                data-testid="file-tree-create-child"
                onClick={(e) => {
                  e.stopPropagation();
                  startCreate(node.id, createFileType);
                }}
                className="p-2 md:p-1 min-h-[44px] min-w-[44px] md:min-h-0 md:min-w-0 flex items-center justify-center text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--accent-primary))]"
                title={`${t('common:create')} ${getFileTypeName(createFileType)}`}
                aria-label={`${t('common:create')} ${getFileTypeName(createFileType)}`}
              >
                <Plus size={14} />
              </button>
            )}

            {/* Explicit upload button for draft folder (.txt / .md chapter import) */}
            {isDraftFolder(node) && (
              <button
                type="button"
                data-testid="file-tree-upload-draft"
                onClick={triggerDraftUpload}
                disabled={isImportingDrafts}
                className={`p-2 md:p-1 min-h-[44px] min-w-[44px] md:min-h-0 md:min-w-0 flex items-center justify-center text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--accent-primary))] ${isImportingDrafts ? 'opacity-50 cursor-not-allowed' : ''}`}
                title={t('editor:fileTree.uploadDraft')}
                aria-label={t('editor:fileTree.uploadDraft')}
              >
                <Upload size={14} />
              </button>
            )}

            {/* Explicit upload button for material folder (.txt / .md reference upload) */}
            {isMaterialFolder(node) && (
              <button
                type="button"
                data-testid="file-tree-upload-material"
                onClick={triggerUpload}
                disabled={isUploading}
                className={`p-2 md:p-1 min-h-[44px] min-w-[44px] md:min-h-0 md:min-w-0 flex items-center justify-center text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--accent-primary))] ${isUploading ? 'opacity-50 cursor-not-allowed' : ''}`}
                title={t('editor:fileTree.uploadMaterial')}
                aria-label={t('editor:fileTree.uploadMaterial')}
              >
                <Upload size={14} />
              </button>
            )}

            {/* Add to chat button for snippets (materials) */}
            {node.file_type === "snippet" && (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  if (isMaterialAttached(node.id)) {
                    removeMaterial(node.id);
                  } else {
                    const success = addMaterial(node.id, node.title);
                    if (!success && isAtLimit) {
                      toast.info(t('editor:fileTree.maxMaterials', { max: MAX_ATTACHED_MATERIALS }));
                    }
                  }
                }}
                className={`p-2 md:p-1 min-h-[44px] min-w-[44px] md:min-h-0 md:min-w-0 flex items-center justify-center ${
                  isMaterialAttached(node.id)
                    ? "text-[hsl(var(--accent-primary))]"
                    : "text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--accent-primary))]"
                }`}
                title={isMaterialAttached(node.id) ? t('editor:fileTree.removeFromChat') : t('editor:fileTree.addToChat')}
                aria-label={isMaterialAttached(node.id) ? t('editor:fileTree.removeFromChat') : t('editor:fileTree.addToChat')}
              >
                {isMaterialAttached(node.id) ? <Check size={14} /> : <MessageSquarePlus size={14} />}
              </button>
            )}

            {/* Delete button (not for root folders) */}
            {!isFolder && (
              <button
                onClick={(e) => handleDelete(e, node.id, node.title)}
                className="p-2 md:p-1 min-h-[44px] min-w-[44px] md:min-h-0 md:min-w-0 flex items-center justify-center text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--error-text))]"
                title={t('common:delete')}
                aria-label={t('common:delete')}
              >
                <Trash2 size={12} />
              </button>
            )}
          </div>
        </div>

        {/* Children */}
        {isFolder && isExpanded && (
          <div role="group" className="mt-0.5">
            {/* New item input */}
            {isCreating === node.id && (
              <div
                className="flex items-center gap-2 px-2 py-1"
                style={{ marginLeft: 12 }}
              >
                <input
                  data-testid="file-tree-new-item-input"
                  type="text"
                  value={newItemName}
                  onChange={(e) => setNewItemName(e.target.value)}
                  onBlur={cancelCreate}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      handleCreate(node.id);
                    } else if (e.key === "Escape") {
                      cancelCreate();
                    }
                  }}
                  placeholder={getPlaceholder(newItemType)}
                  autoFocus
                  className="flex-1 bg-[hsl(var(--bg-tertiary))] border border-[hsl(var(--accent-primary))] rounded px-2 py-1 text-sm text-[hsl(var(--text-primary))] placeholder-[hsl(var(--text-secondary))] focus:outline-none"
                />
              </div>
            )}

            {/* Child nodes */}
            {node.children?.map((child) => renderNode(child, depth + 1))}

            {/* Empty state */}
            {treeHasFiles &&
              (!node.children || node.children.length === 0) &&
              isCreating !== node.id && (
                <div
                  className="px-2 py-2 text-[hsl(var(--text-secondary))] text-xs italic"
                  style={{ marginLeft: 12 }}
                >
                  {isMaterialFolder(node)
                    ? t('editor:fileTree.emptyMaterialFolder')
                    : t('editor:fileTree.emptyFolder')}
                </div>
              )}
          </div>
        )}
      </div>
    );
  };

  const loadErrorNotice = loadError ? (
    <div role="alert" className="p-3 text-sm text-[hsl(var(--text-secondary))]">
      <p>{t('editor:fileTree.loadFailed')}</p>
      <Button type="button" variant="secondary" size="touch" className="mt-2" disabled={loading} aria-busy={loading} onClick={() => void loadData(true)}>
        {t(loading ? 'common:loading' : 'common:retry')}
      </Button>
    </div>
  ) : null;

  if (loading && isInitialLoad && tree.length === 0) {
    return (
      <div className="w-full h-full flex items-center justify-center text-[hsl(var(--text-secondary))]">
        {t('common:loading')}
      </div>
    );
  }

  if (tree.length === 0) {
    return (
      <div className="w-full h-full flex flex-col items-center justify-center text-[hsl(var(--text-secondary))] p-4">
        {loadErrorNotice || <>
          <Folder size={48} className="mb-4 opacity-30" />
          <p className="text-sm">{t('editor:fileTree.folderEmpty')}</p>
          <p className="text-xs mt-1">{t('editor:fileTree.createWithAI')}</p>
        </>}
      </div>
    );
  }

  return (
    <div className="w-full h-full text-sm overflow-auto p-2">
      {/* Hidden file input for upload */}
      <input
        type="file"
        ref={fileInputRef}
        onChange={handleFileUpload}
        accept=".txt,.md"
        className="hidden"
      />

      {/* Hidden file input for draft upload */}
      <input
        type="file"
        ref={draftFileInputRef}
        onChange={handleDraftUpload}
        accept={DRAFT_IMPORT_ACCEPT}
        multiple
        className="hidden"
      />

      {/* File Search Section */}
      <div className="mb-2 px-2 relative">
        <div className="flex items-center gap-2">
          <FileSearchInput
            value={fileSearchQuery}
            onChange={(value) => {
              setFileSearchQuery(value);
              openSearch();
              setSelectedResultIndex(0);
            }}
            onClear={() => {
              setFileSearchQuery('');
              closeSearch();
              clearFileSearch();
            }}
            onFocus={openSearch}
            placeholder={t('editor:fileTree.searchPlaceholder')}
            className="flex-1"
          />
        </div>

        {/* Search Results Dropdown */}
        {isFileSearchOpen && fileSearchQuery && (
          <SearchResultsDropdown
            results={fileSearchResults}
            selectedIndex={selectedResultIndex}
            onSelect={handleSearchResultSelect}
            onHover={setSelectedResultIndex}
            visible={isFileSearchOpen && fileSearchQuery.length > 0}
            loading={isFileSearching}
            onClose={closeSearch}
          />
        )}
      </div>

      {/* File Tree */}
      {loadErrorNotice}
      <div role="tree" aria-label={t('editor:sidebar.files')}>
        {tree.map((node) => renderNode(node, 0))}
      </div>

      <ConfirmDialog
        open={pendingDelete !== null && pendingDelete.projectId === currentProjectId}
        onClose={() => {
          if (!deletingFile) setPendingDelete(null);
        }}
        onConfirm={handleConfirmDelete}
        title={t('common:confirmDelete')}
        message={pendingDelete?.title ?? ''}
        variant="danger"
        loading={deletingFile}
        confirmLabel={t('common:delete')}
        cancelLabel={t('common:cancel')}
      />
    </div>
  );
};
