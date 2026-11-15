import { useState, useCallback, useEffect, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { inspirationsApi } from '../lib/api';
import { logger } from '../lib/logger';
import type {
  Inspiration,
  InspirationDetail,
  InspirationListResponse,
  CopyInspirationResponse,
  MyInspirationSubmission,
  MyInspirationSubmissionsResponse,
} from '../types';

export interface UseInspirationsOptions {
  /** Filter by project type */
  projectType?: string;
  /** Search query */
  search?: string;
  /** Tags filter (comma-separated) */
  tags?: string;
  /** Page number */
  page?: number;
  /** Page size */
  pageSize?: number;
  /** Featured only mode */
  featuredOnly?: boolean;
  /** Enable/disable automatic fetching */
  enabled?: boolean;
}

export interface UseInspirationsReturn {
  /** List of inspirations */
  inspirations: Inspiration[];
  /** Total count */
  total: number;
  /** Current page */
  page: number;
  /** Page size */
  pageSize: number;
  /** Loading state */
  isLoading: boolean;
  /** Background fetching state */
  isFetching: boolean;
  /** Error state */
  error: Error | null;
  /** Refetch inspirations */
  refetch: () => Promise<void>;
  /** Get inspiration detail */
  getDetail: (id: string) => Promise<InspirationDetail | null>;
  /** Current detail */
  currentDetail: InspirationDetail | null;
  /** Loading state for detail */
  isDetailLoading: boolean;
  /** Copy inspiration to workspace */
  copyInspiration: (id: string, projectName?: string) => Promise<CopyInspirationResponse>;
  /** Loading state for copy operation */
  isCopying: boolean;
  /** Reset detail view */
  resetDetail: () => void;
}

/**
 * Hook for managing inspirations (project templates)
 *
 * Provides functionality for:
 * - Listing inspirations with filtering and pagination
 * - Getting featured inspirations
 * - Viewing inspiration details
 * - Copying inspirations to user's workspace
 *
 * @param options - Configuration options for filtering and pagination
 * @returns Object containing inspirations data, loading states, and actions
 *
 * @example
 * ```tsx
 * const {
 *   inspirations,
 *   isLoading,
 *   featured,
 *   getDetail,
 *   copyInspiration,
 * } = useInspirations({
 *   projectType: 'novel',
 *   search: 'fantasy',
 *   pageSize: 24,
 * });
 *
 * // Display inspirations
 * if (isLoading) return <Spinner />;
 * return inspirations.map(insp => <InspirationCard key={insp.id} {...insp} />);
 * ```
 */
export function useInspirations(options: UseInspirationsOptions = {}): UseInspirationsReturn {
  const {
    projectType,
    search,
    tags,
    page = 1,
    pageSize = 12,
    featuredOnly = false,
    enabled = true,
  } = options;

  const queryClient = useQueryClient();
  const [currentDetail, setCurrentDetail] = useState<InspirationDetail | null>(null);
  const [isDetailLoading, setIsDetailLoading] = useState(false);
  const detailRequestRef = useRef(0);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      detailRequestRef.current += 1;
    };
  }, []);

  // Fetch inspirations list
  const {
    data: listData,
    isLoading,
    isFetching,
    error,
    refetch,
  } = useQuery<InspirationListResponse, Error>({
    queryKey: ['inspirations', projectType, search, tags, page, pageSize, featuredOnly],
    queryFn: () =>
      inspirationsApi.list({
        project_type: projectType,
        search,
        tags,
        page,
        page_size: pageSize,
        featured_only: featuredOnly,
      }),
    enabled,
    staleTime: 5 * 60 * 1000, // 5 minutes
  });

  // Copy inspiration mutation
  const copyMutation = useMutation<
    CopyInspirationResponse,
    Error,
    { id: string; projectName?: string }
  >({
    mutationFn: ({ id, projectName }) => inspirationsApi.copy(id, projectName),
    onSuccess: () => {
      // Invalidate projects list to show the newly created project
      queryClient.invalidateQueries({ queryKey: ['projects'] });
      // Invalidate inspirations to update copy_count
      queryClient.invalidateQueries({ queryKey: ['inspirations'] });
    },
  });

  // Get inspiration detail
  const getDetail = useCallback(async (id: string): Promise<InspirationDetail | null> => {
    const request = ++detailRequestRef.current;
    setCurrentDetail(null);
    setIsDetailLoading(true);
    try {
      const detail = await inspirationsApi.get(id);
      if (!mountedRef.current || detailRequestRef.current !== request) return null;
      setCurrentDetail(detail);
      return detail;
    } catch (err) {
      logger.error('Failed to load inspiration detail:', err);
      if (mountedRef.current && detailRequestRef.current === request) setCurrentDetail(null);
      return null;
    } finally {
      if (mountedRef.current && detailRequestRef.current === request) setIsDetailLoading(false);
    }
  }, []);

  // Reset detail
  const resetDetail = useCallback(() => {
    detailRequestRef.current += 1;
    setCurrentDetail(null);
    setIsDetailLoading(false);
  }, []);

  // Refetch wrapper
  const handleRefetch = useCallback(async () => {
    await refetch();
  }, [refetch]);

  // Copy inspiration wrapper
  const copyInspiration = useCallback(
    async (id: string, projectName?: string): Promise<CopyInspirationResponse> => {
      return copyMutation.mutateAsync({ id, projectName });
    },
    [copyMutation]
  );

  return {
    inspirations: listData?.inspirations ?? [],
    total: listData?.total ?? 0,
    page: listData?.page ?? 1,
    pageSize: listData?.page_size ?? 12,
    isLoading,
    isFetching: Boolean(isFetching),
    error: error ?? null,
    refetch: handleRefetch,
    getDetail,
    currentDetail,
    isDetailLoading,
    copyInspiration,
    isCopying: copyMutation.isPending,
    resetDetail,
  };
}

