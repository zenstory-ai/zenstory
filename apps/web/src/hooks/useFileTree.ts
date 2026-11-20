import { useState, useEffect, useCallback, useRef } from "react";
import type React from "react";
import { useTranslation } from "react-i18next";
import { useProject } from "../contexts/ProjectContext";
import { fileApi } from "../lib/api";
import { isFileTitleTooLong } from "../lib/fileTitle";
import { FOLDER_TYPE_MAP, MATERIAL_FOLDER_NAMES } from "../lib/folderTypeMap";
import { findAncestorFolderIds } from "../lib/lastOpenedFile";
import { clearLiveTreeRemoved, notifyFileRemoved } from "../lib/fileRemovalEvents";
import { logger } from "../lib/logger";
import { toast } from "../lib/toast";
import type { FileTreeNode, TreeNodeType } from "../types";

const FILE_TYPE_TO_NODE_TYPE: Record<string, TreeNodeType> = {
  outline: "outline",
  draft: "draft",
  character: "character",
  lore: "lore",
  snippet: "material",
  folder: "folder",
};

const isAbortError = (error: unknown): boolean =>
  (error instanceof DOMException && error.name === "AbortError") ||
  (typeof error === "object" &&
    error !== null &&
    "name" in error &&
    (error as { name?: unknown }).name === "AbortError");

export const getCreateFileType = (folderTitle: string): string =>
  FOLDER_TYPE_MAP[folderTitle] || "draft";

export const isMaterialFolder = (node: FileTreeNode): boolean =>
  node.file_type === "folder" &&
  MATERIAL_FOLDER_NAMES.some((name) => name === node.title);

export interface PendingDeleteFile {
  id: string;
  title: string;
  projectId: string | null;
}

export interface UseFileTreeOptions {
  /** Called right after updating ProjectContext selection (e.g. mobile panel switch). */
  onSelectItem?: (id: string, fileType: string, title: string) => void;
  /** How material upload errors are surfaced: toast (desktop) or inline alert state (mobile). */
  uploadErrorMode?: "toast" | "inline";
}

