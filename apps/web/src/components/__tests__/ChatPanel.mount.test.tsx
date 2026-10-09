import React from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, render as rtlRender, screen, waitFor, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

const mockQuota = vi.hoisted(() => ({
  value: { ai_conversations: { used: 2, limit: 10, reset_at: null } } as {
    ai_conversations: { used: number; limit: number; reset_at: string | null }
  },
}))

const mockProjectState = vi.hoisted(() => ({
  currentProject: { id: 'project-1', name: '外卖小哥看见倒计时' } as { id: string; name: string } | null,
  refreshProjects: vi.fn(async () => {}),
  refreshProject: vi.fn(async (_projectId: string) => {}),
  triggerFileTreeRefresh: vi.fn(),
  triggerEditorRefresh: vi.fn(),
}))

const mockStartStream = vi.hoisted(() => vi.fn())
const mockScrollToBottom = vi.hoisted(() => vi.fn())
const mockRollback = vi.hoisted(() => vi.fn())
const mockGetProgress = vi.hoisted(() => vi.fn(async () => [] as unknown[]))

let testQueryClient: QueryClient
const render = (ui: React.ReactElement) => {
  testQueryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return rtlRender(<QueryClientProvider client={testQueryClient}>{ui}</QueryClientProvider>)
}

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
  'chat:panel.quotaExceededTitle': '今天的免费 AI 消息用完了',
  'chat:panel.quotaExceededHint': '北京时间明天 00:00 恢复。想接着写，可以开通 Pro，AI 消息不限条数。',
  'chat:panel.notCharged': '这一轮没有改动文件，不计入今日 AI 消息。',
  'chat:panel.notChargedError': '这次出错不计入今日 AI 消息。',
  'chat:panel.notChargedStopped': '已停止，这一轮还没有写出内容，不计入今日 AI 消息。',
  'chat:panel.resend': '重新发送',
  'chat:nextStep.novel.message': '按大纲写第一章正文',
  'chat:input.mode.switchedFast': '已切换到快速模式：更快出结果（可能更简略）',
  'chat:input.mode.switchedQuality': '已切换到高质量模式：更稳更全面（可能更慢）',
  'dashboard:billing.ctaUpgradePro': '升级专业版',
  'home:pricingTeaser.viewPricing': '查看套餐权益',
  'chat:input.placeholderQuotaExhausted': '今天的 {{limit}} 条 AI 消息用完了，北京时间明天 00:00 恢复。可以先把想法写下来，到时再发。',
  'chat:input.placeholderWhileProcessing': 'AI 正在生成，你可以先输入，结束后再发送…',
  'chat:tool.undo_edit': '撤销这次修改',
  'chat:actions.undo': '撤销',
  'common:cancel': '取消',
}

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: { defaultValue?: string; [name: string]: unknown } | string) => {
      const template = chatPanelTranslations[key] ?? (typeof options === 'string' ? options : options?.defaultValue ?? key)
      if (!options || typeof options === 'string') return template
      return template.replace(/\{\{(\w+)\}\}/g, (_m, name: string) => String(options[name] ?? ''))
    },
  }),
}))

vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({
    currentProjectId: 'project-1',
    currentProject: mockProjectState.currentProject,
    refreshProjects: mockProjectState.refreshProjects,
    refreshProject: mockProjectState.refreshProject,
    selectedItem: null,
    triggerFileTreeRefresh: mockProjectState.triggerFileTreeRefresh,
    triggerEditorRefresh: mockProjectState.triggerEditorRefresh,
    setSelectedItem: vi.fn(),
    appendFileContent: vi.fn(),
    finishFileStreaming: vi.fn(),
    startFileStreaming: vi.fn(),
    streamingFileId: null,
    enterDiffReview: vi.fn(),
  }),
}))

