/**
 * Tests for Admin API client
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const mockApiCall = vi.fn()
const fetchMock = vi.fn()

const mockApi = {
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
}

vi.mock('../apiClient', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../apiClient')>()
  return {
    ...actual,
    api: mockApi,
    apiCall: mockApiCall,
  }
})

describe('adminApi', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubGlobal('fetch', fetchMock)
    localStorage.clear()
    localStorage.setItem('access_token', 'mock-access-token')
    localStorage.setItem('refresh_token', 'mock-refresh-token')
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
    localStorage.clear()
  })

  // ==================== User Management ====================

  describe('users', () => {
    it('maps backend items/total to users/total and builds pagination/search query', async () => {
      const { adminApi } = await import('../adminApi')
      const items = [
        { id: '1', username: 'user1', email: 'user1@example.com' },
        { id: '2', username: 'user2', email: 'user2@example.com' },
      ]
      mockApi.get.mockResolvedValueOnce({ items, total: 92 })

      const defaultPage = await adminApi.getUsers()
      expect(mockApi.get).toHaveBeenCalledWith('/api/admin/users?skip=0&limit=20')
      expect(defaultPage).toEqual({ users: items, total: 92 })

      mockApi.get.mockResolvedValueOnce({ items: [items[0]], total: 1 })
      const searched = await adminApi.getUsers(10, 50, 'alice@')
      expect(mockApi.get).toHaveBeenLastCalledWith('/api/admin/users?skip=10&limit=50&search=alice%40')
      expect(searched).toEqual({ users: [items[0]], total: 1 })
    })

    it('fetches, updates, and deletes a user and propagates API errors', async () => {
      const { adminApi } = await import('../adminApi')
      const user = { id: 'u/1', username: 'alice', email: 'alice@example.com' }
      mockApi.get.mockResolvedValueOnce(user)
      mockApi.put.mockResolvedValueOnce({ ...user, is_superuser: true })
      mockApi.delete.mockResolvedValueOnce({ message: 'User deleted' })

      expect(await adminApi.getUser('u/1')).toBe(user)
      expect(mockApi.get).toHaveBeenCalledWith('/api/admin/users/u%2F1')

      expect(await adminApi.updateUser('123', { is_superuser: true })).toEqual({ ...user, is_superuser: true })
      expect(mockApi.put).toHaveBeenCalledWith('/api/admin/users/123', { is_superuser: true })

      expect(await adminApi.deleteUser('123')).toEqual({ message: 'User deleted' })
      expect(mockApi.delete).toHaveBeenCalledWith('/api/admin/users/123')

      mockApi.get.mockRejectedValueOnce(new Error('Network error'))
      await expect(adminApi.getUser('123')).rejects.toThrow('Network error')
    })
  })

  // ==================== Prompt Management ====================

  describe('prompts', () => {
    it('encodes project types and forwards CRUD + reload payloads', async () => {
      const { adminApi } = await import('../adminApi')
      const prompt = { id: '1', project_type: 'sci-fi novel', role_definition: 'Writer' }
      const requestBody = {
        role_definition: 'Writer',
        capabilities: 'Drafting',
        directory_structure: 'Standard',
        content_structure: 'Chapters',
        file_types: 'md',
        writing_guidelines: 'Clear prose',
        include_dialogue_guidelines: true,
        is_active: true,
      }
      mockApi.get.mockResolvedValueOnce([prompt]).mockResolvedValueOnce(prompt)
      mockApi.put.mockResolvedValueOnce(prompt)
      mockApi.delete.mockResolvedValueOnce({ message: 'deleted' })
      mockApi.post.mockResolvedValueOnce({ message: 'reloaded', count: 1 })

      expect(await adminApi.getPrompts()).toEqual([prompt])
      expect(mockApi.get).toHaveBeenNthCalledWith(1, '/api/admin/prompts')

      expect(await adminApi.getPrompt('sci-fi novel')).toBe(prompt)
      expect(mockApi.get).toHaveBeenNthCalledWith(2, '/api/admin/prompts/sci-fi%20novel')

      expect(await adminApi.upsertPrompt('sci-fi novel', requestBody)).toBe(prompt)
      expect(mockApi.put).toHaveBeenCalledWith('/api/admin/prompts/sci-fi%20novel', requestBody)

      expect(await adminApi.deletePrompt('sci-fi novel')).toEqual({ message: 'deleted' })
      expect(mockApi.delete).toHaveBeenCalledWith('/api/admin/prompts/sci-fi%20novel')

      expect(await adminApi.reloadPrompts()).toEqual({ message: 'reloaded', count: 1 })
      expect(mockApi.post).toHaveBeenCalledWith('/api/admin/prompts/reload')
    })
  })

  // ==================== Skill Review ====================

  describe('skills', () => {
    it('queries review statuses, forwards moderation decisions, and unwraps resources', async () => {
      const { adminApi } = await import('../adminApi')
      const skills = [{ id: 's1', name: 'Hook Skill', status: 'pending' }]
      const resources = [{ path: 'references/a.md', size: 3, content: 'abc' }]

      mockApi.get
        .mockResolvedValueOnce(skills)
        .mockResolvedValueOnce([])
        .mockResolvedValueOnce({ resources })
        .mockResolvedValueOnce({})
      mockApi.post
        .mockResolvedValueOnce({ message: 'approved', skill_id: 's1' })
        .mockResolvedValueOnce({ message: 'rejected', skill_id: 's1' })
        .mockResolvedValueOnce({ message: 'unpublished', skill_id: 's1' })

      expect(await adminApi.getPendingSkills()).toBe(skills)
      expect(mockApi.get).toHaveBeenNthCalledWith(1, '/api/admin/skills/pending?status=pending')

      expect(await adminApi.getPendingSkills('approved')).toEqual([])
      expect(mockApi.get).toHaveBeenNthCalledWith(2, '/api/admin/skills/pending?status=approved')

      expect(await adminApi.approveSkill('s1')).toEqual({ message: 'approved', skill_id: 's1' })
      expect(mockApi.post).toHaveBeenNthCalledWith(1, '/api/admin/skills/s1/approve')

      expect(await adminApi.rejectSkill('s1', 'Quality issues')).toEqual({ message: 'rejected', skill_id: 's1' })
      expect(mockApi.post).toHaveBeenNthCalledWith(2, '/api/admin/skills/s1/reject', {
        rejection_reason: 'Quality issues',
      })

      expect(await adminApi.unpublishSkill('s1', 'Infringement')).toEqual({ message: 'unpublished', skill_id: 's1' })
      expect(mockApi.post).toHaveBeenNthCalledWith(3, '/api/admin/skills/s1/unpublish', {
        rejection_reason: 'Infringement',
      })

      expect(await adminApi.getSkillReviewResources('s1')).toEqual(resources)
      expect(mockApi.get).toHaveBeenNthCalledWith(3, '/api/admin/skills/s1/resources')
      expect(await adminApi.getSkillReviewResources('s1')).toEqual([])
    })
  })

  // ==================== Codes, Subscriptions, Payment Orders, Plans ====================

  describe('codes, subscriptions, payment orders, and plans', () => {
    it('builds query strings and posts code payloads including multi-use max_uses', async () => {
      const { adminApi } = await import('../adminApi')
      const codesResp = { items: [], total: 0, page: 2, page_size: 10 }
      mockApi.get.mockResolvedValueOnce(codesResp)
      mockApi.post
        .mockResolvedValueOnce({ id: 'c1', code: 'PRO-1' })
        .mockResolvedValueOnce({ codes: ['A'], count: 1, max_uses: 5 })
      mockApi.put.mockResolvedValueOnce({ id: 'c1', is_active: false })

      expect(await adminApi.getCodes({ page: 2, page_size: 10, tier: 'pro', is_active: false })).toBe(codesResp)
      expect(mockApi.get).toHaveBeenCalledWith('/api/admin/codes?page=2&page_size=10&tier=pro&is_active=false')

      await adminApi.createCode({ tier: 'pro', duration_days: 30 })
      expect(mockApi.post).toHaveBeenNthCalledWith(1, '/api/admin/codes', { tier: 'pro', duration_days: 30 })

      const batch = await adminApi.createCodesBatch({
        tier: 'pro',
        duration_days: 30,
        count: 1,
        code_type: 'multi_use',
        max_uses: 5,
      })
      expect(mockApi.post).toHaveBeenNthCalledWith(2, '/api/admin/codes/batch', {
        tier: 'pro',
        duration_days: 30,
        count: 1,
        code_type: 'multi_use',
        max_uses: 5,
      })
      expect(batch.max_uses).toBe(5)

      await adminApi.updateCode('c1', { is_active: false })
      expect(mockApi.put).toHaveBeenCalledWith('/api/admin/codes/c1', { is_active: false })
    })

    it('sends subscription, payment-order, and plan queries directly', async () => {
      const { adminApi } = await import('../adminApi')
      const subsResp = { items: [], total: 0, page: 2, page_size: 20 }
      const ordersResp = { items: [], total: 0, page: 1, page_size: 10 }
      const plansResp = [{ id: 'free', name: 'free', effective_features: { ai_conversations_per_day: 3 } }]

      mockApi.get
        .mockResolvedValueOnce(subsResp)
        .mockResolvedValueOnce(ordersResp)
        .mockResolvedValueOnce(plansResp)
      mockApi.put.mockResolvedValueOnce({ id: 'pro', price_monthly_cents: 3900 })

      expect(await adminApi.getSubscriptions({ page: 2, status: 'active', search: 'alice@' })).toBe(subsResp)
      expect(mockApi.get).toHaveBeenNthCalledWith(1, '/api/admin/subscriptions?page=2&status=active&search=alice%40')

      expect(await adminApi.getPaymentOrders({ user_id: 'user-1', page_size: 10 })).toBe(ordersResp)
      expect(mockApi.get).toHaveBeenNthCalledWith(2, '/api/admin/payment-orders?page_size=10&user_id=user-1')

      expect(await adminApi.getPlans()).toBe(plansResp)
      expect(mockApi.get).toHaveBeenNthCalledWith(3, '/api/admin/plans')

      await adminApi.updatePlan('pro', { price_monthly_cents: 3900 })
      expect(mockApi.put).toHaveBeenCalledWith('/api/admin/plans/pro', { price_monthly_cents: 3900 })
    })
  })

  // ==================== Audit Logs ====================

  describe('audit logs', () => {
    it('passes filter query params and falls back null admin_name to "-"', async () => {
      const { adminApi } = await import('../adminApi')
      mockApi.get.mockResolvedValueOnce({
        items: [
          {
            id: 'log-1',
            admin_id: 'admin-1',
            admin_name: 'super-admin',
            action: 'update',
            resource_type: 'subscription',
            resource_id: 'sub-1',
            old_value: { plan_name: 'free' },
            new_value: { plan_name: 'pro' },
            ip_address: '127.0.0.1',
            user_agent: 'playwright',
            created_at: '2026-03-08T00:00:00Z',
          },
          {
            id: 'log-2',
            admin_id: 'deleted-admin',
            admin_name: null,
            action: 'delete_user',
            resource_type: 'user',
            resource_id: 'u-2',
            old_value: null,
            new_value: null,
            ip_address: null,
            user_agent: null,
            created_at: '2026-03-09T00:00:00Z',
          },
        ],
        total: 22,
        page: 2,
        page_size: 20,
      })

      const result = await adminApi.getAuditLogs({
        page: 2,
        page_size: 20,
        resource_type: 'subscription',
        action: 'update',
      })

      expect(mockApi.get).toHaveBeenCalledWith(
        '/api/admin/audit-logs?page=2&page_size=20&resource_type=subscription&action=update',
      )
      expect(result.items[0].admin_name).toBe('super-admin')
      expect(result.items[1].admin_name).toBe('-')
      expect(result.total).toBe(22)
    })
  })

  // ==================== Dashboard, Funnels, Growth ====================

  describe('dashboard and funnels', () => {
    it('fetches stats and growth endpoints and clamps funnel window days to [1, 90]', async () => {
      const { adminApi } = await import('../adminApi')
      const stats = { total_users: 10 }
      const growth = { days: 14, current: { metrics: { cohort_activation_rate: null, revenue_cents: 19900 } } }
      const funnel = { window_days: 7, steps: [] }
      const conversion = { window_days: 90, channels: [], sources: [] }
      const upgradeFunnel = { window_days: 1, totals: { expose: 0, click: 0, conversion: 0 }, sources: [] }

      mockApi.get
        .mockResolvedValueOnce(stats)
        .mockResolvedValueOnce(growth)
        .mockResolvedValueOnce(funnel)
        .mockResolvedValueOnce(conversion)
        .mockResolvedValueOnce(upgradeFunnel)

      expect(await adminApi.getDashboardStats()).toBe(stats)
      expect(mockApi.get).toHaveBeenNthCalledWith(1, '/api/admin/dashboard/stats')

      expect(await adminApi.getGrowthDashboard(14)).toBe(growth)
      expect(mockApi.get).toHaveBeenNthCalledWith(2, '/api/admin/dashboard/growth?days=14')

      expect(await adminApi.getActivationFunnel(Number.NaN)).toBe(funnel)
      expect(mockApi.get).toHaveBeenNthCalledWith(3, '/api/admin/dashboard/activation-funnel?days=7')

      expect(await adminApi.getUpgradeConversionStats(120)).toBe(conversion)
      expect(mockApi.get).toHaveBeenNthCalledWith(4, '/api/admin/dashboard/upgrade-conversion?days=90')

      expect(await adminApi.getUpgradeFunnelStats(0)).toBe(upgradeFunnel)
      expect(mockApi.get).toHaveBeenNthCalledWith(5, '/api/admin/dashboard/upgrade-funnel?days=1')
    })
  })

  // ==================== Inspirations & Feedback ====================

  describe('inspirations and feedback', () => {
    it('builds optional query strings and forwards review/status mutations', async () => {
      const { adminApi } = await import('../adminApi')
      mockApi.get
        .mockResolvedValueOnce({ items: [], total: 0 })
        .mockResolvedValueOnce({ items: [], total: 1 })
        .mockResolvedValueOnce({ items: [], total: 0 })
        .mockResolvedValueOnce({ items: [], total: 1 })
      mockApi.post.mockResolvedValueOnce({ message: 'ok' })
      mockApi.patch
        .mockResolvedValueOnce({ id: 'ins-1', name: 'Hook updated' })
        .mockResolvedValueOnce({ id: 'fb-1', status: 'processing' })
      mockApi.delete.mockResolvedValueOnce({ message: 'deleted' })

      await adminApi.getInspirations()
      expect(mockApi.get).toHaveBeenNthCalledWith(1, '/api/admin/inspirations')

      await adminApi.getInspirations({ status: 'pending', source: 'community', skip: 20, limit: 20 })
      expect(mockApi.get).toHaveBeenNthCalledWith(
        2,
        '/api/admin/inspirations?status=pending&source=community&skip=20&limit=20',
      )

      expect(await adminApi.reviewInspiration('ins-1', false, 'needs refinement')).toEqual({ message: 'ok' })
      expect(mockApi.post).toHaveBeenCalledWith('/api/admin/inspirations/ins-1/review', {
        approve: false,
        rejection_reason: 'needs refinement',
      })

      expect(await adminApi.updateInspiration('ins-1', { name: 'Hook updated' })).toEqual({
        id: 'ins-1',
        name: 'Hook updated',
      })
      expect(mockApi.patch).toHaveBeenNthCalledWith(1, '/api/admin/inspirations/ins-1', { name: 'Hook updated' })

      expect(await adminApi.deleteInspiration('ins-1')).toEqual({ message: 'deleted' })
      expect(mockApi.delete).toHaveBeenCalledWith('/api/admin/inspirations/ins-1')

      await adminApi.getFeedbackList()
      expect(mockApi.get).toHaveBeenNthCalledWith(3, '/api/admin/feedback')

      await adminApi.getFeedbackList({
        status: 'open',
        source_page: 'editor',
        has_screenshot: false,
        search: 'alice',
        skip: 0,
        limit: 20,
      })
      expect(mockApi.get).toHaveBeenNthCalledWith(
        4,
        '/api/admin/feedback?status=open&source_page=editor&has_screenshot=false&search=alice&skip=0&limit=20',
      )

      expect(await adminApi.updateFeedbackStatus('fb-1', 'processing')).toEqual({
        id: 'fb-1',
        status: 'processing',
      })
      expect(mockApi.patch).toHaveBeenNthCalledWith(2, '/api/admin/feedback/fb-1/status', {
        status: 'processing',
      })
    })

    it('downloads feedback screenshot blob with auth and locale headers', async () => {
      const { adminApi } = await import('../adminApi')
      const { getApiBase } = await import('../apiClient')
      localStorage.setItem('zenstory-language', 'en')

      const screenshotBlob = new Blob(['binary-image'], { type: 'image/png' })
      fetchMock.mockResolvedValue({
        ok: true,
        status: 200,
        blob: vi.fn().mockResolvedValue(screenshotBlob),
      })

      const result = await adminApi.getFeedbackScreenshotBlob('fb-1')

      expect(fetchMock).toHaveBeenCalledWith(
        `${getApiBase()}/api/admin/feedback/fb-1/screenshot`,
        {
          method: 'GET',
          headers: {
            Authorization: 'Bearer mock-access-token',
            'Accept-Language': 'en',
          },
        },
      )
      expect(result).toBe(screenshotBlob)
    })

    it('retries once on 401 when refresh token succeeds', async () => {
      const { adminApi } = await import('../adminApi')
      const screenshotBlob = new Blob(['retry-image'], { type: 'image/png' })

      fetchMock
        .mockResolvedValueOnce({
          ok: false,
          status: 401,
          json: vi.fn().mockResolvedValue({ detail: 'ERR_UNAUTHORIZED' }),
        })
        .mockResolvedValueOnce({
          ok: true,
          status: 200,
          json: vi.fn().mockResolvedValue({
            access_token: 'rotated-access-token',
            refresh_token: 'rotated-refresh-token',
            user: { id: 'admin-1' },
          }),
        })
        .mockResolvedValueOnce({
          ok: true,
          status: 200,
          blob: vi.fn().mockResolvedValue(screenshotBlob),
        })

      const result = await adminApi.getFeedbackScreenshotBlob('fb-2')

      expect(fetchMock).toHaveBeenCalledTimes(3)
      expect(fetchMock.mock.calls[1]?.[0]).toContain('/api/auth/refresh')
      expect(fetchMock.mock.calls[2]?.[1]).toMatchObject({
        headers: expect.objectContaining({ Authorization: 'Bearer rotated-access-token' }),
      })
      expect(result).toBe(screenshotBlob)
    })

    it('throws ApiError with resolved backend error code on JSON failure', async () => {
      const { adminApi } = await import('../adminApi')

      fetchMock.mockResolvedValue({
        ok: false,
        status: 404,
        json: vi.fn().mockResolvedValue({ detail: 'ERR_FEEDBACK_NOT_FOUND' }),
      })

      await expect(adminApi.getFeedbackScreenshotBlob('fb-missing')).rejects.toMatchObject({
        name: 'ApiError',
        status: 404,
        errorCode: 'ERR_FEEDBACK_NOT_FOUND',
        rawMessage: 'ERR_FEEDBACK_NOT_FOUND',
      })
    })

    it('uses apiErrorFromPayload instead of fabricating ERR_INTERNAL_SERVER_ERROR on non-JSON 403/429', async () => {
      const { adminApi } = await import('../adminApi')

      fetchMock.mockResolvedValue({
        ok: false,
        status: 429,
        json: vi.fn().mockRejectedValue(new Error('invalid json')),
      })

      await expect(adminApi.getFeedbackScreenshotBlob('fb-rate-limited')).rejects.toMatchObject({
        name: 'ApiError',
        status: 429,
        rawMessage: 'API error: 429',
        errorCode: undefined,
      })
    })
  })

  // ==================== Points, Check-in, Referrals, Quotas ====================

  describe('points, check-in, referrals, and quotas', () => {
    it('routes points, check-in, referral, and quota endpoints with expected query params', async () => {
      const { adminApi } = await import('../adminApi')
      const pointsStats = { total_points_issued: 20 }
      const userPoints = { user_id: 'u-1', available: 100 }
      const txResp = { items: [], total: 9, page: 2, page_size: 20 }
      const checkInStats = { today_count: 4, streak_distribution: { 7: 3 } }
      const checkInRecords = { items: [], total: 22, page: 1, page_size: 20 }
      const referralStats = { total_codes: 8, total_points_awarded: 40 }
      const inviteCodes = { items: [], total: 1, page: 1, page_size: 20 }
      const rewards = { items: [], total: 1, page: 1, page_size: 20 }
      const quotaStats = { material_decompositions: 4, inspiration_copies: 2, skills_created: 1 }
      const userQuota = { user_id: 'u-1', custom_skills: { used: 2, limit: -1, reset_at: null } }

      mockApi.get
        .mockResolvedValueOnce(pointsStats)
        .mockResolvedValueOnce(userPoints)
        .mockResolvedValueOnce(txResp)
        .mockResolvedValueOnce(checkInStats)
        .mockResolvedValueOnce(checkInRecords)
        .mockResolvedValueOnce(referralStats)
        .mockResolvedValueOnce(inviteCodes)
        .mockResolvedValueOnce(rewards)
        .mockResolvedValueOnce(quotaStats)
        .mockResolvedValueOnce(userQuota)
      mockApi.post
        .mockResolvedValueOnce({ message: 'ok', new_balance: 80 })
        .mockResolvedValueOnce({ id: 'c2', code: 'WXYZ-5678' })

      expect(await adminApi.getPointsStats()).toBe(pointsStats)
      expect(await adminApi.getUserPoints('u-1')).toBe(userPoints)
      expect(await adminApi.getUserPointsTransactions('u-1', { page: 2, page_size: 20 })).toBe(txResp)
      expect(await adminApi.adjustUserPoints('u-1', { amount: -20, reason: 'manual correction' })).toEqual({
        message: 'ok',
        new_balance: 80,
      })

      expect(await adminApi.getCheckInStats()).toBe(checkInStats)
      expect(await adminApi.getCheckInRecords({ page: 1, page_size: 20, user_id: 'u-1' })).toBe(checkInRecords)

      expect(await adminApi.getReferralStats()).toBe(referralStats)
      expect(await adminApi.createAdminInviteCode()).toEqual({ id: 'c2', code: 'WXYZ-5678' })
      expect(await adminApi.getInviteCodes({ page: 1, page_size: 20, is_active: true })).toBe(inviteCodes)
      expect(await adminApi.getReferralRewards({ page: 1, page_size: 20 })).toBe(rewards)

      expect(await adminApi.getQuotaUsageStats()).toBe(quotaStats)
      expect(await adminApi.getUserQuota('u-1')).toBe(userQuota)

      expect(mockApi.get.mock.calls.map(([url]) => url)).toEqual([
        '/api/admin/points/stats',
        '/api/admin/points/u-1',
        '/api/admin/points/u-1/transactions?page=2&page_size=20',
        '/api/admin/check-in/stats',
        '/api/admin/check-in/records?page=1&page_size=20&user_id=u-1',
        '/api/admin/referrals/stats',
        '/api/admin/invites?page=1&page_size=20&is_active=true',
        '/api/admin/referrals/rewards?page=1&page_size=20',
        '/api/admin/quota/usage',
        '/api/admin/quota/u-1',
      ])
      expect(mockApi.post).toHaveBeenNthCalledWith(1, '/api/admin/points/u-1/adjust', {
        amount: -20,
        reason: 'manual correction',
      })
      expect(mockApi.post).toHaveBeenNthCalledWith(2, '/api/admin/invites')
    })
  })

  // ==================== Backend Pydantic Contract Shapes ====================

  describe('backend schema contract regressions', () => {
    it('accepts exact FastAPI response shapes including nullable and omitted fields', async () => {
      const { adminApi } = await import('../adminApi')

      const subscriptionDetail = {
        subscription: {
          id: 'sub-1',
          user_id: 'user-1',
          plan_id: 'plan-pro',
          status: 'active',
          current_period_start: '2026-03-01T00:00:00Z',
          current_period_end: '2099-01-01T00:00:00Z',
          cancel_at_period_end: false,
          stripe_subscription_id: null,
          stripe_customer_id: null,
          created_at: '2026-03-01T00:00:00Z',
          updated_at: '2026-03-01T00:00:00Z',
        },
        plan: {
          id: 'plan-pro',
          name: 'pro' as const,
          display_name: '专业版',
          display_name_en: 'Pro',
          price_monthly_cents: 2900,
          price_yearly_cents: 29000,
          features: { ai_conversations_per_day: -1 },
          is_active: true,
          created_at: '2026-03-01T00:00:00Z',
          updated_at: '2026-03-01T00:00:00Z',
        },
      }
      const promptWithNulls = {
        id: 'prompt-1',
        project_type: 'novel',
        role_definition: 'Writer',
        capabilities: 'Drafting',
        directory_structure: null,
        content_structure: null,
        file_types: null,
        writing_guidelines: null,
        include_dialogue_guidelines: false,
        primary_content_type: null,
        is_active: true,
        version: 1,
        created_by: null,
        updated_by: null,
        created_at: '2026-03-01T00:00:00Z',
        updated_at: '2026-03-01T00:00:00Z',
      }
      const pointsTxWithoutUsername = {
        items: [{
          id: 'tx-1',
          user_id: 'user-1',
          amount: 10,
          balance_after: 120,
          transaction_type: 'check_in',
          source_id: null,
          description: null,
          expires_at: null,
          is_expired: false,
          expired_at: null,
          created_at: '2026-03-01T00:00:00Z',
        }],
        total: 1,
        page: 1,
        page_size: 10,
      }
      const codeWithoutUpdatedAt = {
        items: [{
          id: 'code-1',
          code: 'PRO-30D-ABCD',
          tier: 'pro',
          duration_days: 30,
          code_type: 'single_use',
          max_uses: 1,
          current_uses: 0,
          is_active: true,
          notes: null,
          created_at: '2026-03-01T00:00:00Z',
        }],
        total: 1,
        page: 1,
        page_size: 20,
      }
      const batchAuditLog = {
        items: [{
          id: 'log-batch',
          admin_id: 'admin-1',
          admin_name: null,
          action: 'create_codes_batch',
          resource_type: 'code',
          resource_id: null,
          old_value: null,
          new_value: { count: 10 },
          ip_address: null,
          user_agent: null,
          created_at: '2026-03-01T00:00:00Z',
        }],
        total: 1,
        page: 1,
        page_size: 20,
      }
      const adjustResponse = {
        success: true,
        transaction: pointsTxWithoutUsername.items[0],
        old_balance: 110,
        new_balance: 120,
      }

      mockApi.get
        .mockResolvedValueOnce(subscriptionDetail)
        .mockResolvedValueOnce(promptWithNulls)
        .mockResolvedValueOnce(pointsTxWithoutUsername)
        .mockResolvedValueOnce(codeWithoutUpdatedAt)
        .mockResolvedValueOnce(batchAuditLog)
      mockApi.post.mockResolvedValueOnce(adjustResponse)

      const subResult = await adminApi.getUserSubscription('user-1')
      expect(subResult.plan?.name).toBe('pro')
      expect(subResult.quota).toBeUndefined()

      const promptResult = await adminApi.getPrompt('novel')
      expect(promptResult.directory_structure).toBeNull()

      const txResult = await adminApi.getUserPointsTransactions('user-1', { page: 1, page_size: 10 })
      expect(txResult.items[0].username).toBeUndefined()

      const codesResult = await adminApi.getCodes()
      expect(codesResult.items[0].updated_at).toBeUndefined()

      const auditResult = await adminApi.getAuditLogs()
      expect(auditResult.items[0].admin_name).toBe('-')
      expect(auditResult.items[0].resource_id).toBeNull()

      const adjustResult = await adminApi.adjustUserPoints('user-1', { amount: 10, reason: 'bonus' })
      expect(adjustResult.success).toBe(true)
      expect(adjustResult.new_balance).toBe(120)
    })
  })

  // ==================== Export Surface ====================

  describe('adminApi export', () => {
    it('exports adminApi without removed getCode or top-level function duplicates', async () => {
      const adminModule = await import('../adminApi')

      expect(adminModule.adminApi.getUsers).toBeDefined()
      expect(adminModule.adminApi.getPrompts).toBeDefined()
      expect(adminModule.adminApi.getPendingSkills).toBeDefined()
      expect(adminModule.adminApi.getCodes).toBeDefined()
      expect('getCode' in adminModule.adminApi).toBe(false)
      expect('getUsers' in adminModule).toBe(false)
    })
  })
})
