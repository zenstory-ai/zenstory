import { describe, it, expect, vi, afterEach } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { LeaveWhileGeneratingDialog } from '../LeaveWhileGeneratingDialog'
import { RoundEndNote } from '../RoundEndNote'
import { parseRoundStopOutcome } from '../../lib/chatRoundEnd'
import zhChat from '../../../public/locales/zh/chat.json'

/** Renders the real Simplified Chinese copy, so the tests read what the author reads. */
function zhText(key: string, options?: Record<string, unknown>): string {
  const [, path] = key.split(':')
  const value = path.split('.').reduce<unknown>(
    (node, part) => (node && typeof node === 'object' ? (node as Record<string, unknown>)[part] : undefined),
    zhChat,
  )
  const template = typeof value === 'string' ? value : String(options?.defaultValue ?? key)
  return template.replace(/\{\{(\w+)\}\}/g, (_m, name: string) => String(options?.[name] ?? ''))
}

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: zhText }),
}))

afterEach(() => cleanup())

describe('RoundEndNote (stopped / interrupted round after a refresh)', () => {
  const outcome = (raw: Record<string, unknown>) => {
    const parsed = parseRoundStopOutcome(JSON.stringify({ stop_reason: raw.reason, stop_outcome: raw }))
    if (!parsed) throw new Error('not a stop outcome')
    return parsed
  }

  it.each([
    [
      { reason: 'incomplete', charged: true, saved_output: true,
        written_files: [{ id: 'filled', title: '林舟', words: 120 }],
        unwritten_files: [{ id: 'empty', title: '周念' }] },
      true,
      '已保存《林舟》120 字 · 未确认保存《周念》',
    ],
    [
      { reason: 'error', charged: true, saved_output: true,
        written_files: [{ id: 'filled', title: '林舟', words: 120 }],
        unwritten_files: [{ id: 'empty', title: '周念' }] },
      true,
      '已中断 · 已保存《林舟》120 字 · 未确认保存《周念》 · 本条计入今日 AI 消息',
    ],
    [{ reason: 'user_stopped', charged: false, saved_output: false }, true, '已停止 · 未计入今日 AI 消息'],
    // A stop / disconnect whose body save was never confirmed (F-03): counted, but never 「已写入的内容已保存」.
    [
      { reason: 'user_stopped', charged: true, saved_output: false,
        unwritten_files: [{ id: 'ch-1', title: '第1章 最后一页' }] },
      true,
      '已停止 · 未确认保存《第1章 最后一页》 · 本条计入今日 AI 消息',
    ],
    [
      { reason: 'client_disconnected', charged: true, saved_output: false,
        unwritten_files: [{ id: 'ch-1', title: '第1章 最后一页' }] },
      false,
      '已中断 · 未确认保存《第1章 最后一页》',
    ],
    // Same wording as the live stop note, so a refresh does not drop "counted".
    [{ reason: 'user_stopped', charged: true, saved_output: true }, true, '已停止 · 已写入的内容已保存 · 本条计入今日 AI 消息'],
    [{ reason: 'user_stopped', charged: true, saved_output: false }, true, '已停止 · 本条计入今日 AI 消息'],
    [{ reason: 'client_disconnected', charged: true, saved_output: false }, true, '已中断 · 本条计入今日 AI 消息'],
    [{ reason: 'user_stopped', charged: true, saved_output: true }, false, '已停止 · 已写入的内容已保存'],
    [{ reason: 'client_disconnected', charged: false, saved_output: false }, true, '已中断 · 未计入今日 AI 消息'],
    // Pro has no daily count to mention.
    [{ reason: 'user_stopped', charged: false, saved_output: false }, false, '已停止'],
    [
      { reason: 'user_stopped', charged: false, saved_output: false, removed_files: ['第1章 最后一页'] },
      true,
      '已停止 · 未计入今日 AI 消息',
    ],
    [
      { reason: 'user_stopped', charged: false, saved_output: false, removed_files: ['第1章', '第2章'] },
      false,
      '已停止',
    ],
    // The files this round wrote (U088: chapter 2 written, the reply only its opening line).
    [
      {
        reason: 'client_disconnected', charged: true, saved_output: true,
        written_files: [{ id: 'ch-2', title: '第2章 入宅', words: 3120 }],
      },
      true,
      '已中断 · 已保存《第2章 入宅》3,120 字 · 本条计入今日 AI 消息',
    ],
    [
      {
        reason: 'user_stopped', charged: true, saved_output: true,
        written_files: [{ id: 'ch-1', title: '第1章' }, { id: 'ch-5', title: '第5章', words: 0 }],
      },
      false,
      // An edit has no word count; an empty count is not worth saying either.
      '已停止 · 已保存《第1章》、《第5章》',
    ],
    [
      {
        reason: 'client_disconnected', charged: true, saved_output: true,
        written_files: ['人物', '世界观', '大纲', '第1章', '第2章'].map((title, i) => ({ id: `f${i}`, title, words: 100 * (i + 1) })),
      },
      false,
      '已中断 · 已保存《人物》100 字、《世界观》200 字、《大纲》300 字等 5 个文件',
    ],
    // A normally completed round (#267): only the write receipt, never the daily count.
    [
      { reason: 'completed', charged: true, saved_output: true,
        written_files: [{ id: 'ch-1', title: '第1章', words: 2100 }, { id: 'ch-2', title: '第2章', partial: true }] },
      true,
      '已保存《第1章》2,100 字、《第2章》（部分修改未生效）',
    ],
    [{ reason: 'completed', charged: true, saved_output: false, no_file_changes: true }, true, '本轮没有改动文件'],
    [{ reason: 'incomplete', charged: true, saved_output: true }, true, '已保存本轮完成的修改，可以继续写后续内容。'],
    [{ reason: 'incomplete', charged: true, saved_output: false }, true, '本轮尚未完成，可以继续。'],
    [{ reason: 'incomplete', charged: true, saved_output: false, no_file_changes: true }, true, '本轮尚未写入新内容，可以继续完成。'],
    [{ reason: 'incomplete', charged: true, saved_output: false, removed_files: ['临时空文件'] }, true, '本轮尚未完成，可以继续。'],
  ])('%j (daily count shown: %s) reads 「%s」', (raw, showDailyCount, text) => {
    render(<RoundEndNote outcome={outcome(raw)} showDailyCount={showDailyCount} />)
    expect(screen.getByTestId('round-end-note')).toHaveTextContent(new RegExp(`^${text}$`))
  })

  it('reads only well-formed written files, and older messages without them as before', () => {
    expect(outcome({ reason: 'user_stopped', charged: true, saved_output: true }).writtenFiles).toEqual([])
    expect(outcome({
      reason: 'user_stopped', charged: true, saved_output: true,
      written_files: [{ id: 'a', title: '第1章', words: 12 }, { id: 'b', title: '' }, 'x', { id: 'c', title: '第3章', words: 'many' }],
    }).writtenFiles).toEqual([{ title: '第1章', words: 12 }, { title: '第3章', words: null }])
  })

  it('ignores messages that did not end in a stop or disconnect', () => {
    expect(parseRoundStopOutcome(JSON.stringify({ stop_reason: 'end_turn' }))).toBeNull()
    expect(parseRoundStopOutcome(JSON.stringify({ stop_outcome: { reason: 'cancelled' } }))).toBeNull()
    expect(parseRoundStopOutcome('not json')).toBeNull()
    expect(parseRoundStopOutcome(null)).toBeNull()
  })
})