vi.mock('../../lib/subscriptionApi', () => ({
  subscriptionApi: {
    getQuota: vi.fn(async () => mockQuota.value),
    getStatus: vi.fn(async () => ({ tier: 'free' })),
  },
  subscriptionQueryKeys: {
    status: () => ['subscription-status', 'test-user'],
    quota: () => ['subscription-quota', 'test-user'],
    quotaLite: () => ['quota', 'test-user'],
  },
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

const mockGetStreamCallbacks = vi.fn((_deps: {
  getLatestUserRequest?: () => string | null | undefined
  onProjectRenamed?: (projectId: string) => void
}) => streamCallbacks)

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
    getStreamCallbacks: mockGetStreamCallbacks,
    clearIdleTimer: vi.fn(),
  }),
}))

vi.mock('../../hooks/useAgentStream', () => ({
  useAgentStream: (_projectId: string, options?: Record<string, unknown>) => {
    capturedUseAgentStream.options = options ?? null
    return ({
    state: {},
    startStream: mockStartStream,
    cancel: vi.fn(),
    stop: vi.fn(),
    isStopping: false,
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
  fileVersionApi: { rollback: mockRollback },
  versionApi: {},
  projectApi: { getProgress: mockGetProgress },
}))

type MockMessageInputProps = {
  onGenerationModeChange?: (mode: 'fast' | 'quality') => void
  onSend: (message: string, selectedSkillIds: string[]) => void | Promise<void>
  disabled?: boolean
  sendDisabled?: boolean
  placeholder?: string
  onCancel?: () => void
}

const mockMessageList = vi.fn((_props: unknown) => <div data-testid="mock-message-list" />)
const mockMessageInput = vi.fn((props: MockMessageInputProps) => (
  <div data-testid="mock-message-input">
    <button
      type="button"
      data-testid="mock-generation-mode-toggle"
      onClick={() => props.onGenerationModeChange?.('fast')}
    >
      toggle-generation-mode
    </button>
    {props.onCancel && (
      <button type="button" data-testid="mock-stop-button" onClick={props.onCancel}>
        stop
      </button>
    )}
    {/* Mirrors MessageInput: drafting follows `disabled`, sending follows `sendDisabled`. */}
    <textarea data-testid="mock-input-textarea" disabled={props.disabled} placeholder={props.placeholder} />
  </div>
))
const lastMessageInputProps = () => mockMessageInput.mock.calls.at(-1)?.[0] as MockMessageInputProps

vi.mock('../MessageList', () => ({
  MessageList: React.forwardRef((props: unknown, ref) => {
    React.useImperativeHandle(ref, () => ({ scrollToBottom: mockScrollToBottom }))
    return mockMessageList(props)
  }),
}))

vi.mock('../MessageInput', () => ({
  MessageInput: (props: MockMessageInputProps) => mockMessageInput(props),
}))

vi.mock('../ToolResultCard', () => ({
  ToolResultCard: () => <div data-testid="mock-tool-result-card" />,
}))

vi.mock('../ProjectStatusDialog', () => ({
  ProjectStatusDialog: () => null,
}))

vi.mock('../subscription/QuotaBadge', () => ({
  QuotaBadge: ({ compact }: { compact?: boolean }) => (
    <div data-testid="mock-quota-badge" data-compact={String(Boolean(compact))} />
  ),
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
    localStorage.removeItem('zenstory_inspiration_project-1')
    localStorage.removeItem('zenstory_next_step_dismissed_project-1')
    mockQuota.value = { ai_conversations: { used: 2, limit: 10, reset_at: null } }
    mockProjectState.currentProject = { id: 'project-1', name: '外卖小哥看见倒计时' }
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

  it('forwards mutation and partial completion metadata to snapshot finalization', async () => {
    render(<ChatPanel />)
    await waitFor(() => expect(capturedUseAgentStream.options).not.toBeNull())

    const options = capturedUseAgentStream.options as {
      onComplete?: (segments: unknown[], applyAction: unknown, meta?: Record<string, unknown>) => void;
    }
    await act(async () => {
      options.onComplete?.([], null, {
        confirmedFileMutation: false,
        partial: true,
      })
    })

    expect(streamCallbacks.onComplete).toHaveBeenCalledWith([], null, {
      confirmedFileMutation: false,
      partial: true,
    })
  })

  it('offers the same upgrade path when the daily cost backstop trips, without revealing a second quota', async () => {
    const originalLocation = window.location
    const assignMock = vi.fn()
    Object.defineProperty(window, 'location', {
      value: { ...originalLocation, assign: assignMock },
      writable: true,
      configurable: true,
    })

    try {
      mockAgentStreamState.errorCode = 'ERR_QUOTA_AI_DAILY_COST_EXCEEDED'
      mockAgentStreamState.error = '今日 AI 额度已用完'
      render(<ChatPanel />)
      // 成本兜底和每日条数用同一个标题与说明；错误码文案本身不再重复显示。
      expect((await screen.findAllByText('今天的免费 AI 消息用完了')).length).toBeGreaterThan(0)
      expect(screen.queryByText('今日 AI 额度已用完')).not.toBeInTheDocument()
      expect(screen.getAllByText(/北京时间明天 00:00 恢复/).length).toBeGreaterThan(0)

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

  it('opens quota upgrade modal when quota error code is returned', async () => {
    mockAgentStreamState.errorCode = 'ERR_QUOTA_AI_CONVERSATIONS_EXCEEDED'
    mockAgentStreamState.error = 'quota exceeded'

    render(<ChatPanel />)

    await waitFor(() => {
      expect(screen.getByText('今天的免费 AI 消息用完了')).toBeInTheDocument()
    })
    // 同一件事不在一张卡片里说三遍：额度错误不再渲染原始 {error}。
    expect(screen.queryByText('quota exceeded')).not.toBeInTheDocument()
  })

  it('tells the author a round was not charged only after the backend confirms the refund', async () => {
    vi.mocked(getRecentMessages).mockResolvedValueOnce([{
      id: 'user-1', session_id: 'session-1', role: 'user', content: '写第五章',
      created_at: '2026-10-05T10:00:00Z',
    }] as never)
    render(<ChatPanel />)
    await waitFor(() => expect(screen.getByTestId('mock-message-list')).toBeInTheDocument())
    expect(screen.queryByTestId('chat-quota-refund-note')).not.toBeInTheDocument()

    const options = () => capturedUseAgentStream.options as {
      onQuotaRefunded: (kind: 'no_progress' | 'error') => void
      onStart: () => void
    }
    act(() => options().onQuotaRefunded('no_progress'))
    expect(await screen.findByText('这一轮没有改动文件，不计入今日 AI 消息。')).toBeInTheDocument()

    act(() => options().onQuotaRefunded('error'))
    expect(await screen.findByText('这次出错不计入今日 AI 消息。')).toBeInTheDocument()

    // 下一轮开始就清掉，不把上一轮的说明带过去。
    act(() => options().onStart())
    await waitFor(() => expect(screen.queryByTestId('chat-quota-refund-note')).not.toBeInTheDocument())
  })

  it('after a stop that wrote nothing, says it was not charged and offers to send the same request again', async () => {
    vi.mocked(getRecentMessages).mockResolvedValueOnce([{
      id: 'user-1', session_id: 'session-1', role: 'user', content: '写第五章',
      created_at: '2026-10-05T10:00:00Z',
    }] as never)
    render(<ChatPanel />)
    await waitFor(() => expect(screen.getByTestId('mock-message-list')).toBeInTheDocument())

    const options = () => capturedUseAgentStream.options as {
      onQuotaRefunded: (kind: 'no_progress' | 'error' | 'stopped') => void
    }
    act(() => options().onQuotaRefunded('stopped'))
    expect(await screen.findByText(/已停止，这一轮还没有写出内容/)).toBeInTheDocument()

    fireEvent.click(screen.getByTestId('chat-resend-after-stop'))
    await waitFor(() => expect(mockStartStream).toHaveBeenCalledTimes(1))
    const resent = mockStartStream.mock.calls[0][0] as { message: string; metadata: Record<string, unknown> }
    expect(resent.message).toBe('写第五章')
    expect(resent.metadata.entry).toBe('resend_after_stop')
  })

  it.each(['no_progress', 'error'] as const)('offers no resend for a %s refund', async (kind) => {
    vi.mocked(getRecentMessages).mockResolvedValueOnce([{
      id: 'user-1', session_id: 'session-1', role: 'user', content: '写第五章',
      created_at: '2026-10-05T10:00:00Z',
    }] as never)
    render(<ChatPanel />)
    await waitFor(() => expect(screen.getByTestId('mock-message-list')).toBeInTheDocument())
    const options = () => capturedUseAgentStream.options as {
      onQuotaRefunded: (kind: 'no_progress' | 'error' | 'stopped') => void
    }
    act(() => options().onQuotaRefunded(kind))
    expect(await screen.findByTestId('chat-quota-refund-note')).toBeInTheDocument()
    expect(screen.queryByTestId('chat-resend-after-stop')).not.toBeInTheDocument()
  })

  it('offers to write chapter 1 once the framework exists, sending it in one press', async () => {
    mockGetProgress.mockResolvedValue([
      { project_id: 'project-1', written_units: 0, word_count: 0, framework_ready: true },
    ])
    render(<ChatPanel />)

    fireEvent.click(await screen.findByTestId('next-step-start'))

    await waitFor(() => expect(mockStartStream).toHaveBeenCalledTimes(1))
    const request = mockStartStream.mock.calls[0][0] as { message: string; metadata: Record<string, unknown> }
    expect(request.message).toBe('按大纲写第一章正文')
    expect(request.metadata.entry).toBe('next_step')
    expect(screen.queryByTestId('next-step-card')).not.toBeInTheDocument()
    mockGetProgress.mockResolvedValue([])
  })

  it('keeps the chapter-1 offer hidden after the author dismisses it', async () => {
    mockGetProgress.mockResolvedValue([
      { project_id: 'project-1', written_units: 0, word_count: 0, framework_ready: true },
    ])
    render(<ChatPanel />)

    await screen.findByTestId('next-step-card')
    fireEvent.click(screen.getByText('chat:nextStep.dismiss'))

    expect(screen.queryByTestId('next-step-card')).not.toBeInTheDocument()
    expect(mockStartStream).not.toHaveBeenCalled()
    mockGetProgress.mockResolvedValue([])
  })

  it('remembers 先不用 for the project after a refresh or re-entry', async () => {
    mockGetProgress.mockResolvedValue([
      { project_id: 'project-1', written_units: 0, word_count: 0, framework_ready: true },
    ])
    const first = render(<ChatPanel />)
    await screen.findByTestId('next-step-card')
    fireEvent.click(screen.getByText('chat:nextStep.dismiss'))
    first.unmount()

    // A fresh mount is what a page reload or leaving and coming back does.
    const second = render(<ChatPanel />)
    await waitFor(() => expect(mockGetProgress).toHaveBeenCalledTimes(2))
    await act(async () => {})
    expect(screen.queryByTestId('next-step-card')).not.toBeInTheDocument()
    second.unmount()

    // Control: the same mount without the remembered 先不用 shows the card again.
    localStorage.removeItem('zenstory_next_step_dismissed_project-1')
    render(<ChatPanel />)
    expect(await screen.findByTestId('next-step-card')).toBeInTheDocument()
    mockGetProgress.mockResolvedValue([])
  })

  it('drops the suggestion chips that repeat the chapter-1 card, keeping the others', async () => {
    vi.mocked(fetchSuggestions).mockResolvedValueOnce([
      '写第一章，老周还剩七天',
      '先补陈越的角色卡',
      '调整大纲的节奏',
    ])
    mockGetProgress.mockResolvedValue([
      { project_id: 'project-1', written_units: 0, word_count: 0, framework_ready: true },
    ])
    render(<ChatPanel />)
    await screen.findByTestId('next-step-card')

    // The chips MessageInput would show: what ChatPanel passes, minus what it asks to hide.
    const chips = () => {
      const { aiSuggestions, hideSuggestion } = lastMessageInputProps() as unknown as {
        aiSuggestions: string[]
        hideSuggestion?: (suggestion: string) => boolean
      }
      return hideSuggestion ? aiSuggestions.filter((s) => !hideSuggestion(s)) : aiSuggestions
    }
    await waitFor(() => expect(chips()).toEqual(['先补陈越的角色卡', '调整大纲的节奏']))
    // The same filter covers the fallback pool, whose 「开始创作第一章」 repeats the card too.
    expect((lastMessageInputProps() as unknown as { hideSuggestion?: (s: string) => boolean }).hideSuggestion?.('开始创作第一章')).toBe(true)

    // Once the author says 先不用, the card is gone and the chip is the way back in.
    fireEvent.click(screen.getByText('chat:nextStep.dismiss'))
    await waitFor(() => expect(chips()).toEqual(['写第一章，老周还剩七天', '先补陈越的角色卡', '调整大纲的节奏']))
    mockGetProgress.mockResolvedValue([])
  })

  describe('after the author clicks 停止生成', () => {
    type StopOptions = {
      onStart: () => void
      onSessionStarted: (sessionId: string) => void
      onToolResult: (toolName: string, status: string, result?: Record<string, unknown>, error?: string) => void
    }
    const stopOptions = () => capturedUseAgentStream.options as StopOptions

    const runRoundThenStop = async (round: (options: StopOptions) => void) => {
      vi.mocked(getRecentMessages).mockResolvedValueOnce([{
        id: 'user-1', session_id: 'session-1', role: 'user', content: '写第五章',
        created_at: '2026-10-05T10:00:00Z',
      }] as never)
      mockAgentStreamState.isStreaming = true
      render(<ChatPanel />)
      await waitFor(() => expect(screen.getByTestId('mock-message-list')).toBeInTheDocument())
      await waitFor(() => expect(testQueryClient.getQueryData(['subscription-quota', 'test-user'])).toBeDefined())
      act(() => {
        stopOptions().onStart()
        round(stopOptions())
      })
      // The stop ends streaming; the note renders once the panel is idle.
      mockAgentStreamState.isStreaming = false
      fireEvent.click(screen.getByTestId('mock-stop-button'))
      return screen.findByTestId('chat-user-stop-note')
    }

    it('says what was written is saved and that the message counts, when both are true', async () => {
      const note = await runRoundThenStop((options) => {
        options.onSessionStarted('session-stop')
        options.onToolResult('edit_file', 'success', { data: { id: 'file-1' } })
      })
      expect(note).toHaveTextContent('已停止 · 已写入的内容已保存 · 本条计入今日 AI 消息')
    })

    it('does not claim anything was written when no write succeeded', async () => {
      const note = await runRoundThenStop((options) => {
        options.onSessionStarted('session-stop')
        options.onToolResult('query_files', 'success', {})
        options.onToolResult('edit_file', 'error', {})
        options.onToolResult('parallel_execute', 'success', {
          tasks: [{ type: 'query_files', status: 'completed' }],
        })
      })
      expect(note).toHaveTextContent('已停止 · 本条计入今日 AI 消息')
      expect(note).not.toHaveTextContent('已写入')
    })

    it('does not mention today\'s count before the server started the round', async () => {
      const early = await runRoundThenStop(() => {})
      expect(early).toHaveTextContent(/^已停止$/)
    })

    it('leaves out the daily count for unlimited plans', async () => {
      mockQuota.value = { ai_conversations: { used: 12, limit: -1, reset_at: null } }
      const note = await runRoundThenStop((options) => {
        options.onSessionStarted('session-stop')
        options.onToolResult('parallel_execute', 'success', {
          tasks: [{ type: 'write_chapter', status: 'completed' }],
        })
      })
      expect(note).toHaveTextContent('已停止 · 已写入的内容已保存')
      expect(note).not.toHaveTextContent('今日 AI 消息')
    })

    it('clears the note when the next round starts', async () => {
      await runRoundThenStop((options) => options.onSessionStarted('session-stop'))
      act(() => stopOptions().onStart())
      await waitFor(() => expect(screen.queryByTestId('chat-user-stop-note')).not.toBeInTheDocument())
    })

    it('shows only the refund note when the server refunds the stop, never "计入" next to "不计入"', async () => {
      await runRoundThenStop((options) => options.onSessionStarted('session-stop'))
      act(() => (capturedUseAgentStream.options as {
        onQuotaRefunded: (kind: 'no_progress' | 'error' | 'stopped') => void
      }).onQuotaRefunded('stopped'))
      expect(await screen.findByText(/已停止，这一轮还没有写出内容/)).toBeInTheDocument()
      expect(screen.queryByTestId('chat-user-stop-note')).not.toBeInTheDocument()
      expect(screen.queryByText(/本条计入/)).not.toBeInTheDocument()
    })
  })

  it('never pairs a refund note with the used-up card, which would hint at a second limit', async () => {
    mockAgentStreamState.errorCode = 'ERR_QUOTA_AI_DAILY_COST_EXCEEDED'
    mockAgentStreamState.error = 'cost backstop'
    render(<ChatPanel />)
    expect((await screen.findAllByText('今天的免费 AI 消息用完了')).length).toBeGreaterThan(0)

    const options = () => capturedUseAgentStream.options as {
      onQuotaRefunded: (kind: 'no_progress' | 'error') => void
    }
    act(() => options().onQuotaRefunded('error'))
    expect(screen.queryByTestId('chat-quota-refund-note')).not.toBeInTheDocument()
    expect(screen.queryByText('这次出错不计入今日 AI 消息。')).not.toBeInTheDocument()
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

type CapturedStreamOptions = {
  onComplete: (segments: unknown[], action: unknown, meta?: Record<string, unknown>) => Promise<void>
  onSessionStarted: (sessionId: string) => void
  onError: (message?: string, code?: string, retryable?: boolean) => void
  onQuotaRefunded: (kind: 'no_progress' | 'error') => void
}
const streamOptions = () => capturedUseAgentStream.options as unknown as CapturedStreamOptions
const lastMessageListProps = () => mockMessageList.mock.calls.at(-1)?.[0] as {
  messages?: Array<{ role: string; content: string }>
  onUndo?: (target: { fileId: string; beforeVersionNumber: number; expectedAfterUpdatedAt: string }) => void
}

describe('ChatPanel new-author flow', () => {
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
    localStorage.removeItem('zenstory_inspiration_project-1')
    mockQuota.value = { ai_conversations: { used: 2, limit: 10, reset_at: null } }
    mockProjectState.currentProject = { id: 'project-1', name: '外卖小哥看见倒计时' }
  })

  it('sends the dashboard idea exactly as written and tags the request as the dashboard entry', async () => {
    const idea = '一个外卖小哥每次送单都能看见客户头顶的倒计时'
    localStorage.setItem('zenstory_inspiration_project-1', JSON.stringify({
      content: idea,
      projectType: 'short',
      timestamp: Date.now(),
    }))

    render(<ChatPanel />)

    await waitFor(() => expect(mockStartStream).toHaveBeenCalledTimes(1), { timeout: 3000 })
    const request = mockStartStream.mock.calls[0][0] as { message: string; metadata: Record<string, unknown> }
    expect(request.message).toBe(idea)
    expect(request.metadata.entry).toBe('dashboard_idea')
    expect(localStorage.getItem('zenstory_inspiration_project-1')).toBeNull()

    await waitFor(() => {
      expect(lastMessageListProps().messages?.map((m) => [m.role, m.content])).toEqual([['user', idea]])
    })
    // The automatic snapshot after this round is described with the author's words.
    expect(mockGetStreamCallbacks.mock.calls.at(-1)?.[0].getLatestUserRequest?.()).toBe(idea)
  })

  it('does not tag ordinary sends from the input box', async () => {
    render(<ChatPanel />)
    await waitFor(() => expect(screen.getByTestId('mock-message-input')).toBeInTheDocument())

    await act(async () => { await lastMessageInputProps().onSend('写第二章', []) })

    const request = mockStartStream.mock.calls[0][0] as { message: string; metadata: Record<string, unknown> }
    expect(request.message).toBe('写第二章')
    expect(request.metadata).not.toHaveProperty('entry')
  })

  it('jumps back to the latest message after sending, even when the author had scrolled up', async () => {
    vi.mocked(getRecentMessages).mockResolvedValueOnce([
      { id: 'u-1', session_id: 'session-1', role: 'user', content: '写第一章', created_at: '2026-10-08T10:00:00Z' },
      { id: 'a-1', session_id: 'session-1', role: 'assistant', content: '第一章写好了', created_at: '2026-10-08T10:01:00Z' },
    ] as never)
    render(<ChatPanel />)
    await waitFor(() => expect(lastMessageListProps()?.messages).toHaveLength(2))

    // The author scrolls up to re-read the first chapter.
    const scroller = screen.getByTestId('message-list')
    Object.defineProperty(scroller, 'scrollHeight', { configurable: true, value: 2000 })
    Object.defineProperty(scroller, 'clientHeight', { configurable: true, value: 400 })
    Object.defineProperty(scroller, 'scrollTop', { configurable: true, writable: true, value: 0 })
    fireEvent.scroll(scroller)
    mockScrollToBottom.mockClear()

    await act(async () => { await lastMessageInputProps().onSend('继续写第二章', []) })

    await waitFor(() => expect(mockScrollToBottom).toHaveBeenCalledWith(false))
    expect(screen.queryByRole('button', { name: 'chat:panel.jumpToLatest' })).not.toBeInTheDocument()
  })

  it('keeps drafting open but blocks sending once today\'s AI messages are used up', async () => {
    mockQuota.value = { ai_conversations: { used: 10, limit: 10, reset_at: null } }
    render(<ChatPanel />)

    // sendDisabled is also true while history loads, so wait on the quota placeholder itself.
    const placeholder = '今天的 10 条 AI 消息用完了，北京时间明天 00:00 恢复。可以先把想法写下来，到时再发。'
    await waitFor(() => expect(lastMessageInputProps().placeholder).toBe(placeholder))
    expect(lastMessageInputProps().sendDisabled).toBe(true)
    const textarea = screen.getByTestId('mock-input-textarea')
    expect(textarea).not.toBeDisabled()
    expect(textarea).toHaveAttribute('placeholder', placeholder)
  })

  it.each([
    ['quota remains', { used: 9, limit: 10 }],
    ['Pro (limit -1)', { used: 999, limit: -1 }],
  ])('lets the author send when %s', async (_label, usage) => {
    mockQuota.value = { ai_conversations: { ...usage, reset_at: null } }
    render(<ChatPanel />)
    await waitFor(() => expect(testQueryClient.getQueryData(['subscription-quota', 'test-user'])).toBeDefined())
    await act(async () => {})

    expect(lastMessageInputProps().sendDisabled).toBe(false)
    expect(lastMessageInputProps().placeholder).toBeUndefined()
  })

  it('refreshes the quota pill when the server charges, finishes, fails or refunds a round', async () => {
    render(<ChatPanel />)
    await waitFor(() => expect(capturedUseAgentStream.options).not.toBeNull())
    const invalidate = vi.spyOn(testQueryClient, 'invalidateQueries')
    const expectQuotaInvalidated = () => {
      expect(invalidate).toHaveBeenCalledWith({ queryKey: ['subscription-quota', 'test-user'] })
      expect(invalidate).toHaveBeenCalledWith({ queryKey: ['quota', 'test-user'] })
      invalidate.mockClear()
    }

    act(() => streamOptions().onSessionStarted('session-9'))
    expectQuotaInvalidated()

    await act(async () => {
      await streamOptions().onComplete([{ type: 'content', id: 'c', content: '写好了' }], null)
    })
    expectQuotaInvalidated()

    act(() => streamOptions().onError('boom', 'ERR_INTERNAL', true))
    expectQuotaInvalidated()

    act(() => streamOptions().onQuotaRefunded('error'))
    expectQuotaInvalidated()
  })

  it('keeps the header on one line in a narrow right panel with a compact quota pill', async () => {
    render(<div style={{ width: 300 }}><ChatPanel /></div>)
    await waitFor(() => expect(screen.getByTestId('mock-quota-badge')).toBeInTheDocument())

    const titleGroup = screen.getByTestId('chat-panel-header-title')
    expect(titleGroup).toHaveClass('min-w-0')
    expect(titleGroup.firstElementChild).toHaveClass('shrink-0', 'whitespace-nowrap')
    expect(screen.getByTestId('chat-panel-header-actions')).toHaveClass('min-w-0')
    expect(screen.getByTestId('mock-quota-badge')).toHaveAttribute('data-compact', 'true')
  })

  it('asks for confirmation in an in-app dialog before undoing an AI edit', async () => {
    const nativeConfirm = vi.fn(() => true)
    vi.stubGlobal('confirm', nativeConfirm)
    mockRollback.mockResolvedValue({ snapshot_created: true })
    vi.mocked(getRecentMessages).mockResolvedValueOnce([
      { id: 'a-1', session_id: 'session-1', role: 'assistant', content: '改好了', created_at: '2026-10-08T10:01:00Z' },
    ] as never)
    render(<ChatPanel />)
    await waitFor(() => expect(lastMessageListProps()?.onUndo).toBeTypeOf('function'))
    const target = { fileId: 'file-1', beforeVersionNumber: 3, expectedAfterUpdatedAt: '2026-10-08T10:01:00Z' }

    act(() => lastMessageListProps().onUndo?.(target))
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('撤销这次 AI 修改？正文会换回修改前的内容，已有的历史版本都会保留。')
    expect(nativeConfirm).not.toHaveBeenCalled()

    // Cancelling leaves the text alone.
    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(mockRollback).not.toHaveBeenCalled()

    act(() => lastMessageListProps().onUndo?.(target))
    await screen.findByRole('dialog')
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '撤销' }))
    })
    expect(mockRollback).toHaveBeenCalledWith('file-1', 3, '2026-10-08T10:01:00Z')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(mockProjectState.triggerEditorRefresh).toHaveBeenCalledWith('file-1')
    vi.unstubAllGlobals()
  })

  it('does not reload the project list when a round completes, even under a default name', async () => {
    // refreshProjects flips ProjectContext.loading, and ProjectEditor then swaps
    // the whole workspace (this panel included) for a page loader.
    mockProjectState.currentProject = { id: 'project-1', name: '我的短篇' }
    render(<ChatPanel />)
    await waitFor(() => expect(capturedUseAgentStream.options).not.toBeNull())

    await act(async () => {
      await streamOptions().onComplete([{ type: 'content', id: 'c', content: '大纲好了' }], null)
    })

    expect(mockProjectState.refreshProjects).not.toHaveBeenCalled()
    expect(mockProjectState.refreshProject).not.toHaveBeenCalled()
  })

  it('re-reads only the renamed project when update_project reports a rename', async () => {
    mockProjectState.currentProject = { id: 'project-1', name: '我的短篇' }
    render(<ChatPanel />)
    await waitFor(() => expect(capturedUseAgentStream.options).not.toBeNull())

    const deps = mockGetStreamCallbacks.mock.calls.at(-1)?.[0]
    expect(deps?.onProjectRenamed).toBeTypeOf('function')
    act(() => deps?.onProjectRenamed?.('project-1'))

    expect(mockProjectState.refreshProject).toHaveBeenCalledTimes(1)
    expect(mockProjectState.refreshProject).toHaveBeenCalledWith('project-1')
    expect(mockProjectState.refreshProjects).not.toHaveBeenCalled()
  })
})
