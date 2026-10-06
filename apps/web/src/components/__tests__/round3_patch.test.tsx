/**
 * Round-3 补丁组回归：Editor 的两条整篇覆盖写入口必须都不丢用户输入。
 *
 * 1) 409 stale_write 分支原本无条件 setEditContent(服务端正文)，把用户尚未保存的
 *    本地编辑整段替换掉，没有备份/合并/撤销入口——只是把「AI 被用户覆盖」换成了
 *    「用户被服务端覆盖」，同样是不可逆的数据丢失。
 * 2) handleFinishReview 既不带 base_updated_at（无保护的整篇覆盖），成功后也不
 *    同步返回的 updated_at。于是「接受一次 diff review → 继续打字 → 自动保存」
 *    必然命中 409，用户这一轮输入被 409 分支处理掉。两条缺陷叠加即确定性数据丢失。
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import * as React from 'react'

import { Editor } from '../Editor'
import * as api from '../../lib/api'
import { ApiError } from '../../lib/apiClient'

vi.mock('../SimpleEditor', () => ({
  SimpleEditor: ({
    fileId,
    fileTitle,
    baseUpdatedAt,
    content,
    onTitleChange,
    onContentChange,
    onSave,
    onFinishReview,
  }: {
    fileId: string
    fileTitle: string
    baseUpdatedAt?: string
    content: string
    onTitleChange?: (value: string) => void
    onContentChange?: (value: string) => void
    onSave?: (submission: {fileId:string;title:string;content:string;previousTitle:string;baseUpdatedAt?:string}) => void
    onFinishReview?: () => void
  }) => (
    <div data-testid="simple-editor">
      <input
        data-testid="title-input"
        value={fileTitle}
        onChange={(e) => onTitleChange?.(e.target.value)}
      />
      <textarea
        data-testid="content-input"
        value={content}
        onChange={(e) => onContentChange?.(e.target.value)}
      />
      <button data-testid="save-button" onClick={() => onSave?.({fileId,title:fileTitle,content,baseUpdatedAt,previousTitle:"Test Chapter"})}>
        Save
      </button>
      <button data-testid="finish-review-button" onClick={() => onFinishReview?.()}>
        Finish Review
      </button>
    </div>
  ),
}))

vi.mock('../subscription/UpgradePromptModal', () => ({
  UpgradePromptModal: () => null,
}))

vi.mock('../../lib/api', () => ({
  fileApi: {
    get: vi.fn(),
    getTree: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
  },
  fileVersionApi: {
    getVersions: vi.fn(),
    createVersion: vi.fn(),
  },
}))

vi.mock('../../lib/toast', () => ({
  toast: { error: vi.fn(), success: vi.fn() },
}))

vi.mock('../../lib/analytics', () => ({
  trackEvent: vi.fn(),
  captureException: vi.fn(),
}))

let mockProjectContext: Record<string, unknown> | null = null

vi.mock('../../contexts/MaterialLibraryContext', () => ({
  useMaterialLibraryContext: () => ({
    preview: null,
    isPreviewLoading: false,
    libraries: [],
    clearPreview: vi.fn(),
  }),
  MaterialLibraryProvider: ({ children }: { children: React.ReactNode }) =>
    React.createElement(React.Fragment, null, children),
}))

vi.mock('../../contexts/MaterialAttachmentContext', () => ({
  useMaterialAttachment: () => ({ addMaterial: vi.fn(), removeMaterial: vi.fn() }),
  MaterialAttachmentProvider: ({ children }: { children: React.ReactNode }) =>
    React.createElement(React.Fragment, null, children),
}))

vi.mock('../../contexts/MobileLayoutContext', () => ({
  useMobileLayout: () => ({ isMobile: false }),
  MobileLayoutProvider: ({ children }: { children: React.ReactNode }) =>
    React.createElement(React.Fragment, null, children),
}))

vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => mockProjectContext,
  ProjectProvider: ({ children }: { children: React.ReactNode }) =>
    React.createElement(React.Fragment, null, children),
}))

// 注意：必须返回**同一个**对象。Editor 的 loadData 把 t 放进了 useCallback 依赖，
// 每次渲染都换一个新 t 会让 `useEffect(loadData, [loadData])` 每渲染一次就重新
// 拉一次文件，把用户刚敲进去的本地编辑覆盖回服务端内容——那样测的就不是被测行为了。
vi.mock('react-i18next', () => {
  const translation = { t: (key: string) => key }
  return { useTranslation: () => translation }
})

const BASE_UPDATED_AT = '2026-07-01T00:00:00Z'
const REVIEWED_UPDATED_AT = '2026-07-01T00:05:00Z'

const mockFile = {
  id: 'file-1',
  project_id: 'project-1',
  title: 'Test Chapter',
  content: '原始正文',
  file_type: 'draft',
  parent_id: null,
  updated_at: BASE_UPDATED_AT,
}

function createContext(overrides: Record<string, unknown> = {}) {
  return {
    currentProjectId: 'project-1',
    selectedItem: { id: 'file-1', type: 'draft', title: 'Test Chapter' },
    setSelectedItem: vi.fn(),
    streamingFileId: null,
    streamingContent: '',
    triggerFileTreeRefresh: vi.fn(),
    editorRefreshVersion: 0,
    lastEditedFileId: null,
    aiEditingFileId: null,
    diffReviewState: null,
    enterDiffReview: vi.fn(),
    acceptEdit: vi.fn(),
    rejectEdit: vi.fn(),
    resetEdit: vi.fn(),
    acceptAllEdits: vi.fn(),
    rejectAllEdits: vi.fn(),
    exitDiffReview: vi.fn(),
    applyDiffReviewChanges: vi.fn(),
    ...overrides,
  }
}

function staleWriteError(currentContent: string, currentUpdatedAt: string) {
  return new ApiError(409, 'ERR_RESOURCE_CONFLICT', {
    reason: 'stale_write',
    file_id: 'file-1',
    current_content: currentContent,
    current_updated_at: currentUpdatedAt,
  })
}

describe('Editor: 整篇覆盖写的两条入口都不得丢用户输入', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.spyOn(console, 'error').mockImplementation(() => {})
    mockProjectContext = createContext()
    vi.mocked(api.fileApi.get).mockResolvedValue(mockFile)
    vi.mocked(api.fileApi.getTree).mockResolvedValue({ tree: [] })
    vi.mocked(api.fileVersionApi.getVersions).mockResolvedValue({ total: 1, items: [] })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('409 stale_write 时保留本地编辑，并把服务端正文送进 diff review', async () => {
    const enterDiffReview = vi.fn()
    mockProjectContext = createContext({ enterDiffReview })
    vi.mocked(api.fileApi.update).mockRejectedValue(
      staleWriteError('服务端的新正文', REVIEWED_UPDATED_AT),
    )

    render(<Editor />)
    await waitFor(() => expect(screen.getByTestId('simple-editor')).toBeInTheDocument())

    const textarea = screen.getByTestId('content-input') as HTMLTextAreaElement
    fireEvent.change(textarea, { target: { value: '用户刚敲的一大段内容' } })
    fireEvent.click(screen.getByTestId('save-button'))

    await waitFor(() => expect(enterDiffReview).toHaveBeenCalled())

    // 本地编辑一个字都不能被替换掉
    expect((screen.getByTestId('content-input') as HTMLTextAreaElement).value).toBe(
      '用户刚敲的一大段内容',
    )
    // 服务端正文作为基线进入审阅通道，本地编辑作为待审改动
    expect(enterDiffReview).toHaveBeenCalledWith(
      'file-1',
      '服务端的新正文',
      '用户刚敲的一大段内容',
    )
  })

  it('handleFinishReview 带上 base_updated_at，并把返回的 updated_at 回填', async () => {
    const applyDiffReviewChanges = vi.fn().mockReturnValue('审阅后的定稿')
    const exitDiffReview = vi.fn()
    mockProjectContext = createContext({
      applyDiffReviewChanges,
      exitDiffReview,
      diffReviewState: {
        isReviewing: true,
        fileId: 'file-1',
        originalContent: '原始正文',
        modifiedContent: '审阅后的定稿',
        pendingEdits: [],
      },
    })
    vi.mocked(api.fileApi.update).mockResolvedValue({
      ...mockFile,
      content: '审阅后的定稿',
      updated_at: REVIEWED_UPDATED_AT,
    })

    render(<Editor />)
    await waitFor(() => expect(screen.getByTestId('simple-editor')).toBeInTheDocument())

    fireEvent.click(screen.getByTestId('finish-review-button'))

    await waitFor(() =>
      expect(api.fileApi.update).toHaveBeenCalledWith(
        'file-1',
        expect.objectContaining({ base_updated_at: BASE_UPDATED_AT }),
      ),
    )

    // `fileApi.update` being called only proves the request started. Wait for
    // the resolved response to be committed to Editor state before simulating
    // the user's next save; otherwise this assertion races React's state update
    // and intermittently observes the pre-review concurrency token in CI.
    await waitFor(() => {
      expect(exitDiffReview).toHaveBeenCalled()
      expect((screen.getByTestId('content-input') as HTMLTextAreaElement).value).toBe(
        '审阅后的定稿',
      )
    })

    // 审阅之后紧接一次保存：必须带**新**令牌，否则服务端必判 stale_write
    fireEvent.click(screen.getByTestId('save-button'))
    await waitFor(() => expect(api.fileApi.update).toHaveBeenCalledTimes(2))
    expect(vi.mocked(api.fileApi.update).mock.calls[1][1]).toMatchObject({
      base_updated_at: REVIEWED_UPDATED_AT,
    })
  })

  it('handleFinishReview 撞上 409 时不丢定稿，改为重新进入审阅', async () => {
    const enterDiffReview = vi.fn()
    const exitDiffReview = vi.fn()
    const applyDiffReviewChanges = vi.fn().mockReturnValue('审阅后的定稿')
    mockProjectContext = createContext({
      enterDiffReview,
      exitDiffReview,
      applyDiffReviewChanges,
      diffReviewState: {
        isReviewing: true,
        fileId: 'file-1',
        originalContent: '原始正文',
        modifiedContent: '审阅后的定稿',
        pendingEdits: [],
      },
    })
    vi.mocked(api.fileApi.update).mockRejectedValue(
      staleWriteError('别人刚写的正文', REVIEWED_UPDATED_AT),
    )

    render(<Editor />)
    await waitFor(() => expect(screen.getByTestId('simple-editor')).toBeInTheDocument())

    fireEvent.click(screen.getByTestId('finish-review-button'))

    await waitFor(() =>
      expect(enterDiffReview).toHaveBeenCalledWith(
        'file-1',
        '别人刚写的正文',
        '审阅后的定稿',
      ),
    )
    // 审阅态不能就这么退出：定稿还没落库
    expect(exitDiffReview).not.toHaveBeenCalled()
  })
})
