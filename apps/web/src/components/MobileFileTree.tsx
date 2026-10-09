/**
 * @fileoverview MobileFileTree component - Touch-optimized file tree navigation for mobile devices.
 *
 * This component provides a mobile-first file tree interface with:
 * - Touch-optimized 44px minimum touch targets (iOS/Android guidelines)
 * - Prominent file search with fuzzy matching and keyboard navigation
 * - Expandable folder hierarchy with smooth animations
 * - Inline file creation with contextual file type detection
 * - File deletion with confirmation dialog
 * - Automatic panel switching to editor on file selection
 * - i18n support for Chinese and English folder names
 *
 * The component integrates with:
 * - ProjectContext for project/file state management
 * - MobileLayoutContext for panel navigation
 * - useFileSearch hook for debounced fuzzy search
 *
 * @module components/MobileFileTree
 */
import React, { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { useTranslation } from "react-i18next";
import { useProject } from "../contexts/ProjectContext";
import { useMobileLayout } from "../contexts/MobileLayoutContext";
import { useMaterialAttachment, MAX_ATTACHED_MATERIALS } from "../contexts/MaterialAttachmentContext";
import { fileApi } from "../lib/api";
import { FOLDER_TYPE_MAP, MATERIAL_FOLDER_NAMES } from "../lib/folderTypeMap";
import { toast } from "../lib/toast";
import { ConfirmDialog } from "./ui/ConfirmDialog";
import { findAncestorFolderIds } from "../lib/lastOpenedFile";
import { FileSearchInput } from "./FileSearchInput";
import { useFileSearch, type FileSearchResult } from "../hooks/useFileSearch";
import SearchResultsDropdown from "./SearchResultsDropdown";
import type { FileTreeNode, TreeNodeType } from "../types";
import { logger } from "../lib/logger";
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
  Search,
  MessageSquarePlus,
  Check,
} from "lucide-react";

/**
 * Props for the MobileFileTree component.
 *
 * @interface MobileFileTreeProps
 */
export interface MobileFileTreeProps {
  /** Optional CSS class name to apply to the root container */
  className?: string;
}

/**
 * Icon mapping for file types with larger 20px icons optimized for mobile touch targets.
 * Maps file_type strings to their corresponding Lucide React icons.
 *
 * @constant
 */
const FILE_TYPE_ICONS: Record<string, React.ReactNode> = {
  folder: <Folder size={20} />,
  lore: <Sparkles size={20} />,
  character: <Users size={20} />,
  outline: <FileText size={20} />,
  snippet: <FolderOpen size={20} />,
  draft: <BookOpen size={20} />,
};

/**
 * Internal implementation of the MobileFileTree component.
 *
 * Renders a touch-optimized file tree with:
 * - **Search bar**: Prominent search input with fuzzy matching and dropdown results
 * - **Tree view**: Hierarchical folder/file structure with expand/collapse
 * - **Actions**: Create new files, delete files with inline UI
 * - **Navigation**: Auto-switches to editor panel when a file is selected
 *
 * State Management:
 * - `tree`: Current file tree data from API
 * - `loading`/`isInitialLoad`: Loading states for skeleton/empty UI
 * - `expandedFolders`: Set of expanded folder IDs
 * - `isCreating`/`newItemName`/`newItemType`: File creation state
 * - `searchQuery`/`isSearchFocused`/`selectedSearchIndex`: Search UI state
 *
 * @param props - Component props
 * @param props.className - Optional CSS class for the root container
 *
 * @example
 * ```tsx
 * // Basic usage
 * <MobileFileTree />
 *
 * // With custom styling
 * <MobileFileTree className="border-r border-[hsl(var(--border-color))]" />
 * ```
 */
