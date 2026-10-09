import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RedeemProModal } from '../points/RedeemProModal'
import * as pointsApi from '../../lib/pointsApi'
import type { PointsBalance } from '../../types/points'

// Mock pointsApi
vi.mock('../../lib/pointsApi', () => ({
  pointsApi: {
    getBalance: vi.fn(),
    getConfig: vi.fn(),
    redeemForPro: vi.fn(),
  },
}))

// Mock react-i18next
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, defaultValue?: string, options?: Record<string, unknown>) => {
      // Namespaced keys used by shared components (e.g. the Modal close button)
      // are called without a default value, so resolve them explicitly.
      const namespaced: Record<string, string> = {
        'common:closeModal': 'Close modal',
      }
      if (namespaced[key]) {
        return namespaced[key]
      }
      if (options) {
        return defaultValue!.replace(/{{\s*(\w+)\s*}}/g, (_, name: string) => String(options[name] ?? ''))
      }
      return defaultValue
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
      },
    },
  })
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
}

describe('RedeemProModal', () => {
  const mockOnClose = vi.fn()

  beforeEach(() => {
    vi.resetAllMocks()
    mockPointsApi.getConfig.mockResolvedValue({check_in:5,check_in_streak:20,referral:50,skill_contribution:50,inspiration_contribution:50,profile_complete:20,pro_7days_cost:100,streak_bonus_threshold:7})
  })

  it('uses the configured base cost and disables options above the real balance', async () => {
    mockPointsApi.getConfig.mockResolvedValue({check_in:5,check_in_streak:20,referral:50,skill_contribution:50,inspiration_contribution:50,profile_complete:20,pro_7days_cost:250,streak_bonus_threshold:7})
    mockPointsApi.getBalance.mockResolvedValue({available:400,pending_expiration:0,nearest_expiration_date:null})
    render(<RedeemProModal isOpen onClose={mockOnClose} />, {wrapper:createWrapper()})
    expect(await screen.findByText('250 积分')).toBeInTheDocument()
    expect(screen.getByRole('button',{name:/7 天 250 积分/})).toBeEnabled()
    expect(screen.getByRole('button',{name:/14 天 500 积分/})).toBeDisabled()
  })

  it('shows a config failure and retries without inventing default prices', async () => {
    mockPointsApi.getConfig.mockRejectedValueOnce(new Error('Pricing unavailable')).mockResolvedValue({check_in:5,check_in_streak:20,referral:50,skill_contribution:50,inspiration_contribution:50,profile_complete:20,pro_7days_cost:100,streak_bonus_threshold:7})
    mockPointsApi.getBalance.mockResolvedValue({available:400,pending_expiration:0,nearest_expiration_date:null})
    render(<RedeemProModal isOpen onClose={mockOnClose} />, {wrapper:createWrapper()})
    expect(await screen.findByRole('alert')).toHaveTextContent('Pricing unavailable')
    expect(screen.queryByText('100 积分')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button',{name:'重试'}))
    expect(await screen.findByText('100 积分')).toBeInTheDocument()
  })

  it('treats an empty pricing response as a load failure with author-facing copy', async () => {
    const zhPoints = (await import('../../../public/locales/zh/points.json')).default as Record<string, unknown>
    mockPointsApi.getConfig
      .mockResolvedValueOnce(null as unknown as Awaited<ReturnType<typeof pointsApi.pointsApi.getConfig>>)
      .mockResolvedValue({check_in:5,check_in_streak:20,referral:50,skill_contribution:50,inspiration_contribution:50,profile_complete:20,pro_7days_cost:100,streak_bonus_threshold:7})
    mockPointsApi.getBalance.mockResolvedValue({available:400,pending_expiration:0,nearest_expiration_date:null})
    render(<RedeemProModal isOpen onClose={mockOnClose} />, {wrapper:createWrapper()})
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('兑换价格没加载出来，请重试')
    expect(zhPoints.configLoadFailed).toBe('兑换价格没加载出来，请重试')
    expect(screen.queryByText('100 积分')).not.toBeInTheDocument()
    expect(screen.getByRole('button',{name:'兑换'})).toBeDisabled()
    expect(mockPointsApi.redeemForPro).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button',{name:'重试'}))
    expect(await screen.findByText('100 积分')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(mockPointsApi.getConfig).toHaveBeenCalledTimes(2)
  })

  it('does not present a balance load failure as a zero balance', async () => {
    mockPointsApi.getBalance.mockRejectedValue(new Error('Balance unavailable'))
    render(<RedeemProModal isOpen onClose={mockOnClose} />, {wrapper:createWrapper()})
    expect(await screen.findByRole('alert')).toHaveTextContent('Balance unavailable')
    expect(screen.queryByText('0')).not.toBeInTheDocument()
    expect(screen.getByRole('button',{name:'兑换'})).toBeDisabled()
  })

  it('blocks every close path while redemption is in flight', async () => {
    mockPointsApi.getBalance.mockResolvedValue({available:400,pending_expiration:0,nearest_expiration_date:null})
    mockPointsApi.redeemForPro.mockReturnValue(new Promise(()=>{}))
    render(<RedeemProModal isOpen onClose={mockOnClose} />, {wrapper:createWrapper()})
    const redeem=await screen.findByRole('button',{name:'兑换'})
    await waitFor(()=>expect(redeem).toBeEnabled())
    fireEvent.click(redeem)
    await screen.findByRole('button',{name:'处理中...'})
    fireEvent.click(screen.getByRole('button',{name:'取消'}))
    fireEvent.click(document.querySelector('[role="presentation"]')!)
    fireEvent.keyDown(document,{key:'Escape'})
    expect(screen.queryByRole('button',{name:'Close modal'})).not.toBeInTheDocument()
    expect(mockOnClose).not.toHaveBeenCalled()
  })

  describe('Open/Close Behavior', () => {
    it('renders nothing when isOpen is false', () => {
      mockPointsApi.getBalance.mockResolvedValue({
        available: 1000, pending_expiration: 0, nearest_expiration_date: null,
      })

      const { container } = render(
        <RedeemProModal isOpen={false} onClose={mockOnClose} />,
        { wrapper: createWrapper() }
      )

      expect(container.firstChild).toBeNull()
    })

    it('renders modal when isOpen is true', async () => {
      mockPointsApi.getBalance.mockResolvedValue({
        available: 1000, pending_expiration: 0, nearest_expiration_date: null,
      })

      render(<RedeemProModal isOpen={true} onClose={mockOnClose} />, {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(screen.getByText('用积分兑换 Pro')).toBeInTheDocument()
      })
    })

    it('calls onClose when close button is clicked', async () => {
      mockPointsApi.getBalance.mockResolvedValue({
        available: 1000, pending_expiration: 0, nearest_expiration_date: null,
      })

      render(<RedeemProModal isOpen={true} onClose={mockOnClose} />, {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(screen.getByText('用积分兑换 Pro')).toBeInTheDocument()
      })

      const closeButton = screen.getByRole('button', { name: /close modal/i })
      fireEvent.click(closeButton!)

      expect(mockOnClose).toHaveBeenCalledTimes(1)
    })

    it('calls onClose when backdrop is clicked', async () => {
      mockPointsApi.getBalance.mockResolvedValue({
        available: 1000, pending_expiration: 0, nearest_expiration_date: null,
      })

      render(<RedeemProModal isOpen={true} onClose={mockOnClose} />, {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(screen.getByText('用积分兑换 Pro')).toBeInTheDocument()
      })

      const backdrop = document.querySelector('[role="presentation"]')
      fireEvent.click(backdrop!)

      expect(mockOnClose).toHaveBeenCalledTimes(1)
    })

    it('calls onClose when cancel button is clicked', async () => {
      mockPointsApi.getBalance.mockResolvedValue({
        available: 1000, pending_expiration: 0, nearest_expiration_date: null,
      })

      render(<RedeemProModal isOpen={true} onClose={mockOnClose} />, {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(screen.getByText('取消')).toBeInTheDocument()
      })

      const cancelButton = screen.getByRole('button', { name: '取消' })
      fireEvent.click(cancelButton)

      expect(mockOnClose).toHaveBeenCalledTimes(1)
    })
  })

  describe('Balance Display', () => {
    it('displays current balance correctly', async () => {
      mockPointsApi.getBalance.mockResolvedValue({
        available: 1500, pending_expiration: 0, nearest_expiration_date: null,
      })

      render(<RedeemProModal isOpen={true} onClose={mockOnClose} />, {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(screen.getByText('1,500')).toBeInTheDocument()
      })
    })

    it('formats large balance with locale separators', async () => {
      mockPointsApi.getBalance.mockResolvedValue({
        available: 1234567, pending_expiration: 0, nearest_expiration_date: null,
      })

      render(<RedeemProModal isOpen={true} onClose={mockOnClose} />, {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(screen.getByText('1,234,567')).toBeInTheDocument()
      })
    })

    it('displays zero balance correctly', async () => {
      mockPointsApi.getBalance.mockResolvedValue({
        available: 0, pending_expiration: 0, nearest_expiration_date: null,
      })

      render(<RedeemProModal isOpen={true} onClose={mockOnClose} />, {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(screen.getByText('0')).toBeInTheDocument()
      })
    })

    it('shows balance label', async () => {
      mockPointsApi.getBalance.mockResolvedValue({
        available: 1000, pending_expiration: 0, nearest_expiration_date: null,
      })

      render(<RedeemProModal isOpen={true} onClose={mockOnClose} />, {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(screen.getByText('当前积分')).toBeInTheDocument()
      })
    })
  })

  describe('Redeem Options', () => {
    it('displays all three redeem options', async () => {
      mockPointsApi.getBalance.mockResolvedValue({
        available: 1000, pending_expiration: 0, nearest_expiration_date: null,
      })

      render(<RedeemProModal isOpen={true} onClose={mockOnClose} />, {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(screen.getByText('7 天')).toBeInTheDocument()
        expect(screen.getByText('14 天')).toBeInTheDocument()
        expect(screen.getByText('30 天')).toBeInTheDocument()
      })
    })

    it('displays correct cost for each option', async () => {
      mockPointsApi.getBalance.mockResolvedValue({
        available: 1000, pending_expiration: 0, nearest_expiration_date: null,
      })

      render(<RedeemProModal isOpen={true} onClose={mockOnClose} />, {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(screen.getByText('100 积分')).toBeInTheDocument()
        expect(screen.getByText('200 积分')).toBeInTheDocument()
        expect(screen.getByText('400 积分')).toBeInTheDocument()
      })
    })

    it('selects 7 days option by default', async () => {
      mockPointsApi.getBalance.mockResolvedValue({
        available: 1000, pending_expiration: 0, nearest_expiration_date: null,
      })

      render(<RedeemProModal isOpen={true} onClose={mockOnClose} />, {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        const selectedButton = screen.getByText('7 天').closest('button')
        expect(selectedButton).toHaveTextContent('7 天')
        expect(selectedButton).toHaveClass('border-[hsl(var(--accent-primary))]')
      })
    })

    it('allows selecting different options', async () => {
      mockPointsApi.getBalance.mockResolvedValue({
        available: 1000, pending_expiration: 0, nearest_expiration_date: null,
      })

      render(<RedeemProModal isOpen={true} onClose={mockOnClose} />, {
        wrapper: createWrapper(),
      })

      // Wait for balance to load
      await waitFor(() => {
        expect(screen.getByText('1,000')).toBeInTheDocument()
      })

      const fourteenDaysButton = screen.getByText('14 天').closest('button')
      expect(fourteenDaysButton).not.toBeDisabled()
      fireEvent.click(fourteenDaysButton!)

      await waitFor(() => {
        // Check that the button container has the blue border indicating selection
        const selectedContainer = fourteenDaysButton!
        expect(selectedContainer).toHaveClass('border-[hsl(var(--accent-primary))]')
      })
    })

    it('disables option when balance is insufficient', async () => {
      mockPointsApi.getBalance.mockResolvedValue({
        available: 50, pending_expiration: 0, nearest_expiration_date: null,
      })

      render(<RedeemProModal isOpen={true} onClose={mockOnClose} />, {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        const sevenDaysButton = screen.getByText('7 天').closest('button')
        expect(sevenDaysButton).toBeDisabled()
      })
    })

    it('shows opacity-50 class for unaffordable options', async () => {
      mockPointsApi.getBalance.mockResolvedValue({
        available: 150, pending_expiration: 0, nearest_expiration_date: null,
      })

      render(<RedeemProModal isOpen={true} onClose={mockOnClose} />, {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        const thirtyDaysButton = screen.getByText('30 天').closest('button')
        expect(thirtyDaysButton).toHaveClass('opacity-50')
      })
    })
  })

  describe('Insufficient Balance', () => {
    it('shows "insufficient points" when balance is too low', async () => {
      mockPointsApi.getBalance.mockResolvedValue({
        available: 50, pending_expiration: 0, nearest_expiration_date: null,
      })

      render(<RedeemProModal isOpen={true} onClose={mockOnClose} />, {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(screen.getByText('积分不足')).toBeInTheDocument()
      })
    })

    it('disables redeem button when balance is insufficient', async () => {
      mockPointsApi.getBalance.mockResolvedValue({
        available: 50, pending_expiration: 0, nearest_expiration_date: null,
      })

      render(<RedeemProModal isOpen={true} onClose={mockOnClose} />, {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        const redeemButton = screen.getByRole('button', { name: '积分不足' })
        expect(redeemButton).toBeDisabled()
      })
    })

    it('enables redeem button when balance is sufficient', async () => {
      mockPointsApi.getBalance.mockResolvedValue({
        available: 500, pending_expiration: 0, nearest_expiration_date: null,
      })

      render(<RedeemProModal isOpen={true} onClose={mockOnClose} />, {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        const redeemButton = screen.getByRole('button', { name: '兑换' })
        expect(redeemButton).not.toBeDisabled()
      })
    })

    it('updates button state when selecting more expensive option', async () => {
      mockPointsApi.getBalance.mockResolvedValue({
        available: 150, pending_expiration: 0, nearest_expiration_date: null,
      })

      render(<RedeemProModal isOpen={true} onClose={mockOnClose} />, {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(screen.getByRole('button', { name: '兑换' })).not.toBeDisabled()
      })

      // Select 14 days (200 points) - can't afford
      const fourteenDaysButton = screen.getByText('14 天').closest('button')
      // The button should be disabled since balance < 200
      expect(fourteenDaysButton).toBeDisabled()

      // Try to click - it won't change selection since disabled
      fireEvent.click(fourteenDaysButton!)

      // Redeem button should still work since 7 days (100) is selected and affordable
      await waitFor(() => {
        expect(screen.getByRole('button', { name: '兑换' })).not.toBeDisabled()
      })
    })
  })

  describe('Redeem Action', () => {
    it('calls redeemForPro API with selected days', async () => {
      mockPointsApi.getBalance.mockResolvedValue({
        available: 500, pending_expiration: 0, nearest_expiration_date: null,
      })
      mockPointsApi.redeemForPro.mockResolvedValue({
        success: true,
        points_spent: 100,
        pro_days: 7,
        new_period_end: '2024-12-31T00:00:00Z',
      })

      render(<RedeemProModal isOpen={true} onClose={mockOnClose} />, {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(screen.getByRole('button', { name: '兑换' })).toBeInTheDocument()
      })

      const redeemButton = screen.getByRole('button', { name: '兑换' })
      fireEvent.click(redeemButton)

      await waitFor(() => {
        expect(mockPointsApi.redeemForPro).toHaveBeenCalledWith(7)
      })
    })

    it('calls redeemForPro with correct days when 14 is selected', async () => {
      mockPointsApi.getBalance.mockResolvedValue({
        available: 500, pending_expiration: 0, nearest_expiration_date: null,
      })
      mockPointsApi.redeemForPro.mockResolvedValue({
        success: true,
        points_spent: 200,
        pro_days: 14,
        new_period_end: '2024-12-31T00:00:00Z',
      })

      render(<RedeemProModal isOpen={true} onClose={mockOnClose} />, {
        wrapper: createWrapper(),
      })

      // Wait for balance to load
      await waitFor(() => {
        expect(screen.getByText('500')).toBeInTheDocument()
      })

      const fourteenDaysButton = screen.getByText('14 天').closest('button')
      expect(fourteenDaysButton).not.toBeDisabled()
      fireEvent.click(fourteenDaysButton!)

      // Wait for selection to update
      await waitFor(() => {
        expect(fourteenDaysButton).toHaveClass('border-[hsl(var(--accent-primary))]')
      })

      const redeemButton = screen.getByRole('button', { name: '兑换' })
      fireEvent.click(redeemButton)

      await waitFor(() => {
        expect(mockPointsApi.redeemForPro).toHaveBeenCalledWith(14)
      })
    })

    it('shows loading state during redemption', async () => {
      mockPointsApi.getBalance.mockResolvedValue({
        available: 500, pending_expiration: 0, nearest_expiration_date: null,
      })
      mockPointsApi.redeemForPro.mockImplementation(() => new Promise(() => {}))

      render(<RedeemProModal isOpen={true} onClose={mockOnClose} />, {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(screen.getByRole('button', { name: '兑换' })).toBeInTheDocument()
      })

      const redeemButton = screen.getByRole('button', { name: '兑换' })
      fireEvent.click(redeemButton)

      await waitFor(() => {
        expect(screen.getByText('处理中...')).toBeInTheDocument()
      })
    })

    it('disables redeem button during redemption', async () => {
      mockPointsApi.getBalance.mockResolvedValue({
        available: 500, pending_expiration: 0, nearest_expiration_date: null,
      })
      mockPointsApi.redeemForPro.mockImplementation(() => new Promise(() => {}))

      render(<RedeemProModal isOpen={true} onClose={mockOnClose} />, {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(screen.getByRole('button', { name: '兑换' })).toBeInTheDocument()
      })

      const redeemButton = screen.getByRole('button', { name: '兑换' })
      fireEvent.click(redeemButton)

      await waitFor(() => {
        expect(redeemButton).toBeDisabled()
      })
    })
  })

  describe('Success Handling', () => {
    it('shows the added Pro days with a celebration and a single close action', async () => {
      mockPointsApi.getBalance.mockResolvedValue({
        available: 500, pending_expiration: 0, nearest_expiration_date: null,
      })
      mockPointsApi.redeemForPro.mockResolvedValue({
        success: true,
        points_spent: 100,
        pro_days: 7,
        new_period_end: '2024-12-31T00:00:00Z',
      })

      render(<RedeemProModal isOpen={true} onClose={mockOnClose} />, {
        wrapper: createWrapper(),
      })

      const redeemButton = await screen.findByRole('button', { name: '兑换' })
      await waitFor(() => expect(redeemButton).toBeEnabled())
      fireEvent.click(redeemButton)

      expect(await screen.findByText('兑换成功！Pro 已增加 7 天。')).toBeInTheDocument()
      expect(screen.getByTestId('celebration-burst')).toBeInTheDocument()
      // The modal stays open so the author sees the result.
      expect(mockOnClose).not.toHaveBeenCalled()
      expect(screen.queryByRole('button', { name: '兑换' })).not.toBeInTheDocument()
      expect(screen.queryByRole('button', { name: '取消' })).not.toBeInTheDocument()

      fireEvent.click(screen.getByRole('button', { name: '关闭' }))
      expect(mockOnClose).toHaveBeenCalledTimes(1)
    })

    it('invalidates queries on successful redemption', async () => {
      const queryClient = new QueryClient({
        defaultOptions: {
          queries: {
            retry: false,
          },
        },
      })

      mockPointsApi.getBalance.mockResolvedValue({
        available: 500, pending_expiration: 0, nearest_expiration_date: null,
      })
      mockPointsApi.redeemForPro.mockResolvedValue({
        success: true,
        points_spent: 100,
        pro_days: 7,
        new_period_end: '2024-12-31T00:00:00Z',
      })

      const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')

      render(
        <QueryClientProvider client={queryClient}>
          <RedeemProModal isOpen={true} onClose={mockOnClose} />
        </QueryClientProvider>
      )

      await waitFor(() => {
        expect(screen.getByRole('button', { name: '兑换' })).toBeInTheDocument()
      })

      const redeemButton = screen.getByRole('button', { name: '兑换' })
      fireEvent.click(redeemButton)

      await waitFor(() => {
        expect(mockPointsApi.redeemForPro).toHaveBeenCalled()
      })

      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['points-balance'] })
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['subscription'] })
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['quota'] })
    })
  })

  describe('Error Handling', () => {
    it('shows error message on redemption failure', async () => {
      mockPointsApi.getBalance.mockResolvedValue({
        available: 500, pending_expiration: 0, nearest_expiration_date: null,
      })
      mockPointsApi.redeemForPro.mockRejectedValue(new Error('Redemption failed'))

      render(<RedeemProModal isOpen={true} onClose={mockOnClose} />, {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(screen.getByRole('button', { name: '兑换' })).toBeInTheDocument()
      })

      const redeemButton = screen.getByRole('button', { name: '兑换' })
      fireEvent.click(redeemButton)

      await waitFor(() => {
        expect(screen.getByText('兑换失败，请重试')).toBeInTheDocument()
      })
    })

    it('does not close modal on redemption failure', async () => {
      mockPointsApi.getBalance.mockResolvedValue({
        available: 500, pending_expiration: 0, nearest_expiration_date: null,
      })
      mockPointsApi.redeemForPro.mockRejectedValue(new Error('Redemption failed'))

      render(<RedeemProModal isOpen={true} onClose={mockOnClose} />, {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(screen.getByRole('button', { name: '兑换' })).toBeInTheDocument()
      })

      const redeemButton = screen.getByRole('button', { name: '兑换' })
      fireEvent.click(redeemButton)

      await waitFor(() => {
        expect(screen.getByText('兑换失败，请重试')).toBeInTheDocument()
      })

      // Modal should not close on error
      expect(mockOnClose).not.toHaveBeenCalled()
    })

    it('displays error in red alert box', async () => {
      mockPointsApi.getBalance.mockResolvedValue({
        available: 500, pending_expiration: 0, nearest_expiration_date: null,
      })
      mockPointsApi.redeemForPro.mockRejectedValue(new Error('Redemption failed'))

      render(<RedeemProModal isOpen={true} onClose={mockOnClose} />, {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(screen.getByRole('button', { name: '兑换' })).toBeInTheDocument()
      })

      const redeemButton = screen.getByRole('button', { name: '兑换' })
      fireEvent.click(redeemButton)

      await waitFor(() => {
        const errorBox = screen.getByText('兑换失败，请重试').closest('div')
        expect(errorBox).toHaveClass('bg-[hsl(var(--error)/0.1)]')
      })
    })

    it('clears error when retrying', async () => {
      mockPointsApi.getBalance.mockResolvedValue({
        available: 500, pending_expiration: 0, nearest_expiration_date: null,
      })
      mockPointsApi.redeemForPro
        .mockRejectedValueOnce(new Error('First failure'))
        .mockResolvedValueOnce({
          success: true,
          points_spent: 100,
          pro_days: 7,
          new_period_end: '2024-12-31T00:00:00Z',
        })

      render(<RedeemProModal isOpen={true} onClose={mockOnClose} />, {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(screen.getByRole('button', { name: '兑换' })).toBeInTheDocument()
      })

      const redeemButton = screen.getByRole('button', { name: '兑换' })
      fireEvent.click(redeemButton)

      await waitFor(() => {
        expect(screen.getByText('兑换失败，请重试')).toBeInTheDocument()
      })

      // Click again for success
      fireEvent.click(redeemButton)

      expect(await screen.findByText('兑换成功！Pro 已增加 7 天。')).toBeInTheDocument()
      expect(screen.queryByText('兑换失败，请重试')).not.toBeInTheDocument()
    })
  })

  describe('Pro Benefits Display', () => {
    it('displays Pro benefits section', async () => {
      mockPointsApi.getBalance.mockResolvedValue({
        available: 500, pending_expiration: 0, nearest_expiration_date: null,
      })

      render(<RedeemProModal isOpen={true} onClose={mockOnClose} />, {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(screen.getByText('Pro 权益')).toBeInTheDocument()
      })
    })

    it('lists all Pro benefits', async () => {
      mockPointsApi.getBalance.mockResolvedValue({
        available: 500, pending_expiration: 0, nearest_expiration_date: null,
      })

      render(<RedeemProModal isOpen={true} onClose={mockOnClose} />, {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(screen.getByText('AI 消息不限条数')).toBeInTheDocument()
        expect(screen.getByText('项目数不限')).toBeInTheDocument()
        expect(screen.getByText('每月 5 次素材拆解')).toBeInTheDocument()
      })
    })
  })

  describe('Query Configuration', () => {
    it('fetches balance when modal opens', async () => {
      mockPointsApi.getBalance.mockResolvedValue({
        available: 500, pending_expiration: 0, nearest_expiration_date: null,
      })

      render(<RedeemProModal isOpen={true} onClose={mockOnClose} />, {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(mockPointsApi.getBalance).toHaveBeenCalledTimes(1)
      })
    })

    it('does not fetch balance when modal is closed', () => {
      mockPointsApi.getBalance.mockResolvedValue({
        available: 500, pending_expiration: 0, nearest_expiration_date: null,
      })

      render(<RedeemProModal isOpen={false} onClose={mockOnClose} />, {
        wrapper: createWrapper(),
      })

      expect(mockPointsApi.getBalance).not.toHaveBeenCalled()
    })

    it('uses correct query key', async () => {
      const queryClient = new QueryClient({
        defaultOptions: {
          queries: {
            retry: false,
          },
        },
      })

      mockPointsApi.getBalance.mockResolvedValue({
        available: 500, pending_expiration: 0, nearest_expiration_date: null,
      })

      render(
        <QueryClientProvider client={queryClient}>
          <RedeemProModal isOpen={true} onClose={mockOnClose} />
        </QueryClientProvider>
      )

      await waitFor(() => {
        expect(mockPointsApi.getBalance).toHaveBeenCalled()
      })

      const cachedData = queryClient.getQueryData(['points-balance'])
      expect(cachedData).toBeDefined()
    })
  })

  describe('Accessibility', () => {
    it('has accessible close button', async () => {
      mockPointsApi.getBalance.mockResolvedValue({
        available: 500, pending_expiration: 0, nearest_expiration_date: null,
      })

      render(<RedeemProModal isOpen={true} onClose={mockOnClose} />, {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        const closeButton = screen.getByRole('button', { name: /close modal/i })
        expect(closeButton).toBeInTheDocument()
      })
    })

    it('redeem button is properly disabled during loading', async () => {
      mockPointsApi.getBalance.mockResolvedValue({
        available: 500, pending_expiration: 0, nearest_expiration_date: null,
      })
      mockPointsApi.redeemForPro.mockImplementation(() => new Promise(() => {}))

      render(<RedeemProModal isOpen={true} onClose={mockOnClose} />, {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(screen.getByRole('button', { name: '兑换' })).toBeInTheDocument()
      })

      const redeemButton = screen.getByRole('button', { name: '兑换' })
      fireEvent.click(redeemButton)

      await waitFor(() => {
        expect(redeemButton).toBeDisabled()
        expect(redeemButton).toHaveAttribute('disabled')
      })
    })
  })

  describe('Edge Cases', () => {
    it('handles balance API returning null', async () => {
      mockPointsApi.getBalance.mockResolvedValue(null as unknown as PointsBalance)

      render(<RedeemProModal isOpen={true} onClose={mockOnClose} />, {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(screen.getByRole('alert')).toHaveTextContent('积分余额没加载出来，请重试')
        expect(screen.queryByText('0')).not.toBeInTheDocument()
        expect(screen.getByRole('button', {name:'兑换'})).toBeDisabled()
      })
    })

    it('handles balance data missing available field', async () => {
      mockPointsApi.getBalance.mockResolvedValue({} as PointsBalance)

      render(<RedeemProModal isOpen={true} onClose={mockOnClose} />, {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(screen.getByRole('alert')).toHaveTextContent('积分余额没加载出来，请重试')
        expect(screen.queryByText('0')).not.toBeInTheDocument()
        expect(screen.getByRole('button', {name:'兑换'})).toBeDisabled()
      })
    })

    it('handles selecting 30 days option', async () => {
      mockPointsApi.getBalance.mockResolvedValue({
        available: 500, pending_expiration: 0, nearest_expiration_date: null,
      })

      render(<RedeemProModal isOpen={true} onClose={mockOnClose} />, {
        wrapper: createWrapper(),
      })

      // Wait for balance to load
      await waitFor(() => {
        expect(screen.getByText('500')).toBeInTheDocument()
      })

      const thirtyDaysButton = screen.getByText('30 天').closest('button')
      expect(thirtyDaysButton).not.toBeDisabled()
      fireEvent.click(thirtyDaysButton!)

      // Wait for selection to update
      await waitFor(() => {
        expect(thirtyDaysButton).toHaveClass('border-[hsl(var(--accent-primary))]')
      })
    })
  })
})
