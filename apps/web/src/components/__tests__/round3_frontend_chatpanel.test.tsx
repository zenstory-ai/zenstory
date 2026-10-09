/**
 * Round-3 回归（ChatPanel 侧）：
 *
 * #24 的另一半 —— ChatPanel.handleSteer 必须把失败**抛回**给 MessageInput。
 * 它此前把异常吞在内部只弹一句 toast，于是调用方以为「已送达」，
 * 同步清空输入框并把持久化草稿覆写成空串，用户输入彻底无法找回。
 */
import React from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, render as rtlRender, screen, waitFor, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

// ChatPanel reads the quota query (shared with QuotaBadge), so it needs a QueryClient.
const render = (ui: React.ReactElement) =>
  rtlRender(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      {ui}
    </QueryClientProvider>,
  )

const mockAgentStreamState = vi.hoisted(() => ({
  isStreaming: false,
  sessionId: null as string | null,
}))

const sendSteeringMessageMock = vi.hoisted(() => vi.fn())
const startStreamMock = vi.hoisted(() => vi.fn())
const rollbackMock = vi.hoisted(() => vi.fn())
const getVersionsMock = vi.hoisted(() => vi.fn())
const getRecentMessagesMock = vi.hoisted(() => vi.fn(async () => []))
const triggerFileTreeRefreshMock = vi.hoisted(() => vi.fn())
const triggerEditorRefreshMock = vi.hoisted(() => vi.fn())

const capturedMessageInputProps = vi.hoisted(() => ({
  props: null as Record<string, unknown> | null,
}))
const messageListMock = vi.hoisted(() => vi.fn())

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}))

vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({
    currentProjectId: 'project-1',
    selectedItem: null,
    triggerFileTreeRefresh: triggerFileTreeRefreshMock,
    triggerEditorRefresh: triggerEditorRefreshMock,
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
  useTextQuote: () => ({ quotes: [], clearQuotes: vi.fn() }),
}))

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
  useAgentStream: () => ({
    state: {},
    startStream: startStreamMock,
    cancel: vi.fn(),
    reset: vi.fn(),
    isStreaming: mockAgentStreamState.isStreaming,
    isThinking: false,
    thinkingContent: '',
    conflicts: [],
    error: null,
    errorCode: null,
    sessionId: mockAgentStreamState.sessionId,
    sendSteeringMessage: sendSteeringMessageMock,
  }),
}))

vi.mock('../../hooks/useDraftPersistence', () => ({
  useDraftPersistence: () => ({
    draft: '',
    saveDraft: vi.fn(),
    clearDraft: vi.fn(),
  }),
}))

vi.mock('../../lib/chatApi', () => ({
  getRecentMessages: getRecentMessagesMock,
  createNewSession: vi.fn(async () => ({ id: 'session-1' })),
  submitMessageFeedback: vi.fn(),
}))

vi.mock('../../lib/agentApi', () => ({
  fetchSuggestions: vi.fn(async () => []),
}))

vi.mock('../../lib/subscriptionApi', () => ({
  subscriptionApi: {
    getQuota: vi.fn(async () => ({ ai_conversations: { used: 0, limit: 10, reset_at: null } })),
  },
  subscriptionQueryKeys: {
    quota: () => ['subscription-quota', 'test-user'],
    quotaLite: () => ['quota', 'test-user'],
  },
}))

vi.mock('../../lib/analytics', () => ({
  trackEvent: vi.fn(),
}))

vi.mock('../../lib/api', () => ({
  fileVersionApi: {
    getVersions: getVersionsMock,
    rollback: rollbackMock,
  },
  versionApi: {},
}))

vi.mock('../MessageList', () => ({
  MessageList: React.forwardRef((props: Record<string, unknown>, _ref) => {
    messageListMock(props)
    return <div data-testid="mock-message-list" />
  }),
}))

