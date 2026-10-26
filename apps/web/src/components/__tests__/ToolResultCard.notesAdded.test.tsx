import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ToolResultCard } from '../ToolResultCard'

const t = (key: string) => key
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t }) }))
vi.mock('../../lib/i18n', () => ({ default: { language: 'zh' } }))

const added = ['女主要到第五章才出场', '主角左手有旧伤，阴天会疼']
const renderCard = (data: Record<string, unknown>, onOpenAIMemory = vi.fn()) => {
  const view = render(
    <ToolResultCard
      type="tool_result"
      toolName="update_project"
      result={{ status: 'success', data: { project_id: 'p1', ...data } }}
      onOpenAIMemory={onOpenAIMemory}
    />,
  )
  return { ...view, onOpenAIMemory }
}

describe('update_project Writing Conventions receipt', () => {
  it('shows the exact saved entries as a single read-only receipt', () => {
    const { container } = renderCard({ updated_fields: ['notes'], notes_added: added })

    expect(screen.getByText('chat:notesAdded.title')).toBeInTheDocument()
    expect(screen.getAllByRole('listitem').map(item => item.textContent)).toEqual(added)
    expect(screen.queryByText('chat:tool.status_unchanged')).not.toBeInTheDocument()
    expect(screen.queryByText('chat:tool.status_updated')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /undo|撤销/i })).not.toBeInTheDocument()
    expect(container.querySelectorAll('.rounded-lg.border')).toHaveLength(1)
  })

  it('opens the shared editor from live and history-loaded cards without reading or deleting', async () => {
    const user = userEvent.setup()
    const { onOpenAIMemory } = renderCard({ updated_fields: ['notes'], notes_added: added })

    const open = screen.getByRole('button', { name: 'chat:notesAdded.open' })
    await user.click(open)
    expect(onOpenAIMemory).toHaveBeenCalledTimes(1)
    expect(open).toHaveFocus()
  })

  it('keeps an older history receipt usable without a stored project id', () => {
    render(
      <ToolResultCard type="tool_result" toolName="update_project"
        result={{ status: 'success', data: { updated_fields: ['notes'], notes_added: added } }}
        onOpenAIMemory={vi.fn()} />,
    )
    expect(screen.getByText('chat:notesAdded.title')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'chat:notesAdded.open' })).toBeInTheDocument()
  })

  it('combines other updated fields and the agreements receipt in one result surface', () => {
    const { container } = renderCard({ updated_fields: ['summary', 'notes'], notes_added: added })

    expect(screen.getByText('chat:tool.status_updated')).toBeInTheDocument()
    expect(screen.getByText('(chat:project.field.summary)')).toBeInTheDocument()
    expect(screen.getByText('chat:notesAdded.title')).toBeInTheDocument()
    expect(container.querySelectorAll('.rounded-lg.border')).toHaveLength(1)
  })

  it('keeps the capacity result concise when nothing was written', () => {
    renderCard({ updated_fields: [], notes_note: 'capacity reached' })
    expect(screen.getByText('chat:notesAdded.full')).toBeInTheDocument()
    expect(screen.queryByText('chat:tool.status_updated')).not.toBeInTheDocument()
    expect(screen.queryByRole('listitem')).not.toBeInTheDocument()
  })

  it('preserves a partial result receipt when another update_project subtask failed', () => {
    render(
      <ToolResultCard
        type="tool_result"
        toolName="update_project"
        error="task board rejected"
        result={{ status: 'partial', data: { project_id: 'p1', updated_fields: ['notes'], notes_added: added } }}
        onOpenAIMemory={vi.fn()}
      />,
    )
    expect(screen.getByText('chat:notesAdded.title')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'chat:notesAdded.open' })).toBeInTheDocument()
  })
})