describe('LeaveWhileGeneratingDialog', () => {
  const renderDialog = (props: Partial<React.ComponentProps<typeof LeaveWhileGeneratingDialog>> = {}) => {
    const onStay = vi.fn()
    const onLeave = vi.fn()
    render(
      <LeaveWhileGeneratingDialog
        open
        roundEnded={false}
        producedOutput={false}
        showDailyCount
        onStay={onStay}
        onLeave={onLeave}
        {...props}
      />,
    )
    return { onStay, onLeave }
  }

  it('promises "not counted" only while nothing has been written', () => {
    renderDialog()
    expect(screen.getByText('离开这个页面会中断这一轮。还没写出内容的话，这一轮不计入今日 AI 消息。')).toBeInTheDocument()
  })

  it('says what really happens once something was written: kept, and counted', () => {
    renderDialog({ producedOutput: true })
    expect(screen.getByText('离开这个页面会中断这一轮。已经写出的内容会保存，这一轮计入今日 AI 消息。')).toBeInTheDocument()
    expect(screen.queryByText(/不计入/)).not.toBeInTheDocument()
  })

  it('never mentions the daily count on Pro', () => {
    renderDialog({ producedOutput: true, showDailyCount: false })
    expect(screen.getByText('离开这个页面会中断这一轮，已经写出的内容会保存。')).toBeInTheDocument()
    expect(screen.queryByText(/今日 AI 消息/)).not.toBeInTheDocument()
  })

  it('makes staying the primary action and leaving the quiet one', () => {
    const { onStay, onLeave } = renderDialog()
    const stay = screen.getByTestId('leave-dialog-stay')
    const leave = screen.getByTestId('leave-dialog-leave')
    expect(stay).toHaveTextContent('继续等')
    expect(stay.className).toContain('bg-[hsl(var(--primary))]')
    expect(leave).toHaveTextContent('离开')
    // Not the blue primary: leaving ends the round.
    expect(leave.className).not.toContain('bg-[hsl(var(--primary))]')
    expect(document.activeElement).toBe(stay)

    fireEvent.click(leave)
    expect(onLeave).toHaveBeenCalledTimes(1)
    fireEvent.click(stay)
    expect(onStay).toHaveBeenCalledTimes(1)
  })

  it('says leaving cuts nothing off once the round has finished', () => {
    renderDialog({ roundEnded: true, producedOutput: true })
    expect(screen.getByText('这一轮已经结束')).toBeInTheDocument()
    expect(screen.getByText('现在离开不会中断任何内容。')).toBeInTheDocument()
    expect(screen.getByTestId('leave-dialog-stay')).toHaveTextContent('留在这里')
  })
})

