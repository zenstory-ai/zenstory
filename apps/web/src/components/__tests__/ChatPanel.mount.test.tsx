import React from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'

const mockAgentStreamState = vi.hoisted(() => ({
  isStreaming: false,
  isThinking: false,
  thinkingContent: '',
  error: null as string | null,
  errorCode: null as string | null,
}))

const capturedUseAgentStream = vi.hoisted(() => ({
  options: null as Record<string, unknown> | null,
}))

const chatPanelTranslations: Record<string, string> = {
  'chat:panel.dailyCostExceededTitle': '今日免费 AI 额度不足',
  'chat:panel.dailyCostExceededHint': '额度于北京时间次日零点恢复，升级套餐可继续创作。',
  'chat:panel.quotaExceededTitle': '今日 AI 配额已用尽',
  'chat:panel.quotaExceededHint': '额度次日恢复，升级会员可获得更多每日额度。',
  'chat:input.mode.switchedFast': '已切换到快速模式：更快出结果（可能更简略）',
  'chat:input.mode.switchedQuality': '已切换到高质量模式：更稳更全面（可能更慢）',
  'dashboard:billing.ctaUpgradePro': '升级专业版',
  'home:pricingTeaser.viewPricing': '查看套餐权益',
}

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: { defaultValue?: string } | string) =>
      chatPanelTranslations[key] ?? (typeof options === 'string' ? options : options?.defaultValue ?? key),
  }),
}))

vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({
    currentProjectId: 'project-1',
    selectedItem: null,
    triggerFileTreeRefresh: vi.fn(),
    triggerEditorRefresh: vi.fn(),
    setSelectedItem: vi.fn(),
    appendFileContent: vi.fn(),
    finishFileStreaming: vi.fn(),
    startFileStreaming: vi.fn(),
    streamingFileId: null,
    enterDiffReview: vi.fn(),
  }),
}))

vi.mock('../../contexts/MobileLayoutContext', () => ({
  useMobileLayout: () => ({ isMobile: false }),
}))

vi.mock('../../contexts/MaterialAttachmentContext', () => ({
  useMaterialAttachment: () => ({
    attachedFileIds: [],
    attachedLibraryMaterials: [],
    clearMaterials: vi.fn(),
  }),
}))

vi.mock('../../contexts/TextQuoteContext', () => ({
  useTextQuote: () => ({
    quotes: [],
    clearQuotes: vi.fn(),
  }),
}))

const mockStreamSnapshot = vi.hoisted(() => ({ items: [] as Array<Record<string, unknown>> }))

const streamCallbacks = {
  onStart: vi.fn(),
  onContext: vi.fn(),
  onThinking: vi.fn(),
  onThinkingContent: vi.fn(),
  onSegmentStart: vi.fn(),
  onSegmentUpdate: vi.fn(),
  onSegmentUpdateToolCalls: vi.fn(),
  onSegmentEnd: vi.fn(),
  onComplete: vi.fn(async () => {}),
  onError: vi.fn(),
  onToolResult: vi.fn(),
  onFileCreated: vi.fn(),
  onFileContent: vi.fn(),
  onFileContentEnd: vi.fn(),
  onFileEditStart: vi.fn(),
  onFileEditApplied: vi.fn(),
  onFileEditEnd: vi.fn(),
  onSkillMatched: vi.fn(),
  onSkillsMatched: vi.fn(),
  onAgentSelected: vi.fn(),
  onIterationExhausted: vi.fn(),
  onRouterThinking: vi.fn(),
  onRouterDecided: vi.fn(),
  onHandoff: vi.fn(),
  onWorkflowStopped: vi.fn(),
  onWorkflowComplete: vi.fn(),
  onSessionStarted: vi.fn(),
  onParallelStart: vi.fn(),
  onParallelTaskStart: vi.fn(),
  onParallelTaskEnd: vi.fn(),
  onParallelEnd: vi.fn(),
  onSteeringReceived: vi.fn(),
}

vi.mock('../../hooks/useChatStreaming', () => ({
  useChatStreaming: () => ({
    streamRenderItems: [],
    getStreamItemsSnapshot: () => mockStreamSnapshot.items.slice(),
    clearStreamItems: vi.fn(),
    editProgress: null,
    setEditProgress: vi.fn(),
    aiSuggestions: [],
    setAiSuggestions: vi.fn(),
    isRefreshingSuggestions: false,
    setIsRefreshingSuggestions: vi.fn(),
    matchedSkills: [],
    setMatchedSkills: vi.fn(),
    getStreamCallbacks: vi.fn(() => streamCallbacks),
    clearIdleTimer: vi.fn(),
  }),
}))