vi.mock('../MessageInput', () => ({
  MessageInput: (props: Record<string, unknown>) => {
    capturedMessageInputProps.props = props
    return <div data-testid="mock-message-input" />
  },
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

vi.mock('../subscription/UpgradePromptModal', () => ({
  UpgradePromptModal: ({ open }: { open: boolean }) => (
    <div data-testid="mock-upgrade-modal" data-open={String(open)} />
  ),
}))

vi.mock('../../lib/toast', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}))

import { ChatPanel } from '../ChatPanel'
import { toast } from '../../lib/toast'
import { ApiError } from '../../lib/apiClient'

async function renderAndGetSteer(): Promise<(message: string) => Promise<void>> {
  render(<ChatPanel />)
  await waitFor(() => {
    expect(screen.getByTestId('mock-message-input')).toBeInTheDocument()
  })
  const onSteer = capturedMessageInputProps.props?.onSteer as
    | ((message: string) => Promise<void>)
    | undefined
  expect(typeof onSteer).toBe('function')
  return onSteer!
}

async function renderAndGetUndo(): Promise<(target: {
  fileId: string
  beforeVersionNumber: number
  expectedAfterUpdatedAt: string
}) => Promise<void>> {
  getRecentMessagesMock.mockResolvedValueOnce([{
    id: 'assistant-message',
    role: 'assistant',
    content: 'ready',
    created_at: '2026-10-06T12:00:00.000Z',
    session_id: 'session-1',
  }])
  render(<ChatPanel />)
  await waitFor(() => {
    expect(screen.getByTestId('mock-message-list')).toBeInTheDocument()
  })
  const onUndo = (messageListMock.mock.lastCall?.[0] as Record<string, unknown> | undefined)?.onUndo
  expect(typeof onUndo).toBe('function')
  return onUndo as (target: {
    fileId: string
    beforeVersionNumber: number
    expectedAfterUpdatedAt: string
  }) => Promise<void>
}

/** Clicking undo opens the in-app confirm dialog; the rollback runs on its confirm button. */
async function undoAndConfirm(
  onUndo: (target: { fileId: string; beforeVersionNumber: number; expectedAfterUpdatedAt: string }) => unknown,
  target: { fileId: string; beforeVersionNumber: number; expectedAfterUpdatedAt: string },
) {
  act(() => {
    onUndo(target)
  })
  const confirmButton = await screen.findByRole('button', { name: 'chat:actions.undo' })
  await act(async () => {
    fireEvent.click(confirmButton)
  })
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
}

describe('round3 #24 ChatPanel.handleSteer 必须把失败抛回调用方', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    capturedMessageInputProps.props = null
    mockAgentStreamState.isStreaming = true
    mockAgentStreamState.sessionId = 'session-1'
    sendSteeringMessageMock.mockReset()
  })

  it('后端返回 404 时既弹 toast 也向上抛出', async () => {
    sendSteeringMessageMock.mockRejectedValue(new Error('Request failed: 404'))
    const onSteer = await renderAndGetSteer()

    await expect(onSteer('把主角改名为林川')).rejects.toThrow('Request failed: 404')
    expect(vi.mocked(toast.error)).toHaveBeenCalledWith('chat:input.steerFailed')
    expect(vi.mocked(toast.success)).not.toHaveBeenCalled()
  })

  it('流已结束时也必须抛出，而不是静默 return（否则输入框照样被清空）', async () => {
    mockAgentStreamState.isStreaming = false
    const onSteer = await renderAndGetSteer()

    await expect(onSteer('补一句')).rejects.toThrow()
    expect(sendSteeringMessageMock).not.toHaveBeenCalled()
    expect(vi.mocked(toast.error)).toHaveBeenCalledWith('chat:input.steerFailed')
  })

  it('成功时正常 resolve 并提示已送达', async () => {
    sendSteeringMessageMock.mockResolvedValue(undefined)
    const onSteer = await renderAndGetSteer()

    await expect(onSteer('换个视角写')).resolves.toBeUndefined()
    expect(sendSteeringMessageMock).toHaveBeenCalledWith('换个视角写')
    expect(vi.mocked(toast.success)).toHaveBeenCalledWith('chat:input.steerSent')
  })

  it('canSteer 只在本轮流拿到 session 之后才为真', async () => {
    mockAgentStreamState.sessionId = null
    await renderAndGetSteer()
    expect(capturedMessageInputProps.props?.canSteer).toBe(false)
  })
})

describe('ChatPanel.handleSendMessage 透传显式选择的技能', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    capturedMessageInputProps.props = null
    mockAgentStreamState.isStreaming = false
    mockAgentStreamState.sessionId = null
  })

  it('把 MessageInput 传来的技能 id 放进 selected_skill_ids', async () => {
    render(<ChatPanel />)
    await waitFor(() => {
      expect(screen.getByTestId('mock-message-input')).toBeInTheDocument()
    })
    const onSend = capturedMessageInputProps.props?.onSend as (
      message: string,
      selectedSkillIds?: string[],
    ) => Promise<void>

    await onSend('写下一章', ['skill-a', 'skill-b'])
    expect(startStreamMock).toHaveBeenLastCalledWith(
      expect.objectContaining({ message: '写下一章', selected_skill_ids: ['skill-a', 'skill-b'] }),
    )

    await onSend('不选技能', [])
    expect(startStreamMock.mock.lastCall?.[0].selected_skill_ids).toBeUndefined()
  })
})

