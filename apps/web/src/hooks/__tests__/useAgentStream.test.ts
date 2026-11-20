import { renderHook, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { STOP_GRACE_MS, useAgentStream } from '../useAgentStream'
import * as agentApi from '@/lib/agentApi'

// Mock agentApi
vi.mock('@/lib/agentApi', () => ({
  streamAgentRequest: vi.fn(),
  sendSteeringRequest: vi.fn(),
  stopAgentRun: vi.fn(),
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
      expect(result.current.state.content).toBe('')
      expect(result.current.error).toBe(null)
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

      expect(result.current.state.content).toBe('first content')

      // Second stream should clear
      act(() => {
        result.current.startStream({ message: 'second' })
      })

      expect(result.current.state.content).toBe('')
    })

    it('ends the round and releases the stream even when done handling throws', () => {
      const onComplete = vi.fn()
      const onSegmentEnd = vi.fn(() => { throw new TypeError('segment end failed') })
      const { result } = renderHook(() =>
        useAgentStream('test-project-id', { onComplete, onSegmentEnd })
      )
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })
      const callbacks = controller.getCallbacks()!
      act(() => {
        callbacks.onContent?.('delivered text')
      })
      // agentApi reports the throw as a diagnostic, not as onError.
      let thrown: unknown
      act(() => {
        try { callbacks.onDone?.({}) } catch (error) { thrown = error }
      })
      expect(thrown).toEqual(new TypeError('segment end failed'))

      expect(result.current.isStreaming).toBe(false)
      expect(result.current.isThinking).toBe(false)
      expect(onComplete).toHaveBeenCalledTimes(1)
      expect(onComplete).toHaveBeenCalledWith(expect.any(Array), expect.objectContaining({ partial: true }))
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
        })
      })

      expect(agentApi.streamAgentRequest).toHaveBeenCalledWith(
        expect.objectContaining({
          project_id: 'project-123',
          message: 'Hello AI',
          session_id: 'session-123',
          selected_text: 'selected',
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
      expect(result.current.state.content).toBe('')

      // Current stream content should still work.
      act(() => {
        secondCallbacks.onContent?.('fresh-content')
      })
      act(() => {
        vi.advanceTimersByTime(100)
      })
      expect(result.current.state.content).toBe('fresh-content')
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
      expect(result.current.state.content).toBe('Hello')

      act(() => {
        callbacks.onContent?.(' World')
      })
      act(() => {
        vi.runOnlyPendingTimers()
      })
      expect(result.current.state.content).toBe('Hello World')
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

      expect(onSegmentStart).toHaveBeenCalledWith(
        expect.objectContaining({ type: 'content', isStreaming: true })
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

      expect(onSegmentUpdate).toHaveBeenCalledWith(
        expect.any(String),
        'Hello'
      )
    })

    it('marks segment as complete on onContentEnd', () => {
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
        callbacks.onContentEnd?.()
        callbacks.onDone?.({})
      })

      expect(onComplete).toHaveBeenCalledWith(
        [expect.objectContaining({ type: 'content', content: 'Hello', isStreaming: false })],
        {}
      )
    })
  })

  describe('tool calls', () => {
    it('handles tool calls with pending status', async () => {
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

      expect(onSegmentStart).toHaveBeenCalledWith(
        expect.objectContaining({
          type: 'tool_calls',
          toolCalls: [expect.objectContaining({ tool_name: 'create_file', status: 'pending' })],
        })
      )
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

      expect(onSegmentStart).toHaveBeenCalledWith(
        expect.objectContaining({
          type: 'tool_calls',
          isStreaming: true,
        })
      )
    })

    it('updates tool status on tool result', async () => {
      const onSegmentUpdateToolCalls = vi.fn()
      const { result } = renderHook(() =>
        useAgentStream('test-project-id', { onSegmentUpdateToolCalls })
      )
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      const callbacks = controller.getCallbacks()!

      act(() => {
        callbacks.onToolCall?.('create_file', { title: 'test.txt' })
      })

      act(() => {
        callbacks.onToolResult?.('create_file', 'success', { file_id: '123' })
      })

      expect(onSegmentUpdateToolCalls).toHaveBeenCalledWith(
        expect.any(String),
        [expect.objectContaining({ status: 'success', result: { file_id: '123' } })]
      )
    })

    it('matches tool results by tool_use_id when tool names are duplicated', async () => {
      const onSegmentUpdateToolCalls = vi.fn()
      const { result } = renderHook(() =>
        useAgentStream('test-project-id', { onSegmentUpdateToolCalls })
      )
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      const callbacks = controller.getCallbacks()!

      act(() => {
        callbacks.onToolCall?.('update_project', { tasks: ['a'] }, 'tool-1')
        callbacks.onToolCall?.('update_project', { tasks: ['b'] }, 'tool-2')
      })

      act(() => {
        callbacks.onToolResult?.('update_project', 'success', { idx: 2 }, undefined, 'tool-2')
      })

      expect(onSegmentUpdateToolCalls).toHaveBeenLastCalledWith(
        expect.any(String),
        [
          expect.objectContaining({ id: 'tool-1', status: 'pending' }),
          expect.objectContaining({ id: 'tool-2', status: 'success', result: { idx: 2 } }),
        ]
      )
    })

    it('replaces a resolved write_chapter card when the server re-sends it under the same tool_use_id', async () => {
      const onSegmentStart = vi.fn()
      const onSegmentUpdateToolCalls = vi.fn()
      const onToolResult = vi.fn()
      const { result } = renderHook(() =>
        useAgentStream('test-project-id', { onSegmentStart, onSegmentUpdateToolCalls, onToolResult })
      )
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      const callbacks = controller.getCallbacks()!
      const armed = {
        id: 'ch-2', title: '第2章 旧城', file_type: 'draft', chapter: 2,
        action: 'replaced', awaiting_body: true, previous_length: 2400, previous_word_count: 2400,
      }
      const saved = {
        id: 'ch-2', title: '第2章 旧城', file_type: 'draft', chapter: 2, action: 'replaced',
        previous_length: 2400, previous_word_count: 2400, mutation_applied: true, body_saved: true,
        new_length: 1800, new_word_count: 1800, updated_at: '2026-11-16T08:00:00Z',
        undo: { before_version_number: 4, expected_after_updated_at: '2026-11-16T08:00:00Z' },
      }

      act(() => {
        callbacks.onToolCall?.('write_chapter', { chapter: 2 }, 'wc-1')
        callbacks.onToolResult?.('write_chapter', 'success', armed, undefined, 'wc-1')
      })
      const toolSegmentId = onSegmentStart.mock.calls
        .map(([segment]) => segment)
        .find(segment => segment.type === 'tool_calls')!.id

      // The chapter text streams as a reply, then the saved card arrives for the same call.
      act(() => {
        callbacks.onContentStart?.()
        callbacks.onContent?.('第二章换好了。')
        callbacks.onContentEnd?.()
        callbacks.onToolResult?.('write_chapter', 'success', saved, undefined, 'wc-1')
      })

      expect(onSegmentUpdateToolCalls).toHaveBeenLastCalledWith(toolSegmentId, [
        expect.objectContaining({ id: 'wc-1', status: 'success', result: saved }),
      ])
      expect(onToolResult).toHaveBeenCalledTimes(2)

      // A result for an unknown tool_use_id leaves the saved card alone.
      const callsBefore = onSegmentUpdateToolCalls.mock.calls.length
      act(() => {
        callbacks.onToolResult?.('write_chapter', 'success', { action: 'created' }, undefined, 'other-id')
      })
      expect(onSegmentUpdateToolCalls.mock.calls.slice(callsBefore)).not.toContainEqual([
        toolSegmentId,
        expect.anything(),
      ])
    })

    it('merges multiple tool calls into single segment', async () => {
      const onSegmentStart = vi.fn()
      const onSegmentUpdateToolCalls = vi.fn()
      const { result } = renderHook(() =>
        useAgentStream('test-project-id', { onSegmentStart, onSegmentUpdateToolCalls })
      )
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      const callbacks = controller.getCallbacks()!

      act(() => {
        callbacks.onToolCall?.('create_file', { title: 'a.txt' })
        callbacks.onToolCall?.('edit_file', { file_id: '1' })
      })

      expect(onSegmentStart).toHaveBeenCalledTimes(1)
      expect(onSegmentUpdateToolCalls).toHaveBeenLastCalledWith(
        expect.any(String),
        expect.arrayContaining([
          expect.objectContaining({ tool_name: 'create_file' }),
          expect.objectContaining({ tool_name: 'edit_file' }),
        ])
      )
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
        callbacks.onToolCall?.('tool1', {})
        callbacks.onToolCall?.('tool2', {})
      })

      expect(onSegmentEnd).not.toHaveBeenCalled()

      act(() => {
        callbacks.onToolResult?.('tool1', 'success')
      })

      // Still streaming because tool2 is pending
      expect(onSegmentEnd).not.toHaveBeenCalled()

      act(() => {
        callbacks.onToolResult?.('tool2', 'success')
      })

      // Now complete
      expect(onSegmentEnd).toHaveBeenCalledTimes(1)
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

      expect(result.current.state.content).toBe('some content')
      expect(result.current.isStreaming).toBe(true)

      act(() => {
        result.current.reset()
      })

      expect(result.current.isStreaming).toBe(false)
      expect(result.current.state.content).toBe('')
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

  describe('stop before run_started', () => {
    it('cancels stream immediately when stop() is called before runId arrives', async () => {
      const { result } = renderHook(() => useAgentStream('test-project-id'))
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      expect(result.current.isStreaming).toBe(true)

      await act(async () => {
        await result.current.stop()
      })

      expect(controller.mockAbortController.abort).toHaveBeenCalled()
      expect(result.current.isStreaming).toBe(false)
      expect(result.current.isThinking).toBe(false)
    })
  })

  describe('stop (author pressed stop)', () => {
    function startWithRun(runId: string | null = 'run-1') {
      const hook = renderHook(() => useAgentStream('test-project-id'))
      const controller = createMockStreamController()
      act(() => {
        hook.result.current.startStream({ message: 'test' })
      })
      if (runId) {
        act(() => {
          controller.getCallbacks()?.onRunStarted?.(runId)
        })
      }
      return { ...hook, controller }
    }

    it('asks the server to stop and keeps reading the stream until it wraps up', async () => {
      vi.mocked(agentApi.stopAgentRun).mockResolvedValue(true)
      const { result, controller } = startWithRun()

      await act(async () => {
        result.current.stop()
      })

      expect(agentApi.stopAgentRun).toHaveBeenCalledWith('run-1', [])
      expect(controller.mockAbortController.abort).not.toHaveBeenCalled()
      expect(result.current.isStopping).toBe(true)

      act(() => {
        controller.getCallbacks()?.onWorkflowStopped?.({
          reason: 'user_stopped',
          agent_type: '',
          message: '已停止生成。',
        })
        controller.getCallbacks()?.onDone?.({})
      })
      act(() => {
        vi.advanceTimersByTime(STOP_GRACE_MS)
      })

      expect(result.current.isStreaming).toBe(false)
      expect(result.current.isStopping).toBe(false)
      expect(controller.mockAbortController.abort).not.toHaveBeenCalled()
    })

    it('passes the files the editor could not save to the stop request', async () => {
      vi.mocked(agentApi.stopAgentRun).mockResolvedValue(true)
      const { result } = startWithRun()

      await act(async () => {
        await result.current.stop({ keepFileIds: ['ch-typed'] })
      })

      expect(agentApi.stopAgentRun).toHaveBeenCalledWith('run-1', ['ch-typed'])
    })

    it('stays in 正在停止… until done arrives with this round\'s message id', async () => {
      vi.mocked(agentApi.stopAgentRun).mockResolvedValue(true)
      const onComplete = vi.fn()
      const hook = renderHook(() => useAgentStream('test-project-id', { onComplete }))
      const controller = createMockStreamController()
      act(() => {
        hook.result.current.startStream({ message: 'test' })
      })
      act(() => {
        controller.getCallbacks()?.onRunStarted?.('run-1')
      })
      await act(async () => {
        hook.result.current.stop()
      })

      act(() => {
        controller.getCallbacks()?.onWorkflowStopped?.({
          reason: 'user_stopped',
          agent_type: '',
          message: '已停止生成。',
        })
      })
      // The stop card alone does not end the round: a send now would cut it off.
      expect(hook.result.current.isStreaming).toBe(true)
      expect(hook.result.current.isStopping).toBe(true)

      act(() => {
        controller.getCallbacks()?.onDone?.({
          assistant_message_id: 'assistant-7',
          session_id: 's-1',
          stop_reason: 'user_stopped',
          produced_output: false,
        })
      })
      expect(hook.result.current.isStreaming).toBe(false)
      expect(onComplete).toHaveBeenCalledWith(expect.any(Array), expect.objectContaining({
        assistantMessageId: 'assistant-7',
        stoppedByAuthor: true,
        producedOutput: false,
      }))
    })

    it('hands the stopped round\'s written files from done to onComplete', async () => {
      vi.mocked(agentApi.stopAgentRun).mockResolvedValue(true)
      const onComplete = vi.fn()
      const hook = renderHook(() => useAgentStream('test-project-id', { onComplete }))
      const controller = createMockStreamController()
      act(() => {
        hook.result.current.startStream({ message: 'test' })
      })
      act(() => {
        controller.getCallbacks()?.onRunStarted?.('run-1')
      })
      await act(async () => {
        hook.result.current.stop()
      })
      act(() => {
        controller.getCallbacks()?.onDone?.({
          stop_reason: 'user_stopped',
          produced_output: true,
          written_files: [
            { id: 'ch-1', title: '雨夜', words: 1039 },
            { id: 'ch-2', title: '人物' },
            { id: 'ch-3', title: '' },
          ],
          unwritten_files: [{ id: 'ch-4', title: '周念' }],
        })
      })
      expect(onComplete).toHaveBeenCalledWith(expect.any(Array), expect.objectContaining({
        stoppedByAuthor: true,
        writtenFiles: [{ title: '雨夜', words: 1039 }, { title: '人物', words: null }],
        unwrittenFiles: [{ title: '周念', words: null }],
      }))
    })

    it.each([
      [
        { written_files: [{ id: 'ch-1', title: '雨夜', words: 1039 }, { id: 'ch-2', title: '人物', partial: true }] },
        { reason: 'completed', savedOutput: true,
          writtenFiles: [{ title: '雨夜', words: 1039 }, { title: '人物', words: null, partial: true }] },
      ],
      [{ no_file_changes: true }, { reason: 'completed', savedOutput: false, writtenFiles: [], noFileChanges: true }],
    ])('turns a completed round\'s write receipt %j into its end note', (receipt, outcome) => {
      const onComplete = vi.fn()
      const hook = renderHook(() => useAgentStream('test-project-id', { onComplete }))
      const controller = createMockStreamController()
      act(() => {
        hook.result.current.startStream({ message: 'test' })
      })
      act(() => {
        controller.getCallbacks()?.onDone?.({ assistant_message_id: 'assistant-8', ...receipt })
      })
      expect(onComplete).toHaveBeenCalledWith(expect.any(Array), expect.objectContaining({
        deliveryOutcome: { charged: true, removedFiles: [], ...outcome },
      }))
    })

    it.each([
      [{ delivery_incomplete: true, saved_output: true }, { reason: 'incomplete', savedOutput: true, writtenFiles: [] }],
      [{ delivery_incomplete: true, saved_output: false }, { reason: 'incomplete', savedOutput: false, writtenFiles: [] }],
      [
        { delivery_incomplete: true, saved_output: false, no_file_changes: true },
        { reason: 'incomplete', savedOutput: false, writtenFiles: [], noFileChanges: true },
      ],
      [
        { delivery_incomplete: true, saved_output: true, written_files: [{ id: 'ch-1', title: '雨夜' }] },
        { reason: 'incomplete', savedOutput: true, writtenFiles: [{ title: '雨夜', words: null }] },
      ],
      [
        { delivery_incomplete: true, saved_output: false,
          unwritten_files: [{ id: 'ch-2', title: '未完成章' }] },
        { reason: 'incomplete', savedOutput: false, writtenFiles: [],
          unwrittenFiles: [{ title: '未完成章', words: null }] },
      ],
    ])('uses the server completion receipt %j without inferring save state from named files', (receipt, outcome) => {
      const onComplete = vi.fn()
      const hook = renderHook(() => useAgentStream('test-project-id', { onComplete }))
      const controller = createMockStreamController()
      act(() => hook.result.current.startStream({ message: 'test' }))
      act(() => controller.getCallbacks()?.onDone?.({ assistant_message_id: 'assistant-incomplete', ...receipt }))

      expect(onComplete).toHaveBeenCalledWith(expect.any(Array), expect.objectContaining({
        deliveryOutcome: { charged: true, removedFiles: [], ...outcome },
      }))
    })

    it('leaves writtenFiles out when an older server sends a stopped done without them', async () => {
      vi.mocked(agentApi.stopAgentRun).mockResolvedValue(true)
      const onComplete = vi.fn()
      const hook = renderHook(() => useAgentStream('test-project-id', { onComplete }))
      const controller = createMockStreamController()
      act(() => {
        hook.result.current.startStream({ message: 'test' })
      })
      act(() => {
        controller.getCallbacks()?.onRunStarted?.('run-1')
      })
      await act(async () => {
        hook.result.current.stop()
      })
      act(() => {
        controller.getCallbacks()?.onDone?.({ stop_reason: 'user_stopped', produced_output: true })
      })
      const meta = onComplete.mock.calls.at(-1)?.[1]
      expect(meta).toMatchObject({ stoppedByAuthor: true })
      expect(meta).not.toHaveProperty('writtenFiles')
    })

    it('drops the connection when the stop request fails', async () => {
      vi.mocked(agentApi.stopAgentRun).mockResolvedValue(false)
      const { result, controller } = startWithRun()

      await act(async () => {
        result.current.stop()
      })

      expect(controller.mockAbortController.abort).toHaveBeenCalled()
      expect(result.current.isStreaming).toBe(false)
    })

    it('drops the connection when the server does not wrap up in time', async () => {
      vi.mocked(agentApi.stopAgentRun).mockResolvedValue(true)
      const { result, controller } = startWithRun()

      await act(async () => {
        result.current.stop()
      })
      act(() => {
        vi.advanceTimersByTime(STOP_GRACE_MS)
      })

      expect(controller.mockAbortController.abort).toHaveBeenCalled()
      expect(result.current.isStreaming).toBe(false)
    })

    it('does nothing once the round already ended (done arrived while the editor was saving)', async () => {
      vi.mocked(agentApi.stopAgentRun).mockResolvedValue(true)
      const { result, controller } = startWithRun()

      act(() => {
        controller.getCallbacks()?.onDone?.({ assistant_message_id: 'assistant-1' })
      })
      await act(async () => {
        result.current.stop()
      })
      act(() => {
        vi.advanceTimersByTime(STOP_GRACE_MS)
      })

      // No /stop for a finished run, and no fallback cancel that would drop the frames after done.
      expect(agentApi.stopAgentRun).not.toHaveBeenCalled()
      expect(controller.mockAbortController.abort).not.toHaveBeenCalled()
      expect(result.current.isStopping).toBe(false)
    })

    it('drops the connection right away before the run id is known', () => {
      const { result, controller } = startWithRun(null)

      act(() => {
        result.current.stop()
      })

      expect(agentApi.stopAgentRun).not.toHaveBeenCalled()
      expect(controller.mockAbortController.abort).toHaveBeenCalled()
    })
  })

  describe('whenStreamClosed (start a new chat only after the stopped round wrapped up)', () => {
    function startStreaming() {
      const hook = renderHook(() => useAgentStream('test-project-id'))
      const controller = createMockStreamController()
      act(() => {
        hook.result.current.startStream({ message: 'test' })
      })
      act(() => {
        controller.getCallbacks()?.onRunStarted?.('run-1')
      })
      return { ...hook, controller }
    }

    it('resolves at once when no round is open', async () => {
      const { result } = renderHook(() => useAgentStream('test-project-id'))
      const settled = vi.fn()
      await act(async () => {
        void result.current.whenStreamClosed().then(settled)
      })
      expect(settled).toHaveBeenCalled()
    })

    it('waits past done for the frames that follow it, until the server closes the stream', async () => {
      vi.mocked(agentApi.stopAgentRun).mockResolvedValue(true)
      const { result, controller } = startStreaming()
      const settled = vi.fn()
      await act(async () => {
        await result.current.stop()
        void result.current.whenStreamClosed().then(settled)
      })
      act(() => {
        controller.getCallbacks()?.onDone?.({ stop_reason: 'user_stopped', produced_output: false })
      })
      await act(async () => {})
      // done ended the round, but quota_refunded (removed blank files) may still follow.
      expect(result.current.isStreaming).toBe(false)
      expect(settled).not.toHaveBeenCalled()
      expect(controller.mockAbortController.abort).not.toHaveBeenCalled()

      await act(async () => {
        controller.getCallbacks()?.onClosed?.()
      })
      expect(settled).toHaveBeenCalled()
    })

    it('resolves when the connection is dropped instead (stop failed or timed out)', async () => {
      vi.mocked(agentApi.stopAgentRun).mockResolvedValue(false)
      const { result } = startStreaming()
      const settled = vi.fn()
      await act(async () => {
        void result.current.whenStreamClosed().then(settled)
        await result.current.stop()
      })
      expect(settled).toHaveBeenCalled()
    })

    it('does not wait forever for a server that never closes the stream', async () => {
      const { result } = startStreaming()
      const settled = vi.fn()
      await act(async () => {
        void result.current.whenStreamClosed().then(settled)
      })
      await act(async () => {
        vi.advanceTimersByTime(STOP_GRACE_MS)
      })
      expect(settled).toHaveBeenCalled()
    })
  })

  describe('prose segment boundaries', () => {
    it('delivers the text streamed before file_created first, so the prose count can restart there', () => {
      const onSegmentUpdate = vi.fn()
      const onBoundary = vi.fn()
      const { result } = renderHook(() =>
        useAgentStream('test-project-id', {
          onSegmentUpdate,
          onFileCreated: onBoundary,
        }),
      )
      const controller = createMockStreamController()
      act(() => {
        result.current.startStream({ message: 'test' })
      })
      act(() => {
        const callbacks = controller.getCallbacks()
        callbacks?.onContent?.('甲'.repeat(40))
        callbacks?.onFileCreated?.('file-1', 'draft', '第1章')
      })

      expect(onSegmentUpdate).toHaveBeenCalledWith(expect.any(String), '甲'.repeat(40))
      expect(onBoundary).toHaveBeenCalledTimes(1)
      expect(onSegmentUpdate.mock.invocationCallOrder[0]).toBeLessThan(onBoundary.mock.invocationCallOrder[0])
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
        {}
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
        {
          assistantMessageId: 'assistant-1',
          sessionId: 'session-1',
        },
      )
    })

    it.each([true, false])('maps explicit file_mutated=%s into completion metadata', (fileMutated) => {
      const onComplete = vi.fn()
      const { result } = renderHook(() =>
        useAgentStream('test-project-id', { onComplete })
      )
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })

      act(() => {
        controller.getCallbacks()!.onDone?.({ file_mutated: fileMutated })
      })

      expect(onComplete).toHaveBeenCalledWith(
        expect.any(Array),
        { confirmedFileMutation: fileMutated },
      )
    })

    it('does not carry a previous mutation confirmation into the next stream', () => {
      const onComplete = vi.fn()
      const { result } = renderHook(() =>
        useAgentStream('test-project-id', { onComplete })
      )
      const controller = createMockStreamController()

      act(() => result.current.startStream({ message: 'first' }))
      act(() => controller.getCallbacks()!.onDone?.({ file_mutated: true }))
      act(() => result.current.startStream({ message: 'second' }))
      act(() => controller.getCallbacks()!.onDone?.({}))

      expect(onComplete).toHaveBeenNthCalledWith(
        1,
        expect.any(Array),
        { confirmedFileMutation: true },
      )
      expect(onComplete).toHaveBeenNthCalledWith(2, expect.any(Array), {})
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
        callbacks.onDone?.({ file_mutated: true })
        callbacks.onDone?.({ file_mutated: true }) // Second terminal event
      })

      expect(onComplete).toHaveBeenCalledTimes(1)
      expect(onComplete).toHaveBeenCalledWith(
        expect.any(Array),
        { confirmedFileMutation: true },
      )
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

    it('clearError clears error, errorCode, state.error, state.errorCode, and retryable', () => {
      const { result } = renderHook(() => useAgentStream('test-project-id'))
      const controller = createMockStreamController()

      act(() => result.current.startStream({ message: 'first' }))
      act(() => controller.getCallbacks()!.onError?.('网络错误', 'STREAM_CLOSED', true))

      expect(result.current.error).toBe('网络错误')
      expect(result.current.errorCode).toBe('STREAM_CLOSED')
      expect(result.current.state.error).toBe('网络错误')
      expect(result.current.state.errorCode).toBe('STREAM_CLOSED')
      expect(result.current.retryable).toBe(true)

      act(() => result.current.clearError())

      expect(result.current.error).toBeNull()
      expect(result.current.errorCode).toBeNull()
      expect(result.current.state.error).toBeNull()
      expect(result.current.state.errorCode).toBeNull()
      expect(result.current.retryable).toBe(false)
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
        callbacks.onThinking?.('Compacting conversation…', 'history_compaction')
      })

      expect(result.current.isThinking).toBe(true)
      expect(onThinking).toHaveBeenCalledWith('Analyzing context...', undefined)
      // The step code reaches the chat (it decides the compaction row), not just the text.
      expect(onThinking).toHaveBeenCalledWith('Compacting conversation…', 'history_compaction')
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
        callbacks.onFileEditApplied?.(0, 'replace')
      })
      expect(onFileEditApplied).toHaveBeenCalledWith(0, 'replace')

      act(() => {
        callbacks.onFileEditEnd?.('file-123', 3)
      })
      expect(onFileEditEnd).toHaveBeenCalledWith(
        'file-123',
        3,
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
        })
      })

      expect(result.current.isStreaming).toBe(false)
      expect(onWorkflowStopped).toHaveBeenCalledWith({
        reason: 'clarification_needed',
        agent_type: 'writer',
        message: 'Need more info',
      })
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
        callbacks.onIterationExhausted?.('tool_call', 10, 10, 'Max iterations reached', 'writer', 'wall_clock')
      })

      expect(onIterationExhausted).toHaveBeenCalledWith('tool_call', 10, 10, 'Max iterations reached', 'writer', 'wall_clock')
    })

    it('forwards done.charged on user_stopped completionMeta', () => {
      const onComplete = vi.fn()
      const { result } = renderHook(() =>
        useAgentStream('test-project-id', { onComplete })
      )
      const controller = createMockStreamController()

      act(() => {
        result.current.startStream({ message: 'test' })
      })
      let callbacks = controller.getCallbacks()!
      act(() => {
        callbacks.onDone?.({
          stop_reason: 'user_stopped',
          charged: true,
          produced_output: false,
          assistant_message_id: 'msg-charged',
        })
      })
      expect(onComplete).toHaveBeenLastCalledWith(
        [],
        expect.objectContaining({
          stoppedByAuthor: true,
          charged: true,
          producedOutput: false,
          assistantMessageId: 'msg-charged',
        }),
      )

      act(() => {
        result.current.startStream({ message: 'test 2' })
      })
      callbacks = controller.getCallbacks()!
      act(() => {
        callbacks.onDone?.({
          stop_reason: 'user_stopped',
          charged: false,
          produced_output: false,
          assistant_message_id: 'msg-refunded',
        })
      })
      expect(onComplete).toHaveBeenLastCalledWith(
        [],
        expect.objectContaining({
          stoppedByAuthor: true,
          charged: false,
          producedOutput: false,
          assistantMessageId: 'msg-refunded',
        }),
      )
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
    it('uses latest callbacks for stream events after rerender', () => {
      const onSessionStartedV1 = vi.fn()
      const onSteeringReceivedV1 = vi.fn()

      const onSessionStartedV2 = vi.fn()
      const onSteeringReceivedV2 = vi.fn()

      type CallbackProps = {
        onSessionStarted: (sessionId: string) => void
        onSteeringReceived: (messageId: string, preview: string) => void
      }

      const { result, rerender } = renderHook(
        (props: CallbackProps) =>
          useAgentStream('test-project-id', {
            onSessionStarted: props.onSessionStarted,
            onSteeringReceived: props.onSteeringReceived,
          }),
        {
          initialProps: {
            onSessionStarted: onSessionStartedV1,
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
        callbacks.onSteeringReceived?.('steer-v1', 'preview-v1')
      })

      expect(onSessionStartedV1).toHaveBeenCalledWith('session-v1')
      expect(onSteeringReceivedV1).toHaveBeenCalledWith('steer-v1', 'preview-v1')

      rerender({
        onSessionStarted: onSessionStartedV2,
        onSteeringReceived: onSteeringReceivedV2,
      })

      act(() => {
        result.current.startStream({ message: 'second' })
      })
      callbacks = controller.getCallbacks()!
      act(() => {
        callbacks.onSessionStarted?.('session-v2')
        callbacks.onSteeringReceived?.('steer-v2', 'preview-v2')
      })

      expect(onSessionStartedV2).toHaveBeenCalledWith('session-v2')
      expect(onSteeringReceivedV2).toHaveBeenCalledWith('steer-v2', 'preview-v2')

      expect(onSessionStartedV1).toHaveBeenCalledTimes(1)
      expect(onSteeringReceivedV1).toHaveBeenCalledTimes(1)
    })
  })
})
