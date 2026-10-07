import { renderHook, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { useChatStreaming, throttle, generateUniqueId } from '../useChatStreaming'
import { PROJECT_STATUS_UPDATED_EVENT } from '../../lib/projectStatusEvents'
import type { UseChatStreamingDependencies } from '../useChatStreaming'

// Helper to create mock dependencies
function createMockDeps(): UseChatStreamingDependencies {
  return {
    triggerFileTreeRefresh: vi.fn(),
    triggerEditorRefresh: vi.fn(),
    setSelectedItem: vi.fn(),
    getCurrentSelectedItem: vi.fn(() => null),
    appendFileContent: vi.fn(),
    finishFileStreaming: vi.fn(),
    startFileStreaming: vi.fn(),
    streamingFileId: null,
    enterDiffReview: vi.fn(),
    activeProjectId: 'test-project-id',
    createSnapshot: vi.fn().mockResolvedValue({}),
    t: vi.fn((key: string) => key),
  }
}

describe('useChatStreaming', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers()
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    vi.spyOn(console, 'error').mockImplementation(() => {})
  })

  afterEach(() => {
    vi.clearAllTimers()
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  describe('initial state', () => {
    it('initializes with correct default state', () => {
      const { result } = renderHook(() => useChatStreaming())

      expect(result.current.streamRenderItems).toEqual([])
      expect(result.current.editProgress).toBe(null)
      expect(result.current.matchedSkills).toEqual([])
    })
  })

  describe('stream render items', () => {
    it('snapshots the unthrottled buffer before completion and clearing without mutating the saved sequence', () => {
      const { result } = renderHook(() => useChatStreaming())
      const timestamp = new Date()
      act(() => result.current.updateStreamItems(() => [{ type: 'content', id: 'first', content: 'First', timestamp }]))
      act(() => result.current.updateStreamItems(previous => [...previous, { type: 'thinking_status', id: 'handoff', content: 'Handoff', timestamp }]))
      expect(result.current.streamRenderItems).toHaveLength(1)
      const snapshot = result.current.getStreamItemsSnapshot()
      expect(snapshot.map(item => item.id)).toEqual(['first', 'handoff'])
      act(() => result.current.clearStreamItems())
      expect(result.current.getStreamItemsSnapshot()).toEqual([])
      expect(snapshot).toHaveLength(2)
    })

    it('starts with empty stream render items', () => {
      const { result } = renderHook(() => useChatStreaming())
      expect(result.current.streamRenderItems).toHaveLength(0)
    })

    it('updates stream items via updateStreamItems', () => {
      const { result } = renderHook(() => useChatStreaming())

      act(() => {
        result.current.updateStreamItems((prev) => [
          ...prev,
          {
            type: 'content',
            id: 'test-id',
            content: 'Hello',
            timestamp: new Date(),
          },
        ])
      })

      // Advance timers to allow throttled update
      act(() => {
        vi.advanceTimersByTime(100)
      })

      expect(result.current.streamRenderItems).toHaveLength(1)
      expect(result.current.streamRenderItems[0].content).toBe('Hello')
    })

    it('clears stream items via clearStreamItems', () => {
      const { result } = renderHook(() => useChatStreaming())

      act(() => {
        result.current.updateStreamItems((prev) => [
          ...prev,
          {
            type: 'content',
            id: 'test-id',
            content: 'Test',
            timestamp: new Date(),
          },
        ])
      })

      act(() => {
        vi.advanceTimersByTime(100)
      })

      expect(result.current.streamRenderItems).toHaveLength(1)

      act(() => {
        result.current.clearStreamItems()
      })

      expect(result.current.streamRenderItems).toHaveLength(0)
    })

    it('force flushes pending state updates', () => {
      const { result } = renderHook(() => useChatStreaming())

      // First, verify items are empty
      expect(result.current.streamRenderItems).toHaveLength(0)

      act(() => {
        result.current.updateStreamItems((prev) => [
          ...prev,
          {
            type: 'content',
            id: 'test-id',
            content: 'Force flush test',
            timestamp: new Date(),
          },
        ])
      })

      // Force flush should ensure items are available immediately
      act(() => {
        result.current.forceFlushStreamItems()
      })

      expect(result.current.streamRenderItems).toHaveLength(1)
      expect(result.current.streamRenderItems[0].content).toBe('Force flush test')
    })
  })

  describe('edit progress', () => {
    it('starts with null edit progress', () => {
      const { result } = renderHook(() => useChatStreaming())
      expect(result.current.editProgress).toBe(null)
    })

    it('sets edit progress', () => {
      const { result } = renderHook(() => useChatStreaming())

      act(() => {
        result.current.setEditProgress({
          fileId: 'file-123',
          title: 'Test File',
          totalEdits: 5,
          completedEdits: 0,
        })
      })

      expect(result.current.editProgress).toEqual({
        fileId: 'file-123',
        title: 'Test File',
        totalEdits: 5,
        completedEdits: 0,
      })
    })

    it('clears edit progress by setting to null', () => {
      const { result } = renderHook(() => useChatStreaming())

      act(() => {
        result.current.setEditProgress({
          fileId: 'file-123',
          title: 'Test File',
          totalEdits: 5,
          completedEdits: 2,
        })
      })

      expect(result.current.editProgress).not.toBe(null)

      act(() => {
        result.current.setEditProgress(null)
      })

      expect(result.current.editProgress).toBe(null)
    })

    it('updates edit progress using function form', () => {
      const { result } = renderHook(() => useChatStreaming())

      act(() => {
        result.current.setEditProgress({
          fileId: 'file-123',
          title: 'Test File',
          totalEdits: 5,
          completedEdits: 0,
        })
      })

      act(() => {
        result.current.setEditProgress((prev) =>
          prev ? { ...prev, completedEdits: prev.completedEdits + 1 } : null
        )
      })

      expect(result.current.editProgress?.completedEdits).toBe(1)
    })
  })

  describe('skill matching', () => {
    it('starts with empty matched skills', () => {
      const { result } = renderHook(() => useChatStreaming())
      expect(result.current.matchedSkills).toEqual([])
    })

    it('sets matched skills', () => {
      const { result } = renderHook(() => useChatStreaming())

      act(() => {
        result.current.setMatchedSkills([
          { name: 'Auto-correct', trigger: '/fix' },
          { name: 'Summarize', trigger: '/summary' },
        ])
      })

      expect(result.current.matchedSkills).toHaveLength(2)
      expect(result.current.matchedSkills[0].name).toBe('Auto-correct')
      expect(result.current.matchedSkills[1].trigger).toBe('/summary')
    })

    it('replaces matched skills on update', () => {
      const { result } = renderHook(() => useChatStreaming())

      act(() => {
        result.current.setMatchedSkills([{ name: 'Skill 1', trigger: '/s1' }])
      })

      expect(result.current.matchedSkills).toHaveLength(1)

      act(() => {
        result.current.setMatchedSkills([{ name: 'Skill 2', trigger: '/s2' }])
      })

      expect(result.current.matchedSkills).toHaveLength(1)
      expect(result.current.matchedSkills[0].name).toBe('Skill 2')
    })
  })

  describe('generateUniqueId', () => {
    it('generates unique IDs with prefix', () => {
      const id1 = generateUniqueId('msg')
      const id2 = generateUniqueId('msg')

      expect(id1).toMatch(/^msg-\d+-[a-z0-9]+$/)
      expect(id2).toMatch(/^msg-\d+-[a-z0-9]+$/)
      expect(id1).not.toBe(id2)
    })

    it('uses correct prefix', () => {
      const id = generateUniqueId('custom')
      expect(id.startsWith('custom-')).toBe(true)
    })

    it('is exposed via hook return', () => {
      const { result } = renderHook(() => useChatStreaming())
      expect(result.current.generateUniqueId).toBe(generateUniqueId)
    })
  })

  describe('getStreamCallbacks', () => {
    it('returns callbacks object with all required handlers', () => {
      const { result } = renderHook(() => useChatStreaming())
      const deps = createMockDeps()

      const callbacks = result.current.getStreamCallbacks(deps)

      expect(typeof callbacks.onStart).toBe('function')
      expect(typeof callbacks.onContext).toBe('function')
      expect(typeof callbacks.onThinking).toBe('function')
      expect(typeof callbacks.onThinkingContent).toBe('function')
      expect(typeof callbacks.onSegmentStart).toBe('function')
      expect(typeof callbacks.onSegmentUpdate).toBe('function')
      expect(typeof callbacks.onSegmentUpdateToolCalls).toBe('function')
      expect(typeof callbacks.onSegmentEnd).toBe('function')
      expect(typeof callbacks.onAgentSelected).toBe('function')
      expect(typeof callbacks.onIterationExhausted).toBe('function')
      expect(typeof callbacks.onRouterThinking).toBe('function')
      expect(typeof callbacks.onRouterDecided).toBe('function')
      expect(typeof callbacks.onWorkflowStopped).toBe('function')
      expect(typeof callbacks.onWorkflowComplete).toBe('function')
      expect(typeof callbacks.onToolResult).toBe('function')
      expect(typeof callbacks.onFileCreated).toBe('function')
      expect(typeof callbacks.onFileContent).toBe('function')
      expect(typeof callbacks.onFileContentEnd).toBe('function')
      expect(typeof callbacks.onFileEditStart).toBe('function')
      expect(typeof callbacks.onFileEditApplied).toBe('function')
      expect(typeof callbacks.onFileEditEnd).toBe('function')
      expect(typeof callbacks.onSkillMatched).toBe('function')
      expect(typeof callbacks.onSkillsMatched).toBe('function')
      expect(typeof callbacks.onComplete).toBe('function')
      expect(typeof callbacks.onError).toBe('function')
    })

    describe('onStart callback', () => {
      it('clears previous state', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()

        // Add some initial state
        act(() => {
          result.current.setEditProgress({
            fileId: 'file-1',
            title: 'Test',
            totalEdits: 1,
            completedEdits: 0,
          })
          result.current.setMatchedSkills([{ name: 'Test', trigger: '/test' }])
        })

        const callbacks = result.current.getStreamCallbacks(deps)

        act(() => {
          callbacks.onStart()
        })

        expect(result.current.editProgress).toBe(null)
        expect(result.current.matchedSkills).toHaveLength(0)
      })
    })

    describe('onContext callback', () => {
      it('adds context items to stream render items', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        const contextItems = [
          { id: '1', type: 'draft', title: 'Chapter 1', content: '...' },
        ]

        act(() => {
          callbacks.onContext(contextItems)
        })

        act(() => {
          vi.advanceTimersByTime(100)
        })

        expect(result.current.streamRenderItems).toHaveLength(1)
        expect(result.current.streamRenderItems[0].type).toBe('context')
      })

      it('ignores empty context items', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        act(() => {
          callbacks.onContext([])
        })

        act(() => {
          vi.advanceTimersByTime(100)
        })

        expect(result.current.streamRenderItems).toHaveLength(0)
      })
    })

    describe('onThinking callback', () => {
      it('adds thinking status item', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        act(() => {
          callbacks.onThinking('Analyzing...')
        })

        act(() => {
          vi.advanceTimersByTime(100)
        })

        expect(result.current.streamRenderItems).toHaveLength(1)
        expect(result.current.streamRenderItems[0].type).toBe('thinking_status')
        expect(result.current.streamRenderItems[0].content).toBe('Analyzing...')
      })
    })

    describe('onThinkingContent callback', () => {
      it('creates new thinking_content item when first called', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        act(() => {
          callbacks.onThinkingContent('Initial thought')
        })

        act(() => {
          vi.advanceTimersByTime(100)
        })

        expect(result.current.streamRenderItems).toHaveLength(1)
        expect(result.current.streamRenderItems[0].type).toBe('thinking_content')
        expect(result.current.streamRenderItems[0].content).toBe('Initial thought')
      })

      it('accumulates to existing thinking_content item', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        act(() => {
          callbacks.onThinkingContent('First')
        })

        act(() => {
          vi.advanceTimersByTime(100)
        })

        act(() => {
          callbacks.onThinkingContent(' Second')
        })

        act(() => {
          vi.advanceTimersByTime(100)
        })

        expect(result.current.streamRenderItems).toHaveLength(1)
        expect(result.current.streamRenderItems[0].content).toBe('First Second')
      })
    })

    describe('onSegmentStart callback', () => {
      it('creates content segment', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        act(() => {
          callbacks.onSegmentStart({ id: 'seg-1', type: 'content' })
        })

        act(() => {
          vi.advanceTimersByTime(100)
        })

        expect(result.current.streamRenderItems).toHaveLength(1)
        expect(result.current.streamRenderItems[0].type).toBe('content')
        expect(result.current.streamRenderItems[0].id).toBe('seg-1')
      })

      it('creates tool_calls segment with tool calls', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        act(() => {
          callbacks.onSegmentStart({
            id: 'seg-2',
            type: 'tool_calls',
            toolCalls: [{ tool_name: 'create_file', arguments: {}, status: 'pending' }],
          })
        })

        act(() => {
          vi.advanceTimersByTime(100)
        })

        expect(result.current.streamRenderItems).toHaveLength(1)
        expect(result.current.streamRenderItems[0].type).toBe('tool_calls')
        expect(result.current.streamRenderItems[0].toolCalls).toHaveLength(1)
      })
    })

    describe('onSegmentUpdate callback', () => {
      it('updates existing segment content', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        // First create a segment
        act(() => {
          callbacks.onSegmentStart({ id: 'seg-1', type: 'content' })
        })

        act(() => {
          vi.advanceTimersByTime(100)
        })

        // Then update it
        act(() => {
          callbacks.onSegmentUpdate('seg-1', 'Updated content')
        })

        act(() => {
          vi.advanceTimersByTime(100)
        })

        expect(result.current.streamRenderItems).toHaveLength(1)
        expect(result.current.streamRenderItems[0].content).toBe('Updated content')
      })

      it('strips control markers from content', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        act(() => {
          callbacks.onSegmentStart({ id: 'seg-1', type: 'content' })
        })

        act(() => {
          vi.advanceTimersByTime(100)
        })

        // The stripThinkTags function also removes control markers
        act(() => {
          callbacks.onSegmentUpdate('seg-1', 'Hello [TASK_COMPLETE]World')
        })

        act(() => {
          vi.advanceTimersByTime(100)
        })

        // stripThinkTags removes [TASK_COMPLETE] leaving "Hello World"
        expect(result.current.streamRenderItems[0].content).toBe('Hello World')
      })

      it('creates new segment if not found', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        act(() => {
          callbacks.onSegmentUpdate('non-existent-seg', 'New content')
        })

        act(() => {
          vi.advanceTimersByTime(100)
        })

        expect(result.current.streamRenderItems).toHaveLength(1)
        expect(result.current.streamRenderItems[0].content).toBe('New content')
        expect(result.current.streamRenderItems[0].id).toBe('non-existent-seg')
      })

      it('ignores whitespace-only content', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        act(() => {
          callbacks.onSegmentStart({ id: 'seg-1', type: 'content' })
        })

        act(() => {
          vi.advanceTimersByTime(100)
        })

        // Whitespace-only content should be ignored (stripThinkTags + trim check)
        act(() => {
          callbacks.onSegmentUpdate('seg-1', '   ')
        })

        act(() => {
          vi.advanceTimersByTime(100)
        })

        // Content should remain empty since whitespace was trimmed and ignored
        expect(result.current.streamRenderItems[0].content).toBe('')
      })
    })

    describe('onSegmentUpdateToolCalls callback', () => {
      it('updates tool calls for existing segment', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        // Create tool_calls segment
        act(() => {
          callbacks.onSegmentStart({
            id: 'seg-1',
            type: 'tool_calls',
            toolCalls: [{ tool_name: 'create_file', arguments: {}, status: 'pending' }],
          })
        })

        act(() => {
          vi.advanceTimersByTime(100)
        })

        // Update tool calls
        act(() => {
          callbacks.onSegmentUpdateToolCalls('seg-1', [
            { tool_name: 'create_file', arguments: { title: 'test.txt' }, status: 'success' },
          ])
        })

        act(() => {
          vi.advanceTimersByTime(100)
        })

        expect(result.current.streamRenderItems[0].toolCalls).toHaveLength(1)
        expect(result.current.streamRenderItems[0].toolCalls?.[0].status).toBe('success')
        expect(result.current.streamRenderItems[0].toolCalls?.[0].arguments).toEqual({ title: 'test.txt' })
      })

      it('does not modify non-tool_calls segments', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        act(() => {
          callbacks.onSegmentStart({ id: 'seg-1', type: 'content' })
        })

        act(() => {
          vi.advanceTimersByTime(100)
        })

        act(() => {
          callbacks.onSegmentUpdateToolCalls('seg-1', [
            { tool_name: 'test', arguments: {}, status: 'success' },
          ])
        })

        act(() => {
          vi.advanceTimersByTime(100)
        })

        // Should not add toolCalls to content segment
        expect(result.current.streamRenderItems[0].toolCalls).toBeUndefined()
      })

      it('does nothing if segment not found', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        act(() => {
          callbacks.onSegmentUpdateToolCalls('non-existent', [
            { tool_name: 'test', arguments: {}, status: 'success' },
          ])
        })

        act(() => {
          vi.advanceTimersByTime(100)
        })

        expect(result.current.streamRenderItems).toHaveLength(0)
      })
    })

    describe('onSegmentEnd callback', () => {
      it('handles segment end without error', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        act(() => {
          callbacks.onSegmentStart({ id: 'seg-1', type: 'content' })
        })

        act(() => {
          vi.advanceTimersByTime(100)
        })

        // Should not throw
        act(() => {
          callbacks.onSegmentEnd('seg-1')
        })

        // Segment should still exist
        expect(result.current.streamRenderItems).toHaveLength(1)
      })
    })

    describe('onIterationExhausted callback', () => {
      it('adds iteration exhausted item with all fields', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        act(() => {
          callbacks.onIterationExhausted('collaboration', 10, 10, 'Max iterations reached', 'writer')
        })

        act(() => {
          vi.advanceTimersByTime(100)
        })

        expect(result.current.streamRenderItems).toHaveLength(1)
        const item = result.current.streamRenderItems[0]
        expect(item.type).toBe('iteration_exhausted')
        expect(item.layer).toBe('collaboration')
        expect(item.iterationsUsed).toBe(10)
        expect(item.maxIterations).toBe(10)
        expect(item.reason).toBe('Max iterations reached')
        expect(item.lastAgent).toBe('writer')
      })

      it('handles tool_call layer', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        act(() => {
          callbacks.onIterationExhausted('tool_call', 5, 5, 'Tool limit', 'editor')
        })

        act(() => {
          vi.advanceTimersByTime(100)
        })

        expect(result.current.streamRenderItems[0].layer).toBe('tool_call')
      })

      it('handles missing lastAgent', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        act(() => {
          callbacks.onIterationExhausted('collaboration', 3, 3, 'Limit')
        })

        act(() => {
          vi.advanceTimersByTime(100)
        })

        expect(result.current.streamRenderItems[0].lastAgent).toBeUndefined()
      })
    })

    describe('onWorkflowComplete callback', () => {
      it('adds workflow complete item', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        act(() => {
          callbacks.onWorkflowComplete({
            reason: 'task_complete',
            agent_type: 'writer',
            message: 'All tasks completed successfully',
          })
        })

        act(() => {
          vi.advanceTimersByTime(100)
        })

        expect(result.current.streamRenderItems).toHaveLength(1)
        const item = result.current.streamRenderItems[0]
        expect(item.type).toBe('workflow_complete')
        expect(item.reason).toBe('task_complete')
        expect(item.agentType).toBe('writer')
        expect(item.message).toBe('All tasks completed successfully')
      })

      it('handles different completion reasons', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        act(() => {
          callbacks.onWorkflowComplete({
            reason: 'user_cancelled',
            agent_type: 'router',
            message: 'User cancelled operation',
          })
        })

        act(() => {
          vi.advanceTimersByTime(100)
        })

        expect(result.current.streamRenderItems[0].reason).toBe('user_cancelled')
      })
    })

    describe('onAgentSelected callback', () => {
      it('adds agent selected item', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        act(() => {
          callbacks.onAgentSelected('writer', 'Content Writer', 1, 5, 4)
        })

        act(() => {
          vi.advanceTimersByTime(100)
        })

        expect(result.current.streamRenderItems).toHaveLength(1)
        const item = result.current.streamRenderItems[0]
        expect(item.type).toBe('agent_selected')
        expect(item.agentType).toBe('writer')
        expect(item.agentName).toBe('Content Writer')
        expect(item.iteration).toBe(1)
        expect(item.maxIterations).toBe(5)
        expect(item.remaining).toBe(4)
      })
    })

    describe('onRouterThinking callback', () => {
      it('adds router thinking item', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        act(() => {
          callbacks.onRouterThinking('Analyzing request...')
        })

        act(() => {
          vi.advanceTimersByTime(100)
        })

        expect(result.current.streamRenderItems).toHaveLength(1)
        expect(result.current.streamRenderItems[0].type).toBe('router_thinking')
      })
    })

    describe('onRouterDecided callback', () => {
      it('adds router decided item', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        act(() => {
          callbacks.onRouterDecided('writer', 'Write content', ['writer', 'editor'])
        })

        act(() => {
          vi.advanceTimersByTime(100)
        })

        expect(result.current.streamRenderItems).toHaveLength(1)
        const item = result.current.streamRenderItems[0]
        expect(item.type).toBe('router_decided')
        expect(item.initialAgent).toBe('writer')
        expect(item.workflowPlan).toBe('Write content')
        expect(item.workflowAgents).toEqual(['writer', 'editor'])
      })
    })

    describe('onWorkflowStopped callback', () => {
      it('adds workflow stopped item', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        act(() => {
          callbacks.onWorkflowStopped({
            reason: 'clarification_needed',
            agent_type: 'writer',
            message: 'Need more info',
          })
        })

        act(() => {
          vi.advanceTimersByTime(100)
        })

        expect(result.current.streamRenderItems).toHaveLength(1)
        const item = result.current.streamRenderItems[0]
        expect(item.type).toBe('workflow_stopped')
        expect(item.reason).toBe('clarification_needed')
        expect(item.agentType).toBe('writer')
        expect(item.message).toBe('Need more info')
      })
    })

    describe('new orchestration status callbacks', () => {
      it('adds thinking_status item for handoff', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        act(() => {
          callbacks.onHandoff?.({
            target_agent: 'quality_reviewer',
            reason: '自动质量门控',
            context: '内容较长',
          })
        })

        act(() => {
          vi.advanceTimersByTime(100)
        })

        expect(result.current.streamRenderItems).toHaveLength(1)
        const item = result.current.streamRenderItems[0]
        expect(item.type).toBe('thinking_status')
        expect(item.content).toContain('chat:workflow.handoffMessage')
      })

      it('skips the handoff bubble when the target role is unknown', () => {
        const { result } = renderHook(() => useChatStreaming())
        const callbacks = result.current.getStreamCallbacks(createMockDeps())

        act(() => {
          callbacks.onHandoff?.({ target_agent: 'internal_debugger', reason: 'debug', context: '' })
        })
        act(() => {
          vi.advanceTimersByTime(100)
        })

        expect(result.current.streamRenderItems).toHaveLength(0)
      })

      it('does not add stream node for session_started (noise reduction)', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        act(() => {
          callbacks.onSessionStarted?.('session-123')
        })

        act(() => {
          vi.advanceTimersByTime(100)
        })

        expect(result.current.streamRenderItems).toHaveLength(0)
      })

      it('adds thinking_status item for parallel_start', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        act(() => {
          callbacks.onParallelStart?.('exec-1', 3, ['task1', 'task2', 'task3'])
        })

        act(() => {
          vi.advanceTimersByTime(100)
        })

        expect(result.current.streamRenderItems).toHaveLength(1)
        expect(result.current.streamRenderItems[0].type).toBe('thinking_status')
        expect(result.current.streamRenderItems[0].content).toContain('chat:workflow.parallelStart')
      })

      it('renders no node on parallel task start, one line on task end (live progress)', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        // Task start only records the description; it renders no stream node.
        act(() => {
          callbacks.onParallelTaskStart?.('exec-1', 'task-1', 'tool_call', '章节检查')
        })
        act(() => {
          vi.advanceTimersByTime(100)
        })
        expect(result.current.streamRenderItems).toHaveLength(0)

        // Task end renders a single per-task completion line.
        act(() => {
          callbacks.onParallelTaskEnd?.('exec-1', 'task-1', 'completed')
        })
        act(() => {
          vi.advanceTimersByTime(100)
        })
        expect(result.current.streamRenderItems).toHaveLength(1)
        expect(result.current.streamRenderItems[0].type).toBe('thinking_status')
        expect(result.current.streamRenderItems[0].content).toContain(
          'chat:workflow.parallelTaskCompleted'
        )
      })

      it('uses the failed label and includes the error on a failed parallel task', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        act(() => {
          callbacks.onParallelTaskStart?.('exec-1', 'task-1', 'tool_call', '删除草稿')
          callbacks.onParallelTaskEnd?.('exec-1', 'task-1', 'failed', undefined, 'File not found')
        })
        act(() => {
          vi.advanceTimersByTime(100)
        })

        expect(result.current.streamRenderItems).toHaveLength(1)
        expect(result.current.streamRenderItems[0].content).toContain(
          'chat:workflow.parallelTaskFailed'
        )
      })

      it('adds thinking_status item for steering_received', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        act(() => {
          callbacks.onSteeringReceived?.('steer-1', '请聚焦第二章')
        })

        act(() => {
          vi.advanceTimersByTime(100)
        })

        expect(result.current.streamRenderItems).toHaveLength(1)
        expect(result.current.streamRenderItems[0].type).toBe('thinking_status')
        expect(result.current.streamRenderItems[0].content).toContain('chat:workflow.steeringReceived')
      })
    })

    describe('onToolResult callback', () => {
      it('triggers file tree refresh on success for file tools', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        act(() => {
          callbacks.onToolResult('create_file', 'success')
        })

        act(() => {
          vi.advanceTimersByTime(200)
        })

        expect(deps.triggerFileTreeRefresh).toHaveBeenCalled()
      })

      it('does not trigger refresh for non-file tools', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        act(() => {
          callbacks.onToolResult('other_tool', 'success')
        })

        act(() => {
          vi.advanceTimersByTime(200)
        })

        expect(deps.triggerFileTreeRefresh).not.toHaveBeenCalled()
      })

      it('does not trigger refresh on error', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        act(() => {
          callbacks.onToolResult('create_file', 'error')
        })

        act(() => {
          vi.advanceTimersByTime(200)
        })

        expect(deps.triggerFileTreeRefresh).not.toHaveBeenCalled()
      })

      it('dispatches project status update event on update_project success', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)
        const eventHandler = vi.fn()
        window.addEventListener(PROJECT_STATUS_UPDATED_EVENT, eventHandler as EventListener)

        try {
          act(() => {
            callbacks.onToolResult('update_project', 'success', {
              data: {
                updated_fields: ['summary'],
              },
            })
          })

          expect(eventHandler).toHaveBeenCalledTimes(1)
          const event = eventHandler.mock.calls[0]?.[0] as CustomEvent
          expect(event.detail).toEqual({
            projectId: 'test-project-id',
            updatedFields: ['summary'],
          })
        } finally {
          window.removeEventListener(PROJECT_STATUS_UPDATED_EVENT, eventHandler as EventListener)
        }
      })

      it('reads nested project_status.updated_fields when dispatching status update event', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)
        const eventHandler = vi.fn()
        window.addEventListener(PROJECT_STATUS_UPDATED_EVENT, eventHandler as EventListener)

        try {
          act(() => {
            callbacks.onToolResult('update_project', 'success', {
              data: {
                project_status: {
                  updated_fields: ['notes'],
                },
              },
            })
          })

          const event = eventHandler.mock.calls[0]?.[0] as CustomEvent
          expect(event.detail).toEqual({
            projectId: 'test-project-id',
            updatedFields: ['notes'],
          })
        } finally {
          window.removeEventListener(PROJECT_STATUS_UPDATED_EVENT, eventHandler as EventListener)
        }
      })

      it('does not dispatch project status update event when only tasks are updated', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)
        const eventHandler = vi.fn()
        window.addEventListener(PROJECT_STATUS_UPDATED_EVENT, eventHandler as EventListener)

        try {
          act(() => {
            callbacks.onToolResult('update_project', 'success', {
              data: {
                plan: {
                  tasks: [],
                },
              },
            })
          })

          expect(eventHandler).not.toHaveBeenCalled()
        } finally {
          window.removeEventListener(PROJECT_STATUS_UPDATED_EVENT, eventHandler as EventListener)
        }
      })

      it('does not dispatch status event when updated fields exclude AI memory keys', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)
        const eventHandler = vi.fn()
        window.addEventListener(PROJECT_STATUS_UPDATED_EVENT, eventHandler as EventListener)

        try {
          act(() => {
            callbacks.onToolResult('update_project', 'success', {
              data: {
                updated_fields: ['name'],
              },
            })
          })

          expect(eventHandler).not.toHaveBeenCalled()
        } finally {
          window.removeEventListener(PROJECT_STATUS_UPDATED_EVENT, eventHandler as EventListener)
        }
      })

      it('prefers payload project_id over active project id when dispatching status event', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        deps.activeProjectId = 'active-project-id'
        const callbacks = result.current.getStreamCallbacks(deps)
        const eventHandler = vi.fn()
        window.addEventListener(PROJECT_STATUS_UPDATED_EVENT, eventHandler as EventListener)

        try {
          act(() => {
            callbacks.onToolResult('update_project', 'success', {
              data: {
                project_id: 'tool-project-id',
                updated_fields: ['summary'],
              },
            })
          })

          const event = eventHandler.mock.calls[0]?.[0] as CustomEvent
          expect(event.detail).toEqual({
            projectId: 'tool-project-id',
            updatedFields: ['summary'],
          })
        } finally {
          window.removeEventListener(PROJECT_STATUS_UPDATED_EVENT, eventHandler as EventListener)
        }
      })
    })

    describe('onFileCreated callback', () => {
      it('triggers file tree refresh and selects file', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        act(() => {
          callbacks.onFileCreated('file-123', 'draft', 'New Chapter')
        })

        act(() => {
          vi.advanceTimersByTime(250)
        })

        expect(deps.triggerFileTreeRefresh).toHaveBeenCalled()
        expect(deps.startFileStreaming).toHaveBeenCalledWith('file-123')
        expect(deps.setSelectedItem).toHaveBeenCalledWith({
          id: 'file-123',
          type: 'draft',
          title: 'New Chapter',
        })
      })
    })

    describe('onFileContent callback', () => {
      it('appends content to file', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        act(() => {
          callbacks.onFileContent('file-123', 'chunk of content')
        })

        expect(deps.appendFileContent).toHaveBeenCalledWith('file-123', 'chunk of content')
      })
    })

    describe('onFileContentEnd callback', () => {
      it('finishes file streaming and refreshes tree', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        deps.streamingFileId = 'file-123'
        const callbacks = result.current.getStreamCallbacks(deps)

        act(() => {
          callbacks.onFileContentEnd('file-123')
        })

        act(() => {
          vi.advanceTimersByTime(250)
        })

        expect(deps.finishFileStreaming).toHaveBeenCalledWith('file-123')
        expect(deps.triggerFileTreeRefresh).toHaveBeenCalled()
      })
    })

    describe('onFileEditStart callback', () => {
      it('sets edit progress', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        act(() => {
          callbacks.onFileEditStart('file-123', 'Chapter 1', 5)
        })

        expect(result.current.editProgress).toEqual({
          fileId: 'file-123',
          title: 'Chapter 1',
          totalEdits: 5,
          completedEdits: 0,
        })
      })
    })

    describe('onFileEditApplied callback', () => {
      it('updates edit progress', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        // Start edit first
        act(() => {
          callbacks.onFileEditStart('file-123', 'Chapter 1', 5)
        })

        act(() => {
          callbacks.onFileEditApplied('file-123', 0, 'replace', 'old', 'new', true)
        })

        expect(result.current.editProgress?.completedEdits).toBe(1)
        expect(result.current.editProgress?.currentOp).toBe('replace')
      })
    })

    describe('onFileEditEnd callback', () => {
      it('clears edit progress and enters diff review', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        deps.getCurrentSelectedItem = vi.fn(() => ({
          id: 'file-123',
          type: 'outline',
          title: 'Chapter 1',
        }))
        const callbacks = result.current.getStreamCallbacks(deps)

        // Start edit first
        act(() => {
          callbacks.onFileEditStart('file-123', 'Chapter 1', 5)
        })

        expect(result.current.editProgress).not.toBe(null)

        act(() => {
          callbacks.onFileEditEnd('file-123', 5, 100, 'new content', 'old content')
        })

        act(() => {
          vi.advanceTimersByTime(250)
        })

        expect(result.current.editProgress).toBe(null)
        expect(deps.enterDiffReview).toHaveBeenCalledWith('file-123', 'old content', 'new content')
        expect(deps.triggerFileTreeRefresh).toHaveBeenCalled()
        expect(deps.setSelectedItem).toHaveBeenCalledWith({
          id: 'file-123',
          type: 'outline',
          title: 'Chapter 1',
        })
      })

      it('switches selection to edited file for visible diff review when current file is different', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        deps.getCurrentSelectedItem = vi.fn(() => ({
          id: 'other-file',
          type: 'draft',
          title: 'Other',
        }))
        const callbacks = result.current.getStreamCallbacks(deps)

        act(() => {
          callbacks.onFileEditStart('file-999', 'Edited File', 1, 'outline')
        })

        act(() => {
          callbacks.onFileEditEnd('file-999', 1, 88, 'new content', 'old content', 'outline', 'Edited File')
        })

        expect(deps.enterDiffReview).toHaveBeenCalledWith('file-999', 'old content', 'new content')
        expect(deps.setSelectedItem).toHaveBeenCalledWith({
          id: 'file-999',
          type: 'outline',
          title: 'Edited File',
        })
      })

      it('triggers editor refresh without diff review if no original content', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        act(() => {
          callbacks.onFileEditStart('file-123', 'Chapter 1', 5)
        })

        act(() => {
          callbacks.onFileEditEnd('file-123', 5, 100, 'new content')
        })

        expect(deps.enterDiffReview).not.toHaveBeenCalled()
        expect(deps.triggerEditorRefresh).toHaveBeenCalledWith('file-123')
      })
    })

    describe('onSkillMatched callback', () => {
      it('sets matched skills', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        act(() => {
          callbacks.onSkillMatched('skill-123', 'Auto-correct', '/fix')
        })

        expect(result.current.matchedSkills).toHaveLength(1)
        expect(result.current.matchedSkills[0]).toEqual({
          id: 'skill-123',
          name: 'Auto-correct',
          trigger: '/fix',
        })
      })

      it('appends skills matched later in the same turn and dedupes by id', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        act(() => {
          callbacks.onSkillMatched('skill-1', '悬念大师', 'selected')
          callbacks.onSkillMatched('skill-2', '节奏控', 'load_skill')
          callbacks.onSkillMatched('skill-1', '悬念大师', 'load_skill')
        })

        expect(result.current.matchedSkills).toEqual([
          { id: 'skill-1', name: '悬念大师', trigger: 'selected' },
          { id: 'skill-2', name: '节奏控', trigger: 'load_skill' },
        ])
      })

      it('dedupes by name when the skill id is missing', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        act(() => {
          callbacks.onSkillMatched('', 'Fix', 'load_skill')
          callbacks.onSkillMatched('', 'Fix', 'load_skill')
        })

        expect(result.current.matchedSkills).toEqual([{ name: 'Fix', trigger: 'load_skill' }])
      })
    })

    describe('onSkillsMatched callback', () => {
      it('sets multiple matched skills', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        act(() => {
          callbacks.onSkillsMatched([
            { id: '1', name: 'Fix', trigger: '/fix', confidence: 0.9 },
            { id: '2', name: 'Summarize', trigger: '/summary', confidence: 0.8 },
          ])
        })

        expect(result.current.matchedSkills).toHaveLength(2)
        expect(result.current.matchedSkills[0].name).toBe('Fix')
        expect(result.current.matchedSkills[1].name).toBe('Summarize')
      })
    })

    describe('onComplete callback', () => {
      it('clears edit progress and stream items', async () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        // Add some state
        act(() => {
          result.current.setEditProgress({
            fileId: 'file-1',
            title: 'Test',
            totalEdits: 1,
            completedEdits: 0,
          })
        })

        await act(async () => {
          await callbacks.onComplete([], null)
        })

        expect(result.current.editProgress).toBe(null)
        expect(result.current.streamRenderItems).toHaveLength(0)
      })

      it.each([
        ['prose-only completion', [{ id: '1', type: 'content', content: 'Generated content' }], undefined],
        ['read-only tool result', [{ id: '1', type: 'tool_calls', toolCalls: [{ tool_name: 'search', arguments: {}, status: 'success' }] }], undefined],
        ['named failed mutation tool', [{ id: '1', type: 'tool_calls', toolCalls: [{ tool_name: 'edit_file', arguments: { title: 'Chapter 1' }, status: 'error' }] }], undefined],
        ['absent mutation flag', [], undefined],
        ['explicit false mutation flag', [], { confirmedFileMutation: false }],
      ])('does not snapshot %s', async (_label, segments, completionMeta) => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        await act(async () => {
          await callbacks.onComplete(segments, null, completionMeta)
        })

        expect(deps.createSnapshot).not.toHaveBeenCalled()
      })

      it('creates one generic snapshot for an explicit confirmed mutation even without segments', async () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        await act(async () => {
          await callbacks.onComplete([], null, { confirmedFileMutation: true })
        })

        expect(deps.createSnapshot).toHaveBeenCalledTimes(1)
        expect(deps.createSnapshot).toHaveBeenCalledWith('test-project-id', {
          description: 'chat:message.aiDoneFilesModified',
          snapshotType: 'auto',
        })
        expect(deps.t).toHaveBeenCalledWith('chat:message.aiDoneFilesModified')
      })

      it('suppresses snapshots for partial completion even when mutation was confirmed', async () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        await act(async () => {
          await callbacks.onComplete([], null, {
            confirmedFileMutation: true,
            partial: true,
          })
        })

        expect(deps.createSnapshot).not.toHaveBeenCalled()
      })

      it('creates only one snapshot for a completed turn containing multiple mutations', async () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)
        const segments = [{
          id: '1',
          type: 'tool_calls',
          toolCalls: [
            { tool_name: 'create_file', arguments: { title: 'One' }, status: 'success' },
            { tool_name: 'edit_file', arguments: { title: 'Two' }, status: 'success' },
          ],
        }]

        await act(async () => {
          await callbacks.onComplete(segments, null, { confirmedFileMutation: true })
        })

        expect(deps.createSnapshot).toHaveBeenCalledTimes(1)
      })

      it('does not create a snapshot without an active project', async () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        deps.activeProjectId = null
        const callbacks = result.current.getStreamCallbacks(deps)

        await act(async () => {
          await callbacks.onComplete([], null, { confirmedFileMutation: true })
        })

        expect(deps.createSnapshot).not.toHaveBeenCalled()
      })

      it('does not carry mutation confirmation across stream or project callback sets', async () => {
        const { result } = renderHook(() => useChatStreaming())
        const firstProject = createMockDeps()
        firstProject.activeProjectId = 'project-a'
        const firstCallbacks = result.current.getStreamCallbacks(firstProject)

        await act(async () => {
          await firstCallbacks.onComplete([], null, { confirmedFileMutation: true })
        })

        const nextProject = createMockDeps()
        nextProject.activeProjectId = 'project-b'
        const nextCallbacks = result.current.getStreamCallbacks(nextProject)
        await act(async () => {
          await nextCallbacks.onComplete([], null)
        })

        expect(firstProject.createSnapshot).toHaveBeenCalledTimes(1)
        expect(nextProject.createSnapshot).not.toHaveBeenCalled()
      })

      it('handles confirmed snapshot creation errors as best effort', async () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        deps.createSnapshot = vi.fn().mockRejectedValue(new Error('Snapshot failed'))
        const callbacks = result.current.getStreamCallbacks(deps)

        await act(async () => {
          await expect(
            callbacks.onComplete([], null, { confirmedFileMutation: true })
          ).resolves.toBeUndefined()
        })

        expect(result.current.editProgress).toBe(null)
        expect(result.current.streamRenderItems).toHaveLength(0)
      })

      it('force flushes pending stream items before processing', async () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        // Add items via updateStreamItems (which uses throttling)
        act(() => {
          result.current.updateStreamItems((prev) => [
            ...prev,
            {
              type: 'content',
              id: 'test-id',
              content: 'Pending content',
              timestamp: new Date(),
            },
          ])
        })

        // Don't advance timers - items should be in ref but not state yet

        await act(async () => {
          await callbacks.onComplete(
            [{ id: '1', type: 'content', content: 'Final content' }],
            null
          )
        })

        // After onComplete, items should be cleared
        expect(result.current.streamRenderItems).toHaveLength(0)
      })
    })

    describe('onError callback', () => {
      it('clears edit progress', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        const callbacks = result.current.getStreamCallbacks(deps)

        act(() => {
          result.current.setEditProgress({
            fileId: 'file-1',
            title: 'Test',
            totalEdits: 1,
            completedEdits: 0,
          })
        })

        act(() => {
          callbacks.onError()
        })

        expect(result.current.editProgress).toBe(null)
      })

      it('finishes streaming file on error', () => {
        const { result } = renderHook(() => useChatStreaming())
        const deps = createMockDeps()
        deps.streamingFileId = 'file-123'
        const callbacks = result.current.getStreamCallbacks(deps)

        act(() => {
          callbacks.onError()
        })

        expect(deps.finishFileStreaming).toHaveBeenCalledWith('file-123')
      })
    })
  })
})