export function useFileTree(options: UseFileTreeOptions = {}) {
  const { uploadErrorMode = "toast" } = options;
  const { t } = useTranslation(["editor", "common"]);
  const {
    currentProjectId,
    selectedItem,
    setSelectedItem,
    fileTreeVersion,
    triggerFileTreeRefresh,
  } = useProject();

  // Data state
  const [tree, setTree] = useState<FileTreeNode[]>([]);
  const [loading, setLoading] = useState(false);
  const [isInitialLoad, setIsInitialLoad] = useState(true);
  const [loadError, setLoadError] = useState(false);

  // UI states
  const [expandedFolders, setExpandedFolders] = useState<Set<string>>(new Set());
  const [isCreating, setIsCreating] = useState<string | null>(null);
  const [newItemName, setNewItemName] = useState("");
  const [newItemType, setNewItemType] = useState<string>("draft");
  const [pendingDelete, setPendingDelete] = useState<PendingDeleteFile | null>(null);
  const [deletingFile, setDeletingFile] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const mountedRef = useRef(true);
  const loadRequestIdRef = useRef(0);
  const loadAbortControllerRef = useRef<AbortController | null>(null);
  const activeProjectIdRef = useRef(currentProjectId);
  const selectedItemRef = useRef(selectedItem);
  const treeRef = useRef(tree);
  const revealedSelectionRef = useRef<string | null>(null);
  const uploadErrorTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onSelectItemRef = useRef(options.onSelectItem);

  selectedItemRef.current = selectedItem;
  treeRef.current = tree;
  onSelectItemRef.current = options.onSelectItem;

  const loadData = useCallback(
    async (showLoading = false) => {
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
        const response = await fileApi.getTree(currentProjectId, {
          signal: abortController.signal,
        });
        if (abortController.signal.aborted || requestId !== loadRequestIdRef.current) {
          return;
        }
        const nextTree = response.tree || [];
        clearLiveTreeRemoved(currentProjectId, nextTree);
        setTree(nextTree);
        setLoadError(false);
        if (
          selectedAtRequest &&
          selectedItemRef.current?.id === selectedAtRequest.id &&
          findAncestorFolderIds(previousTree, selectedAtRequest.id) !== null &&
          findAncestorFolderIds(nextTree, selectedAtRequest.id) === null
        ) {
          notifyFileRemoved(currentProjectId, selectedAtRequest.id);
          if (selectedItemRef.current?.id === selectedAtRequest.id) {
            setSelectedItem(null);
          }
        }

        // Auto-expand root folders
        const rootFolderIds = nextTree
          .filter((node) => node.file_type === "folder")
          .map((node) => node.id);
        setExpandedFolders((prev) => new Set([...prev, ...rootFolderIds]));
      } catch (error: unknown) {
        if (
          abortController.signal.aborted ||
          isAbortError(error) ||
          requestId !== loadRequestIdRef.current
        ) {
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
    },
    [currentProjectId, setSelectedItem],
  );

  // Track mount lifetime and abort any in-flight request / pending upload error timer on unmount.
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      loadAbortControllerRef.current?.abort();
      if (uploadErrorTimerRef.current) {
        clearTimeout(uploadErrorTimerRef.current);
        uploadErrorTimerRef.current = null;
      }
    };
  }, []);

  // Expand the folders around a selected file once (e.g. the file restored on
  // reopening the project), without re-opening folders the author collapses later.
  const selectedItemId = selectedItem?.id ?? null;
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

  // Reload when fileTreeVersion changes (silent refresh triggered by AI tool calls / edits)
  useEffect(() => {
    if (fileTreeVersion > 0) {
      loadData(false);
    }
  }, [fileTreeVersion, loadData]);

  // Initial load & project switch fence
  useEffect(() => {
    // Files from the previous project must not remain selectable while loading.
    activeProjectIdRef.current = currentProjectId;
    setTree([]);
    setLoadError(false);
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

  const handleSelect = useCallback(
    (id: string, fileType: string, title: string) => {
      setSelectedItem({
        id,
        type: FILE_TYPE_TO_NODE_TYPE[fileType] || "draft",
        title,
      });
      onSelectItemRef.current?.(id, fileType, title);
    },
    [setSelectedItem],
  );

  const startCreate = useCallback((parentId: string, fileType: string) => {
    setIsCreating(parentId);
    setNewItemType(fileType);
    setNewItemName("");
    // Make sure parent folder is expanded
    setExpandedFolders((prev) => new Set([...prev, parentId]));
  }, []);

  const cancelCreate = useCallback(() => {
    setIsCreating(null);
    setNewItemName("");
  }, []);

  const handleCreate = useCallback(
    async (parentId: string) => {
      if (!currentProjectId || !newItemName.trim()) {
        cancelCreate();
        return;
      }

      if (isFileTitleTooLong(newItemName.trim())) {
        toast.error(t("editor:fileTree.titleTooLong"));
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
        toast.error(t("editor:fileTree.createFailed"));
      }
    },
    [currentProjectId, newItemName, newItemType, loadData, cancelCreate, t],
  );

  const handleDelete = useCallback(
    (e: React.MouseEvent, fileId: string, fileTitle: string) => {
      e.stopPropagation();
      setPendingDelete({ id: fileId, title: fileTitle, projectId: currentProjectId });
    },
    [currentProjectId],
  );

  const handleConfirmDelete = useCallback(async () => {
    if (
      !currentProjectId ||
      !pendingDelete ||
      pendingDelete.projectId !== currentProjectId ||
      deletingFile
    ) {
      return;
    }
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

      await loadData(false);
    } catch (error) {
      logger.error("Failed to delete item:", error);
      toast.error(t("editor:fileTree.deleteFailed"));
    } finally {
      if (mountedRef.current) {
        setDeletingFile(false);
        setPendingDelete(null);
      }
    }
  }, [pendingDelete, deletingFile, currentProjectId, setSelectedItem, loadData, t]);

  const getPlaceholder = useCallback(
    (fileType: string): string => {
      const placeholderMap: Record<string, string> = {
        outline: t("editor:fileTree.newOutline"),
        draft: t("editor:fileTree.newDraft"),
        character: t("editor:fileTree.newCharacter"),
        lore: t("editor:fileTree.newLore"),
        snippet: t("editor:fileTree.newSnippet"),
        script: t("editor:fileTree.newScript"),
      };
      return placeholderMap[fileType] || t("editor:fileTree.newFileName");
    },
    [t],
  );

  const triggerUpload = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      if (isUploading) return;
      fileInputRef.current?.click();
    },
    [isUploading],
  );

  const handleFileUpload = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (!file || !currentProjectId) return;

      if (uploadErrorMode === "inline") {
        if (uploadErrorTimerRef.current) {
          clearTimeout(uploadErrorTimerRef.current);
          uploadErrorTimerRef.current = null;
        }
        setUploadError(null);
      }
      setIsUploading(true);

      try {
        await fileApi.upload(currentProjectId, file);
        await loadData(false);
        toast.success(t("editor:fileTree.uploadSuccess", { name: file.name }));
      } catch (error) {
        logger.error("Failed to upload file:", error);
        const message =
          error instanceof Error ? error.message : t("editor:fileTree.uploadFailed");
        if (uploadErrorMode === "inline") {
          if (mountedRef.current) {
            setUploadError(message);
            uploadErrorTimerRef.current = setTimeout(() => {
              uploadErrorTimerRef.current = null;
              if (mountedRef.current) setUploadError(null);
            }, 5000);
          }
        } else {
          toast.error(message);
        }
      } finally {
        if (mountedRef.current) {
          setIsUploading(false);
        }
        if (fileInputRef.current) {
          fileInputRef.current.value = "";
        }
      }
    },
    [currentProjectId, loadData, t, uploadErrorMode],
  );

  return {
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
    uploadError,
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
    getCreateFileType,
    isMaterialFolder,
    triggerUpload,
    handleFileUpload,
  };
}