const MobileFileTreeComponent: React.FC<MobileFileTreeProps> = ({ className }) => {
  const { t } = useTranslation(['editor', 'common']);
  const { currentProjectId, selectedItem, setSelectedItem, fileTreeVersion } = useProject();
  const { switchToEditor } = useMobileLayout();
  const { addMaterial, removeMaterial, isMaterialAttached, isAtLimit } = useMaterialAttachment();

  // Data state
  const [tree, setTree] = useState<FileTreeNode[]>([]);
  const [loading, setLoading] = useState(false);
  const [isInitialLoad, setIsInitialLoad] = useState(true);

  // UI states
  const [expandedFolders, setExpandedFolders] = useState<Set<string>>(new Set());
  const [isCreating, setIsCreating] = useState<string | null>(null);
  const [newItemName, setNewItemName] = useState("");
  const [newItemType, setNewItemType] = useState<string>("draft");
  const [pendingDelete, setPendingDelete] = useState<{ id: string; title: string; projectId: string | null } | null>(null);
  const [deletingFile, setDeletingFile] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Search state - prominent mobile file search
  const [searchQuery, setSearchQuery] = useState("");
  const [isSearchFocused, setIsSearchFocused] = useState(false);
  const [selectedSearchIndex, setSelectedSearchIndex] = useState(0);
  const loadRequestIdRef = useRef(0);
  const loadAbortControllerRef = useRef<AbortController | null>(null);
  const activeProjectIdRef = useRef(currentProjectId);

  const isAbortError = useCallback((error: unknown): boolean => {
    return (
      (error instanceof DOMException && error.name === "AbortError")
      || (typeof error === "object"
        && error !== null
        && "name" in error
        && (error as { name?: unknown }).name === "AbortError")
    );
  }, []);
  const searchContainerRef = useRef<HTMLDivElement>(null);

  // Use the file search hook for debounced search with fuzzy matching
  const { results: searchResults, isSearching, clearSearch } = useFileSearch({
    tree,
    query: searchQuery,
    debounceMs: 300,
    maxResults: 20,
  });

  // Show dropdown when search is focused and has query
  const showSearchDropdown = isSearchFocused && searchQuery.trim().length > 0;

  /**
   * Loads file tree data from the API and auto-expands root folders.
   *
   * @param showLoading - Whether to show the loading spinner (true for initial load)
   */
  const loadData = useCallback(async (showLoading = false) => {
    // A mutation started in a previous project may finish after navigation.
    if (!currentProjectId || currentProjectId !== activeProjectIdRef.current) return;
    const requestId = ++loadRequestIdRef.current;
    loadAbortControllerRef.current?.abort();
    const abortController = new AbortController();
    loadAbortControllerRef.current = abortController;

    if (showLoading) {
      setLoading(true);
    }
    try {
      const response = await fileApi.getTree(currentProjectId, {
        signal: abortController.signal,
      });
      if (abortController.signal.aborted || requestId !== loadRequestIdRef.current) {
        return;
      }
      setTree(response.tree || []);

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
      setTree([]);
    } finally {
      if (requestId === loadRequestIdRef.current) {
        if (showLoading) {
          setLoading(false);
        }
        setIsInitialLoad(false);
      }
    }
  }, [currentProjectId, isAbortError]);

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

  useEffect(() => {
    return () => {
      loadAbortControllerRef.current?.abort();
    };
  }, []);

  // Reload when fileTreeVersion changes
  useEffect(() => {
    if (fileTreeVersion > 0) {
      loadData(false);
    }
  }, [fileTreeVersion, loadData]);

  // Initial load
  useEffect(() => {
    // Files from the previous project must not remain selectable while loading.
    activeProjectIdRef.current = currentProjectId;
    setTree([]);
    if (currentProjectId) {
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

  /**
   * Toggles the expansion state of a folder.
   *
   * @param folderId - The ID of the folder to toggle
   */
  const toggleFolder = useCallback((folderId: string) => {
    setExpandedFolders((prev) => {
      const next = new Set(prev);
      if (next.has(folderId)) {
        next.delete(folderId);
      } else {
        next.add(folderId);
      }
      return next;
    });
  }, []);

  /**
   * Handles selection of a file or folder item.
   * Updates the selected item in context and switches to editor panel for files.
   *
   * @param id - The file/folder ID
   * @param fileType - The type of the item (outline, draft, character, lore, snippet, folder)
   * @param title - The display title of the item
   */
  const handleSelect = useCallback((id: string, fileType: string, title: string) => {
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
    if (fileType !== "folder") {
      switchToEditor();
    }
  }, [setSelectedItem, switchToEditor]);

  /**
   * Initiates the inline file creation UI for a folder.
   *
   * @param parentId - The parent folder ID where the new file will be created
   * @param fileType - The type of file to create
   */
  const startCreate = useCallback((parentId: string, fileType: string) => {
    setIsCreating(parentId);
    setNewItemType(fileType);
    setNewItemName("");
    // Make sure parent folder is expanded
    setExpandedFolders((prev) => new Set([...prev, parentId]));
  }, []);

  /**
   * Cancels the file creation flow and resets creation state.
   */
  const cancelCreate = useCallback(() => {
    setIsCreating(null);
    setNewItemName("");
  }, []);

  /**
   * Determines the appropriate file type to create based on folder title.
   * Uses FOLDER_TYPE_MAP for contextual file type detection.
   *
   * @param folderTitle - The title of the parent folder
   * @returns The file type to create, defaults to "draft"
   */
  const getCreateFileType = useCallback((folderTitle: string): string => {
    return FOLDER_TYPE_MAP[folderTitle] || "draft";
  }, []);

  const isMaterialFolder = useCallback((node: FileTreeNode): boolean => {
    return node.file_type === "folder" && MATERIAL_FOLDER_NAMES.some((name) => name === node.title);
  }, []);

  const triggerUpload = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    if (isUploading) return;
    fileInputRef.current?.click();
  }, [isUploading]);

  const handleFileUpload = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !currentProjectId) return;

    setUploadError(null);
    setIsUploading(true);

    try {
      await fileApi.upload(currentProjectId, file);
      await loadData(false);
      toast.success(t('editor:fileTree.uploadSuccess', { name: file.name }));
    } catch (error) {
      logger.error("Failed to upload file:", error);
      const message = error instanceof Error ? error.message : t('editor:fileTree.uploadFailed');
      setUploadError(message);
      setTimeout(() => setUploadError(null), 5000);
    } finally {
      setIsUploading(false);
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    }
  }, [currentProjectId, loadData, t]);

  /**
   * Creates a new file via the API and refreshes the tree.
   *
   * @param parentId - The parent folder ID for the new file
   */
  const handleCreate = useCallback(async (parentId: string) => {
    if (!currentProjectId || !newItemName.trim()) {
      cancelCreate();
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
      await loadData(false);
      cancelCreate();
    } catch (error) {
      logger.error("Failed to create item:", error);
      toast.error(t('editor:fileTree.createFailed'));
    }
  }, [currentProjectId, newItemName, newItemType, loadData, cancelCreate, t]);

  /**
   * Asks for confirmation (in-app dialog) before deleting a file.
   *
   * @param e - The click event (used to stop propagation)
   * @param fileId - The ID of the file to delete
   * @param fileTitle - Shown in the dialog so the author knows which file goes
   */
  const handleDelete = useCallback((e: React.MouseEvent, fileId: string, fileTitle: string) => {
    e.stopPropagation();
    setPendingDelete({ id: fileId, title: fileTitle, projectId: currentProjectId });
  }, [currentProjectId]);

  /**
   * Deletes the confirmed file.
   * Clears selection if the deleted file was selected.
   */
  const handleConfirmDelete = useCallback(async () => {
    if (!pendingDelete || pendingDelete.projectId !== currentProjectId || deletingFile) return;
    const fileId = pendingDelete.id;
    setDeletingFile(true);

    try {
      await fileApi.delete(fileId);

      if (currentProjectId !== activeProjectIdRef.current) return;
      // Clear selection if deleted item was selected
      if (selectedItem?.id === fileId) {
        setSelectedItem(null);
      }

      await loadData(false);
    } catch (error) {
      logger.error("Failed to delete item:", error);
      toast.error(t('editor:fileTree.deleteFailed'));
    } finally {
      setDeletingFile(false);
      setPendingDelete(null);
    }
  }, [pendingDelete, deletingFile, currentProjectId, selectedItem, setSelectedItem, loadData, t]);

  /**
   * Returns localized placeholder text for the file creation input.
   *
   * @param fileType - The type of file being created
   * @returns Translated placeholder text
   */
  const getPlaceholder = useCallback((fileType: string): string => {
    const placeholderMap: Record<string, string> = {
      outline: t('editor:fileTree.newOutline'),
      draft: t('editor:fileTree.newDraft'),
      character: t('editor:fileTree.newCharacter'),
      lore: t('editor:fileTree.newLore'),
      snippet: t('editor:fileTree.newSnippet'),
      script: t('editor:fileTree.newScript'),
    };
    return placeholderMap[fileType] || t('editor:fileTree.newFileName');
  }, [t]);

  /**
   * Handles selection of a search result from the dropdown.
   * Selects the file and clears the search UI.
   *
   * @param result - The selected search result
   */
  const handleSearchSelect = useCallback((result: FileSearchResult) => {
    handleSelect(result.id, result.fileType, result.title);
    setSearchQuery("");
    setIsSearchFocused(false);
    setSelectedSearchIndex(0);
  }, [handleSelect]);

  /**
   * Clears the search query and resets search state.
   */
  const handleClearSearch = useCallback(() => {
    setSearchQuery("");
    setSelectedSearchIndex(0);
    clearSearch();
  }, [clearSearch]);

  /**
   * Closes the search dropdown without clearing the query.
   */
  const handleCloseSearchDropdown = useCallback(() => {
    setIsSearchFocused(false);
  }, []);

  /**
   * Handles keyboard navigation within the search dropdown.
   * Supports ArrowUp/Down, Enter, and Escape keys.
   *
   * @param e - The keyboard event
   */
  const handleSearchKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (!showSearchDropdown || searchResults.length === 0) return;

    switch (e.key) {
      case "ArrowDown":
        e.preventDefault();
        setSelectedSearchIndex((prev) =>
          prev < searchResults.length - 1 ? prev + 1 : prev
        );
        break;
      case "ArrowUp":
        e.preventDefault();
        setSelectedSearchIndex((prev) => (prev > 0 ? prev - 1 : 0));
        break;
      case "Enter":
        e.preventDefault();
        if (searchResults[selectedSearchIndex]) {
          handleSearchSelect(searchResults[selectedSearchIndex]);
        }
        break;
      case "Escape":
        e.preventDefault();
        setIsSearchFocused(false);
        break;
    }
  }, [showSearchDropdown, searchResults, selectedSearchIndex, handleSearchSelect]);

  /**
   * Memoized filtered tree based on search query.
   * When dropdown is shown, returns full tree (dropdown overlays it).
   * Otherwise, filters nodes to only show matching items and their parents.
   */
  const filteredTree = useMemo(() => {
    // When dropdown is shown, don't filter the tree (show full tree behind dropdown)
    if (showSearchDropdown || !searchQuery.trim()) return tree;

    const filterNodes = (nodes: FileTreeNode[]): FileTreeNode[] => {
      return nodes.reduce<FileTreeNode[]>((acc, node) => {
        const matchesSearch = node.title.toLowerCase().includes(searchQuery.toLowerCase());

        if (node.file_type === "folder") {
          // For folders, include if they have matching children
          const filteredChildren = filterNodes(node.children || []);
          if (filteredChildren.length > 0 || matchesSearch) {
            acc.push({ ...node, children: filteredChildren });
          }
        } else if (matchesSearch) {
          acc.push(node);
        }

        return acc;
      }, []);
    };

    return filterNodes(tree);
  }, [tree, searchQuery, showSearchDropdown]);

  /**
   * Recursively renders a single tree node with mobile-optimized touch targets.
   *
   * Each node includes:
   * - Expand/collapse icon for folders
   * - File type icon
   * - Title with truncation
   * - Children count for folders
   * - Create button for folders
   * - Delete button for files
   * - Inline create input when active
   *
   * @param node - The tree node to render
   * @param depth - Current nesting depth for indentation (default: 0)
   * @returns React element for the node and its children
   */
  const renderNode = useCallback((node: FileTreeNode, depth: number = 0): React.ReactNode => {
    const isFolder = node.file_type === "folder";
    const isExpanded = expandedFolders.has(node.id);
    const isSelected = selectedItem?.id === node.id;
    const createFileType = getCreateFileType(node.title);
    const isMaterialFolderNode = isMaterialFolder(node);
    // Same wording as the desktop tree: 创建 角色卡, not the internal type id.
    const folderActionLabel = isMaterialFolderNode
      ? t('editor:fileTree.uploadMaterial')
      : `${t('common:create')} ${t(`common:fileTypes.${createFileType}`, { defaultValue: createFileType })}`;

    return (
      <div key={node.id} className="select-none">
        {/* Main item row - 44px min height for touch targets */}
        <div
          role="treeitem"
          aria-expanded={isFolder ? isExpanded : undefined}
          className={`
            flex items-center gap-3 px-4 cursor-pointer transition-colors
            min-h-[44px] /* Mobile touch target minimum */
            ${isSelected && !isFolder
              ? "bg-[hsl(var(--accent-primary)/0.15)] text-[hsl(var(--accent-primary))]"
              : "text-[hsl(var(--text-primary))] active:bg-[hsl(var(--bg-tertiary))]"
            }
          `}
          style={{ paddingLeft: `${12 + depth * 16}px` }}
          onClick={() => {
            if (isFolder) {
              toggleFolder(node.id);
            } else {
              handleSelect(node.id, node.file_type, node.title);
            }
          }}
        >
          {/* Expand/collapse icon for folders */}
          {isFolder ? (
            isExpanded ? (
              <ChevronDown size={20} className="text-[hsl(var(--text-secondary))] shrink-0" />
            ) : (
              <ChevronRight size={20} className="text-[hsl(var(--text-secondary))] shrink-0" />
            )
          ) : (
            <span className="w-5" />
          )}

          {/* Icon */}
          <span className="text-[hsl(var(--text-secondary))] shrink-0">
            {FILE_TYPE_ICONS[node.file_type] || <FileText size={20} />}
          </span>

          {/* Title */}
          <span className="flex-1 truncate text-base font-medium">
            {node.title}
          </span>

          {/* Children count for folders */}
          {isFolder && (
            <span className="text-[hsl(var(--text-secondary))] text-sm mr-1">
              {node.children?.length || 0}
            </span>
          )}

          {/* Action buttons - always visible on mobile for better UX */}
          {isFolder && (
            <button
              onClick={
                isMaterialFolderNode
                  ? triggerUpload
                  : (e) => {
                      e.stopPropagation();
                      startCreate(node.id, createFileType);
                    }
              }
              disabled={isMaterialFolderNode && isUploading}
              className={`p-2 rounded-lg text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--accent-primary))] active:bg-[hsl(var(--bg-tertiary))] touch-target ${
                isMaterialFolderNode && isUploading ? 'opacity-50 cursor-not-allowed' : ''
              }`}
              title={folderActionLabel}
              aria-label={folderActionLabel}
            >
              <Plus size={20} />
            </button>
          )}

          {/* Add/remove snippet to chat context */}
          {node.file_type === "snippet" && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                if (isMaterialAttached(node.id)) {
                  removeMaterial(node.id);
                  return;
                }
                const success = addMaterial(node.id, node.title);
                if (!success && isAtLimit) {
                  toast.info(t('editor:fileTree.maxMaterials', { max: MAX_ATTACHED_MATERIALS }));
                }
              }}
              className={`p-2 rounded-lg active:bg-[hsl(var(--bg-tertiary))] touch-target ${
                isMaterialAttached(node.id)
                  ? "text-[hsl(var(--accent-primary))]"
                  : "text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--accent-primary))]"
              }`}
              title={isMaterialAttached(node.id) ? t('editor:fileTree.removeFromChat') : t('editor:fileTree.addToChat')}
            >
              {isMaterialAttached(node.id) ? <Check size={18} /> : <MessageSquarePlus size={18} />}
            </button>
          )}

          {/* Delete button */}
          {!isFolder && (
            <button
              onClick={(e) => handleDelete(e, node.id, node.title)}
              className="p-2 rounded-lg text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--error))] active:bg-[hsl(var(--bg-tertiary))] touch-target"
              title={t('common:delete')}
            >
              <Trash2 size={18} />
            </button>
          )}
        </div>

        {/* Create input row */}
        {isCreating === node.id && (
          <div
            className="flex items-center gap-2 px-4 py-2 bg-[hsl(var(--bg-tertiary))]"
            style={{ paddingLeft: `${28 + depth * 16}px` }}
          >
            <input
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
              className="flex-1 bg-[hsl(var(--bg-primary))] border border-[hsl(var(--accent-primary))] rounded-lg px-3 py-2 text-base text-[hsl(var(--text-primary))] placeholder-[hsl(var(--text-secondary))] focus:outline-none min-h-[44px]"
            />
          </div>
        )}

        {/* Children - only render if expanded */}
        {isFolder && isExpanded && node.children && node.children.length > 0 && (
          <div>
            {node.children.map((child) => renderNode(child, depth + 1))}
          </div>
        )}

        {/* Empty folder indicator */}
        {isFolder && isExpanded && (!node.children || node.children.length === 0) && (
          <div
            className="px-4 py-3 text-[hsl(var(--text-secondary))] text-sm italic"
            style={{ paddingLeft: `${28 + depth * 16}px` }}
          >
            {isMaterialFolder(node)
              ? t('editor:fileTree.emptyMaterialFolder')
              : t('editor:fileTree.emptyFolder')}
          </div>
        )}
      </div>
    );
  }, [
    expandedFolders,
    selectedItem,
    isCreating,
    newItemName,
    newItemType,
    toggleFolder,
    handleSelect,
    startCreate,
    cancelCreate,
    handleCreate,
    handleDelete,
    getCreateFileType,
    isMaterialFolder,
    isMaterialAttached,
    removeMaterial,
    addMaterial,
    isAtLimit,
    isUploading,
    triggerUpload,
    getPlaceholder,
    t,
  ]);

  // Loading state
  if (loading && isInitialLoad && tree.length === 0) {
    return (
      <div className={`flex flex-col h-full ${className || ''}`}>
        {/* Search input disabled during loading */}
        <div className="p-3 border-b border-[hsl(var(--border-color))]">
          <FileSearchInput
            value=""
            onChange={() => {}}
            onClear={() => {}}
            placeholder={t('common:loading')}
            className="w-full opacity-50 pointer-events-none"
            disabled
          />
        </div>
        <div className="flex-1 flex items-center justify-center text-[hsl(var(--text-secondary))]">
          {t('common:loading')}
        </div>
      </div>
    );
  }

  // Empty state
  if (tree.length === 0) {
    return (
      <div className={`flex flex-col h-full ${className || ''}`}>
        {/* Search input disabled when no files */}
        <div className="p-3 border-b border-[hsl(var(--border-color))]">
          <FileSearchInput
            value=""
            onChange={() => {}}
            onClear={() => {}}
            placeholder={t('editor:fileTree.searchPlaceholder')}
            className="w-full opacity-50 pointer-events-none"
            disabled
          />
        </div>
        <div className="flex-1 flex flex-col items-center justify-center text-[hsl(var(--text-secondary))] p-4">
          <Folder size={48} className="mb-4 opacity-30" />
          <p className="text-sm">{t('editor:fileTree.folderEmpty')}</p>
          <p className="text-xs mt-1">{t('editor:fileTree.createWithAI')}</p>
        </div>
      </div>
    );
  }

  return (
    <div data-testid="file-tree" className={`flex flex-col h-full overflow-hidden ${className || ''}`}>
      <input
        type="file"
        ref={fileInputRef}
        onChange={handleFileUpload}
        accept=".txt"
        className="hidden"
      />

      {/* Search bar - prominent at top with dropdown support */}
      <div className="p-3 border-b border-[hsl(var(--border-color))] shrink-0">
        <div ref={searchContainerRef} className="relative">
          <FileSearchInput
            value={searchQuery}
            onChange={setSearchQuery}
            onClear={handleClearSearch}
            onFocus={() => setIsSearchFocused(true)}
            onBlur={() => {
              // Delay to allow click on dropdown items
              setTimeout(() => setIsSearchFocused(false), 150);
            }}
            onKeyDown={handleSearchKeyDown}
            placeholder={t('editor:fileTree.searchPlaceholder')}
            className="w-full"
          />
          {/* Search results dropdown - mobile optimized */}
          <SearchResultsDropdown
            results={searchResults}
            selectedIndex={selectedSearchIndex}
            onSelect={handleSearchSelect}
            onHover={setSelectedSearchIndex}
            visible={showSearchDropdown}
            loading={isSearching}
            onClose={handleCloseSearchDropdown}
          />
        </div>
      </div>

      {uploadError && (
        <div className="mx-3 mt-2 px-3 py-2 bg-[hsl(var(--error)/0.1)] border border-[hsl(var(--error)/0.3)] rounded text-[hsl(var(--error))] text-xs">
          {uploadError}
        </div>
      )}

      {isUploading && (
        <div className="mx-3 mt-2 px-3 py-2 bg-[hsl(var(--accent-primary)/0.1)] border border-[hsl(var(--accent-primary)/0.3)] rounded text-[hsl(var(--accent-primary))] text-xs">
          {t('editor:fileTree.uploading')}
        </div>
      )}

      {/* File tree list - scrollable */}
      <div className="flex-1 overflow-y-auto overflow-x-hidden -webkit-overflow-scrolling-touch">
        {filteredTree.length === 0 && searchQuery && !showSearchDropdown ? (
          <div className="flex flex-col items-center justify-center py-12 text-[hsl(var(--text-secondary))]">
            <Search size={32} className="mb-2 opacity-30" />
            <p className="text-sm">{t('editor:fileTree.noSearchResults')}</p>
          </div>
        ) : (
          filteredTree.map((node) => renderNode(node))
        )}
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

/**
 * Memoized MobileFileTree component for mobile file navigation.
 *
 * @see MobileFileTreeComponent for implementation details
 */
export const MobileFileTree = React.memo(MobileFileTreeComponent);
