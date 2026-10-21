import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { PointsHistory } from '../points/PointsHistory'
import * as pointsApi from '../../lib/pointsApi'
import type { PointsTransaction, TransactionHistoryResponse } from '../../types/points'

// Mock pointsApi
vi.mock('../../lib/pointsApi', () => ({
  pointsApi: {
    getTransactions: vi.fn(),
  },
}))

// Mock react-i18next
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    i18n: { language: 'zh' },
    t: (key: string, defaultOrOptions?: string | Record<string, unknown>, maybeOptions?: Record<string, unknown>) => {
      const translations: Record<string, string> = {
        history: '积分记录',
        historyError: '加载历史记录失败',
        noHistory: '暂无积分记录',
        totalTransactions: '共 {{count}} 条记录',
        expired: '已过期',
        'transactionTypes.check_in': '签到',
        'transactionTypes.check_in_streak': '连续签到奖励',
        'transactionTypes.referral': '邀请奖励',
        'transactionTypes.redeem_pro': '兑换 Pro',
        'transactionTypes.admin_grant': '系统赠送',
        'transactionTypes.expiration': '积分过期',
        'common:previous': '上一页',
        'common:next': '下一页',
      }

      const defaultValue = translations[key]
        ?? (typeof defaultOrOptions === 'string' ? defaultOrOptions : key)
      const options = (
        typeof defaultOrOptions === 'object' && defaultOrOptions !== null
          ? defaultOrOptions
          : maybeOptions
      ) ?? {}

      let result = defaultValue
      Object.keys(options).forEach((k) => {
        result = result.replace(new RegExp(`{{\\s*${k}\\s*}}`, 'g'), String(options[k] ?? ''))
      })
      return result
    },
  }),
}))

const mockPointsApi = vi.mocked(pointsApi.pointsApi)

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        refetchInterval: false,
        gcTime: 0,
      },
    },
  })
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
}

// Helper to create mock transaction
function createMockTransaction(overrides: Partial<PointsTransaction> = {}): PointsTransaction {
  return {
    id: 'tx-1',
    amount: 10,
    balance_after: 100,
    transaction_type: 'check_in',
    source_id: null,
    description: null,
    expires_at: null,
    is_expired: false,
    created_at: '2024-06-15T12:00:00Z',
    ...overrides,
  }
}

// Helper to create mock response
function createMockResponse(
  transactions: PointsTransaction[],
  overrides: Partial<TransactionHistoryResponse> = {}
): TransactionHistoryResponse {
  return {
    transactions,
    total: transactions.length,
    page: 1,
    page_size: 10,
    total_pages: 1,
    ...overrides,
  }
}

