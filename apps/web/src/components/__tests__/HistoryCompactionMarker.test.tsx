import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { HistoryCompactionMarker } from '../HistoryCompactionMarker'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}))

describe('HistoryCompactionMarker', () => {
  it('renders the saved handoff as structured Markdown without widening the chat panel', async () => {
    const summary = [
      '# 交接摘要',
      '',
      '- 保留作者原文',
      '',
      '| 文件 | id |',
      '| --- | --- |',
      '| 第一章 | `de904a54-c7b1-4fa0-be3c-ee0d4033048d` |',
    ].join('\n')

    render(<HistoryCompactionMarker state="done" summary={summary} />)

    expect(screen.queryByRole('heading', { name: '交接摘要' })).not.toBeInTheDocument()
    expect(screen.getByTestId('history-compaction-marker')).toHaveClass('min-w-0')
    const toggle = screen.getByRole('button', { name: /compaction\.done/ })
    fireEvent.click(toggle)

    expect(await screen.findByRole('heading', { level: 3, name: '交接摘要' })).toBeInTheDocument()
    expect(screen.getByText('保留作者原文').closest('li')).not.toBeNull()

    const table = await screen.findByRole('table')
    const scrollContainer = table.parentElement
    expect(scrollContainer).toHaveClass('max-w-full', 'overflow-x-auto')
    expect(table).toHaveClass('w-max', 'min-w-full')
    expect(screen.getByText('de904a54-c7b1-4fa0-be3c-ee0d4033048d').tagName).toBe('CODE')
    expect(screen.queryByText(/^# 交接摘要$/)).not.toBeInTheDocument()
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()

    fireEvent.click(toggle)
    expect(screen.queryByRole('heading', { name: '交接摘要' })).not.toBeInTheDocument()
  })
})
