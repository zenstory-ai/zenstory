import { createContext, useContext } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MessageList, type Message } from '../MessageList'
import type { StreamRenderItem } from '../../hooks/useChatStreaming'

const markdownRenders = vi.hoisted(() => new Map<string, number>())
const authContext = createContext<{ user: { username: string; avatar_url: string } | null }>({ user: null })
const mobileContext = createContext({ isMobile: false })
const languageContext = createContext('zh')

vi.mock('../LazyMarkdown', () => ({
  LazyMarkdown: ({ children }: { children: string }) => {
    markdownRenders.set(children, (markdownRenders.get(children) ?? 0) + 1)
    return <div>{children}</div>
  },
}))
vi.mock('../../contexts/AuthContext', () => ({ useAuth: () => useContext(authContext) }))
vi.mock('../../contexts/MobileLayoutContext', () => ({ useMobileLayout: () => useContext(mobileContext) }))
vi.mock('react-i18next', () => ({
  initReactI18next: { type: '3rdParty', init: vi.fn() },
  useTranslation: () => {
    const language = useContext(languageContext)
    return { t: (key: string) => `${language}:${key}` }
  },
}))

const timestamp = new Date('2026-01-01T12:00:00Z')
const message = (id: string, overrides: Partial<Message> = {}): Message => ({
  id, role: 'assistant', content: `Historical ${id}`, timestamp, backendMessageId: id, ...overrides,
})
const stream = (content: string): StreamRenderItem[] => [{ id: 'live', type: 'content', content, timestamp }]
const count = (content: string) => markdownRenders.get(content) ?? 0

beforeEach(() => markdownRenders.clear())

describe('MessageList history render isolation', () => {
  it('does not re-execute unchanged history during twenty stream updates', () => {
    const messages = Array.from({ length: 100 }, (_, index) => message(String(index)))
    const { rerender } = render(<MessageList messages={messages} streamingMessageId="live" streamRenderItems={stream('Live 0')} />)
    for (let frame = 1; frame <= 20; frame++) {
      rerender(<MessageList messages={messages} streamingMessageId="live" streamRenderItems={stream(`Live ${frame}`)} />)
    }
    const historyExecutions = messages.reduce((total, item) => total + count(item.content), 0)
    process.stdout.write(`PERF_CHAT_HISTORY ${JSON.stringify({ historyRows: 100, streamUpdates: 20, historyExecutions })}\n`)
    expect(historyExecutions).toBe(100)
    expect(screen.getByText('Live 20')).toBeInTheDocument()
    expect(screen.queryByText('Live 19')).not.toBeInTheDocument()
    for (const item of messages) expect(screen.getByText(item.content)).toBeInTheDocument()
  })

  it('only re-executes a replaced message when the messages array changes', () => {
    const a = message('a')
    const b = message('b')
    const { rerender } = render(<MessageList messages={[a, b]} />)
    rerender(<MessageList messages={[a, { ...b, content: 'Hydrated history' }]} />)
    expect(count(a.content)).toBe(1)
    expect(screen.getByText('Hydrated history')).toBeInTheDocument()
    expect(screen.queryByText(b.content)).not.toBeInTheDocument()
  })

  it('updates thinking only for the active row and removes it when streaming moves', () => {
    const messages = [message('old'), message('active')]
    const { rerender } = render(<MessageList messages={messages} streamingMessageId="active" streamingThinkingContent="Thinking one" isThinking />)
    rerender(<MessageList messages={messages} streamingMessageId="active" streamingThinkingContent="Thinking two" isThinking />)
    expect(count(messages[0].content)).toBe(1)
    expect(count(messages[1].content)).toBe(2)
    rerender(<MessageList messages={messages} streamingMessageId="new-stream" streamingThinkingContent="Next round" isThinking />)
    expect(count(messages[1].content)).toBe(3)
    expect(screen.queryByText('Thinking two')).not.toBeInTheDocument()
  })

  it('isolates feedback pending state and still uses a replaced callback', () => {
    const messages = [message('a'), message('b')]
    const first = vi.fn()
    const second = vi.fn()
    const { rerender } = render(<MessageList messages={messages} onSubmitFeedback={first} />)
    rerender(<MessageList messages={messages} onSubmitFeedback={first} feedbackPendingMessageId="b" />)
    expect(count(messages[0].content)).toBe(1)
    const likes = screen.getAllByRole('button', { name: 'zh:feedback.like' })
    expect(likes[0]).toBeEnabled()
    expect(likes[1]).toBeDisabled()
    rerender(<MessageList messages={messages} onSubmitFeedback={second} />)
    fireEvent.click(screen.getAllByRole('button', { name: 'zh:feedback.like' })[0])
    expect(first).not.toHaveBeenCalled()
    expect(second).toHaveBeenCalledWith(messages[0], 'up')
  })

  it('keeps role-group timestamps correct when a same-role neighbor is appended or changed', () => {
    const a = message('a')
    const b = message('b')
    const c = message('c')
    const { container, rerender } = render(<MessageList messages={[a, b]} />)
    expect(container.querySelectorAll('.chat-timestamp')).toHaveLength(1)
    rerender(<MessageList messages={[a, b, c]} />)
    expect(container.querySelectorAll('.chat-timestamp')).toHaveLength(1)
    rerender(<MessageList messages={[a, { ...b, role: 'user' }, c]} />)
    expect(container.querySelectorAll('.chat-timestamp')).toHaveLength(3)
    expect(container.querySelectorAll('.lucide-sparkles')).toHaveLength(2)
  })

  it('continues reacting to auth, mobile and language contexts without changing message references', () => {
    const messages = [message('user', { role: 'user', content: 'My prompt' }), message('reply')]
    const feedback = vi.fn()
    const view = (updated: boolean) => (
      <authContext.Provider value={{ user: updated ? { username: 'Author', avatar_url: '/avatar.png' } : null }}>
        <mobileContext.Provider value={{ isMobile: updated }}>
          <languageContext.Provider value={updated ? 'en' : 'zh'}>
            <MessageList messages={messages} onSubmitFeedback={feedback} />
          </languageContext.Provider>
        </mobileContext.Provider>
      </authContext.Provider>
    )
    const { container, rerender } = render(view(false))
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'zh:feedback.like' })).toBeInTheDocument()
    rerender(view(true))
    expect(screen.getByRole('img', { name: 'Author' })).toHaveAttribute('src', '/avatar.png')
    expect(screen.getByRole('button', { name: 'en:feedback.like' })).toBeInTheDocument()
    expect(container.querySelector('.chat-message-bubble')?.closest('.flex.items-start')).toHaveClass('py-2')
  })
})
