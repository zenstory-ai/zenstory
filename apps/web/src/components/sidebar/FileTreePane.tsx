import React, { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { useProject } from "../../contexts/ProjectContext";
import { useMobileLayout } from "../../contexts/MobileLayoutContext";
import { useMaterialAttachment, MAX_ATTACHED_MATERIALS } from "../../contexts/MaterialAttachmentContext";
import { fileApi } from "../../lib/api";
import { toast } from "../../lib/toast";
import { FOLDER_TYPE_MAP } from "../../lib/folderTypeMap";
import type { FileTreeNode, TreeNodeType } from "../../types";
import { useFileSearch } from "../../hooks/useFileSearch";
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
  const [loading, setLoading] = useState(false);
  const [isInitialLoad, setIsInitialLoad] = useState(true);

  // UI states
  const [expandedFolders, setExpandedFolders] = useState<Set<string>>(
    new Set(),
  );
  const [isCreating, setIsCreating] = useState<string | null>(null);
  const [newItemName, setNewItemName] = useState("");
  const [newItemType, setNewItemType] = useState<string>("draft");

  // Upload state
  const [isUploading, setIsUploading] = useState(false);
  const mountedRef = useRef(true);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const draftFileInputRef = useRef<HTMLInputElement>(null);

  const { isSearchOpen: isFileSearchOpen, openSearch, closeSearch } = useFileSearchContext();

  const [fileSearchQuery, setFileSearchQuery] = useState('');
  const [selectedResultIndex, setSelectedResultIndex] = useState(0);
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
    const requestId = ++loadRequestIdRef.current;
    loadAbortControllerRef.current?.abort();
    const abortController = new AbortController();
    loadAbortControllerRef.current = abortController;

    if (showLoading) {
      setLoading(true);
    }
    try {
      // Try to load from new File API first
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
      // Fallback: tree is empty, folders will be created on first use
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

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      loadAbortControllerRef.current?.abort();
      mountedRef.current = false;
    };
  }, []);

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

  // Delete item
  const handleDelete = async (e: React.MouseEvent, fileId: string) => {
    e.stopPropagation();

    if (!confirm(t('common:confirmDelete'))) return;

    try {
      await fileApi.delete(fileId);

      if (currentProjectId !== activeProjectIdRef.current) return;
      // Clear selection if deleted item was selected
      if (selectedItem?.id === fileId) {
        setSelectedItem(null);
      }

      await loadData(false); // silent refresh - no loading indicator
    } catch (error) {
      logger.error("Failed to delete item:", error);
      toast.error(t('editor:fileTree.deleteFailed'));
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
      toast.success(t('editor:fileTree.uploadSuccess'));
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

    setIsUploading(true);

    const errorCodeMap: Record<string, string> = {
      ERR_FILE_TOO_LARGE: t('editor:fileTree.errorFileTooLarge'),
      ERR_FILE_TYPE_INVALID: t('editor:fileTree.errorFileTypeInvalid'),
      ERR_FILE_CONTENT_TOO_LONG: t('editor:fileTree.errorFileContentTooLong'),
      ERR_VALIDATION_ERROR: t('editor:fileTree.errorValidation'),
    };

    const translateError = (raw: string) => {
      for (const [code, msg] of Object.entries(errorCodeMap)) {
        if (raw.includes(code)) {
          const fileName = raw.split(':')[0]?.trim();
          return fileName ? `${fileName}: ${msg}` : msg;
        }
      }
      return raw;
    };

    try {
      const allErrors: string[] = [];
      let totalChapters = 0;
      for (let i = 0; i < files.length; i++) {
        if (!mountedRef.current) return;
        const result = await fileApi.uploadDraft(currentProjectId, files[i]);
        totalChapters += result.total;
        if (result.errors?.length) {
          allErrors.push(...result.errors.map(translateError));
        }
      }
      if (!mountedRef.current) return;
      if (allErrors.length > 0) {
        toast.error(allErrors.join('\n'));
      }
      if (totalChapters > 0) {
        toast.success(t('editor:fileTree.draftUploadSuccess', { count: totalChapters }));
      } else if (allErrors.length === 0) {
        toast.info(t('editor:fileTree.draftUploadEmpty'));
      }
      await loadData(false);
    } catch (error) {
      if (!mountedRef.current) return;
      logger.error("Failed to upload draft:", error);
      toast.error(error instanceof Error ? error.message : t('editor:fileTree.uploadFailed'));
    } finally {
      if (mountedRef.current) {
        setIsUploading(false);
      }
      if (draftFileInputRef.current) {
        draftFileInputRef.current.value = "";
      }
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

    return (
      <div key={node.id} style={{ marginLeft: depth > 0 ? 12 : 0 }}>
        {/* Node header */}
        <div
          className={`flex items-center gap-2 px-2 py-1.5 rounded cursor-pointer group transition-colors ${
            isSelected && !isFolder
              ? "bg-[hsl(var(--bg-tertiary))] text-[hsl(var(--text-primary))]"
              : "text-[hsl(var(--text-primary))] hover:bg-[hsl(var(--bg-tertiary))]"
          }`}
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
          <div className="opacity-0 group-hover:opacity-100 flex items-center gap-0.5 transition-opacity">
            {/* For draft folder: + button triggers draft upload */}
            {isDraftFolder(node) ? (
              <button
                onClick={triggerDraftUpload}
                disabled={isUploading}
                className={`p-2 md:p-1 min-h-[44px] min-w-[44px] md:min-h-0 md:min-w-0 flex items-center justify-center text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--accent-primary))] ${isUploading ? 'opacity-50 cursor-not-allowed' : ''}`}
                title={t('editor:fileTree.uploadDraft')}
              >
                <Plus size={14} />
              </button>
            ) : isMaterialFolder(node) ? (
              /* For material folder: + button triggers upload */
              <button
                onClick={triggerUpload}
                disabled={isUploading}
                className={`p-2 md:p-1 min-h-[44px] min-w-[44px] md:min-h-0 md:min-w-0 flex items-center justify-center text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--accent-primary))] ${isUploading ? 'opacity-50 cursor-not-allowed' : ''}`}
                title={t('editor:fileTree.uploadMaterial')}
              >
                <Plus size={14} />
              </button>
            ) : (
              /* Add button for other folders */
              isFolder && (
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    startCreate(node.id, createFileType);
                  }}
                  className="p-2 md:p-1 min-h-[44px] min-w-[44px] md:min-h-0 md:min-w-0 flex items-center justify-center text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--accent-primary))]"
                  title={`${t('common:create')} ${getFileTypeName(createFileType)}`}
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
                      alert(t('editor:fileTree.maxMaterials', { max: MAX_ATTACHED_MATERIALS }));
                    }
                  }
                }}
                className={`p-2 md:p-1 min-h-[44px] min-w-[44px] md:min-h-0 md:min-w-0 flex items-center justify-center ${
                  isMaterialAttached(node.id)
                    ? "text-[hsl(var(--accent-primary))]"
                    : "text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--accent-primary))]"
                }`}
                title={isMaterialAttached(node.id) ? t('editor:fileTree.removeFromChat') : t('editor:fileTree.addToChat')}
              >
                {isMaterialAttached(node.id) ? <Check size={14} /> : <MessageSquarePlus size={14} />}
              </button>
            )}

            {/* Delete button (not for root folders) */}
            {!isFolder && (
              <button
                onClick={(e) => handleDelete(e, node.id)}
                className="p-2 md:p-1 min-h-[44px] min-w-[44px] md:min-h-0 md:min-w-0 flex items-center justify-center text-[hsl(var(--text-secondary))] hover:text-[hsl(var(--error))]"
                title={t('common:delete')}
              >
                <Trash2 size={12} />
              </button>
            )}
          </div>
        </div>

        {/* Children */}
        {isFolder && isExpanded && (
          <div className="mt-0.5">
            {/* New item input */}
            {isCreating === node.id && (
              <div
                className="flex items-center gap-2 px-2 py-1"
                style={{ marginLeft: 12 }}
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
                  className="flex-1 bg-[hsl(var(--bg-tertiary))] border border-[hsl(var(--accent-primary))] rounded px-2 py-1 text-sm text-[hsl(var(--text-primary))] placeholder-[hsl(var(--text-secondary))] focus:outline-none"
                />
              </div>
            )}

            {/* Child nodes */}
            {node.children?.map((child) => renderNode(child, depth + 1))}

            {/* Empty state */}
            {(!node.children || node.children.length === 0) &&
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
        <Folder size={48} className="mb-4 opacity-30" />
        <p className="text-sm">{t('editor:fileTree.folderEmpty')}</p>
        <p className="text-xs mt-1">{t('editor:fileTree.createWithAI')}</p>
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
        accept=".txt"
        className="hidden"
      />

      {/* Hidden file input for draft upload */}
      <input
        type="file"
        ref={draftFileInputRef}
        onChange={handleDraftUpload}
        accept=".txt,.md"
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
      {tree.map((node) => renderNode(node, 0))}
    </div>
  );
};
