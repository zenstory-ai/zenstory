import { describe, it, expect, vi, beforeEach } from 'vitest'
import { subscriptionApi } from '../subscriptionApi'
import * as apiClient from '../apiClient'

// Mock the apiClient but keep ApiError from the original
vi.mock('../apiClient', async (importOriginal) => {
  const actual = await importOriginal<typeof apiClient>()
  return {
    ...actual,
    api: {
      get: vi.fn(),
      post: vi.fn(),
      put: vi.fn(),
      delete: vi.fn(),
      patch: vi.fn(),
    },
  }
})

describe('subscriptionApi', () => {
  const mockApiGet = vi.mocked(apiClient.api.get)
  const mockApiPost = vi.mocked(apiClient.api.post)

  beforeEach(() => {
    vi.clearAllMocks()
  })

  describe('getStatus', () => {
    it('calls correct endpoint', async () => {
      const mockStatus = {
        tier: 'pro' as const,
        status: 'active' as const,
        display_name: 'Pro',
        current_period_end: '2025-12-31T00:00:00Z',
        days_remaining: 30,
        features: {
          ai_conversations_per_day: -1,
          context_window_tokens: 200000,
          file_versions_per_file: 100,
          max_projects: -1,
          export_formats: ['docx', 'txt', 'md'],
          custom_prompts: true,
          materialUploads: -1,
          materialDecompositions: 50,
          customSkills: -1,
          publicSkillsAccess: 'full' as const,
          inspirationCopiesMonthly: -1,
          featuredInspirationAccess: 'immediate' as const,
          prioritySupport: true,
          apiAccess: true,
        },
      }
      mockApiGet.mockResolvedValue(mockStatus)

      const result = await subscriptionApi.getStatus()

      expect(mockApiGet).toHaveBeenCalledWith('/api/v1/subscription/me')
      expect(result).toEqual(mockStatus)
    })

    it('returns free tier status', async () => {
      const mockFreeStatus = {
        tier: 'free' as const,
        status: 'active' as const,
        display_name: '免费版',
        current_period_end: null,
        days_remaining: null,
        features: {
          ai_conversations_per_day: 10,
          context_window_tokens: 50000,
          file_versions_per_file: 10,
          max_projects: 3,
          export_formats: ['txt'],
          custom_prompts: false,
          materialUploads: 10,
          materialDecompositions: 5,
          customSkills: 0,
          publicSkillsAccess: 'basic' as const,
          inspirationCopiesMonthly: 5,
          featuredInspirationAccess: 'delayed' as const,
          prioritySupport: false,
          apiAccess: false,
        },
      }
      mockApiGet.mockResolvedValue(mockFreeStatus)

      const result = await subscriptionApi.getStatus()

      expect(result.tier).toBe('free')
      expect(result.days_remaining).toBeNull()
    })

    it('propagates errors from API', async () => {
      const error = new Error('Network error')
      mockApiGet.mockRejectedValue(error)

      await expect(subscriptionApi.getStatus()).rejects.toThrow('Network error')
    })

    it('handles unauthorized error', async () => {
      const error = new apiClient.ApiError(401, 'Unauthorized')
      mockApiGet.mockRejectedValue(error)

      await expect(subscriptionApi.getStatus()).rejects.toThrow('Unauthorized')
    })
  })

  describe('getQuota', () => {
    it('calls correct endpoint', async () => {
      const mockQuota = {
        ai_conversations: {
          used: 5,
          limit: 20,
          reset_at: '2025-01-02T00:00:00Z',
        },
        projects: {
          used: 2,
          limit: 3,
        },
      }
      mockApiGet.mockResolvedValue(mockQuota)

      const result = await subscriptionApi.getQuota()

      expect(mockApiGet).toHaveBeenCalledWith('/api/v1/subscription/quota')
      expect(result).toEqual(mockQuota)
    })

    it('returns unlimited quota for pro users', async () => {
      const mockQuota = {
        ai_conversations: {
          used: 50,
          limit: -1,
          reset_at: '2025-01-02T00:00:00Z',
        },
        projects: {
          used: 10,
          limit: -1,
        },
      }
      mockApiGet.mockResolvedValue(mockQuota)

      const result = await subscriptionApi.getQuota()

      expect(result.ai_conversations.limit).toBe(-1)
      expect(result.projects.limit).toBe(-1)
    })

    it('propagates errors from API', async () => {
      const error = new Error('Quota service unavailable')
      mockApiGet.mockRejectedValue(error)

      await expect(subscriptionApi.getQuota()).rejects.toThrow('Quota service unavailable')
    })
  })

  describe('redeemCode', () => {
    it('calls correct endpoint with code payload', async () => {
      const mockResponse = {
        success: true,
        message: 'Code redeemed successfully',
        subscription: {
          id: 'sub-1',
          user_id: 'user-1',
          plan_id: 'plan-pro',
          status: 'active' as const,
          current_period_start: '2025-01-01T00:00:00Z',
          current_period_end: '2025-02-01T00:00:00Z',
          cancel_at_period_end: false,
          created_at: '2025-01-01T00:00:00Z',
          updated_at: '2025-01-01T00:00:00Z',
        },
      }
      mockApiPost.mockResolvedValue(mockResponse)

      const result = await subscriptionApi.redeemCode('PRO-CODE-123')

      expect(mockApiPost).toHaveBeenCalledWith('/api/v1/subscription/redeem', { code: 'PRO-CODE-123' })
      expect(result).toEqual(mockResponse)
    })

    it('handles invalid code response', async () => {
      const mockResponse = {
        success: false,
        message: 'Invalid or expired code',
      }
      mockApiPost.mockResolvedValue(mockResponse)

      const result = await subscriptionApi.redeemCode('INVALID-CODE')

      expect(result.success).toBe(false)
      expect(result.message).toBe('Invalid or expired code')
      expect(result.subscription).toBeUndefined()
    })

    it('includes attribution source when provided', async () => {
      mockApiPost.mockResolvedValue({
        success: true,
        message: 'Code redeemed successfully',
      })

      await subscriptionApi.redeemCode('PRO-CODE-123', 'chat_quota_blocked')

      expect(mockApiPost).toHaveBeenCalledWith('/api/v1/subscription/redeem', {
        code: 'PRO-CODE-123',
        source: 'chat_quota_blocked',
      })
    })

    it('handles already redeemed code', async () => {
      const error = new apiClient.ApiError(400, 'Code already redeemed')
      mockApiPost.mockRejectedValue(error)

      await expect(subscriptionApi.redeemCode('USED-CODE')).rejects.toThrow('Code already redeemed')
    })

    it('propagates errors from API', async () => {
      const error = new Error('Redemption failed')
      mockApiPost.mockRejectedValue(error)

      await expect(subscriptionApi.redeemCode('ANY-CODE')).rejects.toThrow('Redemption failed')
    })
  })

  describe('getHistory', () => {
    it('calls correct endpoint with default limit', async () => {
      const mockHistory = [
        {
          id: 'hist-1',
          user_id: 'user-1',
          action: 'created' as const,
          plan_name: 'free',
          start_date: '2025-01-01T00:00:00Z',
          end_date: null,
          metadata: {},
          created_at: '2025-01-01T00:00:00Z',
        },
      ]
      mockApiGet.mockResolvedValue(mockHistory)

      const result = await subscriptionApi.getHistory()

      expect(mockApiGet).toHaveBeenCalledWith('/api/v1/subscription/history?limit=50')
      expect(result).toEqual(mockHistory)
    })

    it('calls correct endpoint with custom limit', async () => {
      mockApiGet.mockResolvedValue([])

      await subscriptionApi.getHistory(10)

      expect(mockApiGet).toHaveBeenCalledWith('/api/v1/subscription/history?limit=10')
    })

    it('returns empty array when no history', async () => {
      mockApiGet.mockResolvedValue([])

      const result = await subscriptionApi.getHistory()

      expect(result).toEqual([])
    })

    it('handles multiple history entries with different actions', async () => {
      const mockHistory = [
        {
          id: 'hist-1',
          user_id: 'user-1',
          action: 'created' as const,
          plan_name: 'free',
          start_date: '2025-01-01T00:00:00Z',
          end_date: null,
          metadata: {},
          created_at: '2025-01-01T00:00:00Z',
        },
        {
          id: 'hist-2',
          user_id: 'user-1',
          action: 'upgraded' as const,
          plan_name: 'pro',
          start_date: '2025-01-15T00:00:00Z',
          end_date: '2025-02-15T00:00:00Z',
          metadata: { code: 'PRO-CODE' },
          created_at: '2025-01-15T00:00:00Z',
        },
        {
          id: 'hist-3',
          user_id: 'user-1',
          action: 'expired' as const,
          plan_name: 'pro',
          start_date: '2025-02-15T00:00:00Z',
          end_date: '2025-02-15T00:00:00Z',
          metadata: {},
          created_at: '2025-02-15T00:00:00Z',
        },
      ]
      mockApiGet.mockResolvedValue(mockHistory)

      const result = await subscriptionApi.getHistory()

      expect(result).toHaveLength(3)
      expect(result[0].action).toBe('created')
      expect(result[1].action).toBe('upgraded')
      expect(result[2].action).toBe('expired')
    })

    it('propagates errors from API', async () => {
      const error = new Error('History unavailable')
      mockApiGet.mockRejectedValue(error)

      await expect(subscriptionApi.getHistory()).rejects.toThrow('History unavailable')
    })
  })

  describe('API Integration', () => {
    it('all methods return promises', () => {
      mockApiGet.mockResolvedValue({})
      mockApiPost.mockResolvedValue({})

      expect(subscriptionApi.getStatus()).toBeInstanceOf(Promise)
      expect(subscriptionApi.getQuota()).toBeInstanceOf(Promise)
      expect(subscriptionApi.redeemCode('TEST')).toBeInstanceOf(Promise)
      expect(subscriptionApi.getHistory()).toBeInstanceOf(Promise)
    })

    it('uses correct HTTP methods for each operation', async () => {
      mockApiGet.mockResolvedValue({})
      mockApiPost.mockResolvedValue({})

      await subscriptionApi.getStatus()
      expect(mockApiGet).toHaveBeenCalled()

      await subscriptionApi.getQuota()
      expect(mockApiGet).toHaveBeenCalledTimes(2)

      await subscriptionApi.redeemCode('CODE')
      expect(mockApiPost).toHaveBeenCalled()

      await subscriptionApi.getHistory()
      expect(mockApiGet).toHaveBeenCalledTimes(3)
    })
  })
})
