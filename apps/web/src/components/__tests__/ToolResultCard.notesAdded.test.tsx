import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ToolResultCard } from '../ToolResultCard'
import { ApiError } from '../../lib/apiClient'
import { PROJECT_STATUS_UPDATED_EVENT } from '../../lib/projectStatusEvents'

const t = (key: string, options?: Record<string, unknown>) =>
  options?.count !== undefined ? `${key}:${options.count}` : key
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t }) }))
vi.mock('../../lib/i18n', () => ({ default: { language: 'zh' } }))

const api = vi.hoisted(() => ({ get: vi.fn(), patch: vi.fn() }))
vi.mock('../../lib/api', () => ({ projectApi: api }))

const before = '旧约定\n\n【作者确认】\n原确认'
const stored = `${before}\n【未标来源】\n女主要到第五章才出场\n主角左手有旧伤，阴天会疼`
const added = ['女主要到第五章才出场', '主角左手有旧伤，阴天会疼']
const card = (data: Record<string, unknown>) => render(
  <ToolResultCard type="tool_result" toolName="update_project"
    result={{ status: 'success', data: { project_id: 'p1', updated_fields: ['notes'], ...data } }} />,
)
const undoButton = () => screen.getByRole('button', { name: 'chat:notesAdded.undo' })
let events: unknown[]
const listener = (event: Event) => events.push((event as CustomEvent).detail)

beforeEach(() => {
  vi.clearAllMocks()
  events = []
  window.addEventListener(PROJECT_STATUS_UPDATED_EVENT, listener)
  api.get.mockResolvedValue({ id: 'p1', notes: stored })
  api.patch.mockImplementation(async (_id: string, body: { notes: string }) => ({ id: 'p1', notes: body.notes }))
})
afterEach(() => window.removeEventListener(PROJECT_STATUS_UPDATED_EVENT, listener))

