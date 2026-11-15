import { renderHook, act, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { useQuota } from '../useQuota'
import * as subscriptionApi from '@/lib/subscriptionApi'

// Mock subscriptionApi
vi.mock('@/lib/subscriptionApi', () => ({
  subscriptionApi: {
    getQuota: vi.fn(),
  },
  subscriptionQueryKeys: {
    status: () => ['subscription-status', 'test-user'],
    quota: () => ['subscription-quota', 'test-user'],
    quotaLite: () => ['quota', 'test-user'],
  },
}))

// Mock subscriptionStore
const mockIsPro = vi.fn(() => false)
const mockGetTier = vi.fn(() => 'free')

vi.mock('@/stores/subscriptionStore', () => ({
  useSubscriptionStore: vi.fn(() => ({
    subscription: null,
    isPro: mockIsPro,
    getTier: mockGetTier,
  })),
}))

const mockGetQuota = vi.mocked(subscriptionApi.subscriptionApi.getQuota)

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  })
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
  }
}

describe('useQuota', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockIsPro.mockReturnValue(false)
    mockGetTier.mockReturnValue('free')
  })

  it.each([2, 3, 10])('uses the lowered daily free allowance with %i existing messages', async (used) => {
    mockGetQuota.mockResolvedValue({
      ai_conversations: { used, limit: 3 },
      projects: { used: 1, limit: 3 },
    } as never)
    const { result } = renderHook(() => useQuota(), { wrapper: createWrapper() })
    await waitFor(() => expect(result.current.quotas).not.toBeNull())

    expect(result.current.getQuotaStatus('ai_conversations')).toMatchObject({
      used,
      limit: 3,
      remaining: Math.max(0, 3 - used),
      isExceeded: used >= 3,
      isUnlimited: false,
    })
    expect(result.current.canUseFeature('ai_conversations')).toBe(used < 3)
  })

  describe('initial state', () => {
    it('initializes with loading state when fetching', () => {
      mockGetQuota.mockImplementation(() => new Promise(() => {}))

      const { result } = renderHook(() => useQuota(), {
        wrapper: createWrapper(),
      })

      expect(result.current.isLoading).toBe(true)
      expect(result.current.quotas).toBe(null)
      expect(result.current.error).toBe(null)
    })

    it('initializes with correct default values', async () => {
      const mockQuotaResponse = {
        data: {
          ai_conversations: {
            used: 5,
            limit: 20,
            reset_at: '2025-01-02T00:00:00Z',
          },
          projects: {
            used: 2,
            limit: 3,
          },
        },
      }
      mockGetQuota.mockResolvedValue(mockQuotaResponse.data)

      const { result } = renderHook(() => useQuota(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.isLoading).toBe(false)
      })

      expect(result.current.quotas).not.toBe(null)
      expect(result.current.isPro).toBe(false)
      expect(result.current.tier).toBe('free')
    })
  })

  describe('fetch quota data', () => {
    it('fetches and returns quota data successfully', async () => {
      const mockQuotaResponse = {
        data: {
          ai_conversations: {
            used: 5,
            limit: 20,
            reset_at: '2025-01-02T00:00:00Z',
          },
          projects: {
            used: 2,
            limit: 3,
          },
        },
      }
      mockGetQuota.mockResolvedValue(mockQuotaResponse.data)

      const { result } = renderHook(() => useQuota(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.isLoading).toBe(false)
      })

      expect(mockGetQuota).toHaveBeenCalledTimes(1)
      expect(result.current.quotas).not.toBe(null)
    })

    it('calls getQuota API endpoint', async () => {
      const mockQuotaResponse = {
        data: {
          ai_conversations: {
            used: 0,
            limit: 10,
            reset_at: '2025-01-02T00:00:00Z',
          },
          projects: {
            used: 0,
            limit: 3,
          },
        },
      }
      mockGetQuota.mockResolvedValue(mockQuotaResponse.data)

      renderHook(() => useQuota(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(mockGetQuota).toHaveBeenCalled()
      })
    })
  })

  describe('quota calculation', () => {
    it('calculates quota status for ai_conversations', async () => {
      const mockQuotaResponse = {
        data: {
          ai_conversations: {
            used: 5,
            limit: 20,
            reset_at: '2025-01-02T00:00:00Z',
          },
          projects: {
            used: 2,
            limit: 3,
          },
        },
      }
      mockGetQuota.mockResolvedValue(mockQuotaResponse.data)

      const { result } = renderHook(() => useQuota(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.quotas).not.toBe(null)
      })

      const quotaStatus = result.current.quotas!['ai_conversations']
      expect(quotaStatus.used).toBe(5)
      expect(quotaStatus.limit).toBe(20)
      expect(quotaStatus.remaining).toBe(15)
      expect(quotaStatus.isUnlimited).toBe(false)
      expect(quotaStatus.isExceeded).toBe(false)
      expect(quotaStatus.percentage).toBe(25)
    })

    it('calculates unlimited quota correctly', async () => {
      const mockQuotaResponse = {
        data: {
          ai_conversations: {
            used: 100,
            limit: -1,
            reset_at: '2025-01-02T00:00:00Z',
          },
          projects: {
            used: 10,
            limit: -1,
          },
        },
      }
      mockGetQuota.mockResolvedValue(mockQuotaResponse.data)

      const { result } = renderHook(() => useQuota(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.quotas).not.toBe(null)
      })

      const quotaStatus = result.current.quotas!['ai_conversations']
      expect(quotaStatus.used).toBe(100)
      expect(quotaStatus.limit).toBe(-1)
      expect(quotaStatus.remaining).toBe(-1)
      expect(quotaStatus.isUnlimited).toBe(true)
      expect(quotaStatus.isExceeded).toBe(false)
      expect(quotaStatus.percentage).toBe(-1)
    })

    it('calculates exceeded quota correctly', async () => {
      const mockQuotaResponse = {
        data: {
          ai_conversations: {
            used: 25,
            limit: 20,
            reset_at: '2025-01-02T00:00:00Z',
          },
          projects: {
            used: 5,
            limit: 3,
          },
        },
      }
      mockGetQuota.mockResolvedValue(mockQuotaResponse.data)

      const { result } = renderHook(() => useQuota(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.quotas).not.toBe(null)
      })

      const quotaStatus = result.current.quotas!['ai_conversations']
      expect(quotaStatus.used).toBe(25)
      expect(quotaStatus.limit).toBe(20)
      expect(quotaStatus.remaining).toBe(0)
      expect(quotaStatus.isUnlimited).toBe(false)
      expect(quotaStatus.isExceeded).toBe(true)
      expect(quotaStatus.percentage).toBe(100)
    })

    it('calculates percentage correctly for various usage levels', async () => {
      const testCases = [
        { used: 0, limit: 20, expectedPercentage: 0 },
        { used: 10, limit: 20, expectedPercentage: 50 },
        { used: 16, limit: 20, expectedPercentage: 80 },
        { used: 19, limit: 20, expectedPercentage: 95 },
        { used: 20, limit: 20, expectedPercentage: 100 },
      ]

      for (const testCase of testCases) {
        vi.clearAllMocks()

        const mockQuotaResponse = {
          data: {
            ai_conversations: {
              used: testCase.used,
              limit: testCase.limit,
              reset_at: '2025-01-02T00:00:00Z',
            },
            projects: {
              used: 0,
              limit: 3,
            },
          },
        }
        mockGetQuota.mockResolvedValue(mockQuotaResponse.data)

        const { result } = renderHook(() => useQuota(), {
          wrapper: createWrapper(),
        })

        await waitFor(() => {
          expect(result.current.quotas).not.toBe(null)
        })

        expect(result.current.quotas!['ai_conversations'].percentage).toBe(
          testCase.expectedPercentage
        )
      }
    })

    it('calculates remaining correctly when used equals limit', async () => {
      const mockQuotaResponse = {
        data: {
          ai_conversations: {
            used: 20,
            limit: 20,
            reset_at: '2025-01-02T00:00:00Z',
          },
          projects: {
            used: 3,
            limit: 3,
          },
        },
      }
      mockGetQuota.mockResolvedValue(mockQuotaResponse.data)

      const { result } = renderHook(() => useQuota(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.quotas).not.toBe(null)
      })

      const quotaStatus = result.current.quotas!['ai_conversations']
      expect(quotaStatus.remaining).toBe(0)
      expect(quotaStatus.isExceeded).toBe(true)
    })
  })

  describe('canUseFeature', () => {
    it('returns true when quota is not exceeded', async () => {
      const mockQuotaResponse = {
        data: {
          ai_conversations: {
            used: 5,
            limit: 20,
            reset_at: '2025-01-02T00:00:00Z',
          },
          projects: {
            used: 2,
            limit: 3,
          },
        },
      }
      mockGetQuota.mockResolvedValue(mockQuotaResponse.data)

      const { result } = renderHook(() => useQuota(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.quotas).not.toBe(null)
      })

      expect(result.current.canUseFeature('ai_conversations')).toBe(true)
    })

    it('returns false when quota is exceeded', async () => {
      const mockQuotaResponse = {
        data: {
          ai_conversations: {
            used: 20,
            limit: 20,
            reset_at: '2025-01-02T00:00:00Z',
          },
          projects: {
            used: 3,
            limit: 3,
          },
        },
      }
      mockGetQuota.mockResolvedValue(mockQuotaResponse.data)

      const { result } = renderHook(() => useQuota(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.quotas).not.toBe(null)
      })

      expect(result.current.canUseFeature('ai_conversations')).toBe(false)
    })

    it('returns true for unlimited quota', async () => {
      const mockQuotaResponse = {
        data: {
          ai_conversations: {
            used: 1000,
            limit: -1,
            reset_at: '2025-01-02T00:00:00Z',
          },
          projects: {
            used: 100,
            limit: -1,
          },
        },
      }
      mockGetQuota.mockResolvedValue(mockQuotaResponse.data)

      const { result } = renderHook(() => useQuota(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.quotas).not.toBe(null)
      })

      expect(result.current.canUseFeature('ai_conversations')).toBe(true)
    })

    it('returns true for pro users regardless of quota', async () => {
      mockIsPro.mockReturnValue(true)

      const mockQuotaResponse = {
        data: {
          ai_conversations: {
            used: 20,
            limit: 20,
            reset_at: '2025-01-02T00:00:00Z',
          },
          projects: {
            used: 3,
            limit: 3,
          },
        },
      }
      mockGetQuota.mockResolvedValue(mockQuotaResponse.data)

      const { result } = renderHook(() => useQuota(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.quotas).not.toBe(null)
      })

      expect(result.current.isPro).toBe(true)
      expect(result.current.canUseFeature('ai_conversations')).toBe(true)
    })

    it('returns false when quota data is not available', async () => {
      mockGetQuota.mockResolvedValue(null as unknown as Awaited<ReturnType<typeof subscriptionApi.subscriptionApi.getQuota>>)

      const { result } = renderHook(() => useQuota(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.isLoading).toBe(false)
      })

      expect(result.current.quotas).toBe(null)
      expect(result.current.canUseFeature('ai_conversations')).toBe(false)
    })
  })

  describe('getQuotaStatus', () => {
    it('returns quota status for specific feature', async () => {
      const mockQuotaResponse = {
        data: {
          ai_conversations: {
            used: 8,
            limit: 20,
            reset_at: '2025-01-02T00:00:00Z',
          },
          projects: {
            used: 1,
            limit: 3,
          },
        },
      }
      mockGetQuota.mockResolvedValue(mockQuotaResponse.data)

      const { result } = renderHook(() => useQuota(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.quotas).not.toBe(null)
      })

      const status = result.current.getQuotaStatus('ai_conversations')
      expect(status).not.toBe(null)
      expect(status?.used).toBe(8)
      expect(status?.limit).toBe(20)
    })

    it('returns null when quota data is not available', async () => {
      mockGetQuota.mockResolvedValue(null as unknown as Awaited<ReturnType<typeof subscriptionApi.subscriptionApi.getQuota>>)

      const { result } = renderHook(() => useQuota(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.isLoading).toBe(false)
      })

      expect(result.current.getQuotaStatus('ai_conversations')).toBe(null)
    })

    it('returns default quota for features not in response', async () => {
      const mockQuotaResponse = {
        data: {
          ai_conversations: {
            used: 5,
            limit: 20,
            reset_at: '2025-01-02T00:00:00Z',
          },
          projects: {
            used: 2,
            limit: 3,
          },
        },
      }
      mockGetQuota.mockResolvedValue(mockQuotaResponse.data)

      const { result } = renderHook(() => useQuota(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.quotas).not.toBe(null)
      })

      const status = result.current.getQuotaStatus('material_uploads')
      expect(status).toEqual({
        used: 0,
        limit: 0,
        remaining: 0,
        isUnlimited: false,
        isExceeded: false,
        percentage: 0,
      })
    })
  })

  describe('refreshQuota', () => {
    it('refreshes quota data', async () => {
      const mockQuotaResponse1 = {
        data: {
          ai_conversations: {
            used: 5,
            limit: 20,
            reset_at: '2025-01-02T00:00:00Z',
          },
          projects: {
            used: 2,
            limit: 3,
          },
        },
      }
      const mockQuotaResponse2 = {
        data: {
          ai_conversations: {
            used: 6,
            limit: 20,
            reset_at: '2025-01-02T00:00:00Z',
          },
          projects: {
            used: 2,
            limit: 3,
          },
        },
      }

      mockGetQuota
        .mockResolvedValueOnce(mockQuotaResponse1.data)
        .mockResolvedValueOnce(mockQuotaResponse2.data)

      const { result } = renderHook(() => useQuota(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.quotas).not.toBe(null)
      })

      expect(result.current.quotas!['ai_conversations'].used).toBe(5)
      expect(mockGetQuota).toHaveBeenCalledTimes(1)

      await act(async () => {
        await result.current.refreshQuota()
      })

      await waitFor(() => {
        expect(mockGetQuota).toHaveBeenCalledTimes(2)
      })
    })
  })

  describe('subscription info', () => {
    it('returns isPro status from store', async () => {
      mockIsPro.mockReturnValue(true)

      const mockQuotaResponse = {
        data: {
          ai_conversations: {
            used: 5,
            limit: -1,
            reset_at: '2025-01-02T00:00:00Z',
          },
          projects: {
            used: 2,
            limit: -1,
          },
        },
      }
      mockGetQuota.mockResolvedValue(mockQuotaResponse.data)

      const { result } = renderHook(() => useQuota(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.isLoading).toBe(false)
      })

      expect(result.current.isPro).toBe(true)
    })

    it('returns tier from store', async () => {
      mockGetTier.mockReturnValue('pro')

      const mockQuotaResponse = {
        data: {
          ai_conversations: {
            used: 5,
            limit: -1,
            reset_at: '2025-01-02T00:00:00Z',
          },
          projects: {
            used: 2,
            limit: -1,
          },
        },
      }
      mockGetQuota.mockResolvedValue(mockQuotaResponse.data)

      const { result } = renderHook(() => useQuota(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.isLoading).toBe(false)
      })

      expect(result.current.tier).toBe('pro')
    })
  })

  describe('loading state', () => {
    it('sets isLoading to true while fetching', () => {
      mockGetQuota.mockImplementation(() => new Promise(() => {}))

      const { result } = renderHook(() => useQuota(), {
        wrapper: createWrapper(),
      })

      expect(result.current.isLoading).toBe(true)
    })

    it('sets isLoading to false after successful fetch', async () => {
      const mockQuotaResponse = {
        data: {
          ai_conversations: {
            used: 5,
            limit: 20,
            reset_at: '2025-01-02T00:00:00Z',
          },
          projects: {
            used: 2,
            limit: 3,
          },
        },
      }
      mockGetQuota.mockResolvedValue(mockQuotaResponse.data)

      const { result } = renderHook(() => useQuota(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.isLoading).toBe(false)
      })
    })

    it('sets isLoading to false after error', async () => {
      mockGetQuota.mockRejectedValue(new Error('Network error'))

      const { result } = renderHook(() => useQuota(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.isLoading).toBe(false)
      })
    })
  })

  describe('error handling', () => {
    it('sets error when fetch fails', async () => {
      mockGetQuota.mockRejectedValue(new Error('Network error'))

      const { result } = renderHook(() => useQuota(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.error).not.toBe(null)
      })

      expect(result.current.error?.message).toBe('Network error')
    })

    it('clears error on successful refetch', async () => {
      mockGetQuota.mockRejectedValueOnce(new Error('Network error'))

      const { result } = renderHook(() => useQuota(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.error).not.toBe(null)
      })

      const mockQuotaResponse = {
        data: {
          ai_conversations: {
            used: 5,
            limit: 20,
            reset_at: '2025-01-02T00:00:00Z',
          },
          projects: {
            used: 2,
            limit: 3,
          },
        },
      }
      mockGetQuota.mockResolvedValue(mockQuotaResponse.data)

      await act(async () => {
        await result.current.refreshQuota()
      })

      await waitFor(() => {
        expect(result.current.error).toBe(null)
      })
    })

    it('handles non-Error rejection', async () => {
      mockGetQuota.mockRejectedValue('String error')

      const { result } = renderHook(() => useQuota(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.error).not.toBe(null)
      })
    })
  })

  describe('edge cases', () => {
    it('handles zero limit quota', async () => {
      const mockQuotaResponse = {
        data: {
          ai_conversations: {
            used: 0,
            limit: 0,
            reset_at: '2025-01-02T00:00:00Z',
          },
          projects: {
            used: 0,
            limit: 0,
          },
        },
      }
      mockGetQuota.mockResolvedValue(mockQuotaResponse.data)

      const { result } = renderHook(() => useQuota(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.quotas).not.toBe(null)
      })

      const quotaStatus = result.current.quotas!['ai_conversations']
      expect(quotaStatus.used).toBe(0)
      expect(quotaStatus.limit).toBe(0)
      expect(quotaStatus.remaining).toBe(0)
      expect(quotaStatus.isExceeded).toBe(true) // 0 >= 0 means exceeded when limit is 0
      expect(quotaStatus.percentage).toBeNaN() // 0/0 = NaN
    })

    it('handles empty quota response gracefully', async () => {
      mockGetQuota.mockResolvedValue(null as unknown as Awaited<ReturnType<typeof subscriptionApi.subscriptionApi.getQuota>>)

      const { result } = renderHook(() => useQuota(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.isLoading).toBe(false)
      })

      expect(result.current.quotas).toBe(null)
      expect(result.current.canUseFeature('ai_conversations')).toBe(false)
    })

    it('handles quota with limit of 1', async () => {
      const mockQuotaResponse = {
        data: {
          ai_conversations: {
            used: 0,
            limit: 1,
            reset_at: '2025-01-02T00:00:00Z',
          },
          projects: {
            used: 0,
            limit: 1,
          },
        },
      }
      mockGetQuota.mockResolvedValue(mockQuotaResponse.data)

      const { result } = renderHook(() => useQuota(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.quotas).not.toBe(null)
      })

      const quotaStatus = result.current.quotas!['ai_conversations']
      expect(quotaStatus.remaining).toBe(1)
      expect(quotaStatus.percentage).toBe(0)
      expect(result.current.canUseFeature('ai_conversations')).toBe(true)
    })

    it('handles exceeded quota with limit of 1', async () => {
      const mockQuotaResponse = {
        data: {
          ai_conversations: {
            used: 1,
            limit: 1,
            reset_at: '2025-01-02T00:00:00Z',
          },
          projects: {
            used: 1,
            limit: 1,
          },
        },
      }
      mockGetQuota.mockResolvedValue(mockQuotaResponse.data)

      const { result } = renderHook(() => useQuota(), {
        wrapper: createWrapper(),
      })

      await waitFor(() => {
        expect(result.current.quotas).not.toBe(null)
      })

      const quotaStatus = result.current.quotas!['ai_conversations']
      expect(quotaStatus.remaining).toBe(0)
      expect(quotaStatus.isExceeded).toBe(true)
      expect(quotaStatus.percentage).toBe(100)
      expect(result.current.canUseFeature('ai_conversations')).toBe(false)
    })
  })
})
