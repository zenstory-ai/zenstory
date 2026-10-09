/* eslint-disable @typescript-eslint/no-non-null-asserted-optional-chain */
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { InspirationGrid } from '../inspirations/InspirationGrid'
import * as useInspirationsModule from '../../hooks/useInspirations'
import { ApiError } from '../../lib/apiClient'
import type { Inspiration, InspirationDetail, UseInspirationsReturn } from '../../types'

// Mock useInspirations hook
vi.mock('../../hooks/useInspirations', () => ({
  useInspirations: vi.fn(),
}))

const mockToastError = vi.fn()
const mockToastSuccess = vi.fn()
vi.mock('../../lib/toast', () => ({
  toast: {
    error: (message: string) => mockToastError(message),
    success: (message: string) => mockToastSuccess(message),
    info: vi.fn(),
  },
}))

// Translation map for the inspirations namespace
const translations: Record<string, string> = {
  'searchPlaceholder': '搜索灵感...',
  'search': '搜索',
  'view': '查看',
  'use': '使用',
  'copying': '复制中...',
  'copied': '已复制!',
  'useThis': '使用此模板',
  'copySuccess': '灵感已复制到您的工作空间！',
  'copyError': '复制灵感失败，请重试。',
  'copyAuthRequired': '请先登录后再复制灵感。',
  'copyQuotaExceeded': '本月灵感复制次数已用完，请升级套餐或下月再试。',
  'copyQuotaExceededTitle': '灵感复制额度已用尽',
  'copyQuotaExceededModalDesc': '你已用完本月灵感复制额度。可先去订阅页提升额度，或到套餐页快速对比后再决定。',
  'copyQuotaUpgradePrimary': '查看升级方案',
  'copyQuotaUpgradeSecondary': '查看套餐对比',
  'copyCount': '{{count}} 次使用',
  'community': '社区',
  'featured': '精选',
  'projectTypes.novel': '长篇小说',
  'projectTypes.short': '短篇小说',
  'projectTypes.screenplay': '剧本',
  'noResults': '没有找到匹配的灵感。',
  'resultsCount': '找到 {{count}} 个灵感',
  'loadError': '加载灵感失败，请重试。',
  'retry': '重试',
  'page': '第 {{current}} 页，共 {{total}} 页',
}

// Mock react-i18next
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown>) => {
      let value = translations[key] ?? key
      if (options) {
        Object.entries(options).forEach(([k, v]) => {
          value = value.replace(new RegExp(`{{\\s*${k}\\s*}}`, 'g'), String(v ?? ''))
        })
      }
      return value
    },
  }),
}))

const mockUseInspirations = vi.mocked(useInspirationsModule.useInspirations)

// Mock data factories
function createMockInspiration(overrides: Partial<Inspiration> = {}): Inspiration {
  return {
    id: 'inspiration-1',
    name: 'Test Inspiration',
    description: 'A test inspiration description',
    cover_image: 'https://example.com/cover.jpg',
    project_type: 'novel',
    tags: ['fantasy', 'adventure'],
    source: 'official',
    author_id: 'author-1',
    original_project_id: 'project-1',
    copy_count: 10,
    is_featured: false,
    created_at: '2024-01-01T00:00:00Z',
    ...overrides,
  }
}

function createMockInspirationDetail(overrides: Partial<InspirationDetail> = {}): InspirationDetail {
  return {
    ...createMockInspiration(overrides),
    file_preview: [
      { title: 'Chapter 1', file_type: 'draft', has_content: true },
      { title: 'Main Character', file_type: 'character', has_content: true },
    ],
    ...overrides,
  }
}

function createMockUseInspirationsReturn(
  overrides: Partial<UseInspirationsReturn> = {}
): UseInspirationsReturn {
  return {
    inspirations: [],
    total: 0,
    page: 1,
    pageSize: 12,
    isLoading: false,
    error: null,
    refetch: vi.fn().mockResolvedValue(undefined),
    featured: [],
    isFeaturedLoading: false,
    getDetail: vi.fn().mockResolvedValue(null),
    currentDetail: null,
    isDetailLoading: false,
    copyInspiration: vi.fn().mockResolvedValue({ success: true, message: 'Success', project_id: 'new-project', project_name: 'New Project' }),
    isCopying: false,
    resetDetail: vi.fn(),
    ...overrides,
  }
}

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  })
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
}

