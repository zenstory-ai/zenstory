import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as pointsApi from '../../lib/pointsApi'
import { DailyCheckIn } from '../points/DailyCheckIn'

vi.mock('../../lib/pointsApi', () => ({
  pointsApi: {
    getCheckInStatus: vi.fn(),
    checkIn: vi.fn(),
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

const mockGetCheckInStatus = vi.mocked(pointsApi.pointsApi.getCheckInStatus)
const mockCheckIn = vi.mocked(pointsApi.pointsApi.checkIn)

function renderWithQuery(ui: JSX.Element) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
      mutations: {
        retry: false,
      },
    },
  })

  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>)
}

describe('DailyCheckIn', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders check-in button when user has not checked in today', async () => {
    mockGetCheckInStatus.mockResolvedValue({
      checked_in: false,
      streak_days: 3,
      points_earned_today: 0,
    })

    renderWithQuery(<DailyCheckIn />)

    expect(await screen.findByText('签到领积分')).toBeInTheDocument()
    expect(screen.getByText('连续 3 天')).toBeInTheDocument()
  })

  it('renders checked-in state and earned points', async () => {
    mockGetCheckInStatus.mockResolvedValue({
      checked_in: true,
      streak_days: 7,
      points_earned_today: 10,
    })

    renderWithQuery(<DailyCheckIn />)

    expect(await screen.findByText('今日已签到')).toBeInTheDocument()
    expect(screen.getByText(/\(\+10\)/)).toBeInTheDocument()
  })

  it('submits check-in and shows success feedback', async () => {
    mockGetCheckInStatus.mockResolvedValue({
      checked_in: false,
      streak_days: 0,
      points_earned_today: 0,
    })
    mockCheckIn.mockResolvedValue({
      success: true,
      points_earned: 10,
      streak_days: 1,
      message: 'ok',
    })

    renderWithQuery(<DailyCheckIn />)

    fireEvent.click(await screen.findByText('签到领积分'))

    await waitFor(() => {
      expect(mockCheckIn).toHaveBeenCalledTimes(1)
    })
    expect(await screen.findByText('签到成功！+10 积分')).toBeInTheDocument()
  })

  it('shows error feedback when check-in fails', async () => {
    mockGetCheckInStatus.mockResolvedValue({
      checked_in: false,
      streak_days: 0,
      points_earned_today: 0,
    })
    mockCheckIn.mockRejectedValue(new Error('check-in failed'))

    renderWithQuery(<DailyCheckIn />)

    fireEvent.click(await screen.findByText('签到领积分'))

    expect(await screen.findByText('签到失败，请重试')).toBeInTheDocument()
  })
})
