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

  it('uses unified BookMarked icon, borderless intro, icon-free save button, and auto-resizing textareas', async () => {
    const scrollHeights: Record<string, number> = {
      'summary placeholder': 40,
      'style placeholder': 130,
      'phase placeholder': 360,
      'notes placeholder': 100,
    }
    const originalScrollHeight = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollHeight')
    Object.defineProperty(HTMLElement.prototype, 'scrollHeight', {
      configurable: true,
      get() {
        const placeholder = this.getAttribute?.('placeholder')
        return (placeholder && scrollHeights[placeholder]) ?? 0
      },
    })

    try {
      mockProjectGet.mockResolvedValueOnce({
        id: 'project-1',
        name: 'Test Project',
        description: 'desc',
        summary: '',
        writing_style: '克制凝练',
        current_phase: '很长的当前阶段描述',
        notes: '第一条约定',
      })

      const { container } = render(<ProjectStatusDialog isOpen={true} onClose={vi.fn()} projectId="project-1" />)
      const summary = await screen.findByRole('textbox', { name: '项目简介' })
      const style = screen.getByRole('textbox', { name: '写作风格' })
      const phase = screen.getByRole('textbox', { name: '当前阶段' })
      const notes = screen.getByRole('textbox', { name: '写作约定' })

      // Unified BookMarked icon in title, no Brain or Sparkles icon, no floppy Save icon in footer button
      expect(container.querySelector('svg.lucide-book-marked')).not.toBeNull()
      expect(container.querySelector('svg.lucide-brain')).toBeNull()
      expect(container.querySelector('svg.lucide-sparkles')).toBeNull()
      const saveButton = screen.getByRole('button', { name: '保存' })
      expect(saveButton.querySelector('svg.lucide-save')).toBeNull()

      // Auto-resize bounds: min 72px, natural content height in between, capped at 224px
      expect(summary.style.height).toBe('72px')
      expect(style.style.height).toBe('130px')
      expect(phase.style.height).toBe('224px')
      expect(notes.style.height).toBe('100px')

      // Mobile-safe >=16px text, auto-sized without manual resize conflict, and internal scrolling on all 4 textareas
      for (const field of [summary, style, phase, notes]) {
        expect(field.className).toContain('text-base')
        expect(field.className).toContain('md:text-sm')
        expect(field.className).toContain('overflow-y-auto')
        expect(field.className).toContain('resize-none')
        expect(field.className).not.toContain('resize-y')
      }
    } finally {
      if (originalScrollHeight) {
        Object.defineProperty(HTMLElement.prototype, 'scrollHeight', originalScrollHeight)
      } else {
        delete (HTMLElement.prototype as { scrollHeight?: number }).scrollHeight
      }
    }
  })
})
