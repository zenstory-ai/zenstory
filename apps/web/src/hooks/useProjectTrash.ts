import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { fileTrashApi } from '../lib/api';
import { logger } from '../lib/logger';
import { toast } from '../lib/toast';
import type { FileDeletionBatchSummary, FileDeletionTarget, FileTreeNode } from '../types';

export function collectSubtreeTargets(
  node: FileTreeNode,
  fallbackParentId: string | null = null,
): FileDeletionTarget[] {
  const targets: FileDeletionTarget[] = [
    {
      id: node.id,
      title: node.title,
      file_type: node.file_type,
      parent_id: node.parent_id ?? fallbackParentId,
      order: node.order ?? 0,
    },
  ];
  for (const child of node.children ?? []) {
    targets.push(...collectSubtreeTargets(child, node.id));
  }
  return targets;
}

export interface ProjectTrashController {
  open: boolean;
  loading: boolean;
  error: boolean;
  batches: FileDeletionBatchSummary[];
  nextOffset: number | null;
  loadingMore: boolean;
  restoringBatchId: string | null;
  openTrash: () => Promise<void>;
  closeTrash: () => void;
  loadMore: () => Promise<void>;
  restoreBatch: (batchId: string) => Promise<void>;
}

export function useProjectTrash(
  projectId: string | null,
  onRestored: () => void | Promise<void>,
): ProjectTrashController {
  const { t } = useTranslation(['editor']);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const [batches, setBatches] = useState<FileDeletionBatchSummary[]>([]);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [restoringBatchId, setRestoringBatchId] = useState<string | null>(null);

  const activeProjectIdRef = useRef<string | null>(projectId);
  const mountedRef = useRef(true);
  const onRestoredRef = useRef(onRestored);
  onRestoredRef.current = onRestored;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    activeProjectIdRef.current = projectId;
    setOpen(false);
    setLoading(false);
    setError(false);
    setBatches([]);
    setNextOffset(null);
    setLoadingMore(false);
    setRestoringBatchId(null);
    return () => {
      activeProjectIdRef.current = null;
    };
  }, [projectId]);

  const openTrash = useCallback(async () => {
    if (!projectId || loading) return;
    setOpen(true);
    setLoading(true);
    setError(false);
    try {
      const response = await fileTrashApi.list(projectId, { limit: 50, offset: 0 });
      if (mountedRef.current && projectId === activeProjectIdRef.current) {
        setBatches(response.batches);
        setNextOffset(response.next_offset);
      }
    } catch (err) {
      if (mountedRef.current && projectId === activeProjectIdRef.current) {
        setError(true);
        toast.error(t('editor:fileTree.trashLoadFailed'));
      }
      logger.error('Failed to load trash:', err);
    } finally {
      if (mountedRef.current && projectId === activeProjectIdRef.current) {
        setLoading(false);
      }
    }
  }, [projectId, loading, t]);

  const closeTrash = useCallback(() => {
    if (!restoringBatchId) setOpen(false);
  }, [restoringBatchId]);

  const loadMore = useCallback(async () => {
    if (!projectId || nextOffset === null || loadingMore) return;
    setLoadingMore(true);
    try {
      const response = await fileTrashApi.list(projectId, { limit: 50, offset: nextOffset });
      if (mountedRef.current && projectId === activeProjectIdRef.current) {
        setBatches((current) => [...current, ...response.batches]);
        setNextOffset(response.next_offset);
      }
    } catch (err) {
      if (mountedRef.current && projectId === activeProjectIdRef.current) {
        toast.error(t('editor:fileTree.trashLoadFailed'));
      }
      logger.error('Failed to load more trash:', err);
    } finally {
      if (mountedRef.current && projectId === activeProjectIdRef.current) {
        setLoadingMore(false);
      }
    }
  }, [projectId, nextOffset, loadingMore, t]);

  const restoreBatch = useCallback(async (batchId: string) => {
    if (!projectId || restoringBatchId) return;
    setRestoringBatchId(batchId);
    try {
      await fileTrashApi.restore(projectId, batchId);
      const response = await fileTrashApi.list(projectId, { limit: 50, offset: 0 });
      if (mountedRef.current && projectId === activeProjectIdRef.current) {
        setBatches(response.batches);
        setNextOffset(response.next_offset);
        setError(false);
        await onRestoredRef.current();
      }
    } catch (err) {
      if (mountedRef.current && projectId === activeProjectIdRef.current) {
        toast.error(t('editor:fileTree.restoreFailed'));
      }
      logger.error('Failed to restore deletion batch:', err);
    } finally {
      if (mountedRef.current && projectId === activeProjectIdRef.current) {
        setRestoringBatchId(null);
      }
    }
  }, [projectId, restoringBatchId, t]);

  return {
    open,
    loading,
    error,
    batches,
    nextOffset,
    loadingMore,
    restoringBatchId,
    openTrash,
    closeTrash,
    loadMore,
    restoreBatch,
  };
}
