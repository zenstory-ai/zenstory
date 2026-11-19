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
      '较早的对话已经整理为以下工作上下文。',
      '',
      '## 项目文件',
      '',
      '### 当前状态',
      '',
      '#### 后续顺序',
      '',
      '1. 先核对作者原文',
      '2. 再续写第三章',
      '',
      '- 保留作者原文',
      '',
      '| 文件 | 类型 | 字数 | id |',
      '| --- | --- | --- | --- |',
      '| 第一章 雨夜 | 正文 | 380 字 | `de904a54-c7b1-4fa0-be3c-ee0d4033048d` |',
      '',
      '- 正文目录（id: 09ab1059-6f47-440c-8ef3-8a47bfb38943-draft-folder）',
      '',
      '```',
      '第三章从旧挂钟继续。',
      '```',
    ].join('\n')

    render(<HistoryCompactionMarker state="done" summary={summary} />)

    expect(screen.queryByRole('heading', { name: '交接摘要' })).not.toBeInTheDocument()
    expect(screen.getByTestId('history-compaction-marker')).toHaveClass('min-w-0')
    const toggle = screen.getByRole('button', { name: /compaction\.done/ })
    fireEvent.click(toggle)

    expect(await screen.findByRole('heading', { level: 3, name: '交接摘要' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 4, name: '项目文件' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 5, name: '当前状态' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 6, name: '后续顺序' })).toBeInTheDocument()
    expect(screen.getByText('较早的对话已经整理为以下工作上下文。').tagName).toBe('P')
    expect(screen.getByText('先核对作者原文').closest('ol')).not.toBeNull()
    expect(screen.getByText('再续写第三章').closest('ol')).not.toBeNull()
    expect(screen.getByText('保留作者原文').closest('li')).not.toBeNull()
    expect(screen.getByText('第一章 雨夜').closest('li')).toHaveTextContent('正文 · 380 字')
    expect(screen.getByText('正文目录').closest('li')).not.toBeNull()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(screen.queryByText(/de904a54-c7b1-4fa0-be3c-ee0d4033048d/)).not.toBeInTheDocument()
    expect(screen.queryByText(/09ab1059-6f47-440c-8ef3-8a47bfb38943/)).not.toBeInTheDocument()
    const fencedCode = screen.getByText('第三章从旧挂钟继续。')
    expect(fencedCode.tagName).toBe('CODE')
    expect(fencedCode.closest('pre')).toHaveClass('max-w-full', 'overflow-x-auto')
    expect(screen.queryByText(/^# 交接摘要$/)).not.toBeInTheDocument()
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()

    fireEvent.click(toggle)
    expect(screen.queryByRole('heading', { name: '交接摘要' })).not.toBeInTheDocument()
  })
})
