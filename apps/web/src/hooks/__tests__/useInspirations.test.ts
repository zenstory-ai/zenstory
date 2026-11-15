import { renderHook, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { useInspirations, useFeaturedInspirations } from '../useInspirations'
import * as api from '@/lib/api'
import type {
  Inspiration,
  InspirationDetail,
  CopyInspirationResponse,
} from '@/types'

// Mock inspirationsApi
vi.mock('@/lib/api', () => ({
  inspirationsApi: {
    list: vi.fn(),
    getFeatured: vi.fn(),
    get: vi.fn(),
    copy: vi.fn(),
    submit: vi.fn(),
    getMySubmissions: vi.fn(),
  },
}))

// Mock @tanstack/react-query
const mockUseQuery = vi.fn()
const mockUseMutation = vi.fn()
const mockInvalidateQueries = vi.fn()

vi.mock('@tanstack/react-query', () => ({
  useQuery: (args: unknown) => mockUseQuery(args),
  useMutation: (args: unknown) => mockUseMutation(args),
  useQueryClient: () => ({
    invalidateQueries: mockInvalidateQueries,
  }),
}))

// Helper to create mock inspiration
function createMockInspiration(overrides: Partial<Inspiration> = {}): Inspiration {
  return {
    id: 'test-id',
    name: 'Test Inspiration',
    description: 'Test description',
    cover_image: null,
    project_type: 'novel',
    tags: ['fantasy', 'adventure'],
    source: 'official',
    author_id: null,
    original_project_id: null,
    copy_count: 10,
    is_featured: false,
    created_at: '2024-01-01T00:00:00Z',
    ...overrides,
  }
}

// Helper to create mock inspiration detail
function createMockInspirationDetail(
  overrides: Partial<InspirationDetail> = {}
): InspirationDetail {
  return {
    ...createMockInspiration(overrides),
    file_preview: [
      { title: 'Chapter 1', file_type: 'draft', has_content: true },
      { title: 'Outline', file_type: 'outline', has_content: true },
    ],
    ...overrides,
  }
}

// Helper to setup default mocks
function setupDefaultMocks() {
  mockUseQuery.mockImplementation((options: { queryKey: string[] }) => {
    const queryKey = options.queryKey
    if (queryKey[0] === 'inspirations') {
      return {
        data: {
          inspirations: [],
          total: 0,
          page: 1,
          page_size: 12,
        },
        isLoading: false,
        error: null,
        refetch: vi.fn(),
      }
    }
    if (queryKey[0] === 'inspirations-featured') {
      return {
        data: [],
        isLoading: false,
        error: null,
      }
    }
    return {
      data: undefined,
      isLoading: false,
      error: null,
    }
  })

  mockUseMutation.mockImplementation(() => ({
    mutateAsync: vi.fn(),
    isPending: false,
  }))
}

describe('useInspirations', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.spyOn(console, 'error').mockImplementation(() => {})
    setupDefaultMocks()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  describe('initial state', () => {
    it('initializes with empty inspirations array', () => {
      const { result } = renderHook(() => useInspirations())

      expect(result.current.inspirations).toEqual([])
      expect(result.current.total).toBe(0)
      expect(result.current.page).toBe(1)
      expect(result.current.pageSize).toBe(12)
    })

    it('initializes with loading state from useQuery', () => {
      mockUseQuery.mockImplementation(() => ({
        data: undefined,
        isLoading: true,
        error: null,
        refetch: vi.fn(),
      }))

      const { result } = renderHook(() => useInspirations())

      expect(result.current.isLoading).toBe(true)
    })

    it('accepts custom options and passes them to API', () => {
      renderHook(() =>
        useInspirations({
          projectType: 'novel',
          search: 'fantasy',
          tags: 'adventure',
          page: 2,
          pageSize: 24,
        })
      )

      expect(mockUseQuery).toHaveBeenCalled()
      const callArgs = mockUseQuery.mock.calls.find(
        (call) => call[0]?.queryKey?.[0] === 'inspirations'
      )
      expect(callArgs).toBeDefined()
    })

    it('respects enabled option', () => {
      renderHook(() => useInspirations({ enabled: false }))

      const listCall = mockUseQuery.mock.calls.find(
        (call) => call[0]?.queryKey?.[0] === 'inspirations'
      )
      expect(listCall?.[0]?.enabled).toBe(false)
    })
  })

  describe('fetching inspirations list', () => {
    it('returns inspirations from query data', () => {
      const mockInspirations = [
        createMockInspiration({ id: '1', name: 'Fantasy Novel' }),
        createMockInspiration({ id: '2', name: 'Sci-Fi Story' }),
      ]

      mockUseQuery.mockImplementation((options: { queryKey: string[] }) => {
        if (options.queryKey[0] === 'inspirations') {
          return {
            data: {
              inspirations: mockInspirations,
              total: 2,
              page: 1,
              page_size: 12,
            },
            isLoading: false,
            error: null,
            refetch: vi.fn(),
          }
        }
        return {
          data: [],
          isLoading: false,
          error: null,
        }
      })

      const { result } = renderHook(() => useInspirations())

      expect(result.current.inspirations).toEqual(mockInspirations)
      expect(result.current.total).toBe(2)
      expect(result.current.page).toBe(1)
      expect(result.current.pageSize).toBe(12)
    })

    it('sets error state from query error', () => {
      const mockError = new Error('Network error')

      mockUseQuery.mockImplementation((options: { queryKey: string[] }) => {
        if (options.queryKey[0] === 'inspirations') {
          return {
            data: undefined,
            isLoading: false,
            error: mockError,
            refetch: vi.fn(),
          }
        }
        return {
          data: [],
          isLoading: false,
          error: null,
        }
      })

      const { result } = renderHook(() => useInspirations())

      expect(result.current.error).toBe(mockError)
      expect(result.current.inspirations).toEqual([])
    })
  })

  describe('search functionality', () => {
    it('passes search query to queryKey', () => {
      renderHook(() => useInspirations({ search: 'fantasy' }))

      const listCall = mockUseQuery.mock.calls.find(
        (call) => call[0]?.queryKey?.[0] === 'inspirations'
      )
      expect(listCall?.[0]?.queryKey).toContain('fantasy')
    })

    it('passes project type to queryKey', () => {
      renderHook(() => useInspirations({ projectType: 'novel' }))

      const listCall = mockUseQuery.mock.calls.find(
        (call) => call[0]?.queryKey?.[0] === 'inspirations'
      )
      expect(listCall?.[0]?.queryKey).toContain('novel')
    })

    it('passes tags to queryKey', () => {
      renderHook(() => useInspirations({ tags: 'fantasy,adventure' }))

      const listCall = mockUseQuery.mock.calls.find(
        (call) => call[0]?.queryKey?.[0] === 'inspirations'
      )
      expect(listCall?.[0]?.queryKey).toContain('fantasy,adventure')
    })

    it('passes featuredOnly to queryKey', () => {
      renderHook(() => useInspirations({ featuredOnly: true }))

      const listCall = mockUseQuery.mock.calls.find(
        (call) => call[0]?.queryKey?.[0] === 'inspirations'
      )
      expect(listCall?.[0]?.queryKey).toContain(true)
    })
  })

  describe('pagination functionality', () => {
    it('passes page number to queryKey', () => {
      renderHook(() => useInspirations({ page: 3 }))

      const listCall = mockUseQuery.mock.calls.find(
        (call) => call[0]?.queryKey?.[0] === 'inspirations'
      )
      expect(listCall?.[0]?.queryKey).toContain(3)
    })

    it('passes page size to queryKey', () => {
      renderHook(() => useInspirations({ pageSize: 24 }))

      const listCall = mockUseQuery.mock.calls.find(
        (call) => call[0]?.queryKey?.[0] === 'inspirations'
      )
      expect(listCall?.[0]?.queryKey).toContain(24)
    })

    it('returns page info from query data', () => {
      mockUseQuery.mockImplementation((options: { queryKey: string[] }) => {
        if (options.queryKey[0] === 'inspirations') {
          return {
            data: {
              inspirations: [],
              total: 100,
              page: 5,
              page_size: 24,
            },
            isLoading: false,
            error: null,
            refetch: vi.fn(),
          }
        }
        return {
          data: [],
          isLoading: false,
          error: null,
        }
      })

      const { result } = renderHook(() => useInspirations({ page: 5, pageSize: 24 }))

      expect(result.current.page).toBe(5)
      expect(result.current.total).toBe(100)
      expect(result.current.pageSize).toBe(24)
    })
  })

  describe('featured inspirations separation', () => {
    it('does not fetch featured inspirations inside useInspirations', () => {
      renderHook(() => useInspirations())

      const featuredCall = mockUseQuery.mock.calls.find(
        (call) => call[0]?.queryKey?.[0] === 'inspirations-featured'
      )
      expect(featuredCall).toBeUndefined()
    })
  })

  describe('getDetail functionality', () => {
    it('fetches inspiration detail via API', async () => {
      const mockDetail = createMockInspirationDetail({ id: 'detail-1' })
      vi.mocked(api.inspirationsApi.get).mockResolvedValue(mockDetail)

      const { result } = renderHook(() => useInspirations())

      expect(result.current.currentDetail).toBeNull()
      expect(result.current.isDetailLoading).toBe(false)

      let detail: InspirationDetail | null = null
      await act(async () => {
        detail = await result.current.getDetail('detail-1')
      })

      expect(api.inspirationsApi.get).toHaveBeenCalledWith('detail-1')
      expect(detail).toEqual(mockDetail)
      expect(result.current.currentDetail).toEqual(mockDetail)
      expect(result.current.isDetailLoading).toBe(false)
    })

    it('sets loading state during detail fetch', async () => {
      let resolveDetail: (value: InspirationDetail) => void
      vi.mocked(api.inspirationsApi.get).mockImplementation(
        () =>
          new Promise((resolve) => {
            resolveDetail = resolve
          })
      )

      const { result } = renderHook(() => useInspirations())

      expect(result.current.isDetailLoading).toBe(false)

      // Start the fetch but don't await yet
      let fetchPromise: Promise<InspirationDetail | null>
      act(() => {
        fetchPromise = result.current.getDetail('detail-1')
      })

      // Allow React to process state updates
      await act(async () => {
        // Give React time to process state updates
        await new Promise((resolve) => setTimeout(resolve, 0))
      })

      // Check loading state during fetch
      expect(result.current.isDetailLoading).toBe(true)

      // Resolve the promise
      await act(async () => {
        resolveDetail!(createMockInspirationDetail())
        await fetchPromise!
      })

      expect(result.current.isDetailLoading).toBe(false)
    })

    it('returns null on detail fetch error', async () => {
      vi.mocked(api.inspirationsApi.get).mockRejectedValue(new Error('Not found'))

      const { result } = renderHook(() => useInspirations())

      let detail: InspirationDetail | null = null
      await act(async () => {
        detail = await result.current.getDetail('invalid-id')
      })

      expect(detail).toBeNull()
      expect(result.current.currentDetail).toBeNull()
      expect(result.current.isDetailLoading).toBe(false)
    })

    it('keeps a newer detail when an older request resolves later', async () => {
      let resolveOld!: (value: InspirationDetail) => void
      vi.mocked(api.inspirationsApi.get)
        .mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve }))
        .mockResolvedValueOnce(createMockInspirationDetail({ id: 'new-detail', name: 'New detail' }))
      const { result } = renderHook(() => useInspirations())

      let oldRequest!: Promise<InspirationDetail | null>
      act(() => { oldRequest = result.current.getDetail('old-detail') })
      await act(async () => { await result.current.getDetail('new-detail') })
      expect(result.current.currentDetail?.id).toBe('new-detail')

      await act(async () => {
        resolveOld(createMockInspirationDetail({ id: 'old-detail', name: 'Old detail' }))
        await oldRequest
      })
      expect(result.current.currentDetail?.id).toBe('new-detail')
    })

    it('resets detail state', async () => {
      const mockDetail = createMockInspirationDetail({ id: 'detail-1' })
      vi.mocked(api.inspirationsApi.get).mockResolvedValue(mockDetail)

      const { result } = renderHook(() => useInspirations())

      await act(async () => {
        await result.current.getDetail('detail-1')
      })

      expect(result.current.currentDetail).toEqual(mockDetail)

      act(() => {
        result.current.resetDetail()
      })

      expect(result.current.currentDetail).toBeNull()
    })
  })

  describe('copy inspiration functionality', () => {
    it('copies inspiration successfully', async () => {
      const mockResponse: CopyInspirationResponse = {
        success: true,
        message: 'Copied successfully',
        project_id: 'new-project-1',
        project_name: 'My Copy',
      }

      const mockMutateAsync = vi.fn().mockResolvedValue(mockResponse)
      mockUseMutation.mockImplementation(() => ({
        mutateAsync: mockMutateAsync,
        isPending: false,
      }))

      const { result } = renderHook(() => useInspirations())

      let copyResult: CopyInspirationResponse | undefined
      await act(async () => {
        copyResult = await result.current.copyInspiration('inspiration-1', 'My Copy')
      })

      expect(mockMutateAsync).toHaveBeenCalledWith({
        id: 'inspiration-1',
        projectName: 'My Copy',
      })
      expect(copyResult).toEqual(mockResponse)
    })

    it('tracks copying state', () => {
      mockUseMutation.mockImplementation(() => ({
        mutateAsync: vi.fn(),
        isPending: true,
      }))

      const { result } = renderHook(() => useInspirations())

      expect(result.current.isCopying).toBe(true)
    })

    it('copies without project name', async () => {
      const mockResponse: CopyInspirationResponse = {
        success: true,
        message: 'Copied',
        project_id: '1',
        project_name: null,
      }

      const mockMutateAsync = vi.fn().mockResolvedValue(mockResponse)
      mockUseMutation.mockImplementation(() => ({
        mutateAsync: mockMutateAsync,
        isPending: false,
      }))

      const { result } = renderHook(() => useInspirations())

      await act(async () => {
        await result.current.copyInspiration('inspiration-1')
      })

      expect(mockMutateAsync).toHaveBeenCalledWith({
        id: 'inspiration-1',
        projectName: undefined,
      })
    })

    it('invalidates queries on successful copy', async () => {
      const mockResponse: CopyInspirationResponse = {
        success: true,
        message: 'Copied',
        project_id: '1',
        project_name: 'Test',
      }

      const copyMutateAsync = vi.fn().mockResolvedValue(mockResponse)
      let copyMutationOptions: { onSuccess?: () => void } | undefined

      mockUseMutation.mockImplementationOnce((options) => {
        copyMutationOptions = options
        return {
          mutateAsync: copyMutateAsync,
          isPending: false,
        }
      })

      const { result } = renderHook(() => useInspirations())

      await act(async () => {
        await result.current.copyInspiration('inspiration-1')
      })

      // Call onSuccess callback
      if (copyMutationOptions?.onSuccess) {
        copyMutationOptions.onSuccess()
      }

      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['projects'] })
      expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ['inspirations'] })
    })

    it('throws error on copy failure', async () => {
      const mockError = new Error('Copy failed')
      const mockMutateAsync = vi.fn().mockRejectedValue(mockError)
      mockUseMutation.mockImplementation(() => ({
        mutateAsync: mockMutateAsync,
        isPending: false,
      }))

      const { result } = renderHook(() => useInspirations())

      await expect(
        act(async () => {
          await result.current.copyInspiration('inspiration-1')
        })
      ).rejects.toThrow('Copy failed')
    })
  })

  describe('refetch functionality', () => {
    it('calls refetch from useQuery', async () => {
      const mockRefetch = vi.fn()
      mockUseQuery.mockImplementation((options: { queryKey: string[] }) => {
        if (options.queryKey[0] === 'inspirations') {
          return {
            data: { inspirations: [], total: 0, page: 1, page_size: 12 },
            isLoading: false,
            error: null,
            refetch: mockRefetch,
          }
        }
        return {
          data: [],
          isLoading: false,
          error: null,
        }
      })

      const { result } = renderHook(() => useInspirations())

      await act(async () => {
        await result.current.refetch()
      })

      expect(mockRefetch).toHaveBeenCalled()
    })
  })

  describe('loading state management', () => {
    it('tracks list loading state correctly', () => {
      mockUseQuery.mockImplementation((options: { queryKey: string[] }) => {
        if (options.queryKey[0] === 'inspirations') {
          return {
            data: undefined,
            isLoading: true,
            error: null,
            refetch: vi.fn(),
          }
        }
        return {
          data: [],
          isLoading: false,
          error: null,
        }
      })

      const { result } = renderHook(() => useInspirations())

      expect(result.current.isLoading).toBe(true)
    })

    it('returns false for loading when query is not loading', () => {
      mockUseQuery.mockImplementation((options: { queryKey: string[] }) => {
        if (options.queryKey[0] === 'inspirations') {
          return {
            data: { inspirations: [], total: 0, page: 1, page_size: 12 },
            isLoading: false,
            error: null,
            refetch: vi.fn(),
          }
        }
        return {
          data: [],
          isLoading: false,
          error: null,
        }
      })

      const { result } = renderHook(() => useInspirations())

      expect(result.current.isLoading).toBe(false)
    })
  })

  describe('error handling', () => {
    it('stores error from list fetch', () => {
      const mockError = new Error('Failed to fetch')
      mockUseQuery.mockImplementation((options: { queryKey: string[] }) => {
        if (options.queryKey[0] === 'inspirations') {
          return {
            data: undefined,
            isLoading: false,
            error: mockError,
            refetch: vi.fn(),
          }
        }
        return {
          data: [],
          isLoading: false,
          error: null,
        }
      })

      const { result } = renderHook(() => useInspirations())

      expect(result.current.error).toBe(mockError)
      expect(result.current.inspirations).toEqual([])
    })

    it('returns null error when no error', () => {
      mockUseQuery.mockImplementation((options: { queryKey: string[] }) => {
        if (options.queryKey[0] === 'inspirations') {
          return {
            data: { inspirations: [], total: 0, page: 1, page_size: 12 },
            isLoading: false,
            error: null,
            refetch: vi.fn(),
          }
        }
        return {
          data: [],
          isLoading: false,
          error: null,
        }
      })

      const { result } = renderHook(() => useInspirations())

      expect(result.current.error).toBeNull()
    })
  })
})

