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
  projectApi: { getNextStep: vi.fn(async () => ({ next_step: null })) },
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

type StreamCallbackOptions = {
  onThinking?: (message: string) => void
  onSkillsMatched?: (
    skills: Array<{ id: string; name: string; trigger: string; confidence: number }>
  ) => void
}

const lastMessageListProps = () => {
  const calls = mockMessageList.mock.calls as unknown as Array<
    [{ streamRenderItems?: Array<{ content?: string }>; messages?: Array<{ content: string }> }]
  >
  return calls[calls.length - 1]?.[0]
}

const lastMessageInputProps = () => {
  const calls = mockMessageInput.mock.calls as unknown as Array<
    [{ matchedSkills?: Array<{ name: string }>; disabled?: boolean; sendDisabled?: boolean; onSend?: (message: string, skillIds?: string[]) => void }]
  >
  return calls[calls.length - 1]?.[0]
}

describe('ChatPanel project switch stream cleanup', () => {
  beforeEach(() => {
    vi.clearAllMocks()
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

  it('shows Retry only for retryable errors and replays the captured request once per click', async () => {
    mockAgentStreamState.error = 'temporary failure'
    mockAgentStreamState.retryable = true
    const user = userEvent.setup()
    render(<ChatPanel />)
    await waitFor(() => expect(lastMessageInputProps()?.onSend).toBeTypeOf('function'))

    await act(async () => {
      await lastMessageInputProps()?.onSend?.('same request', ['skill-1'])
    })
    expect(agentStartStream).toHaveBeenCalledTimes(1)
    await user.click(screen.getByRole('button', { name: 'common:retry' }))
    expect(agentStartStream).toHaveBeenCalledTimes(2)
    expect(agentStartStream.mock.calls[1]?.[0]).toEqual(agentStartStream.mock.calls[0]?.[0])
  })

  it('does not show Retry for non-retryable errors', async () => {
    mockAgentStreamState.error = 'permanent failure'
    mockAgentStreamState.retryable = false
    render(<ChatPanel />)
    expect(screen.queryByRole('button', { name: 'common:retry' })).not.toBeInTheDocument()
  })
})