describe('LeaveWhileGeneratingDialog before a new chat', () => {
  const renderDialog = (props: Partial<React.ComponentProps<typeof LeaveWhileGeneratingDialog>> = {}) => {
    const onStay = vi.fn()
    const onLeave = vi.fn()
    render(
      <LeaveWhileGeneratingDialog
        open
        intent="newChat"
        roundEnded={false}
        producedOutput={false}
        showDailyCount
        onStay={onStay}
        onLeave={onLeave}
        {...props}
      />,
    )
    return { onStay, onLeave }
  }

  it('says a new chat stops this round first, and what that costs', () => {
    renderDialog()
    expect(screen.getByText('AI 还在写')).toBeInTheDocument()
    expect(screen.getByText('开始新对话会先停止这一轮。还没写出内容的话，这一轮不计入今日 AI 消息。')).toBeInTheDocument()
    expect(screen.getByTestId('new-chat-dialog-confirm')).toHaveTextContent('停止并开始新对话')
    expect(screen.getByTestId('new-chat-dialog-stay')).toHaveTextContent('继续等')
    expect(document.activeElement).toBe(screen.getByTestId('new-chat-dialog-stay'))
  })

  it('says what was written is kept and counted once something was written', () => {
    renderDialog({ producedOutput: true })
    expect(screen.getByText('开始新对话会先停止这一轮。已经写出的内容会保存，这一轮计入今日 AI 消息。')).toBeInTheDocument()
  })

  it('never mentions the daily count on Pro', () => {
    renderDialog({ showDailyCount: false })
    expect(screen.getByText('开始新对话会先停止这一轮。')).toBeInTheDocument()
  })

  it('offers a plain new chat once the round has finished', () => {
    const { onLeave, onStay } = renderDialog({ roundEnded: true })
    expect(screen.getByText('这一轮已经结束')).toBeInTheDocument()
    expect(screen.getByText('现在开始新对话不会中断任何内容。')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('new-chat-dialog-confirm'))
    expect(screen.getByTestId('new-chat-dialog-confirm')).toHaveTextContent('开始新对话')
    expect(screen.getByTestId('new-chat-dialog-stay')).toHaveTextContent('取消')
    expect(onLeave).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByTestId('new-chat-dialog-stay'))
    expect(onStay).toHaveBeenCalledTimes(1)
  })
})
