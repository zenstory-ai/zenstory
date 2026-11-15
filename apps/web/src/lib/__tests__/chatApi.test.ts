/**
 * Tests for Chat API client
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import {
  getRecentMessages,
  createNewSession,
  submitMessageFeedback,
  type ChatSession,
  type ChatMessage,
} from '../chatApi'
import * as chatApiModule from '../chatApi'

const mockApiGet = vi.fn()
const mockApiPost = vi.fn()

vi.mock('../apiClient', () => ({
  api: {
    get: (...args: unknown[]) => mockApiGet(...args),
    post: (...args: unknown[]) => mockApiPost(...args),
  },
}))

vi.mock('../i18n-helpers', () => ({
  getLocale: vi.fn(() => 'en'),
}))

describe('chatApi', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  describe('getRecentMessages', () => {
    it('returns recent messages with default limit', async () => {
      const mockMessages: ChatMessage[] = [
        {
          id: 'msg-1',
          session_id: 'session-1',
          role: 'user',
          content: 'Recent message',
          created_at: '2024-01-01T00:00:00Z',
        },
      ]
      mockApiGet.mockResolvedValue(mockMessages)

      const result = await getRecentMessages('project-1')

      expect(result).toEqual(mockMessages)
      expect(mockApiGet).toHaveBeenCalledWith(
        '/api/v1/chat/session/project-1/recent?limit=20',
        undefined,
      )
    })

    it('passes abort signal to API call', async () => {
      const mockMessages: ChatMessage[] = []
      mockApiGet.mockResolvedValue(mockMessages)

      const controller = new AbortController()
      await getRecentMessages('project-1', 50, controller.signal)

      expect(mockApiGet).toHaveBeenCalledWith(
        '/api/v1/chat/session/project-1/recent?limit=50',
        { signal: controller.signal },
      )
    })

    it('requests one assistant message by id without changing the abort-signal argument', async () => {
      mockApiGet.mockResolvedValue([])
      const controller = new AbortController()

      await getRecentMessages('project-1', 50, controller.signal, 'assistant/1?tag=a&b')

      expect(mockApiGet).toHaveBeenCalledWith(
        '/api/v1/chat/session/project-1/recent?limit=50&message_id=assistant%2F1%3Ftag%3Da%26b',
        { signal: controller.signal },
      )
    })

    it('propagates API errors', async () => {
      mockApiGet.mockRejectedValue(new Error('Network error'))

      await expect(getRecentMessages('project-1')).rejects.toThrow('Network error')
    })
  })

  describe('createNewSession', () => {
    it('creates new session with default title (English)', async () => {
      const mockSession: ChatSession = {
        id: 'session-2',
        user_id: 'user-1',
        project_id: 'project-1',
        title: 'New Chat',
        is_active: true,
        message_count: 0,
        created_at: '2024-01-01T00:00:00Z',
        updated_at: '2024-01-01T00:00:00Z',
      }
      mockApiPost.mockResolvedValue(mockSession)

      const result = await createNewSession('project-1')

      expect(result).toEqual(mockSession)
      expect(mockApiPost).toHaveBeenCalledWith(
        '/api/v1/chat/session/project-1/new?title=New+Chat',
      )
    })

    it('creates new session with default title (Chinese)', async () => {
      const { getLocale } = await import('../i18n-helpers')
      const mockGetLocale = getLocale as ReturnType<typeof vi.fn>
      mockGetLocale.mockReturnValue('zh')

      mockApiPost.mockResolvedValue({ id: 'session-zh' })

      await createNewSession('project-1')

      expect(mockApiPost).toHaveBeenCalledWith(
        '/api/v1/chat/session/project-1/new?title=%E6%96%B0%E5%AF%B9%E8%AF%9D',
      )
    })

    it('creates new session with custom title', async () => {
      mockApiPost.mockResolvedValue({ id: 'session-custom' })

      await createNewSession('project-1', 'Custom Title')

      expect(mockApiPost).toHaveBeenCalledWith(
        '/api/v1/chat/session/project-1/new?title=Custom+Title',
      )
    })
  })

  describe('submitMessageFeedback', () => {
    it('submits thumbs up feedback', async () => {
      const mockResponse = {
        message_id: 'msg-1',
        feedback: {
          vote: 'up' as const,
          preset: null,
          comment: null,
          updated_at: '2024-01-01T00:00:00Z',
        },
        updated_at: '2024-01-01T00:00:00Z',
      }
      mockApiPost.mockResolvedValue(mockResponse)

      const result = await submitMessageFeedback('msg-1', { vote: 'up' })

      expect(result).toEqual(mockResponse)
      expect(mockApiPost).toHaveBeenCalledWith('/api/v1/chat/messages/msg-1/feedback', { vote: 'up' })
    })

    it('submits thumbs down feedback with optional fields', async () => {
      mockApiPost.mockResolvedValue({ message_id: 'msg-2' })

      await submitMessageFeedback('msg-2', {
        vote: 'down',
        preset: 'not_helpful',
        comment: 'Missing details',
      })

      expect(mockApiPost).toHaveBeenCalledWith('/api/v1/chat/messages/msg-2/feedback', {
        vote: 'down',
        preset: 'not_helpful',
        comment: 'Missing details',
      })
    })
  })

  describe('legacy cleanup guards', () => {
    it('does not export removed session history helpers', () => {
      expect('getSession' in chatApiModule).toBe(false)
      expect('getMessages' in chatApiModule).toBe(false)
      expect('clearSession' in chatApiModule).toBe(false)
    })

    it('ChatPanel no longer references deprecated session history APIs', () => {
      const chatPanelSource = readFileSync(
        join(process.cwd(), 'src/components/ChatPanel.tsx'),
        'utf8',
      )

      expect(chatPanelSource).not.toMatch(/\bgetSession\b/)
      expect(chatPanelSource).not.toMatch(/\bgetMessages\b/)
      expect(chatPanelSource).not.toMatch(/\bclearSession\b/)
    })
  })
})
