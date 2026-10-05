import { renderHook, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { useMaterialLibrary } from '../useMaterialLibrary'
import * as materialsApi from '@/lib/materialsApi'
import type { LibrarySummaryItem, MaterialPreviewResponse } from '@/lib/materialsApi'

// Mock materialsApi
vi.mock('@/lib/materialsApi', () => ({
  materialsApi: {
    getLibrarySummary: vi.fn(),
    getPreview: vi.fn(),
  },
}))

vi.mock('@/lib/subscriptionApi', () => ({
  subscriptionApi: { getStatus: vi.fn() },
  subscriptionQueryKeys: { status: () => ['subscription-status', 'test-user'] },
}))

// Mock @tanstack/react-query
const mockUseQuery = vi.fn()
vi.mock('@tanstack/react-query', () => ({
  useQuery: (args: unknown) => mockUseQuery(args),
}))

describe('useMaterialLibrary', () => {
  const mockLibraries: LibrarySummaryItem[] = [
    {
      id: 1,
      title: 'Novel 1',
      status: 'completed',
      counts: {
        characters: 10,
        worldview: 1,
        golden_fingers: 2,
        storylines: 5,
        relationships: 8,
      },
    },
    {
      id: 2,
      title: 'Novel 2',
      status: 'completed',
      counts: {
        characters: 5,
        worldview: 1,
        golden_fingers: 0,
        storylines: 3,
        relationships: 4,
      },
    },
  ]

  const mockPreview: MaterialPreviewResponse = {
    title: 'Character: Hero',
    markdown: '# Hero\n\nA brave warrior...',
    novel_title: 'Novel 1',
    suggested_file_type: 'character',
    suggested_folder_name: 'Characters',
    suggested_file_name: 'hero',
  }

  beforeEach(() => {
    vi.clearAllMocks()

    // Default mock implementation for useQuery
    mockUseQuery.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: null,
    })
  })

  describe('initial state', () => {
    it('initializes with correct default state', () => {
      const { result } = renderHook(() => useMaterialLibrary())

      expect(result.current.libraries).toEqual([])
      expect(result.current.isLoading).toBe(false)
      expect(result.current.error).toBe(null)
      expect(result.current.expandedNovels).toBeInstanceOf(Set)
      expect(result.current.expandedTypes).toBeInstanceOf(Map)
      expect(result.current.preview).toBe(null)
      expect(result.current.previewEntityInfo).toBe(null)
      expect(result.current.isPreviewLoading).toBe(false)
    })

    it('initializes summary query config', () => {
      renderHook(() => useMaterialLibrary())

      // Hook always initializes the summary query (cached by react-query)
      expect(mockUseQuery).toHaveBeenCalledWith(
        expect.objectContaining({
          queryKey: ['material-library-summary'],
          queryFn: expect.any(Function),
        })
      )
    })
  })

  describe('library fetching', () => {
    it('fetches library summary on mount', async () => {
      mockUseQuery.mockReturnValue({
        data: mockLibraries,
        isLoading: false,
        error: null,
      })

      renderHook(() => useMaterialLibrary())

      expect(mockUseQuery).toHaveBeenCalledWith(
        expect.objectContaining({
          queryKey: ['material-library-summary'],
          queryFn: expect.any(Function),
        })
      )
    })

    it('returns loading state correctly', () => {
      mockUseQuery.mockReturnValue({
        data: undefined,
        isLoading: true,
        error: null,
      })

      const { result } = renderHook(() => useMaterialLibrary())

      expect(result.current.isLoading).toBe(true)
    })

    it('handles fetch error', () => {
      const error = new Error('Network error')
      mockUseQuery.mockReturnValue({
        data: undefined,
        isLoading: false,
        error,
      })

      const { result } = renderHook(() => useMaterialLibrary())

      expect(result.current.error).toBe(error)
    })
  })

  describe('materials entitlement', () => {
    type QueryArgs = { queryKey: readonly unknown[]; enabled?: boolean; retry?: unknown }
    const summaryCall = () =>
      mockUseQuery.mock.calls
        .map(([args]) => args as QueryArgs)
        .find((args) => args.queryKey[0] === 'material-library-summary')

    function mockQueries(status: unknown, summary: Record<string, unknown>) {
      mockUseQuery.mockImplementation((args: QueryArgs) =>
        args.queryKey[0] === 'subscription-status'
          ? { data: status, isLoading: false, error: null }
          : { data: undefined, isLoading: false, error: null, ...summary },
      )
    }

    it('does not request the paid summary for plans without the materials library', () => {
      mockQueries({ tier: 'free', features: { materials_library_access: false } }, {})

      const { result } = renderHook(() => useMaterialLibrary())

      expect(summaryCall()?.enabled).toBe(false)
      expect(result.current.accessDenied).toBe(true)
      expect(result.current.error).toBe(null)
    })

    it('requests the summary for paid plans', () => {
      mockQueries({ tier: 'pro', features: { materials_library_access: true } }, { data: mockLibraries })

      const { result } = renderHook(() => useMaterialLibrary())

      expect(summaryCall()?.enabled).toBe(true)
      expect(result.current.accessDenied).toBe(false)
      expect(result.current.libraries).toEqual(mockLibraries)
    })

    it('treats a 402 feature-not-included response as no access, without retrying', async () => {
      const { ApiError } = await import('@/lib/apiClient')
      const featureError = new ApiError(402, 'ERR_FEATURE_NOT_INCLUDED')
      mockQueries(undefined, { error: featureError })

      const { result } = renderHook(() => useMaterialLibrary())

      expect(result.current.accessDenied).toBe(true)
      expect(result.current.error).toBe(null)
      const retry = summaryCall()?.retry as (count: number, error: unknown) => boolean
      expect(retry(0, featureError)).toBe(false)
      expect(retry(0, new Error('network'))).toBe(true)
      expect(retry(1, new Error('network'))).toBe(false)
    })
  })

  describe('toggleNovel', () => {
    it('adds novel to expanded set when not present', () => {
      const { result } = renderHook(() => useMaterialLibrary())

      act(() => {
        result.current.toggleNovel(1)
      })

      expect(result.current.expandedNovels.has(1)).toBe(true)
    })

    it('removes novel from expanded set when present', () => {
      const { result } = renderHook(() => useMaterialLibrary())

      act(() => {
        result.current.toggleNovel(1)
      })
      expect(result.current.expandedNovels.has(1)).toBe(true)

      act(() => {
        result.current.toggleNovel(1)
      })
      expect(result.current.expandedNovels.has(1)).toBe(false)
    })

    it('handles multiple novels independently', () => {
      const { result } = renderHook(() => useMaterialLibrary())

      act(() => {
        result.current.toggleNovel(1)
        result.current.toggleNovel(2)
      })

      expect(result.current.expandedNovels.has(1)).toBe(true)
      expect(result.current.expandedNovels.has(2)).toBe(true)

      act(() => {
        result.current.toggleNovel(1)
      })

      expect(result.current.expandedNovels.has(1)).toBe(false)
      expect(result.current.expandedNovels.has(2)).toBe(true)
    })
  })

  describe('toggleEntityType', () => {
    it('toggles entity type expansion for a novel', () => {
      const { result } = renderHook(() => useMaterialLibrary())

      const key = '1:characters'
      expect(result.current.expandedTypes.get(key)).toBe(undefined)

      act(() => {
        result.current.toggleEntityType(1, 'characters')
      })

      expect(result.current.expandedTypes.get(key)).toBe(true)

      act(() => {
        result.current.toggleEntityType(1, 'characters')
      })

      expect(result.current.expandedTypes.get(key)).toBe(false)
    })

    it('handles different entity types for same novel', () => {
      const { result } = renderHook(() => useMaterialLibrary())

      act(() => {
        result.current.toggleEntityType(1, 'characters')
        result.current.toggleEntityType(1, 'worldview')
      })

      expect(result.current.expandedTypes.get('1:characters')).toBe(true)
      expect(result.current.expandedTypes.get('1:worldview')).toBe(true)
    })

    it('handles same entity type for different novels', () => {
      const { result } = renderHook(() => useMaterialLibrary())

      act(() => {
        result.current.toggleEntityType(1, 'characters')
        result.current.toggleEntityType(2, 'characters')
      })

      expect(result.current.expandedTypes.get('1:characters')).toBe(true)
      expect(result.current.expandedTypes.get('2:characters')).toBe(true)
    })
  })

  describe('loadPreview', () => {
    it('loads preview for an entity', async () => {
      vi.mocked(materialsApi.materialsApi.getPreview).mockResolvedValueOnce(mockPreview)

      const { result } = renderHook(() => useMaterialLibrary())

      await act(async () => {
        await result.current.loadPreview(1, 'characters', 123)
      })

      expect(materialsApi.materialsApi.getPreview).toHaveBeenCalledWith(
        1,
        'characters',
        123
      )
      expect(result.current.preview).toEqual(mockPreview)
      expect(result.current.previewEntityInfo).toEqual({
        novelId: 1,
        entityType: 'characters',
        entityId: 123,
      })
    })

    it('sets isPreviewLoading during load', async () => {
      let resolvePreview: (value: MaterialPreviewResponse) => void
      vi.mocked(materialsApi.materialsApi.getPreview).mockImplementationOnce(
        () => new Promise((resolve) => {
          resolvePreview = resolve
        })
      )

      const { result } = renderHook(() => useMaterialLibrary())

      act(() => {
        result.current.loadPreview(1, 'characters', 123)
      })

      expect(result.current.isPreviewLoading).toBe(true)

      await act(async () => {
        resolvePreview!(mockPreview)
      })

      expect(result.current.isPreviewLoading).toBe(false)
    })

    it('handles preview load error', async () => {
      const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
      vi.mocked(materialsApi.materialsApi.getPreview).mockRejectedValueOnce(
        new Error('Preview failed')
      )

      const { result } = renderHook(() => useMaterialLibrary())

      await act(async () => {
        await result.current.loadPreview(1, 'characters', 123)
      })

      expect(result.current.isPreviewLoading).toBe(false)
      expect(result.current.preview).toBe(null)
      expect(consoleErrorSpy).toHaveBeenCalled()

      consoleErrorSpy.mockRestore()
    })

    it('clears stale preview when loading a different entity fails', async () => {
      const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
      vi.mocked(materialsApi.materialsApi.getPreview)
        .mockResolvedValueOnce(mockPreview)
        .mockRejectedValueOnce(new Error('Next preview failed'))

      const { result } = renderHook(() => useMaterialLibrary())

      await act(async () => {
        await result.current.loadPreview(1, 'characters', 123)
      })
      expect(result.current.preview).toEqual(mockPreview)
      expect(result.current.previewEntityInfo).toEqual({
        novelId: 1,
        entityType: 'characters',
        entityId: 123,
      })

      await act(async () => {
        await result.current.loadPreview(1, 'stories', 456)
      })

      expect(result.current.isPreviewLoading).toBe(false)
      expect(result.current.preview).toBe(null)
      expect(result.current.previewEntityInfo).toBe(null)
      expect(consoleErrorSpy).toHaveBeenCalled()

      consoleErrorSpy.mockRestore()
    })

    it('does not clear a newer preview when an earlier request fails out of order', async () => {
      const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
      let rejectFirst: ((err: Error) => void) | undefined
      vi.mocked(materialsApi.materialsApi.getPreview)
        .mockImplementationOnce(
          () =>
            new Promise<MaterialPreviewResponse>((_resolve, reject) => {
              rejectFirst = reject
            })
        )
        .mockResolvedValueOnce(mockPreview)

      const { result } = renderHook(() => useMaterialLibrary())

      // Kick off a slow first request that will fail later (do not await it).
      let firstCall: Promise<void> | undefined
      act(() => {
        firstCall = result.current.loadPreview(1, 'characters', 123)
      })

      // A later request resolves first and populates the preview.
      await act(async () => {
        await result.current.loadPreview(1, 'stories', 456)
      })
      expect(result.current.preview).toEqual(mockPreview)
      expect(result.current.previewEntityInfo).toEqual({
        novelId: 1,
        entityType: 'stories',
        entityId: 456,
      })

      // The earlier request now fails — it must NOT clobber the newer preview.
      await act(async () => {
        rejectFirst!(new Error('Stale preview failed'))
        await firstCall!
      })

      expect(result.current.preview).toEqual(mockPreview)
      expect(result.current.previewEntityInfo).toEqual({
        novelId: 1,
        entityType: 'stories',
        entityId: 456,
      })

      consoleErrorSpy.mockRestore()
    })
  })

  describe('clearPreview', () => {
    it('clears preview state', async () => {
      vi.mocked(materialsApi.materialsApi.getPreview).mockResolvedValueOnce(mockPreview)

      const { result } = renderHook(() => useMaterialLibrary())

      await act(async () => {
        await result.current.loadPreview(1, 'characters', 123)
      })

      expect(result.current.preview).not.toBe(null)

      act(() => {
        result.current.clearPreview()
      })

      expect(result.current.preview).toBe(null)
      expect(result.current.previewEntityInfo).toBe(null)
    })

    it('invalidates a pending preview response and clears loading', async () => {
      let resolvePreview: (value: MaterialPreviewResponse) => void
      vi.mocked(materialsApi.materialsApi.getPreview).mockImplementationOnce(
        () => new Promise((resolve) => {
          resolvePreview = resolve
        })
      )
      const { result } = renderHook(() => useMaterialLibrary())

      let pending: Promise<void>
      act(() => {
        pending = result.current.loadPreview(1, 'characters', 123)
      })
      expect(result.current.isPreviewLoading).toBe(true)

      act(() => {
        result.current.clearPreview()
      })
      expect(result.current.isPreviewLoading).toBe(false)

      await act(async () => {
        resolvePreview!(mockPreview)
        await pending!
      })
      expect(result.current.preview).toBe(null)
      expect(result.current.previewEntityInfo).toBe(null)
      expect(result.current.isPreviewLoading).toBe(false)
    })

    it('ignores a pending preview rejection after clear', async () => {
      const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
      let rejectPreview: (error: Error) => void
      vi.mocked(materialsApi.materialsApi.getPreview).mockImplementationOnce(
        () => new Promise((_resolve, reject) => {
          rejectPreview = reject
        })
      )
      const { result } = renderHook(() => useMaterialLibrary())

      let pending: Promise<void>
      act(() => {
        pending = result.current.loadPreview(1, 'characters', 123)
      })
      act(() => {
        result.current.clearPreview()
      })

      await act(async () => {
        rejectPreview!(new Error('stale failure'))
        await pending!
      })
      expect(result.current.preview).toBe(null)
      expect(result.current.previewEntityInfo).toBe(null)
      expect(result.current.isPreviewLoading).toBe(false)
      consoleErrorSpy.mockRestore()
    })
  })

  describe('query configuration', () => {
    it('uses correct stale time', () => {
      renderHook(() => useMaterialLibrary())

      expect(mockUseQuery).toHaveBeenCalledWith(
        expect.objectContaining({
          staleTime: 5 * 60 * 1000, // 5 minutes
        })
      )
    })

    it('uses correct query key', () => {
      renderHook(() => useMaterialLibrary())

      expect(mockUseQuery).toHaveBeenCalledWith(
        expect.objectContaining({
          queryKey: ['material-library-summary'],
        })
      )
    })
  })

  describe('state updates', () => {
    it('returns libraries from query data', () => {
      mockUseQuery.mockReturnValue({
        data: mockLibraries,
        isLoading: false,
        error: null,
      })

      const { result } = renderHook(() => useMaterialLibrary())

      expect(result.current.libraries).toEqual(mockLibraries)
    })

    it('returns empty array when no data', () => {
      mockUseQuery.mockReturnValue({
        data: undefined,
        isLoading: false,
        error: null,
      })

      const { result } = renderHook(() => useMaterialLibrary())

      expect(result.current.libraries).toEqual([])
    })
  })

  describe('return values', () => {
    it('returns all expected properties', () => {
      const { result } = renderHook(() => useMaterialLibrary())

      expect(result.current).toHaveProperty('libraries')
      expect(result.current).toHaveProperty('isLoading')
      expect(result.current).toHaveProperty('error')
      expect(result.current).toHaveProperty('expandedNovels')
      expect(result.current).toHaveProperty('expandedTypes')
      expect(result.current).toHaveProperty('toggleNovel')
      expect(result.current).toHaveProperty('toggleEntityType')
      expect(result.current).toHaveProperty('preview')
      expect(result.current).toHaveProperty('previewEntityInfo')
      expect(result.current).toHaveProperty('isPreviewLoading')
      expect(result.current).toHaveProperty('loadPreview')
      expect(result.current).toHaveProperty('clearPreview')
    })
  })
})