describe('ChatPanel immutable edit undo', () => {
  const target = {
    fileId: 'file-immutable',
    beforeVersionNumber: 7,
    expectedAfterUpdatedAt: '2026-10-06T12:34:56.000Z',
  }

  beforeEach(() => {
    vi.clearAllMocks()
    messageListMock.mockClear()
    rollbackMock.mockReset()
    getVersionsMock.mockReset()
    vi.stubGlobal('confirm', vi.fn(() => true))
  })

  it('preserves immutable provenance while parsing persisted tool history', async () => {
    getRecentMessagesMock.mockResolvedValueOnce([{
      id: 'assistant-history',
      role: 'assistant',
      content: '',
      created_at: '2026-10-06T12:00:00.000Z',
      session_id: 'session-1',
      tool_calls: JSON.stringify([{
        id: 'tool-history',
        name: 'edit_file',
        arguments: {},
        status: 'success',
        result: {
          data: {
            id: target.fileId,
            details: [],
            undo: {
              before_version_number: target.beforeVersionNumber,
              expected_after_updated_at: target.expectedAfterUpdatedAt,
            },
          },
        },
      }]),
    }])

    render(<ChatPanel />)

    await waitFor(() => {
      const messages = (messageListMock.mock.lastCall?.[0] as Record<string, unknown> | undefined)?.messages as Array<{
        toolResults?: Array<{ result?: Record<string, unknown> }>
      }> | undefined
      expect(messages?.[0]?.toolResults?.[0]?.result).toEqual({
        data: {
          id: target.fileId,
          details: [],
          undo: {
            before_version_number: target.beforeVersionNumber,
            expected_after_updated_at: target.expectedAfterUpdatedAt,
          },
        },
      })
    })
  })

  it('posts the exact stored target and token without re-reading latest versions', async () => {
    rollbackMock.mockResolvedValue({
      success: true,
      snapshot_created: true,
      version_quota_exceeded: false,
    })
    const onUndo = await renderAndGetUndo()

    await undoAndConfirm(onUndo, target)

    expect(getVersionsMock).not.toHaveBeenCalled()
    expect(rollbackMock).toHaveBeenCalledWith(
      'file-immutable',
      7,
      '2026-10-06T12:34:56.000Z',
    )
    expect(triggerFileTreeRefreshMock).toHaveBeenCalledTimes(1)
    expect(triggerEditorRefreshMock).toHaveBeenCalledWith('file-immutable')
  })

  it('confirms with the file-level undo message, not the snapshot pre-save promise', async () => {
    const confirmMock = vi.fn(() => false)
    vi.stubGlobal('confirm', confirmMock)
    const onUndo = await renderAndGetUndo()

    act(() => {
      void onUndo(target)
    })

    // File rollback does not save the current text first, so the undo confirm
    // must not reuse the snapshot copy that promises a way back.
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('editor:versionHistory.confirmUndoAIEdit')
    expect(dialog).not.toHaveTextContent('editor:versionHistory.confirmRollback')
    expect(confirmMock).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'common:cancel' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(rollbackMock).not.toHaveBeenCalled()
    expect(triggerEditorRefreshMock).not.toHaveBeenCalled()
  })

  it.each([
    {
      name: 'quota exhaustion',
      result: { success: true, snapshot_created: false, version_quota_exceeded: true },
      feedback: 'versions:quota.limitDescription',
      opensUpgrade: true,
    },
    {
      name: 'history omission',
      result: { success: true, snapshot_created: false, version_quota_exceeded: false },
      feedback: 'versions:rollbackHistoryNotSaved',
      opensUpgrade: false,
    },
  ])('refreshes restored content and reports $name', async ({ result, feedback, opensUpgrade }) => {
    rollbackMock.mockResolvedValue(result)
    const onUndo = await renderAndGetUndo()

    await undoAndConfirm(onUndo, target)

    expect(toast.error).toHaveBeenCalledWith(feedback)
    expect(triggerFileTreeRefreshMock).toHaveBeenCalledTimes(1)
    expect(triggerEditorRefreshMock).toHaveBeenCalledWith('file-immutable')
    await waitFor(() => {
      expect(screen.getAllByTestId('mock-upgrade-modal').at(-1)).toHaveAttribute(
        'data-open',
        String(opensUpgrade),
      )
    })
  })

  it('reports a stale-token conflict without success refresh', async () => {
    rollbackMock.mockRejectedValue(new ApiError(409, 'ERR_RESOURCE_CONFLICT'))
    const onUndo = await renderAndGetUndo()

    await undoAndConfirm(onUndo, target)

    expect(toast.error).toHaveBeenCalledTimes(1)
    expect(triggerFileTreeRefreshMock).not.toHaveBeenCalled()
    expect(triggerEditorRefreshMock).not.toHaveBeenCalled()
  })
})