describe('throttle utility', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('executes immediately on first call', () => {
    const fn = vi.fn()
    const throttled = throttle(fn, 100)

    throttled('first')

    expect(fn).toHaveBeenCalledTimes(1)
    expect(fn).toHaveBeenCalledWith('first')
  })

  it('throttles subsequent calls', () => {
    const fn = vi.fn()
    const throttled = throttle(fn, 100)

    throttled('first')
    throttled('second')
    throttled('third')

    expect(fn).toHaveBeenCalledTimes(1) // Only first call executed immediately
  })

  it('executes latest call after delay', () => {
    const fn = vi.fn()
    const throttled = throttle(fn, 100)

    throttled('first')
    throttled('second')

    vi.advanceTimersByTime(100)

    expect(fn).toHaveBeenCalledTimes(2)
    expect(fn).toHaveBeenLastCalledWith('second')
  })

  it('can cancel pending execution', () => {
    const fn = vi.fn()
    const throttled = throttle(fn, 100)

    throttled('first')
    throttled('second')
    throttled.cancel()

    vi.advanceTimersByTime(100)

    expect(fn).toHaveBeenCalledTimes(1) // Only first call, second was cancelled
  })
})

describe('generateUniqueId utility', () => {
  it('generates unique IDs', () => {
    const ids = new Set()
    for (let i = 0; i < 100; i++) {
      ids.add(generateUniqueId('test'))
    }
    expect(ids.size).toBe(100)
  })

  it('uses correct prefix', () => {
    expect(generateUniqueId('msg').startsWith('msg-')).toBe(true)
    expect(generateUniqueId('context').startsWith('context-')).toBe(true)
  })
})
