/**
 * Regression tests: 项目切换时旧项目的流式渲染残留必须被清空。
 *
 * 旧流被 useAgentStream 的项目切换 effect 中止后，onComplete 永远不会执行，
 * 因此 streamRenderItems / matchedSkills 只能由 ChatPanel 的项目切换 reset
 * effect 主动清理，否则会被追加渲染进新项目的会话中。
 *
 * 本文件使用真实的 useChatStreaming（不 mock），只 mock useAgentStream 以便
 * 直接驱动其回调向真实流式状态里注入渲染项。
 */
import React from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, render as rtlRender, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

vi.mock('../../contexts/AuthContext', () => ({ useAuth: () => ({ user: { id: 'test-user' } }) }))
vi.mock('../../lib/firstWorkApi', async (importOriginal) => ({ ...await importOriginal<typeof import('../../lib/firstWorkApi')>(), firstWorkApi: {
  get: vi.fn(async () => ({ status: 'ineligible', project_id: null, static_guidance: false })),
} }))

// ChatPanel reads the quota query (shared with QuotaBadge), so it needs a QueryClient.
const render = (ui: React.ReactElement) =>
  rtlRender(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      {ui}
    </QueryClientProvider>,
  )

// ChatPanel 被 React.memo 包裹且无 props，真实应用里靠 ProjectContext 变化触发
// 重渲染；这里用可通知的外部 store 模拟 context 更新，绕过 memo 的 props 比较。
const projectStore = vi.hoisted(() => {
  const listeners = new Set<() => void>()
  const store = {
    currentProjectId: 'project-1',
    setProjectId(id: string) {
      store.currentProjectId = id
      listeners.forEach((listener) => listener())
    },
    subscribe(listener: () => void) {
      listeners.add(listener)
      return () => {
        listeners.delete(listener)
      }
    },
  }
  return store
})

const mockAgentStreamState = vi.hoisted(() => ({
  isStreaming: false,
  isThinking: false,
  error: null as string | null,
  errorCode: null as string | null,
  retryable: false,
}))

const capturedUseAgentStream = vi.hoisted(() => ({
  options: null as Record<string, unknown> | null,
}))

const agentStreamReset = vi.hoisted(() => vi.fn())
const agentStartStream = vi.hoisted(() => vi.fn())

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: { defaultValue?: string } | string) =>
      typeof options === 'string' ? options : options?.defaultValue ?? key,
  }),
}))

vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => {
    const currentProjectId = React.useSyncExternalStore(
      (listener: () => void) => projectStore.subscribe(listener),
      () => projectStore.currentProjectId
    )
    return {
      currentProjectId,
      selectedItem: null,
      triggerFileTreeRefresh: vi.fn(),
      triggerEditorRefresh: vi.fn(),
      setSelectedItem: vi.fn(),
      appendFileContent: vi.fn(),
      finishFileStreaming: vi.fn(),
      startFileStreaming: vi.fn(),
      streamingFileId: null,
      enterDiffReview: vi.fn(),
    }
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

vi.mock('../../hooks/useAgentStream', () => ({
  useAgentStream: (_projectId: string, options?: Record<string, unknown>) => {
    capturedUseAgentStream.options = options ?? null
    return {
      state: { conflicts: [], retryable: mockAgentStreamState.retryable },
      startStream: agentStartStream,
      cancel: vi.fn(),
      reset: agentStreamReset,
      isStreaming: mockAgentStreamState.isStreaming,
      isThinking: mockAgentStreamState.isThinking,
      thinkingContent: '',
      conflicts: [],
      error: mockAgentStreamState.error,
      errorCode: mockAgentStreamState.errorCode,
      retryable: mockAgentStreamState.retryable,
      sessionId: null,
      sendSteeringMessage: vi.fn(),
    }
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

vi.mock('../../lib/subscriptionApi', () => ({
  subscriptionApi: {
    getQuota: vi.fn(async () => ({ ai_conversations: { used: 0, limit: 10, reset_at: null } })),
    getStatus: vi.fn(async () => ({ tier: 'free' })),
  },
  subscriptionQueryKeys: {
    status: () => ['subscription-status', 'test-user'],
    quota: () => ['subscription-quota', 'test-user'],
    quotaLite: () => ['quota', 'test-user'],
  },
}))

vi.mock('../../lib/api', () => ({
  fileVersionApi: {},
  versionApi: {},
  projectApi: { getProgress: vi.fn(async () => []) },
}))

const mockMessageList = vi.fn(() => <div data-testid="mock-message-list" />)
const mockMessageInput = vi.fn(() => <div data-testid="mock-message-input" />)

vi.mock('../MessageList', () => ({
  MessageList: React.forwardRef((props: unknown, _ref) => mockMessageList(props)),
}))

vi.mock('../MessageInput', () => ({
  MessageInput: (props: unknown) => mockMessageInput(props),
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

/** An earlier exchange in the chat: opening it requests suggestions (a work's empty first chat shows the starter cards instead). */
function withEarlierTurn() {
  vi.mocked(getRecentMessages).mockResolvedValueOnce([{
    id: 'msg-earlier', session_id: 'session-1', role: 'assistant', content: '上一轮的回复',
    tool_calls: null, created_at: '2026-10-05T10:00:00Z', metadata: null,
  }] as never)
}

import { fetchSuggestions } from '../../lib/agentApi'

type StreamCallbackOptions = {
  onStart?: () => void
  onComplete?: (segments: unknown[], action: unknown) => Promise<void>
  onThinking?: (message: string) => void
  onSkillsMatched?: (
    skills: Array<{ id: string; name: string; trigger: string; confidence: number }>
  ) => void
  onQuotaRefunded?: (kind: 'no_progress' | 'error' | 'stopped') => void
}

const lastMessageListProps = () => {
  const calls = mockMessageList.mock.calls as unknown as Array<
    [{ streamRenderItems?: Array<{ content?: string }>; messages?: Array<{ content: string }> }]
  >
  return calls[calls.length - 1]?.[0]
}

const lastMessageInputProps = () => {
  const calls = mockMessageInput.mock.calls as unknown as Array<
    [{ aiSuggestions?: string[]; suggestionDisplayState?: string; matchedSkills?: Array<{ name: string }>; disabled?: boolean; sendDisabled?: boolean; onSend?: (message: string, skillIds?: string[]) => void }]
  >
  return calls[calls.length - 1]?.[0]
}

describe('ChatPanel project switch stream cleanup', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.removeItem('zenstory_suggestions_cache_project-1')
    localStorage.removeItem('zenstory_suggestions_cache_project-2')
    capturedUseAgentStream.options = null
    projectStore.currentProjectId = 'project-1'
    mockAgentStreamState.isStreaming = false
    mockAgentStreamState.isThinking = false
    mockAgentStreamState.error = null
    mockAgentStreamState.errorCode = null
    mockAgentStreamState.retryable = false
  })

  it('clears leftover streamRenderItems and matchedSkills when switching projects', async () => {
    // 项目 A：流式进行中，产生渲染残留
    mockAgentStreamState.isStreaming = true
    render(<ChatPanel />)

    await waitFor(() => {
      expect(capturedUseAgentStream.options).not.toBeNull()
    })

    const options = capturedUseAgentStream.options as StreamCallbackOptions
    act(() => {
      options.onThinking?.('旧项目残留内容')
      options.onSkillsMatched?.([
        { id: 'skill-1', name: '写作技能', trigger: '续写', confidence: 0.9 },
      ])
    })

    await waitFor(() => {
      const listProps = lastMessageListProps()
      expect(listProps?.streamRenderItems).toHaveLength(1)
      expect(listProps?.streamRenderItems?.[0]?.content).toBe('旧项目残留内容')
    })
    expect(lastMessageInputProps()?.matchedSkills).toHaveLength(1)

    // 切换到项目 B：旧流被中止（onComplete 不会执行），新项目加载自己的历史
    vi.mocked(getRecentMessages).mockResolvedValueOnce([
      {
        id: 'msg-b1',
        session_id: 'session-b',
        role: 'assistant',
        content: '项目B的历史消息',
        tool_calls: null,
        created_at: '2026-03-01T12:00:00Z',
        metadata: null,
      },
    ] as never)
    mockAgentStreamState.isStreaming = false
    act(() => {
      projectStore.setProjectId('project-2')
    })

    await waitFor(() => {
      const listProps = lastMessageListProps()
      expect(listProps?.messages?.[0]?.content).toBe('项目B的历史消息')
    })

    // 旧项目的流式残留不能渲染进新项目会话
    const listProps = lastMessageListProps()
    expect(listProps?.streamRenderItems).toHaveLength(0)
    expect(lastMessageInputProps()?.matchedSkills).toHaveLength(0)
    // conflicts 等 useAgentStream 内部状态通过 reset() 一并复位
    expect(agentStreamReset).toHaveBeenCalled()
  })

  it('keeps history loading disabled until the newest project request finishes', async () => {
    let resolveA!: (value: never[]) => void
    let resolveB!: (value: never[]) => void
    vi.mocked(getRecentMessages)
      .mockImplementationOnce(() => new Promise((resolve) => { resolveA = resolve }))
      .mockImplementationOnce(() => new Promise((resolve) => { resolveB = resolve }))
    render(<ChatPanel />)
    await waitFor(() => expect(lastMessageInputProps()?.disabled).toBe(true))

    act(() => projectStore.setProjectId('project-2'))
    await waitFor(() => expect(vi.mocked(getRecentMessages)).toHaveBeenCalledTimes(2))
    await act(async () => resolveA([]))
    expect(lastMessageInputProps()?.disabled).toBe(true)

    await act(async () => resolveB([]))
    await waitFor(() => expect(lastMessageInputProps()?.disabled).toBe(false))
  })

  it('does not let an old project result replace the current empty-ready repeat state', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      for (const id of ['project-1', 'project-2']) {
        localStorage.setItem(`zenstory_suggestions_cache_${id}`, JSON.stringify({ suggestions: [], updatedAt: Date.now() }))
      }
      let finishA!: (value: string[]) => void
      vi.mocked(fetchSuggestions)
        .mockReturnValueOnce(new Promise(resolve => { finishA = resolve }))
        .mockResolvedValueOnce([])
      render(<ChatPanel />)
      await waitFor(() => expect(lastMessageInputProps()?.suggestionDisplayState).toBe('ready'))
      const finishRound = async () => {
        const options = capturedUseAgentStream.options as StreamCallbackOptions
        await act(async () => { options.onStart?.(); await options.onComplete?.([], null) })
        await act(async () => { vi.advanceTimersByTime(1600) })
      }
      await finishRound()
      expect(vi.mocked(fetchSuggestions)).toHaveBeenCalledTimes(1)
      act(() => projectStore.setProjectId('project-2'))
      await waitFor(() => expect(lastMessageInputProps()?.suggestionDisplayState).toBe('ready'))
      await finishRound()
      expect(vi.mocked(fetchSuggestions)).toHaveBeenCalledTimes(2)
      await act(async () => { finishA([]) })
      await finishRound()
      expect(lastMessageInputProps()?.suggestionDisplayState).toBe('ready')
      expect(lastMessageInputProps()?.aiSuggestions).toEqual([])
      expect(vi.mocked(fetchSuggestions).mock.calls.map(args => args[0])).toEqual(['project-1', 'project-2'])
    } finally { vi.useRealTimers() }
  })

  it('keeps the owned failure fallback on a same-key completion without refetching', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      localStorage.setItem('zenstory_suggestions_cache_project-1', JSON.stringify({ suggestions: [], updatedAt: Date.now() }))
      vi.mocked(fetchSuggestions).mockRejectedValueOnce(new Error('synthetic failure'))
      render(<ChatPanel />)
      await waitFor(() => expect(lastMessageInputProps()?.suggestionDisplayState).toBe('ready'))
      const options = capturedUseAgentStream.options as StreamCallbackOptions
      for (let i = 0; i < 2; i++) {
        await act(async () => { options.onStart?.(); await options.onComplete?.([], null) })
        await act(async () => { vi.advanceTimersByTime(1600) })
        expect(lastMessageInputProps()?.suggestionDisplayState).toBe('fallback')
      }
      expect(vi.mocked(fetchSuggestions)).toHaveBeenCalledTimes(1)
    } finally { vi.useRealTimers() }
  })

  it.each([
    ['non-array suggestions', () => JSON.stringify({ suggestions: 'invalid', updatedAt: Date.now() })],
    ['invalid timestamp', () => JSON.stringify({ suggestions: [], updatedAt: 'invalid' })],
    ['invalid JSON', () => '{not valid json'],
  ])('refreshes an unusable %s cache once instead of treating it as empty success', async (_name, cache) => {
    localStorage.setItem('zenstory_suggestions_cache_project-1', cache())
    vi.mocked(fetchSuggestions).mockResolvedValueOnce(['当前任务的建议'])
    withEarlierTurn()
    render(<ChatPanel />)
    await waitFor(() => expect(lastMessageInputProps()?.aiSuggestions).toEqual(['当前任务的建议']))
    expect(lastMessageInputProps()?.suggestionDisplayState).toBe('ready')
    expect(fetchSuggestions).toHaveBeenCalledTimes(1)
    expect(JSON.parse(localStorage.getItem('zenstory_suggestions_cache_project-1')!).suggestions).toEqual(['当前任务的建议'])
  })

  it('discards an expired suggestion cache and writes the new explicit empty result', async () => {
    localStorage.setItem('zenstory_suggestions_cache_project-1', JSON.stringify({
      suggestions: ['过期建议'], updatedAt: Date.now() - 11 * 60 * 1000,
    }))
    const remove = vi.spyOn(localStorage, 'removeItem').mockClear()
    try {
      vi.mocked(fetchSuggestions).mockResolvedValueOnce([])
      withEarlierTurn()
      render(<ChatPanel />)
      await waitFor(() => expect(lastMessageInputProps()?.suggestionDisplayState).toBe('ready'))
      expect(fetchSuggestions).toHaveBeenCalledTimes(1)
      expect(remove).toHaveBeenCalledWith('zenstory_suggestions_cache_project-1')
      expect(lastMessageInputProps()?.aiSuggestions).toEqual([])
      expect(JSON.parse(localStorage.getItem('zenstory_suggestions_cache_project-1')!).suggestions).toEqual([])
    } finally { remove.mockRestore() }
  })

  it('keeps timeout fallback when the delayed request is unavailable', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      let finish!: (value: null) => void
      vi.mocked(fetchSuggestions).mockReturnValueOnce(new Promise(resolve => { finish = resolve }) as never)
      withEarlierTurn()
      render(<ChatPanel />)
      await waitFor(() => expect(fetchSuggestions).toHaveBeenCalledTimes(1))
      await act(async () => { vi.advanceTimersByTime(1600) })
      expect(lastMessageInputProps()?.suggestionDisplayState).toBe('fallback')
      await act(async () => { finish(null) })
      expect(lastMessageInputProps()?.suggestionDisplayState).toBe('fallback')
      expect(lastMessageInputProps()?.aiSuggestions).toEqual([])
      expect(localStorage.getItem('zenstory_suggestions_cache_project-1')).toBeNull()
      expect(fetchSuggestions).toHaveBeenCalledTimes(1)
    } finally { vi.useRealTimers() }
  })

  it.each(['empty', 'existing chips'] as const)('preserves owned %s display state after an unavailable completion', async mode => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      const existing = mode === 'empty' ? [] : ['仍适用的任务建议']
      localStorage.setItem('zenstory_suggestions_cache_project-1', JSON.stringify({ suggestions: existing, updatedAt: Date.now() }))
      vi.mocked(fetchSuggestions).mockResolvedValueOnce(null as never)
      withEarlierTurn()
      render(<ChatPanel />)
      await waitFor(() => expect(lastMessageInputProps()?.suggestionDisplayState).toBe('ready'))
      const options = capturedUseAgentStream.options as StreamCallbackOptions
      for (let i = 0; i < 2; i++) {
        await act(async () => { options.onStart?.(); await options.onComplete?.([], null) })
        await act(async () => { vi.advanceTimersByTime(1600) })
        expect(lastMessageInputProps()?.suggestionDisplayState).toBe(mode === 'empty' ? 'fallback' : 'ready')
        expect(lastMessageInputProps()?.aiSuggestions).toEqual(existing)
      }
      expect(fetchSuggestions).toHaveBeenCalledTimes(1)
    } finally { vi.useRealTimers() }
  })

  it('preserves existing chips when the owned completion rejects', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      localStorage.setItem('zenstory_suggestions_cache_project-1', JSON.stringify({ suggestions: ['已有建议'], updatedAt: Date.now() }))
      vi.mocked(fetchSuggestions).mockRejectedValueOnce(new Error('synthetic refresh failure'))
      withEarlierTurn()
      render(<ChatPanel />)
      await waitFor(() => expect(lastMessageInputProps()?.suggestionDisplayState).toBe('ready'))
      const options = capturedUseAgentStream.options as StreamCallbackOptions
      for (let i = 0; i < 2; i++) {
        await act(async () => { options.onStart?.(); await options.onComplete?.([], null) })
        await act(async () => { vi.advanceTimersByTime(1600) })
        expect(lastMessageInputProps()?.suggestionDisplayState).toBe('ready')
        expect(lastMessageInputProps()?.aiSuggestions).toEqual(['已有建议'])
      }
      expect(fetchSuggestions).toHaveBeenCalledTimes(1)
    } finally { vi.useRealTimers() }
  })

  it('ignores an old project rejection after the current project confirmed an empty result', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      for (const id of ['project-1', 'project-2']) {
        localStorage.setItem(`zenstory_suggestions_cache_${id}`, JSON.stringify({ suggestions: [], updatedAt: Date.now() }))
      }
      let rejectA!: (error: Error) => void
      vi.mocked(fetchSuggestions)
        .mockReturnValueOnce(new Promise((_resolve, reject) => { rejectA = reject }))
        .mockResolvedValueOnce([])
      render(<ChatPanel />)
      await waitFor(() => expect(lastMessageInputProps()?.suggestionDisplayState).toBe('ready'))
      const finishRound = async () => {
        const options = capturedUseAgentStream.options as StreamCallbackOptions
        await act(async () => { options.onStart?.(); await options.onComplete?.([], null) })
        await act(async () => { vi.advanceTimersByTime(1600) })
      }
      await finishRound()
      act(() => projectStore.setProjectId('project-2'))
      await waitFor(() => expect(lastMessageInputProps()?.suggestionDisplayState).toBe('ready'))
      await finishRound()
      await act(async () => { rejectA(new Error('obsolete failure')) })
      await finishRound()
      expect(lastMessageInputProps()?.suggestionDisplayState).toBe('ready')
      expect(lastMessageInputProps()?.aiSuggestions).toEqual([])
      expect(vi.mocked(fetchSuggestions).mock.calls.map(args => args[0])).toEqual(['project-1', 'project-2'])
    } finally { vi.useRealTimers() }
  })

  it('shows a retryable routing failure, retries once, and waits for a real refund event', async () => {
    mockAgentStreamState.error = '这次没能处理你的要求，作品没有改动。可以稍后重试。'
    mockAgentStreamState.errorCode = 'ERR_AGENT_ROUTING_FAILED'
    mockAgentStreamState.retryable = true
    const user = userEvent.setup()
    render(<ChatPanel />)
    await waitFor(() => expect(lastMessageInputProps()?.onSend).toBeTypeOf('function'))

    // The banner shows once history has loaded, which a slow CI runner may not have done yet.
    expect(await screen.findByText('这次没能处理你的要求，作品没有改动。可以稍后重试。')).toBeInTheDocument()
    expect(screen.queryByTestId('chat-quota-refund-note')).not.toBeInTheDocument()

    await act(async () => {
      await lastMessageInputProps()?.onSend?.('same request', ['skill-1'])
    })
    expect(agentStartStream).toHaveBeenCalledTimes(1)
    await user.click(screen.getByRole('button', { name: 'common:retry' }))
    expect(agentStartStream).toHaveBeenCalledTimes(2)
    // Same request; the server reuses the unanswered user message instead of saving another.
    const original = agentStartStream.mock.calls[0]?.[0] as { metadata: Record<string, unknown> }
    expect(agentStartStream.mock.calls[1]?.[0]).toEqual({
      ...original,
      metadata: { ...original.metadata, retry_unanswered: true },
    })
    expect(agentStartStream).toHaveBeenCalledTimes(2)

    act(() => {
      const options = capturedUseAgentStream.options as StreamCallbackOptions
      options.onQuotaRefunded?.('error')
    })
    expect(await screen.findByTestId('chat-quota-refund-note')).toHaveTextContent(
      'chat:panel.notChargedError',
    )
  })

  it('does not show Retry for non-retryable errors', async () => {
    mockAgentStreamState.error = 'permanent failure'
    mockAgentStreamState.retryable = false
    render(<ChatPanel />)
    expect(screen.queryByRole('button', { name: 'common:retry' })).not.toBeInTheDocument()
  })
})