vi.mock('../../hooks/useAgentStream', () => ({
  useAgentStream: (_projectId: string, options?: Record<string, unknown>) => {
    capturedUseAgentStream.options = options ?? null
    return ({
    state: {},
    startStream: vi.fn(),
    cancel: vi.fn(),
    reset: vi.fn(),
    isStreaming: mockAgentStreamState.isStreaming,
    isThinking: mockAgentStreamState.isThinking,
    thinkingContent: mockAgentStreamState.thinkingContent,
    conflicts: [],
    error: mockAgentStreamState.error,
    errorCode: mockAgentStreamState.errorCode,
  })
  },
}))

vi.mock('../../hooks/useDraftPersistence', () => ({
  useDraftPersistence: () => ({
    draft: '',
    saveDraft: vi.fn(),
    clearDraft: vi.fn(),
  }),
}))

vi.mock('../../lib/chatApi', () => ({
  getRecentMessages: vi.fn(async () => []),
  createNewSession: vi.fn(async () => ({ id: 'session-1' })),
  submitMessageFeedback: vi.fn(),
}))

vi.mock('../../lib/agentApi', () => ({
  fetchSuggestions: vi.fn(async () => []),
}))

vi.mock('../../lib/api', () => ({
  fileVersionApi: {},
  versionApi: {},
}))

const mockMessageList = vi.fn(() => <div data-testid="mock-message-list" />)
const mockMessageInput = vi.fn((props: { onGenerationModeChange?: (mode: 'fast' | 'quality') => void }) => (
  <div data-testid="mock-message-input">
    <button
      type="button"
      data-testid="mock-generation-mode-toggle"
      onClick={() => props.onGenerationModeChange?.('fast')}
    >
      toggle-generation-mode
    </button>
  </div>
))

vi.mock('../MessageList', () => ({
  MessageList: React.forwardRef((props: unknown, _ref) => mockMessageList(props)),
}))

vi.mock('../MessageInput', () => ({
  MessageInput: (props: { onGenerationModeChange?: (mode: 'fast' | 'quality') => void }) =>
    mockMessageInput(props),
}))

vi.mock('../ToolResultCard', () => ({
  ToolResultCard: () => <div data-testid="mock-tool-result-card" />,
}))

vi.mock('../ProjectStatusDialog', () => ({
  ProjectStatusDialog: () => null,
}))

vi.mock('../subscription/QuotaBadge', () => ({
  QuotaBadge: () => <div data-testid="mock-quota-badge" />,
}))

vi.mock('../../lib/toast', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}))

import { ChatPanel } from '../ChatPanel'
import { getRecentMessages } from '../../lib/chatApi'
import { fetchSuggestions } from '../../lib/agentApi'
import { toast } from '../../lib/toast'

