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
    [{ reason: 'user_stopped', charged: false, saved_output: false }, true, '已停止 · 未计入今日 AI 消息'],
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
      '已停止 · 未计入今日 AI 消息 · 空白的《第1章 最后一页》已移除',
    ],
    [
      { reason: 'user_stopped', charged: false, saved_output: false, removed_files: ['第1章', '第2章'] },
      false,
      '已停止 · 空白的《第1章》《第2章》已移除',
    ],
  ])('%j (daily count shown: %s) reads 「%s」', (raw, showDailyCount, text) => {
    render(<RoundEndNote outcome={outcome(raw)} showDailyCount={showDailyCount} />)
    expect(screen.getByTestId('round-end-note')).toHaveTextContent(new RegExp(`^${text}$`))
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
    expect(stay.className).toContain('bg-[hsl(var(--accent-primary))]')
    expect(leave).toHaveTextContent('离开')
    // Not the blue primary: leaving ends the round.
    expect(leave.className).not.toContain('bg-[hsl(var(--accent-primary))]')
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
