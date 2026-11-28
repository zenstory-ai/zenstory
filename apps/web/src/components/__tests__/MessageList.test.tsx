import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MessageList, type Message, type MessageListRef } from '../MessageList'
import { useEffect, useRef } from 'react'
import { MobileLayoutProvider, useMobileLayout } from '../../contexts/MobileLayoutContext'

vi.mock('../LazyMarkdown', () => {
  const SyncMarkdown = ({
    children,
    className,
  }: {
    children: string
    className?: string
  }) => {
    const content = children ?? ''

    const h1Match = content.match(/^#\s+(.+?)\n\n([\s\S]+)$/)
    if (h1Match) {
      return (
        <div className={className}>
          <h1>{h1Match[1]}</h1>
          <p>{h1Match[2]}</p>
        </div>
      )
    }

    const boldMatch = content.match(/^\*\*(.+)\*\*$/)
    if (boldMatch) {
      return (
        <div className={className}>
          <strong>{boldMatch[1]}</strong>
        </div>
      )
    }

    const codeBlockMatch = content.trim().match(/^```[\r\n]+([\s\S]*?)```$/)
    if (codeBlockMatch) {
      return (
        <div className={className}>
          <pre><code>{codeBlockMatch[1].trim()}</code></pre>
        </div>
      )
    }

    const inlineCodeMatch = content.match(/^(.*)`([^`]+)`(.*)$/)
    if (inlineCodeMatch) {
      return (
        <div className={className}>
          <span>{inlineCodeMatch[1]}</span>
          <code>{inlineCodeMatch[2]}</code>
          <span>{inlineCodeMatch[3]}</span>
        </div>
      )
    }

    return <div className={className}>{content}</div>
  }

  return {
    LazyMarkdown: SyncMarkdown,
    default: SyncMarkdown,
  }
})

// Mock useAuth hook
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: vi.fn(() => ({
    user: null,
  })),
}))

vi.mock('react-i18next', () => ({
  initReactI18next: {
    type: '3rdParty',
    init: vi.fn(),
  },
  useTranslation: () => ({
    // Return the key as-is to match actual component behavior; step-label numbers are appended.
    t: (key: string, options?: Record<string, unknown>) =>
      [key, options?.step, options?.n, options?.titles].filter((part) => part !== undefined).join(' '),
  }),
}))

// Helper component to test ref methods
function MessageListWithRef({
  messages,
  onRef,
  ...props
}: {
  messages: Message[]
  onRef: (ref: MessageListRef | null) => void
} & Omit<React.ComponentProps<typeof MessageList>, 'ref'>) {
  const ref = useRef<MessageListRef>(null)
  useEffect(() => {
    onRef(ref.current)
  }, [onRef])
  return <MessageList ref={ref} messages={messages} {...props} />
}

describe('MessageList', () => {
  const createMessage = (overrides: Partial<Message> = {}): Message => ({
    id: `msg-${Date.now()}-${Math.random()}`,
    role: 'user',
    content: 'Test message',
    timestamp: new Date(),
    ...overrides,
  })

  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows history compaction as a visible step while it runs', () => {
    render(
      <MessageList
        messages={[]}
        streamRenderItems={[{ id: 'c', type: 'history_compaction', compactionState: 'running', transient: true, timestamp: new Date() }]}
      />,
    )
    expect(screen.getByTestId('history-compaction-running')).toHaveTextContent('compaction.running')
    expect(screen.getByRole('status')).toBeInTheDocument()
    expect(screen.queryByTestId('history-compaction-marker')).not.toBeInTheDocument()
  })

  it('replays the compaction marker in a saved reply and opens the summary read-only', () => {
    const displayItems: NonNullable<Message['displayItems']> = [
      { id: 'c', type: 'history_compaction', compactionState: 'done', content: 'Handoff summary text', timestamp: new Date() },
      { id: 'r', type: 'content', content: 'Reply', timestamp: new Date() },
    ]
    render(<MessageList messages={[createMessage({ role: 'assistant', content: 'Reply', displayItems })]} />)

    const marker = screen.getByTestId('history-compaction-marker')
    expect(marker).toHaveTextContent('compaction.done')
    expect(marker).toHaveTextContent('compaction.doneHint')
    expect(screen.queryByText('Handoff summary text')).not.toBeInTheDocument()

    const toggle = screen.getByRole('button', { name: /compaction\.done/ })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('Handoff summary text')).toBeInTheDocument()
    expect(screen.getByText('Reply')).toBeInTheDocument()
  })

  it('renders a compaction marker without a summary as a plain divider', () => {
    const displayItems: NonNullable<Message['displayItems']> = [
      { id: 'c', type: 'history_compaction', compactionState: 'done', timestamp: new Date() },
    ]
    render(<MessageList messages={[createMessage({ role: 'assistant', content: '', displayItems })]} />)
    expect(screen.getByTestId('history-compaction-marker')).toHaveTextContent('compaction.done')
    expect(screen.queryByRole('button', { name: /compaction\.done/ })).not.toBeInTheDocument()
  })

  it('renders without crashing', () => {
    render(<MessageList messages={[]} />)
    // MessageList returns null for empty messages, so we just check it doesn't throw
  })

  it('keeps a stopped round that produced nothing visible, showing how it ended', () => {
    const stopped = createMessage({
      role: 'assistant',
      content: '',
      backendMessageId: 'a-1',
      stopOutcome: { reason: 'user_stopped', charged: false, savedOutput: false, removedFiles: [], writtenFiles: [] },
    })
    render(<MessageList messages={[createMessage({ content: '写第一章' }), stopped]} showDailyCount />)

    expect(screen.getByTestId('round-end-note')).toHaveTextContent('chat:panel.userStopped · chat:panel.roundNotCounted')
  })

  it('offers the manuscript on phones after the latest round wrote files', () => {
    function PanelProbe() {
      const { activePanel, setActivePanel } = useMobileLayout()
      return <button data-testid="panel" onClick={() => setActivePanel('chat')}>{activePanel}</button>
    }
    const wrote = createMessage({
      role: 'assistant',
      content: '开头写好了。',
      backendMessageId: 'a-2',
      stopOutcome: {
        reason: 'completed',
        charged: true,
        savedOutput: true,
        removedFiles: [],
        writtenFiles: [
          { id: 'f-1', title: '第一章', words: 812 },
          { id: 'f-2', title: '第二章', words: 640 },
        ],
      },
    })
    const onOpenFile = vi.fn()
    const view = render(
      <MobileLayoutProvider isMobile>
        <PanelProbe />
        <MessageList messages={[createMessage({ content: '写个开头' }), wrote]} onOpenFile={onOpenFile} />
      </MobileLayoutProvider>,
    )
    fireEvent.click(screen.getByTestId('panel'))
    expect(screen.getByTestId('panel')).toHaveTextContent('chat')

    fireEvent.click(screen.getByTestId('read-written-files'))
    expect(onOpenFile).toHaveBeenCalledWith('f-2', '', '第二章')
    expect(screen.getByTestId('panel')).toHaveTextContent('editor')

    // Only the latest round, and only on phones (desktop shows the editor beside the chat).
    view.rerender(
      <MobileLayoutProvider isMobile>
        <PanelProbe />
        <MessageList messages={[createMessage({ content: '写个开头' }), wrote, createMessage({ content: '接着写' })]} />
      </MobileLayoutProvider>,
    )
    expect(screen.queryByTestId('read-written-files')).not.toBeInTheDocument()
    view.rerender(<MessageList messages={[createMessage({ content: '写个开头' }), wrote]} />)
    expect(screen.queryByTestId('read-written-files')).not.toBeInTheDocument()
  })

  it('hides feedback for a stopped bubble that no saved message could be matched to', () => {
    const stopped = createMessage({ role: 'assistant', content: '第一段', feedbackUnavailable: true })
    render(<MessageList messages={[stopped]} onSubmitFeedback={vi.fn()} />)

    expect(screen.queryByText('feedback.pendingSave')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'feedback.like' })).not.toBeInTheDocument()
  })

  it('shows only the task role for an agent step, never internal step or budget details', () => {
    const displayItems: NonNullable<Message['displayItems']> = [
      { id: 'agent-step', type: 'agent_selected', agentName: 'Step writer', iteration: 2, maxIterations: 12, remaining: 10, timestamp: new Date() },
    ]
    const { container } = render(<MessageList messages={[createMessage({ role: 'assistant', content: '', displayItems })]} />)

    expect(container.textContent).toContain('Step writer')
    expect(container.textContent).not.toContain('workflow.iteration')
    expect(container.textContent).not.toContain('/12')
  })

  it('does not expose remaining internal turns', () => {
    const displayItems: NonNullable<Message['displayItems']> = [
      { id: 'agent-step', type: 'agent_selected', agentType: 'quality_reviewer', iteration: 3, maxIterations: 4, remaining: 1, timestamp: new Date() },
    ]
    const { container } = render(<MessageList messages={[createMessage({ role: 'assistant', content: '', displayItems })]} />)

    expect(container.textContent).not.toContain('workflow.iteration')
    expect(container.textContent).not.toContain('workflow.remaining')
    expect(container.textContent).not.toContain('workflow.lowTurnWarning')
  })

  it('hides Chinese control markers in assistant replies', () => {
    const { container } = render(
      <MessageList messages={[createMessage({ role: 'assistant', content: '第一章写好了。\n\n[任务完成]' })]} />
    )
    expect(screen.getByText(/第一章写好了。/)).toBeInTheDocument()
    expect(container.textContent).not.toContain('[任务完成]')

    const { container: clarification } = render(
      <MessageList messages={[createMessage({ role: 'assistant', content: '主角叫什么名字？ [需要澄清]' })]} />
    )
    expect(clarification.textContent).toContain('主角叫什么名字？')
    expect(clarification.textContent).not.toContain('[需要澄清]')
  })

  it('preserves author edit tags when they are part of a user message', () => {
    const content = '请保留这段示例：<author_edits>作者原文</author_edits>'
    const { container } = render(
      <MessageList messages={[createMessage({ role: 'user', content })]} />
    )

    expect(container.textContent).toContain(content)
  })

  it('hides internal author edit context in a saved assistant reply', () => {
    const { container } = render(
      <MessageList messages={[createMessage({
        role: 'assistant',
        content: '第二章已改写。\n\n<author_edits>无</author_edits>\n\n可以继续写下一章。',
      })]} />
    )

    expect(container.textContent).toContain('第二章已改写。')
    expect(container.textContent).toContain('可以继续写下一章。')
    expect(container.textContent).not.toContain('author_edits')
    expect(container.textContent).not.toContain('无')
  })

  it('omits empty assistant bubbles whose only displayItems content was stripped author_edits, and wires legacy/streaming delete_file cards safely', () => {
    const emptyEcho = createMessage({
      role: 'assistant',
      content: '',
      displayItems: [{ id: 'echo-only', type: 'content', content: '<author_edits>无</author_edits>', timestamp: new Date() }],
    })
    const { container: emptyContainer } = render(
      <MessageList
        messages={[emptyEcho]}
        streamRenderItems={[{ id: 'stream-echo', type: 'content', content: '', timestamp: new Date() }]}
      />,
    )
    expect(emptyContainer).toBeEmptyDOMElement()

    const onRestoreDeletion = vi.fn()
    const legacyDelete = createMessage({
      role: 'assistant',
      backendMessageId: 'msg-legacy-del',
      content: '已删除',
      toolResults: [
        { id: 'legacy-del-1', tool_name: 'delete_file', arguments: { id: 'legacy-file-1' }, status: 'success', result: undefined },
      ],
    })
    const streamItems = [
      {
        id: 'stream-del',
        type: 'tool_calls' as const,
        timestamp: new Date(),
        toolCalls: [
          { id: 'live-del-1', tool_name: 'delete_file', arguments: { id: 'live-file-1' }, status: 'success' as const, result: { id: 'live-file-1', restored: false } },
        ],
      },
    ]
    render(<MessageList messages={[legacyDelete]} streamRenderItems={streamItems} onRestoreDeletion={onRestoreDeletion} />)
    const restoreButtons = screen.getAllByRole('button', { name: /restoreDeleted/ })
    expect(restoreButtons).toHaveLength(1)
  })

  it('marks the conversation as off-limits to analytics DOM capture', () => {
    const { container } = render(
      <MessageList messages={[createMessage({ role: 'user', content: 'private prompt text' })]} />
    )
    const root = container.firstElementChild
    expect(root).toHaveClass('ph-no-capture')
    expect(root).toHaveTextContent('private prompt text')
  })

  it('replays saved text, tools, handoff and agent selection in observed order without duplicates', () => {
    const timestamp = new Date('2026-10-05T10:00:00Z')
    const tool = { id: 'tool-ordered', tool_name: 'create_file', arguments: {}, status: 'success' as const, result: { title: 'Ordered file' } }
    const displayItems = [
      { id: 'one', type: 'content' as const, content: 'First text', timestamp },
      { id: 'tool', type: 'tool_calls' as const, toolCalls: [tool], timestamp },
      { id: 'two', type: 'content' as const, content: 'Second text', timestamp },
      { id: 'handoff', type: 'thinking_status' as const, content: 'Handing off now', timestamp },
      { id: 'agent', type: 'agent_selected' as const, agentName: 'Writer agent', timestamp },
      { id: 'three', type: 'content' as const, content: 'Last text', timestamp },
    ]
    const message = createMessage({ role: 'assistant', content: 'First textSecond textLast text', toolResults: [tool], ...{ displayItems } })
    const { container, rerender } = render(<MessageList messages={[]} streamRenderItems={displayItems} />)
    const markers = ['First text', 'Ordered file', 'Second text', 'Handing off now', 'Writer agent', 'Last text']
    const positions = () => markers.map(marker => container.textContent!.indexOf(marker))
    const livePositions = positions()
    expect(livePositions.every((position, index) => position >= 0 && (index === 0 || position > livePositions[index - 1]))).toBe(true)
    rerender(<MessageList messages={[message]} />)
    const historyPositions = positions()
    expect(historyPositions.every((position, index) => position >= 0 && (index === 0 || position > historyPositions[index - 1]))).toBe(true)
    for (const marker of markers) expect(screen.getAllByText(marker)).toHaveLength(1)
  })

  it('shows the live thinking indicator without a streaming message id, but not on replay', () => {
    const displayItems = [{ id: 'thinking', type: 'thinking_content' as const, content: 'Thinking about continuity', timestamp: new Date() }]
    const { container, rerender } = render(<MessageList messages={[]} streamRenderItems={displayItems} />)
    expect(container.querySelectorAll('.animate-pulse')).toHaveLength(3)
    rerender(<MessageList messages={[createMessage({ role: 'assistant', content: '', displayItems })]} />)
    expect(container.querySelectorAll('.animate-pulse')).toHaveLength(0)
  })

  it('replays useful task activity without internal topology or budgets', () => {
    const timestamp = new Date()
    const displayItems: NonNullable<Message['displayItems']> = [
      { id: 'handoff', type: 'thinking_status', content: 'Choosing ordered agent', timestamp },
      { id: 'agent-low', type: 'agent_selected', agentName: 'Ordered writer', iteration: 3, maxIterations: 4, remaining: 1, timestamp },
      { id: 'reply', type: 'content', content: 'Ordered control reply', timestamp },
      { id: 'done', type: 'workflow_complete', timestamp },
      { id: 'hidden', type: 'content', content: '<think>Not visible</think>', timestamp },
    ]
    render(<MessageList messages={[createMessage({ role: 'assistant', content: '', displayItems })]} />)
    for (const marker of ['Choosing ordered agent', 'Ordered writer', 'Ordered control reply', 'workflow.taskCompleted']) expect(screen.getByText(marker)).toBeInTheDocument()
    expect(screen.queryByText('workflow.lowTurnWarning')).not.toBeInTheDocument()
    expect(screen.queryByText('Not visible')).not.toBeInTheDocument()
  })

  it.each(['collaboration', 'tool_call'] as const)('retains %s exhaustion actions on a saved timeline without duplicate legacy status cards', layer => {
    const onAction = vi.fn()
    const item: NonNullable<Message['displayItems']>[number] = { id: 'limit', type: 'iteration_exhausted', layer, maxIterations: 4, iterationsUsed: 4, reason: 'Ordered limit reason', lastAgent: 'writer', timestamp: new Date() }
    render(<MessageList messages={[createMessage({ role: 'assistant', content: '', displayItems: [item], statusCards: [{ type: 'iteration_exhausted', layer, maxIterations: 4 }] })]} onIterationAssistAction={onAction} />)
    const continueButton = screen.getByRole('button', { name: 'workflow.actionContinue' })
    const splitButton = screen.getByRole('button', { name: 'workflow.actionSplit' })
    const manualButton = screen.getByRole('button', { name: 'workflow.actionManual' })
    expect(continueButton.className).toContain('bg-[hsl(var(--primary))]')
    expect(splitButton.className).toBe(manualButton.className)
    expect(splitButton.className).not.toContain('--warning')
    expect(screen.queryByText('workflow.nextStepHint')).not.toBeInTheDocument()
    for (const [button, action] of [[continueButton, 'continue'], [splitButton, 'split'], [manualButton, 'manual']] as const) {
      fireEvent.click(button)
      expect(onAction).toHaveBeenLastCalledWith(action, item)
    }
    // The internal stop reason stays out of the author-facing card
    expect(screen.queryByText('Ordered limit reason')).not.toBeInTheDocument()
  })

  it.each(['empty_file_correction', 'tool_call'] as const)('says a %s stop with an unwritten file is unfinished and names the files, live and after reload', layer => {
    const item: NonNullable<Message['displayItems']>[number] = { id: 'empty', type: 'iteration_exhausted', layer,
      unfinishedFiles: ['第一章'], rolledBackFiles: ['第二章', '第三章'], timestamp: new Date() }
    const { unmount } = render(<MessageList messages={[createMessage({ role: 'assistant', content: '', displayItems: [item] })]} />)
    expect(screen.getByText('workflow.emptyFileUnfinished')).toBeInTheDocument()
    expect(screen.getByText('workflow.emptyFileUnresolved 第一章')).toBeInTheDocument()
    expect(screen.getByText('workflow.emptyFileRolledBack 第二章panel.removedFilesJoiner第三章')).toBeInTheDocument()
    expect(screen.queryByText('workflow.toolCallExhaustedSummary')).not.toBeInTheDocument()
    unmount()

    render(<MessageList messages={[createMessage({ role: 'assistant', content: '', statusCards: [{ type: 'iteration_exhausted', layer, rolledBackFiles: ['第二章'] }] })]} />)
    expect(screen.getByText('workflow.emptyFileUnfinished')).toBeInTheDocument()
    expect(screen.getByText('workflow.emptyFileRolledBack 第二章')).toBeInTheDocument()
    expect(screen.queryByText('workflow.toolCallExhaustedSummary')).not.toBeInTheDocument()
  })

  it('says a step written out as text did not run, live and after reload, without next-step buttons', () => {
    const onAction = vi.fn()
    const item: NonNullable<Message['displayItems']>[number] = { id: 'textual', type: 'iteration_exhausted',
      layer: 'unexecuted_tool_text', iterationsUsed: 1, maxIterations: 12, reason: 'internal reason', timestamp: new Date() }
    const { unmount } = render(<MessageList messages={[createMessage({ role: 'assistant', content: '', displayItems: [item] })]} onIterationAssistAction={onAction} />)
    expect(screen.getByText('workflow.unexecutedToolText')).toBeInTheDocument()
    expect(screen.getByText('workflow.unexecutedToolTextSummary')).toBeInTheDocument()
    expect(screen.queryByText('workflow.toolCallExhaustedSummary')).not.toBeInTheDocument()
    expect(screen.queryByText('internal reason')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'workflow.actionContinue' })).not.toBeInTheDocument()
    unmount()

    render(<MessageList messages={[createMessage({ role: 'assistant', content: '', statusCards: [{ type: 'iteration_exhausted', layer: 'unexecuted_tool_text' }] })]} onIterationAssistAction={onAction} />)
    expect(screen.getByText('workflow.unexecutedToolText')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'workflow.actionContinue' })).not.toBeInTheDocument()
    expect(onAction).not.toHaveBeenCalled()
  })

  it('defers only empty-file cleanup while streaming and restores recovery if the terminal is an error', () => {
    const card = { id: 'delayed-cleanup', type: 'iteration_exhausted' as const,
      layer: 'empty_file_correction' as const, rolledBackFiles: ['临时占位'], timestamp: new Date() }
    const view = render(<MessageList messages={[]} streamRenderItems={[card]} />)
    expect(screen.queryByText('workflow.emptyFileUnfinished')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'workflow.actionContinue' })).not.toBeInTheDocument()

    view.rerender(<MessageList messages={[createMessage({ role: 'assistant', content: '', displayItems: [card],
      stopOutcome: { reason: 'error', charged: true, savedOutput: false, removedFiles: [], writtenFiles: [] },
    })]} />)
    expect(screen.getByText('workflow.emptyFileUnfinished')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'workflow.actionContinue' })).toBeInTheDocument()

    view.rerender(<MessageList messages={[]} streamRenderItems={[{ ...card, layer: 'tool_call' }]} />)
    expect(screen.getByText('workflow.emptyFileUnfinished')).toBeInTheDocument()
  })

  it('uses the round note as the sole terminal UI for empty-file cleanup while keeping continue reachable', () => {
    const onAction = vi.fn()
    const card = { type: 'iteration_exhausted' as const, layer: 'empty_file_correction' as const,
      unfinishedFiles: ['仍存在的文件'], rolledBackFiles: ['已撤销的临时文件'] }
    render(<MessageList messages={[createMessage({
      role: 'assistant', content: '', statusCards: [card], stopOutcome: {
        reason: 'incomplete', charged: true, savedOutput: true, removedFiles: ['已撤销的临时文件'], writtenFiles: [],
      },
    })]} onIterationAssistAction={onAction} />)

    expect(screen.getByTestId('round-end-note')).toHaveTextContent('panel.roundIncompleteSaved')
    expect(screen.queryByText('workflow.emptyFileUnfinished')).not.toBeInTheDocument()
    expect(screen.queryByText(/仍存在的文件|已撤销的临时文件/)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'workflow.actionContinue' }))
    expect(onAction).toHaveBeenCalledWith('continue', card)
  })

  it.each([false, true])('still says a quiet empty-file round is unfinished when it also saved files (messages used up: %s)', nextStepsBlocked => {
    const card = { type: 'iteration_exhausted' as const, layer: 'empty_file_correction' as const, rolledBackFiles: ['第二章'] }
    render(<MessageList messages={[createMessage({
      role: 'assistant', content: '', statusCards: [card], stopOutcome: {
        reason: 'incomplete', charged: true, savedOutput: true, removedFiles: [],
        writtenFiles: [{ title: '第一章', words: 3000 }],
      },
    })]} onIterationAssistAction={vi.fn()} nextStepsBlocked={nextStepsBlocked} />)

    const note = screen.getByTestId('round-end-note')
    expect(note).toHaveTextContent('panel.roundSavedFiles')
    expect(note).toHaveTextContent(nextStepsBlocked ? 'panel.roundUnfinishedItem' : 'panel.roundUnfinishedContinueItem')
    expect(screen.queryAllByRole('button', { name: 'workflow.actionContinue' })).toHaveLength(nextStepsBlocked ? 0 : 1)
  })

  it('hides a canonical incomplete display card while keeping exactly one continue action', () => {
    const onAction = vi.fn()
    const card = { id: 'canonical-empty', type: 'iteration_exhausted' as const,
      layer: 'empty_file_correction' as const, unfinishedFiles: ['仍存在的文件'],
      rolledBackFiles: ['已撤销的临时文件'], timestamp: new Date() }
    render(<MessageList messages={[createMessage({
      role: 'assistant', content: '', displayItems: [card], stopOutcome: {
        reason: 'incomplete', charged: true, savedOutput: true,
        removedFiles: ['已撤销的临时文件'], writtenFiles: [],
      },
    })]} onIterationAssistAction={onAction} />)

    expect(screen.getByTestId('round-end-note')).toHaveTextContent('panel.roundIncompleteSaved')
    expect(screen.queryByText('workflow.emptyFileUnfinished')).not.toBeInTheDocument()
    expect(screen.queryByText(/仍存在的文件|已撤销的临时文件/)).not.toBeInTheDocument()
    const continueButtons = screen.getAllByRole('button', { name: 'workflow.actionContinue' })
    expect(continueButtons).toHaveLength(1)
    fireEvent.click(continueButtons[0])
    expect(onAction).toHaveBeenCalledWith('continue', card)
  })

  it('keeps the big-task summary on a tool-turn stop that names no files', () => {
    render(<MessageList messages={[createMessage({ role: 'assistant', content: '', statusCards: [{ type: 'iteration_exhausted', layer: 'tool_call', rolledBackFiles: [], unfinishedFiles: [] }] })]} />)
    expect(screen.getByText('workflow.iterationExhausted')).toBeInTheDocument()
    expect(screen.getByText('workflow.toolCallExhaustedSummary')).toBeInTheDocument()
    expect(screen.queryByText('workflow.emptyFileUnfinished')).not.toBeInTheDocument()
  })

  it('says a run stopped near the time limit may already be done, live and after reload', () => {
    const item: NonNullable<Message['displayItems']>[number] = { id: 'time', type: 'iteration_exhausted', layer: 'tool_call', limit: 'wall_clock', timestamp: new Date() }
    const { unmount } = render(<MessageList messages={[createMessage({ role: 'assistant', content: '', displayItems: [item] })]} />)
    expect(screen.getByText('workflow.timeLimitSummary')).toBeInTheDocument()
    expect(screen.queryByText('workflow.toolCallExhaustedSummary')).not.toBeInTheDocument()
    unmount()

    render(<MessageList messages={[createMessage({ role: 'assistant', content: '', statusCards: [{ type: 'iteration_exhausted', layer: 'tool_call', limit: 'wall_clock' }] })]} />)
    expect(screen.getByText('workflow.timeLimitSummary')).toBeInTheDocument()
  })

  it('shows the request-level time pause (graph wrap-up or hard timeout) with 继续完成, live and after reload, next to the saved-files receipt', () => {
    const onAction = vi.fn()
    const outcome = {
      reason: 'incomplete' as const, charged: true, savedOutput: true, removedFiles: [],
      writtenFiles: [{ title: '第3章', words: 3120 }],
    }
    const item: NonNullable<Message['displayItems']>[number] = {
      id: 'wrap', type: 'iteration_exhausted', layer: 'collaboration', limit: 'wall_clock', lastAgent: 'writer', timestamp: new Date(),
    }
    const { unmount } = render(<MessageList
      messages={[createMessage({ role: 'assistant', content: '第3章写好了。', displayItems: [item], stopOutcome: outcome })]}
      onIterationAssistAction={onAction}
    />)
    expect(screen.getByText('workflow.timeLimitSummary')).toBeInTheDocument()
    expect(screen.queryByText('workflow.collaborationExhaustedSummary')).not.toBeInTheDocument()
    expect(screen.getByTestId('round-end-note')).toHaveTextContent('panel.roundSavedFiles')
    expect(screen.getByTestId('round-end-note')).not.toHaveTextContent('panel.roundUnfinishedContinueItem')
    expect(screen.getByTestId('round-end-note')).not.toHaveTextContent('panel.roundUnfinishedItem')
    fireEvent.click(screen.getByRole('button', { name: 'workflow.actionContinue' }))
    expect(onAction).toHaveBeenCalledWith('continue', item)
    unmount()

    const card = { type: 'iteration_exhausted' as const, layer: 'collaboration' as const, limit: 'wall_clock', lastAgent: 'writer' }
    render(<MessageList
      messages={[createMessage({ role: 'assistant', content: '', statusCards: [card], stopOutcome: outcome })]}
      onIterationAssistAction={onAction}
    />)
    expect(screen.getByText('workflow.timeLimitSummary')).toBeInTheDocument()
    expect(screen.getByTestId('round-end-note')).toHaveTextContent('panel.roundSavedFiles')
    expect(screen.getByTestId('round-end-note')).not.toHaveTextContent('panel.roundUnfinishedContinueItem')
    expect(screen.getAllByRole('button', { name: 'workflow.actionContinue' })).toHaveLength(1)
  })

  it('keeps the pause facts but offers no 继续完成 when today\'s AI messages are used up, live, saved and streaming', () => {
    const onAction = vi.fn()
    const timeItem: NonNullable<Message['displayItems']>[number] = { id: 'time', type: 'iteration_exhausted', layer: 'tool_call', limit: 'wall_clock', timestamp: new Date() }
    const emptyItem: NonNullable<Message['displayItems']>[number] = { id: 'empty', type: 'iteration_exhausted', layer: 'tool_call', unfinishedFiles: ['第一章'], rolledBackFiles: ['第二章'], timestamp: new Date() }
    const messages = [
      createMessage({ id: 'm-1', role: 'assistant', content: '', displayItems: [timeItem] }),
      createMessage({ id: 'm-2', role: 'assistant', content: '', statusCards: [{ type: 'iteration_exhausted', layer: 'collaboration', maxIterations: 4 }] }),
    ]
    const streamRenderItems = [emptyItem]
    const { rerender } = render(<MessageList
      messages={messages}
      streamRenderItems={streamRenderItems}
      onIterationAssistAction={onAction}
      nextStepsBlocked
    />)
    expect(screen.getByText('workflow.timeLimitSummaryPaused')).toBeInTheDocument()
    expect(screen.getByText('workflow.stepLimitSummaryPaused')).toBeInTheDocument()
    expect(screen.getByText('workflow.emptyFileUnresolvedPaused 第一章')).toBeInTheDocument()
    expect(screen.getByText('workflow.emptyFileRolledBackPaused 第二章')).toBeInTheDocument()
    expect(screen.queryByText('workflow.timeLimitSummary')).not.toBeInTheDocument()
    expect(screen.queryByText('workflow.nextStepHint')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'workflow.actionContinue' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'workflow.actionSplit' })).not.toBeInTheDocument()

    // Messages come back (a new Beijing day, or Pro): the same list, only the flag changes.
    rerender(<MessageList messages={messages} streamRenderItems={streamRenderItems} onIterationAssistAction={onAction} nextStepsBlocked={false} />)
    expect(screen.getByText('workflow.timeLimitSummary')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'workflow.actionContinue' })).toHaveLength(3)
    fireEvent.click(screen.getAllByRole('button', { name: 'workflow.actionContinue' })[0])
    expect(onAction).toHaveBeenCalledWith('continue', timeItem)
  })

  it.each([
    { reason: 'error', message: 'Ordered stop error' },
    { reason: 'clarification_needed', question: 'Ordered question', context: 'Ordered context', details: ['Ordered detail'] },
    { reason: 'clarification_needed' },
    { question: 'Ordered implicit question', details: ['Ordered implicit detail'] },
  ])('replays stop/clarification payload without grouping or duplicating it: %s', data => {
    const displayItems: NonNullable<Message['displayItems']> = [{ id: 'stop', type: 'workflow_stopped', ...data, timestamp: new Date() }]
    render(<MessageList messages={[createMessage({ role: 'assistant', content: '', displayItems })]} />)
    if (data.message) expect(screen.getByText(data.message)).toBeInTheDocument()
    else if (data.question) expect(screen.getByText(data.question)).toBeInTheDocument()
    else expect(screen.getByText('workflow.needsConfirmation')).toBeInTheDocument()
    if (data.context) expect(screen.getByText(data.context)).toBeInTheDocument()
    for (const detail of data.details ?? []) expect(screen.getByText(detail)).toBeInTheDocument()
  })

  it('keeps saved agent-only timeline turns visible', () => {
    const message = createMessage({ role: 'assistant', content: '', ...{ displayItems: [{ id: 'agent-only', type: 'agent_selected', agentName: 'Planner agent', timestamp: new Date() }] } })
    render(<MessageList messages={[message]} />)
    expect(screen.getByText('Planner agent')).toBeInTheDocument()
  })

  it('labels a selected agent by its localized role, its display name, or not at all when only the raw id is known', () => {
    const timestamp = new Date()
    const displayItems: NonNullable<Message['displayItems']> = [
      // Known agent id: the localized role replaces both the id and the backend name.
      { id: 'known', type: 'agent_selected', agentType: 'writer', agentName: 'writer_agent_v2', timestamp },
      // Unknown agent id with a readable name: fall back to that name.
      { id: 'unknown-named', type: 'agent_selected', agentType: 'lore_keeper', agentName: 'Lore Keeper', timestamp },
      // Unknown agent id whose name is just the id again: hide it instead of leaking the internal id.
      { id: 'raw-id', type: 'agent_selected', agentType: 'mystery_agent', agentName: 'mystery_agent', timestamp },
      { id: 'reply', type: 'content', content: 'Agent label reply', timestamp },
    ]
    render(<MessageList messages={[createMessage({ role: 'assistant', content: '', displayItems })]} />)
    expect(screen.getByText('chat:workflow.agents.writer')).toBeInTheDocument()
    expect(screen.queryByText('writer_agent_v2')).not.toBeInTheDocument()
    expect(screen.getByText('Lore Keeper')).toBeInTheDocument()
    expect(screen.queryByText('lore_keeper')).not.toBeInTheDocument()
    expect(screen.queryByText(/mystery_agent/)).not.toBeInTheDocument()
    expect(screen.getByText('Agent label reply')).toBeInTheDocument()
  })

  it('renders user message correctly', () => {
    const messages = [createMessage({ role: 'user', content: 'Hello' })]
    render(<MessageList messages={messages} />)
    expect(screen.getByText('Hello')).toBeInTheDocument()
  })

  it('renders assistant message correctly', () => {
    const messages = [createMessage({ role: 'assistant', content: 'Hi there' })]
    render(<MessageList messages={messages} />)
    expect(screen.getByText('Hi there')).toBeInTheDocument()
  })

  it('submits assistant feedback when like/dislike is clicked', () => {
    const onSubmitFeedback = vi.fn()
    const message = createMessage({
      role: 'assistant',
      content: 'Helpful response',
      backendMessageId: 'msg-1',
    })

    render(<MessageList messages={[message]} onSubmitFeedback={onSubmitFeedback} />)

    fireEvent.click(screen.getByRole('button', { name: 'feedback.like' }))
    expect(onSubmitFeedback).toHaveBeenCalledWith(
      expect.objectContaining({ id: message.id }),
      'up'
    )

    fireEvent.click(screen.getByRole('button', { name: 'feedback.dislike' }))
    expect(onSubmitFeedback).toHaveBeenCalledWith(
      expect.objectContaining({ id: message.id }),
      'down'
    )
  })

  it('shows disabled feedback controls while backend message is pending', () => {
    const onSubmitFeedback = vi.fn()
    const message = createMessage({
      role: 'assistant',
      content: 'Pending response',
      // backendMessageId intentionally missing
    })

    render(<MessageList messages={[message]} onSubmitFeedback={onSubmitFeedback} />)

    const likeButton = screen.getByRole('button', { name: 'feedback.like' })
    const dislikeButton = screen.getByRole('button', { name: 'feedback.dislike' })

    expect(likeButton).toBeDisabled()
    expect(dislikeButton).toBeDisabled()
    expect(screen.getByText('feedback.pendingSave')).toBeInTheDocument()

    fireEvent.click(likeButton)
    fireEvent.click(dislikeButton)
    expect(onSubmitFeedback).not.toHaveBeenCalled()
  })

  it('renders assistant message with markdown', () => {
    const messages = [createMessage({ role: 'assistant', content: '# Title\n\nContent' })]
    render(<MessageList messages={messages} />)
    // Check if markdown heading is rendered
    expect(screen.getByRole('heading', { name: 'Title' })).toBeInTheDocument()
    expect(screen.getByText('Content')).toBeInTheDocument()
  })

  it('renders bold markdown text', () => {
    const messages = [createMessage({ role: 'assistant', content: '**Bold text**' })]
    render(<MessageList messages={messages} />)
    // Check if strong element is rendered
    const strongElement = screen.getByText('Bold text')
    expect(strongElement.tagName).toBe('STRONG')
  })

  it('renders code blocks', () => {
    const messages = [createMessage({ role: 'assistant', content: '```\ncode\n```' })]
    render(<MessageList messages={messages} />)
    expect(screen.getByText('code')).toBeInTheDocument()
  })

  it('renders inline code', () => {
    const messages = [createMessage({ role: 'assistant', content: 'Use `code` here' })]
    render(<MessageList messages={messages} />)
    const codeElement = screen.getByText('code')
    expect(codeElement.tagName).toBe('CODE')
  })

  it('renders multiple messages', () => {
    const messages = [
      createMessage({ role: 'user', content: 'Hello' }),
      createMessage({ role: 'assistant', content: 'Hi there' }),
      createMessage({ role: 'user', content: 'How are you?' }),
    ]
    render(<MessageList messages={messages} />)
    expect(screen.getByText('Hello')).toBeInTheDocument()
    expect(screen.getByText('Hi there')).toBeInTheDocument()
    expect(screen.getByText('How are you?')).toBeInTheDocument()
  })

  it('renders tool call cards', () => {
    const messages = [
      createMessage({
        role: 'assistant',
        content: '',
        toolCalls: [
          {
            id: 'tool-1',
            tool_name: 'create_file',
            arguments: { title: 'Test File' },
            status: 'success',
          },
        ],
      }),
    ]
    render(<MessageList messages={messages} />)
    // When status is 'success', ToolResultCard renders as 'tool_result' type
    // which shows 'chat:tool.created' for create_file
    expect(screen.getByText('chat:tool.created')).toBeInTheDocument()
  })

  it.each(['toolCalls', 'toolResults', 'displayItems', 'streamRenderItems'] as const)(
    'opens only a successful result with an ID through %s', field => {
      const onOpenFile = vi.fn()
      const tool = { id: 'tool-open', tool_name: 'create_file', arguments: { id: 'argument-only', title: 'Argument title' }, status: 'success' as const,
        result: { id: 'exact-file-id', title: 'Open this file', file_type: 'draft' } }
      const items = [{ id: 'open-items', type: 'tool_calls' as const, toolCalls: [tool], timestamp: new Date() }]
      const message = createMessage({ role: 'assistant', content: '' })
      const props = field === 'streamRenderItems' ? { messages: [], streamRenderItems: items }
        : { messages: [{ ...message, [field]: field === 'displayItems' ? items : [tool] }] }
      const { rerender } = render(<MessageList {...props} onOpenFile={onOpenFile} />)
      fireEvent.click(screen.getByRole('button', { name: 'Open this file' }))
      expect(onOpenFile).toHaveBeenCalledWith('exact-file-id', 'draft', 'Open this file')
      const nextOpen = vi.fn()
      rerender(<MessageList {...props} onOpenFile={nextOpen} />)
      fireEvent.click(screen.getByRole('button', { name: 'Open this file' }))
      expect(nextOpen).toHaveBeenCalledTimes(1)
      // Neither a failed result with no error string nor arguments-only history is a confirmed target.
      const invalidTools = [{ ...tool, status: 'error' as const }, { ...tool, result: undefined }]
      rerender(<MessageList messages={[{ ...message, toolCalls: invalidTools }]} onOpenFile={onOpenFile} />)
      expect(screen.queryByRole('button', { name: /Open this file|Argument title/ })).not.toBeInTheDocument()
    },
  )

  it.each(['toolResults', 'displayItems', 'streamRenderItems'] as const)(
    'opens Writing Conventions from a remembered-note receipt through %s', field => {
      const firstOpen = vi.fn()
      const tool = {
        id: 'remember-notes',
        tool_name: 'update_project',
        arguments: {},
        status: 'success' as const,
        result: {
          status: 'success',
          data: { updated_fields: ['notes'], notes_added: ['女主要到第五章才出场'] },
        },
      }
      const items = [{ id: 'remember-items', type: 'tool_calls' as const, toolCalls: [tool], timestamp: new Date() }]
      const message = createMessage({ role: 'assistant', content: '' })
      const props = field === 'streamRenderItems'
        ? { messages: [], streamRenderItems: items }
        : { messages: [{ ...message, [field]: field === 'displayItems' ? items : [tool] }] }
      const { rerender } = render(<MessageList {...props} onOpenAIMemory={firstOpen} />)

      fireEvent.click(screen.getByRole('button', { name: 'chat:notesAdded.open' }))
      expect(firstOpen).toHaveBeenCalledTimes(1)

      const latestOpen = vi.fn()
      rerender(<MessageList {...props} onOpenAIMemory={latestOpen} />)
      fireEvent.click(screen.getByRole('button', { name: 'chat:notesAdded.open' }))
      expect(latestOpen).toHaveBeenCalledTimes(1)
      expect(firstOpen).toHaveBeenCalledTimes(1)
    },
  )

  it('renders pending tool calls', () => {
    const messages = [
      createMessage({
        role: 'assistant',
        content: '',
        toolCalls: [
          {
            id: 'tool-1',
            tool_name: 'create_file',
            arguments: { title: 'Test File' },
            status: 'pending',
          },
        ],
      }),
    ]
    render(<MessageList messages={messages} />)
    // When status is 'pending', ToolResultCard renders as 'tool_call' type
    // which shows 'chat:tool.create_file' label
    expect(screen.getByText('chat:tool.create_file')).toBeInTheDocument()
  })

  it('a tool call left pending by a stopped round reads as unfinished, not as still running', () => {
    const pendingEdit = { id: 'edit-1', tool_name: 'edit_file', arguments: { title: '第4集' }, status: 'pending' as const }
    const displayItems = [{ id: 'tools', type: 'tool_calls' as const, toolCalls: [pendingEdit], timestamp: new Date() }]
    const { container, rerender } = render(<MessageList messages={[]} streamRenderItems={displayItems} />)
    // 流式中：照常转圈、写「处理中」。
    expect(screen.getByText('chat:tool.processing_ellipsis')).toBeInTheDocument()
    expect(container.querySelector('.animate-spin')).not.toBeNull()

    // 这一轮已结束（停止 / 断开）：不再转圈，改说没做完。
    rerender(<MessageList messages={[createMessage({ role: 'assistant', content: '', displayItems })]} />)
    expect(screen.queryByText('chat:tool.processing_ellipsis')).not.toBeInTheDocument()
    expect(container.querySelector('.animate-spin')).toBeNull()
    expect(screen.getByText('chat:tool.interrupted')).toBeInTheDocument()

    // 没有 displayItems 的旧消息走 legacy 分支，同样不转圈。
    rerender(<MessageList messages={[createMessage({ role: 'assistant', content: '', toolCalls: [pendingEdit] })]} />)
    expect(container.querySelector('.animate-spin')).toBeNull()
    expect(screen.getByText('chat:tool.interrupted')).toBeInTheDocument()
  })

  it('does not draw tool cards for handoff and clarification control calls, live or saved', () => {
    const timestamp = new Date()
    const handoff = { id: 'tool-handoff', tool_name: 'handoff_to_agent', arguments: { target_agent: 'writer' }, status: 'success' as const, result: {} }
    const clarify = { id: 'tool-clarify', tool_name: 'request_clarification', arguments: { question: 'Which chapter?' }, status: 'pending' as const }
    const displayItems = [
      { id: 'tools', type: 'tool_calls' as const, toolCalls: [handoff, clarify], timestamp },
      { id: 'handoff', type: 'thinking_status' as const, content: 'Handing off to writer', timestamp },
    ]
    const { container, rerender } = render(<MessageList messages={[]} streamRenderItems={displayItems} />)
    expect(screen.getByText('Handing off to writer')).toBeInTheDocument()
    expect(container.textContent).not.toMatch(/chat:tool\./)

    rerender(<MessageList messages={[createMessage({ role: 'assistant', content: '', toolCalls: [handoff, clarify], toolResults: [handoff] })]} />)
    expect(container.textContent).not.toMatch(/chat:tool\./)
  })

  it('shows a dropped round that saved files as a delivery receipt the author can open', () => {
    const onOpenFile = vi.fn()
    const outcome = {
      reason: 'client_disconnected' as const, charged: true, savedOutput: true, removedFiles: [],
      writtenFiles: [{ id: 'ch-1', title: '第一章 雨夜', words: 1273 }, { title: '没有 id 的文件', words: 10 }],
    }
    const { rerender } = render(<MessageList
      messages={[createMessage({ role: 'assistant', content: '', stopOutcome: outcome })]}
      onOpenFile={onOpenFile}
    />)
    expect(screen.getByTestId('round-end-note')).toHaveTextContent('panel.roundSavedFiles')
    const open = screen.getByRole('button', { name: 'panel.openWrittenFile' })
    fireEvent.click(open)
    expect(onOpenFile).toHaveBeenCalledWith('ch-1', '', '第一章 雨夜')
    expect(screen.getAllByRole('button', { name: 'panel.openWrittenFile' })).toHaveLength(1)

    // Nothing confirmed saved, another ending, or no way to open: no receipt buttons.
    rerender(<MessageList messages={[createMessage({ role: 'assistant', content: '', stopOutcome: { ...outcome, savedOutput: false } })]} onOpenFile={onOpenFile} />)
    expect(screen.queryByRole('button', { name: 'panel.openWrittenFile' })).not.toBeInTheDocument()
    rerender(<MessageList messages={[createMessage({ role: 'assistant', content: '', stopOutcome: { ...outcome, reason: 'user_stopped' as const } })]} onOpenFile={onOpenFile} />)
    expect(screen.queryByRole('button', { name: 'panel.openWrittenFile' })).not.toBeInTheDocument()
    rerender(<MessageList messages={[createMessage({ role: 'assistant', content: '', stopOutcome: outcome })]} />)
    expect(screen.queryByRole('button', { name: 'panel.openWrittenFile' })).not.toBeInTheDocument()
  })

  it('does not draw the server\'s write-order refusal as a failed step, live or saved', () => {
    const timestamp = new Date()
    const refused = {
      id: 'tool-refused', tool_name: 'edit_file', arguments: { id: 'ch-1' }, status: 'error' as const,
      result: { status: 'error', error_type: 'file_body_pending', error: 'internal instruction for the model' },
      error: 'internal instruction for the model',
    }
    const created = {
      id: 'tool-created', tool_name: 'create_file', arguments: { title: '第一章' }, status: 'success' as const,
      result: { data: { id: 'ch-1', title: '第一章', file_type: 'draft', content: '', awaiting_body: true } },
    }
    const displayItems = [{ id: 'tools', type: 'tool_calls' as const, toolCalls: [created, refused], timestamp }]
    const { container, rerender } = render(<MessageList messages={[]} streamRenderItems={displayItems} />)
    expect(container.textContent).not.toMatch(/chat:tool\.failed/)
    expect(container.textContent).not.toMatch(/internal instruction/)
    expect(container.textContent).toMatch(/第一章/)

    rerender(<MessageList messages={[createMessage({ role: 'assistant', content: '', toolCalls: [created, refused], toolResults: [created, refused] })]} />)
    expect(container.textContent).not.toMatch(/chat:tool\.failed/)
    expect(container.textContent).toMatch(/第一章/)

    const savedChapter = {
      id: 'tool-write-ch1', tool_name: 'write_chapter', arguments: { chapter: 1, title: '第1章 开篇' }, status: 'success' as const,
      result: { data: { id: 'ch-1', title: '第1章 开篇', file_type: 'draft', chapter: 1, action: 'created', body_saved: true, new_word_count: 849 } },
    }
    const duplicateWriteChapter = {
      id: 'tool-dup-ch1', tool_name: 'write_chapter', arguments: { chapter: 1, title: '第1章 开篇' }, status: 'error' as const,
      result: { error_type: 'chapter_written_this_turn' },
      error: '第1章《第1章 开篇》的正文这一轮已经用 write_chapter 写好并保存了',
    }
    rerender(
      <MessageList
        messages={[]}
        streamRenderItems={[{ id: 'tools-ch1', type: 'tool_calls' as const, toolCalls: [savedChapter, duplicateWriteChapter], timestamp }]}
      />,
    )
    expect(container.textContent).not.toMatch(/chat:tool\.failed/)
    expect(container.textContent).toMatch(/第1章 开篇/)

    rerender(
      <MessageList
        messages={[createMessage({
          role: 'assistant',
          content: '第1章已写好，共 849 字。',
          toolCalls: [savedChapter, duplicateWriteChapter],
          toolResults: [savedChapter, duplicateWriteChapter],
        })]}
      />,
    )
    expect(container.textContent).not.toMatch(/chat:tool\.failed/)
    expect(container.textContent).toMatch(/第1章 开篇/)

    const wrapUpBlockedEdit = {
      id: 'tool-wrapup-edit', tool_name: 'edit_file', arguments: { id: 'ch-1' }, status: 'error' as const,
      result: { error_type: 'wall_clock_wrap_up', user_message: '本轮已进入收尾阶段，这一步留到下一轮继续。' },
      error: '本轮已进入收尾阶段，这一步留到下一轮继续。',
    }
    rerender(
      <MessageList
        messages={[]}
        streamRenderItems={[{ id: 'tools-wrapup', type: 'tool_calls' as const, toolCalls: [savedChapter, wrapUpBlockedEdit], timestamp }]}
      />,
    )
    expect(container.textContent).not.toMatch(/chat:tool\.failed/)
    expect(container.textContent).not.toMatch(/本轮已进入收尾阶段/)
    expect(container.textContent).toMatch(/第1章 开篇/)

    rerender(
      <MessageList
        messages={[createMessage({
          role: 'assistant',
          content: '已完成第1章，其余留待继续。',
          toolCalls: [savedChapter, wrapUpBlockedEdit],
          toolResults: [savedChapter, wrapUpBlockedEdit],
        })]}
      />,
    )
    expect(container.textContent).not.toMatch(/chat:tool\.failed/)
    expect(container.textContent).not.toMatch(/本轮已进入收尾阶段/)
    expect(container.textContent).toMatch(/第1章 开篇/)
  })

  it('hides guard_skipped and edit_repeat_limit soft-skipped edit_file cards regardless of call.status', () => {
    const timestamp = new Date()
    const realEdit = {
      id: 'tool-real-edit',
      tool_name: 'edit_file',
      arguments: { id: 'ch-1' },
      status: 'success' as const,
      result: { id: 'ch-1', title: '第1章 开篇', edits_applied: 1, mutation_applied: true },
    }
    const softSkippedEdit = {
      id: 'tool-repeat-skip',
      tool_name: 'edit_file',
      arguments: { id: 'ch-2' },
      status: 'success' as const,
      result: {
        status: 'skipped',
        guard_skipped: true,
        error_type: 'edit_repeat_limit',
        id: 'ch-2',
        title: '被跳过的微调',
        edits_applied: 0,
        mutation_applied: false,
        user_message: '这一步的细微调整已跳过，之前的修改都已保存。',
      },
    }
    const guardSkippedOnly = {
      id: 'tool-guard-skip-only',
      tool_name: 'edit_file',
      arguments: { id: 'ch-3' },
      status: 'success' as const,
      result: { data: { guard_skipped: true, title: '嵌套跳过' } },
    }
    const repeatLimitSuccess = {
      id: 'tool-repeat-success',
      tool_name: 'edit_file',
      arguments: { id: 'ch-4' },
      status: 'success' as const,
      result: { error_type: 'edit_repeat_limit', title: '重复限制无标记' },
    }

    const displayItems = [
      {
        id: 'tools-skip',
        type: 'tool_calls' as const,
        toolCalls: [realEdit, softSkippedEdit, guardSkippedOnly, repeatLimitSuccess],
        timestamp,
      },
    ]

    // Live stream
    const { container, rerender } = render(<MessageList messages={[]} streamRenderItems={displayItems} />)
    expect(screen.getAllByText('chat:tool.edit_success')).toHaveLength(1)
    expect(container.textContent).toContain('第1章 开篇')
    expect(container.textContent).not.toContain('被跳过的微调')
    expect(container.textContent).not.toContain('嵌套跳过')
    expect(container.textContent).not.toContain('重复限制无标记')
    expect(container.textContent).not.toContain('这一步的细微调整已跳过')

    // Saved displayItems
    rerender(<MessageList messages={[createMessage({ role: 'assistant', content: '已完成修改。', displayItems })]} />)
    expect(screen.getAllByText('chat:tool.edit_success')).toHaveLength(1)
    expect(container.textContent).toContain('第1章 开篇')
    expect(container.textContent).not.toContain('被跳过的微调')

    // Saved legacy toolCalls
    rerender(
      <MessageList
        messages={[createMessage({
          role: 'assistant',
          content: '',
          toolCalls: [realEdit, softSkippedEdit, guardSkippedOnly, repeatLimitSuccess],
        })]}
      />,
    )
    expect(screen.getAllByText('chat:tool.edit_success')).toHaveLength(1)
    expect(container.textContent).toContain('第1章 开篇')
    expect(container.textContent).not.toContain('被跳过的微调')

    // Saved legacy toolResults
    rerender(
      <MessageList
        messages={[createMessage({
          role: 'assistant',
          content: '',
          toolResults: [realEdit, softSkippedEdit, guardSkippedOnly, repeatLimitSuccess],
        })]}
      />,
    )
    expect(screen.getAllByText('chat:tool.edit_success')).toHaveLength(1)
    expect(container.textContent).toContain('第1章 开篇')
    expect(container.textContent).not.toContain('被跳过的微调')

    // Assistant message containing only a guard-skipped edit renders nothing
    rerender(
      <MessageList
        messages={[createMessage({
          role: 'assistant',
          content: '',
          toolResults: [softSkippedEdit],
        })]}
      />,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('labels citations by the real file type and shows the quoted file name', () => {
    const streamRenderItems = [
      {
        id: 'ctx-live',
        type: 'context' as const,
        timestamp: new Date(),
        items: [
          // 组装器把正文装成 type="outline"，真实类型在 metadata.file_type。
          { id: 'ctx-draft', type: 'outline', title: 'Chapter 3', content: 'Body', metadata: { file_type: 'draft' } },
          { id: 'ctx-script', type: 'outline', title: 'Episode 1', content: 'Body', metadata: { file_type: 'script' } },
          { id: 'ctx-quote', type: 'quote', title: '引用自: Chapter 2', content: 'Quoted', metadata: { file_title: 'Chapter 2', is_quote: true } },
        ],
      },
    ]
    render(<MessageList messages={[]} streamRenderItems={streamRenderItems} />)
    fireEvent.click(screen.getByText('context.label'))
    expect(screen.getByText('fileType.draft')).toBeInTheDocument()
    expect(screen.getByText('fileType.script')).toBeInTheDocument()
    expect(screen.queryByText('fileType.outline')).not.toBeInTheDocument()
    expect(screen.getByText('fileType.quote')).toBeInTheDocument()
    expect(screen.getByText('Chapter 2')).toBeInTheDocument()
    expect(screen.queryByText('引用自: Chapter 2')).not.toBeInTheDocument()
  })

  it('renders tool results', () => {
    const messages = [
      createMessage({
        role: 'assistant',
        content: '',
        toolResults: [
          {
            id: 'tool-1',
            tool_name: 'create_file',
            arguments: {},
            status: 'success',
            result: { title: 'Created File', file_type: 'draft' },
          },
        ],
      }),
    ]
    render(<MessageList messages={messages} />)
    // Tool results are rendered via ToolResultCard
  })

  it('forwards immutable edit provenance from saved tool results', () => {
    const onUndo = vi.fn()
    const messages = [
      createMessage({
        role: 'assistant',
        content: '',
        backendMessageId: 'assistant-1',
        toolResults: [{
          id: 'tool-edit',
          tool_name: 'edit_file',
          arguments: {},
          status: 'success',
          result: {
            data: {
              id: 'file-1',
              details: [],
              undo: {
                before_version_number: 4,
                expected_after_updated_at: '2026-10-06T12:34:56.000Z',
              },
            },
          },
        }],
      }),
    ]

    render(<MessageList messages={messages} onUndo={onUndo} />)
    fireEvent.click(screen.getByRole('button', { name: /common:undo/i }))

    expect(onUndo).toHaveBeenCalledWith({
      fileId: 'file-1',
      beforeVersionNumber: 4,
      expectedAfterUpdatedAt: '2026-10-06T12:34:56.000Z',
      chatMessageId: 'assistant-1',
      toolUseId: 'tool-edit',
    })
  })

  it('handles streaming content updates', () => {
    const { rerender } = render(
      <MessageList
        messages={[createMessage({ role: 'assistant', content: 'Hello' })]}
      />
    )
    expect(screen.getByText('Hello')).toBeInTheDocument()

    rerender(
      <MessageList
        messages={[createMessage({ role: 'assistant', content: 'Hello World' })]}
      />
    )
    expect(screen.getByText(/Hello World/)).toBeInTheDocument()
  })

  it('displays timestamps', () => {
    const timestamp = new Date('2024-01-01T12:00:00')
    const messages = [createMessage({ role: 'user', content: 'Test', timestamp })]
    render(<MessageList messages={messages} />)
    // Timestamp should be rendered (format varies by locale)
  })

  it('filters out invisible messages', () => {
    const messages = [
      createMessage({ role: 'assistant', content: '' }), // Empty content
      createMessage({ role: 'assistant', content: '<think thinking</think' }), // Only think tags
    ]
    render(<MessageList messages={messages} />)
    expect(screen.queryByText('chat:thinking.title')).not.toBeInTheDocument()
  })

  it('handles stream render items', () => {
    const streamRenderItems = [
      {
        type: 'content' as const,
        id: 'stream-1',
        content: 'Streaming content',
        timestamp: new Date(),
      },
    ]
    render(<MessageList messages={[]} streamRenderItems={streamRenderItems} />)
    expect(screen.getByText('Streaming content')).toBeInTheDocument()
  })

  it('handles stream render items with thinking', () => {
    const streamRenderItems = [
      {
        type: 'thinking_content' as const,
        id: 'think-1',
        content: 'AI is thinking...',
        timestamp: new Date(),
      },
    ]
    render(<MessageList messages={[]} streamRenderItems={streamRenderItems} />)
    // ThinkingContent is rendered with collapsible panel
    // The title is always visible
    expect(screen.getByText('chat:thinking.title')).toBeInTheDocument()
    // The content is inside a collapsible panel (collapsed by default in test env)
    // Just verify the ThinkingContent component is rendered by checking the title
  })

  it('handles stream render items with tool calls', () => {
    const streamRenderItems = [
      {
        type: 'tool_calls' as const,
        id: 'tool-stream-1',
        toolCalls: [
          {
            id: 'tool-1',
            tool_name: 'create_file',
            arguments: { title: 'Test' },
            status: 'pending',
          },
        ],
        timestamp: new Date(),
      },
    ]
    render(<MessageList messages={[]} streamRenderItems={streamRenderItems} />)
    // When status is 'pending', ToolResultCard shows 'chat:tool.create_file'
    expect(screen.getByText('chat:tool.create_file')).toBeInTheDocument()
  })

  it('handles onUndo callback', () => {
    const onUndo = vi.fn()
    const messages = [
      createMessage({
        role: 'assistant',
        content: '',
        toolCalls: [
          {
            id: 'tool-1',
            tool_name: 'edit_file',
            arguments: {},
            status: 'success',
            result: {
              id: 'file-1',
              details: [{ op: 'replace', old_preview: 'old', new_preview: 'new' }],
            },
          },
        ],
      }),
    ]
    render(<MessageList messages={messages} onUndo={onUndo} />)
    // ToolResultCard should be rendered
  })

  describe('message list rendering', () => {
    it('renders messages in stable document order', () => {
      const messages = [
        createMessage({ role: 'user', content: 'Hello' }),
        createMessage({ role: 'assistant', content: 'Hi there' }),
      ]
      const { container } = render(<MessageList messages={messages} />)

      expect(container.firstChild).toBeInTheDocument()

      expect(screen.getByText('Hello')).toBeInTheDocument()
      expect(screen.getByText('Hi there')).toBeInTheDocument()
    })

    it('renders messages without virtualization wrappers', () => {
      const messages = [
        createMessage({ role: 'user', content: 'Message 1' }),
        createMessage({ role: 'assistant', content: 'Message 2' }),
      ]
      const { container } = render(<MessageList messages={messages} />)

      expect(container.querySelectorAll('[data-index]').length).toBe(0)
      expect(screen.getByText('Message 1')).toBeInTheDocument()
      expect(screen.getByText('Message 2')).toBeInTheDocument()
    })

    it('exposes scrollToBottom method via ref', () => {
      let messageListRef: MessageListRef | null = null

      render(
        <MessageListWithRef
          messages={[createMessage({ role: 'user', content: 'Test' })]}
          onRef={(ref) => { messageListRef = ref }}
        />
      )

      // The ref should be available after render
      // Note: In tests without a proper DOM, the ref might be null initially
      // This test verifies the component doesn't crash when using ref
      expect(() => {
        if (messageListRef) {
          messageListRef.scrollToBottom()
        }
      }).not.toThrow()
    })

    it('handles scrollToBottom with smooth parameter', () => {
      let messageListRef: MessageListRef | null = null

      render(
        <MessageListWithRef
          messages={[createMessage({ role: 'user', content: 'Test' })]}
          onRef={(ref) => { messageListRef = ref }}
        />
      )

      // Test both smooth and instant scroll
      expect(() => {
        if (messageListRef) {
          messageListRef.scrollToBottom(true)
          messageListRef.scrollToBottom(false)
        }
      }).not.toThrow()
    })

    it('renders many messages (50+) without performance issues', () => {
      const messages = Array.from({ length: 60 }, (_, i) =>
        createMessage({
          id: `msg-${i}`,
          role: i % 2 === 0 ? 'user' : 'assistant',
          content: `Message ${i + 1}`,
        })
      )

      const { container } = render(<MessageList messages={messages} />)

      expect(container.firstChild).toBeInTheDocument()
      expect(screen.getByText('Message 1')).toBeInTheDocument()
      expect(screen.getByText('Message 60')).toBeInTheDocument()
    })

    it('handles rapid message updates efficiently', () => {
      const messages1 = [createMessage({ role: 'user', content: 'First' })]
      const messages2 = [
        createMessage({ role: 'user', content: 'First' }),
        createMessage({ role: 'assistant', content: 'Second' }),
      ]
      const messages3 = [
        createMessage({ role: 'user', content: 'First' }),
        createMessage({ role: 'assistant', content: 'Second' }),
        createMessage({ role: 'user', content: 'Third' }),
      ]

      const { rerender } = render(<MessageList messages={messages1} />)
      expect(screen.getByText('First')).toBeInTheDocument()

      rerender(<MessageList messages={messages2} />)
      expect(screen.getByText('First')).toBeInTheDocument()
      expect(screen.getByText('Second')).toBeInTheDocument()

      rerender(<MessageList messages={messages3} />)
      expect(screen.getByText('First')).toBeInTheDocument()
      expect(screen.getByText('Second')).toBeInTheDocument()
      expect(screen.getByText('Third')).toBeInTheDocument()
    })

    it('maintains scroll behavior with external scrollContainerRef', () => {
      const scrollContainerRef = { current: null }
      const messages = [createMessage({ role: 'user', content: 'Test' })]

      render(
        <MessageList
          messages={messages}
          scrollContainerRef={scrollContainerRef as React.RefObject<HTMLDivElement | null>}
        />
      )

      // Component should render without errors when using external ref
      expect(screen.getByText('Test')).toBeInTheDocument()
    })

    it('correctly filters and virtualizes only visible messages', () => {
      const messages = [
        createMessage({ role: 'user', content: 'Visible message 1' }),
        createMessage({ role: 'assistant', content: '' }), // Empty, should be filtered
        createMessage({ role: 'user', content: 'Visible message 2' }),
        createMessage({ role: 'assistant', content: '  ' }), // Whitespace only, should be filtered
        createMessage({ role: 'user', content: 'Visible message 3' }),
      ]

      render(<MessageList messages={messages} />)

      // Visible messages should be rendered
      expect(screen.getByText('Visible message 1')).toBeInTheDocument()
      expect(screen.getByText('Visible message 2')).toBeInTheDocument()
      expect(screen.getByText('Visible message 3')).toBeInTheDocument()
    })

    it('renders messages with complex content (tool calls, thinking)', () => {
      const timestamp = new Date()
      const tool = {
        id: 'tool-1',
        tool_name: 'create_file',
        arguments: { title: 'Test' },
        status: 'success' as const,
      }
      const messages = [
        createMessage({
          role: 'assistant',
          content: '',
          displayItems: [
            { id: 'think-1', type: 'thinking_content', content: 'AI is analyzing...', timestamp },
            { id: 'tools-1', type: 'tool_calls', toolCalls: [tool], timestamp },
            { id: 'reply-1', type: 'content', content: 'Response with thinking', timestamp },
          ],
        }),
        createMessage({
          role: 'assistant',
          content: 'Another response',
        }),
      ]

      const { container } = render(<MessageList messages={messages} />)

      expect(container.firstChild).toBeInTheDocument()

      // All content should be rendered
      expect(screen.getByText('Response with thinking')).toBeInTheDocument()
      expect(screen.getByText('Another response')).toBeInTheDocument()
      expect(screen.getByText('chat:thinking.title')).toBeInTheDocument()
      expect(screen.getByText('chat:tool.created')).toBeInTheDocument()
    })
  })

  describe('streaming with virtualization', () => {
    it('keeps streaming content outside virtualized container', () => {
      const messages = [createMessage({ role: 'user', content: 'User message' })]
      const streamRenderItems = [
        {
          type: 'content' as const,
          id: 'stream-1',
          content: 'Streaming content',
          timestamp: new Date(),
        },
      ]

      const { container } = render(
        <MessageList messages={messages} streamRenderItems={streamRenderItems} />
      )

      // Both virtualized messages and stream items should be present
      expect(screen.getByText('User message')).toBeInTheDocument()
      expect(screen.getByText('Streaming content')).toBeInTheDocument()

      // Stream items container should have a specific key
      const streamItemsContainer = container.querySelector('[key="stream-items"]') ||
        Array.from(container.querySelectorAll('div')).find(div =>
          div.textContent?.includes('Streaming content')
        )
      expect(streamItemsContainer).toBeTruthy()
    })
  })

  describe('copy AI reply action', () => {
    it('copies assistant reply text (stripping think tags) and shows reversible copied state', async () => {
      const writeText = vi.fn().mockResolvedValue(undefined)
      Object.defineProperty(navigator, 'clipboard', {
        value: { writeText },
        configurable: true,
      })
      const messages = [
        createMessage({ role: 'user', content: '帮我写一段开场' }),
        createMessage({ role: 'assistant', content: '</think>窗外雨声淅沥。[TASK_COMPLETE]' }),
      ]
      render(<MessageList messages={messages} />)

      const copyButtons = screen.getAllByTestId('copy-message-button')
      expect(copyButtons).toHaveLength(1)
      expect(copyButtons[0]).toHaveAttribute('aria-label', 'message.copyReply')

      fireEvent.click(copyButtons[0])
      await waitFor(() => expect(writeText).toHaveBeenCalledWith('窗外雨声淅沥。'))
      await waitFor(() => expect(copyButtons[0]).toHaveAttribute('aria-label', 'message.copiedReply'))
    })
  })

  describe('read-only tool progress grouping', () => {
    it('folds >=2 consecutive successful read-only tools across adjacent tool_calls items while keeping writes, pending, and errors exposed', () => {
      const ts = new Date('2026-11-24T03:00:00Z')
      const displayItems = [
        {
          id: 'tc-1',
          type: 'tool_calls' as const,
          timestamp: ts,
          toolCalls: [
            { id: 'q1', tool_name: 'query_files', arguments: {}, status: 'success' as const, result: { files: [{ id: 'f1', title: '人物小传' }] } },
          ],
        },
        {
          id: 'tc-2',
          type: 'tool_calls' as const,
          timestamp: ts,
          toolCalls: [
            { id: 'q2', tool_name: 'query_files', arguments: {}, status: 'success' as const, result: { files: [{ id: 'f2', title: '大纲' }] } },
            { id: 's1', tool_name: 'load_skill', arguments: { name: '去AI味' }, status: 'success' as const, result: { name: '去AI味' } },
          ],
        },
        {
          id: 'tc-3',
          type: 'tool_calls' as const,
          timestamp: ts,
          toolCalls: [
            { id: 'w1', tool_name: 'create_file', arguments: {}, status: 'success' as const, result: { id: 'f3', title: '第一章 雨夜' } },
            { id: 'q3', tool_name: 'query_files', arguments: {}, status: 'error' as const, error: 'not found' },
            { id: 'q4', tool_name: 'query_files', arguments: {}, status: 'success' as const, result: { files: [{ id: 'f4', title: '设定集' }] } },
          ],
        },
      ]

      render(<MessageList messages={[createMessage({ role: 'assistant', content: '', displayItems })]} />)

      // 3 consecutive successful read-only calls collapse into 1 group; write, error, and trailing single read-only stay exposed
      const groups = screen.getAllByTestId('readonly-tool-group')
      expect(groups).toHaveLength(1)
      const toggle = groups[0].querySelector('button[aria-expanded]')!
      expect(toggle).toHaveAttribute('aria-expanded', 'false')

      // Write card and error card remain directly visible outside the group
      expect(screen.getByText(/第一章 雨夜/)).toBeInTheDocument()
      expect(screen.getByText('chat:tool.failed')).toBeInTheDocument()

      // Clicking expands to reveal the grouped read-only ToolResultCards
      fireEvent.click(toggle)
      expect(toggle).toHaveAttribute('aria-expanded', 'true')
    })

    it('folds consecutive successful read_files and search_files calls into one read-only group while keeping batch_replace_files exposed', () => {
      const ts = new Date('2026-11-26T03:00:00Z')
      const displayItems = [
        {
          id: 'tc-ro-new',
          type: 'tool_calls' as const,
          timestamp: ts,
          toolCalls: [
            { id: 'rf-1', tool_name: 'read_files', arguments: { ids: ['f1'] }, status: 'success' as const, result: { files: [{ id: 'f1', title: '第一章' }] } },
            { id: 'sf-1', tool_name: 'search_files', arguments: { query: '青铜镜' }, status: 'success' as const, result: { total_matches: 2, matched_files: 1, files: [{ id: 'f1', title: '第一章', match_count: 2 }] } },
            { id: 'br-1', tool_name: 'batch_replace_files', arguments: { old_text: '旧名', new_text: '新名' }, status: 'success' as const, result: { dry_run: false, total_matches: 2, files_matched: 1, files_modified: 1, modified_files: [{ id: 'f1', title: '第一章', replacements: 2 }] } },
          ],
        },
      ]

      render(<MessageList messages={[createMessage({ role: 'assistant', content: '', displayItems })]} />)

      expect(screen.getAllByTestId('readonly-tool-group')).toHaveLength(1)
      expect(screen.getByText('chat:tool.batch_replace_done_many_in_one')).toBeInTheDocument()
    })

    it('never folds pending read-only tools while streaming', () => {
      const ts = new Date('2026-11-24T03:00:00Z')
      const streamItems = [
        {
          id: 'live-tc',
          type: 'tool_calls' as const,
          timestamp: ts,
          toolCalls: [
            { id: 'p1', tool_name: 'query_files', arguments: {}, status: 'pending' as const },
            { id: 'p2', tool_name: 'load_skill', arguments: { name: '古风润色' }, status: 'pending' as const },
          ],
        },
      ]

      render(<MessageList messages={[]} streamRenderItems={streamItems} />)
      expect(screen.queryByTestId('readonly-tool-group')).not.toBeInTheDocument()
    })

    it('groups consecutive read-only legacy toolResults and renders RoundEndNote + mobile read-written-files chronologically after tool cards', () => {
      const wrote = createMessage({
        role: 'assistant',
        content: '已微调第二章末段。',
        backendMessageId: 'a-legacy-order',
        toolResults: [
          { id: 'r1', tool_name: 'query_files', arguments: {}, status: 'success', result: [{ id: 'f1', title: '大纲' }] },
          { id: 'r2', tool_name: 'hybrid_search', arguments: {}, status: 'success', result: [{ id: 'f2', title: '卷宗' }] },
          { id: 'w1', tool_name: 'edit_file', arguments: {}, status: 'success', result: { id: 'f3', title: '第二章 铜雀密信', applied_count: 1 } },
        ],
        stopOutcome: {
          reason: 'completed',
          charged: true,
          savedOutput: true,
          removedFiles: [],
          writtenFiles: [{ id: 'f3', title: '第二章 铜雀密信', words: 2510 }],
        },
      })

      render(
        <MobileLayoutProvider isMobile>
          <MessageList messages={[createMessage({ content: '帮我微调' }), wrote]} />
        </MobileLayoutProvider>,
      )

      const readonlyGroup = screen.getByTestId('readonly-tool-group')
      const editTitle = screen.getByText('第二章 铜雀密信')
      const roundEnd = screen.getByTestId('round-end-note')
      const readButton = screen.getByTestId('read-written-files')

      // Read-only group precedes the edit card, and both precede the round-end receipt & mobile jump pill
      expect(readonlyGroup.compareDocumentPosition(editTitle) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
      expect(editTitle.compareDocumentPosition(roundEnd) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
      expect(editTitle.compareDocumentPosition(readButton) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    })
  })

  describe('active streaming thinking and tool-arg status UX', () => {
    it('passes isStreaming=true only to the latest active thinking block, not earlier completed ones', () => {
      const ts = new Date('2026-11-25T10:00:00Z')
      const streamItems = [
        { id: 'think-1', type: 'thinking_content' as const, content: '先读一遍大纲，确认人物动机', timestamp: ts },
        {
          id: 'tool-1',
          type: 'tool_calls' as const,
          toolCalls: [
            { id: 'tc-1', tool_name: 'query_files', arguments: {}, status: 'success' as const, result: { files: [] } },
          ],
          timestamp: ts,
        },
        { id: 'think-2', type: 'thinking_content' as const, content: '大纲已读完，准备构思第二章冲突', timestamp: ts },
      ]

      render(<MessageList messages={[]} streamRenderItems={streamItems} />)

      const thinkingButtons = screen.getAllByRole('button', { name: 'chat:thinking.expand' })
      expect(thinkingButtons).toHaveLength(2)
      // Earlier thinking block is finalized: no pulsing dots, no live preview.
      expect(thinkingButtons[0].querySelectorAll('.animate-pulse')).toHaveLength(0)
      expect(thinkingButtons[0].textContent).not.toContain('先读一遍大纲')
      // Latest active thinking block pulses and shows its live rolling preview.
      expect(thinkingButtons[1].querySelectorAll('.animate-pulse')).toHaveLength(3)
      expect(thinkingButtons[1].textContent).toContain('大纲已读完，准备构思第二章冲突')
    })

    it('stops pulsing an earlier thinking block once prose, tool calls, or tool-arg prep status follows it', () => {
      const ts = new Date('2026-11-25T10:00:00Z')
      const { rerender } = render(
        <MessageList
          messages={[]}
          streamRenderItems={[
            { id: 'think-1', type: 'thinking_content' as const, content: '构思修改方案', timestamp: ts },
            // Empty content placeholder must not prematurely finalize thinking.
            { id: 'empty-seg', type: 'content' as const, content: '', timestamp: ts },
          ]}
        />,
      )

      let thinkingButton = screen.getByRole('button', { name: 'chat:thinking.expand' })
      expect(thinkingButton.querySelectorAll('.animate-pulse')).toHaveLength(3)
      expect(screen.getByTestId('thinking-live-preview')).toHaveTextContent('构思修改方案')

      // Once a transient tool-arg preparation status arrives after thinking, thinking stops pulsing.
      rerender(
        <MessageList
          messages={[]}
          streamRenderItems={[
            { id: 'think-1', type: 'thinking_content' as const, content: '构思修改方案', timestamp: ts },
            { id: 'prep-1', type: 'thinking_status' as const, content: '正在准备编辑文件…', transient: true, timestamp: ts },
          ]}
        />,
      )
      thinkingButton = screen.getByRole('button', { name: 'chat:thinking.expand' })
      expect(thinkingButton.querySelectorAll('.animate-pulse')).toHaveLength(0)
      expect(screen.queryByTestId('thinking-live-preview')).not.toBeInTheDocument()
      expect(screen.getByTestId('active-thinking-status')).toHaveTextContent('正在准备编辑文件…')

      // Once visible prose arrives, thinking stays finalized too.
      rerender(
        <MessageList
          messages={[]}
          streamRenderItems={[
            { id: 'think-1', type: 'thinking_content' as const, content: '构思修改方案', timestamp: ts },
            { id: 'seg-1', type: 'content' as const, content: '好的，我来修改这一段。', timestamp: ts },
          ]}
        />,
      )
      thinkingButton = screen.getByRole('button', { name: 'chat:thinking.expand' })
      expect(thinkingButton.querySelectorAll('.animate-pulse')).toHaveLength(0)
      expect(screen.queryByTestId('thinking-live-preview')).not.toBeInTheDocument()
    })

    it('collapses consecutive transient tool-arg preparation updates in place while preserving non-transient statuses', () => {
      const ts = new Date('2026-11-25T10:00:00Z')
      const { rerender } = render(
        <MessageList
          messages={[]}
          streamRenderItems={[
            { id: 'handoff-1', type: 'thinking_status' as const, content: '接下来由内容创作者继续', timestamp: ts },
            { id: 'prep-1', type: 'thinking_status' as const, content: '正在准备编辑文件…', transient: true, timestamp: ts },
            { id: 'prep-2', type: 'thinking_status' as const, content: '正在准备编辑文件…（已生成 1,500 字）', transient: true, timestamp: ts },
            { id: 'prep-3', type: 'thinking_status' as const, content: '正在准备编辑文件…（已生成 3,000 字）', transient: true, timestamp: ts },
          ]}
        />,
      )

      // Non-transient handoff stays visible alongside the single collapsed live prep status.
      expect(screen.getByText('接下来由内容创作者继续')).toBeInTheDocument()
      expect(screen.queryByText('正在准备编辑文件…')).not.toBeInTheDocument()
      expect(screen.queryByText('正在准备编辑文件…（已生成 1,500 字）')).not.toBeInTheDocument()
      const activeStatus = screen.getByTestId('active-thinking-status')
      expect(activeStatus).toHaveTextContent('正在准备编辑文件…（已生成 3,000 字）')
      expect(activeStatus.querySelector('.animate-spin')).toBeInTheDocument()

      // Once tool_calls starts, superseded transient prep status disappears while non-transient handoff remains.
      rerender(
        <MessageList
          messages={[]}
          streamRenderItems={[
            { id: 'handoff-1', type: 'thinking_status' as const, content: '接下来由内容创作者继续', timestamp: ts },
            { id: 'prep-1', type: 'thinking_status' as const, content: '正在准备编辑文件…', transient: true, timestamp: ts },
            { id: 'prep-3', type: 'thinking_status' as const, content: '正在准备编辑文件…（已生成 3,000 字）', transient: true, timestamp: ts },
            {
              id: 'tool-1',
              type: 'tool_calls' as const,
              toolCalls: [{ id: 'tc-1', tool_name: 'edit_file', arguments: {}, status: 'pending' as const }],
              timestamp: ts,
            },
          ]}
        />,
      )
      expect(screen.queryByTestId('active-thinking-status')).not.toBeInTheDocument()
      expect(screen.queryByText('正在准备编辑文件…（已生成 3,000 字）')).not.toBeInTheDocument()
      expect(screen.getByText('接下来由内容创作者继续')).toBeInTheDocument()
    })

    it('hides superseded transient status pills when context, thinking, or prose arrive', () => {
      const ts = new Date('2026-11-27T10:00:00Z')
      const attachedCtx = {
        id: 'ctx-1',
        type: 'context' as const,
        timestamp: ts,
        items: [
          {
            id: 'file-3',
            type: 'outline',
            title: '第3章大纲',
            content: '第三章核心冲突',
            priority: 'critical' as const,
            metadata: { file_type: 'outline', attached: true, is_focus: false },
          },
        ],
      }

      // 1. Initially active while assembling context
      const { rerender } = render(
        <MessageList
          messages={[]}
          streamRenderItems={[
            { id: 't-1', type: 'thinking_status' as const, content: '正在组装上下文...', transient: true, timestamp: ts },
          ]}
        />,
      )
      expect(screen.getByTestId('active-thinking-status')).toHaveTextContent('正在组装上下文...')

      // 2. Once visible context arrives, 正在组装上下文... disappears
      rerender(
        <MessageList
          messages={[]}
          streamRenderItems={[
            { id: 't-1', type: 'thinking_status' as const, content: '正在组装上下文...', transient: true, timestamp: ts },
            attachedCtx,
          ]}
        />,
      )
      expect(screen.queryByText('正在组装上下文...')).not.toBeInTheDocument()
      expect(screen.getByText('context.label')).toBeInTheDocument()

      // 3. Once 正在思考... follows after context, only 正在思考... is active; 正在组装上下文... stays gone
      rerender(
        <MessageList
          messages={[]}
          streamRenderItems={[
            { id: 't-1', type: 'thinking_status' as const, content: '正在组装上下文...', transient: true, timestamp: ts },
            attachedCtx,
            { id: 't-2', type: 'thinking_status' as const, content: '正在思考...', transient: true, timestamp: ts },
          ]}
        />,
      )
      expect(screen.queryByText('正在组装上下文...')).not.toBeInTheDocument()
      expect(screen.getByTestId('active-thinking-status')).toHaveTextContent('正在思考...')

      // 4. Once thinking_content or prose follows, 正在思考... disappears too
      rerender(
        <MessageList
          messages={[]}
          streamRenderItems={[
            { id: 't-1', type: 'thinking_status' as const, content: '正在组装上下文...', transient: true, timestamp: ts },
            attachedCtx,
            { id: 't-2', type: 'thinking_status' as const, content: '正在思考...', transient: true, timestamp: ts },
            { id: 'tc-1', type: 'thinking_content' as const, content: '先读取第3章大纲', timestamp: ts },
          ]}
        />,
      )
      expect(screen.queryByText('正在组装上下文...')).not.toBeInTheDocument()
      expect(screen.queryByText('正在思考...')).not.toBeInTheDocument()
      expect(screen.queryByTestId('active-thinking-status')).not.toBeInTheDocument()
    })

    it('hides auto-injected is_focus-only context items while keeping explicitly attached or quoted items', () => {
      const ts = new Date('2026-11-27T10:00:00Z')
      const focusOnlyItem = {
        id: 'ch-1',
        type: 'outline',
        title: '第1章 天降剑仙',
        content: '第一章大纲内容',
        priority: 'critical' as const,
        metadata: { file_type: 'outline', is_focus: true, relation: null },
      }

      // Auto-injected focus-only item alone renders no context card at all
      const { container, rerender } = render(
        <MessageList
          messages={[]}
          streamRenderItems={[
            { id: 'ctx-focus', type: 'context' as const, timestamp: ts, items: [focusOnlyItem] },
          ]}
        />,
      )
      expect(container).toBeEmptyDOMElement()

      // When a hidden focus-only context sits between 正在组装上下文... and 正在思考..., no zombie pill remains
      rerender(
        <MessageList
          messages={[]}
          streamRenderItems={[
            { id: 't-1', type: 'thinking_status' as const, content: '正在组装上下文...', transient: true, timestamp: ts },
            { id: 'ctx-focus', type: 'context' as const, timestamp: ts, items: [focusOnlyItem] },
            { id: 't-2', type: 'thinking_status' as const, content: '正在思考...', transient: true, timestamp: ts },
          ]}
        />,
      )
      expect(screen.queryByText('正在组装上下文...')).not.toBeInTheDocument()
      expect(screen.queryByText('context.label')).not.toBeInTheDocument()
      expect(screen.getByTestId('active-thinking-status')).toHaveTextContent('正在思考...')

      // If the same open file is ALSO explicitly @-attached (is_focus + attached), it IS shown
      rerender(
        <MessageList
          messages={[]}
          streamRenderItems={[
            {
              id: 'ctx-both',
              type: 'context' as const,
              timestamp: ts,
              items: [
                {
                  ...focusOnlyItem,
                  metadata: { file_type: 'outline', is_focus: true, attached: true, relation: 'attached' },
                },
              ],
            },
          ]}
        />,
      )
      const toggle = screen.getByRole('button', { name: /context\.label/ })
      expect(toggle).toHaveTextContent('(1)')
      fireEvent.click(toggle)
      expect(screen.getByText('第1章 天降剑仙')).toBeInTheDocument()
      expect(screen.getByText('context.origin.attached')).toBeInTheDocument()
    })

    it('renders explicit attachments and quotes in single-border button cards with neutral origin badges and no details/summary or error styling', () => {
      const ts = new Date('2026-11-27T10:00:00Z')
      const { container } = render(
        <MessageList
          messages={[]}
          streamRenderItems={[
            {
              id: 'ctx-explicit',
              type: 'context' as const,
              timestamp: ts,
              items: [
                {
                  id: 'focus-hidden',
                  type: 'outline',
                  title: '第1章 天降剑仙',
                  content: '未附加的当前打开文件',
                  priority: 'critical' as const,
                  metadata: { file_type: 'outline', is_focus: true },
                },
                {
                  id: 'attached-ch3',
                  type: 'outline',
                  title: '第3章 伏笔回收',
                  content: '第三章大纲正文内容',
                  priority: 'critical' as const,
                  metadata: { file_type: 'outline', attached: true, is_focus: false },
                },
                {
                  id: 'quote-1',
                  type: 'quote',
                  title: '引用自: 第2章 试剑',
                  content: '选中的精彩段落',
                  priority: 'critical' as const,
                  metadata: { file_title: '第2章 试剑', is_quote: true },
                },
              ],
            },
          ]}
        />,
      )

      // Only the 2 explicit items are counted and shown (auto-focus item is excluded)
      const sectionToggle = screen.getByRole('button', { name: /context\.label/ })
      expect(sectionToggle).toHaveAttribute('aria-expanded', 'false')
      expect(sectionToggle).toHaveTextContent('(2)')

      fireEvent.click(sectionToggle)
      expect(sectionToggle).toHaveAttribute('aria-expanded', 'true')
      expect(screen.queryByText('第1章 天降剑仙')).not.toBeInTheDocument()

      // No native <details> or <summary> elements
      expect(container.querySelector('details')).toBeNull()
      expect(container.querySelector('summary')).toBeNull()

      // Neutral origin badges only — zero red error badge styling or legacy priority.critical label
      expect(screen.getByText('context.origin.attached')).toBeInTheDocument()
      expect(screen.getByText('context.origin.quote')).toBeInTheDocument()
      expect(screen.queryByText('context.priority.critical')).not.toBeInTheDocument()
      expect(container.innerHTML).not.toContain('var(--error)')

      // Item triggers are accessible buttons with aria-expanded and inset focus ring
      const attachedButton = screen.getByRole('button', { name: /第3章 伏笔回收/ })
      expect(attachedButton).toHaveAttribute('aria-expanded', 'false')
      expect(attachedButton.className).toContain('focus-visible:ring-inset')

      fireEvent.click(attachedButton)
      expect(attachedButton).toHaveAttribute('aria-expanded', 'true')
      expect(screen.getByText('第三章大纲正文内容')).toBeInTheDocument()

      // Expanded body uses a flat top divider inside the single outer card (no nested bordered card)
      const card = attachedButton.parentElement as HTMLElement
      expect(card.querySelectorAll('.border-t').length).toBe(1)
      expect(card.querySelector('.rounded-lg.border')).toBeNull()
    })
  })

  describe('UI/UX polish: Feather icons and flush-aligned chat controls', () => {
    it('uses Feather instead of Bot on agent role badges and clarification cards, and avoids negative left margins on footer/group controls', () => {
      const timestamp = new Date()
      const { container } = render(
        <MessageList
          messages={[
            createMessage({
              role: 'assistant',
              content: '已完成章节润色。',
              displayItems: [
                { id: 'agent-1', type: 'agent_selected', agentType: 'writer', agentName: '内容创作者', timestamp },
                {
                  id: 'reads-1',
                  type: 'tool_calls',
                  toolCalls: [
                    { id: 'r1', tool_name: 'read_files', arguments: {}, status: 'success', result: { data: { files: [] } } },
                    { id: 'r2', tool_name: 'query_files', arguments: {}, status: 'success', result: { data: { files: [] } } },
                  ],
                  timestamp,
                },
                { id: 'content-1', type: 'content', content: '已完成章节润色。', timestamp },
                { id: 'stop-1', type: 'workflow_stopped', reason: 'clarification_needed', question: '要继续写下一章吗？', timestamp },
              ],
            }),
          ]}
        />,
      )

      // Feather replaces Bot on agent_selected pill and clarification card
      expect(container.querySelectorAll('svg.lucide-feather').length).toBe(2)
      expect(container.querySelector('svg.lucide-bot')).toBeNull()

      // Copy footer wrapper and ReadOnlyToolGroup toggle stay flush-aligned (no negative left margin, inset focus rings)
      const copyButton = screen.getByTestId('copy-message-button')
      expect(copyButton.className).toContain('focus-visible:ring-inset')
      expect(copyButton.className).not.toContain('focus-visible:ring-offset-1')
      const footerRow = copyButton.parentElement?.parentElement as HTMLElement
      expect(footerRow.className).not.toContain('-ml-1')

      const readonlyToggle = screen.getByTestId('readonly-tool-group').querySelector('button') as HTMLElement
      expect(readonlyToggle.className).not.toContain('-ml-1.5')
      expect(readonlyToggle.className).toContain('focus-visible:ring-inset')
    })
  })
})
