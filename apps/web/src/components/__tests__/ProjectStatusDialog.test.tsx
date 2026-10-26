import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'

const mockNavigate = vi.fn()
const mockProjectGet = vi.fn()
const inspirationFeature = vi.hoisted(() => ({ enabled: true }))
let projectStatusSubscriber: ((detail: { projectId: string; updatedFields: string[] }) => void) | undefined

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  }
})

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
  inspirationsApi: {
    submit: vi.fn(),
  },
}))

vi.mock('../../lib/projectStatusEvents', () => ({
  subscribeProjectStatusUpdated: (callback: typeof projectStatusSubscriber) => {
    projectStatusSubscriber = callback
    return () => { projectStatusSubscriber = undefined }
  },
}))

vi.mock('../../config/inspirations', () => ({
  inspirationsConfig: inspirationFeature,
}))

import { ProjectStatusDialog } from '../ProjectStatusDialog'

describe('ProjectStatusDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    projectStatusSubscriber = undefined
    inspirationFeature.enabled = true
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

  it('renders updated inspiration action labels', async () => {
    render(<ProjectStatusDialog isOpen={true} onClose={vi.fn()} projectId="project-1" />)

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '投稿到灵感库' })).toBeInTheDocument()
      expect(screen.getByRole('button', { name: '查看灵感库' })).toBeInTheDocument()
    })
  })

  it('navigates to inspiration library and closes dialog', async () => {
    const onClose = vi.fn()

    render(<ProjectStatusDialog isOpen={true} onClose={onClose} projectId="project-1" />)

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '查看灵感库' })).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: '查看灵感库' }))

    expect(onClose).toHaveBeenCalledTimes(1)
    expect(mockNavigate).toHaveBeenCalledWith('/dashboard/inspirations')
  })

  it('hides inspiration actions while keeping save available when disabled', async () => {
    inspirationFeature.enabled = false
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
})
