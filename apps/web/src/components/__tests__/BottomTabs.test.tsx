import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useState } from 'react'
import { BottomTabs } from '../BottomTabs'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) =>
      (
        {
          'editor:bottomTabs.files': 'Files',
          'editor:bottomTabs.editor': 'Editor',
          'editor:bottomTabs.ai': 'AI Chat',
          'editor:bottomTabs.ariaLabel': '底部导航',
        } as Record<string, string>
      )[key] ?? key,
  }),
}))

describe('BottomTabs', () => {
  it('renders all tabs and marks the active one', () => {
    render(<BottomTabs activeTab="editor" onTabChange={vi.fn()} />)

    expect(screen.getByRole('tablist', { name: '底部导航' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Files' })).toHaveAttribute('aria-selected', 'false')
    expect(screen.getByRole('tab', { name: 'Editor' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: 'AI Chat' })).toHaveAttribute('aria-selected', 'false')
    expect(screen.getByRole('tab', { name: 'Files' })).toHaveAttribute('id', 'files-tab')
    expect(screen.getByRole('tab', { name: 'Files' })).toHaveAttribute('aria-controls', 'files-panel')
    expect(screen.getByRole('tab', { name: 'Editor' })).toHaveAttribute('id', 'editor-tab')
    expect(screen.getByRole('tab', { name: 'AI Chat' })).toHaveAttribute('id', 'chat-tab')
  })

  it('supports wrapped arrows and Home/End with selection and focus', () => {
    const Harness = () => {
      const [activeTab, setActiveTab] = useState<'files' | 'editor' | 'chat'>('editor')
      return <BottomTabs activeTab={activeTab} onTabChange={setActiveTab} />
    }
    render(<Harness />)
    const files = screen.getByRole('tab', { name: 'Files' })
    const editor = screen.getByRole('tab', { name: 'Editor' })
    const chat = screen.getByRole('tab', { name: 'AI Chat' })
    editor.focus()
    for (const [key, target] of [
      ['ArrowRight', chat], ['ArrowRight', files], ['ArrowLeft', chat],
      ['Home', files], ['End', chat],
    ] as const) {
      fireEvent.keyDown(document.activeElement!, { key })
      expect(target).toHaveFocus()
      expect(target).toHaveAttribute('aria-selected', 'true')
      expect(target).toHaveAttribute('tabindex', '0')
    }
  })

  it('notifies when a different tab is selected', () => {
    const onTabChange = vi.fn()
    render(<BottomTabs activeTab="files" onTabChange={onTabChange} />)

    fireEvent.click(screen.getByRole('tab', { name: 'AI Chat' }))

    expect(onTabChange).toHaveBeenCalledWith('chat')
  })
})