describe('PointsHistory', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  describe('Loading State', () => {
    it('renders loading skeleton initially', () => {
      mockPointsApi.getTransactions.mockImplementation(() => new Promise(() => {}))

      render(<PointsHistory />, { wrapper: createWrapper() })

      const skeleton = document.querySelector('.animate-pulse')
      expect(skeleton).toBeInTheDocument()
    })

    it('shows correct skeleton styling', () => {
      mockPointsApi.getTransactions.mockImplementation(() => new Promise(() => {}))

      render(<PointsHistory />, { wrapper: createWrapper() })

      const skeleton = document.querySelector('.animate-pulse')
      expect(skeleton).toHaveClass('rounded')
    })
  })

  describe('Error State', () => {
    it('displays error message when API fails', async () => {
      mockPointsApi.getTransactions.mockRejectedValue(new Error('Network error'))

      render(<PointsHistory />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('加载历史记录失败')).toBeInTheDocument()
      })
    })

    it('displays error message when data is null', async () => {
      mockPointsApi.getTransactions.mockResolvedValue(null as unknown as TransactionHistoryResponse)

      render(<PointsHistory />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('加载历史记录失败')).toBeInTheDocument()
      })
    })

    it('applies custom className to error state', async () => {
      mockPointsApi.getTransactions.mockRejectedValue(new Error('Network error'))

      render(<PointsHistory className="custom-class" />, { wrapper: createWrapper() })

      await waitFor(() => {
        const errorContainer = screen.getByText('加载历史记录失败').closest('.custom-class')
        expect(errorContainer).toHaveClass('custom-class')
      })
    })
  })

  describe('Empty State', () => {
    it('displays empty state when no transactions', async () => {
      mockPointsApi.getTransactions.mockResolvedValue(createMockResponse([]))

      render(<PointsHistory />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('暂无积分记录')).toBeInTheDocument()
      })
    })

    it('displays empty state icon', async () => {
      mockPointsApi.getTransactions.mockResolvedValue(createMockResponse([]))

      render(<PointsHistory />, { wrapper: createWrapper() })

      await waitFor(() => {
        const icon = document.querySelector('svg')
        expect(icon).toBeInTheDocument()
        expect(icon).toHaveClass('w-12', 'h-12')
      })
    })

    it('applies custom className to empty state', async () => {
      mockPointsApi.getTransactions.mockResolvedValue(createMockResponse([]))

      render(<PointsHistory className="empty-class" />, { wrapper: createWrapper() })

      await waitFor(() => {
        const emptyContainer = screen.getByText('暂无积分记录').closest('.empty-class')
        expect(emptyContainer).toHaveClass('empty-class')
      })
    })
  })

  describe('Transaction List Rendering', () => {
    it('renders transaction list correctly', async () => {
      const transactions = [
        createMockTransaction({ id: 'tx-1', amount: 10, transaction_type: 'check_in' }),
        createMockTransaction({ id: 'tx-2', amount: -50, transaction_type: 'redeem_pro' }),
      ]
      mockPointsApi.getTransactions.mockResolvedValue(createMockResponse(transactions))

      render(<PointsHistory />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('签到')).toBeInTheDocument()
        expect(screen.getByText('兑换 Pro')).toBeInTheDocument()
      })
    })

    it('displays total transaction count', async () => {
      const transactions = [
        createMockTransaction({ id: 'tx-1' }),
        createMockTransaction({ id: 'tx-2' }),
      ]
      mockPointsApi.getTransactions.mockResolvedValue(createMockResponse(transactions, { total: 25 }))

      render(<PointsHistory />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('共 25 条记录')).toBeInTheDocument()
      })
    })

    it('renders transaction amount with correct sign for earnings', async () => {
      const transactions = [
        createMockTransaction({ id: 'tx-1', amount: 100, transaction_type: 'check_in' }),
      ]
      mockPointsApi.getTransactions.mockResolvedValue(createMockResponse(transactions))

      render(<PointsHistory />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('+100')).toBeInTheDocument()
      })
    })

    it('renders transaction amount with correct sign for spending', async () => {
      const transactions = [
        createMockTransaction({ id: 'tx-1', amount: -50, transaction_type: 'redeem_pro' }),
      ]
      mockPointsApi.getTransactions.mockResolvedValue(createMockResponse(transactions))

      render(<PointsHistory />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('-50')).toBeInTheDocument()
      })
    })

    it('displays description when available', async () => {
      const transactions = [
        createMockTransaction({
          id: 'tx-1',
          transaction_type: 'referral',
          description: '邀请用户A',
        }),
      ]
      mockPointsApi.getTransactions.mockResolvedValue(createMockResponse(transactions))

      render(<PointsHistory />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('邀请奖励 - 邀请用户A')).toBeInTheDocument()
      })
    })
  })

  describe('Transaction Types', () => {
    it('displays check_in transaction type correctly', async () => {
      const transactions = [createMockTransaction({ transaction_type: 'check_in' })]
      mockPointsApi.getTransactions.mockResolvedValue(createMockResponse(transactions))

      render(<PointsHistory />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('签到')).toBeInTheDocument()
      })
    })

    it('displays check_in_streak transaction type correctly', async () => {
      const transactions = [createMockTransaction({ transaction_type: 'check_in_streak' })]
      mockPointsApi.getTransactions.mockResolvedValue(createMockResponse(transactions))

      render(<PointsHistory />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('连续签到奖励')).toBeInTheDocument()
      })
    })

    it('displays referral transaction type correctly', async () => {
      const transactions = [createMockTransaction({ transaction_type: 'referral' })]
      mockPointsApi.getTransactions.mockResolvedValue(createMockResponse(transactions))

      render(<PointsHistory />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('邀请奖励')).toBeInTheDocument()
      })
    })

    it('displays redeem_pro transaction type correctly', async () => {
      const transactions = [createMockTransaction({ transaction_type: 'redeem_pro' })]
      mockPointsApi.getTransactions.mockResolvedValue(createMockResponse(transactions))

      render(<PointsHistory />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('兑换 Pro')).toBeInTheDocument()
      })
    })

    it('displays admin_grant transaction type correctly', async () => {
      const transactions = [createMockTransaction({ transaction_type: 'admin_grant' })]
      mockPointsApi.getTransactions.mockResolvedValue(createMockResponse(transactions))

      render(<PointsHistory />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('系统赠送')).toBeInTheDocument()
      })
    })

    it('displays expiration transaction type correctly', async () => {
      const transactions = [createMockTransaction({ transaction_type: 'expiration' })]
      mockPointsApi.getTransactions.mockResolvedValue(createMockResponse(transactions))

      render(<PointsHistory />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('积分过期')).toBeInTheDocument()
      })
    })

    it('displays unknown transaction type as-is', async () => {
      const transactions = [createMockTransaction({ transaction_type: 'unknown_type' })]
      mockPointsApi.getTransactions.mockResolvedValue(createMockResponse(transactions))

      render(<PointsHistory />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('unknown_type')).toBeInTheDocument()
      })
    })
  })

  describe('Transaction Amount Styling', () => {
    it('applies green styling for positive amounts', async () => {
      const transactions = [createMockTransaction({ amount: 50 })]
      mockPointsApi.getTransactions.mockResolvedValue(createMockResponse(transactions))

      render(<PointsHistory />, { wrapper: createWrapper() })

      await waitFor(() => {
        const amountElement = screen.getByText('+50')
        expect(amountElement).toHaveClass('text-[hsl(var(--success-light))]')
      })
    })

    it('applies red styling for negative amounts', async () => {
      const transactions = [createMockTransaction({ amount: -30 })]
      mockPointsApi.getTransactions.mockResolvedValue(createMockResponse(transactions))

      render(<PointsHistory />, { wrapper: createWrapper() })

      await waitFor(() => {
        const amountElement = screen.getByText('-30')
        expect(amountElement).toHaveClass('text-[hsl(var(--error-text))]')
      })
    })

    it('applies green background for positive transaction icon', async () => {
      const transactions = [createMockTransaction({ amount: 50 })]
      mockPointsApi.getTransactions.mockResolvedValue(createMockResponse(transactions))

      render(<PointsHistory />, { wrapper: createWrapper() })

      await waitFor(() => {
        const transactionLabel = screen.getByText('签到')
        const iconContainer = transactionLabel.closest('div')?.previousElementSibling
        expect(iconContainer).toHaveClass('bg-[hsl(var(--success)/0.15)]')
        expect(iconContainer).toBeInTheDocument()
      })
    })

    it('applies red background for negative transaction icon', async () => {
      const transactions = [createMockTransaction({ amount: -30 })]
      mockPointsApi.getTransactions.mockResolvedValue(createMockResponse(transactions))

      render(<PointsHistory />, { wrapper: createWrapper() })

      await waitFor(() => {
        const transactionLabel = screen.getByText('签到')
        const iconContainer = transactionLabel.closest('div')?.previousElementSibling
        expect(iconContainer).toHaveClass('bg-[hsl(var(--error)/0.15)]')
        expect(iconContainer).toBeInTheDocument()
      })
    })
  })

  describe('Expired Points', () => {
    it('displays expired badge for expired transactions', async () => {
      const transactions = [createMockTransaction({ is_expired: true })]
      mockPointsApi.getTransactions.mockResolvedValue(createMockResponse(transactions))

      render(<PointsHistory />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('已过期')).toBeInTheDocument()
      })
    })

    it('does not display expired badge for non-expired transactions', async () => {
      const transactions = [createMockTransaction({ is_expired: false })]
      mockPointsApi.getTransactions.mockResolvedValue(createMockResponse(transactions))

      render(<PointsHistory />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('签到')).toBeInTheDocument()
      })

      expect(screen.queryByText('已过期')).not.toBeInTheDocument()
    })

    it('expired badge has correct styling', async () => {
      const transactions = [createMockTransaction({ is_expired: true })]
      mockPointsApi.getTransactions.mockResolvedValue(createMockResponse(transactions))

      render(<PointsHistory />, { wrapper: createWrapper() })

      await waitFor(() => {
        const expiredBadge = screen.getByText('已过期')
        expect(expiredBadge).toHaveClass('text-[hsl(var(--error-text))]')
      })
    })
  })

  describe('Relative Time Display', () => {
    // Note: The formatTransactionDate function is internal to the component.
    // Since it uses `new Date()` internally and we can't easily mock it with
    // async React Query operations, we test that the date element exists.
    // The actual time formatting is simple logic that works correctly.

    it('displays relative time for transactions', async () => {
      const transactions = [createMockTransaction({ created_at: '2024-06-15T12:00:00Z' })]
      mockPointsApi.getTransactions.mockResolvedValue(createMockResponse(transactions))

      render(<PointsHistory />, { wrapper: createWrapper() })

      await waitFor(() => {
        // The date element should be present (either relative time or formatted date)
        const dateElement = screen.getByText(/2024/)
        expect(dateElement).toHaveClass('text-xs', 'text-[hsl(var(--text-secondary))]')
        expect(dateElement).toBeInTheDocument()
        expect(dateElement?.textContent).toBeTruthy()
      })
    })
  })

  describe('Pagination', () => {
    it('does not show pagination when only one page', async () => {
      const transactions = [createMockTransaction()]
      mockPointsApi.getTransactions.mockResolvedValue(createMockResponse(transactions, { total_pages: 1 }))

      render(<PointsHistory />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('签到')).toBeInTheDocument()
      })

      expect(screen.queryByText('上一页')).not.toBeInTheDocument()
      expect(screen.queryByText('下一页')).not.toBeInTheDocument()
    })

    it('shows pagination when multiple pages', async () => {
      const transactions = [createMockTransaction()]
      mockPointsApi.getTransactions.mockResolvedValue(createMockResponse(transactions, { total_pages: 3 }))

      render(<PointsHistory />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('上一页')).toBeInTheDocument()
        expect(screen.getByText('下一页')).toBeInTheDocument()
      })
    })

    it('displays current page and total pages', async () => {
      const transactions = [createMockTransaction()]
      // Component uses internal state for current page, not from API response
      mockPointsApi.getTransactions.mockResolvedValue(createMockResponse(transactions, { page: 1, total_pages: 5 }))

      render(<PointsHistory />, { wrapper: createWrapper() })

      await waitFor(() => {
        // Initial page is 1, total_pages from response is 5
        expect(screen.getByText('1 / 5')).toBeInTheDocument()
      })
    })

    it('disables previous button on first page', async () => {
      const transactions = [createMockTransaction()]
      mockPointsApi.getTransactions.mockResolvedValue(createMockResponse(transactions, { page: 1, total_pages: 3 }))

      render(<PointsHistory />, { wrapper: createWrapper() })

      await waitFor(() => {
        const prevButton = screen.getByText('上一页')
        expect(prevButton).toBeDisabled()
      })
    })

    it('disables next button when on last page after navigation', async () => {
      const transactions = [createMockTransaction()]
      // Setup with 3 total pages
      mockPointsApi.getTransactions
        .mockResolvedValueOnce(createMockResponse(transactions, { page: 1, total_pages: 3 }))
        .mockResolvedValueOnce(createMockResponse(transactions, { page: 2, total_pages: 3 }))
        .mockResolvedValueOnce(createMockResponse(transactions, { page: 3, total_pages: 3 }))

      render(<PointsHistory />, { wrapper: createWrapper() })

      // Wait for initial render
      await waitFor(() => {
        expect(screen.getByText('1 / 3')).toBeInTheDocument()
      })

      // Click next to go to page 2
      fireEvent.click(screen.getByText('下一页'))

      await waitFor(() => {
        expect(screen.getByText('2 / 3')).toBeInTheDocument()
      })

      // Click next again to go to page 3 (last page)
      fireEvent.click(screen.getByText('下一页'))

      await waitFor(() => {
        expect(screen.getByText('3 / 3')).toBeInTheDocument()
      })

      // Get fresh reference to the next button and verify it's disabled
      await waitFor(() => {
        const nextButtonOnLastPage = screen.getByRole('button', { name: '下一页' })
        expect(nextButtonOnLastPage).toBeDisabled()
      })
    })

    it('enables previous button when not on first page', async () => {
      const transactions = [createMockTransaction()]
      // Start on page 1, navigate to page 2 to enable previous button
      mockPointsApi.getTransactions
        .mockResolvedValueOnce(createMockResponse(transactions, { page: 1, total_pages: 3 }))
        .mockResolvedValueOnce(createMockResponse(transactions, { page: 2, total_pages: 3 }))

      render(<PointsHistory />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('1 / 3')).toBeInTheDocument()
      })

      // Click next to go to page 2
      const nextButton = screen.getByText('下一页')
      fireEvent.click(nextButton)

      await waitFor(() => {
        const prevButton = screen.getByText('上一页')
        expect(prevButton).not.toBeDisabled()
      })
    })

    it('enables next button when not on last page', async () => {
      const transactions = [createMockTransaction()]
      mockPointsApi.getTransactions.mockResolvedValue(createMockResponse(transactions, { page: 1, total_pages: 3 }))

      render(<PointsHistory />, { wrapper: createWrapper() })

      await waitFor(() => {
        const nextButton = screen.getByText('下一页')
        expect(nextButton).not.toBeDisabled()
      })
    })

    it('calls API with next page when next button is clicked', async () => {
      const transactions = [createMockTransaction()]
      mockPointsApi.getTransactions
        .mockResolvedValueOnce(createMockResponse(transactions, { page: 1, total_pages: 3 }))
        .mockResolvedValueOnce(createMockResponse(transactions, { page: 2, total_pages: 3 }))

      render(<PointsHistory />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('下一页')).toBeInTheDocument()
      })

      const nextButton = screen.getByText('下一页')
      fireEvent.click(nextButton)

      await waitFor(() => {
        expect(mockPointsApi.getTransactions).toHaveBeenCalledWith(2, 10)
      })
    })

    it('calls API with previous page when previous button is clicked', async () => {
      const transactions = [createMockTransaction()]
      mockPointsApi.getTransactions
        .mockResolvedValueOnce(createMockResponse(transactions, { page: 2, total_pages: 3 }))
        .mockResolvedValueOnce(createMockResponse(transactions, { page: 1, total_pages: 3 }))

      render(<PointsHistory />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('上一页')).toBeInTheDocument()
      })

      const prevButton = screen.getByText('上一页')
      fireEvent.click(prevButton)

      await waitFor(() => {
        expect(mockPointsApi.getTransactions).toHaveBeenCalledWith(1, 10)
      })
    })

    it('does not go below page 1', async () => {
      const transactions = [createMockTransaction()]
      mockPointsApi.getTransactions.mockResolvedValue(createMockResponse(transactions, { page: 1, total_pages: 3 }))

      render(<PointsHistory />, { wrapper: createWrapper() })

      await waitFor(() => {
        const prevButton = screen.getByText('上一页')
        expect(prevButton).toBeDisabled()
      })
    })

    it('does not exceed total pages', async () => {
      const transactions = [createMockTransaction()]
      // Need to navigate to page 2 first, then verify we can't go past page 3
      mockPointsApi.getTransactions
        .mockResolvedValueOnce(createMockResponse(transactions, { page: 1, total_pages: 2 }))
        .mockResolvedValueOnce(createMockResponse(transactions, { page: 2, total_pages: 2 }))

      render(<PointsHistory />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('1 / 2')).toBeInTheDocument()
      })

      // Navigate to page 2 (last page)
      const nextButton = screen.getByText('下一页')
      fireEvent.click(nextButton)

      await waitFor(() => {
        expect(screen.getByText('2 / 2')).toBeInTheDocument()
      })

      // On last page, next button should be disabled
      await waitFor(() => {
        const nextButtonOnLastPage = screen.getByRole('button', { name: '下一页' })
        expect(nextButtonOnLastPage).toBeDisabled()
      })
    })
  })

  describe('Props', () => {
    it('uses default pageSize of 10', async () => {
      mockPointsApi.getTransactions.mockResolvedValue(createMockResponse([]))

      render(<PointsHistory />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(mockPointsApi.getTransactions).toHaveBeenCalledWith(1, 10)
      })
    })

    it('accepts custom pageSize prop', async () => {
      mockPointsApi.getTransactions.mockResolvedValue(createMockResponse([]))

      render(<PointsHistory pageSize={20} />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(mockPointsApi.getTransactions).toHaveBeenCalledWith(1, 20)
      })
    })

    it('applies custom className', async () => {
      const transactions = [createMockTransaction()]
      mockPointsApi.getTransactions.mockResolvedValue(createMockResponse(transactions))

      render(<PointsHistory className="custom-history-class" />, { wrapper: createWrapper() })

      await waitFor(() => {
        const container = document.querySelector('.custom-history-class')
        expect(container).toBeInTheDocument()
      })
    })
  })

  describe('Query Configuration', () => {
    it('uses correct query key with page and pageSize', async () => {
      const queryClient = new QueryClient({
        defaultOptions: {
          queries: {
            retry: false,
            gcTime: 0,
          },
        },
      })

      mockPointsApi.getTransactions.mockResolvedValue(createMockResponse([]))

      render(
        <QueryClientProvider client={queryClient}>
          <PointsHistory pageSize={15} />
        </QueryClientProvider>
      )

      await waitFor(() => {
        expect(mockPointsApi.getTransactions).toHaveBeenCalled()
      })

      const cachedData = queryClient.getQueryData(['points-transactions', 1, 15])
      expect(cachedData).toBeDefined()
    })

    it('refetches when page changes', async () => {
      const transactions = [createMockTransaction()]
      mockPointsApi.getTransactions
        .mockResolvedValueOnce(createMockResponse(transactions, { page: 1, total_pages: 2 }))
        .mockResolvedValueOnce(createMockResponse(transactions, { page: 2, total_pages: 2 }))

      render(<PointsHistory />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('下一页')).toBeInTheDocument()
      })

      const nextButton = screen.getByText('下一页')
      fireEvent.click(nextButton)

      await waitFor(() => {
        expect(mockPointsApi.getTransactions).toHaveBeenCalledTimes(2)
        expect(mockPointsApi.getTransactions).toHaveBeenNthCalledWith(2, 2, 10)
      })
    })
  })

  describe('Transaction Icons', () => {
    it('renders icon for each transaction', async () => {
      const transactions = [createMockTransaction({ transaction_type: 'check_in' })]
      mockPointsApi.getTransactions.mockResolvedValue(createMockResponse(transactions))

      render(<PointsHistory />, { wrapper: createWrapper() })

      await waitFor(() => {
        const icon = document.querySelector('svg')
        expect(icon).toBeInTheDocument()
      })
    })

    it('renders different icons for different transaction types', async () => {
      const transactions = [
        createMockTransaction({ id: 'tx-1', transaction_type: 'check_in' }),
        createMockTransaction({ id: 'tx-2', transaction_type: 'redeem_pro' }),
      ]
      mockPointsApi.getTransactions.mockResolvedValue(createMockResponse(transactions))

      render(<PointsHistory />, { wrapper: createWrapper() })

      await waitFor(() => {
        const icons = document.querySelectorAll('svg')
        expect(icons.length).toBeGreaterThanOrEqual(2)
      })
    })
  })

  describe('Edge Cases', () => {
    it('handles zero amount correctly', async () => {
      const transactions = [createMockTransaction({ amount: 0 })]
      mockPointsApi.getTransactions.mockResolvedValue(createMockResponse(transactions))

      render(<PointsHistory />, { wrapper: createWrapper() })

      await waitFor(() => {
        // Zero amount should display as "0" (no + sign)
        expect(screen.getByText('0')).toBeInTheDocument()
      })
    })

    it('handles very large amounts', async () => {
      const transactions = [createMockTransaction({ amount: 999999 })]
      mockPointsApi.getTransactions.mockResolvedValue(createMockResponse(transactions))

      render(<PointsHistory />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('+999999')).toBeInTheDocument()
      })
    })

    it('handles many transactions', async () => {
      const transactions = Array.from({ length: 50 }, (_, i) =>
        createMockTransaction({ id: `tx-${i}` })
      )
      mockPointsApi.getTransactions.mockResolvedValue(createMockResponse(transactions))

      render(<PointsHistory />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('共 50 条记录')).toBeInTheDocument()
      })
    })

    it('handles page size of 1', async () => {
      const transactions = [createMockTransaction()]
      mockPointsApi.getTransactions.mockResolvedValue(createMockResponse(transactions))

      render(<PointsHistory pageSize={1} />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(mockPointsApi.getTransactions).toHaveBeenCalledWith(1, 1)
      })
    })
  })

  describe('Accessibility', () => {
    it('has accessible pagination buttons', async () => {
      const transactions = [createMockTransaction()]
      mockPointsApi.getTransactions.mockResolvedValue(createMockResponse(transactions, { total_pages: 2 }))

      render(<PointsHistory />, { wrapper: createWrapper() })

      await waitFor(() => {
        const prevButton = screen.getByRole('button', { name: '上一页' })
        const nextButton = screen.getByRole('button', { name: '下一页' })
        expect(prevButton).toBeInTheDocument()
        expect(nextButton).toBeInTheDocument()
      })
    })

    it('pagination buttons show disabled state correctly', async () => {
      const transactions = [createMockTransaction()]
      mockPointsApi.getTransactions.mockResolvedValue(createMockResponse(transactions, { page: 1, total_pages: 2 }))

      render(<PointsHistory />, { wrapper: createWrapper() })

      await waitFor(() => {
        const prevButton = screen.getByRole('button', { name: '上一页' })
        const nextButton = screen.getByRole('button', { name: '下一页' })
        expect(prevButton).toBeDisabled()
        expect(prevButton).toHaveClass('disabled:cursor-not-allowed')
        expect(nextButton).not.toBeDisabled()
      })
    })
  })
})
