/**
 * Tests for Admin API client
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

// Mock apiClient BEFORE importing adminApi
const mockApiCall = vi.fn()
const mockGetAccessToken = vi.fn()
const mockGetApiBase = vi.fn()
const mockTryRefreshToken = vi.fn()
const fetchMock = vi.fn()

class MockApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

const mockApi = {
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
}

vi.mock('../apiClient', () => ({
  api: mockApi,
  apiCall: mockApiCall,
  ApiError: MockApiError,
  getAccessToken: mockGetAccessToken,
  getApiBase: mockGetApiBase,
  resolveOwnedAuthSession: () => ({
    accessToken: mockGetAccessToken(), refreshToken: localStorage.getItem('refresh_token'),
  }),
  tryRefreshToken: mockTryRefreshToken,
}))

describe('adminApi', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubGlobal('fetch', fetchMock)
    mockGetAccessToken.mockReturnValue('mock-access-token')
    mockGetApiBase.mockReturnValue('https://api.example.com')
    mockTryRefreshToken.mockResolvedValue(false)
    localStorage.clear()
    localStorage.setItem('access_token', 'mock-access-token')
    localStorage.setItem('refresh_token', 'mock-refresh-token')
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
    localStorage.clear()
  })

  // ==================== User Management Tests ====================

  describe('getUsers', () => {
    it('returns the page items and the filtered total', async () => {
      const { getUsers } = await import('../adminApi')
      const mockUsers = [
        { id: '1', username: 'user1', email: 'user1@example.com' },
        { id: '2', username: 'user2', email: 'user2@example.com' },
      ]
      mockApi.get.mockResolvedValue({ items: mockUsers, total: 92 })

      const result = await getUsers()

      expect(mockApi.get).toHaveBeenCalledWith('/api/admin/users?skip=0&limit=20')
      expect(result.total).toBe(92)
      expect(result.users).toHaveLength(2)
      expect(result.users[0]).toMatchObject(mockUsers[0])
      expect(result.users[1]).toMatchObject(mockUsers[1])
    })

    it('fetches users with custom skip and limit', async () => {
      const { getUsers } = await import('../adminApi')
      const mockUsers = [{ id: '1', username: 'user1' }]
      mockApi.get.mockResolvedValue({ items: mockUsers, total: 11 })

      const result = await getUsers(10, 50)

      expect(mockApi.get).toHaveBeenCalledWith('/api/admin/users?skip=10&limit=50')
      expect(result.users).toHaveLength(1)
      expect(result.users[0]).toMatchObject(mockUsers[0])
      expect(result.total).toBe(11)
    })

    it('fetches users with search parameter', async () => {
      const { getUsers } = await import('../adminApi')
      const mockUsers = [{ id: '1', username: 'testuser' }]
      mockApi.get.mockResolvedValue({ items: mockUsers, total: 1 })

      const result = await getUsers(0, 20, 'testuser')

      expect(mockApi.get).toHaveBeenCalledWith('/api/admin/users?skip=0&limit=20&search=testuser')
      expect(result.users).toHaveLength(1)
      expect(result.users[0]).toMatchObject(mockUsers[0])
      expect(result.total).toBe(1)
    })

    it('reports an unknown total for a bare array from an older API', async () => {
      const { getUsers } = await import('../adminApi')
      const mockUsers = [{ id: '1', username: 'user1' }]
      mockApi.get.mockResolvedValue(mockUsers)

      const result = await getUsers()

      expect(result.users).toHaveLength(1)
      expect(result.total).toBeNull()
    })

    it('handles empty users list', async () => {
      const { getUsers } = await import('../adminApi')
      mockApi.get.mockResolvedValue({ items: [], total: 0 })

      const result = await getUsers()

      expect(result).toEqual({ users: [], total: 0 })
    })

    it('propagates API errors', async () => {
      const { getUsers } = await import('../adminApi')
      const error = new Error('API Error')
      mockApi.get.mockRejectedValue(error)

      await expect(getUsers()).rejects.toThrow('API Error')
    })
  })

  describe('getUser', () => {
    it('fetches single user by ID', async () => {
      const { getUser } = await import('../adminApi')
      const mockUser = { id: '123', username: 'testuser', email: 'test@example.com' }
      mockApi.get.mockResolvedValue(mockUser)

      const result = await getUser('123')

      expect(mockApi.get).toHaveBeenCalledWith('/api/admin/users/123')
      expect(result).toMatchObject(mockUser)
    })

    it('handles user not found error', async () => {
      const { getUser } = await import('../adminApi')
      const error = new Error('User not found')
      mockApi.get.mockRejectedValue(error)

      await expect(getUser('999')).rejects.toThrow('User not found')
    })

    it('propagates API errors', async () => {
      const { getUser } = await import('../adminApi')
      const error = new Error('Network error')
      mockApi.get.mockRejectedValue(error)

      await expect(getUser('123')).rejects.toThrow('Network error')
    })
  })

  describe('updateUser', () => {
    it('updates user with partial data', async () => {
      const { updateUser } = await import('../adminApi')
      const mockUser = { id: '123', username: 'updateduser', email: 'updated@example.com' }
      const updateData = { username: 'updateduser' }
      mockApi.put.mockResolvedValue(mockUser)

      const result = await updateUser('123', updateData)

      expect(mockApi.put).toHaveBeenCalledWith('/api/admin/users/123', updateData)
      expect(result).toEqual(mockUser)
    })

    it('updates user role', async () => {
      const { updateUser } = await import('../adminApi')
      const mockUser = { id: '123', username: 'user', is_superuser: true }
      const updateData = { is_superuser: true }
      mockApi.put.mockResolvedValue(mockUser)

      const result = await updateUser('123', updateData)

      expect(mockApi.put).toHaveBeenCalledWith('/api/admin/users/123', updateData)
      expect(result).toEqual(mockUser)
    })

    it('updates user active status', async () => {
      const { updateUser } = await import('../adminApi')
      const mockUser = { id: '123', username: 'user', is_active: false }
      const updateData = { is_active: false }
      mockApi.put.mockResolvedValue(mockUser)

      const result = await updateUser('123', updateData)

      expect(mockApi.put).toHaveBeenCalledWith('/api/admin/users/123', updateData)
      expect(result).toEqual(mockUser)
    })

    it('handles update error', async () => {
      const { updateUser } = await import('../adminApi')
      const error = new Error('Update failed')
      mockApi.put.mockRejectedValue(error)

      await expect(updateUser('123', { username: 'new' })).rejects.toThrow('Update failed')
    })
  })

  describe('deleteUser', () => {
    it('deletes user by ID', async () => {
      const { deleteUser } = await import('../adminApi')
      const response = { message: 'User deleted successfully' }
      mockApi.delete.mockResolvedValue(response)

      const result = await deleteUser('123')

      expect(mockApi.delete).toHaveBeenCalledWith('/api/admin/users/123')
      expect(result).toEqual(response)
    })

    it('handles delete error', async () => {
      const { deleteUser } = await import('../adminApi')
      const error = new Error('Delete failed')
      mockApi.delete.mockRejectedValue(error)

      await expect(deleteUser('123')).rejects.toThrow('Delete failed')
    })
  })

  // ==================== Prompt Management Tests ====================

  describe('getPrompts', () => {
    it('fetches all prompts', async () => {
      const { getPrompts } = await import('../adminApi')
      const mockPrompts = [
        {
          id: '1',
          project_type: 'novel',
          role_definition: 'Novel writer',
          capabilities: 'Writing',
          is_active: true,
        },
        {
          id: '2',
          project_type: 'thesis',
          role_definition: 'Thesis writer',
          capabilities: 'Academic writing',
          is_active: false,
        },
      ]
      mockApi.get.mockResolvedValue(mockPrompts)

      const result = await getPrompts()

      expect(mockApi.get).toHaveBeenCalledWith('/api/admin/prompts')
      expect(result).toHaveLength(2)
      expect(result[0]).toMatchObject(mockPrompts[0])
      expect(result[1]).toMatchObject(mockPrompts[1])
    })

    it('handles empty prompts list', async () => {
      const { getPrompts } = await import('../adminApi')
      mockApi.get.mockResolvedValue([])

      const result = await getPrompts()

      expect(result).toEqual([])
    })
  })

  describe('getPrompt', () => {
    it('fetches prompt by project type', async () => {
      const { getPrompt } = await import('../adminApi')
      const mockPrompt = {
        id: '1',
        project_type: 'novel',
        role_definition: 'Novel writer',
        capabilities: 'Writing',
        directory_structure: 'Standard',
        content_structure: 'Chapters',
        file_types: 'txt,md',
        writing_guidelines: 'Style guide',
        include_dialogue_guidelines: true,
        primary_content_type: 'novel',
        is_active: true,
        version: 1,
      }
      mockApi.get.mockResolvedValue(mockPrompt)

      const result = await getPrompt('novel')

      expect(mockApi.get).toHaveBeenCalledWith('/api/admin/prompts/novel')
      expect(result).toEqual(mockPrompt)
    })

    it('encodes project type for special characters', async () => {
      const { getPrompt } = await import('../adminApi')
      const mockPrompt = { id: '1', project_type: 'sci-fi novel' }
      mockApi.get.mockResolvedValue(mockPrompt)

      await getPrompt('sci-fi novel')

      expect(mockApi.get).toHaveBeenCalledWith('/api/admin/prompts/sci-fi%20novel')
    })

    it('handles prompt not found', async () => {
      const { getPrompt } = await import('../adminApi')
      const error = new Error('Prompt not found')
      mockApi.get.mockRejectedValue(error)

      await expect(getPrompt('unknown')).rejects.toThrow('Prompt not found')
    })
  })

  describe('upsertPrompt', () => {
    it('creates new prompt', async () => {
      const { upsertPrompt } = await import('../adminApi')
      const mockPrompt = {
        id: '1',
        project_type: 'novel',
        role_definition: 'Novel writer',
        is_active: true,
      }
      const promptData = {
        role_definition: 'Novel writer',
        capabilities: 'Writing',
        directory_structure: 'Standard',
        content_structure: 'Chapters',
        file_types: 'txt,md',
        writing_guidelines: 'Style guide',
        include_dialogue_guidelines: true,
        primary_content_type: 'novel',
        is_active: true,
      }
      mockApi.put.mockResolvedValue(mockPrompt)

      const result = await upsertPrompt('novel', promptData)

      expect(mockApi.put).toHaveBeenCalledWith('/api/admin/prompts/novel', promptData)
      expect(result).toEqual(mockPrompt)
    })

    it('updates existing prompt', async () => {
      const { upsertPrompt } = await import('../adminApi')
      const mockPrompt = {
        id: '1',
        project_type: 'novel',
        role_definition: 'Updated writer',
        is_active: true,
      }
      const promptData = {
        role_definition: 'Updated writer',
        capabilities: 'Writing',
        directory_structure: 'Standard',
        content_structure: 'Chapters',
        file_types: 'txt,md',
        writing_guidelines: 'Style guide',
        include_dialogue_guidelines: false,
        primary_content_type: 'novel',
        is_active: true,
      }
      mockApi.put.mockResolvedValue(mockPrompt)

      const result = await upsertPrompt('novel', promptData)

      expect(mockApi.put).toHaveBeenCalledWith('/api/admin/prompts/novel', promptData)
      expect(result).toEqual(mockPrompt)
    })

    it('encodes project type for special characters', async () => {
      const { upsertPrompt } = await import('../adminApi')
      const mockPrompt = { id: '1', project_type: 'sci-fi novel' }
      const promptData = {
        role_definition: 'Sci-fi writer',
        capabilities: 'Writing',
        directory_structure: 'Standard',
        content_structure: 'Chapters',
        file_types: 'txt,md',
        writing_guidelines: 'Style guide',
        include_dialogue_guidelines: true,
        primary_content_type: 'scifi',
        is_active: true,
      }
      mockApi.put.mockResolvedValue(mockPrompt)

      await upsertPrompt('sci-fi novel', promptData)

      expect(mockApi.put).toHaveBeenCalledWith('/api/admin/prompts/sci-fi%20novel', promptData)
    })

    it('handles upsert error', async () => {
      const { upsertPrompt } = await import('../adminApi')
      const error = new Error('Upsert failed')
      const promptData = {
        role_definition: 'Writer',
        capabilities: 'Writing',
        directory_structure: 'Standard',
        content_structure: 'Chapters',
        file_types: 'txt,md',
        writing_guidelines: 'Style guide',
        include_dialogue_guidelines: true,
        primary_content_type: 'novel',
        is_active: true,
      }
      mockApi.put.mockRejectedValue(error)

      await expect(upsertPrompt('novel', promptData)).rejects.toThrow('Upsert failed')
    })
  })

  describe('deletePrompt', () => {
    it('deletes prompt by project type', async () => {
      const { deletePrompt } = await import('../adminApi')
      const response = { message: 'Prompt deleted successfully' }
      mockApi.delete.mockResolvedValue(response)

      const result = await deletePrompt('novel')

      expect(mockApi.delete).toHaveBeenCalledWith('/api/admin/prompts/novel')
      expect(result).toEqual(response)
    })

    it('encodes project type for special characters', async () => {
      const { deletePrompt } = await import('../adminApi')
      const response = { message: 'Prompt deleted' }
      mockApi.delete.mockResolvedValue(response)

      await deletePrompt('sci-fi novel')

      expect(mockApi.delete).toHaveBeenCalledWith('/api/admin/prompts/sci-fi%20novel')
    })

    it('handles delete error', async () => {
      const { deletePrompt } = await import('../adminApi')
      const error = new Error('Delete failed')
      mockApi.delete.mockRejectedValue(error)

      await expect(deletePrompt('novel')).rejects.toThrow('Delete failed')
    })
  })

  describe('reloadPrompts', () => {
    it('reloads prompts configuration', async () => {
      const { reloadPrompts } = await import('../adminApi')
      const response = { message: 'Prompts reloaded successfully' }
      mockApi.post.mockResolvedValue(response)

      const result = await reloadPrompts()

      expect(mockApi.post).toHaveBeenCalledWith('/api/admin/prompts/reload')
      expect(result).toEqual(response)
    })

    it('handles reload error', async () => {
      const { reloadPrompts } = await import('../adminApi')
      const error = new Error('Reload failed')
      mockApi.post.mockRejectedValue(error)

      await expect(reloadPrompts()).rejects.toThrow('Reload failed')
    })
  })

  // ==================== Skill Approval Tests ====================

  describe('getPendingSkills', () => {
    it('fetches pending skills', async () => {
      const { getPendingSkills } = await import('../adminApi')
      const mockSkills = [
        {
          id: '1',
          name: 'Skill 1',
          description: 'Description 1',
          instructions: 'Instructions 1',
          category: 'writing',
          tags: ['hook'],
          skill_metadata: { license: 'MIT' },
          resource_count: 2,
          source: 'community',
          author_id: 'author1',
          author_name: 'Author 1',
          status: 'pending',
          reviewed_by: null,
          reviewer_name: null,
          reviewed_at: null,
          rejection_reason: null,
          created_at: '2024-01-01T00:00:00Z',
        },
        {
          id: '2',
          name: 'Skill 2',
          description: null,
          instructions: 'Instructions 2',
          category: 'editing',
          author_id: null,
          author_name: null,
          status: 'pending',
          reviewed_by: null,
          reviewer_name: null,
          reviewed_at: null,
          rejection_reason: null,
          created_at: '2024-01-02T00:00:00Z',
        },
      ]
      mockApi.get.mockResolvedValue(mockSkills)

      const result = await getPendingSkills()

      expect(mockApi.get).toHaveBeenCalledWith('/api/admin/skills/pending?status=pending')
      // 旧响应缺少审核材料字段时补上安全的默认值
      expect(result).toEqual([
        mockSkills[0],
        { ...mockSkills[1], tags: [], skill_metadata: {}, resource_count: 0, source: 'community' },
      ])
    })

    it('handles empty pending skills list', async () => {
      const { getPendingSkills } = await import('../adminApi')
      mockApi.get.mockResolvedValue([])

      const result = await getPendingSkills()

      expect(result).toEqual([])
    })

    it('propagates API errors', async () => {
      const { getPendingSkills } = await import('../adminApi')
      const error = new Error('API Error')
      mockApi.get.mockRejectedValue(error)

      await expect(getPendingSkills()).rejects.toThrow('API Error')
    })
  })

  describe('approveSkill', () => {
    it('approves skill by ID', async () => {
      const { approveSkill } = await import('../adminApi')
      const response = { message: 'Skill approved successfully', skill_id: '123' }
      mockApi.post.mockResolvedValue(response)

      const result = await approveSkill('123')

      expect(mockApi.post).toHaveBeenCalledWith('/api/admin/skills/123/approve')
      expect(result).toEqual(response)
    })

    it('handles approval error', async () => {
      const { approveSkill } = await import('../adminApi')
      const error = new Error('Approval failed')
      mockApi.post.mockRejectedValue(error)

      await expect(approveSkill('123')).rejects.toThrow('Approval failed')
    })
  })

  describe('rejectSkill', () => {
    it('rejects skill with reason', async () => {
      const { rejectSkill } = await import('../adminApi')
      const response = { message: 'Skill rejected', skill_id: '123' }
      mockApi.post.mockResolvedValue(response)

      const result = await rejectSkill('123', 'Quality issues')

      expect(mockApi.post).toHaveBeenCalledWith('/api/admin/skills/123/reject', {
        rejection_reason: 'Quality issues',
      })
      expect(result).toEqual(response)
    })

    it('rejects skill without reason', async () => {
      const { rejectSkill } = await import('../adminApi')
      const response = { message: 'Skill rejected', skill_id: '123' }
      mockApi.post.mockResolvedValue(response)

      const result = await rejectSkill('123')

      expect(mockApi.post).toHaveBeenCalledWith('/api/admin/skills/123/reject', {
        rejection_reason: undefined,
      })
      expect(result).toEqual(response)
    })

    it('handles rejection error', async () => {
      const { rejectSkill } = await import('../adminApi')
      const error = new Error('Rejection failed')
      mockApi.post.mockRejectedValue(error)

      await expect(rejectSkill('123', 'Reason')).rejects.toThrow('Rejection failed')
    })
  })

  describe('unpublishSkill', () => {
    it('posts the reason to the unpublish endpoint', async () => {
      const { unpublishSkill } = await import('../adminApi')
      const response = { message: 'Skill unpublished', skill_id: '123' }
      mockApi.post.mockResolvedValue(response)

      const result = await unpublishSkill('123', 'Infringement')

      expect(mockApi.post).toHaveBeenCalledWith('/api/admin/skills/123/unpublish', {
        rejection_reason: 'Infringement',
      })
      expect(result).toEqual(response)
    })
  })

  describe('getSkillReviewResources', () => {
    it('returns the raw resource files of a skill under review', async () => {
      const { getSkillReviewResources } = await import('../adminApi')
      const resources = [{ path: 'references/a.md', size: 3, content: 'abc' }]
      mockApi.get.mockResolvedValue({ resources })

      const result = await getSkillReviewResources('123')

      expect(mockApi.get).toHaveBeenCalledWith('/api/admin/skills/123/resources')
      expect(result).toEqual(resources)
    })
  })

  // ==================== Points / Check-in / Referral / Quota Tests ====================

  describe('points and growth APIs', () => {
    it('normalizes dashboard stats payload', async () => {
      const { getDashboardStats } = await import('../adminApi')
      mockApi.get.mockResolvedValue({
        total_users: '10',
        active_users: '8',
        total_projects: 4,
      })

      const result = await getDashboardStats()

      expect(mockApi.get).toHaveBeenCalledWith('/api/admin/dashboard/stats')
      expect(result).toMatchObject({
        total_users: 10,
        active_users: 8,
        total_projects: 4,
        week_referrals: 0,
      })
    })

    it('normalizes activation funnel payload', async () => {
      const { getActivationFunnel } = await import('../adminApi')
      mockApi.get.mockResolvedValue({
        window_days: '7',
        activation_rate: '0.5',
        steps: [
          { event_name: 'signup_success', label: 'Signup', users: '20', conversion_from_previous: null, drop_off_from_previous: null },
          { event_name: 'project_created', label: 'Project', users: '10', conversion_from_previous: '0.5', drop_off_from_previous: '10' },
        ],
      })

      const result = await getActivationFunnel(7)

      expect(mockApi.get).toHaveBeenCalledWith('/api/admin/dashboard/activation-funnel?days=7')
      expect(result).toEqual({
        window_days: 7,
        period_start: '',
        period_end: '',
        activation_rate: 0.5,
        steps: [
          {
            event_name: 'signup_success',
            label: 'Signup',
            users: 20,
            conversion_from_previous: null,
            drop_off_from_previous: null,
          },
          {
            event_name: 'project_created',
            label: 'Project',
            users: 10,
            conversion_from_previous: 0.5,
            drop_off_from_previous: 10,
          },
        ],
      })
    })

    it('normalizes upgrade conversion stats payload', async () => {
      const { getUpgradeConversionStats } = await import('../adminApi')
      mockApi.get.mockResolvedValue({
        window_days: '30',
        total_conversions: '20',
        paid_conversions: '12',
        unattributed_conversions: '5',
        channels: [
          { channel: 'zpay', conversions: '12', paid: true },
          { channel: 'admin_update', conversions: '8', paid: 'yes' },
        ],
        sources: [
          {
            source: 'chat_quota_blocked',
            conversions: '8',
            share: '0.4',
          },
          {
            source: 'settings_subscription_upgrade',
            conversions: '7',
            share: '0.35',
          },
        ],
      })

      const result = await getUpgradeConversionStats(30)

      expect(mockApi.get).toHaveBeenCalledWith('/api/admin/dashboard/upgrade-conversion?days=30')
      expect(result).toEqual({
        window_days: 30,
        period_start: '',
        period_end: '',
        total_conversions: 20,
        paid_conversions: 12,
        unattributed_conversions: 5,
        channels: [
          { channel: 'zpay', conversions: 12, paid: true },
          // Only a literal true counts as paid.
          { channel: 'admin_update', conversions: 8, paid: false },
        ],
        sources: [
          {
            source: 'chat_quota_blocked',
            conversions: 8,
            share: 0.4,
          },
          {
            source: 'settings_subscription_upgrade',
            conversions: 7,
            share: 0.35,
          },
        ],
      })
    })

    it('normalizes upgrade funnel stats payload', async () => {
      const { getUpgradeFunnelStats } = await import('../adminApi')
      mockApi.get.mockResolvedValue({
        window_days: '7',
        totals: { expose: '20', click: '8', conversion: '3' },
        sources: [
          {
            source: 'chat_quota_blocked',
            exposes: '12',
            clicks: '6',
            conversions: '3',
            click_through_rate: '0.5',
            conversion_rate_from_click: '0.5',
            conversion_rate_from_expose: '0.25',
          },
        ],
      })

      const result = await getUpgradeFunnelStats(7)

      expect(mockApi.get).toHaveBeenCalledWith('/api/admin/dashboard/upgrade-funnel?days=7')
      expect(result).toEqual({
        window_days: 7,
        period_start: '',
        period_end: '',
        totals: { expose: 20, click: 8, conversion: 3 },
        sources: [
          {
            source: 'chat_quota_blocked',
            exposes: 12,
            clicks: 6,
            conversions: 3,
            click_through_rate: 0.5,
            conversion_rate_from_click: 0.5,
            conversion_rate_from_expose: 0.25,
          },
        ],
      })
    })

    it('normalizes points stats payload', async () => {
      const { getPointsStats } = await import('../adminApi')
      mockApi.get.mockResolvedValue({
        data: {
          total_points_issued: '20',
          total_points_spent: 5,
          active_users_with_points: '3',
        },
      })

      const result = await getPointsStats()

      expect(mockApi.get).toHaveBeenCalledWith('/api/admin/points/stats')
      expect(result).toEqual({
        total_points_issued: 20,
        total_points_spent: 5,
        total_points_expired: 0,
        active_users_with_points: 3,
      })
    })

    it('normalizes user points payload with defaults', async () => {
      const { getUserPoints } = await import('../adminApi')
      mockApi.get.mockResolvedValue({
        user_id: 'u-1',
        username: 'alice',
        email: 'alice@example.com',
        available: '100',
      })

      const result = await getUserPoints('u-1')

      expect(mockApi.get).toHaveBeenCalledWith('/api/admin/points/u-1')
      expect(result).toEqual({
        user_id: 'u-1',
        username: 'alice',
        email: 'alice@example.com',
        available: 100,
        pending_expiration: 0,
        total_earned: 0,
        total_spent: 0,
      })
    })

    it('normalizes points transactions payload from nested shape', async () => {
      const { getUserPointsTransactions } = await import('../adminApi')
      mockApi.get.mockResolvedValue({
        data: {
          transactions: [
            {
              id: 'tx-1',
              user_id: 'u-1',
              username: 'alice',
              amount: '12',
              balance_after: '88',
              transaction_type: 'manual_adjust',
              created_at: '2025-01-01T00:00:00Z',
            },
          ],
          total: '9',
          page: '2',
          page_size: '20',
        },
      })

      const result = await getUserPointsTransactions('u-1', { page: 2, page_size: 20 })

      expect(mockApi.get).toHaveBeenCalledWith('/api/admin/points/u-1/transactions?page=2&page_size=20')
      expect(result.total).toBe(9)
      expect(result.page).toBe(2)
      expect(result.page_size).toBe(20)
      expect(result.items[0]).toMatchObject({
        id: 'tx-1',
        amount: 12,
        balance_after: 88,
        source_id: null,
        description: null,
        expires_at: null,
        is_expired: false,
      })
    })

    it('normalizes check-in stats and filters invalid streak keys', async () => {
      const { getCheckInStats } = await import('../adminApi')
      mockApi.get.mockResolvedValue({
        streak_distribution: {
          '1': 5,
          '7': '3',
          invalid: 99,
        },
      })

      const result = await getCheckInStats()

      expect(mockApi.get).toHaveBeenCalledWith('/api/admin/check-in/stats')
      expect(result).toEqual({
        today_count: 0,
        yesterday_count: 0,
        week_total: 0,
        streak_distribution: {
          1: 5,
          7: 3,
        },
      })
    })

    it('normalizes check-in records payload and paging fields', async () => {
      const { getCheckInRecords } = await import('../adminApi')
      mockApi.get.mockResolvedValue({
        items: [
          {
            id: 'r1',
            user_id: 'u1',
            username: 'alice',
            check_in_date: '2025-01-01',
            streak_days: '6',
            points_earned: '2',
            created_at: '2025-01-01T10:00:00Z',
          },
        ],
        total: '22',
        page: '1',
        page_size: '20',
      })

      const result = await getCheckInRecords({ page: 1, page_size: 20 })

      expect(mockApi.get).toHaveBeenCalledWith('/api/admin/check-in/records?page=1&page_size=20')
      expect(result.total).toBe(22)
      expect(result.items[0]).toMatchObject({
        id: 'r1',
        streak_days: 6,
        points_earned: 2,
      })
    })

    it('uses empty string fallback for invalid check-in dates', async () => {
      const { getCheckInRecords } = await import('../adminApi')
      mockApi.get.mockResolvedValue({
        items: [
          {
            id: 'r2',
            user_id: 'u2',
            username: 'bob',
            check_in_date: null,
            streak_days: 1,
            points_earned: 1,
            created_at: 'invalid-date',
          },
        ],
      })

      const result = await getCheckInRecords()

      expect(result.items[0]).toMatchObject({
        id: 'r2',
        check_in_date: '',
        created_at: '',
      })
    })

    it('normalizes referral and quota related payloads', async () => {
      const {
        getReferralStats,
        createAdminInviteCode,
        getInviteCodes,
        getReferralRewards,
        getQuotaUsageStats,
        getUserQuota,
      } = await import('../adminApi')

      mockApi.get
        .mockResolvedValueOnce({ total_codes: '8', active_codes: 3 })
        .mockResolvedValueOnce({
          data: {
            codes: [{ id: 'c1', code: 'ABCD', owner_id: 'u1', owner_name: 'alice', max_uses: '10', current_uses: '2', is_active: true, created_at: '2025-01-01T00:00:00Z' }],
            total: '1',
            page: '1',
            page_size: '20',
          },
        })
        .mockResolvedValueOnce({
          rewards: [{ id: 'rw1', user_id: 'u1', username: 'alice', reward_type: 'points', amount: '10', source: 'invite', is_used: false, created_at: '2025-01-01T00:00:00Z' }],
        })
        .mockResolvedValueOnce({ period_start: '2026-10-01T00:00:00+00:00', material_decompositions: '4' })
        .mockResolvedValueOnce({
          user_id: 'u1', username: 'alice', plan_name: 'pro',
          ai_conversations: { used: '9', limit: '50', reset_at: '2026-10-06T08:00:00+00:00' },
          material_decompositions: { used: 1, limit: 5 },
        })
      mockApi.post.mockResolvedValueOnce({
        id: 'c2',
        code: 'WXYZ-5678',
        owner_id: 'u1',
        owner_name: 'alice',
        max_uses: '3',
        current_uses: '0',
        is_active: true,
        created_at: '2025-01-01T00:00:00Z',
      })

      const referralStats = await getReferralStats()
      const createdCode = await createAdminInviteCode()
      const inviteCodes = await getInviteCodes({ page: 1, page_size: 20 })
      const rewards = await getReferralRewards()
      const quotaStats = await getQuotaUsageStats()
      const userQuota = await getUserQuota('u1')

      expect(referralStats).toEqual({
        total_codes: 8,
        active_codes: 3,
        total_referrals: 0,
        successful_referrals: 0,
        pending_rewards: 0,
        total_points_awarded: 0,
      })
      expect(inviteCodes.items[0]).toMatchObject({ code: 'ABCD', max_uses: 10, current_uses: 2 })
      expect(createdCode).toMatchObject({ code: 'WXYZ-5678', max_uses: 3, current_uses: 0 })
      expect(rewards.items[0]).toMatchObject({ reward_type: 'points', amount: 10, is_used: false })
      expect(quotaStats).toEqual({
        period_start: '2026-10-01T00:00:00+00:00',
        period_end: '',
        material_decompositions: 4,
        inspiration_copies: 0,
        skills_created: 0,
      })
      expect(userQuota).toEqual({
        user_id: 'u1',
        username: 'alice',
        email: '',
        plan_name: 'pro',
        plan_display_name: null,
        plan_display_name_en: null,
        ai_conversations: { used: 9, limit: 50, reset_at: '2026-10-06T08:00:00+00:00' },
        material_decompositions: { used: 1, limit: 5, reset_at: null },
        inspiration_copies: { used: 0, limit: 0, reset_at: null },
        custom_skills: { used: 0, limit: 0, reset_at: null },
      })
      expect(mockApi.post).toHaveBeenCalledWith('/api/admin/invites')
    })

    it('adjusts user points with explicit payload', async () => {
      const { adjustUserPoints } = await import('../adminApi')
      const apiPayload = {
        message: 'ok',
        new_balance: '80',
      }
      mockApi.post.mockResolvedValue(apiPayload)

      const result = await adjustUserPoints('u1', {
        amount: -20,
        reason: 'manual correction',
      })

      expect(mockApi.post).toHaveBeenCalledWith('/api/admin/points/u1/adjust', {
        amount: -20,
        reason: 'manual correction',
      })
      expect(result).toEqual(apiPayload)
    })

    it('rejects an audit payload missing its required total rather than inventing a healthy count', async () => {
      const { getAuditLogs } = await import('../adminApi')
      mockApi.get.mockResolvedValue({ items: [], page: 1, page_size: 20 })
      await expect(getAuditLogs()).rejects.toThrow('Invalid audit log response')
    })

    it('rejects an audit row missing actor identity rather than hiding it as an empty string', async () => {
      const { getAuditLogs } = await import('../adminApi')
      mockApi.get.mockResolvedValue({ items: [{ id: 'log-1', action: 'update_user', resource_type: 'user', created_at: '2026-10-04T00:00:00Z' }], total: 1, page: 1, page_size: 20 })
      await expect(getAuditLogs()).rejects.toThrow('Invalid audit log response')
    })

    it('normalizes the supported audit query response and pagination', async () => {
      const { getAuditLogs } = await import('../adminApi')
      mockApi.get.mockResolvedValue({
        data: {
          logs: [
            {
              id: 'log-1',
              admin_id: 'admin-1',
              admin_name: 'super-admin',
              action: 'update',
              resource_type: 'subscription',
              resource_id: 'sub-1',
              details: 'updated plan',
              old_value: { plan_name: 'free' },
              new_value: { plan_name: 'pro' },
              ip_address: '127.0.0.1',
              user_agent: 'playwright',
              created_at: '2026-03-08T00:00:00Z',
            },
          ],
          total: '22',
          page: '2',
          page_size: '20',
        },
      })

      const result = await getAuditLogs({
        page: 2,
        page_size: 20,
        resource_type: 'subscription',
        action: 'update',
      })

      expect(mockApi.get).toHaveBeenCalledWith(
        '/api/admin/audit-logs?page=2&page_size=20&resource_type=subscription&action=update',
      )
      expect(result).toEqual({
        items: [
          {
            id: 'log-1',
            admin_id: 'admin-1',
            admin_name: 'super-admin',
            action: 'update',
            resource_type: 'subscription',
            resource_id: 'sub-1',
            details: 'updated plan',
            old_value: { plan_name: 'free' },
            new_value: { plan_name: 'pro' },
            ip_address: '127.0.0.1',
            user_agent: 'playwright',
            created_at: '2026-03-08T00:00:00Z',
          },
        ],
        total: 22,
        page: 2,
        page_size: 20,
      })
    })

    it('normalizes plans payload from tiers shape and updates plan by id', async () => {
      const { getPlans, updatePlan } = await import('../adminApi')
      mockApi.get.mockResolvedValue({
        data: {
          tiers: [
            {
              id: 'pro',
              name: 'pro',
              display_name: '专业版',
              price_monthly_cents: '2900',
              price_yearly_cents: 29900,
              entitlements: {
                ai_conversations_monthly: 2000,
              },
              is_active: true,
              created_at: '2026-03-01T00:00:00Z',
            },
          ],
        },
      })
      mockApi.put.mockResolvedValue({
        id: 'pro',
        name: 'pro',
        display_name: '专业版',
        price_monthly_cents: 3900,
      })

      const plans = await getPlans()
      const updated = await updatePlan('pro', {
        display_name: '专业版',
        display_name_en: 'Pro',
        price_monthly_cents: 3900,
      })

      expect(mockApi.get).toHaveBeenCalledWith('/api/admin/plans')
      expect(plans).toEqual([
        {
          id: 'pro',
          name: 'pro',
          display_name: '专业版',
          display_name_en: undefined,
          price_monthly_cents: 2900,
          price_yearly_cents: 29900,
          features: {
            ai_conversations_monthly: 2000,
          },
          is_active: true,
          created_at: '2026-03-01T00:00:00Z',
          updated_at: undefined,
        },
      ])
      expect(mockApi.put).toHaveBeenCalledWith('/api/admin/plans/pro', {
        display_name: '专业版',
        display_name_en: 'Pro',
        price_monthly_cents: 3900,
      })
      expect(updated).toEqual({
        id: 'pro',
        name: 'pro',
        display_name: '专业版',
        price_monthly_cents: 3900,
      })
    })

    it('normalizes subscriptions payload including virtual rows', async () => {
      const { getSubscriptions } = await import('../adminApi')
      mockApi.get.mockResolvedValue({
        items: [
          {
            id: 'sub-1',
            user_id: 'u1',
            username: 'alice',
            email: 'alice@example.com',
            plan_name: 'pro',
            plan_display_name: '专业版',
            status: 'active',
            current_period_start: '2025-01-01T00:00:00Z',
            current_period_end: '2126-01-01T00:00:00Z',
            created_at: '2025-01-01T00:00:00Z',
            updated_at: '2025-01-02T00:00:00Z',
            has_subscription_record: true,
            is_test_account: false,
          },
          {
            id: 'virtual-u2',
            user_id: 'u2',
            username: 'test_user_2',
            email: 'qa@example.com',
            plan_name: 'free',
            status: 'active',
            current_period_start: null,
            current_period_end: null,
            created_at: '2025-01-03T00:00:00Z',
            updated_at: '2025-01-03T00:00:00Z',
            has_subscription_record: false,
            is_test_account: true,
          },
        ],
        total: 2,
        page: 1,
        page_size: 20,
      })

      const result = await getSubscriptions({ page: 1, page_size: 20 })

      expect(result.items[0]).toMatchObject({
        plan_display_name: '专业版',
        current_period_end: '2126-01-01T00:00:00Z',
        has_subscription_record: true,
      })
      expect(result.items[1]).toMatchObject({
        user_id: 'u2',
        current_period_start: null,
        current_period_end: null,
        has_subscription_record: false,
        is_test_account: true,
      })
    })

    it('covers inspiration and feedback admin APIs', async () => {
      const {
        getInspirations,
        reviewInspiration,
        updateInspiration,
        deleteInspiration,
        getFeedbackList,
        updateFeedbackStatus,
      } = await import('../adminApi')

      mockApi.get
        .mockResolvedValueOnce({
          items: [
            {
              id: 'ins-1',
              name: 'Hook',
              description: 'desc',
              tags: ['tag-a'],
              source: 'community',
              status: 'pending',
              copy_count: '5',
              created_at: '2025-01-01T00:00:00Z',
              updated_at: '2025-01-02T00:00:00Z',
            },
          ],
          total: '1',
        })
        .mockResolvedValueOnce({
          items: [
            {
              id: 'fb-1',
              user_id: 'u-1',
              username: 'alice',
              email: 'alice@example.com',
              source_page: 'editor',
              issue_text: 'bug report',
              has_screenshot: true,
              screenshot_size_bytes: '1024',
              status: 'open',
              created_at: '2025-01-01T00:00:00Z',
              updated_at: '2025-01-02T00:00:00Z',
            },
          ],
          total: '1',
        })

      mockApi.post.mockResolvedValueOnce({ message: 'ok' })
      mockApi.patch
        .mockResolvedValueOnce({ id: 'ins-1', name: 'Hook updated' })
        .mockResolvedValueOnce({
          id: 'fb-1',
          user_id: 'u-1',
          username: 'alice',
          email: 'alice@example.com',
          source_page: 'editor',
          issue_text: 'bug report',
          has_screenshot: false,
          status: 'processing',
          created_at: '2025-01-01T00:00:00Z',
          updated_at: '2025-01-03T00:00:00Z',
        })
      mockApi.delete.mockResolvedValueOnce({ message: 'deleted' })

      const inspirations = await getInspirations({
        status: 'pending',
        source: 'community',
        skip: 20,
        limit: 20,
      })
      const reviewed = await reviewInspiration('ins-1', false, 'needs refinement')
      const updated = await updateInspiration('ins-1', { name: 'Hook updated' })
      const deleted = await deleteInspiration('ins-1')
      const feedback = await getFeedbackList({
        status: 'open',
        source_page: 'editor',
        has_screenshot: true,
        search: 'alice',
        skip: 20,
        limit: 20,
      })
      const updatedFeedback = await updateFeedbackStatus('fb-1', 'processing')

      expect(mockApi.get).toHaveBeenNthCalledWith(
        1,
        '/api/admin/inspirations?status=pending&source=community&skip=20&limit=20',
      )
      expect(mockApi.post).toHaveBeenCalledWith('/api/admin/inspirations/ins-1/review', {
        approve: false,
        rejection_reason: 'needs refinement',
      })
      expect(mockApi.patch).toHaveBeenNthCalledWith(1, '/api/admin/inspirations/ins-1', {
        name: 'Hook updated',
      })
      expect(mockApi.delete).toHaveBeenCalledWith('/api/admin/inspirations/ins-1')
      expect(mockApi.get).toHaveBeenNthCalledWith(
        2,
        '/api/admin/feedback?status=open&source_page=editor&has_screenshot=true&search=alice&skip=20&limit=20',
      )
      expect(mockApi.patch).toHaveBeenNthCalledWith(2, '/api/admin/feedback/fb-1/status', {
        status: 'processing',
      })

      expect(inspirations.total).toBe(1)
      expect(inspirations.items[0]).toMatchObject({
        id: 'ins-1',
        source: 'community',
        status: 'pending',
        copy_count: 5,
      })
      expect(reviewed).toEqual({ message: 'ok' })
      expect(updated).toEqual({ id: 'ins-1', name: 'Hook updated' })
      expect(deleted).toEqual({ message: 'deleted' })
      expect(feedback.total).toBe(1)
      expect(feedback.items[0]).toMatchObject({
        id: 'fb-1',
        source_page: 'editor',
        status: 'open',
        screenshot_size_bytes: 1024,
      })
      expect(updatedFeedback).toMatchObject({
        id: 'fb-1',
        status: 'processing',
      })
    })

    it('downloads feedback screenshot blob with auth and locale headers', async () => {
      const { getFeedbackScreenshotBlob } = await import('../adminApi')
      localStorage.setItem('zenstory-language', 'en')

      const screenshotBlob = new Blob(['binary-image'], { type: 'image/png' })
      fetchMock.mockResolvedValue({
        ok: true,
        status: 200,
        blob: vi.fn().mockResolvedValue(screenshotBlob),
      })

      const result = await getFeedbackScreenshotBlob('fb-1')

      expect(fetchMock).toHaveBeenCalledWith(
        'https://api.example.com/api/admin/feedback/fb-1/screenshot',
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
      const { getFeedbackScreenshotBlob } = await import('../adminApi')
      const screenshotBlob = new Blob(['retry-image'], { type: 'image/png' })

      mockTryRefreshToken.mockImplementation(async () => {
        mockGetAccessToken.mockReturnValue('rotated-access-token')
        localStorage.setItem('access_token', 'rotated-access-token')
        localStorage.setItem('refresh_token', 'rotated-refresh-token')
        return true
      })
      fetchMock
        .mockResolvedValueOnce({
          ok: false,
          status: 401,
          json: vi.fn().mockResolvedValue({ detail: 'ERR_UNAUTHORIZED' }),
        })
        .mockResolvedValueOnce({
          ok: true,
          status: 200,
          blob: vi.fn().mockResolvedValue(screenshotBlob),
        })

      const result = await getFeedbackScreenshotBlob('fb-2')

      expect(mockTryRefreshToken).toHaveBeenCalledTimes(1)
      expect(fetchMock).toHaveBeenCalledTimes(2)
      expect(result).toBe(screenshotBlob)
    })

    it('throws ApiError with resolved backend error message on failure', async () => {
      const { getFeedbackScreenshotBlob } = await import('../adminApi')

      fetchMock.mockResolvedValue({
        ok: false,
        status: 404,
        json: vi.fn().mockResolvedValue({ detail: 'ERR_FEEDBACK_NOT_FOUND' }),
      })

      await expect(getFeedbackScreenshotBlob('fb-missing')).rejects.toMatchObject({
        name: 'ApiError',
        status: 404,
        message: 'ERR_FEEDBACK_NOT_FOUND',
      })
    })

    it('falls back to generic error code when error payload is unreadable', async () => {
      const { getFeedbackScreenshotBlob } = await import('../adminApi')

      fetchMock.mockResolvedValue({
        ok: false,
        status: 500,
        json: vi.fn().mockRejectedValue(new Error('invalid json')),
      })

      await expect(getFeedbackScreenshotBlob('fb-bad-json')).rejects.toMatchObject({
        name: 'ApiError',
        status: 500,
        message: 'ERR_INTERNAL_SERVER_ERROR',
      })
    })
  })

  // ==================== Admin API Object Tests ====================

  describe('adminApi export', () => {
    it('exports all user management functions', async () => {
      const { adminApi } = await import('../adminApi')

      expect(adminApi.getUsers).toBeDefined()
      expect(adminApi.getUser).toBeDefined()
      expect(adminApi.updateUser).toBeDefined()
      expect(adminApi.deleteUser).toBeDefined()
    })

    it('exports all prompt management functions', async () => {
      const { adminApi } = await import('../adminApi')

      expect(adminApi.getPrompts).toBeDefined()
      expect(adminApi.getPrompt).toBeDefined()
      expect(adminApi.upsertPrompt).toBeDefined()
      expect(adminApi.deletePrompt).toBeDefined()
      expect(adminApi.reloadPrompts).toBeDefined()
    })

    it('exports all skill approval functions', async () => {
      const { adminApi } = await import('../adminApi')

      expect(adminApi.getPendingSkills).toBeDefined()
      expect(adminApi.approveSkill).toBeDefined()
      expect(adminApi.rejectSkill).toBeDefined()
    })

    it('aliases match original functions', async () => {
      const { getUsers, getUser, updateUser, deleteUser, adminApi } = await import('../adminApi')

      expect(adminApi.getUsers).toBe(getUsers)
      expect(adminApi.getUser).toBe(getUser)
      expect(adminApi.updateUser).toBe(updateUser)
      expect(adminApi.deleteUser).toBe(deleteUser)
      expect(adminApi.getActivationFunnel).toBeDefined()
      expect(adminApi.createAdminInviteCode).toBeDefined()
    })
  })
})

describe('admin cleanup query params', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('sends the subscription search and the payment-order user filter', async () => {
    const { getSubscriptions, getPaymentOrders } = await import('../adminApi')
    mockApi.get.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 })

    await getSubscriptions({ page: 2, search: 'alice@' })
    await getPaymentOrders({ user_id: 'user-1', page_size: 10 })

    expect(mockApi.get).toHaveBeenNthCalledWith(1, '/api/admin/subscriptions?page=2&search=alice%40')
    expect(mockApi.get).toHaveBeenNthCalledWith(2, '/api/admin/payment-orders?page_size=10&user_id=user-1')
  })

  it('posts max_uses with a multi-use batch', async () => {
    const { createCodesBatch } = await import('../adminApi')
    mockApi.post.mockResolvedValue({ codes: ['A'], count: 1, max_uses: 5 })

    const result = await createCodesBatch({ tier: 'pro', duration_days: 30, count: 1, code_type: 'multi_use', max_uses: 5 })

    expect(mockApi.post).toHaveBeenCalledWith('/api/admin/codes/batch', {
      tier: 'pro', duration_days: 30, count: 1, code_type: 'multi_use', max_uses: 5,
    })
    expect(result.max_uses).toBe(5)
  })
})
