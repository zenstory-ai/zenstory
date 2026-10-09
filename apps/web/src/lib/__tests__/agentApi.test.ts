/**
 * Tests for Agent API client with SSE streaming support
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import {
  streamAgentRequest,
  fetchSuggestions,
  sendSteeringRequest,
  selectStreamErrorMessage,
  stopAgentRun,
} from '../agentApi'
import { api } from '../apiClient'
import type { SSEEvent } from '../../types'
import { debugContext } from '../debugContext'

// Mock dependencies
vi.mock('../apiClient', async importOriginal => ({
  resolveOwnedAuthSession: (await importOriginal<typeof import('../apiClient')>()).resolveOwnedAuthSession,
  tryRefreshToken: vi.fn(),
  getAccessToken: vi.fn(() => 'test-access-token'),
  clearAuthStorage: vi.fn(),
  getApiBase: vi.fn(() => 'http://localhost:8000'),
  api: { post: vi.fn() },
}))

vi.mock('../errorHandler', () => ({
  translateError: vi.fn((msg: string) => `Translated: ${msg}`),
  toUserErrorMessage: vi.fn((msg: string) => {
    if (!msg || !msg.trim()) {
      return 'Translated: ERR_INTERNAL_SERVER_ERROR'
    }
    return msg.startsWith('ERR_') ? `Translated: ${msg}` : msg
  }),
  resolveApiErrorMessage: vi.fn((payload: unknown, fallback: string) => {
    if (!payload || typeof payload !== 'object') {
      return fallback
    }
    const data = payload as Record<string, unknown>
    const detailObject = data.error_detail && typeof data.error_detail === 'object'
      ? data.error_detail as Record<string, unknown>
      : null
    return (
      (typeof data.error_code === 'string' && data.error_code) ||
      (typeof data.detail === 'string' && data.detail) ||
      (typeof data.error_detail === 'string' && data.error_detail) ||
      (detailObject && typeof detailObject.message === 'string' && detailObject.message) ||
      (detailObject && typeof detailObject.detail === 'string' && detailObject.detail) ||
      (typeof data.message === 'string' && data.message) ||
      fallback
    )
  }),
}))

// Helper to create mock ReadableStream for SSE
function createMockStream(chunks: string[]): ReadableStream<Uint8Array> {
  let index = 0
  return new ReadableStream({
    pull(controller) {
      if (index < chunks.length) {
        const encoder = new TextEncoder()
        controller.enqueue(encoder.encode(chunks[index]))
        index++
      } else {
        controller.close()
      }
    },
  })
}

// Helper to extract parseSSEEvent logic for direct testing
function parseSSEEvent(eventString: string): SSEEvent | null {
  const lines = eventString.split('\n')
  let eventType: SSEEvent['type'] = 'content'
  let dataString = ''

  for (const line of lines) {
    if (line.startsWith('event:')) {
      eventType = line.slice(6).trim() as SSEEvent['type']
    } else if (line.startsWith('data:')) {
      dataString = line.slice(5).trim()
    }
  }

  if (!dataString) {
    return null
  }

  try {
    const data = JSON.parse(dataString)
    return { type: eventType, data }
  } catch {
    return { type: eventType, data: { text: dataString } }
  }
}

describe('agentApi', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.spyOn(console, 'log').mockImplementation(() => {})
    vi.spyOn(console, 'error').mockImplementation(() => {})
    localStorage.clear()
    sessionStorage.clear()
    localStorage.setItem('access_token', 'test-access-token')
    localStorage.setItem('refresh_token', 'test-refresh-token')
    localStorage.setItem('zenstory-language', 'en')
  })

  afterEach(() => {
    // Vitest 4's restoreAllMocks only restores vi.spyOn spies, so also reset the
    // vi.fn() module mocks to keep per-test overrides (e.g. getAccessToken) from leaking.
    vi.resetAllMocks()
    vi.restoreAllMocks()
  })

  describe('parseSSEEvent (via streamAgentRequest)', () => {
    it('parses content event correctly', async () => {
      const eventString = 'data: {"text":"Hello"}\n\n'
      const result = parseSSEEvent(eventString)
      expect(result).toEqual({
        type: 'content',
        data: { text: 'Hello' },
      })
    })

    it('parses tool_call event with JSON arguments', () => {
      const eventString =
        'data: {"tool_name":"create_file","arguments":{"path":"test.md","content":"hello"}}\n\n'
      const result = parseSSEEvent(eventString)
      expect(result).not.toBeNull()
      expect(result?.type).toBe('content')
      expect(result?.data).toEqual({
        tool_name: 'create_file',
        arguments: { path: 'test.md', content: 'hello' },
      })
    })

    it('handles malformed JSON gracefully', () => {
      const eventString = 'data: {"text":invalid}\n\n'
      const result = parseSSEEvent(eventString)
      // Malformed JSON becomes plain text
      expect(result).toEqual({
        type: 'content',
        data: { text: '{"text":invalid}' },
      })
    })

    it('parses thinking event with step info', () => {
      const eventString =
        'data: {"message":"Analyzing...","step":"1"}\n\n'
      const result = parseSSEEvent(eventString)
      expect(result).toEqual({
        type: 'content',
        data: { message: 'Analyzing...', step: '1' },
      })
    })

    it('parses event with explicit event type', () => {
      const eventString =
        'event: thinking\ndata: {"message":"Processing..."}\n\n'
      const result = parseSSEEvent(eventString)
      expect(result).toEqual({
        type: 'thinking',
        data: { message: 'Processing...' },
      })
    })

    it('returns null for empty data', () => {
      const eventString = 'event: content\n\n'
      const result = parseSSEEvent(eventString)
      expect(result).toBeNull()
    })

    it('parses tool_result event', () => {
      const eventString =
        'event: tool_result\ndata: {"tool_name":"create_file","status":"success","data":{"file_id":"123"}}\n\n'
      const result = parseSSEEvent(eventString)
      expect(result).toEqual({
        type: 'tool_result',
        data: {
          tool_name: 'create_file',
          status: 'success',
          data: { file_id: '123' },
        },
      })
    })

    it('parses error event with retryable flag', () => {
      const eventString =
        'event: error\ndata: {"message":"Rate limited","code":"RATE_LIMIT","retryable":true}\n\n'
      const result = parseSSEEvent(eventString)
      expect(result).toEqual({
        type: 'error',
        data: {
          message: 'Rate limited',
          code: 'RATE_LIMIT',
          retryable: true,
        },
      })
    })

    it('parses done event with apply_action', () => {
      const eventString =
        'event: done\ndata: {"apply_action":"replace","refs":[1,2,3],"assistant_message_id":"msg-1","session_id":"session-1"}\n\n'
      const result = parseSSEEvent(eventString)
      expect(result).toEqual({
        type: 'done',
        data: {
          apply_action: 'replace',
          refs: [1, 2, 3],
          assistant_message_id: 'msg-1',
          session_id: 'session-1',
        },
      })
    })
  })

  describe('selectStreamErrorMessage', () => {
    const noProgress = {
      message: 'AI 在本轮反复读取同一份资料，已先停下。',
      code: 'ERR_AGENT_TOOL_FAILURE_LIMIT',
      retryable: false,
      reason: 'no_progress',
    }

    it('shows the server stop explanation when the frame carries a reason', () => {
      expect(selectStreamErrorMessage(noProgress, 'zh')).toBe(noProgress.message)
    })

    it('shows the server explanation for tool-failure and no-progress codes without a reason', () => {
      expect(
        selectStreamErrorMessage({ message: '这一步连续几次都没做成', code: 'ERR_AGENT_TOOL_FAILURE_LIMIT' }, 'zh-CN'),
      ).toBe('这一步连续几次都没做成')
      expect(
        selectStreamErrorMessage({ message: '一直在翻看同样的资料', code: 'ERR_AGENT_NO_PROGRESS' }, 'zh'),
      ).toBe('一直在翻看同样的资料')
    })

    it('falls back to the localized code when the server message is empty', () => {
      expect(
        selectStreamErrorMessage({ message: '  ', code: 'ERR_AGENT_NO_PROGRESS', reason: 'no_progress' }, 'zh'),
      ).toBe('ERR_AGENT_NO_PROGRESS')
    })

    it('keeps code-based copy in the English UI (server explanations are Chinese only)', () => {
      expect(selectStreamErrorMessage(noProgress, 'en')).toBe('ERR_AGENT_TOOL_FAILURE_LIMIT')
    })

    it('never surfaces raw messages for other error codes', () => {
      expect(
        selectStreamErrorMessage({ message: 'upstream 502 from deepseek', code: 'ERR_AGENT_UPSTREAM_UNAVAILABLE' }, 'zh'),
      ).toBe('ERR_AGENT_UPSTREAM_UNAVAILABLE')
    })

    it('uses the message when there is no ERR_ code, and a generic code when there is nothing', () => {
      expect(selectStreamErrorMessage({ message: 'Rate limited', code: 'RATE_LIMIT' }, 'zh')).toBe('Rate limited')
      expect(selectStreamErrorMessage({}, 'zh')).toBe('ERR_INTERNAL_SERVER_ERROR')
    })
  })

  describe('streamAgentRequest', () => {
    it('reports a refund only when the backend sends quota_refunded after the terminal frame', async () => {
      const onError = vi.fn()
      const onQuotaRefunded = vi.fn()
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        body: createMockStream([
          'event: error\ndata: {"message":"x","code":"ERR_AGENT_UPSTREAM_UNAVAILABLE","retryable":true}\n\n',
          'event: quota_refunded\ndata: {"refunded":true,"kind":"error"}\n\n',
        ]),
      }))

      streamAgentRequest({ project_id: 'test-project', message: 'test' }, { onError, onQuotaRefunded })
      await new Promise(resolve => setTimeout(resolve, 100))

      expect(onError).toHaveBeenCalledTimes(1)
      expect(onQuotaRefunded).toHaveBeenCalledWith('error', [])
    })

    it('passes on the blank files a refunded stop removed, and the stop facts on done', async () => {
      const onQuotaRefunded = vi.fn()
      const onDone = vi.fn()
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        body: createMockStream([
          'event: done\ndata: {"assistant_message_id":"m-1","stop_reason":"user_stopped","produced_output":false}\n\n',
          'event: quota_refunded\ndata: {"refunded":true,"kind":"stopped","removed_files":[{"id":"f-1","title":"第1章"},{"bad":1}]}\n\n',
        ]),
      }))

      streamAgentRequest({ project_id: 'test-project', message: 'test' }, { onQuotaRefunded, onDone })
      await new Promise(resolve => setTimeout(resolve, 100))

      expect(onDone).toHaveBeenCalledWith(expect.objectContaining({
        assistant_message_id: 'm-1',
        stop_reason: 'user_stopped',
        produced_output: false,
      }))
      expect(onQuotaRefunded).toHaveBeenCalledWith('stopped', [{ id: 'f-1', title: '第1章' }])
    })

    it.each(['no_progress', 'stopped'])(
      'maps a %s refund and ignores frames that do not claim a refund',
      async (kind) => {
        const onQuotaRefunded = vi.fn()
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
          ok: true,
          status: 200,
          body: createMockStream([
            'event: done\ndata: {}\n\n',
            `event: quota_refunded\ndata: {"refunded":false,"kind":"${kind}"}\n\n`,
            `event: quota_refunded\ndata: {"refunded":true,"kind":"${kind}"}\n\n`,
          ]),
        }))

        streamAgentRequest({ project_id: 'test-project', message: 'test' }, { onQuotaRefunded })
        await new Promise(resolve => setTimeout(resolve, 100))

        expect(onQuotaRefunded).toHaveBeenCalledTimes(1)
        expect(onQuotaRefunded).toHaveBeenCalledWith(kind, [])
      },
    )

    it('hands the run id from X-Agent-Run-ID to onRunStarted so the run can be stopped', async () => {
      const onRunStarted = vi.fn()
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        body: createMockStream(['event: done\ndata: {}\n\n']),
        headers: { get: (name: string) => (name === 'X-Agent-Run-ID' ? 'run-42' : null) },
      }))

      streamAgentRequest({ project_id: 'test-project', message: 'test' }, { onRunStarted })
      await new Promise(resolve => setTimeout(resolve, 100))

      expect(onRunStarted).toHaveBeenCalledWith('run-42')
    })

    it('passes session_id in stream request body when provided', async () => {
      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        body: createMockStream([]),
      })
      vi.stubGlobal('fetch', mockFetch)

      streamAgentRequest(
        { project_id: 'test-project', message: 'test', session_id: 'session-123' },
        {}
      )

      await new Promise(resolve => setTimeout(resolve, 100))

      const [, options] = mockFetch.mock.calls[0]
      const body = JSON.parse(options.body)
      expect(body.session_id).toBe('session-123')
    })

    it('passes selected_skill_ids in stream request body only when non-empty', async () => {
      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        body: createMockStream([]),
      })
      vi.stubGlobal('fetch', mockFetch)

      streamAgentRequest(
        { project_id: 'test-project', message: 'test', selected_skill_ids: ['s1', 's2'] },
        {}
      )
      streamAgentRequest(
        { project_id: 'test-project', message: 'test', selected_skill_ids: [] },
        {}
      )

      await new Promise(resolve => setTimeout(resolve, 100))

      expect(JSON.parse(mockFetch.mock.calls[0][1].body).selected_skill_ids).toEqual(['s1', 's2'])
      expect(JSON.parse(mockFetch.mock.calls[1][1].body)).not.toHaveProperty('selected_skill_ids')
    })

    it('calls onContent for each content chunk', async () => {
      const onContent = vi.fn()
      const mockStream = createMockStream([
        'event: content\ndata: {"text":"Hello "}\n\n',
        'event: content\ndata: {"text":"World"}\n\n',
      ])

      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        body: mockStream,
      })
      vi.stubGlobal('fetch', mockFetch)

      streamAgentRequest(
        { project_id: 'test-project', message: 'test' },
        { onContent }
      )

      // Wait for stream to complete
      await new Promise(resolve => setTimeout(resolve, 100))

      expect(onContent).toHaveBeenCalledTimes(2)
      expect(onContent).toHaveBeenNthCalledWith(1, 'Hello ', undefined)
      expect(onContent).toHaveBeenNthCalledWith(2, 'World', undefined)
    })

    it('calls onToolCall with parsed arguments', async () => {
      const onToolCall = vi.fn()
      const mockStream = createMockStream([
        'event: tool_call\ndata: {"tool_name":"create_file","arguments":{"path":"test.md","content":"hello"}}\n\n',
      ])

      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        body: mockStream,
      })
      vi.stubGlobal('fetch', mockFetch)

      streamAgentRequest(
        { project_id: 'test-project', message: 'test' },
        { onToolCall }
      )

      await new Promise(resolve => setTimeout(resolve, 100))

      expect(onToolCall).toHaveBeenCalledTimes(1)
      expect(onToolCall).toHaveBeenCalledWith('create_file', {
        path: 'test.md',
        content: 'hello',
      })
    })

    it('calls onToolResult with status and data', async () => {
      const onToolResult = vi.fn()
      const mockStream = createMockStream([
        'event: tool_result\ndata: {"tool_name":"create_file","status":"success","data":{"file_id":"123"}}\n\n',
      ])

      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        body: mockStream,
      })
      vi.stubGlobal('fetch', mockFetch)

      streamAgentRequest(
        { project_id: 'test-project', message: 'test' },
        { onToolResult }
      )

      await new Promise(resolve => setTimeout(resolve, 100))

      expect(onToolResult).toHaveBeenCalledWith(
        'create_file',
        'success',
        { file_id: '123' },
        undefined
      )
    })

    it('passes tool_use_id to tool callbacks when present', async () => {
      const onToolCall = vi.fn()
      const onToolResult = vi.fn()
      const mockStream = createMockStream([
        'event: tool_call\ndata: {"tool_use_id":"tool-123","tool_name":"update_project","arguments":{"tasks":[]}}\n\n',
        'event: tool_result\ndata: {"tool_use_id":"tool-123","tool_name":"update_project","status":"success","data":{"ok":true}}\n\n',
      ])

      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        body: mockStream,
      })
      vi.stubGlobal('fetch', mockFetch)

      streamAgentRequest(
        { project_id: 'test-project', message: 'test' },
        { onToolCall, onToolResult }
      )

      await new Promise(resolve => setTimeout(resolve, 100))

      expect(onToolCall).toHaveBeenCalledWith(
        'update_project',
        { tasks: [] },
        'tool-123'
      )
      expect(onToolResult).toHaveBeenCalledWith(
        'update_project',
        'success',
        { ok: true },
        undefined,
        'tool-123'
      )
    })

    it('passes file metadata on file_edit_start/file_edit_end when provided', async () => {
      const onFileEditStart = vi.fn()
      const onFileEditEnd = vi.fn()
      const mockStream = createMockStream([
        'event: file_edit_start\ndata: {"file_id":"file-1","title":"Chapter 1","total_edits":2,"file_type":"outline"}\n\n',
        'event: file_edit_end\ndata: {"file_id":"file-1","edits_applied":2,"new_length":123,"file_type":"outline","title":"Chapter 1"}\n\n',
      ])

      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        body: mockStream,
      })
      vi.stubGlobal('fetch', mockFetch)

      streamAgentRequest(
        { project_id: 'test-project', message: 'test' },
        { onFileEditStart, onFileEditEnd }
      )

      await new Promise(resolve => setTimeout(resolve, 100))

      expect(onFileEditStart).toHaveBeenCalledWith('file-1', 'Chapter 1', 2, 'outline')
      // 第 8 个参数是新增的批量编辑结果（failed_count / warnings 等）。
      // 事件里没带这些字段时给出安全默认值，等价于"全部成功"。
      expect(onFileEditEnd).toHaveBeenCalledWith(
        'file-1',
        2,
        123,
        undefined,
        undefined,
        'outline',
        'Chapter 1',
        {
          failedCount: 0,
          partialSuccess: false,
          allFailed: false,
          warnings: [],
        }
      )
    })

    it('handles abort signal correctly', async () => {
      const onError = vi.fn()
      const mockFetch = vi.fn().mockImplementation(
        () =>
          new Promise((_, reject) => {
            setTimeout(() => {
              const error = new Error('The user aborted a request.')
              error.name = 'AbortError'
              reject(error)
            }, 50)
          })
      )
      vi.stubGlobal('fetch', mockFetch)

      const controller = streamAgentRequest(
        { project_id: 'test-project', message: 'test' },
        { onError }
      )

      // Abort immediately
      controller.abort()

      await new Promise(resolve => setTimeout(resolve, 100))

      // Should NOT call onError for abort
      expect(onError).not.toHaveBeenCalled()
    })

    it('calls onError on network failure', async () => {
      const onError = vi.fn()
      const mockFetch = vi.fn().mockRejectedValue(new Error('Network error'))
      vi.stubGlobal('fetch', mockFetch)

      streamAgentRequest(
        { project_id: 'test-project', message: 'test' },
        { onError }
      )

      await new Promise(resolve => setTimeout(resolve, 100))

      expect(onError).toHaveBeenCalledWith(
        'Network error',
        'STREAM_ERROR',
        true
      )
    })

    it('handles 401 with token refresh retry', async () => {
      const { tryRefreshToken, getAccessToken } = await import('../apiClient')
      const mockTryRefresh = tryRefreshToken as ReturnType<typeof vi.fn>
      const mockGetToken = getAccessToken as ReturnType<typeof vi.fn>

      mockGetToken.mockReturnValue('old-token')
      localStorage.setItem('access_token', 'old-token')
      mockTryRefresh.mockResolvedValue(true)

      const mockStream = createMockStream([
        'event: content\ndata: {"text":"Success"}\n\n',
      ])

      let callCount = 0
      const mockFetch = vi.fn().mockImplementation(() => {
        callCount++
        if (callCount === 1) {
          return Promise.resolve({
            ok: false,
            status: 401,
            json: async () => ({ detail: 'Unauthorized' }),
          })
        }
        return Promise.resolve({
          ok: true,
          status: 200,
          body: mockStream,
        })
      })
      vi.stubGlobal('fetch', mockFetch)

      const onContent = vi.fn()
      streamAgentRequest(
        { project_id: 'test-project', message: 'test' },
        { onContent }
      )

      await new Promise(resolve => setTimeout(resolve, 150))

      expect(mockTryRefresh).toHaveBeenCalled()
      expect(mockFetch).toHaveBeenCalledTimes(2)
      expect(onContent).toHaveBeenCalledWith('Success', undefined)
    })

    it('calls onError when token refresh fails', async () => {
      const { tryRefreshToken, clearAuthStorage } = await import('../apiClient')
      const mockTryRefresh = tryRefreshToken as ReturnType<typeof vi.fn>

      mockTryRefresh.mockResolvedValue(false)

      const mockFetch = vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        json: async () => ({ detail: 'Unauthorized' }),
      })
      vi.stubGlobal('fetch', mockFetch)

      const onError = vi.fn()
      streamAgentRequest(
        { project_id: 'test-project', message: 'test' },
        { onError }
      )

      await new Promise(resolve => setTimeout(resolve, 100))

      expect(clearAuthStorage).toHaveBeenCalledWith('agent_auth_failed')
      expect(onError).toHaveBeenCalledWith(
        'Translated: ERR_AUTH_TOKEN_INVALID',
        'AUTH_ERROR',
        false
      )
    })

    it('handles non-200 responses', async () => {
      const mockFetch = vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        json: async () => ({ detail: 'Internal Server Error' }),
      })
      vi.stubGlobal('fetch', mockFetch)

      const onError = vi.fn()
      streamAgentRequest(
        { project_id: 'test-project', message: 'test' },
        { onError }
      )

      await new Promise(resolve => setTimeout(resolve, 100))

      expect(onError).toHaveBeenCalledWith(
        'Internal Server Error',
        'HTTP_ERROR',
        false
      )
    })

    it('propagates backend error_code for non-200 responses', async () => {
      const mockFetch = vi.fn().mockResolvedValue({
        ok: false,
        status: 402,
        json: async () => ({
          detail: 'ERR_QUOTA_EXCEEDED',
          error_code: 'ERR_QUOTA_AI_CONVERSATIONS_EXCEEDED',
          error_detail: { message: 'quota detail' },
        }),
      })
      vi.stubGlobal('fetch', mockFetch)

      const onError = vi.fn()
      streamAgentRequest(
        { project_id: 'test-project', message: 'test' },
        { onError }
      )

      await new Promise(resolve => setTimeout(resolve, 100))

      expect(onError).toHaveBeenCalledWith(
        'Translated: ERR_QUOTA_AI_CONVERSATIONS_EXCEEDED',
        'ERR_QUOTA_AI_CONVERSATIONS_EXCEEDED',
        false
      )
    })

    it('marks a busy chat session (409 ERR_SESSION_BUSY) as retryable', async () => {
      const mockFetch = vi.fn().mockResolvedValue({
        ok: false,
        status: 409,
        json: async () => ({
          detail: 'This chat session already has an active generation.',
          error_code: 'ERR_SESSION_BUSY',
        }),
      })
      vi.stubGlobal('fetch', mockFetch)

      const onError = vi.fn()
      streamAgentRequest(
        { project_id: 'test-project', message: 'test' },
        { onError }
      )

      await new Promise(resolve => setTimeout(resolve, 100))

      expect(onError).toHaveBeenCalledWith(
        'Translated: ERR_SESSION_BUSY',
        'ERR_SESSION_BUSY',
        true
      )
    })

    it('ignores SSE heartbeat comment frames between events', async () => {
      const onContent = vi.fn()
      const onError = vi.fn()
      const onDone = vi.fn()
      const mockStream = createMockStream([
        ': ping\n\n',
        'event: content\ndata: {"text":"第一段"}\n\n: ping\n\n',
        ': ping\n\nevent: done\ndata: {}\n\n',
      ])
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, body: mockStream }))

      streamAgentRequest(
        { project_id: 'test-project', message: 'test' },
        { onContent, onError, onDone }
      )

      await new Promise(resolve => setTimeout(resolve, 100))

      expect(onContent).toHaveBeenCalledTimes(1)
      expect(onContent.mock.calls[0][0]).toBe('第一段')
      expect(onDone).toHaveBeenCalledTimes(1)
      expect(onError).not.toHaveBeenCalled()
    })

    it('shows the translated error code instead of the raw backend message', async () => {
      const onError = vi.fn()
      const mockStream = createMockStream([
        'event: error\ndata: {"message":"生成回复时发生错误，请重试","code":"ERR_AGENT_UPSTREAM_UNAVAILABLE","retryable":true,"refundable":true}\n\n',
      ])
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, body: mockStream }))

      streamAgentRequest({ project_id: 'test-project', message: 'test' }, { onError })

      await new Promise(resolve => setTimeout(resolve, 100))

      expect(onError).toHaveBeenCalledWith(
        'Translated: ERR_AGENT_UPSTREAM_UNAVAILABLE',
        'ERR_AGENT_UPSTREAM_UNAVAILABLE',
        true
      )
    })

    it('calls onThinking for thinking events', async () => {
      const onThinking = vi.fn()
      const mockStream = createMockStream([
        'event: thinking\ndata: {"message":"Analyzing...","step":"1"}\n\n',
      ])

      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        body: mockStream,
      })
      vi.stubGlobal('fetch', mockFetch)

      streamAgentRequest(
        { project_id: 'test-project', message: 'test' },
        { onThinking }
      )

      await new Promise(resolve => setTimeout(resolve, 100))

      expect(onThinking).toHaveBeenCalledWith('Analyzing...', '1')
    })

    it('calls onDone when stream completes', async () => {
      const onDone = vi.fn()
      const mockStream = createMockStream([
        'event: done\ndata: {"apply_action":"insert","refs":[1,2],"assistant_message_id":"assistant-1","session_id":"session-9","file_mutated":true}\n\n',
      ])

      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        body: mockStream,
      })
      vi.stubGlobal('fetch', mockFetch)

      streamAgentRequest(
        { project_id: 'test-project', message: 'test' },
        { onDone }
      )

      await new Promise(resolve => setTimeout(resolve, 100))

      expect(onDone).toHaveBeenCalledWith({
        apply_action: 'insert',
        refs: [1, 2],
        assistant_message_id: 'assistant-1',
        session_id: 'session-9',
        file_mutated: true,
      })
    })

    it('calls onConflict for conflict events', async () => {
      const onConflict = vi.fn()
      const mockStream = createMockStream([
        'event: conflict\ndata: {"type":"timeline_conflict","severity":"high","title":"Timeline Error","description":"Date mismatch","suggestions":["Fix date"]}\n\n',
      ])

      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        body: mockStream,
      })
      vi.stubGlobal('fetch', mockFetch)

      streamAgentRequest(
        { project_id: 'test-project', message: 'test' },
        { onConflict }
      )

      await new Promise(resolve => setTimeout(resolve, 100))

      expect(onConflict).toHaveBeenCalledWith({
        type: 'timeline_conflict',
        severity: 'high',
        title: 'Timeline Error',
        description: 'Date mismatch',
        suggestions: ['Fix date'],
      })
    })

    it('calls onHandoff for handoff events', async () => {
      const onHandoff = vi.fn()
      const mockStream = createMockStream([
        'event: handoff\ndata: {"target_agent":"writer","reason":"continue writing","context":"from planner","handoff_packet":{"target_agent":"writer","reason":"continue writing","context":"from planner","completed":["outline done"],"todo":["write section"],"evidence":["plan.md"]}}\n\n',
      ])

      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        body: mockStream,
      })
      vi.stubGlobal('fetch', mockFetch)

      streamAgentRequest(
        { project_id: 'test-project', message: 'test' },
        { onHandoff }
      )

      await new Promise(resolve => setTimeout(resolve, 100))

      expect(onHandoff).toHaveBeenCalledWith({
        target_agent: 'writer',
        reason: 'continue writing',
        context: 'from planner',
        handoff_packet: {
          target_agent: 'writer',
          reason: 'continue writing',
          context: 'from planner',
          completed: ['outline done'],
          todo: ['write section'],
          evidence: ['plan.md'],
        },
      })
    })

    it('calls onRouterDecided with routing metadata', async () => {
      const onRouterDecided = vi.fn()
      const mockStream = createMockStream([
        'event: router_decided\ndata: {"initial_agent":"planner","workflow_plan":"standard","workflow_agents":["writer","quality_reviewer"],"routing_metadata":{"agent_type":"planner","workflow_type":"standard","reason":"需要先规划","confidence":0.93}}\n\n',
      ])

      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        body: mockStream,
      })
      vi.stubGlobal('fetch', mockFetch)

      streamAgentRequest(
        { project_id: 'test-project', message: 'test' },
        { onRouterDecided }
      )

      await new Promise(resolve => setTimeout(resolve, 100))

      expect(onRouterDecided).toHaveBeenCalledWith(
        'planner',
        'standard',
        ['writer', 'quality_reviewer'],
        {
          agent_type: 'planner',
          workflow_type: 'standard',
          reason: '需要先规划',
          confidence: 0.93,
        }
      )
    })

    it('calls workflow callbacks with evaluation payload', async () => {
      const onWorkflowStopped = vi.fn()
      const onWorkflowComplete = vi.fn()
      const mockStream = createMockStream([
        'event: workflow_stopped\ndata: {"reason":"clarification_needed","agent_type":"writer","message":"等待您的回复","confidence":0.82,"evaluation":{"complete_score":0.2,"clarification_score":0.82,"consistency_score":0.38,"decision_reason":"heuristic_clarification"}}\n\n',
        'event: workflow_complete\ndata: {"reason":"task_complete","agent_type":"writer","message":"任务已完成","confidence":1.0,"evaluation":{"complete_score":1.0,"clarification_score":0.0,"consistency_score":0.0,"decision_reason":"explicit_complete_marker"}}\n\n',
      ])

      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        body: mockStream,
      })
      vi.stubGlobal('fetch', mockFetch)

      streamAgentRequest(
        { project_id: 'test-project', message: 'test' },
        { onWorkflowStopped, onWorkflowComplete }
      )

      await new Promise(resolve => setTimeout(resolve, 100))

      expect(onWorkflowStopped).toHaveBeenCalledWith({
        reason: 'clarification_needed',
        agent_type: 'writer',
        message: '等待您的回复',
        confidence: 0.82,
        evaluation: {
          complete_score: 0.2,
          clarification_score: 0.82,
          consistency_score: 0.38,
          decision_reason: 'heuristic_clarification',
        },
      })

      expect(onWorkflowComplete).toHaveBeenCalledWith({
        reason: 'task_complete',
        agent_type: 'writer',
        message: '任务已完成',
        confidence: 1.0,
        evaluation: {
          complete_score: 1.0,
          clarification_score: 0.0,
          consistency_score: 0.0,
          decision_reason: 'explicit_complete_marker',
        },
      })
    })

    it('calls onFileCreated for file creation events', async () => {
      const onFileCreated = vi.fn()
      const mockStream = createMockStream([
        'event: file_created\ndata: {"file_id":"123","file_type":"draft","title":"Chapter 1"}\n\n',
      ])

      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        body: mockStream,
      })
      vi.stubGlobal('fetch', mockFetch)

      streamAgentRequest(
        { project_id: 'test-project', message: 'test' },
        { onFileCreated }
      )

      await new Promise(resolve => setTimeout(resolve, 100))

      expect(onFileCreated).toHaveBeenCalledWith('123', 'draft', 'Chapter 1')
    })

    it('calls onContext for context events', async () => {
      const onContext = vi.fn()
      const mockStream = createMockStream([
        'event: context\ndata: {"items":[{"id":"1","type":"draft","title":"Chapter 1","content":"...","relevance_score":0.9}],"token_count":1500}\n\n',
      ])

      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        body: mockStream,
      })
      vi.stubGlobal('fetch', mockFetch)

      streamAgentRequest(
        { project_id: 'test-project', message: 'test' },
        { onContext }
      )

      await new Promise(resolve => setTimeout(resolve, 100))

      expect(onContext).toHaveBeenCalledWith(
        [{ id: '1', type: 'draft', title: 'Chapter 1', content: '...', relevance_score: 0.9 }],
        1500
      )
    })

    it('handles error event from server', async () => {
      const onError = vi.fn()
      const mockStream = createMockStream([
        'event: error\ndata: {"message":"Rate limit exceeded","code":"RATE_LIMIT","retryable":true}\n\n',
      ])

      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        body: mockStream,
      })
      vi.stubGlobal('fetch', mockFetch)

      streamAgentRequest(
        { project_id: 'test-project', message: 'test' },
        { onError }
      )

      await new Promise(resolve => setTimeout(resolve, 100))

      expect(onError).toHaveBeenCalledWith(
        'Rate limit exceeded',
        'RATE_LIMIT',
        true
      )
    })

    it('prefers ERR_ error code over raw server message for error events', async () => {
      const onError = vi.fn()
      const mockStream = createMockStream([
        'event: error\ndata: {"message":"quota exceeded","code":"ERR_QUOTA_AI_CONVERSATIONS_EXCEEDED","retryable":false}\n\n',
      ])

      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        body: mockStream,
      })
      vi.stubGlobal('fetch', mockFetch)

      streamAgentRequest(
        { project_id: 'test-project', message: 'test' },
        { onError }
      )

      await new Promise(resolve => setTimeout(resolve, 100))

      expect(onError).toHaveBeenCalledWith(
        'Translated: ERR_QUOTA_AI_CONVERSATIONS_EXCEEDED',
        'ERR_QUOTA_AI_CONVERSATIONS_EXCEEDED',
        false
      )
    })

    it('handles multiple events in single chunk', async () => {
      const onContent = vi.fn()
      const mockStream = createMockStream([
        'event: content\ndata: {"text":"Hello "}\n\nevent: content\ndata: {"text":"World"}\n\n',
      ])

      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        body: mockStream,
      })
      vi.stubGlobal('fetch', mockFetch)

      streamAgentRequest(
        { project_id: 'test-project', message: 'test' },
        { onContent }
      )

      await new Promise(resolve => setTimeout(resolve, 100))

      expect(onContent).toHaveBeenCalledTimes(2)
    })

    it('updates debug context session id when session_started event arrives', async () => {
      const mockStream = createMockStream([
        'event: session_started\ndata: {"session_id":"session-from-server"}\n\n',
        'event: done\ndata: {}\n\n',
      ])
      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        body: mockStream,
        headers: {
          get: (name: string) => {
            if (name === 'X-Trace-ID') return 'trace-1'
            if (name === 'X-Request-ID') return 'req-1'
            if (name === 'X-Agent-Run-ID') return 'run-1'
            return null
          },
        },
      })
      vi.stubGlobal('fetch', mockFetch)

      streamAgentRequest(
        { project_id: 'test-project', message: 'test' },
        {}
      )

      await new Promise(resolve => setTimeout(resolve, 100))

      expect(debugContext.get()?.agent_session_id).toBe('session-from-server')
    })

    it('parses trailing done event even without final blank line delimiter', async () => {
      const onDone = vi.fn()
      const onError = vi.fn()
      const mockStream = createMockStream([
        'event: done\ndata: {"apply_action":"insert"}\n',
      ])

      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        body: mockStream,
      })
      vi.stubGlobal('fetch', mockFetch)

      streamAgentRequest(
        { project_id: 'test-project', message: 'test' },
        { onDone, onError }
      )

      await new Promise(resolve => setTimeout(resolve, 100))

      expect(onDone).toHaveBeenCalledWith({ apply_action: 'insert' })
      expect(onError).not.toHaveBeenCalledWith(
        expect.stringContaining('Connection interrupted'),
        'STREAM_CLOSED',
        true
      )
    })
  })

  describe('stopAgentRun', () => {
    it('asks the server to stop the run and reports whether it was recorded', async () => {
      vi.mocked(api.post).mockResolvedValueOnce({ stop_requested: true })
      await expect(stopAgentRun('run-42')).resolves.toBe(true)
      expect(api.post).toHaveBeenCalledWith('/api/v1/agent/stop', { agent_run_id: 'run-42' })

      vi.mocked(api.post).mockRejectedValueOnce(new Error('offline'))
      await expect(stopAgentRun('run-42')).resolves.toBe(false)
    })

    it('sends the files the editor could not save so a refunded round keeps them', async () => {
      vi.mocked(api.post).mockResolvedValueOnce({ stop_requested: true })
      await expect(stopAgentRun('run-42', ['ch-typed'])).resolves.toBe(true)
      expect(api.post).toHaveBeenCalledWith('/api/v1/agent/stop', {
        agent_run_id: 'run-42',
        keep_file_ids: ['ch-typed'],
      })
    })
  })

  describe('fetchSuggestions', () => {
    it('returns suggestions array on success', async () => {
      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          suggestions: ['Write chapter 1', 'Add character', 'Continue story'],
        }),
      })
      vi.stubGlobal('fetch', mockFetch)

      const result = await fetchSuggestions('test-project')

      expect(result).toEqual(['Write chapter 1', 'Add character', 'Continue story'])
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/agent/suggest'),
        expect.objectContaining({
          method: 'POST',
          body: expect.stringContaining('"count":3'),
        })
      )
    })

    it('returns empty array on failure', async () => {
      const mockFetch = vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
      })
      vi.stubGlobal('fetch', mockFetch)

      const result = await fetchSuggestions('test-project')

      expect(result).toEqual([])
    })

    it('stays silent when the suggestion rate limit or daily cap is hit (429)', async () => {
      const { logger } = await import('../logger')
      const errorSpy = vi.spyOn(logger, 'error')
      const mockFetch = vi.fn().mockResolvedValue({
        ok: false,
        status: 429,
        json: async () => ({ detail: 'Rate limit exceeded. Please try again later.' }),
      })
      vi.stubGlobal('fetch', mockFetch)

      const result = await fetchSuggestions('test-project')

      expect(result).toEqual([])
      expect(errorSpy).not.toHaveBeenCalled()
      errorSpy.mockRestore()
    })

    it('limits count to max 5', async () => {
      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ suggestions: [] }),
      })
      vi.stubGlobal('fetch', mockFetch)

      await fetchSuggestions('test-project', undefined, 10)

      const callArgs = mockFetch.mock.calls[0]
      const body = JSON.parse(callArgs[1].body)
      expect(body.count).toBe(5)
    })

    it('slices recent messages to last 5', async () => {
      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ suggestions: [] }),
      })
      vi.stubGlobal('fetch', mockFetch)

      const messages = [
        { role: 'user', content: 'msg1' },
        { role: 'assistant', content: 'msg2' },
        { role: 'user', content: 'msg3' },
        { role: 'assistant', content: 'msg4' },
        { role: 'user', content: 'msg5' },
        { role: 'assistant', content: 'msg6' },
        { role: 'user', content: 'msg7' },
      ]

      await fetchSuggestions('test-project', messages)

      const callArgs = mockFetch.mock.calls[0]
      const body = JSON.parse(callArgs[1].body)
      expect(body.recent_messages).toHaveLength(5)
      expect(body.recent_messages[0].content).toBe('msg3')
    })

    it('handles 401 with token refresh', async () => {
      const { tryRefreshToken } = await import('../apiClient')
      const mockTryRefresh = tryRefreshToken as ReturnType<typeof vi.fn>
      mockTryRefresh.mockResolvedValue(true)

      let callCount = 0
      const mockFetch = vi.fn().mockImplementation(() => {
        callCount++
        if (callCount === 1) {
          return Promise.resolve({ ok: false, status: 401 })
        }
        return Promise.resolve({
          ok: true,
          json: async () => ({ suggestions: ['test'] }),
        })
      })
      vi.stubGlobal('fetch', mockFetch)

      const result = await fetchSuggestions('test-project')

      expect(mockTryRefresh).toHaveBeenCalled()
      expect(mockFetch).toHaveBeenCalledTimes(2)
      expect(result).toEqual(['test'])
    })

    it('returns empty array on network error', async () => {
      const mockFetch = vi.fn().mockRejectedValue(new Error('Network error'))
      vi.stubGlobal('fetch', mockFetch)

      const result = await fetchSuggestions('test-project')

      expect(result).toEqual([])
    })

    it('handles missing suggestions field', async () => {
      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ data: 'something else' }),
      })
      vi.stubGlobal('fetch', mockFetch)

      const result = await fetchSuggestions('test-project')

      expect(result).toEqual([])
    })
  })

  describe('sendSteeringRequest', () => {
    it('posts steering message with auth and base URL', async () => {
      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ message_id: 'steer-1', queued: true }),
      })
      vi.stubGlobal('fetch', mockFetch)

      const result = await sendSteeringRequest('session-1', 'focus chapter 2')

      expect(result).toEqual({ message_id: 'steer-1', queued: true })
      expect(mockFetch).toHaveBeenCalledWith(
        'http://localhost:8000/api/v1/agent/steer',
        expect.objectContaining({
          method: 'POST',
          headers: expect.objectContaining({
            'Content-Type': 'application/json',
            Authorization: 'Bearer test-access-token',
          }),
          body: JSON.stringify({
            session_id: 'session-1',
            message: 'focus chapter 2',
          }),
        })
      )
    })

    it('retries once after 401 when refresh succeeds', async () => {
      const { tryRefreshToken } = await import('../apiClient')
      const mockTryRefresh = tryRefreshToken as ReturnType<typeof vi.fn>
      mockTryRefresh.mockResolvedValue(true)

      let callCount = 0
      const mockFetch = vi.fn().mockImplementation(() => {
        callCount++
        if (callCount === 1) {
          return Promise.resolve({ ok: false, status: 401 })
        }
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({ message_id: 'steer-2', queued: true }),
        })
      })
      vi.stubGlobal('fetch', mockFetch)

      const result = await sendSteeringRequest('session-2', 'revise ending')

      expect(result).toEqual({ message_id: 'steer-2', queued: true })
      expect(mockTryRefresh).toHaveBeenCalledTimes(1)
      expect(mockFetch).toHaveBeenCalledTimes(2)
    })
  })
})
