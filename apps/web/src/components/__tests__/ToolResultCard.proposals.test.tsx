import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ToolResultCard } from '../ToolResultCard'
import { ApiError } from '../../lib/apiClient'
import { PROJECT_STATUS_UPDATED_EVENT } from '../../lib/projectStatusEvents'

const t = (key: string, options?: Record<string, unknown>) =>
  options?.entry !== undefined ? `${key}:${options.entry}` : key
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t }) }))
vi.mock('../../lib/i18n', () => ({ default: { language: 'zh' } }))

const api = vi.hoisted(() => ({ get: vi.fn(), patch: vi.fn() }))
vi.mock('../../lib/api', () => ({ projectApi: api }))

const stored = '旧约定\n\n【作者确认】\n原确认\n\n【AI 提议·待作者确认】\n女主是前朝公主；沧不夺舍\n第五章揭晓'
const card = (data: Record<string, unknown>) => render(
  <ToolResultCard type="tool_result" toolName="update_project"
    result={{ status: 'success', data: { project_id: 'p1', updated_fields: ['notes'], ...data } }} />,
)
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

describe('update_project proposal actions in chat', () => {
  it('renders no actions without proposals_added and reads nothing on mount', () => {
    card({})
    card({ proposals_added: ['女主是前朝公主'], project_id: undefined })
    expect(screen.queryByText('chat:proposals.adopt')).not.toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    card({ proposals_added: ['女主是前朝公主', '沧不夺舍'] })
    expect(screen.getAllByRole('listitem').map(item => item.firstChild?.textContent)).toEqual(['女主是前朝公主', '沧不夺舍'])
    expect(screen.getByRole('button', { name: 'proposals.adoptAll' })).toBeInTheDocument()
    expect(api.get).not.toHaveBeenCalled()
  })

  it('adopts one entry: GET fresh notes, then guarded PATCH, then marks and notifies', async () => {
    const user = userEvent.setup()
    card({ proposals_added: ['女主是前朝公主', '沧不夺舍'] })
    await user.click(screen.getByRole('button', { name: 'proposals.adoptLabel:沧不夺舍' }))
    expect(api.get).toHaveBeenCalledWith('p1')
    expect(api.patch).toHaveBeenCalledWith('p1', {
      notes: '旧约定\n\n【作者确认】\n原确认\n沧不夺舍\n\n【AI 提议·待作者确认】\n女主是前朝公主\n第五章揭晓',
      expected_notes: stored,
    })
    // Handled entries leave the list and are counted in one summary line.
    await screen.findByText('proposals.summary.adopted')
    expect(screen.getAllByRole('listitem').map(item => item.firstChild?.textContent)).toEqual(['女主是前朝公主'])
    expect(screen.queryByRole('button', { name: 'proposals.adoptAll' })).not.toBeInTheDocument()
    expect(events).toEqual([{ projectId: 'p1', updatedFields: ['notes'] }])
  })

  it('ignores one entry and adopts all remaining in one PATCH', async () => {
    const user = userEvent.setup()
    card({ proposals_added: ['女主是前朝公主', '沧不夺舍', '第五章揭晓'] })
    await user.click(screen.getByRole('button', { name: 'proposals.ignoreLabel:女主是前朝公主' }))
    expect(api.patch).toHaveBeenLastCalledWith('p1', {
      notes: '旧约定\n\n【作者确认】\n原确认\n\n【AI 提议·待作者确认】\n沧不夺舍\n第五章揭晓', expected_notes: stored,
    })
    await screen.findByText('proposals.summary.ignored')
    const afterIgnore = '旧约定\n\n【作者确认】\n原确认\n\n【AI 提议·待作者确认】\n沧不夺舍\n第五章揭晓'
    api.get.mockResolvedValue({ id: 'p1', notes: afterIgnore })
    await user.click(screen.getByRole('button', { name: 'proposals.adoptAll' }))
    expect(api.patch).toHaveBeenLastCalledWith('p1', {
      notes: '旧约定\n\n【作者确认】\n原确认\n沧不夺舍\n第五章揭晓', expected_notes: afterIgnore,
    })
    await screen.findByText('proposals.summary.adopted · proposals.summary.ignored')
    expect(screen.queryByRole('listitem')).not.toBeInTheDocument()
    expect(screen.queryByText('proposals.pendingTitle')).not.toBeInTheDocument()
  })

  it('keeps keyboard focus in the card after each action', async () => {
    const user = userEvent.setup()
    card({ proposals_added: ['女主是前朝公主', '沧不夺舍', '第五章揭晓'] })
    await user.click(screen.getByRole('button', { name: 'proposals.ignoreLabel:女主是前朝公主' }))
    await screen.findByText('proposals.summary.ignored')
    // The acted row is gone; focus moves to the next row's first action, not the page body.
    await waitFor(() => expect(screen.getByRole('button', { name: 'proposals.adoptLabel:沧不夺舍' })).toHaveFocus())

    api.get.mockResolvedValue({ id: 'p1', notes: '旧约定\n\n【作者确认】\n原确认\n\n【AI 提议·待作者确认】\n沧不夺舍\n第五章揭晓' })
    await user.click(screen.getByRole('button', { name: 'proposals.adoptAll' }))
    const summary = await screen.findByText('proposals.summary.adopted · proposals.summary.ignored')
    // The pending block unmounted with its last entry: focus lands on the live summary line.
    await waitFor(() => expect(summary).toHaveFocus())
    expect(summary).toHaveAttribute('aria-live', 'polite')
  })

  it('lists the first three pending entries and discloses the rest', async () => {
    const user = userEvent.setup()
    const many = ['一', '二', '三', '四', '五']
    api.get.mockResolvedValue({ id: 'p1', notes: `【AI 提议·待作者确认】\n${many.join('；')}` })
    card({ proposals_added: many })
    const listed = () => screen.getAllByRole('listitem').map(item => item.firstChild?.textContent)
    expect(listed()).toEqual(['一', '二', '三'])
    const more = screen.getByRole('button', { name: 'proposals.showMore' })
    expect(more).toHaveAttribute('aria-expanded', 'false')
    await user.click(more)
    expect(listed()).toEqual(many)
    const less = screen.getByRole('button', { name: 'proposals.showLess' })
    expect(less).toHaveAttribute('aria-expanded', 'true')
    await user.click(less)
    expect(listed()).toEqual(['一', '二', '三'])
    await user.click(screen.getByRole('button', { name: 'proposals.ignoreLabel:一' }))
    await screen.findByText('proposals.summary.ignored')
    // Handled rows are not listed: the next pending entry takes the freed place, then the disclosure goes.
    expect(listed()).toEqual(['二', '三', '四'])
    await user.click(screen.getByRole('button', { name: 'proposals.adoptLabel:二' }))
    await waitFor(() => expect(listed()).toEqual(['三', '四', '五']))
    expect(screen.queryByRole('button', { name: /proposals.show/ })).not.toBeInTheDocument()
  })

  it('reports entries already handled elsewhere without writing', async () => {
    const user = userEvent.setup()
    api.get.mockResolvedValue({ id: 'p1', notes: '旧约定\n\n【作者确认】\n沧不夺舍' })
    card({ proposals_added: ['沧不夺舍'] })
    await user.click(screen.getByRole('button', { name: 'proposals.adoptLabel:沧不夺舍' }))
    await screen.findByText('proposals.summary.handled')
    expect(api.patch).not.toHaveBeenCalled()
    expect(events).toEqual([])
  })

  it('shows a retryable message on 409 without retrying by itself', async () => {
    const user = userEvent.setup()
    api.patch.mockRejectedValueOnce(new ApiError(409, 'ERR_RESOURCE_CONFLICT'))
    card({ proposals_added: ['沧不夺舍'] })
    await user.click(screen.getByRole('button', { name: 'proposals.adoptLabel:沧不夺舍' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('proposals.conflict')
    expect(api.patch).toHaveBeenCalledTimes(1)
    expect(events).toEqual([])
    await user.click(screen.getByRole('button', { name: 'proposals.adoptLabel:沧不夺舍' }))
    expect(api.get).toHaveBeenCalledTimes(2)
    expect(api.patch).toHaveBeenCalledTimes(2)
    await screen.findByText('proposals.summary.adopted')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('offers the saved proposals on a partial result whose task board was rejected', async () => {
    const user = userEvent.setup()
    const saved = { project_id: 'p1', updated_fields: ['notes'], proposals_added: ['女主是前朝公主', '沧不夺舍'] }
    // Live stream: the adapter forwards the result data and the plan error.
    const live = render(
      <ToolResultCard type="tool_result" toolName="update_project" error="任务计划板未更新" result={saved} />,
    )
    expect(screen.getByText('chat:tool.failed')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'proposals.adoptAll' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'proposals.ignoreLabel:女主是前朝公主' }))
    expect(api.patch).toHaveBeenCalledWith('p1', {
      notes: '旧约定\n\n【作者确认】\n原确认\n\n【AI 提议·待作者确认】\n沧不夺舍\n第五章揭晓', expected_notes: stored,
    })
    await screen.findByText('proposals.summary.ignored')
    live.unmount()
    // Persisted tool result shape.
    render(
      <ToolResultCard type="tool_result" toolName="update_project" error="任务计划板未更新"
        result={{ status: 'partial', error_type: 'update_plan_rejected', mutation_applied: true, data: saved }} />,
    )
    expect(screen.getByRole('button', { name: 'proposals.adoptLabel:沧不夺舍' })).toBeInTheDocument()
  })

  it('renders no proposal actions on a failed update_project without saved proposals', () => {
    render(
      <ToolResultCard type="tool_result" toolName="update_project" error="x"
        result={{ error_type: 'update_plan_rejected' }} />,
    )
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('shows a generic failure when the read fails', async () => {
    const user = userEvent.setup()
    api.get.mockRejectedValueOnce(new Error('offline'))
    card({ proposals_added: ['沧不夺舍'] })
    await user.click(screen.getByRole('button', { name: 'proposals.adoptLabel:沧不夺舍' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('proposals.failed')
    expect(api.patch).not.toHaveBeenCalled()
  })
})