describe('useFeaturedInspirations', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('fetches featured inspirations with default limit', () => {
    const mockFeatured = [
      createMockInspiration({ id: '1', is_featured: true }),
      createMockInspiration({ id: '2', is_featured: true }),
    ]

    mockUseQuery.mockImplementation(() => ({
      data: mockFeatured,
      isLoading: false,
      error: null,
    }))

    const { result } = renderHook(() => useFeaturedInspirations())

    expect(result.current.featured).toEqual(mockFeatured)
  })

  it('fetches featured inspirations with custom limit', () => {
    mockUseQuery.mockImplementation((options: { queryKey: (string | number)[] }) => {
      expect(options.queryKey).toContain(10)
      return {
        data: [],
        isLoading: false,
        error: null,
      }
    })

    renderHook(() => useFeaturedInspirations(10))
  })

  it('returns empty array when no data', () => {
    mockUseQuery.mockImplementation(() => ({
      data: undefined,
      isLoading: false,
      error: null,
    }))

    const { result } = renderHook(() => useFeaturedInspirations())

    expect(result.current.featured).toEqual([])
  })

  it('tracks loading state', () => {
    mockUseQuery.mockImplementation(() => ({
      data: undefined,
      isLoading: true,
      error: null,
    }))

    const { result } = renderHook(() => useFeaturedInspirations())

    expect(result.current.isLoading).toBe(true)
  })

  it('tracks error state', () => {
    const mockError = new Error('Failed to fetch')
    mockUseQuery.mockImplementation(() => ({
      data: undefined,
      isLoading: false,
      error: mockError,
    }))

    const { result } = renderHook(() => useFeaturedInspirations())

    expect(result.current.error).toBe(mockError)
  })
})
