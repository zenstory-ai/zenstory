import { renderHook, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { useAgentStream } from '../useAgentStream'
import * as agentApi from '@/lib/agentApi'

// Mock agentApi
vi.mock('@/lib/agentApi', () => ({
  streamAgentRequest: vi.fn(),
  sendSteeringRequest: vi.fn(),
}))

// Helper to simulate SSE events
function createMockStreamController() {
  let callbacks: Parameters<typeof agentApi.streamAgentRequest>[1] | null = null

  const mockAbortController = {
    abort: vi.fn(),
    signal: {} as AbortSignal,
  }

  vi.mocked(agentApi.streamAgentRequest).mockImplementation((_request, cbs) => {
    callbacks = cbs
    return mockAbortController as unknown as AbortController
  })

  return {
    mockAbortController,
    getCallbacks: () => callbacks,
  }
}

describe('useAgentStream', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers()
    vi.mocked(agentApi.sendSteeringRequest).mockResolvedValue({
      message_id: 'steer-1',
      queued: true,
    })
  })

  afterEach(() => {
    vi.clearAllTimers()
    vi.useRealTimers()
  })

  describe('initial state', () => {
    it('initializes with correct default state', () => {
      const { result } = renderHook(() => useAgentStream('test-project-id'))

      expect(result.current.isStreaming).toBe(false)
      expect(result.current.isThinking).toBe(false)
      expect(result.current.segments).toEqual([])
      expect(result.current.content).toBe('')
      expect(result.current.error).toBe(null)
      expect(result.current.conflicts).toEqual([])
    })

    it('accepts projectId parameter', () => {
      const { result } = renderHook(() => useAgentStream('my-project-123'))
      expect(result.current.isStreaming).toBe(false)
    })
  })

  describe('startStream', () => {
    it('sets isStreaming=true when startStream called', () => {
      const { result } = renderHook(() => useAgentStream('test-project-id'))

      act(() => {
        result.current.startStream({ message: 'test message' })
      })

      expect(result.current.isStreaming).toBe(true)
      expect(result.current.isThinking).toBe(true)
    })

    it('clears previous state on new stream', () => {
      const { result } = renderHook(() => useAgentStream('test-project-id'))
      const controller = createMockStreamController()

      // First stream
      act(() => {
        result.current.startStream({ message: 'first' })
      })

      const callbacks = controller.getCallbacks()!
      act(() => {
        callbacks.onContent?.('first content')
        callbacks.onDone?.({})
      })

      expect(result.current.content).toBe('first content')

      // Second stream should clear
      act(() => {
        result.current.startStream({ message: 'second' })
      })

      expect(result.current.content).toBe('')
      expect(result.current.segments).toEqual([])
    })

    it('calls onStart callback', () => {
      const onStart = vi.fn()
      const { result } = renderHook(() =>
        useAgentStream('test-project-id', { onStart })
      )

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      expect(onStart).toHaveBeenCalledTimes(1)
    })

    it('passes request parameters to streamAgentRequest', () => {
      const { result } = renderHook(() => useAgentStream('project-123'))

      act(() => {
        result.current.startStream({
          message: 'Hello AI',
          session_id: 'session-123',
          selected_text: 'selected',
          context_before: 'before',
          context_after: 'after',
        })
      })

      expect(agentApi.streamAgentRequest).toHaveBeenCalledWith(
        expect.objectContaining({
          project_id: 'project-123',
          message: 'Hello AI',
          session_id: 'session-123',
          selected_text: 'selected',
          context_before: 'before',
          context_after: 'after',
        }),
        expect.any(Object)
      )
    })

    it('reuses active session_id when next request omits it', () => {
      const { result } = renderHook(() => useAgentStream('project-123'))
      const controller = createMockStreamController()

      // First turn: receive session_started from backend
      act(() => {
        result.current.startStream({ message: 'first turn' })
      })
      const callbacks = controller.getCallbacks()!
      act(() => {
        callbacks.onSessionStarted?.('session-active-1')
      })

      // Second turn: caller does not pass session_id, hook should reuse active one
      act(() => {
        result.current.startStream({ message: 'second turn' })
      })

      expect(agentApi.streamAgentRequest).toHaveBeenLastCalledWith(
        expect.objectContaining({
          project_id: 'project-123',
          message: 'second turn',
          session_id: 'session-active-1',
        }),
        expect.any(Object)
      )
    })

    it('clears previous session_id when projectId changes', () => {
      const controller = createMockStreamController()
      const { result, rerender } = renderHook(
        ({ projectId }: { projectId: string }) => useAgentStream(projectId),
        { initialProps: { projectId: 'project-a' } }
      )

      act(() => {
        result.current.startStream({ message: 'first turn' })
      })

      const callbacks = controller.getCallbacks()!
      act(() => {
        callbacks.onSessionStarted?.('session-a-1')
      })

      expect(result.current.sessionId).toBe('session-a-1')

      act(() => {
        rerender({ projectId: 'project-b' })
      })

      expect(result.current.sessionId).toBe(null)

      act(() => {
        result.current.startStream({ message: 'new project turn' })
      })

      expect(agentApi.streamAgentRequest).toHaveBeenLastCalledWith(
        expect.objectContaining({
          project_id: 'project-b',
          message: 'new project turn',
          session_id: undefined,
        }),
        expect.any(Object)
      )
    })

    it('ignores late onSessionStarted from previous project after switch', () => {
      const controller = createMockStreamController()
      const { result, rerender } = renderHook(
        ({ projectId }: { projectId: string }) => useAgentStream(projectId),
        { initialProps: { projectId: 'project-a' } }
      )

      act(() => {
        result.current.startStream({ message: 'first turn' })
      })

      const previousCallbacks = controller.getCallbacks()!

      act(() => {
        rerender({ projectId: 'project-b' })
      })

      expect(controller.mockAbortController.abort).toHaveBeenCalledTimes(1)

      // Stale callback from the old project stream should be ignored.
      act(() => {
        previousCallbacks.onSessionStarted?.('session-a-late')
      })
      expect(result.current.sessionId).toBe(null)

      act(() => {
        result.current.startStream({ message: 'new project turn' })
      })

      expect(agentApi.streamAgentRequest).toHaveBeenLastCalledWith(
        expect.objectContaining({
          project_id: 'project-b',
          message: 'new project turn',
          session_id: undefined,
        }),
        expect.any(Object)
      )
    })

    it('ignores late onSessionStarted from a canceled stream in same project', () => {
      const controller = createMockStreamController()
      const { result } = renderHook(() => useAgentStream('project-123'))

      act(() => {
        result.current.startStream({ message: 'first turn' })
      })
      const firstCallbacks = controller.getCallbacks()!

      act(() => {
        result.current.startStream({ message: 'second turn' })
      })
      const secondCallbacks = controller.getCallbacks()!

      // Late callback from previous stream should be ignored.
      act(() => {
        firstCallbacks.onSessionStarted?.('session-old')
      })
      expect(result.current.sessionId).toBe(null)

      act(() => {
        secondCallbacks.onSessionStarted?.('session-new')
      })
      expect(result.current.sessionId).toBe('session-new')

      act(() => {
        result.current.startStream({ message: 'third turn' })
      })

      expect(agentApi.streamAgentRequest).toHaveBeenLastCalledWith(
        expect.objectContaining({
          project_id: 'project-123',
          message: 'third turn',
          session_id: 'session-new',
        }),
        expect.any(Object)
      )
    })

    it('ignores stale content callbacks from previous stream after restart', () => {
      const controller = createMockStreamController()
      const { result } = renderHook(() => useAgentStream('project-123'))

      act(() => {
        result.current.startStream({ message: 'first turn' })
      })
      const firstCallbacks = controller.getCallbacks()!

      act(() => {
        result.current.startStream({ message: 'second turn' })
      })
      const secondCallbacks = controller.getCallbacks()!

      // Stale content from canceled stream should be ignored.
      act(() => {
        firstCallbacks.onContent?.('stale-content')
      })
      act(() => {
        vi.advanceTimersByTime(100)
      })
      expect(result.current.content).toBe('')

      // Current stream content should still work.
      act(() => {
        secondCallbacks.onContent?.('fresh-content')
      })
      act(() => {
        vi.advanceTimersByTime(100)
      })
      expect(result.current.content).toBe('fresh-content')
    })
  })

  describe('content streaming', () => {
    it('accumulates content segments correctly', async () => {
      const { result } = renderHook(() => useAgentStream('test-project-id'))
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      const callbacks = controller.getCallbacks()!

      act(() => {
        callbacks.onContentStart?.()
      })

      act(() => {
        callbacks.onContent?.('Hello')
      })
      act(() => {
        vi.runOnlyPendingTimers()
      })
      expect(result.current.content).toBe('Hello')

      act(() => {
        callbacks.onContent?.(' World')
      })
      act(() => {
        vi.runOnlyPendingTimers()
      })
      expect(result.current.content).toBe('Hello World')
    })

    it('creates content segment on onContentStart', () => {
      const onSegmentStart = vi.fn()
      const { result } = renderHook(() =>
        useAgentStream('test-project-id', { onSegmentStart })
      )
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      const callbacks = controller.getCallbacks()!

      act(() => {
        callbacks.onContentStart?.()
      })

      expect(result.current.segments).toHaveLength(1)
      expect(result.current.segments[0].type).toBe('content')
      expect(result.current.segments[0].isStreaming).toBe(true)
      expect(onSegmentStart).toHaveBeenCalledWith(
        expect.objectContaining({ type: 'content' })
      )
    })

    it('updates segment content on onContent', () => {
      const onSegmentUpdate = vi.fn()
      const { result } = renderHook(() =>
        useAgentStream('test-project-id', { onSegmentUpdate })
      )
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      const callbacks = controller.getCallbacks()!

      act(() => {
        callbacks.onContentStart?.()
        callbacks.onContent?.('Hello')
      })
      act(() => {
        vi.runOnlyPendingTimers()
      })

      expect(result.current.segments[0].content).toBe('Hello')
      expect(onSegmentUpdate).toHaveBeenCalledWith(
        expect.any(String),
        'Hello'
      )
    })

    it('marks segment as complete on onContentEnd', () => {
      const onSegmentEnd = vi.fn()
      const { result } = renderHook(() =>
        useAgentStream('test-project-id', { onSegmentEnd })
      )
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      const callbacks = controller.getCallbacks()!

      act(() => {
        callbacks.onContentStart?.()
        callbacks.onContent?.('Hello')
      })

      expect(result.current.segments[0].isStreaming).toBe(true)

      act(() => {
        callbacks.onContentEnd?.()
      })

      expect(result.current.segments[0].isStreaming).toBe(false)
    })
  })

  describe('tool calls', () => {
    it('handles tool calls with pending status', async () => {
      const { result } = renderHook(() => useAgentStream('test-project-id'))
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      const callbacks = controller.getCallbacks()!

      act(() => {
        callbacks.onToolCall?.('create_file', { title: 'test.txt' })
      })

      expect(result.current.state.toolCalls).toHaveLength(1)
      expect(result.current.state.toolCalls[0].tool_name).toBe('create_file')
      expect(result.current.state.toolCalls[0].status).toBe('pending')
    })

    it('creates tool_calls segment on first tool call', async () => {
      const onSegmentStart = vi.fn()
      const { result } = renderHook(() =>
        useAgentStream('test-project-id', { onSegmentStart })
      )
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      const callbacks = controller.getCallbacks()!

      act(() => {
        callbacks.onToolCall?.('create_file', { title: 'test.txt' })
      })

      expect(result.current.segments).toHaveLength(1)
      expect(result.current.segments[0].type).toBe('tool_calls')
      expect(result.current.segments[0].toolCalls).toHaveLength(1)
      expect(onSegmentStart).toHaveBeenCalled()
    })

    it('updates tool status on tool result', async () => {
      const { result } = renderHook(() => useAgentStream('test-project-id'))
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      const callbacks = controller.getCallbacks()!

      act(() => {
        callbacks.onToolCall?.('create_file', { title: 'test.txt' })
      })

      expect(result.current.state.toolCalls[0].status).toBe('pending')

      act(() => {
        callbacks.onToolResult?.('create_file', 'success', { file_id: '123' })
      })

      expect(result.current.state.toolCalls[0].status).toBe('success')
      expect(result.current.state.toolCalls[0].result).toEqual({ file_id: '123' })
    })

    it('matches tool results by tool_use_id when tool names are duplicated', async () => {
      const { result } = renderHook(() => useAgentStream('test-project-id'))
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      const callbacks = controller.getCallbacks()!

      act(() => {
        callbacks.onToolCall?.('update_project', { tasks: ['a'] }, 'tool-1')
        callbacks.onToolCall?.('update_project', { tasks: ['b'] }, 'tool-2')
      })

      expect(result.current.state.toolCalls).toHaveLength(2)
      expect(result.current.state.toolCalls[0].status).toBe('pending')
      expect(result.current.state.toolCalls[1].status).toBe('pending')

      act(() => {
        callbacks.onToolResult?.('update_project', 'success', { idx: 2 }, undefined, 'tool-2')
      })

      expect(result.current.state.toolCalls[0].status).toBe('pending')
      expect(result.current.state.toolCalls[1].status).toBe('success')
      expect(result.current.state.toolCalls[1].result).toEqual({ idx: 2 })
    })

    it('merges multiple tool calls into single segment', async () => {
      const { result } = renderHook(() => useAgentStream('test-project-id'))
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      const callbacks = controller.getCallbacks()!

      act(() => {
        callbacks.onToolCall?.('create_file', { title: 'a.txt' })
        callbacks.onToolCall?.('edit_file', { file_id: '1' })
      })

      expect(result.current.segments).toHaveLength(1)
      expect(result.current.segments[0].toolCalls).toHaveLength(2)
    })

    it('calls onToolCall and onToolResult callbacks', async () => {
      const onToolCall = vi.fn()
      const onToolResult = vi.fn()
      const { result } = renderHook(() =>
        useAgentStream('test-project-id', { onToolCall, onToolResult })
      )
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      const callbacks = controller.getCallbacks()!

      act(() => {
        callbacks.onToolCall?.('create_file', { title: 'test.txt' })
      })

      expect(onToolCall).toHaveBeenCalledWith('create_file', { title: 'test.txt' })

      act(() => {
        callbacks.onToolResult?.('create_file', 'success', { id: '1' })
      })

      expect(onToolResult).toHaveBeenCalledWith('create_file', 'success', { id: '1' }, undefined)
    })

    it('marks tool_calls segment as complete when all tools done', async () => {
      const { result } = renderHook(() => useAgentStream('test-project-id'))
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      const callbacks = controller.getCallbacks()!

      act(() => {
        callbacks.onToolCall?.('tool1', {})
        callbacks.onToolCall?.('tool2', {})
      })

      expect(result.current.segments[0].isStreaming).toBe(true)

      act(() => {
        callbacks.onToolResult?.('tool1', 'success')
      })

      // Still streaming because tool2 is pending
      expect(result.current.segments[0].isStreaming).toBe(true)

      act(() => {
        callbacks.onToolResult?.('tool2', 'success')
      })

      // Now complete
      expect(result.current.segments[0].isStreaming).toBe(false)
    })
  })

  describe('reset', () => {
    it('resets state correctly', () => {
      const { result } = renderHook(() => useAgentStream('test-project-id'))
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      const callbacks = controller.getCallbacks()!

      act(() => {
        callbacks.onContent?.('some content')
      })
      act(() => {
        vi.runOnlyPendingTimers()
      })

      expect(result.current.content).toBe('some content')
      expect(result.current.isStreaming).toBe(true)

      act(() => {
        result.current.reset()
      })

      expect(result.current.segments).toEqual([])
      expect(result.current.isStreaming).toBe(false)
      expect(result.current.content).toBe('')
      expect(result.current.error).toBe(null)
    })

    it('aborts ongoing stream on reset', () => {
      const { result } = renderHook(() => useAgentStream('test-project-id'))
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      act(() => {
        result.current.reset()
      })

      expect(controller.mockAbortController.abort).toHaveBeenCalled()
    })

    it('clears active session so next stream does not reuse old session_id', () => {
      const { result } = renderHook(() => useAgentStream('project-123'))
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'first turn' })
      })

      const callbacks = controller.getCallbacks()!
      act(() => {
        callbacks.onSessionStarted?.('session-old-1')
      })

      expect(result.current.sessionId).toBe('session-old-1')

      act(() => {
        result.current.reset()
      })

      expect(result.current.sessionId).toBe(null)

      act(() => {
        result.current.startStream({ message: 'new conversation' })
      })

      const [request] = vi.mocked(agentApi.streamAgentRequest).mock.calls.at(-1)!
      expect(request.session_id).toBeUndefined()
    })
  })

  describe('cancel', () => {
    it('cancels stream on cancel() call', () => {
      const { result } = renderHook(() => useAgentStream('test-project-id'))
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      expect(result.current.isStreaming).toBe(true)

      act(() => {
        result.current.cancel()
      })

      expect(controller.mockAbortController.abort).toHaveBeenCalled()
      expect(result.current.isStreaming).toBe(false)
    })

    it('sets isStreaming to false on cancel', () => {
      const { result } = renderHook(() => useAgentStream('test-project-id'))

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      expect(result.current.isStreaming).toBe(true)

      act(() => {
        result.current.cancel()
      })

      expect(result.current.isStreaming).toBe(false)
      expect(result.current.isThinking).toBe(false)
    })
  })

  describe('onComplete', () => {
    it('calls onComplete with segments on done', () => {
      const onComplete = vi.fn()
      const { result } = renderHook(() =>
        useAgentStream('test-project-id', { onComplete })
      )
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      const callbacks = controller.getCallbacks()!

      act(() => {
        callbacks.onContentStart?.()
        callbacks.onContent?.('Hello')
        callbacks.onDone?.({})
      })

      expect(onComplete).toHaveBeenCalledWith(
        expect.arrayContaining([
          expect.objectContaining({ content: 'Hello' })
        ]),
        null
      )
    })

    it('calls onComplete with applyAction when provided', () => {
      const onComplete = vi.fn()
      const { result } = renderHook(() =>
        useAgentStream('test-project-id', { onComplete })
      )
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      const callbacks = controller.getCallbacks()!

      act(() => {
        callbacks.onDone?.({ apply_action: 'insert' })
      })

      expect(onComplete).toHaveBeenCalledWith(
        expect.any(Array),
        'insert'
      )
    })

    it('passes completion metadata when done includes persisted assistant identifiers', () => {
      const onComplete = vi.fn()
      const { result } = renderHook(() =>
        useAgentStream('test-project-id', { onComplete })
      )
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      const callbacks = controller.getCallbacks()!

      act(() => {
        callbacks.onDone?.({
          assistant_message_id: 'assistant-1',
          session_id: 'session-1',
        })
      })

      expect(onComplete).toHaveBeenCalledWith(
        expect.any(Array),
        null,
        {
          assistantMessageId: 'assistant-1',
          sessionId: 'session-1',
        },
      )
    })

    it('prevents duplicate onComplete calls', () => {
      const onComplete = vi.fn()
      const { result } = renderHook(() =>
        useAgentStream('test-project-id', { onComplete })
      )
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      const callbacks = controller.getCallbacks()!

      act(() => {
        callbacks.onDone?.({})
        callbacks.onDone?.({}) // Second call
      })

      expect(onComplete).toHaveBeenCalledTimes(1)
    })
  })

  describe('error handling', () => {
    it('sets error state on error', async () => {
      const { result } = renderHook(() => useAgentStream('test-project-id'))
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      const callbacks = controller.getCallbacks()!

      act(() => {
        callbacks.onError?.('Something went wrong')
      })

      expect(result.current.error).toBe('Something went wrong')
      expect(result.current.isStreaming).toBe(false)
    })

    it('retains retryable from the current error and resets it for the next stream', () => {
      const { result } = renderHook(() => useAgentStream('test-project-id'))
      const controller = createMockStreamController()

      act(() => result.current.startStream({ message: 'first' }))
      act(() => controller.getCallbacks()!.onError?.('temporary', 'TEMP', true))
      expect(result.current.retryable).toBe(true)
      act(() => vi.advanceTimersByTime(3000))
      expect(result.current.error).toBe('temporary')

      act(() => result.current.startStream({ message: 'second' }))
      expect(result.current.retryable).toBe(false)
    })

    it('keeps non-retryable stream errors visible instead of auto-dismissing them', () => {
      const { result } = renderHook(() => useAgentStream('test-project-id'))
      const controller = createMockStreamController()

      act(() => result.current.startStream({ message: 'first' }))
      act(() =>
        controller.getCallbacks()!.onError?.('上下文过长', 'ERR_AGENT_CONTEXT_TOO_LONG', false),
      )
      act(() => vi.advanceTimersByTime(10000))

      expect(result.current.error).toBe('上下文过长')
      expect(result.current.errorCode).toBe('ERR_AGENT_CONTEXT_TOO_LONG')

      act(() => result.current.startStream({ message: 'second' }))
      expect(result.current.error).toBeNull()
    })

    it('calls onError callback', () => {
      const onError = vi.fn()
      const { result } = renderHook(() =>
        useAgentStream('test-project-id', { onError })
      )
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      const callbacks = controller.getCallbacks()!

      act(() => {
        callbacks.onError?.('Test error')
      })

      expect(onError).toHaveBeenCalledWith('Test error')
    })

    it('sets isStreaming to false on error', () => {
      const { result } = renderHook(() => useAgentStream('test-project-id'))
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      expect(result.current.isStreaming).toBe(true)

      const callbacks = controller.getCallbacks()!

      act(() => {
        callbacks.onError?.('Test error')
      })

      expect(result.current.isStreaming).toBe(false)
      expect(result.current.isThinking).toBe(false)
    })
  })

  describe('conflicts', () => {
    it('handles conflict events', async () => {
      const onConflict = vi.fn()
      const { result } = renderHook(() =>
        useAgentStream('test-project-id', { onConflict })
      )
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      const callbacks = controller.getCallbacks()!

      act(() => {
        callbacks.onConflict?.({
          type: 'character_conflict',
          severity: 'high',
          title: 'Character inconsistency',
          description: 'Character behavior contradicts previous actions',
          suggestions: ['Review character arc'],
        })
      })

      expect(result.current.conflicts).toHaveLength(1)
      expect(result.current.conflicts[0].type).toBe('character_conflict')
      expect(onConflict).toHaveBeenCalled()
    })

  })

  describe('context items', () => {
    it('handles context events', async () => {
      const onContext = vi.fn()
      const { result } = renderHook(() =>
        useAgentStream('test-project-id', { onContext })
      )
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      const callbacks = controller.getCallbacks()!

      const contextItems = [
        { id: '1', type: 'draft', title: 'Chapter 1', content: '...' },
      ]

      act(() => {
        callbacks.onContext?.(contextItems, 500)
      })

      expect(result.current.state.contextItems).toEqual(contextItems)
      expect(result.current.state.contextTokenCount).toBe(500)
      expect(onContext).toHaveBeenCalledWith(contextItems, 500)
    })
  })

  describe('thinking state', () => {
    it('handles thinking events', async () => {
      const onThinking = vi.fn()
      const { result } = renderHook(() =>
        useAgentStream('test-project-id', { onThinking })
      )
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      const callbacks = controller.getCallbacks()!

      act(() => {
        callbacks.onThinking?.('Analyzing context...')
      })

      expect(result.current.isThinking).toBe(true)
      expect(result.current.state.thinkingMessage).toBe('Analyzing context...')
      expect(onThinking).toHaveBeenCalledWith('Analyzing context...')
    })

    it('sets isThinking to false when content starts', async () => {
      const { result } = renderHook(() => useAgentStream('test-project-id'))
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      expect(result.current.isThinking).toBe(true)

      const callbacks = controller.getCallbacks()!

      act(() => {
        callbacks.onContentStart?.()
      })

      expect(result.current.isThinking).toBe(false)
    })
  })

  describe('file operations', () => {
    it('calls onFileCreated callback', async () => {
      const onFileCreated = vi.fn()
      const { result } = renderHook(() =>
        useAgentStream('test-project-id', { onFileCreated })
      )
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      const callbacks = controller.getCallbacks()!

      act(() => {
        callbacks.onFileCreated?.('file-123', 'draft', 'New File')
      })

      expect(onFileCreated).toHaveBeenCalledWith('file-123', 'draft', 'New File')
    })

    it('calls onFileContent callback', async () => {
      const onFileContent = vi.fn()
      const { result } = renderHook(() =>
        useAgentStream('test-project-id', { onFileContent })
      )
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      const callbacks = controller.getCallbacks()!

      act(() => {
        callbacks.onFileContent?.('file-123', 'content chunk')
      })

      expect(onFileContent).toHaveBeenCalledWith('file-123', 'content chunk')
    })

    it('calls onFileEditStart, onFileEditApplied, onFileEditEnd callbacks', async () => {
      const onFileEditStart = vi.fn()
      const onFileEditApplied = vi.fn()
      const onFileEditEnd = vi.fn()

      const { result } = renderHook(() =>
        useAgentStream('test-project-id', {
          onFileEditStart,
          onFileEditApplied,
          onFileEditEnd,
        })
      )
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      const callbacks = controller.getCallbacks()!

      act(() => {
        callbacks.onFileEditStart?.('file-123', 'Edit File', 3)
      })
      expect(onFileEditStart).toHaveBeenCalledWith('file-123', 'Edit File', 3, undefined)

      act(() => {
        callbacks.onFileEditApplied?.('file-123', 0, 'replace', 'old', 'new', true)
      })
      expect(onFileEditApplied).toHaveBeenCalledWith('file-123', 0, 'replace', 'old', 'new', true, undefined)

      act(() => {
        callbacks.onFileEditEnd?.('file-123', 3, 100)
      })
      // 第 8 个参数是新增的批量编辑结果；上游没传时原样转发 undefined。
      expect(onFileEditEnd).toHaveBeenCalledWith(
        'file-123',
        3,
        100,
        undefined,
        undefined,
        undefined,
        undefined,
        undefined,
      )
    })
  })

  describe('workflow events', () => {
    it('handles workflow_stopped event', async () => {
      const onWorkflowStopped = vi.fn()
      const { result } = renderHook(() =>
        useAgentStream('test-project-id', { onWorkflowStopped })
      )
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      const callbacks = controller.getCallbacks()!

      act(() => {
        callbacks.onWorkflowStopped?.({
          reason: 'clarification_needed',
          agent_type: 'writer',
          message: 'Need more info',
          confidence: 0.8,
          evaluation: {
            complete_score: 0.1,
            clarification_score: 0.8,
            consistency_score: 0.3,
            decision_reason: 'heuristic_clarification',
          },
        })
      })

      expect(result.current.isStreaming).toBe(false)
      expect(onWorkflowStopped).toHaveBeenCalledWith({
        reason: 'clarification_needed',
        agent_type: 'writer',
        message: 'Need more info',
        confidence: 0.8,
        evaluation: {
          complete_score: 0.1,
          clarification_score: 0.8,
          consistency_score: 0.3,
          decision_reason: 'heuristic_clarification',
        },
      })
    })

    it('handles workflow_complete event', async () => {
      const onWorkflowComplete = vi.fn()
      const { result } = renderHook(() =>
        useAgentStream('test-project-id', { onWorkflowComplete })
      )
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      const callbacks = controller.getCallbacks()!

      act(() => {
        callbacks.onWorkflowComplete?.({
          reason: 'task_complete',
          agent_type: 'writer',
          message: 'Done',
          confidence: 1,
          evaluation: {
            complete_score: 1,
            clarification_score: 0,
            consistency_score: 0,
            decision_reason: 'explicit_complete_marker',
          },
        })
      })

      expect(result.current.isStreaming).toBe(false)
      expect(onWorkflowComplete).toHaveBeenCalledWith({
        reason: 'task_complete',
        agent_type: 'writer',
        message: 'Done',
        confidence: 1,
        evaluation: {
          complete_score: 1,
          clarification_score: 0,
          consistency_score: 0,
          decision_reason: 'explicit_complete_marker',
        },
      })
    })

    it('handles handoff event', async () => {
      const onHandoff = vi.fn()
      const { result } = renderHook(() =>
        useAgentStream('test-project-id', { onHandoff })
      )
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      const callbacks = controller.getCallbacks()!

      act(() => {
        callbacks.onHandoff?.({
          target_agent: 'quality_reviewer',
          reason: 'auto review',
          context: 'content length > threshold',
          handoff_packet: {
            target_agent: 'quality_reviewer',
            reason: 'auto review',
            context: 'content length > threshold',
            completed: ['draft updated'],
            todo: ['review consistency'],
            evidence: ['content_length=900'],
          },
        })
      })

      expect(onHandoff).toHaveBeenCalledWith({
        target_agent: 'quality_reviewer',
        reason: 'auto review',
        context: 'content length > threshold',
        handoff_packet: {
          target_agent: 'quality_reviewer',
          reason: 'auto review',
          context: 'content length > threshold',
          completed: ['draft updated'],
          todo: ['review consistency'],
          evidence: ['content_length=900'],
        },
      })
    })

    it('resolves pending handoff_to_agent tool call when handoff event arrives', async () => {
      const { result } = renderHook(() => useAgentStream('test-project-id'))
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      const callbacks = controller.getCallbacks()!

      act(() => {
        callbacks.onToolCall?.('handoff_to_agent', {
          target_agent: 'quality_reviewer',
          reason: 'review',
        })
      })
      expect(result.current.state.toolCalls[0].status).toBe('pending')

      act(() => {
        callbacks.onHandoff?.({
          target_agent: 'quality_reviewer',
          reason: 'review',
          context: 'done',
        })
      })

      expect(result.current.state.toolCalls[0].status).toBe('success')
      expect(result.current.segments[0].isStreaming).toBe(false)
    })

    it('resolves pending request_clarification tool call on workflow_stopped', async () => {
      const { result } = renderHook(() => useAgentStream('test-project-id'))
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      const callbacks = controller.getCallbacks()!

      act(() => {
        callbacks.onToolCall?.('request_clarification', {
          question: '请补充主角名',
        })
      })
      expect(result.current.state.toolCalls[0].status).toBe('pending')

      act(() => {
        callbacks.onWorkflowStopped?.({
          reason: 'clarification_needed',
          agent_type: 'writer',
          message: '请补充主角名',
        })
      })

      expect(result.current.state.toolCalls[0].status).toBe('success')
      expect(result.current.isStreaming).toBe(false)
    })

    it('handles router_thinking and router_decided events', async () => {
      const onRouterThinking = vi.fn()
      const onRouterDecided = vi.fn()
      const { result } = renderHook(() =>
        useAgentStream('test-project-id', { onRouterThinking, onRouterDecided })
      )
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      const callbacks = controller.getCallbacks()!

      act(() => {
        callbacks.onRouterThinking?.('Analyzing request...')
      })
      expect(onRouterThinking).toHaveBeenCalledWith('Analyzing request...')
      expect(result.current.isThinking).toBe(true)

      act(() => {
        callbacks.onRouterDecided?.(
          'writer',
          'Write content',
          ['writer', 'editor'],
          {
            agent_type: 'writer',
            workflow_type: 'quick',
            reason: 'default route',
            confidence: 0.6,
          }
        )
      })
      expect(onRouterDecided).toHaveBeenCalledWith(
        'writer',
        'Write content',
        ['writer', 'editor'],
        {
          agent_type: 'writer',
          workflow_type: 'quick',
          reason: 'default route',
          confidence: 0.6,
        }
      )
      expect(result.current.isThinking).toBe(false)
    })

    it('handles agent_selected event', async () => {
      const onAgentSelected = vi.fn()
      const { result } = renderHook(() =>
        useAgentStream('test-project-id', { onAgentSelected })
      )
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      const callbacks = controller.getCallbacks()!

      act(() => {
        callbacks.onAgentSelected?.('writer', 'Content Writer', 1, 5, 4)
      })

      expect(onAgentSelected).toHaveBeenCalledWith('writer', 'Content Writer', 1, 5, 4)
    })

    it('handles iteration_exhausted event', async () => {
      const onIterationExhausted = vi.fn()
      const { result } = renderHook(() =>
        useAgentStream('test-project-id', { onIterationExhausted })
      )
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      const callbacks = controller.getCallbacks()!

      act(() => {
        callbacks.onIterationExhausted?.('tool_call', 10, 10, 'Max iterations reached', 'writer')
      })

      expect(onIterationExhausted).toHaveBeenCalledWith('tool_call', 10, 10, 'Max iterations reached', 'writer')
    })
  })

  describe('skill events', () => {
    it('handles skill_matched event', async () => {
      const onSkillMatched = vi.fn()
      const { result } = renderHook(() =>
        useAgentStream('test-project-id', { onSkillMatched })
      )
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      const callbacks = controller.getCallbacks()!

      act(() => {
        callbacks.onSkillMatched?.('skill-123', 'Auto-correct', '/fix')
      })

      expect(onSkillMatched).toHaveBeenCalledWith('skill-123', 'Auto-correct', '/fix')
    })

    it('handles skills_matched event', async () => {
      const onSkillsMatched = vi.fn()
      const { result } = renderHook(() =>
        useAgentStream('test-project-id', { onSkillsMatched })
      )
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      const callbacks = controller.getCallbacks()!

      const skills = [
        { id: '1', name: 'Skill 1', trigger: '/s1', confidence: 0.9 },
        { id: '2', name: 'Skill 2', trigger: '/s2', confidence: 0.8 },
      ]

      act(() => {
        callbacks.onSkillsMatched?.(skills)
      })

      expect(onSkillsMatched).toHaveBeenCalledWith(skills)
    })
  })

  describe('steering', () => {
    it('throws when no active session exists', async () => {
      const { result } = renderHook(() => useAgentStream('test-project-id'))

      await expect(result.current.sendSteeringMessage('hi')).rejects.toThrow(
        'No active session for steering'
      )
    })

    it('routes steering through sendSteeringRequest with current session id', async () => {
      const { result } = renderHook(() => useAgentStream('test-project-id'))
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      const callbacks = controller.getCallbacks()!
      act(() => {
        callbacks.onSessionStarted?.('session-123')
      })

      await act(async () => {
        await result.current.sendSteeringMessage('Please focus on chapter 2')
      })

      expect(agentApi.sendSteeringRequest).toHaveBeenCalledWith(
        'session-123',
        'Please focus on chapter 2'
      )
    })
  })

  describe('callback freshness', () => {
    it('uses latest callbacks for newly added stream events after rerender', () => {
      const onSessionStartedV1 = vi.fn()
      const onParallelStartV1 = vi.fn()
      const onSteeringReceivedV1 = vi.fn()

      const onSessionStartedV2 = vi.fn()
      const onParallelStartV2 = vi.fn()
      const onSteeringReceivedV2 = vi.fn()

      type CallbackProps = {
        onSessionStarted: (sessionId: string) => void
        onParallelStart: (executionId: string, taskCount: number, descriptions: string[]) => void
        onSteeringReceived: (messageId: string, preview: string) => void
      }

      const { result, rerender } = renderHook(
        (props: CallbackProps) =>
          useAgentStream('test-project-id', {
            onSessionStarted: props.onSessionStarted,
            onParallelStart: props.onParallelStart,
            onSteeringReceived: props.onSteeringReceived,
          }),
        {
          initialProps: {
            onSessionStarted: onSessionStartedV1,
            onParallelStart: onParallelStartV1,
            onSteeringReceived: onSteeringReceivedV1,
          },
        }
      )
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'first' })
      })
      let callbacks = controller.getCallbacks()!
      act(() => {
        callbacks.onSessionStarted?.('session-v1')
        callbacks.onParallelStart?.('exec-v1', 1, ['task-v1'])
        callbacks.onSteeringReceived?.('steer-v1', 'preview-v1')
      })

      expect(onSessionStartedV1).toHaveBeenCalledWith('session-v1')
      expect(onParallelStartV1).toHaveBeenCalledWith('exec-v1', 1, ['task-v1'], undefined)
      expect(onSteeringReceivedV1).toHaveBeenCalledWith('steer-v1', 'preview-v1')

      rerender({
        onSessionStarted: onSessionStartedV2,
        onParallelStart: onParallelStartV2,
        onSteeringReceived: onSteeringReceivedV2,
      })

      act(() => {
        result.current.startStream({ message: 'second' })
      })
      callbacks = controller.getCallbacks()!
      act(() => {
        callbacks.onSessionStarted?.('session-v2')
        callbacks.onParallelStart?.('exec-v2', 2, ['task-a', 'task-b'])
        callbacks.onSteeringReceived?.('steer-v2', 'preview-v2')
      })

      expect(onSessionStartedV2).toHaveBeenCalledWith('session-v2')
      expect(onParallelStartV2).toHaveBeenCalledWith(
        'exec-v2',
        2,
        ['task-a', 'task-b'],
        undefined,
      )
      expect(onSteeringReceivedV2).toHaveBeenCalledWith('steer-v2', 'preview-v2')

      expect(onSessionStartedV1).toHaveBeenCalledTimes(1)
      expect(onParallelStartV1).toHaveBeenCalledTimes(1)
      expect(onSteeringReceivedV1).toHaveBeenCalledTimes(1)
    })
  })
})
