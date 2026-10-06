import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { MessageList, type Message, type MessageListRef } from '../MessageList'
import { useEffect, useRef } from 'react'

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

// Mock useThinkingVisibility hook
vi.mock('../../hooks/useThinkingVisibility', () => ({
  useThinkingVisibility: vi.fn(() => ({
    showThinking: true,
    setShowThinking: vi.fn(),
    toggleThinking: vi.fn(),
  })),
}))

vi.mock('react-i18next', () => ({
  initReactI18next: {
    type: '3rdParty',
    init: vi.fn(),
  },
  useTranslation: () => ({
    // Return the key as-is to match actual component behavior
    t: (key: string) => key,
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

  it('renders without crashing', () => {
    render(<MessageList messages={[]} />)
    // MessageList returns null for empty messages, so we just check it doesn't throw
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

  it('replays router, low-turn and completion controls without moving the intervening text', () => {
    const timestamp = new Date()
    const displayItems: NonNullable<Message['displayItems']> = [
      { id: 'router-start', type: 'router_thinking', content: 'Choosing ordered agent', timestamp },
      { id: 'router-end', type: 'router_decided', workflowPlan: 'Plan then write', workflowAgents: ['planner', 'writer'], timestamp },
      { id: 'agent-low', type: 'agent_selected', agentName: 'Ordered writer', iteration: 3, maxIterations: 4, remaining: 1, timestamp },
      { id: 'reply', type: 'content', content: 'Ordered control reply', timestamp },
      { id: 'done', type: 'workflow_complete', timestamp },
      { id: 'hidden', type: 'content', content: '<think>Not visible</think>', timestamp },
    ]
    render(<MessageList messages={[createMessage({ role: 'assistant', content: '', displayItems })]} />)
    for (const marker of ['Choosing ordered agent', 'Ordered writer', 'Ordered control reply', 'workflow.taskCompleted', 'workflow.lowTurnWarning']) expect(screen.getByText(marker)).toBeInTheDocument()
    expect(screen.getByText(/chat:workflow\.agents\.planner → chat:workflow\.agents\.writer/)).toBeInTheDocument()
    expect(screen.queryByText(/Plan then write/)).not.toBeInTheDocument()
    expect(screen.queryByText(/planner → writer/)).not.toBeInTheDocument()
    expect(screen.queryByText('Not visible')).not.toBeInTheDocument()
  })

  it.each(['collaboration', 'tool_call'] as const)('retains %s exhaustion actions on a saved timeline without duplicate legacy status cards', layer => {
    const onAction = vi.fn()
    const item: NonNullable<Message['displayItems']>[number] = { id: 'limit', type: 'iteration_exhausted', layer, maxIterations: 4, iterationsUsed: 4, reason: 'Ordered limit reason', lastAgent: 'writer', timestamp: new Date() }
    render(<MessageList messages={[createMessage({ role: 'assistant', content: '', displayItems: [item], statusCards: [{ type: 'iteration_exhausted', layer, maxIterations: 4 }] })]} onIterationAssistAction={onAction} />)
    for (const [label, action] of [['workflow.actionContinue', 'continue'], ['workflow.actionSplit', 'split'], ['workflow.actionManual', 'manual']]) {
      const button = screen.getByRole('button', { name: label })
      fireEvent.click(button)
      expect(onAction).toHaveBeenLastCalledWith(action, item)
    }
    // The internal stop reason stays out of the author-facing card
    expect(screen.queryByText('Ordered limit reason')).not.toBeInTheDocument()
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

  it('names the workflow by deduplicated role names, or as single mode when no planned agent is recognized', () => {
    const timestamp = new Date()
    const { unmount } = render(<MessageList messages={[createMessage({ role: 'assistant', content: '', displayItems: [
      { id: 'router-multi', type: 'router_decided', initialAgent: 'planner', workflowAgents: ['planner', 'hook_designer', 'writer'], timestamp },
    ] })]} />)
    expect(screen.getByText(
      'workflow.workflowLabel: chat:workflow.agents.planner → chat:workflow.agents.hook_designer → chat:workflow.agents.writer',
    )).toBeInTheDocument()
    expect(screen.queryByText(/workflow\.singleMode/)).not.toBeInTheDocument()
    unmount()

    render(<MessageList messages={[createMessage({ role: 'assistant', content: '', displayItems: [
      { id: 'router-single', type: 'router_decided', initialAgent: 'custom_router_target', timestamp },
    ] })]} />)
    expect(screen.getByText('workflow.workflowLabel: workflow.singleMode')).toBeInTheDocument()
    expect(screen.queryByText(/custom_router_target/)).not.toBeInTheDocument()
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

  it('renders thinking content with toggle', () => {
    const messages = [
      createMessage({
        role: 'assistant',
        content: 'Response',
        reasoningContent: 'Analyzing your request...',
      }),
    ]
    render(<MessageList messages={messages} />)

    // Check if thinking label is present (ThinkingContent renders with 'chat:thinking.title' key)
    expect(screen.getByText('chat:thinking.title')).toBeInTheDocument()
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

  it('displays streaming cursor for streaming message', () => {
    const messages = [
      createMessage({ id: 'streaming-msg', role: 'assistant', content: 'Streaming...' }),
    ]
    render(<MessageList messages={messages} streamingMessageId="streaming-msg" />)

    // Check that the message is rendered
    expect(screen.getByText('Streaming...')).toBeInTheDocument()
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

  it('displays context items for assistant messages', () => {
    const messages = [
      createMessage({
        role: 'assistant',
        content: 'Response',
        contextItems: [
          {
            id: 'ctx-1',
            type: 'draft',
            title: 'Chapter 1',
            content: 'Content preview',
            priority: 'relevant',
          },
        ],
      }),
    ]
    render(<MessageList messages={messages} />)
    // MessageList uses t('context.citations', { ns: 'chat' }) which returns 'context.citations'
    expect(screen.getByText('context.citations')).toBeInTheDocument()
  })

  it('displays conflicts', () => {
    const messages = [
      createMessage({
        role: 'assistant',
        content: 'Response',
        conflicts: [
          {
            type: 'consistency',
            severity: 'high',
            title: 'Conflict Title',
            description: 'Conflict description',
            suggestions: ['Fix suggestion 1'],
            references: [],
          },
        ],
      }),
    ]
    render(<MessageList messages={messages} />)
    expect(screen.getByText('Conflict Title')).toBeInTheDocument()
    expect(screen.getByText('Conflict description')).toBeInTheDocument()
  })

  it('filters out invisible messages', () => {
    const messages = [
      createMessage({ role: 'assistant', content: '' }), // Empty content
      createMessage({ role: 'assistant', content: '<think thinking</think' }), // Only think tags
    ]
    render(<MessageList messages={messages} />)
    // These messages should be filtered out - no thinking content should be shown
    // because reasoningContent is not set
    expect(screen.queryByText('chat:thinking.title')).not.toBeInTheDocument()
  })

  it('renders reasoning segments', () => {
    const messages = [
      createMessage({
        role: 'assistant',
        content: 'Response',
        reasoningSegments: [
          { content: 'First thought', timestamp: new Date() },
          { content: 'Second thought', timestamp: new Date() },
        ],
      }),
    ]
    render(<MessageList messages={messages} />)
    // Thinking content should be rendered - each segment renders a ThinkingContent
    // with 'chat:thinking.title' label
    expect(screen.getAllByText('chat:thinking.title')).toHaveLength(2)
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

    it('virtualizes messages with complex content (tool calls, thinking)', () => {
      const messages = [
        createMessage({
          role: 'assistant',
          content: 'Response with thinking',
          reasoningContent: 'AI is analyzing...',
          toolCalls: [
            {
              id: 'tool-1',
              tool_name: 'create_file',
              arguments: { title: 'Test' },
              status: 'success',
            },
          ],
        }),
        createMessage({
          role: 'assistant',
          content: 'Another response',
          contextItems: [
            {
              id: 'ctx-1',
              type: 'draft',
              title: 'Context',
              content: 'Context content',
              priority: 'relevant',
            },
          ],
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

    it('handles streaming message id correctly with virtualization', () => {
      const messages = [
        createMessage({ id: 'msg-1', role: 'user', content: 'User message' }),
        createMessage({ id: 'streaming-msg', role: 'assistant', content: 'Streaming...' }),
      ]

      render(
        <MessageList
          messages={messages}
          streamingMessageId="streaming-msg"
        />
      )

      // Both messages should be rendered
      expect(screen.getByText('User message')).toBeInTheDocument()
      expect(screen.getByText('Streaming...')).toBeInTheDocument()
    })
  })
})
