import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as pointsApi from '../../lib/pointsApi'
import { PointsBalance } from '../points/PointsBalance'

vi.mock('../../lib/pointsApi', () => ({
  pointsApi: {
    getBalance: vi.fn(),
    getConfig: vi.fn(),
  },
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (
      _key: string,
      defaultValue: string,
      options?: Record<string, unknown>
    ) => {
      if (!options) {
        return defaultValue
      }

      return defaultValue.replace(/{{\s*(\w+)\s*}}/g, (_, name: string) => {
        return String(options[name] ?? '')
      })
    },
  }),
}))

const mockGetBalance = vi.mocked(pointsApi.pointsApi.getBalance)
const mockGetConfig = vi.mocked(pointsApi.pointsApi.getConfig)

function renderWithQuery(ui: JSX.Element) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        refetchInterval: false,
      },
    },
  })

  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>)
}

describe('PointsBalance', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetConfig.mockResolvedValue({
      check_in: 5,
      check_in_streak: 20,
      referral: 50,
      skill_contribution: 50,
      inspiration_contribution: 50,
      profile_complete: 20,
      pro_7days_cost: 100,
      streak_bonus_threshold: 7,
    })
  })

  it('says what the points can buy once the redeem price is known', async () => {
    mockGetBalance.mockResolvedValue({
      available: 1000,
      pending_expiration: 0,
      nearest_expiration_date: null,
    })

    renderWithQuery(<PointsBalance />)

    expect(await screen.findByText('100 积分可兑换 7 天 Pro')).toBeInTheDocument()
  })

  it('omits the redeem hint when the price failed to load', async () => {
    mockGetConfig.mockRejectedValue(new Error('config unavailable'))
    mockGetBalance.mockResolvedValue({
      available: 1000,
      pending_expiration: 0,
      nearest_expiration_date: null,
    })

    renderWithQuery(<PointsBalance />)

    await screen.findByText('1,000')
    await waitFor(() => expect(mockGetConfig).toHaveBeenCalled())
    expect(screen.queryByText(/可兑换 7 天 Pro/)).not.toBeInTheDocument()
  })

  it('renders loading skeleton while query is pending', () => {
    mockGetBalance.mockImplementation(() => new Promise(() => {}))

    renderWithQuery(<PointsBalance />)

    expect(document.querySelector('.animate-pulse')).toBeInTheDocument()
  })

  it('renders available points and pending expiration', async () => {
    mockGetBalance.mockResolvedValue({
      available: 1234567,
      pending_expiration: 100,
      nearest_expiration_date: '2026-03-07T00:00:00Z',
    })

    renderWithQuery(<PointsBalance />)

    expect(await screen.findByText('1,234,567')).toBeInTheDocument()
    expect(screen.getByText(/100 积分即将过期/)).toBeInTheDocument()
  })

  it('does not print a stray 0 when nothing is about to expire', async () => {
    mockGetBalance.mockResolvedValue({
      available: 0,
      pending_expiration: 0,
      nearest_expiration_date: null,
    })

    renderWithQuery(<PointsBalance />)

    expect(await screen.findByText('100 积分可兑换 7 天 Pro')).toBeInTheDocument()
    // Only the balance itself reads "0".
    expect(screen.getAllByText('0')).toHaveLength(1)
  })

  it('hides pending expiration when showExpiration is false', async () => {
    mockGetBalance.mockResolvedValue({
      available: 1000,
      pending_expiration: 100,
      nearest_expiration_date: '2026-03-07T00:00:00Z',
    })

    renderWithQuery(<PointsBalance showExpiration={false} />)

    expect(await screen.findByText('1,000')).toBeInTheDocument()
    expect(screen.queryByText(/积分即将过期/)).not.toBeInTheDocument()
  })

  it('returns null when balance is unavailable', async () => {
    mockGetBalance.mockResolvedValue(null as never)

    renderWithQuery(<PointsBalance />)

    await waitFor(() => {
      expect(mockGetBalance).toHaveBeenCalledTimes(1)
    })
    expect(screen.queryByText('积分余额')).not.toBeInTheDocument()
  })

  it('applies className to card root', async () => {
    mockGetBalance.mockResolvedValue({
      available: 1000,
      pending_expiration: 0,
      nearest_expiration_date: null,
    })

    renderWithQuery(<PointsBalance className="custom-points-card" />)

    await screen.findByText('1,000')
    expect(document.querySelector('.custom-points-card')).toBeInTheDocument()
  })
})
