import { render, screen, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ReferralStats } from '../referral/ReferralStats'
import * as referralApi from '@/lib/referralApi'
import type { ReferralStats as ReferralStatsType, UserReward } from '@/types/referral'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const translations: Record<string, string> = {
        'stats.totalInvites': '总邀请数',
        'stats.successfulInvites': '成功邀请',
        'stats.totalPoints': '累计积分',
        'stats.availablePoints': '可用积分',
        'stats.rewardHistory': '奖励历史',
        'stats.loadError': '加载统计数据失败，请重试',
        'stats.noRewards': '暂无奖励记录',
        'rewardTypes.points': '积分',
        'rewardTypes.pro_trial': 'Pro 试用',
        'rewardTypes.credits': '额度',
        used: '已使用',
        'rewardSources.referral': '邀请好友奖励',
      }
      return translations[key] ?? key
    },
  }),
}))

// Mock referralApi
vi.mock('@/lib/referralApi', () => ({
  referralApi: {
    getStats: vi.fn(),
    getRewards: vi.fn(),
  },
}))

const mockReferralApi = vi.mocked(referralApi.referralApi)

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

describe('ReferralStats', () => {
  const mockStats: ReferralStatsType = {
    total_invites: 10,
    successful_invites: 5,
    total_points: 500,
    available_points: 300,
  }

  const mockRewards: UserReward[] = [
    {
      id: 'reward-1',
      reward_type: 'points',
      amount: 100,
      source: '邀请用户 A 注册',
      is_used: false,
      expires_at: null,
      created_at: '2024-01-15T00:00:00Z',
    },
    {
      id: 'reward-2',
      reward_type: 'pro_trial',
      amount: 7,
      source: '成功邀请用户 B 购买会员',
      is_used: true,
      expires_at: null,
      created_at: '2024-01-10T00:00:00Z',
    },
    {
      id: 'reward-3',
      reward_type: 'credits',
      amount: 50,
      source: '邀请用户 C 完成首次写作任务',
      is_used: false,
      expires_at: '2024-12-31T00:00:00Z',
      created_at: '2024-01-05T00:00:00Z',
    },
  ]

  beforeEach(() => {
    vi.clearAllMocks()
  })

  describe('Loading State', () => {
    it('shows loading spinner while stats are loading', () => {
      mockReferralApi.getStats.mockImplementation(() => new Promise(() => {}))
      mockReferralApi.getRewards.mockResolvedValue([])

      render(<ReferralStats />, { wrapper: createWrapper() })

      // Should show loading spinner (Loader2 icon with animate-spin class)
      const spinner = document.querySelector('.animate-spin')
      expect(spinner).toBeInTheDocument()
    })

    it('shows loading spinner in rewards section while rewards are loading', async () => {
      mockReferralApi.getStats.mockResolvedValue(mockStats)
      mockReferralApi.getRewards.mockImplementation(() => new Promise(() => {}))

      render(<ReferralStats />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('总邀请数')).toBeInTheDocument()
      })

      // Should show loading spinner in rewards section
      const spinners = document.querySelectorAll('.animate-spin')
      expect(spinners.length).toBe(1)
    })
  })

  describe('Error Handling', () => {
    it('shows error message when stats fail to load', async () => {
      mockReferralApi.getStats.mockRejectedValue(new Error('Network error'))
      mockReferralApi.getRewards.mockResolvedValue([])

      render(<ReferralStats />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('加载统计数据失败，请重试')).toBeInTheDocument()
      })
    })

    it('shows error message when stats is null', async () => {
      mockReferralApi.getStats.mockResolvedValue(null as unknown as ReferralStatsType)
      mockReferralApi.getRewards.mockResolvedValue([])

      render(<ReferralStats />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('加载统计数据失败，请重试')).toBeInTheDocument()
      })
    })
  })

  describe('Stats Display', () => {
    beforeEach(() => {
      mockReferralApi.getStats.mockResolvedValue(mockStats)
      mockReferralApi.getRewards.mockResolvedValue([])
    })

    it('displays total invites correctly', async () => {
      render(<ReferralStats />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('总邀请数')).toBeInTheDocument()
        expect(screen.getByText('10')).toBeInTheDocument()
      })
    })

    it('displays successful invites correctly', async () => {
      render(<ReferralStats />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('成功邀请')).toBeInTheDocument()
        expect(screen.getByText('5')).toBeInTheDocument()
      })
    })

    it('displays total points correctly', async () => {
      render(<ReferralStats />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('累计积分')).toBeInTheDocument()
        expect(screen.getByText('500')).toBeInTheDocument()
      })
    })

    it('displays available points correctly', async () => {
      render(<ReferralStats />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('可用积分')).toBeInTheDocument()
        expect(screen.getByText('300')).toBeInTheDocument()
      })
    })

    it('displays zero values correctly', async () => {
      mockReferralApi.getStats.mockResolvedValue({
        total_invites: 0,
        successful_invites: 0,
        total_points: 0,
        available_points: 0,
      })

      render(<ReferralStats />, { wrapper: createWrapper() })

      await waitFor(() => {
        const zeros = screen.getAllByText('0')
        expect(zeros.length).toBe(4)
      })
    })

    it('renders all four stat cards', async () => {
      render(<ReferralStats />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('总邀请数')).toBeInTheDocument()
        expect(screen.getByText('成功邀请')).toBeInTheDocument()
        expect(screen.getByText('累计积分')).toBeInTheDocument()
        expect(screen.getByText('可用积分')).toBeInTheDocument()
      })
    })
  })

  describe('Rewards History', () => {
    beforeEach(() => {
      mockReferralApi.getStats.mockResolvedValue(mockStats)
    })

    it('displays rewards list when rewards exist', async () => {
      mockReferralApi.getRewards.mockResolvedValue(mockRewards)

      render(<ReferralStats />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('奖励历史')).toBeInTheDocument()
        expect(screen.getByText(/邀请用户 A 注册/)).toBeInTheDocument()
      })
    })

    it('displays reward type label for points', async () => {
      mockReferralApi.getRewards.mockResolvedValue([mockRewards[0]])

      render(<ReferralStats />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('+100 积分')).toBeInTheDocument()
      })
    })

    it('displays reward type label for pro_trial', async () => {
      mockReferralApi.getRewards.mockResolvedValue([mockRewards[1]])

      render(<ReferralStats />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('+7 Pro 试用')).toBeInTheDocument()
      })
    })

    it('displays reward type label for credits', async () => {
      mockReferralApi.getRewards.mockResolvedValue([mockRewards[2]])

      render(<ReferralStats />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('+50 额度')).toBeInTheDocument()
      })
    })

    it('shows "已使用" badge for used rewards', async () => {
      mockReferralApi.getRewards.mockResolvedValue([mockRewards[1]])

      render(<ReferralStats />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('已使用')).toBeInTheDocument()
      })
    })

    it('does not show "已使用" badge for unused rewards', async () => {
      mockReferralApi.getRewards.mockResolvedValue([mockRewards[0]])

      render(<ReferralStats />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('+100 积分')).toBeInTheDocument()
      })

      expect(screen.queryByText('已使用')).not.toBeInTheDocument()
    })

    it('displays reward source text', async () => {
      mockReferralApi.getRewards.mockResolvedValue([mockRewards[0]])

      render(<ReferralStats />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('邀请用户 A 注册')).toBeInTheDocument()
      })
    })

    it('shows a localized label for the machine source "referral"', async () => {
      mockReferralApi.getRewards.mockResolvedValue([{ ...mockRewards[1], source: 'referral' }])

      render(<ReferralStats />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('邀请好友奖励')).toBeInTheDocument()
      })
      expect(screen.queryByText('referral')).not.toBeInTheDocument()
      // 「已使用」用中性徽标（次要文字色），不再是灰底浅灰字的 .badge
      expect(screen.getByText('已使用').closest('span.rounded')).toHaveClass('text-[hsl(var(--text-secondary))]')
    })

    it('truncates long source text', async () => {
      const longSource = '这是一个非常长的奖励来源描述文本用于测试截断功能是否正常工作'
      mockReferralApi.getRewards.mockResolvedValue([
        {
          ...mockRewards[0],
          source: longSource,
        },
      ])

      render(<ReferralStats />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText(/这是一个非常长的奖励来源描述文本用于测试截断/)).toBeInTheDocument()
      })
    })

    it('displays formatted date for rewards', async () => {
      mockReferralApi.getRewards.mockResolvedValue([mockRewards[0]])

      render(<ReferralStats />, { wrapper: createWrapper() })

      await waitFor(() => {
        // Date format is locale-dependent (zh-CN)
        expect(screen.getByText(/2024/)).toBeInTheDocument()
      })
    })

    it('renders multiple rewards', async () => {
      mockReferralApi.getRewards.mockResolvedValue(mockRewards)

      render(<ReferralStats />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText(/邀请用户 A 注册/)).toBeInTheDocument()
        expect(screen.getByText(/成功邀请用户 B 购买会员/)).toBeInTheDocument()
        expect(screen.getByText(/邀请用户 C 完成首次写作任务/)).toBeInTheDocument()
      })
    })
  })

  describe('Empty State', () => {
    beforeEach(() => {
      mockReferralApi.getStats.mockResolvedValue(mockStats)
      mockReferralApi.getRewards.mockResolvedValue([])
    })

    it('shows empty state when no rewards exist', async () => {
      render(<ReferralStats />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('暂无奖励记录')).toBeInTheDocument()
      })
    })

    it('shows empty state with correct styling', async () => {
      render(<ReferralStats />, { wrapper: createWrapper() })

      await waitFor(() => {
        const emptyState = screen.getByText('暂无奖励记录').closest('div')
        expect(emptyState).toHaveClass('text-center')
      })
    })
  })

  describe('API Calls', () => {
    it('calls getStats API', async () => {
      mockReferralApi.getStats.mockResolvedValue(mockStats)
      mockReferralApi.getRewards.mockResolvedValue([])

      render(<ReferralStats />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(mockReferralApi.getStats).toHaveBeenCalledTimes(1)
      })
    })

    it('calls getRewards API', async () => {
      mockReferralApi.getStats.mockResolvedValue(mockStats)
      mockReferralApi.getRewards.mockResolvedValue([])

      render(<ReferralStats />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(mockReferralApi.getRewards).toHaveBeenCalledTimes(1)
      })
    })

    it('uses correct query keys', async () => {
      const queryClient = new QueryClient({
        defaultOptions: {
          queries: {
            retry: false,
          },
        },
      })

      mockReferralApi.getStats.mockResolvedValue(mockStats)
      mockReferralApi.getRewards.mockResolvedValue([])

      render(
        <QueryClientProvider client={queryClient}>
          <ReferralStats />
        </QueryClientProvider>
      )

      await waitFor(() => {
        expect(mockReferralApi.getStats).toHaveBeenCalled()
        expect(mockReferralApi.getRewards).toHaveBeenCalled()
      })

      // Verify query keys are set
      const statsData = queryClient.getQueryData(['referralStats'])
      const rewardsData = queryClient.getQueryData(['userRewards'])
      expect(statsData).toBeDefined()
      expect(rewardsData).toBeDefined()
    })
  })

  describe('Styling', () => {
    beforeEach(() => {
      mockReferralApi.getStats.mockResolvedValue(mockStats)
      mockReferralApi.getRewards.mockResolvedValue([])
    })

    it('renders stats cards in grid layout', async () => {
      render(<ReferralStats />, { wrapper: createWrapper() })

      await waitFor(() => {
        const grid = screen.getByText('总邀请数').closest('.grid')
        expect(grid).toHaveClass('grid-cols-2')
      })
    })

    it('renders section title for rewards history', async () => {
      render(<ReferralStats />, { wrapper: createWrapper() })

      await waitFor(() => {
        const title = screen.getByText('奖励历史')
        expect(title).toHaveClass('font-semibold')
      })
    })
  })

  describe('Edge Cases', () => {
    it('handles large numbers in stats', async () => {
      mockReferralApi.getStats.mockResolvedValue({
        total_invites: 999999,
        successful_invites: 888888,
        total_points: 7777777,
        available_points: 6666666,
      })
      mockReferralApi.getRewards.mockResolvedValue([])

      render(<ReferralStats />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('999999')).toBeInTheDocument()
        expect(screen.getByText('7777777')).toBeInTheDocument()
      })
    })

    it('handles rewards with empty source', async () => {
      mockReferralApi.getStats.mockResolvedValue(mockStats)
      mockReferralApi.getRewards.mockResolvedValue([
        {
          ...mockRewards[0],
          source: '',
        },
      ])

      render(<ReferralStats />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('+100 积分')).toBeInTheDocument()
      })
    })

    it('handles very short source text without truncation', async () => {
      mockReferralApi.getStats.mockResolvedValue(mockStats)
      mockReferralApi.getRewards.mockResolvedValue([
        {
          ...mockRewards[0],
          source: '短文本',
        },
      ])

      render(<ReferralStats />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('短文本')).toBeInTheDocument()
      })
    })
  })
})