/**
 * Hook for featured inspirations only
 *
 * Simpler hook for components that only need featured inspirations
 *
 * @param limit - Maximum number of featured inspirations to fetch (default: 6)
 * @returns Object containing featured inspirations, loading state, and error state
 *
 * @example
 * ```tsx
 * const { featured, isLoading, error } = useFeaturedInspirations(3);
 *
 * if (isLoading) return <Skeleton />;
 * if (error) return <ErrorMessage error={error} />;
 *
 * return featured.map(insp => <FeaturedCard key={insp.id} {...insp} />);
 * ```
 */
export function useFeaturedInspirations(limit: number = 6) {
  const { data, isLoading, isFetching, error, refetch } = useQuery<Inspiration[], Error>({
    queryKey: ['inspirations-featured', limit],
    queryFn: () => inspirationsApi.getFeatured(limit),
    staleTime: 10 * 60 * 1000, // 10 minutes
  });

  return {
    featured: data ?? [],
    isLoading,
    isFetching: Boolean(isFetching),
    error,
    refetch,
  };
}

/**
 * Hook for current user's inspiration submissions.
 */
export function useMyInspirationSubmissions(options?: {
  page?: number;
  pageSize?: number;
  enabled?: boolean;
}) {
  const page = options?.page ?? 1;
  const pageSize = options?.pageSize ?? 5;
  const enabled = options?.enabled ?? true;

  const { data, isLoading, isFetching, error, refetch } = useQuery<MyInspirationSubmissionsResponse, Error>({
    queryKey: ['my-inspiration-submissions', page, pageSize],
    queryFn: () => inspirationsApi.getMySubmissions({ page, page_size: pageSize }),
    enabled,
    staleTime: 30 * 1000,
  });

  return {
    items: (data?.items ?? []) as MyInspirationSubmission[],
    total: data?.total ?? 0,
    page: data?.page ?? page,
    pageSize: data?.page_size ?? pageSize,
    isLoading,
    isFetching: Boolean(isFetching),
    error,
    refetch,
  };
}
