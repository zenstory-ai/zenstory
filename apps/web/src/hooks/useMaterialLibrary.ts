import { useState, useCallback, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { materialsApi } from '../lib/materialsApi';
import { logger } from "../lib/logger";
import { subscriptionApi, subscriptionQueryKeys } from '../lib/subscriptionApi';
import { hasMaterialsLibraryAccess, isFeatureNotIncludedError } from '../lib/materialsAccess';
import type {
  LibrarySummaryItem,
  MaterialEntityType,
  MaterialPreviewResponse,
} from '../lib/materialsApi';

export const MATERIAL_LIBRARY_SUMMARY_QUERY_KEY = ['material-library-summary'] as const;

/** Re-fetch the sidebar summary when it is older than this on pane mount. */
const SUMMARY_REFRESH_AFTER_MS = 30 * 1000;

export interface PreviewEntityInfo {
  novelId: number;
  entityType: MaterialEntityType;
  entityId: number;
}

export interface MaterialLibraryState {
  /** All completed material libraries */
  libraries: LibrarySummaryItem[];
  /** Loading state for library list */
  isLoading: boolean;
  /** Background fetching state for library list */
  isFetching: boolean;
  /** Error state (never set for a missing entitlement, see accessDenied) */
  error: Error | null;
  /** The current plan does not include the materials library. */
  accessDenied: boolean;
  /** Retry loading the library summary. */
  refetch: () => Promise<void>;
  /** Re-fetch the summary if the cached copy is older than 30s. */
  refreshIfStale: () => void;
  /** Currently expanded novel IDs */
  expandedNovels: Set<number>;
  /** Currently expanded entity types per novel */
  expandedTypes: Map<string, boolean>;
  /** Toggle novel expansion */
  toggleNovel: (novelId: number) => void;
  /** Toggle entity type expansion */
  toggleEntityType: (novelId: number, entityType: MaterialEntityType) => void;
  /** Current preview data */
  preview: MaterialPreviewResponse | null;
  /** Preview entity info (for import dialog) */
  previewEntityInfo: PreviewEntityInfo | null;
  /** Loading state for preview */
  isPreviewLoading: boolean;
  /** Load preview for an entity */
  loadPreview: (novelId: number, entityType: MaterialEntityType, entityId: number) => Promise<void>;
  /** Clear preview */
  clearPreview: () => void;
}

export function useMaterialLibrary(): MaterialLibraryState {
  const [expandedNovels, setExpandedNovels] = useState<Set<number>>(new Set());
  const [expandedTypes, setExpandedTypes] = useState<Map<string, boolean>>(new Map());
  const [preview, setPreview] = useState<MaterialPreviewResponse | null>(null);
  const [previewEntityInfo, setPreviewEntityInfo] = useState<PreviewEntityInfo | null>(null);
  const [isPreviewLoading, setIsPreviewLoading] = useState(false);
  // Monotonic sequence so out-of-order preview responses cannot clobber a
  // newer one (e.g. an earlier slow request that fails after a later success).
  const previewSeqRef = useRef(0);

  // Entitlement first: free plans never request the paid summary endpoint.
  const { data: subscriptionStatus, isLoading: isAccessLoading } = useQuery({
    queryKey: subscriptionQueryKeys.status(),
    queryFn: () => subscriptionApi.getStatus(),
  });
  const access = hasMaterialsLibraryAccess(
    subscriptionStatus?.features as Record<string, unknown> | undefined,
    subscriptionStatus?.tier,
  );

  const { data, isLoading, isFetching, error, refetch, dataUpdatedAt } = useQuery({
    queryKey: MATERIAL_LIBRARY_SUMMARY_QUERY_KEY,
    queryFn: () => materialsApi.getLibrarySummary(),
    staleTime: 5 * 60 * 1000, // 5 minutes
    // Unknown entitlement (status failed to load) still tries; a 402 then
    // switches the UI to the upgrade notice instead of an error.
    enabled: !isAccessLoading && access !== false,
    retry: (failureCount, retryError) =>
      !isFeatureNotIncludedError(retryError) && failureCount < 1,
  });
  const accessDenied = access === false || isFeatureNotIncludedError(error);

  const toggleNovel = useCallback((novelId: number) => {
    setExpandedNovels(prev => {
      const next = new Set(prev);
      if (next.has(novelId)) {
        next.delete(novelId);
      } else {
        next.add(novelId);
      }
      return next;
    });
  }, []);

  const toggleEntityType = useCallback((novelId: number, entityType: MaterialEntityType) => {
    const key = `${novelId}:${entityType}`;
    setExpandedTypes(prev => {
      const next = new Map(prev);
      next.set(key, !prev.get(key));
      return next;
    });
  }, []);

  const loadPreview = useCallback(async (
    novelId: number,
    entityType: MaterialEntityType,
    entityId: number,
  ) => {
    const seq = ++previewSeqRef.current;
    setIsPreviewLoading(true);
    try {
      const data = await materialsApi.getPreview(novelId, entityType, entityId);
      // Ignore responses that have been superseded by a newer request.
      if (previewSeqRef.current === seq) {
        setPreview(data);
        setPreviewEntityInfo({ novelId, entityType, entityId });
      }
    } catch (err) {
      // Only clear the preview if this is still the latest request, so a stale
      // failure cannot erase a newer, successfully-loaded preview.
      if (previewSeqRef.current === seq) {
        setPreview(null);
        setPreviewEntityInfo(null);
      }
      logger.error('Failed to load material preview:', err);
    } finally {
      if (previewSeqRef.current === seq) {
        setIsPreviewLoading(false);
      }
    }
  }, []);

  const clearPreview = useCallback(() => {
    previewSeqRef.current += 1;
    setPreview(null);
    setPreviewEntityInfo(null);
    setIsPreviewLoading(false);
  }, []);

  const handleRefetch = useCallback(async () => {
    await refetch();
  }, [refetch]);

  const refreshIfStale = useCallback(() => {
    if (accessDenied || !dataUpdatedAt) return;
    if (Date.now() - dataUpdatedAt > SUMMARY_REFRESH_AFTER_MS) {
      void refetch();
    }
  }, [accessDenied, dataUpdatedAt, refetch]);

  return {
    libraries: accessDenied ? [] : data ?? [],
    isLoading: Boolean(isAccessLoading || isLoading),
    isFetching: Boolean(isFetching),
    error: accessDenied ? null : (error as Error | null),
    accessDenied,
    refetch: handleRefetch,
    refreshIfStale,
    expandedNovels,
    expandedTypes,
    toggleNovel,
    toggleEntityType,
    preview,
    previewEntityInfo,
    isPreviewLoading,
    loadPreview,
    clearPreview,
  };
}