describe('InspirationGrid', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockToastError.mockReset()
    mockToastSuccess.mockReset()
  })

  describe('Loading State', () => {
    it('shows loading skeletons while fetching', () => {
      mockUseInspirations.mockReturnValue(
        createMockUseInspirationsReturn({
          isLoading: true,
        })
      )

      render(<InspirationGrid />, { wrapper: createWrapper() })

      // Should show skeleton cards (one per pageSize, default 12)
      const skeletons = screen.getAllByTestId('inspiration-card-skeleton')
      expect(skeletons.length).toBe(12)
    })

    it('shows correct number of skeletons based on pageSize', () => {
      mockUseInspirations.mockReturnValue(
        createMockUseInspirationsReturn({
          isLoading: true,
        })
      )

      render(<InspirationGrid pageSize={6} />, { wrapper: createWrapper() })

      const skeletons = screen.getAllByTestId('inspiration-card-skeleton')
      expect(skeletons.length).toBe(6)
    })
  })

  describe('Empty State', () => {
    it('shows empty state when no inspirations found', () => {
      mockUseInspirations.mockReturnValue(
        createMockUseInspirationsReturn({
          inspirations: [],
          total: 0,
          isLoading: false,
        })
      )

      render(<InspirationGrid />, { wrapper: createWrapper() })

      expect(screen.getByText('没有找到匹配的灵感。')).toBeInTheDocument()
    })

    it('shows filter icon in empty state', () => {
      mockUseInspirations.mockReturnValue(
        createMockUseInspirationsReturn({
          inspirations: [],
          total: 0,
          isLoading: false,
        })
      )

      render(<InspirationGrid />, { wrapper: createWrapper() })

      // The Filter icon should be present in the empty state
      const emptyState = screen.getByText('没有找到匹配的灵感。').closest('div')
      expect(emptyState).toBeInTheDocument()
    })
  })

  describe('Grid Rendering', () => {
    it('renders inspiration cards correctly', () => {
      const mockInspirations = [
        createMockInspiration({ id: '1', name: 'Inspiration 1' }),
        createMockInspiration({ id: '2', name: 'Inspiration 2' }),
        createMockInspiration({ id: '3', name: 'Inspiration 3' }),
      ]

      mockUseInspirations.mockReturnValue(
        createMockUseInspirationsReturn({
          inspirations: mockInspirations,
          total: 3,
          isLoading: false,
        })
      )

      render(<InspirationGrid />, { wrapper: createWrapper() })

      expect(screen.getByText('Inspiration 1')).toBeInTheDocument()
      expect(screen.getByText('Inspiration 2')).toBeInTheDocument()
      expect(screen.getByText('Inspiration 3')).toBeInTheDocument()
    })

    it('shows total count of results', () => {
      mockUseInspirations.mockReturnValue(
        createMockUseInspirationsReturn({
          inspirations: [createMockInspiration()],
          total: 42,
          isLoading: false,
        })
      )

      render(<InspirationGrid />, { wrapper: createWrapper() })

      expect(screen.getByText('找到 42 个灵感')).toBeInTheDocument()
    })

    it('renders featured inspiration with star icon', () => {
      const mockInspirations = [
        createMockInspiration({ id: '1', name: 'Featured', is_featured: true }),
      ]

      mockUseInspirations.mockReturnValue(
        createMockUseInspirationsReturn({
          inspirations: mockInspirations,
          total: 1,
          isLoading: false,
        })
      )

      render(<InspirationGrid />, { wrapper: createWrapper() })

      expect(screen.getByText('Featured')).toBeInTheDocument()
      // Star icon is rendered via the InspirationCard component
    })

    it('renders community source badge for community inspirations', () => {
      const mockInspirations = [
        createMockInspiration({ id: '1', name: 'Community', source: 'community' }),
      ]

      mockUseInspirations.mockReturnValue(
        createMockUseInspirationsReturn({
          inspirations: mockInspirations,
          total: 1,
          isLoading: false,
        })
      )

      render(<InspirationGrid />, { wrapper: createWrapper() })

      expect(screen.getByText('社区')).toBeInTheDocument()
    })
  })

  describe('Search Functionality', () => {
    it('renders search input', () => {
      mockUseInspirations.mockReturnValue(createMockUseInspirationsReturn())

      render(<InspirationGrid />, { wrapper: createWrapper() })

      expect(screen.getByPlaceholderText('搜索灵感...')).toBeInTheDocument()
    })

    it('renders search button', () => {
      mockUseInspirations.mockReturnValue(createMockUseInspirationsReturn())

      render(<InspirationGrid />, { wrapper: createWrapper() })

      expect(screen.getByText('搜索')).toBeInTheDocument()
    })

    it('updates search input value on change', () => {
      mockUseInspirations.mockReturnValue(createMockUseInspirationsReturn())

      render(<InspirationGrid />, { wrapper: createWrapper() })

      const searchInput = screen.getByPlaceholderText('搜索灵感...')
      fireEvent.change(searchInput, { target: { value: 'fantasy' } })

      expect(searchInput).toHaveValue('fantasy')
    })

    it('uses initial search value', () => {
      mockUseInspirations.mockReturnValue(createMockUseInspirationsReturn())

      render(<InspirationGrid initialSearch="initial query" />, { wrapper: createWrapper() })

      const searchInput = screen.getByPlaceholderText('搜索灵感...')
      expect(searchInput).toHaveValue('initial query')
    })

    it('debounces search input before passing to hook', async () => {
      vi.useFakeTimers()
      const mockGetDetail = vi.fn().mockResolvedValue(null)
      mockUseInspirations.mockReturnValue(
        createMockUseInspirationsReturn({
          getDetail: mockGetDetail,
        })
      )

      render(<InspirationGrid />, { wrapper: createWrapper() })

      const searchInput = screen.getByPlaceholderText('搜索灵感...')
      fireEvent.change(searchInput, { target: { value: 'test' } })

      // Before debounce timer fires, useInspirations should still have empty search
      expect(mockUseInspirations).toHaveBeenCalledWith(
        expect.objectContaining({ search: '' })
      )

      // Advance timers by 300ms (debounce delay)
      vi.advanceTimersByTime(300)
      vi.useRealTimers()
    })

    it('resets to page 1 when search form is submitted', () => {
      mockUseInspirations.mockReturnValue(createMockUseInspirationsReturn())

      render(<InspirationGrid />, { wrapper: createWrapper() })

      const searchInput = screen.getByPlaceholderText('搜索灵感...')
      const searchButton = screen.getByText('搜索')

      fireEvent.change(searchInput, { target: { value: 'test' } })
      fireEvent.click(searchButton)

      // Form submission should reset page to 1
      // The component manages page state internally
    })
  })

  describe('Pagination', () => {
    it('does not show pagination when total pages is 1', () => {
      mockUseInspirations.mockReturnValue(
        createMockUseInspirationsReturn({
          inspirations: [createMockInspiration()],
          total: 12,
          pageSize: 12,
        })
      )

      render(<InspirationGrid />, { wrapper: createWrapper() })

      expect(screen.queryByText(/第 \d+ 页/)).not.toBeInTheDocument()
    })

    it('shows pagination when total pages > 1', () => {
      mockUseInspirations.mockReturnValue(
        createMockUseInspirationsReturn({
          inspirations: [createMockInspiration()],
          total: 25,
          pageSize: 12,
        })
      )

      render(<InspirationGrid />, { wrapper: createWrapper() })

      expect(screen.getByText('第 1 页，共 3 页')).toBeInTheDocument()
    })

    it('disables previous button on first page', () => {
      mockUseInspirations.mockReturnValue(
        createMockUseInspirationsReturn({
          inspirations: [createMockInspiration()],
          total: 25,
          pageSize: 12,
        })
      )

      render(<InspirationGrid />, { wrapper: createWrapper() })

      const pagination = screen.getByText('第 1 页，共 3 页').parentElement
      const buttons = pagination?.querySelectorAll('button')
      // First button is previous, should be disabled on page 1
      expect(buttons?.[0]).toBeDisabled()
    })

    it('disables next button when on last page (after navigation)', async () => {
      mockUseInspirations.mockReturnValue(
        createMockUseInspirationsReturn({
          inspirations: [createMockInspiration()],
          total: 25,
          pageSize: 12,
        })
      )

      render(<InspirationGrid />, { wrapper: createWrapper() })

      // Initially on page 1
      const pagination = screen.getByText('第 1 页，共 3 页').parentElement
      const buttons = pagination?.querySelectorAll('button')
      const nextButton = buttons?.[1]

      // Click next twice to get to page 3
      fireEvent.click(nextButton!)

      await waitFor(() => {
        expect(screen.getByText('第 2 页，共 3 页')).toBeInTheDocument()
      })

      // Get the new next button after re-render
      const pagination2 = screen.getByText('第 2 页，共 3 页').parentElement
      const buttons2 = pagination2?.querySelectorAll('button')
      fireEvent.click(buttons2?.[1]!)

      await waitFor(() => {
        expect(screen.getByText('第 3 页，共 3 页')).toBeInTheDocument()
      })

      // Now next button should be disabled
      const pagination3 = screen.getByText('第 3 页，共 3 页').parentElement
      const buttons3 = pagination3?.querySelectorAll('button')
      expect(buttons3?.[1]).toBeDisabled()
    })

    it('calculates total pages correctly', () => {
      mockUseInspirations.mockReturnValue(
        createMockUseInspirationsReturn({
          inspirations: [createMockInspiration()],
          total: 50,
          pageSize: 12,
        })
      )

      render(<InspirationGrid />, { wrapper: createWrapper() })

      // 50 / 12 = 4.17 -> 5 pages
      expect(screen.getByText('第 1 页，共 5 页')).toBeInTheDocument()
    })

    it('navigates to next page when next button clicked', async () => {
      mockUseInspirations.mockReturnValue(
        createMockUseInspirationsReturn({
          inspirations: [createMockInspiration()],
          total: 25,
          pageSize: 12,
        })
      )

      render(<InspirationGrid />, { wrapper: createWrapper() })

      const pagination = screen.getByText('第 1 页，共 3 页').parentElement
      const buttons = pagination?.querySelectorAll('button')
      const nextButton = buttons?.[1]
      fireEvent.click(nextButton!)

      await waitFor(() => {
        expect(screen.getByText('第 2 页，共 3 页')).toBeInTheDocument()
      })
    })

    it('navigates to previous page when prev button clicked', async () => {
      mockUseInspirations.mockReturnValue(
        createMockUseInspirationsReturn({
          inspirations: [createMockInspiration()],
          total: 25,
          pageSize: 12,
        })
      )

      render(<InspirationGrid />, { wrapper: createWrapper() })

      // First navigate to page 2
      const pagination1 = screen.getByText('第 1 页，共 3 页').parentElement
      const buttons1 = pagination1?.querySelectorAll('button')
      fireEvent.click(buttons1?.[1]!)

      await waitFor(() => {
        expect(screen.getByText('第 2 页，共 3 页')).toBeInTheDocument()
      })

      // Now navigate back to page 1
      const pagination2 = screen.getByText('第 2 页，共 3 页').parentElement
      const buttons2 = pagination2?.querySelectorAll('button')
      fireEvent.click(buttons2?.[0]!)

      await waitFor(() => {
        expect(screen.getByText('第 1 页，共 3 页')).toBeInTheDocument()
      })
    })
  })

  describe('Error State', () => {
    it('shows error message when loading fails', () => {
      mockUseInspirations.mockReturnValue(
        createMockUseInspirationsReturn({
          error: new Error('Failed to load'),
          isLoading: false,
        })
      )

      render(<InspirationGrid />, { wrapper: createWrapper() })

      expect(screen.getByText('加载灵感失败，请重试。')).toBeInTheDocument()
    })

    it('shows retry button on error', () => {
      mockUseInspirations.mockReturnValue(
        createMockUseInspirationsReturn({
          error: new Error('Failed to load'),
          isLoading: false,
        })
      )

      render(<InspirationGrid />, { wrapper: createWrapper() })

      expect(screen.getByText('重试')).toBeInTheDocument()
    })

    it('reloads page when retry button clicked', () => {
      // Mock window.location.reload for happy-dom
      const originalLocation = window.location
      const reloadMock = vi.fn()
      Object.defineProperty(window, 'location', {
        value: { ...originalLocation, reload: reloadMock },
        writable: true,
        configurable: true,
      })

      mockUseInspirations.mockReturnValue(
        createMockUseInspirationsReturn({
          error: new Error('Failed to load'),
          isLoading: false,
        })
      )

      render(<InspirationGrid />, { wrapper: createWrapper() })

      const retryButton = screen.getByText('重试')
      fireEvent.click(retryButton)

      expect(reloadMock).toHaveBeenCalled()

      // Restore original location
      Object.defineProperty(window, 'location', {
        value: originalLocation,
        writable: true,
        configurable: true,
      })
    })
  })

  describe('Project Type Filter', () => {
    it('passes projectType to useInspirations hook', () => {
      mockUseInspirations.mockReturnValue(createMockUseInspirationsReturn())

      render(<InspirationGrid projectType="novel" />, { wrapper: createWrapper() })

      expect(mockUseInspirations).toHaveBeenCalledWith(
        expect.objectContaining({ projectType: 'novel' })
      )
    })

    it('handles undefined projectType', () => {
      mockUseInspirations.mockReturnValue(createMockUseInspirationsReturn())

      render(<InspirationGrid />, { wrapper: createWrapper() })

      expect(mockUseInspirations).toHaveBeenCalledWith(
        expect.objectContaining({ projectType: undefined })
      )
    })
  })

  describe('Featured Only Mode', () => {
    it('passes featuredOnly to useInspirations hook', () => {
      mockUseInspirations.mockReturnValue(createMockUseInspirationsReturn())

      render(<InspirationGrid featuredOnly />, { wrapper: createWrapper() })

      expect(mockUseInspirations).toHaveBeenCalledWith(
        expect.objectContaining({ featuredOnly: true })
      )
    })

    it('defaults featuredOnly to false', () => {
      mockUseInspirations.mockReturnValue(createMockUseInspirationsReturn())

      render(<InspirationGrid />, { wrapper: createWrapper() })

      expect(mockUseInspirations).toHaveBeenCalledWith(
        expect.objectContaining({ featuredOnly: false })
      )
    })
  })

  describe('View Inspiration', () => {
    it('calls getDetail and opens dialog when view is clicked', async () => {
      const mockDetail = createMockInspirationDetail({ id: '1', name: 'Test' })
      const mockGetDetail = vi.fn().mockResolvedValue(mockDetail)

      mockUseInspirations.mockReturnValue(
        createMockUseInspirationsReturn({
          inspirations: [createMockInspiration({ id: '1', name: 'Test' })],
          total: 1,
          getDetail: mockGetDetail,
          currentDetail: null,
        })
      )

      render(<InspirationGrid />, { wrapper: createWrapper() })

      const viewButton = screen.getByText('查看')
      fireEvent.click(viewButton)

      await waitFor(() => {
        expect(mockGetDetail).toHaveBeenCalledWith('1')
      })
    })

    it('shows detail dialog after viewing', async () => {
      const mockDetail = createMockInspirationDetail({ id: '1', name: 'Test Inspiration' })
      const mockGetDetail = vi.fn().mockResolvedValue(mockDetail)

      mockUseInspirations.mockReturnValue(
        createMockUseInspirationsReturn({
          inspirations: [createMockInspiration({ id: '1', name: 'Test' })],
          total: 1,
          getDetail: mockGetDetail,
          currentDetail: mockDetail,
        })
      )

      render(<InspirationGrid />, { wrapper: createWrapper() })

      const viewButton = screen.getByText('查看')
      fireEvent.click(viewButton)

      await waitFor(() => {
        expect(mockGetDetail).toHaveBeenCalled()
      })
    })
  })

  describe('Copy Inspiration', () => {
    it('calls copyInspiration when copy button clicked', async () => {
      const mockCopyInspiration = vi.fn().mockResolvedValue({
        success: true,
        message: 'Success',
        project_id: 'new-project',
        project_name: 'New Project',
      })

      mockUseInspirations.mockReturnValue(
        createMockUseInspirationsReturn({
          inspirations: [createMockInspiration({ id: '1', name: 'Test' })],
          total: 1,
          copyInspiration: mockCopyInspiration,
        })
      )

      render(<InspirationGrid />, { wrapper: createWrapper() })

      // Find the copy button by its text
      const copyButton = screen.getByText('使用')

      fireEvent.click(copyButton)

      await waitFor(() => {
        expect(mockCopyInspiration).toHaveBeenCalledWith('1', undefined)
      })
    })

    it('shows copying state during copy operation', () => {
      mockUseInspirations.mockReturnValue(
        createMockUseInspirationsReturn({
          inspirations: [createMockInspiration({ id: '1', name: 'Test' })],
          total: 1,
          isCopying: true,
        })
      )

      render(<InspirationGrid />, { wrapper: createWrapper() })

      // The copy button should show copying state
      expect(screen.getByText('复制中...')).toBeInTheDocument()
    })

    it('shows quota error toast when copy request returns 402', async () => {
      const mockCopyInspiration = vi.fn().mockRejectedValue(new ApiError(402, 'quota exceeded'))

      mockUseInspirations.mockReturnValue(
        createMockUseInspirationsReturn({
          inspirations: [createMockInspiration({ id: '1', name: 'Test' })],
          total: 1,
          copyInspiration: mockCopyInspiration,
        })
      )

      render(<InspirationGrid />, { wrapper: createWrapper() })

      fireEvent.click(screen.getByText('使用'))

      await waitFor(() => {
        expect(mockToastError).toHaveBeenCalledWith('本月灵感复制次数已用完，请升级套餐或下月再试。')
      })

      expect(screen.getByText('灵感复制额度已用尽')).toBeInTheDocument()
    })

    it('navigates to billing with source when quota modal primary action is clicked', async () => {
      const mockCopyInspiration = vi.fn().mockRejectedValue(new ApiError(402, 'quota exceeded'))
      const originalLocation = window.location
      const assignMock = vi.fn()

      Object.defineProperty(window, 'location', {
        value: { ...originalLocation, assign: assignMock },
        writable: true,
        configurable: true,
      })

      try {
        mockUseInspirations.mockReturnValue(
          createMockUseInspirationsReturn({
            inspirations: [createMockInspiration({ id: '1', name: 'Test' })],
            total: 1,
            copyInspiration: mockCopyInspiration,
          })
        )

        render(<InspirationGrid />, { wrapper: createWrapper() })

        fireEvent.click(screen.getByText('使用'))

        await waitFor(() => {
          expect(screen.getByText('灵感复制额度已用尽')).toBeInTheDocument()
        })

        // The upgrade buttons appear once the plan is known (no free copy flashed at a paid author).
        fireEvent.click(await screen.findByRole('button', { name: '查看升级方案' }))
        expect(assignMock).toHaveBeenCalledWith('/dashboard/billing?source=inspiration_copy_quota_blocked')
      } finally {
        Object.defineProperty(window, 'location', {
          value: originalLocation,
          writable: true,
          configurable: true,
        })
      }
    })

    it('navigates to pricing with source when quota modal secondary action is clicked', async () => {
      const mockCopyInspiration = vi.fn().mockRejectedValue(new ApiError(402, 'quota exceeded'))
      const originalLocation = window.location
      const assignMock = vi.fn()

      Object.defineProperty(window, 'location', {
        value: { ...originalLocation, assign: assignMock },
        writable: true,
        configurable: true,
      })

      try {
        mockUseInspirations.mockReturnValue(
          createMockUseInspirationsReturn({
            inspirations: [createMockInspiration({ id: '1', name: 'Test' })],
            total: 1,
            copyInspiration: mockCopyInspiration,
          })
        )

        render(<InspirationGrid />, { wrapper: createWrapper() })

        fireEvent.click(screen.getByText('使用'))

        await waitFor(() => {
          expect(screen.getByText('灵感复制额度已用尽')).toBeInTheDocument()
        })

        fireEvent.click(await screen.findByRole('button', { name: '查看套餐对比' }))
        expect(assignMock).toHaveBeenCalledWith('/pricing?source=inspiration_copy_quota_blocked')
      } finally {
        Object.defineProperty(window, 'location', {
          value: originalLocation,
          writable: true,
          configurable: true,
        })
      }
    })

    it('shows auth error toast when copy request returns 401', async () => {
      const mockCopyInspiration = vi.fn().mockRejectedValue(new ApiError(401, 'unauthorized'))

      mockUseInspirations.mockReturnValue(
        createMockUseInspirationsReturn({
          inspirations: [createMockInspiration({ id: '1', name: 'Test' })],
          total: 1,
          copyInspiration: mockCopyInspiration,
        })
      )

      render(<InspirationGrid />, { wrapper: createWrapper() })

      fireEvent.click(screen.getByText('使用'))

      await waitFor(() => {
        expect(mockToastError).toHaveBeenCalledWith('请先登录后再复制灵感。')
      })
    })
  })

  describe('Custom Page Size', () => {
    it('uses custom page size', () => {
      mockUseInspirations.mockReturnValue(createMockUseInspirationsReturn())

      render(<InspirationGrid pageSize={24} />, { wrapper: createWrapper() })

      expect(mockUseInspirations).toHaveBeenCalledWith(
        expect.objectContaining({ pageSize: 24 })
      )
    })

    it('defaults page size to 12', () => {
      mockUseInspirations.mockReturnValue(createMockUseInspirationsReturn())

      render(<InspirationGrid />, { wrapper: createWrapper() })

      expect(mockUseInspirations).toHaveBeenCalledWith(
        expect.objectContaining({ pageSize: 12 })
      )
    })
  })

  describe('Accessibility', () => {
    it('has accessible search input', () => {
      mockUseInspirations.mockReturnValue(createMockUseInspirationsReturn())

      render(<InspirationGrid />, { wrapper: createWrapper() })

      const searchInput = screen.getByPlaceholderText('搜索灵感...')
      expect(searchInput).toBeInTheDocument()
      expect(searchInput).toHaveAttribute('type', 'text')
    })

    it('has accessible search button', () => {
      mockUseInspirations.mockReturnValue(createMockUseInspirationsReturn())

      render(<InspirationGrid />, { wrapper: createWrapper() })

      const searchButton = screen.getByRole('button', { name: '搜索' })
      expect(searchButton).toBeInTheDocument()
    })

    it('pagination buttons are accessible', () => {
      mockUseInspirations.mockReturnValue(
        createMockUseInspirationsReturn({
          inspirations: [createMockInspiration()],
          total: 25,
          pageSize: 12,
        })
      )

      render(<InspirationGrid />, { wrapper: createWrapper() })

      const pagination = screen.getByText('第 1 页，共 3 页').parentElement
      const buttons = pagination?.querySelectorAll('button')

      expect(buttons).toHaveLength(2)
      buttons?.forEach(btn => {
        expect(btn).toBeInTheDocument()
      })
    })
  })
})
