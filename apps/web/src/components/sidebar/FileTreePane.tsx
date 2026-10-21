import { isFileTitleTooLong } from "../../lib/fileTitle";
import React, { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { useProject } from "../../contexts/ProjectContext";
import { useMobileLayout } from "../../contexts/MobileLayoutContext";
import { useMaterialAttachment, MAX_ATTACHED_MATERIALS } from "../../contexts/MaterialAttachmentContext";
import { fileApi } from "../../lib/api";
import { toast } from "../../lib/toast";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { Button } from "../ui/Button";
import { findAncestorFolderIds } from "../../lib/lastOpenedFile";
import { notifyFileRemoved } from "../../lib/fileRemovalEvents";
import { FOLDER_TYPE_MAP } from "../../lib/folderTypeMap";
import type { FileTreeNode, TreeNodeType } from "../../types";
import { useFileSearch } from "../../hooks/useFileSearch";
import { DRAFT_IMPORT_ACCEPT, useDraftImport } from "../../hooks/useDraftImport";
import { useFileSearchContext } from "../../contexts/FileSearchContext";
import { FileSearchInput } from "../FileSearchInput";
import SearchResultsDropdown from "../SearchResultsDropdown";
import { logger } from "../../lib/logger";
import {
  ChevronDown,
  ChevronRight,
  FileText,
  Users,
  BookOpen,
  Sparkles,
  FolderOpen,
  Plus,
  Trash2,
  Folder,
  MessageSquarePlus,
  Check,
} from "lucide-react";

// Material folder names (both zh and en)
const MATERIAL_FOLDER_NAMES = ["素材", "Materials", "Material"];

// Draft folder names (both zh and en)
const DRAFT_FOLDER_NAMES = ["正文", "Drafts", "Draft"];

// FileTreePane component - file tree pane for sidebar
export const FileTreePane: React.FC = () => {
  const { t } = useTranslation(['editor', 'common']);
  const { currentProjectId, selectedItem, setSelectedItem, fileTreeVersion } =
    useProject();
  const { switchToEditor, isMobile } = useMobileLayout();
  const { addMaterial, removeMaterial, isMaterialAttached, isAtLimit } = useMaterialAttachment();

  // Data state
  const [tree, setTree] = useState<FileTreeNode[]>([]);
  // A brand-new work has no file anywhere: one "empty" hint per folder would only repeat itself
  // beside the editor's own start. The hints return once the work has any file.
  const treeHasFiles = useMemo(() => {
    const hasFile = (nodes: FileTreeNode[]): boolean =>
      nodes.some((node) => node.file_type !== "folder" || hasFile(node.children ?? []));
    return hasFile(tree);
  }, [tree]);
  const [loading, setLoading] = useState(false);
  const [isInitialLoad, setIsInitialLoad] = useState(true);
  const [loadError, setLoadError] = useState(false);

  // UI states
  const [expandedFolders, setExpandedFolders] = useState<Set<string>>(
    new Set(),
  );
  const [isCreating, setIsCreating] = useState<string | null>(null);
  const [newItemName, setNewItemName] = useState("");
  const [newItemType, setNewItemType] = useState<string>("draft");
  const [pendingDelete, setPendingDelete] = useState<{ id: string; title: string; projectId: string | null } | null>(null);
  const [deletingFile, setDeletingFile] = useState(false);

  // Upload state
  const [isUploading, setIsUploading] = useState(false);
  const { importFiles: importDrafts, isImporting: isImportingDrafts } = useDraftImport();
  const mountedRef = useRef(true);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const draftFileInputRef = useRef<HTMLInputElement>(null);

  const { isSearchOpen: isFileSearchOpen, openSearch, closeSearch } = useFileSearchContext();

  const [fileSearchQuery, setFileSearchQuery] = useState('');
  const [selectedResultIndex, setSelectedResultIndex] = useState(0);
  const loadRequestIdRef = useRef(0);
  const loadAbortControllerRef = useRef<AbortController | null>(null);
  const activeProjectIdRef = useRef(currentProjectId);
  const selectedItemRef = useRef(selectedItem);
  const treeRef = useRef(tree);
  selectedItemRef.current = selectedItem;
  treeRef.current = tree;

  const isAbortError = useCallback((error: unknown): boolean => {
    return (
      (error instanceof DOMException && error.name === "AbortError")
      || (typeof error === "object"
        && error !== null
        && "name" in error
        && (error as { name?: unknown }).name === "AbortError")
    );
  }, []);

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

  // Load file tree data
  // @param showLoading - whether to show loading state (only for initial load)
  const loadData = useCallback(async (showLoading = false) => {
    // A mutation started in a previous project may finish after navigation.
    if (!currentProjectId || currentProjectId !== activeProjectIdRef.current) return;
    const selectedAtRequest = selectedItemRef.current;
    const previousTree = treeRef.current;
    const requestId = ++loadRequestIdRef.current;
    loadAbortControllerRef.current?.abort();
    const abortController = new AbortController();
    loadAbortControllerRef.current = abortController;

    setLoading(showLoading);
    try {
      // Try to load from new File API first
      const response = await fileApi.getTree(currentProjectId, {
        signal: abortController.signal,
      });
      if (abortController.signal.aborted || requestId !== loadRequestIdRef.current) {
        return;
      }
      setTree(response.tree || []);
      setLoadError(false);
      if (selectedAtRequest && selectedItemRef.current?.id === selectedAtRequest.id
        && findAncestorFolderIds(previousTree, selectedAtRequest.id) !== null
        && findAncestorFolderIds(response.tree || [], selectedAtRequest.id) === null) {
        notifyFileRemoved(currentProjectId, selectedAtRequest.id);
        if (selectedItemRef.current?.id === selectedAtRequest.id) setSelectedItem(null);
      }

      // Auto-expand root folders
      const rootFolderIds = (response.tree || [])
        .filter((node) => node.file_type === "folder")
        .map((node) => node.id);
      setExpandedFolders((prev) => new Set([...prev, ...rootFolderIds]));
    } catch (error: unknown) {
      if (abortController.signal.aborted || isAbortError(error) || requestId !== loadRequestIdRef.current) {
        return;
      }
      logger.error("Failed to load file tree:", error);
      setLoadError(true);
    } finally {
      if (requestId === loadRequestIdRef.current) {
        setLoading(false);
        setIsInitialLoad(false);
      }
    }
  }, [currentProjectId, isAbortError, setSelectedItem]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      loadAbortControllerRef.current?.abort();
      mountedRef.current = false;
    };
  }, []);

  // Expand the folders around a selected file once (e.g. the file restored on
  // reopening the project), without re-opening folders the author collapses later.
  const selectedItemId = selectedItem?.id ?? null;
  const revealedSelectionRef = useRef<string | null>(null);
  useEffect(() => {
    if (!selectedItemId || revealedSelectionRef.current === selectedItemId) return;
    const ancestors = findAncestorFolderIds(tree, selectedItemId);
    if (!ancestors) return;
    revealedSelectionRef.current = selectedItemId;
    if (ancestors.length === 0) return;
    setExpandedFolders((prev) =>
      ancestors.every((id) => prev.has(id)) ? prev : new Set([...prev, ...ancestors]),
    );
  }, [selectedItemId, tree]);

  // Reload when fileTreeVersion changes (triggered by AI tool calls)
  // This is a silent refresh - no loading indicator
  useEffect(() => {
    if (fileTreeVersion > 0) {
      loadData(false); // silent refresh
    }
  }, [fileTreeVersion, loadData]);

  useEffect(() => {
    // Files from the previous project must not remain selectable while loading.
    activeProjectIdRef.current = currentProjectId;
    setTree([]);
    setLoadError(false);
    if (currentProjectId) {
      // Show loading only on initial load or when switching projects
      setIsInitialLoad(true);
      loadData(true);
    } else {
      setLoading(false);
      setIsInitialLoad(false);
    }
    return () => {
      // Fence pending mutation completions as well as existing requests when
      // the project changes or this tree unmounts.
      activeProjectIdRef.current = null;
      loadAbortControllerRef.current?.abort();
    };
  }, [currentProjectId, loadData]);

  // Clear search when project changes
  useEffect(() => {
    clearFileSearch();
    closeSearch();
    setSelectedResultIndex(0);
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

  // Toggle folder expansion
  const toggleFolder = (folderId: string) => {
    setExpandedFolders((prev) => {
      const next = new Set(prev);
      if (next.has(folderId)) {
        next.delete(folderId);
      } else {
        next.add(folderId);
      }
      return next;
    });
  };

  // Handle item selection
  const handleSelect = useCallback((
    id: string,
    fileType: string,
    title: string,
  ) => {
    // Map file_type to TreeNodeType
    const typeMap: Record<string, TreeNodeType> = {
      outline: "outline",
      draft: "draft",
      character: "character",
      lore: "lore",
      snippet: "material",
      folder: "folder",
    };
    setSelectedItem({
      id,
      type: typeMap[fileType] || "draft",
      title,
    });

    // On mobile, automatically switch to editor panel after selecting a file
    if (isMobile) {
      switchToEditor();
    }
  }, [setSelectedItem, isMobile, switchToEditor]);

  // Handle search result selection
  const handleSearchResultSelect = useCallback((result: { id: string; fileType: string; title: string }) => {
    handleSelect(result.id, result.fileType, result.title);
    closeSearch();
    clearFileSearch();

    // On mobile, switch to editor panel
    if (isMobile) {
      switchToEditor();
    }
  }, [isMobile, switchToEditor, clearFileSearch, handleSelect, closeSearch]);

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
  }, [isFileSearchOpen, fileSearchResults, selectedResultIndex, handleSearchResultSelect, clearFileSearch, closeSearch]);

  // Start creating new item
  const startCreate = (parentId: string, fileType: string) => {
    setIsCreating(parentId);
    setNewItemType(fileType);
    setNewItemName("");
    // Make sure parent folder is expanded
    setExpandedFolders((prev) => new Set([...prev, parentId]));
  };

  // Cancel creating
  const cancelCreate = () => {
    setIsCreating(null);
    setNewItemName("");
  };

  // Get the appropriate file type for creating inside a folder
  const getCreateFileType = (folderTitle: string): string => {
    return FOLDER_TYPE_MAP[folderTitle] || "draft";
  };

  // Create new item
  const handleCreate = async (parentId: string) => {
    if (!currentProjectId || !newItemName.trim()) {
      cancelCreate();
      return;
    }

    if (isFileTitleTooLong(newItemName.trim())) {
      toast.error(t('editor:fileTree.titleTooLong'));
      return;
    }

    try {
      await fileApi.create(currentProjectId, {
        title: newItemName.trim(),
        file_type: newItemType,
        parent_id: parentId,
        content: "",
      });

      if (currentProjectId !== activeProjectIdRef.current) return;
      await loadData(false); // silent refresh - no loading indicator
      cancelCreate();
    } catch (error) {
      logger.error("Failed to create item:", error);
      toast.error(t('editor:fileTree.createFailed'));
    }
  };

  // Delete item: ask in an in-app dialog first.
  const handleDelete = (e: React.MouseEvent, fileId: string, fileTitle: string) => {
    e.stopPropagation();
    setPendingDelete({ id: fileId, title: fileTitle, projectId: currentProjectId });
  };

  const handleConfirmDelete = async () => {
    if (!currentProjectId || !pendingDelete || pendingDelete.projectId !== currentProjectId || deletingFile) return;
    const fileId = pendingDelete.id;
    setDeletingFile(true);

    try {
      await fileApi.delete(fileId);

      if (currentProjectId !== activeProjectIdRef.current) return;
      notifyFileRemoved(currentProjectId, fileId);
      // A folder's selected descendant is corrected by the successful tree refresh.
      if (selectedItemRef.current?.id === fileId) {
        setSelectedItem(null);
      }

      await loadData(false); // silent refresh - no loading indicator
    } catch (error) {
      logger.error("Failed to delete item:", error);
      toast.error(t('editor:fileTree.deleteFailed'));
    } finally {
      if (mountedRef.current) {
        setDeletingFile(false);
        setPendingDelete(null);
      }
    }
  };

  // Get placeholder text for new item
  const getPlaceholder = (fileType: string): string => {
    const placeholderMap: Record<string, string> = {
      outline: t('editor:fileTree.newOutline'),
      draft: t('editor:fileTree.newDraft'),
      character: t('editor:fileTree.newCharacter'),
      lore: t('editor:fileTree.newLore'),
      snippet: t('editor:fileTree.newSnippet'),
      script: t('editor:fileTree.newScript'),
    };
    return placeholderMap[fileType] || t('editor:fileTree.newFileName');
  };

  // Check if a folder is the material folder
  const isMaterialFolder = (node: FileTreeNode) => {
    return node.file_type === "folder" && MATERIAL_FOLDER_NAMES.includes(node.title);
  };

  // Check if a folder is the draft folder
  const isDraftFolder = (node: FileTreeNode) => {
    return node.file_type === "folder" && DRAFT_FOLDER_NAMES.includes(node.title);
  };

  // Trigger file input click
  const triggerUpload = (e: React.MouseEvent) => {
    e.stopPropagation();
    fileInputRef.current?.click();
  };

  // Trigger draft file input click
  const triggerDraftUpload = (e: React.MouseEvent) => {
    e.stopPropagation();
    draftFileInputRef.current?.click();
  };

  // Handle file upload
  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !currentProjectId) return;

    setIsUploading(true);

    try {
      await fileApi.upload(currentProjectId, file);
      toast.success(t('editor:fileTree.uploadSuccess', { name: file.name }));
      await loadData(false);
    } catch (error) {
      logger.error("Failed to upload file:", error);
      toast.error(error instanceof Error ? error.message : t('editor:fileTree.uploadFailed'));
    } finally {
      setIsUploading(false);
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    }
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
    // A batch that failed outright left nothing new to show; a partial one did.
    if ((!failed || created.length > 0) && mountedRef.current && projectId === activeProjectIdRef.current) {
      await loadData(false);
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
          className={`flex items-center gap-2 px-2 py-1.5 rounded cursor-pointer group transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[hsl(var(--accent-primary))] ${
            isSelected && !isFolder
              ? "bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-primary))]"
              : "text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))]"
          }`}
          onClick={activateNode}
          onKeyDown={(e) => {
            // Keys pressed on the row's own action buttons stay with those buttons.
            if (e.target !== e.currentTarget) return;
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              activateNode();
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
          <span className="text-[hsl(var(--text-secondary))]">
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
          {/* Phones have no hover: the draft folder keeps its import button visible there. */}
          <div className={`${isMobile && isDraftFolder(node) ? "opacity-100" : "opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 [@media(hover:none)]:opacity-100"} flex items-center gap-0.5 transition-opacity`}>
            {/* For draft folder: + button triggers draft upload */}
            {isDraftFolder(node) ? (
              <button
                data-testid="file-tree-upload-draft"
                onClick={triggerDraftUpload}
                disabled={isImportingDrafts}
                className={`p-2 md:p-1 min-h-[44px] min-w-[44px] md:min-h-0 md:min-w-0 flex items-center justify-center text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--accent-primary))] ${isImportingDrafts ? 'opacity-50 cursor-not-allowed' : ''}`}
                title={t('editor:fileTree.uploadDraft')}
                aria-label={t('editor:fileTree.uploadDraft')}
              >
                <Plus size={14} />
              </button>
            ) : isMaterialFolder(node) ? (
              /* For material folder: + button triggers upload */
              <button
                data-testid="file-tree-upload-material"
                onClick={triggerUpload}
                disabled={isUploading}
                className={`p-2 md:p-1 min-h-[44px] min-w-[44px] md:min-h-0 md:min-w-0 flex items-center justify-center text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--accent-primary))] ${isUploading ? 'opacity-50 cursor-not-allowed' : ''}`}
                title={t('editor:fileTree.uploadMaterial')}
                aria-label={t('editor:fileTree.uploadMaterial')}
              >
                <Plus size={14} />
              </button>
            ) : (
              /* Add button for other folders */
              isFolder && (
                <button
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
              )
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
      <Button type="button" variant="secondary" size={isMobile ? "touch" : "sm"} className="mt-2" disabled={loading} aria-busy={loading} onClick={() => void loadData(true)}>
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
