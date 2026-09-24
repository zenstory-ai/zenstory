/**
 * Round-3 回归（组件层）：
 *
 * #24 追加指令（steering）发送失败时不得清空输入框 —— setInput 会同步把
 *     localStorage 里的草稿覆写成空串，一旦失败用户输入两头都不剩。
 * #12 切换项目时必须释放 ProjectContext 的流式写入态，否则被 AI 新建的
 *     那个文件在编辑器里永远只读。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup, act, renderHook } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { MessageInput } from '../MessageInput'
import { ProjectProvider, useProject } from '../../contexts/ProjectContext'
import { projectApi } from '../../lib/api'

const { mockUseMaterialAttachment, mockUseTextQuote, mockUseSkillTrigger } = vi.hoisted(() => ({
  mockUseMaterialAttachment: vi.fn(() => ({
    attachedMaterials: [],
    removeMaterial: vi.fn(),
  })),
  mockUseTextQuote: vi.fn(() => ({
    quotes: [],
    removeQuote: vi.fn(),
  })),
  mockUseSkillTrigger: vi.fn(() => ({
    pendingTrigger: null,
    consumeTrigger: vi.fn(),
    insertTrigger: vi.fn(),
  })),
}))

const { mockT, mockI18n } = vi.hoisted(() => ({
  mockT: vi.fn((key: string, options?: { returnObjects?: boolean }) => {
    if (key === 'chat:input.staticSuggestions' && options?.returnObjects) {
      return ['Static 1', 'Static 2', 'Static 3']
    }
    return key
  }),
  mockI18n: { language: 'zh' },
}))

vi.mock('../../contexts/MaterialAttachmentContext', () => ({
  useMaterialAttachment: mockUseMaterialAttachment,
}))

vi.mock('../../contexts/TextQuoteContext', () => ({
  useTextQuote: mockUseTextQuote,
}))

vi.mock('../../contexts/SkillTriggerContext', () => ({
  useSkillTrigger: mockUseSkillTrigger,
}))

vi.mock('../../lib/api', () => ({
  skillsApi: {
    list: vi.fn().mockResolvedValue({ skills: [] }),
  },
  projectApi: {
    getAll: vi.fn().mockResolvedValue([]),
    get: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    patch: vi.fn(),
    getTemplates: vi.fn(),
  },
}))

// 必须返回稳定的对象引用：ProjectContext 的 loadProjects 以 user 为依赖，
// 每次渲染都产出新对象会让加载 effect 无限自触发。
const { mockAuthValue } = vi.hoisted(() => ({
  mockAuthValue: { user: { id: 'user-1' }, loading: false },
}))

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => mockAuthValue,
}))

vi.mock('diff-match-patch', () => ({
  diff_match_patch: vi.fn().mockImplementation(function () {
    return {
      diff_main: vi.fn().mockReturnValue([]),
      diff_cleanupSemantic: vi.fn(),
    }
  }),
  DIFF_DELETE: -1,
  DIFF_INSERT: 1,
  DIFF_EQUAL: 0,
}))

vi.mock('../VoiceInputButton', () => ({
  VoiceInputButton: ({ onResult }: { onResult: (text: string) => void }) => (
    <button type="button" aria-label="voice button" onClick={() => onResult('voice result')}>
      Voice
    </button>
  ),
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: mockT, i18n: mockI18n }),
}))

describe('round3 #24 steering 失败不得吞掉用户输入', () => {
  const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})

  const steerProps = {
    onSend: vi.fn(),
    sendDisabled: true,
    onCancel: vi.fn(),
    canSteer: true,
  }

  beforeEach(() => {
    vi.clearAllMocks()
    mockI18n.language = 'zh'
    mockUseMaterialAttachment.mockReturnValue({
      attachedMaterials: [],
      removeMaterial: vi.fn(),
    })
    mockUseTextQuote.mockReturnValue({ quotes: [], removeQuote: vi.fn() })
    mockUseSkillTrigger.mockReturnValue({
      pendingTrigger: null,
      consumeTrigger: vi.fn(),
      insertTrigger: vi.fn(),
    })
  })

  afterEach(() => {
    cleanup()
    consoleErrorSpy.mockRestore()
  })

  it('onSteer 抛错时保留输入框内容，且不把草稿覆写成空串', async () => {
    const user = userEvent.setup({ delay: null })
    const onSteer = vi.fn().mockRejectedValue(new Error('steer failed: 404'))
    const onDraftChange = vi.fn()

    render(<MessageInput {...steerProps} onSteer={onSteer} onDraftChange={onDraftChange} />)

    const textarea = screen.getByPlaceholderText('chat:input.steerPlaceholder')
    await user.type(textarea, '把第三章的结尾改成开放式')
    await user.click(screen.getByTestId('steer-button'))

    await waitFor(() => expect(onSteer).toHaveBeenCalledWith('把第三章的结尾改成开放式'))

    // 输入必须还在
    expect(textarea).toHaveValue('把第三章的结尾改成开放式')
    // 且从未通知父组件「草稿变空」——那一步会把 localStorage 里的草稿抹掉
    expect(onDraftChange.mock.calls.some(([draft]) => draft === '')).toBe(false)
  })

  it('回车触发的 steering 失败时同样保留输入', async () => {
    const user = userEvent.setup({ delay: null })
    const onSteer = vi.fn().mockRejectedValue(new Error('offline'))
    const onDraftChange = vi.fn()

    render(<MessageInput {...steerProps} onSteer={onSteer} onDraftChange={onDraftChange} />)

    const textarea = screen.getByPlaceholderText('chat:input.steerPlaceholder')
    await user.type(textarea, '补一句悬念{Enter}')

    await waitFor(() => expect(onSteer).toHaveBeenCalledWith('补一句悬念'))
    expect(textarea).toHaveValue('补一句悬念')
    expect(onDraftChange.mock.calls.some(([draft]) => draft === '')).toBe(false)
  })

  it('onSteer 成功时照常清空输入并同步草稿', async () => {
    const user = userEvent.setup({ delay: null })
    const onSteer = vi.fn().mockResolvedValue(undefined)
    const onDraftChange = vi.fn()

    render(<MessageInput {...steerProps} onSteer={onSteer} onDraftChange={onDraftChange} />)

    const textarea = screen.getByPlaceholderText('chat:input.steerPlaceholder')
    await user.type(textarea, '换个视角写')
    await user.click(screen.getByTestId('steer-button'))

    await waitFor(() => expect(textarea).toHaveValue(''))
    expect(onDraftChange).toHaveBeenCalledWith('')
  })

  it('发送期间禁用按钮，避免同一条追加指令被重复提交', async () => {
    const user = userEvent.setup({ delay: null })
    let release: (() => void) | null = null
    const onSteer = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          release = resolve
        })
    )

    render(<MessageInput {...steerProps} onSteer={onSteer} />)

    const textarea = screen.getByPlaceholderText('chat:input.steerPlaceholder')
    await user.type(textarea, '慢一点的请求')

    const button = screen.getByTestId('steer-button')
    await user.click(button)

    await waitFor(() => expect(button).toBeDisabled())
    await user.click(button)
    expect(onSteer).toHaveBeenCalledTimes(1)

    await act(async () => {
      release?.()
    })
    await waitFor(() => expect(textarea).toHaveValue(''))
  })
})

describe('round3 #12 切换项目必须释放流式写入态', () => {
  function wrapper({ children }: { children: ReactNode }) {
    return <ProjectProvider>{children}</ProjectProvider>
  }

  const mockProjects = [
    { id: 'project-1', name: '项目一', updated_at: '2026-01-02T00:00:00Z' },
    { id: 'project-2', name: '项目二', updated_at: '2026-01-01T00:00:00Z' },
  ]

  beforeEach(() => {
    localStorage.clear()
    vi.mocked(projectApi.getAll).mockResolvedValue(
      mockProjects as unknown as Awaited<ReturnType<typeof projectApi.getAll>>
    )
  })

  afterEach(() => {
    cleanup()
    localStorage.clear()
  })

  /** 等待 provider 完成初始加载并稳定在某个项目上 */
  async function renderSettled() {
    const rendered = renderHook(() => useProject(), { wrapper })
    await waitFor(() => expect(rendered.result.current.projects.length).toBe(2))
    await waitFor(() => expect(rendered.result.current.currentProjectId).toBeTruthy())
    return rendered
  }

  it('setCurrentProjectId 会清掉遗留的 streamingFileId 与 streamingContent', async () => {
    const { result } = await renderSettled()
    const startingProjectId = result.current.currentProjectId
    const otherProjectId = startingProjectId === 'project-1' ? 'project-2' : 'project-1'

    act(() => {
      result.current.startFileStreaming('file-ai-chapter-1')
      result.current.appendFileContent('file-ai-chapter-1', '夜色如墨，')
    })

    expect(result.current.streamingFileId).toBe('file-ai-chapter-1')
    await waitFor(() => expect(result.current.streamingContent).toBe('夜色如墨，'))

    // 用户点停止后切走项目：旧流已被 abort，永远不会再有 file_content_end
    act(() => {
      result.current.setCurrentProjectId(otherProjectId)
    })

    expect(result.current.streamingFileId).toBeNull()
    expect(result.current.streamingContent).toBe('')
  })

  it('切换项目会清掉旧流遗留的 aiEditingFileId', async () => {
    const { result } = await renderSettled()
    const startingProjectId = result.current.currentProjectId
    const otherProjectId = startingProjectId === 'project-1' ? 'project-2' : 'project-1'

    act(() => {
      result.current.setAiEditingFileId('file-being-edited')
    })
    expect(result.current.aiEditingFileId).toBe('file-being-edited')

    act(() => {
      result.current.setCurrentProjectId(otherProjectId)
    })

    expect(result.current.aiEditingFileId).toBeNull()
  })

  it('重复设置同一个项目 id 不会误伤正在进行的流式写入', async () => {
    const { result } = await renderSettled()
    const startingProjectId = result.current.currentProjectId!

    act(() => {
      result.current.startFileStreaming('file-ai-chapter-1')
      result.current.appendFileContent('file-ai-chapter-1', '正在写的正文')
    })
    await waitFor(() => expect(result.current.streamingContent).toBe('正在写的正文'))

    act(() => {
      result.current.setCurrentProjectId(startingProjectId)
    })

    expect(result.current.streamingFileId).toBe('file-ai-chapter-1')
    expect(result.current.streamingContent).toBe('正在写的正文')
  })

  it('切换项目后新项目的文件仍能正常开始流式写入', async () => {
    const { result } = await renderSettled()
    const startingProjectId = result.current.currentProjectId
    const otherProjectId = startingProjectId === 'project-1' ? 'project-2' : 'project-1'

    act(() => {
      result.current.startFileStreaming('file-old')
      result.current.setCurrentProjectId(otherProjectId)
      result.current.startFileStreaming('file-new')
      result.current.appendFileContent('file-new', '新项目的正文')
    })

    expect(result.current.streamingFileId).toBe('file-new')
    await waitFor(() => expect(result.current.streamingContent).toBe('新项目的正文'))
  })
})
