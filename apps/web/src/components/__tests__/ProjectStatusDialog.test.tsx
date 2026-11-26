import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'

const mockProjectGet = vi.fn()
let projectStatusSubscriber: ((detail: { projectId: string; updatedFields: string[] }) => void) | undefined

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const translations: Record<string, string> = {
        title: 'AI 记忆',
        shareAsInspiration: '投稿到灵感库',
        sharingInspiration: "投稿中…",
        viewInspirationLibrary: '查看灵感库',
        cancel: '取消',
        save: '保存',
        saving: "保存中…",
        loadFailed: '无法载入项目信息，请重试。',
        retry: '重试',
        infoBanner: 'info',
        projectSummary: '项目简介',
        writingStyle: '写作风格',
        currentPhase: '当前阶段',
        notes: '写作约定',
        notesDescription: '角色事实、情节安排和必须遵守的创作边界，一行一条',
        'placeholders.summary': 'summary placeholder',
        'placeholders.writingStyle': 'style placeholder',
        'placeholders.currentPhase': 'phase placeholder',
        'placeholders.notes': 'notes placeholder',
      }
      return translations[key] || key
    },
  }),
}))

vi.mock('../ui/Modal', () => {
  const Modal = ({
    open,
    title,
    children,
    footer,
  }: {
    open: boolean
    title?: ReactNode
    children: ReactNode
    footer?: ReactNode
  }) => {
    if (!open) return null
    return (
      <div>
        <div>{title}</div>
        <div>{children}</div>
        <div>{footer}</div>
      </div>
    )
  }

  return {
    Modal,
    default: Modal,
  }
})

vi.mock('../../lib/api', () => ({
  projectApi: {
    get: (...args: unknown[]) => mockProjectGet(...args),
    patch: vi.fn(),
  },
}))

vi.mock('../../lib/projectStatusEvents', () => ({
  subscribeProjectStatusUpdated: (callback: typeof projectStatusSubscriber) => {
    projectStatusSubscriber = callback
    return () => { projectStatusSubscriber = undefined }
  },
}))

import { ProjectStatusDialog } from '../ProjectStatusDialog'

describe('ProjectStatusDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    projectStatusSubscriber = undefined
    mockProjectGet.mockResolvedValue({
      id: 'project-1',
      name: 'Test Project',
      description: 'desc',
      summary: '',
      writing_style: '',
      current_phase: '',
      notes: '',
    })
  })

  it('keeps save available without retired inspiration actions', async () => {
    render(<ProjectStatusDialog isOpen={true} onClose={vi.fn()} projectId="project-1" />)

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '保存' })).toBeInTheDocument()
    })
    expect(screen.queryByRole('button', { name: '投稿到灵感库' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '查看灵感库' })).not.toBeInTheDocument()
  })

  it('focuses Writing Conventions at the end when opened from a receipt', async () => {
    mockProjectGet.mockResolvedValueOnce({
      id: 'project-1', name: 'Test Project', description: 'desc', summary: '', writing_style: '',
      current_phase: '', notes: '第一条\n第二条',
    })
    render(<ProjectStatusDialog isOpen={true} onClose={vi.fn()} projectId="project-1" initialSection="writing-agreements" />)

    const input = await screen.findByRole('textbox', { name: '写作约定' }) as HTMLTextAreaElement
    await waitFor(() => expect(input).toHaveFocus())
    expect(input.selectionStart).toBe(input.value.length)
    expect(input).toHaveAccessibleDescription('角色事实、情节安排和必须遵守的创作边界，一行一条')

    const summary = screen.getByRole('textbox', { name: '项目简介' })
    summary.focus()
    mockProjectGet.mockResolvedValueOnce({
      id: 'project-1', name: 'Test Project', description: 'desc', summary: '远端简介', writing_style: '',
      current_phase: '', notes: '第一条\n第二条',
    })
    await act(async () => projectStatusSubscriber?.({ projectId: 'project-1', updatedFields: ['summary'] }))
    await waitFor(() => expect(summary).toHaveValue('远端简介'))
    expect(summary).toHaveFocus()
  })

  it('shows retryable load error instead of blank editable fields when initial project fetch fails', async () => {
    mockProjectGet
      .mockRejectedValueOnce(new Error('network down'))
      .mockResolvedValueOnce({
        id: 'project-1',
        name: 'Test Project',
        description: 'desc',
        summary: '已恢复简介',
        writing_style: '',
        current_phase: '',
        notes: '',
      })

    render(<ProjectStatusDialog isOpen={true} onClose={vi.fn()} projectId="project-1" />)

    expect(await screen.findByRole('alert')).toHaveTextContent('无法载入项目信息，请重试。')
    expect(screen.queryByRole('textbox', { name: '项目简介' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '保存' })).toBeDisabled()

    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    expect(await screen.findByRole('textbox', { name: '项目简介' })).toHaveValue('已恢复简介')
  })
})