describe('ChatPanel mount smoke', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    capturedUseAgentStream.options = null
    mockStreamSnapshot.items = []
    mockAgentStreamState.isStreaming = false
    mockAgentStreamState.isThinking = false
    mockAgentStreamState.thinkingContent = ''
    mockAgentStreamState.error = null
    mockAgentStreamState.errorCode = null
    localStorage.removeItem('zenstory_suggestions_cache_project-1')
  })

  it('mounts without runtime initialization errors', async () => {
    expect(() => render(<ChatPanel />)).not.toThrow()

    await waitFor(() => {
      expect(screen.getByTestId('message-list')).toBeInTheDocument()
    })

    expect(screen.getByTestId('mock-message-input')).toBeInTheDocument()
  })

  it('hydrates historical tool cards and status cards into message list', async () => {
    vi.mocked(getRecentMessages).mockResolvedValueOnce([
      {
        id: 'msg-1',
        session_id: 'session-1',
        role: 'assistant',
        content: '仅正文保留',
        tool_calls: '[{"id":"tool-1","name":"query_files","arguments":"{}","status":"success","result":{"items":[]}}]',
        created_at: '2026-03-01T12:00:00Z',
        metadata: '{"status_cards":[{"type":"workflow_stopped","reason":"clarification_needed","question":"请确认主角姓名"}]}',
      },
    ] as never)

    render(<ChatPanel />)

    await waitFor(() => {
      expect(mockMessageList).toHaveBeenCalled()
    })

    const calls = mockMessageList.mock.calls;
    const lastProps = calls[calls.length - 1]?.[0] as {
      messages?: Array<{
        toolCalls?: unknown[];
        toolResults?: unknown[];
        statusCards?: unknown[];
        content: string;
      }>;
    };
    expect(lastProps.messages?.[0]?.content).toBe('仅正文保留')
    expect(lastProps.messages?.[0]?.toolCalls).toEqual([])
    expect(lastProps.messages?.[0]?.toolResults).toHaveLength(1)
    expect(lastProps.messages?.[0]?.statusCards).toHaveLength(1)
  })

  it('hydrates ordered metadata using final tool results rather than grouping categories', async () => {
    vi.mocked(getRecentMessages).mockResolvedValueOnce([{
      id: 'ordered-backend', session_id: 'session-1', role: 'assistant', content: 'BeforeAfter',
      tool_calls: JSON.stringify([{ id: 'tool-1', name: 'query_files', arguments: {}, status: 'success', result: { items: [] } }]),
      metadata: JSON.stringify({ display_events: [
        { type: 'content', content: 'Before' }, { type: 'tool_call', tool_call_index: 0 },
        { type: 'content', content: 'After' }, { type: 'agent_selected', data: { agent_type: 'writer', agent_name: 'Writer' } },
      ] }), created_at: '2026-10-05T10:00:00Z',
    }] as never)
    render(<ChatPanel />)
    await waitFor(() => {
      const props = mockMessageList.mock.calls.at(-1)?.[0] as { messages?: Array<{ displayItems?: Array<{ type: string; toolCalls?: Array<{ status: string }> }> }> }
      expect(props.messages?.[0]?.displayItems?.map(item => item.type)).toEqual(['content', 'tool_calls', 'content', 'agent_selected'])
      expect(props.messages?.[0]?.displayItems?.[1].toolCalls?.[0].status).toBe('success')
    })
  })

  it('preserves the synchronous stream buffer on completion including handoff events', async () => {
    const timestamp = new Date()
    mockStreamSnapshot.items = [
      { type: 'content', id: 'before', content: 'Before', timestamp },
      { type: 'thinking_status', id: 'handoff', content: 'Handing off', timestamp },
      { type: 'content', id: 'after', content: 'After', timestamp },
    ]
    render(<ChatPanel />)
    await waitFor(() => expect(capturedUseAgentStream.options).not.toBeNull())
    await act(async () => {
      const options = capturedUseAgentStream.options as { onComplete: (segments: unknown[], action: unknown) => Promise<void> }
      await options.onComplete([{ type: 'content', id: 'before', content: 'Before' }, { type: 'content', id: 'after', content: 'After' }], null)
    })
    await waitFor(() => {
      const props = mockMessageList.mock.calls.at(-1)?.[0] as { messages?: Array<{ displayItems?: unknown[] }> }
      expect(props.messages?.at(-1)?.displayItems).toEqual(mockStreamSnapshot.items)
    })
  })

  it('assigns backendMessageId for status-only completions using done metadata', async () => {
    vi.mocked(getRecentMessages)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([
        {
          id: 'assistant-backend-1',
          session_id: 'session-1',
          role: 'assistant',
          content: '',
          tool_calls: null,
          created_at: '2026-03-01T12:00:05Z',
          metadata: '{"status_cards":[{"type":"workflow_stopped","reason":"clarification_needed","question":"请确认设定"}]}',
        },
      ] as never)

    render(<ChatPanel />)

    await waitFor(() => {
      expect(capturedUseAgentStream.options).not.toBeNull()
    })

    const options = capturedUseAgentStream.options as {
      onWorkflowStopped?: (data: Record<string, unknown>) => void;
      onComplete?: (segments: unknown[], applyAction: unknown, meta?: Record<string, unknown>) => void;
    }

    act(() => {
      options.onWorkflowStopped?.({
        reason: 'clarification_needed',
        question: '请确认设定',
      })
    })

    await act(async () => {
      options.onComplete?.([], null, {
        assistantMessageId: 'assistant-backend-1',
        sessionId: 'session-1',
      })
    })

    await waitFor(() => {
      const calls = mockMessageList.mock.calls
      const lastProps = calls[calls.length - 1]?.[0] as {
        messages?: Array<{ backendMessageId?: string; statusCards?: unknown[] }>
      }
      expect(lastProps.messages?.[0]?.backendMessageId).toBe('assistant-backend-1')
      expect(lastProps.messages?.[0]?.statusCards).toHaveLength(1)
    })
  })

  it('offers the upgrade path when the daily cost allowance is exhausted', async () => {
    mockAgentStreamState.errorCode = 'ERR_QUOTA_AI_DAILY_COST_EXCEEDED'
    mockAgentStreamState.error = '今日免费 AI 额度不足'
    render(<ChatPanel />)
    expect((await screen.findAllByText('今日免费 AI 额度不足')).length).toBeGreaterThan(0)
    expect(screen.getByRole('button', {name: '升级专业版'})).toBeInTheDocument()
    expect(screen.queryByText('今日 AI 配额已用尽')).not.toBeInTheDocument()
  })

  it('opens quota upgrade modal when quota error code is returned', async () => {
    mockAgentStreamState.errorCode = 'ERR_QUOTA_AI_CONVERSATIONS_EXCEEDED'
    mockAgentStreamState.error = 'quota exceeded'

    render(<ChatPanel />)

    await waitFor(() => {
      expect(screen.getByText('今日 AI 配额已用尽')).toBeInTheDocument()
    })
  })

  it('quota modal primary action navigates to billing with source', async () => {
    const originalLocation = window.location
    const assignMock = vi.fn()
    Object.defineProperty(window, 'location', {
      value: { ...originalLocation, assign: assignMock },
      writable: true,
      configurable: true,
    })

    try {
      mockAgentStreamState.errorCode = 'ERR_QUOTA_AI_CONVERSATIONS_EXCEEDED'
      mockAgentStreamState.error = 'quota exceeded'
      render(<ChatPanel />)

      await waitFor(() => {
        expect(screen.getByRole('button', { name: '升级专业版' })).toBeInTheDocument()
      })

      screen.getByRole('button', { name: '升级专业版' }).click()
      expect(assignMock).toHaveBeenCalledWith('/dashboard/billing?source=chat_quota_blocked')
    } finally {
      Object.defineProperty(window, 'location', {
        value: originalLocation,
        writable: true,
        configurable: true,
      })
    }
  })

  it('quota modal secondary action navigates to pricing with source', async () => {
    const originalLocation = window.location
    const assignMock = vi.fn()
    Object.defineProperty(window, 'location', {
      value: { ...originalLocation, assign: assignMock },
      writable: true,
      configurable: true,
    })

    try {
      mockAgentStreamState.errorCode = 'ERR_QUOTA_AI_CONVERSATIONS_EXCEEDED'
      mockAgentStreamState.error = 'quota exceeded'
      render(<ChatPanel />)

      await waitFor(() => {
        expect(screen.getByRole('button', { name: '查看套餐权益' })).toBeInTheDocument()
      })

      screen.getByRole('button', { name: '查看套餐权益' }).click()
      expect(assignMock).toHaveBeenCalledWith('/pricing?source=chat_quota_blocked')
    } finally {
      Object.defineProperty(window, 'location', {
        value: originalLocation,
        writable: true,
        configurable: true,
      })
    }
  })

  it('shows toast after switching generation mode', async () => {
    render(<ChatPanel />)

    await waitFor(() => {
      expect(screen.getByTestId('mock-generation-mode-toggle')).toBeInTheDocument()
    })

    screen.getByTestId('mock-generation-mode-toggle').click()

    expect(vi.mocked(toast.success)).toHaveBeenCalledWith(
      '已切换到快速模式：更快出结果（可能更简略）',
    )
  })

  it('uses cached suggestions on open without refetching in the background', async () => {
    localStorage.setItem(
      'zenstory_suggestions_cache_project-1',
      JSON.stringify({ suggestions: ['继续写第二章'], updatedAt: Date.now() }),
    )

    render(<ChatPanel />)

    await waitFor(() => {
      expect(vi.mocked(getRecentMessages)).toHaveBeenCalled()
    })
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50))
    })

    expect(vi.mocked(fetchSuggestions)).not.toHaveBeenCalled()
  })

  it('does not poll suggestions while the user is idle', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      vi.mocked(getRecentMessages).mockResolvedValueOnce([
        {
          id: 'msg-idle-1',
          session_id: 'session-1',
          role: 'assistant',
          content: '上一轮的回复',
          tool_calls: null,
          created_at: '2026-10-05T10:00:00Z',
          metadata: null,
        },
      ] as never)

      render(<ChatPanel />)

      await waitFor(() => {
        expect(vi.mocked(fetchSuggestions)).toHaveBeenCalledTimes(1)
      })

      await act(async () => {
        vi.advanceTimersByTime(30000)
      })

      expect(vi.mocked(fetchSuggestions)).toHaveBeenCalledTimes(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('requests suggestions once after a completed turn', async () => {
    localStorage.setItem(
      'zenstory_suggestions_cache_project-1',
      JSON.stringify({ suggestions: ['继续写第二章'], updatedAt: Date.now() }),
    )
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      render(<ChatPanel />)
      await waitFor(() => expect(capturedUseAgentStream.options).not.toBeNull())

      const options = capturedUseAgentStream.options as {
        onComplete: (segments: unknown[], action: unknown) => Promise<void>
      }
      await act(async () => {
        await options.onComplete([{ type: 'content', id: 'turn-1', content: '第一章写好了' }], null)
      })
      await act(async () => {
        vi.advanceTimersByTime(20000)
      })

      expect(vi.mocked(fetchSuggestions)).toHaveBeenCalledTimes(1)
    } finally {
      vi.useRealTimers()
    }
  })
})