describe('update_project notes the AI remembered', () => {
  it('lists the saved entries with one undo button and reads nothing on mount', () => {
    card({ notes_added: added })
    expect(screen.getByText('chat:notesAdded.title')).toBeInTheDocument()
    expect(screen.getAllByRole('listitem').map(item => item.textContent)).toEqual(added)
    expect(screen.getAllByRole('button')).toHaveLength(1)
    expect(undoButton()).toHaveAttribute('type', 'button')
    expect(api.get).not.toHaveBeenCalled()
  })

  it('undo removes exactly those entries with a guarded PATCH, notifies, and collapses', async () => {
    const user = userEvent.setup()
    card({ notes_added: added })
    undoButton().focus()
    await user.keyboard('{Enter}')
    expect(api.get).toHaveBeenCalledWith('p1')
    expect(api.patch).toHaveBeenCalledWith('p1', { notes: before, expected_notes: stored })
    const done = await screen.findByText('chat:notesAdded.undone')
    expect(screen.queryByRole('listitem')).not.toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    await waitFor(() => expect(done).toHaveFocus())
    expect(events).toEqual([{ projectId: 'p1', updatedFields: ['notes'] }])
  })

  it('removes only exact matches when the author already edited an entry, and says so', async () => {
    const user = userEvent.setup()
    const edited = stored.replace('阴天会疼', '雨天会疼')
    api.get.mockResolvedValue({ id: 'p1', notes: edited })
    card({ notes_added: added })
    await user.click(undoButton())
    expect(api.patch).toHaveBeenCalledWith('p1', {
      notes: `${before}\n【未标来源】\n主角左手有旧伤，雨天会疼`, expected_notes: edited,
    })
    await screen.findByText('chat:notesAdded.undone')
    expect(screen.getByText('chat:notesAdded.partlyChanged:1')).toBeInTheDocument()
  })

  it('writes nothing when every entry is already gone', async () => {
    const user = userEvent.setup()
    api.get.mockResolvedValue({ id: 'p1', notes: before })
    card({ notes_added: added })
    await user.click(undoButton())
    await screen.findByText('chat:notesAdded.alreadyGone')
    expect(api.patch).not.toHaveBeenCalled()
    expect(events).toEqual([])
  })

  it('on 409 refetches and retries once with the fresh baseline', async () => {
    const user = userEvent.setup()
    const newer = `${stored}\n作者刚加的`
    api.get.mockResolvedValueOnce({ id: 'p1', notes: stored }).mockResolvedValueOnce({ id: 'p1', notes: newer })
    api.patch.mockRejectedValueOnce(new ApiError(409, 'ERR_RESOURCE_CONFLICT'))
    card({ notes_added: added })
    await user.click(undoButton())
    await screen.findByText('chat:notesAdded.undone')
    expect(api.patch).toHaveBeenCalledTimes(2)
    expect(api.patch).toHaveBeenLastCalledWith('p1', {
      notes: `${before}\n【未标来源】\n作者刚加的`, expected_notes: newer,
    })
  })

  it('after a 409 whose fresh notes no longer hold the entries, writes nothing more', async () => {
    const user = userEvent.setup()
    api.get.mockResolvedValueOnce({ id: 'p1', notes: stored }).mockResolvedValueOnce({ id: 'p1', notes: before })
    api.patch.mockRejectedValueOnce(new ApiError(409, 'ERR_RESOURCE_CONFLICT'))
    card({ notes_added: added })
    await user.click(undoButton())
    await screen.findByText('chat:notesAdded.alreadyGone')
    expect(api.patch).toHaveBeenCalledTimes(1)
  })

  it('a second 409 shows a short message and keeps the undo button', async () => {
    const user = userEvent.setup()
    api.patch.mockRejectedValue(new ApiError(409, 'ERR_RESOURCE_CONFLICT'))
    card({ notes_added: added })
    await user.click(undoButton())
    expect(await screen.findByRole('alert')).toHaveTextContent('chat:notesAdded.conflict')
    expect(api.patch).toHaveBeenCalledTimes(2)
    expect(undoButton()).toBeEnabled()
    expect(events).toEqual([])
  })

  it('shows a generic failure when the read fails', async () => {
    const user = userEvent.setup()
    api.get.mockRejectedValueOnce(new Error('offline'))
    card({ notes_added: added })
    await user.click(undoButton())
    expect(await screen.findByRole('alert')).toHaveTextContent('chat:notesAdded.failed')
    expect(api.patch).not.toHaveBeenCalled()
  })

  it('offers undo on cards loaded from history, including a partial result with a rejected task board', async () => {
    const user = userEvent.setup()
    const saved = { project_id: 'p1', updated_fields: ['notes'], notes_added: added }
    const restored = render(
      <ToolResultCard type="tool_result" toolName="update_project"
        result={{ status: 'success', data: saved }} />,
    )
    expect(undoButton()).toBeInTheDocument()
    restored.unmount()
    render(
      <ToolResultCard type="tool_result" toolName="update_project" error="任务计划板未更新"
        result={{ status: 'partial', error_type: 'update_plan_rejected', mutation_applied: true, data: saved }} />,
    )
    expect(screen.getByText('chat:tool.failed')).toBeInTheDocument()
    await user.click(undoButton())
    expect(api.patch).toHaveBeenCalledWith('p1', { notes: before, expected_notes: stored })
  })

  it('renders no undo without notes_added or a project id, nor while pending', () => {
    card({ updated_fields: ['summary'] })
    card({ notes_added: added, project_id: undefined })
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })
})

describe('update_project that changed nothing visible', () => {
  it('does not render the green success line', () => {
    card({ updated_fields: [] })
    expect(screen.queryByText('chat:tool.status_updated')).not.toBeInTheDocument()
    expect(screen.getByText('chat:tool.status_unchanged')).toBeInTheDocument()
  })

  it('says briefly when notes were full', () => {
    card({ updated_fields: [], notes_note: '备注已接近 4000 字上限' })
    expect(screen.queryByText('chat:tool.status_updated')).not.toBeInTheDocument()
    expect(screen.getByText('chat:notesAdded.full')).toBeInTheDocument()
  })

  it('still renders success for a rename, a task board or saved fields', () => {
    card({ updated_fields: [], project_name_updated: true })
    card({ updated_fields: [], plan: { status: 'success' } })
    card({ updated_fields: ['summary'] })
    expect(screen.getAllByText('chat:tool.status_updated')).toHaveLength(3)
    expect(screen.queryByText('chat:tool.status_unchanged')).not.toBeInTheDocument()
  })
})
