import { render, screen, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { EarnOpportunities } from '../points/EarnOpportunities'
import * as pointsApi from '../../lib/pointsApi'
import type { EarnOpportunity } from '../../types/points'

const inspirationFeature = vi.hoisted(() => ({ enabled: true }))

// Mock pointsApi
vi.mock('../../lib/pointsApi', () => ({
  pointsApi: {
    getEarnOpportunities: vi.fn(),
  },
}))

// Mock react-i18next
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (_key: string, defaultValue: string) => defaultValue,
  }),
}))

vi.mock('../../config/inspirations', () => ({
  inspirationsConfig: inspirationFeature,
}))

const mockPointsApi = vi.mocked(pointsApi.pointsApi)

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

const mockOpportunities: EarnOpportunity[] = [
  {
    type: 'check_in',
    points: 10,
    description: 'Daily check-in',
    is_completed: true,
    is_available: true,
  },
  {
    type: 'referral',
    points: 50,
    description: 'Invite a friend',
    is_completed: false,
    is_available: true,
  },
  {
    type: 'profile_complete',
    points: 20,
    description: 'Complete your profile',
    is_completed: false,
    is_available: false,
  },
]

describe('EarnOpportunities', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    inspirationFeature.enabled = true
  })

  describe('Rendering', () => {
    it('renders loading state initially', () => {
      mockPointsApi.getEarnOpportunities.mockImplementation(() => new Promise(() => {}))

      render(<EarnOpportunities />, { wrapper: createWrapper() })

      // Should show loading skeleton
      const skeleton = document.querySelector('.animate-pulse')
      expect(skeleton).toBeInTheDocument()
    })

    it('renders opportunities list correctly', async () => {
      mockPointsApi.getEarnOpportunities.mockResolvedValue(mockOpportunities)

      render(<EarnOpportunities />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('获取更多积分')).toBeInTheDocument()
      })

      // Should render all opportunity descriptions
      expect(screen.getByText('Daily check-in')).toBeInTheDocument()
      expect(screen.getByText('Invite a friend')).toBeInTheDocument()
      expect(screen.getByText('Complete your profile')).toBeInTheDocument()
    })

    it('displays points for each opportunity', async () => {
      mockPointsApi.getEarnOpportunities.mockResolvedValue(mockOpportunities)

      render(<EarnOpportunities />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('+10')).toBeInTheDocument()
        expect(screen.getByText('+50')).toBeInTheDocument()
        expect(screen.getByText('+20')).toBeInTheDocument()
      })
    })

    it('applies custom className', async () => {
      mockPointsApi.getEarnOpportunities.mockResolvedValue(mockOpportunities)

      render(<EarnOpportunities className="custom-class" />, { wrapper: createWrapper() })

      await waitFor(() => {
        const element = document.querySelector('.custom-class')
        expect(element).toBeInTheDocument()
      })
    })
  })

  describe('Completed Status Display', () => {
    it('shows completed status for completed opportunities', async () => {
      mockPointsApi.getEarnOpportunities.mockResolvedValue(mockOpportunities)

      render(<EarnOpportunities />, { wrapper: createWrapper() })

      await waitFor(() => {
        // Completed item shows "已完成" text
        expect(screen.getByText('已完成')).toBeInTheDocument()
      })
    })

    it('applies completed styling to completed opportunities', async () => {
      mockPointsApi.getEarnOpportunities.mockResolvedValue(mockOpportunities)

      render(<EarnOpportunities />, { wrapper: createWrapper() })

      await waitFor(() => {
        // Find the completed item's container div (the outer div with bg-* class)
        const completedText = screen.getByText('Daily check-in')
        // Traverse up: p -> div -> div (flex items-center gap-3) -> div (container with theme bg class)
        const container = completedText.closest('p')?.parentElement?.parentElement?.parentElement
        expect(container).toHaveClass('bg-[hsl(var(--bg-tertiary))]')
      })
    })

    it('shows strikethrough on completed opportunity descriptions', async () => {
      mockPointsApi.getEarnOpportunities.mockResolvedValue(mockOpportunities)

      render(<EarnOpportunities />, { wrapper: createWrapper() })

      await waitFor(() => {
        const completedDesc = screen.getByText('Daily check-in')
        expect(completedDesc).toHaveClass('line-through')
      })
    })

    it('shows grayed points for completed opportunities', async () => {
      mockPointsApi.getEarnOpportunities.mockResolvedValue(mockOpportunities)

      render(<EarnOpportunities />, { wrapper: createWrapper() })

      await waitFor(() => {
        const pointsElements = screen.getAllByText(/\+\d+/)
        // Find the +10 points (completed)
        const completedPoints = pointsElements.find(el => el.textContent === '+10')
        expect(completedPoints).toHaveClass('text-[hsl(var(--text-secondary))]')
      })
    })
  })

  describe('Available Status Display', () => {
    it('applies available styling to available opportunities', async () => {
      mockPointsApi.getEarnOpportunities.mockResolvedValue(mockOpportunities)

      render(<EarnOpportunities />, { wrapper: createWrapper() })

      await waitFor(() => {
        // Find the available referral item container
        const referralText = screen.getByText('Invite a friend')
        // Traverse up: p -> div -> div (flex items-center gap-3) -> div (container with theme accent bg)
        const container = referralText.closest('p')?.parentElement?.parentElement?.parentElement
        expect(container).toHaveClass('bg-[hsl(var(--accent-primary)/0.12)]')
      })
    })

    it('shows yellow color for available opportunity points', async () => {
      mockPointsApi.getEarnOpportunities.mockResolvedValue(mockOpportunities)

      render(<EarnOpportunities />, { wrapper: createWrapper() })

      await waitFor(() => {
        const pointsElements = screen.getAllByText(/\+\d+/)
        // Find the +50 points (available)
        const availablePoints = pointsElements.find(el => el.textContent === '+50')
        expect(availablePoints).toHaveClass('text-[hsl(var(--accent-secondary))]')
      })
    })

    it('shows blue icon color for available opportunities', async () => {
      mockPointsApi.getEarnOpportunities.mockResolvedValue(mockOpportunities)

      render(<EarnOpportunities />, { wrapper: createWrapper() })

      await waitFor(() => {
        // Find the referral icon container (available)
        const referralText = screen.getByText('Invite a friend')
        // Traverse up: p -> div (description wrapper) -> div (flex items-center gap-3) -> div (icon container with theme accent text)
        const iconContainer = referralText.closest('p')?.parentElement?.previousElementSibling
        expect(iconContainer).toHaveClass('text-[hsl(var(--accent-primary))]')
      })
    })
  })

  describe('Unavailable Status Display', () => {
    it('applies opacity to unavailable opportunities', async () => {
      mockPointsApi.getEarnOpportunities.mockResolvedValue(mockOpportunities)

      render(<EarnOpportunities />, { wrapper: createWrapper() })

      await waitFor(() => {
        // Find the unavailable profile_complete item container
        const profileText = screen.getByText('Complete your profile')
        // Traverse up: p -> div (description wrapper) -> div (flex items-center gap-3) -> div (container with opacity-60)
        const container = profileText.closest('p')?.parentElement?.parentElement?.parentElement
        expect(container).toHaveClass('opacity-60')
      })
    })

    it('shows gray icon for unavailable opportunities', async () => {
      mockPointsApi.getEarnOpportunities.mockResolvedValue(mockOpportunities)

      render(<EarnOpportunities />, { wrapper: createWrapper() })

      await waitFor(() => {
        const profileText = screen.getByText('Complete your profile')
        // Traverse up: p -> div (description wrapper) -> div (flex items-center gap-3) -> div (icon container with theme secondary text)
        const iconContainer = profileText.closest('p')?.parentElement?.previousElementSibling
        expect(iconContainer).toHaveClass('text-[hsl(var(--text-secondary))]')
      })
    })
  })

  describe('Icon Display', () => {
    it('renders check_in icon correctly', async () => {
      mockPointsApi.getEarnOpportunities.mockResolvedValue([mockOpportunities[0]])

      render(<EarnOpportunities />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('Daily check-in')).toBeInTheDocument()
      })

      // Should have an SVG icon
      const svg = document.querySelector('svg')
      expect(svg).toBeInTheDocument()
    })

    it('renders referral icon correctly', async () => {
      mockPointsApi.getEarnOpportunities.mockResolvedValue([mockOpportunities[1]])

      render(<EarnOpportunities />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('Invite a friend')).toBeInTheDocument()
      })

      // Should have an SVG icon
      const svg = document.querySelector('svg')
      expect(svg).toBeInTheDocument()
    })

    it('renders fallback icon for unknown type', async () => {
      const unknownOpportunity: EarnOpportunity = {
        type: 'unknown_type',
        points: 5,
        description: 'Unknown opportunity',
        is_completed: false,
        is_available: true,
      }

      mockPointsApi.getEarnOpportunities.mockResolvedValue([unknownOpportunity])

      render(<EarnOpportunities />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('Unknown opportunity')).toBeInTheDocument()
      })

      // Should render fallback coin icon
      const svg = document.querySelector('svg')
      expect(svg).toBeInTheDocument()
    })
  })

  describe('Empty State', () => {
    it('renders nothing when opportunities list is empty', async () => {
      mockPointsApi.getEarnOpportunities.mockResolvedValue([])

      const { container } = render(<EarnOpportunities />, { wrapper: createWrapper() })

      await waitFor(() => {
        // Component should render nothing when data is empty
        expect(container.firstChild).toBeNull()
      })
    })

    it('renders nothing when data is undefined', async () => {
      mockPointsApi.getEarnOpportunities.mockResolvedValue(null as unknown as EarnOpportunity[])

      const { container } = render(<EarnOpportunities />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(container.firstChild).toBeNull()
      })
    })
  })

  describe('Query Configuration', () => {
    it('calls getEarnOpportunities API', async () => {
      mockPointsApi.getEarnOpportunities.mockResolvedValue(mockOpportunities)

      render(<EarnOpportunities />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(mockPointsApi.getEarnOpportunities).toHaveBeenCalledTimes(1)
      })
    })

    it('uses correct query key', async () => {
      const queryClient = new QueryClient({
        defaultOptions: {
          queries: {
            retry: false,
          },
        },
      })

      mockPointsApi.getEarnOpportunities.mockResolvedValue(mockOpportunities)

      render(
        <QueryClientProvider client={queryClient}>
          <EarnOpportunities />
        </QueryClientProvider>
      )

      await waitFor(() => {
        expect(mockPointsApi.getEarnOpportunities).toHaveBeenCalled()
      })

      // Verify query key is set
      const cachedData = queryClient.getQueryData(['earn-opportunities'])
      expect(cachedData).toBeDefined()
    })
  })

  describe('Loading State', () => {
    it('shows skeleton while loading', () => {
      mockPointsApi.getEarnOpportunities.mockImplementation(() => new Promise(() => {}))

      render(<EarnOpportunities />, { wrapper: createWrapper() })

      const skeleton = document.querySelector('.animate-pulse')
      expect(skeleton).toBeInTheDocument()
      expect(skeleton).toHaveClass('rounded')
    })

    it('shows correct skeleton dimensions', () => {
      mockPointsApi.getEarnOpportunities.mockImplementation(() => new Promise(() => {}))

      render(<EarnOpportunities />, { wrapper: createWrapper() })

      const skeleton = document.querySelector('.animate-pulse')
      expect(skeleton).toHaveClass('h-4')
    })

    it('applies custom className to loading skeleton', () => {
      mockPointsApi.getEarnOpportunities.mockImplementation(() => new Promise(() => {}))

      render(<EarnOpportunities className="custom-class" />, { wrapper: createWrapper() })

      const card = document.querySelector('.custom-class')
      expect(card).toBeInTheDocument()
    })
  })

  describe('Styling', () => {
    it('has correct card styling', async () => {
      mockPointsApi.getEarnOpportunities.mockResolvedValue(mockOpportunities)

      render(<EarnOpportunities />, { wrapper: createWrapper() })

      await waitFor(() => {
        // The outer container div has the card classes
        const header = screen.getByText('获取更多积分')
        const card = header.parentElement
        expect(card).toHaveClass('border', 'rounded-xl')
      })
    })

    it('displays header with correct styling', async () => {
      mockPointsApi.getEarnOpportunities.mockResolvedValue(mockOpportunities)

      render(<EarnOpportunities />, { wrapper: createWrapper() })

      await waitFor(() => {
        const header = screen.getByText('获取更多积分')
        // Settings-dialog section label: the same 12/500 grey label as the 通用 tab.
        expect(header).toHaveClass('text-xs', 'font-medium')
      })
    })
  })

  describe('Multiple Opportunities', () => {
    it('renders all opportunity types with correct icons', async () => {
      const allOpportunities: EarnOpportunity[] = [
        { type: 'check_in', points: 10, description: 'Check in', is_completed: false, is_available: true },
        { type: 'check_in_streak', points: 20, description: 'Streak bonus', is_completed: false, is_available: true },
        { type: 'referral', points: 50, description: 'Referral', is_completed: false, is_available: true },
        { type: 'skill_contribution', points: 30, description: 'Skill', is_completed: false, is_available: true },
        { type: 'inspiration_contribution', points: 25, description: 'Inspiration', is_completed: false, is_available: true },
        { type: 'profile_complete', points: 15, description: 'Profile', is_completed: false, is_available: true },
      ]

      mockPointsApi.getEarnOpportunities.mockResolvedValue(allOpportunities)

      render(<EarnOpportunities />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('Check in')).toBeInTheDocument()
        expect(screen.getByText('Streak bonus')).toBeInTheDocument()
        expect(screen.getByText('Referral')).toBeInTheDocument()
        expect(screen.getByText('Skill')).toBeInTheDocument()
        expect(screen.getByText('Inspiration')).toBeInTheDocument()
        expect(screen.getByText('Profile')).toBeInTheDocument()
      })

      // All should have icons
      const svgs = document.querySelectorAll('svg')
      expect(svgs.length).toBe(6)
    })

    it('filters stale inspiration contribution opportunities when disabled', async () => {
      inspirationFeature.enabled = false
      mockPointsApi.getEarnOpportunities.mockResolvedValue([
        { type: 'inspiration_contribution', points: 25, description: 'Inspiration', is_completed: false, is_available: true },
        { type: 'skill_contribution', points: 30, description: 'Skill', is_completed: false, is_available: true },
      ])

      render(<EarnOpportunities />, { wrapper: createWrapper() })

      expect(await screen.findByText('Skill')).toBeInTheDocument()
      expect(screen.queryByText('Inspiration')).not.toBeInTheDocument()
    })
  })

  describe('Edge Cases', () => {
    it('handles zero points correctly', async () => {
      const zeroPointsOpportunity: EarnOpportunity = {
        type: 'check_in',
        points: 0,
        description: 'Free bonus',
        is_completed: false,
        is_available: true,
      }

      mockPointsApi.getEarnOpportunities.mockResolvedValue([zeroPointsOpportunity])

      render(<EarnOpportunities />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('+0')).toBeInTheDocument()
      })
    })

    it('handles very large points values', async () => {
      const largePointsOpportunity: EarnOpportunity = {
        type: 'referral',
        points: 999999,
        description: 'Big reward',
        is_completed: false,
        is_available: true,
      }

      mockPointsApi.getEarnOpportunities.mockResolvedValue([largePointsOpportunity])

      render(<EarnOpportunities />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('+999999')).toBeInTheDocument()
      })
    })

    it('handles long descriptions', async () => {
      const longDescOpportunity: EarnOpportunity = {
        type: 'check_in',
        points: 10,
        description: 'This is a very long opportunity description that might overflow the container',
        is_completed: false,
        is_available: true,
      }

      mockPointsApi.getEarnOpportunities.mockResolvedValue([longDescOpportunity])

      render(<EarnOpportunities />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('This is a very long opportunity description that might overflow the container')).toBeInTheDocument()
      })
    })

    it('handles special characters in description', async () => {
      const specialCharOpportunity: EarnOpportunity = {
        type: 'check_in',
        points: 10,
        description: 'Special & <characters> "test"',
        is_completed: false,
        is_available: true,
      }

      mockPointsApi.getEarnOpportunities.mockResolvedValue([specialCharOpportunity])

      render(<EarnOpportunities />, { wrapper: createWrapper() })

      await waitFor(() => {
        expect(screen.getByText('Special & <characters> "test"')).toBeInTheDocument()
      })
    })
  })
})
