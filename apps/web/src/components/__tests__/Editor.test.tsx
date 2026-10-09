import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach, afterAll } from 'vitest'
import { Editor } from '../Editor'
import type { SaveResult } from '../SimpleEditor'
import * as React from 'react'
import * as api from '../../lib/api'
import { ApiError } from '../../lib/apiClient'
import {
  createEditorDraftSnapshot,
  getEditorDraftRecoveryKey,
  writeEditorDraftSnapshot,
} from '../../lib/editorDraftRecovery'

const editorTranslations = vi.hoisted(() => ({
  'editor:placeholder.selectFile': 'Select a file to edit',
  'editor:placeholder.folderSelected': 'Folder selected: ',
  'editor:placeholder.folderHint': 'Select a file to view its content',
  'editor:placeholder.loadFailed': 'Failed to load file',
  'common:loading': 'Loading...',
  'editor:emptyStateTitle': 'Ready to Create',
  'editor:emptyStateDescription': 'Select a file from the left panel or create a new one to begin writing your story.',
  'editor:emptyStateHint': 'Tip: Use the AI assistant on the right to help with writing, brainstorming, and more.',
  'editor:fileTree.newDraft': 'New Chapter',
  'editor:fileTree.newOutline': 'New Outline',
  'editor:fileTree.newCharacter': 'New Character Sheet',
  'editor:fileTree.newLore': 'New Setting',
  'editor:fileTree.newScript': 'New Script',
  'editor:emptyStateDescriptionScript': 'Open a file, or create a script or outline.',
  'editor:showMore': 'More options',
  'editor:showLess': 'Show less',
  'editor:fileTree.shortcutHint': 'Ctrl+K',
  'editor:fileTree.searchFiles': 'Search files',
  'editor:draftRecovery.restored': 'Recovered unsaved draft',
  'editor:draftRecovery.conflict': 'Local draft conflicts with the server',
  'editor:draftRecovery.compare': 'Compare versions',
  'editor:draftRecovery.restore': 'Restore local draft',
  'editor:draftRecovery.discard': 'Keep server version',
  'editor:draftRecovery.serverVersion': 'Latest server version',
  'editor:draftRecovery.localVersion': 'Local draft before refresh',
} satisfies Record<string, string>))
const editorTranslator = vi.hoisted(() => ({
  current: (key: string) => editorTranslations[key] || key,
}))
const createEditorTranslator = () => (key: string) => editorTranslations[key] || key

// Mock SimpleEditor component
vi.mock('../SimpleEditor', () => ({
  SimpleEditor: ({ fileId, fileTitle, baseUpdatedAt, content, onTitleChange, onContentChange, onSave, onFlushReady, onFinishReview, isStreaming }: { fileId: string; fileTitle: string; baseUpdatedAt?: string; content: string; onTitleChange?: (value: string) => void; onContentChange?: (value: string) => void; onSave?: (submission: { fileId: string; title: string; content: string; previousTitle: string; baseUpdatedAt?: string }) => Promise<SaveResult>; onFlushReady?: (flush: (() => Promise<'saved' | 'conflict' | 'failed'>) | null) => void; onFinishReview?: () => void; isStreaming?: boolean }) => {
    const dirtyRef = React.useRef(false)
    const draftRef = React.useRef({ fileId, title: fileTitle, content, baseUpdatedAt, previousTitle: 'Test Chapter' })
    if (!dirtyRef.current) draftRef.current = { fileId, title: fileTitle, content, baseUpdatedAt, previousTitle: fileTitle }
    React.useEffect(() => {
      onFlushReady?.(async () => {
        if (!dirtyRef.current || !onSave) return 'saved'
        try {
          const { outcome } = await onSave(draftRef.current)
          if (outcome === 'saved') dirtyRef.current = false
          return outcome
        } catch {
          return 'failed'
        }
      })
      return () => onFlushReady?.(null)
    }, [onFlushReady, onSave])
    return <div data-testid="simple-editor">
      <input
        data-testid="title-input"
        value={fileTitle}
        onChange={(e) => {
          dirtyRef.current = true
          draftRef.current = { ...draftRef.current, title: e.target.value }
          onTitleChange?.(e.target.value)
        }}
      />
      <textarea
        data-testid="content-input"
        value={content}
        onChange={(e) => {
          dirtyRef.current = true
          draftRef.current = { ...draftRef.current, content: e.target.value }
          onContentChange?.(e.target.value)
        }}
      />
      <button data-testid="save-button" onClick={() => onSave?.(draftRef.current)}>
        Save
      </button>
      <button data-testid="finish-review-button" onClick={() => onFinishReview?.()}>
        Finish Review
      </button>
      <div data-testid="content-display">{content}</div>
      {isStreaming && <div data-testid="streaming-indicator">Streaming...</div>}
    </div>
  },
}))

vi.mock('../subscription/UpgradePromptModal', () => ({
  UpgradePromptModal: ({ open, title, onClose }: { open: boolean; title: string; onClose: () => void }) =>
    open ? <div data-testid="upgrade-modal">{title}<button onClick={onClose}>Dismiss</button></div> : null,
}))

// Mock API calls
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

// Mutable state for mocking
let mockProjectContext: {
  currentProject?: { id: string; project_type?: string } | null;
  currentProjectId: string;
  selectedItem: { id: string; type: string; title: string } | null;
  setSelectedItem: () => void;
  streamingFileId: string | null;
  streamingContent: string;
  triggerFileTreeRefresh: () => void;
  editorRefreshVersion: number;
  lastEditedFileId: string | null;
  aiEditingFileId: string | null;
  diffReviewState: unknown;
  enterDiffReview: () => void;
  acceptEdit: () => void;
  rejectEdit: () => void;
  resetEdit: () => void;
  acceptAllEdits: () => void;
  rejectAllEdits: () => void;
  exitDiffReview: () => void;
  applyDiffReviewChanges: () => void;
} | null = null

// Mock contexts
vi.mock('../../contexts/MaterialLibraryContext', () => ({
  useMaterialLibraryContext: () => ({
    preview: null,
    isPreviewLoading: false,
    libraries: [],
    clearPreview: vi.fn(),
  }),
  MaterialLibraryProvider: ({ children }: { children: React.ReactNode }) => React.createElement(React.Fragment, null, children),
}))

vi.mock('../../contexts/MaterialAttachmentContext', () => ({
  useMaterialAttachment: () => ({
    addMaterial: vi.fn(),
    removeMaterial: vi.fn(),
  }),
  MaterialAttachmentProvider: ({ children }: { children: React.ReactNode }) => React.createElement(React.Fragment, null, children),
}))

const viewport = vi.hoisted(() => ({ isMobile: false }))
vi.mock('../../contexts/MobileLayoutContext', () => ({
  useMobileLayout: () => ({
    isMobile: viewport.isMobile,
  }),
  MobileLayoutProvider: ({ children }: { children: React.ReactNode }) => React.createElement(React.Fragment, null, children),
}))

vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => mockProjectContext,
  ProjectProvider: ({ children }: { children: React.ReactNode }) => React.createElement(React.Fragment, null, children),
}))

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'user-1' } }),
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: editorTranslator.current,
  }),
}))

const createMockProjectContext = (overrides = {}) => ({
  currentProjectId: 'project-1',
  selectedItem: null,
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
})

const mockFile = {
  id: 'file-1',
  title: 'Test Chapter',
  content: 'Test content',
  file_type: 'draft',
  parent_id: null,
}

describe('Editor', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    editorTranslator.current = createEditorTranslator()
    viewport.isMobile = false
    vi.spyOn(console, 'error').mockImplementation(() => {})
    mockProjectContext = createMockProjectContext()
    vi.mocked(api.fileApi.get).mockResolvedValue(mockFile)
    vi.mocked(api.fileApi.getTree).mockResolvedValue({
      tree: [
        {
          id: 'project-1-lore-folder',
          title: '设定',
          file_type: 'folder',
          parent_id: null,
          order: 0,
          metadata: null,
          children: [],
        },
        {
          id: 'project-1-character-folder',
          title: '角色',
          file_type: 'folder',
          parent_id: null,
          order: 1,
          metadata: null,
          children: [],
        },
        {
          id: 'project-1-outline-folder',
          title: '大纲',
          file_type: 'folder',
          parent_id: null,
          order: 2,
          metadata: null,
          children: [],
        },
        {
          id: 'project-1-draft-folder',
          title: '正文',
          file_type: 'folder',
          parent_id: null,
          order: 3,
          metadata: null,
          children: [],
        },
      ],
    })
    vi.mocked(api.fileApi.create).mockResolvedValue({
      ...mockFile,
      id: 'file-created',
      file_type: 'draft',
      title: 'New Chapter',
    })
    vi.mocked(api.fileVersionApi.getVersions).mockResolvedValue({ total: 0, items: [] })
    vi.mocked(api.fileApi.update).mockResolvedValue({ ...mockFile, title: 'Updated Title' })
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  afterAll(() => {
    vi.restoreAllMocks()
  })

  it('renders empty state when no file selected', () => {
    mockProjectContext = createMockProjectContext({ selectedItem: null })
    render(<Editor />)
    // Check for enhanced empty state elements
    expect(screen.getByText('Ready to Create')).toBeInTheDocument()
    expect(screen.getByText(/select a file from the left panel/i)).toBeInTheDocument()
  })

  it('renders action cards in empty state', () => {
    mockProjectContext = createMockProjectContext({ selectedItem: null })
    render(<Editor />)
    // Primary action cards are visible by default
    expect(screen.getByText('New Chapter')).toBeInTheDocument()
    expect(screen.getByText('New Outline')).toBeInTheDocument()
    // Secondary actions are behind "more options" toggle
    expect(screen.getByText('More options')).toBeInTheDocument()
  })

  it('creates draft in draft folder from empty state', async () => {
    mockProjectContext = createMockProjectContext({ selectedItem: null })
    render(<Editor />)

    fireEvent.click(screen.getByText('New Chapter'))

    await waitFor(() => {
      expect(api.fileApi.create).toHaveBeenCalledWith(
        'project-1',
        expect.objectContaining({
          file_type: 'draft',
          parent_id: 'project-1-draft-folder',
        })
      )
    })
  })

  it('creates outline in outline folder from empty state', async () => {
    mockProjectContext = createMockProjectContext({ selectedItem: null })
    vi.mocked(api.fileApi.create).mockResolvedValue({
      ...mockFile,
      id: 'file-outline-created',
      file_type: 'outline',
      title: 'New Outline',
    })

    render(<Editor />)

    fireEvent.click(screen.getByText('New Outline'))

    await waitFor(() => {
      expect(api.fileApi.create).toHaveBeenCalledWith(
        'project-1',
        expect.objectContaining({
          file_type: 'outline',
          parent_id: 'project-1-outline-folder',
        })
      )
    })
  })

  it('reveals and creates the secondary empty-state file types', async () => {
    mockProjectContext = createMockProjectContext({ selectedItem: null })
    render(<Editor />)

    fireEvent.click(screen.getByText('More options'))
    fireEvent.click(screen.getByText('New Character Sheet'))
    await waitFor(() => {
      expect(api.fileApi.create).toHaveBeenCalledWith(
        'project-1',
        expect.objectContaining({
          file_type: 'character',
          parent_id: 'project-1-character-folder',
        })
      )
    })

    fireEvent.click(screen.getByText('New Setting'))
    await waitFor(() => {
      expect(api.fileApi.create).toHaveBeenCalledWith(
        'project-1',
        expect.objectContaining({
          file_type: 'lore',
          parent_id: 'project-1-lore-folder',
        })
      )
    })
  })

  it('renders AI assistant hint in empty state', () => {
    mockProjectContext = createMockProjectContext({ selectedItem: null })
    render(<Editor />)
    expect(screen.getByText(/Tip: Use the AI assistant/i)).toBeInTheDocument()
  })

  it('renders keyboard shortcut hint in empty state', () => {
    mockProjectContext = createMockProjectContext({ selectedItem: null })
    render(<Editor />)
    expect(screen.getByText('Ctrl')).toBeInTheDocument()
    expect(screen.getByText('K')).toBeInTheDocument()
    expect(screen.getByText(/Search files/i)).toBeInTheDocument()
  })

  it('shows the ⌘ K shortcut on Apple platforms', () => {
    const platformSpy = vi.spyOn(window.navigator, 'platform', 'get').mockReturnValue('MacIntel')
    try {
      mockProjectContext = createMockProjectContext({ selectedItem: null })
      render(<Editor />)
      expect(screen.getByText('⌘')).toBeInTheDocument()
      expect(screen.queryByText('Ctrl')).not.toBeInTheDocument()
    } finally {
      platformSpy.mockRestore()
    }
  })

  it('hides the keyboard shortcut hint on phones', () => {
    viewport.isMobile = true
    mockProjectContext = createMockProjectContext({ selectedItem: null })
    render(<Editor />)
    expect(screen.queryByText('Ctrl')).not.toBeInTheDocument()
    expect(screen.queryByText(/Search files/i)).not.toBeInTheDocument()
  })

  it('offers a new script instead of a chapter in short drama projects', async () => {
    mockProjectContext = createMockProjectContext({
      selectedItem: null,
      currentProject: { id: 'project-1', project_type: 'screenplay' },
    })
    vi.mocked(api.fileApi.getTree).mockResolvedValue({
      tree: [
        {
          id: 'project-1-script-folder',
          title: '剧本',
          file_type: 'folder',
          parent_id: null,
          order: 0,
          metadata: null,
          children: [],
        },
      ],
    })
    render(<Editor />)

    expect(screen.queryByText('New Chapter')).not.toBeInTheDocument()
    expect(screen.getByText('Open a file, or create a script or outline.')).toBeInTheDocument()
    fireEvent.click(screen.getByText('New Script'))

    await waitFor(() => {
      expect(api.fileApi.create).toHaveBeenCalledWith(
        'project-1',
        expect.objectContaining({
          title: 'New Script',
          file_type: 'script',
          parent_id: 'project-1-script-folder',
        })
      )
    })
  })

  it('renders folder selected state', () => {
    mockProjectContext = createMockProjectContext({
      selectedItem: { id: 'folder-1', type: 'folder', title: 'My Folder' },
    })
    render(<Editor />)
    // The component renders: {t('editor:placeholder.folderSelected')}{selectedItem.title}
    // Which translates to "Folder selected: My Folder" (concatenated text)
    expect(screen.getByText(/folder.*selected.*my folder/i)).toBeInTheDocument()
  })

  it('renders loading state while fetching file', () => {
    vi.mocked(api.fileApi.get).mockImplementation(() => new Promise(() => {}))
    mockProjectContext = createMockProjectContext({
      selectedItem: { id: 'file-1', type: 'draft', title: 'Test Chapter' },
    })
    render(<Editor />)
    expect(screen.getByText(/loading/i)).toBeInTheDocument()
  })

  it('renders error state on fetch failure', async () => {
    vi.mocked(api.fileApi.get).mockRejectedValue(new Error('Failed to fetch'))
    mockProjectContext = createMockProjectContext({
      selectedItem: { id: 'file-1', type: 'draft', title: 'Test Chapter' },
      diffReviewState: null,
    })
    render(<Editor />)
    await waitFor(() => {
      expect(screen.getByText(/failed/i)).toBeInTheDocument()
    })
  })

  it('renders editor with file content', async () => {
    mockProjectContext = createMockProjectContext({
      selectedItem: { id: 'file-1', type: 'draft', title: 'Test Chapter' },
      diffReviewState: null,
    })
    render(<Editor />)
    await waitFor(() => {
      expect(api.fileApi.get).toHaveBeenCalledWith('file-1')
      expect(screen.getByTestId('simple-editor')).toBeInTheDocument()
    })
  })

  it('restores an equal-token local draft without an automatic write', async () => {
    vi.mocked(api.fileApi.get).mockResolvedValue({ ...mockFile, updated_at: 'server-v1' })
    const snapshot = createEditorDraftSnapshot({
      userId: 'user-1',
      projectId: 'project-1',
      fileId: 'file-1',
      title: 'Recovered title',
      content: 'Recovered unsaved body',
      baseUpdatedAt: 'server-v1',
      capturedAt: '2026-10-07T08:00:00.000Z',
    })
    writeEditorDraftSnapshot(localStorage, snapshot)
    mockProjectContext = createMockProjectContext({
      selectedItem: { id: 'file-1', type: 'draft', title: 'Test Chapter' },
    })

    render(<Editor />)

    await waitFor(() => expect(screen.getByTestId('content-input')).toHaveValue('Recovered unsaved body'))
    expect(screen.getByTestId('title-input')).toHaveValue('Recovered title')
    expect(screen.getByText('Recovered unsaved draft')).toBeInTheDocument()
    expect(api.fileApi.update).not.toHaveBeenCalled()
  })

  it('keeps a newer server version visible until the user explicitly restores the draft', async () => {
    vi.mocked(api.fileApi.get).mockResolvedValue({ ...mockFile, updated_at: 'server-v1' })
    const snapshot = createEditorDraftSnapshot({
      userId: 'user-1',
      projectId: 'project-1',
      fileId: 'file-1',
      title: 'Recovered title',
      content: 'Recovered unsaved body',
      baseUpdatedAt: 'server-v0',
      capturedAt: '2026-10-07T08:00:00.000Z',
    })
    writeEditorDraftSnapshot(localStorage, snapshot)
    mockProjectContext = createMockProjectContext({
      selectedItem: { id: 'file-1', type: 'draft', title: 'Test Chapter' },
    })

    render(<Editor />)

    await waitFor(() => expect(screen.getByTestId('content-input')).toHaveValue('Test content'))
    expect(screen.getByRole('alert')).toHaveTextContent('Local draft conflicts with the server')
    fireEvent.click(screen.getByText('Compare versions'))
    expect(screen.getByText('Latest server version')).toBeInTheDocument()
    expect(screen.getByText('Local draft before refresh')).toBeInTheDocument()
    expect(screen.getByText('Test Chapter')).toBeInTheDocument()
    expect(screen.getByText('Recovered title')).toBeInTheDocument()
    expect(screen.getByText('Recovered unsaved body')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Restore local draft' }))
    expect(screen.getByTestId('content-input')).toHaveValue('Recovered unsaved body')
    expect(api.fileApi.update).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Keep server version' }))
    expect(screen.getByTestId('content-input')).toHaveValue('Test content')
    expect(localStorage.getItem(getEditorDraftRecoveryKey({
      userId: 'user-1', projectId: 'project-1', fileId: 'file-1',
    }))).toBeNull()
  })

  it('clears an identical snapshot and clears a recovered snapshot only after its save succeeds', async () => {
    vi.mocked(api.fileApi.get).mockResolvedValue({ ...mockFile, updated_at: 'server-v1' })
    const scope = { userId: 'user-1', projectId: 'project-1', fileId: 'file-1' }
    const identical = createEditorDraftSnapshot({
      ...scope,
      title: 'Test Chapter',
      content: 'Test content',
      baseUpdatedAt: 'server-v0',
      capturedAt: '2026-10-07T07:00:00.000Z',
    })
    writeEditorDraftSnapshot(localStorage, identical)
    mockProjectContext = createMockProjectContext({
      selectedItem: { id: 'file-1', type: 'draft', title: 'Test Chapter' },
    })
    const { unmount } = render(<Editor />)
    await waitFor(() => expect(screen.getByTestId('content-input')).toHaveValue('Test content'))
    expect(localStorage.getItem(getEditorDraftRecoveryKey(scope))).toBeNull()
    unmount()

    const recovered = createEditorDraftSnapshot({
      ...scope,
      title: 'Recovered title',
      content: 'Recovered body',
      baseUpdatedAt: 'server-v1',
      capturedAt: '2026-10-07T08:00:00.000Z',
    })
    writeEditorDraftSnapshot(localStorage, recovered)
    render(<Editor />)
    await waitFor(() => expect(screen.getByTestId('content-input')).toHaveValue('Recovered body'))
    fireEvent.change(screen.getByTestId('content-input'), {
      target: { value: 'Recovered body edited after restore' },
    })
    fireEvent.click(screen.getByTestId('save-button'))
    await waitFor(() => expect(api.fileApi.update).toHaveBeenCalled())
    expect(api.fileApi.update).toHaveBeenCalledWith('file-1', expect.objectContaining({
      content: 'Recovered body edited after restore',
    }))
    expect(localStorage.getItem(getEditorDraftRecoveryKey(scope))).toBeNull()
  })

  it('keeps recovery state when local snapshot removal fails after a successful save', async () => {
    vi.mocked(api.fileApi.get).mockResolvedValue({ ...mockFile, updated_at: 'server-v1' })
    const scope = { userId: 'user-1', projectId: 'project-1', fileId: 'file-1' }
    writeEditorDraftSnapshot(localStorage, createEditorDraftSnapshot({
      ...scope,
      title: 'Recovered title',
      content: 'Recovered body',
      baseUpdatedAt: 'server-v1',
      capturedAt: '2026-10-07T08:00:00.000Z',
    }))
    mockProjectContext = createMockProjectContext({
      selectedItem: { id: 'file-1', type: 'draft', title: 'Test Chapter' },
    })
    render(<Editor />)
    await waitFor(() => expect(screen.getByTestId('content-input')).toHaveValue('Recovered body'))
    const removeSpy = vi.spyOn(localStorage, 'removeItem').mockImplementation(() => {
      throw new Error('storage unavailable')
    })

    fireEvent.click(screen.getByTestId('save-button'))

    await waitFor(() => expect(api.fileApi.update).toHaveBeenCalled())
    expect(removeSpy).toHaveBeenCalledWith(getEditorDraftRecoveryKey(scope))
    expect(screen.getByText('Recovered unsaved draft')).toBeInTheDocument()
  })

  it('does not mix a discarded recovered side into a later one-sided save', async () => {
    vi.mocked(api.fileApi.get).mockResolvedValue({ ...mockFile, updated_at: 'server-v1' })
    writeEditorDraftSnapshot(localStorage, createEditorDraftSnapshot({
      userId: 'user-1',
      projectId: 'project-1',
      fileId: 'file-1',
      title: 'Recovered title',
      content: 'Recovered body',
      baseUpdatedAt: 'server-v0',
      capturedAt: '2026-10-07T08:00:00.000Z',
    }))
    mockProjectContext = createMockProjectContext({
      selectedItem: { id: 'file-1', type: 'draft', title: 'Test Chapter' },
    })
    render(<Editor />)
    await screen.findByRole('alert')
    fireEvent.click(screen.getByRole('button', { name: 'Restore local draft' }))
    fireEvent.click(screen.getByRole('button', { name: 'Keep server version' }))
    fireEvent.change(screen.getByTestId('title-input'), {
      target: { value: 'Server title edited after discard' },
    })
    fireEvent.click(screen.getByTestId('save-button'))

    await waitFor(() => expect(api.fileApi.update).toHaveBeenCalledWith('file-1', expect.objectContaining({
      title: 'Server title edited after discard',
      content: 'Test content',
    })))
  })

  it('retains the recovered snapshot when a concurrent save returns 409', async () => {
    vi.mocked(api.fileApi.get).mockResolvedValue({ ...mockFile, updated_at: 'server-v1' })
    const scope = { userId: 'user-1', projectId: 'project-1', fileId: 'file-1' }
    const recovered = createEditorDraftSnapshot({
      ...scope,
      title: 'Recovered title',
      content: 'Recovered body',
      baseUpdatedAt: 'server-v1',
      capturedAt: '2026-10-07T08:00:00.000Z',
    })
    writeEditorDraftSnapshot(localStorage, recovered)
    vi.mocked(api.fileApi.update).mockRejectedValue(new ApiError(
      409,
      'ERR_STALE_WRITE',
      { reason: 'stale_write', current_content: 'Newer body', current_updated_at: 'server-v2' },
    ))
    mockProjectContext = createMockProjectContext({
      selectedItem: { id: 'file-1', type: 'draft', title: 'Test Chapter' },
    })

    render(<Editor />)
    await waitFor(() => expect(screen.getByTestId('content-input')).toHaveValue('Recovered body'))
    fireEvent.click(screen.getByTestId('save-button'))
    await waitFor(() => expect(api.fileApi.update).toHaveBeenCalled())
    expect(localStorage.getItem(getEditorDraftRecoveryKey(scope))).not.toBeNull()
  })

  it('does not reload or overwrite a dirty draft when the translator identity changes', async () => {
    mockProjectContext = createMockProjectContext({
      selectedItem: { id: 'file-1', type: 'draft', title: 'Test Chapter' },
      diffReviewState: null,
    })
    const UnmemoizedEditor = (Editor as unknown as { type: React.ComponentType }).type
    const { rerender } = render(<UnmemoizedEditor />)
    await waitFor(() => expect(screen.getByTestId('content-input')).toHaveValue('Test content'))

    fireEvent.change(screen.getByTestId('content-input'), {
      target: { value: 'Unsaved local body' },
    })
    editorTranslator.current = createEditorTranslator()
    rerender(<UnmemoizedEditor />)
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(api.fileApi.get).toHaveBeenCalledTimes(1)
    expect(screen.getByTestId('content-input')).toHaveValue('Unsaved local body')
  })

  it('ignores an out-of-order file load after a newer selection resolves', async () => {
    let resolveFileA: (file: typeof mockFile) => void = () => {}
    const fileA = new Promise<typeof mockFile>((resolve) => {
      resolveFileA = resolve
    })
    const fileB = { ...mockFile, id: 'file-b', title: 'File B', content: 'Content B' }
    vi.mocked(api.fileApi.get).mockImplementation((id) =>
      id === 'file-a' ? fileA : Promise.resolve(fileB)
    )
    mockProjectContext = createMockProjectContext({
      selectedItem: { id: 'file-a', type: 'draft', title: 'File A' },
    })
    const UnmemoizedEditor = (Editor as unknown as { type: React.ComponentType }).type
    const { rerender } = render(<UnmemoizedEditor />)

    mockProjectContext = createMockProjectContext({
      selectedItem: { id: 'file-b', type: 'draft', title: 'File B' },
    })
    rerender(<UnmemoizedEditor />)
    await waitFor(() => expect(screen.getByTestId('content-input')).toHaveValue('Content B'))

    resolveFileA({ ...mockFile, id: 'file-a', title: 'File A', content: 'Late A' })
    await waitFor(() => expect(api.fileApi.get).toHaveBeenCalledWith('file-a'))
    expect(screen.getByTestId('content-input')).toHaveValue('Content B')
    expect(api.fileVersionApi.getVersions).not.toHaveBeenCalled()
    expect(api.fileVersionApi.createVersion).not.toHaveBeenCalled()
    expect(screen.queryByTestId('upgrade-modal')).not.toBeInTheDocument()
  })

  it('flushes edits to the previous file before showing a newly selected file', async () => {
    const fileA = { ...mockFile, id: 'file-a', title: 'File A', content: 'Original A' }
    const fileB = { ...mockFile, id: 'file-b', title: 'File B', content: 'Original B' }
    vi.mocked(api.fileApi.get).mockImplementation((id) => Promise.resolve(id === 'file-a' ? fileA : fileB))
    vi.mocked(api.fileApi.update).mockResolvedValue(fileA)
    mockProjectContext = createMockProjectContext({
      selectedItem: { id: 'file-a', type: 'draft', title: 'File A' },
    })
    const UnmemoizedEditor = (Editor as unknown as { type: React.ComponentType }).type
    const { rerender } = render(<UnmemoizedEditor />)
    await waitFor(() => expect(screen.getByTestId('content-input')).toHaveValue('Original A'))
    fireEvent.change(screen.getByTestId('content-input'), { target: { value: 'Edited A' } })

    mockProjectContext = createMockProjectContext({
      selectedItem: { id: 'file-b', type: 'draft', title: 'File B' },
    })
    rerender(<UnmemoizedEditor />)

    await waitFor(() => expect(api.fileApi.update).toHaveBeenCalledWith(
      'file-a',
      expect.objectContaining({ content: 'Edited A' }),
    ))
    await waitFor(() => expect(screen.getByTestId('content-input')).toHaveValue('Original B'))
  })

  it('blocks a file switch and preserves the draft when flushing the previous file fails', async () => {
    const fileA = { ...mockFile, id: 'file-a', title: 'File A', content: 'Original A' }
    const fileB = { ...mockFile, id: 'file-b', title: 'File B', content: 'Original B' }
    vi.mocked(api.fileApi.get).mockImplementation((id) => Promise.resolve(id === 'file-a' ? fileA : fileB))
    vi.mocked(api.fileApi.update).mockRejectedValue(new Error('save failed'))
    mockProjectContext = createMockProjectContext({
      selectedItem: { id: 'file-a', type: 'draft', title: 'File A' },
    })
    const UnmemoizedEditor = (Editor as unknown as { type: React.ComponentType }).type
    const { rerender } = render(<UnmemoizedEditor />)
    await waitFor(() => expect(screen.getByTestId('content-input')).toHaveValue('Original A'))
    fireEvent.change(screen.getByTestId('content-input'), { target: { value: 'Edited A' } })

    mockProjectContext = createMockProjectContext({
      selectedItem: { id: 'file-b', type: 'draft', title: 'File B' },
    })
    rerender(<UnmemoizedEditor />)

    await waitFor(() => expect(api.fileApi.update).toHaveBeenCalledWith(
      'file-a',
      expect.objectContaining({ content: 'Edited A' }),
    ))
    expect(screen.getByTestId('content-input')).toHaveValue('Edited A')
    expect(api.fileApi.get).not.toHaveBeenCalledWith('file-b')
    await waitFor(() => expect(mockProjectContext?.setSelectedItem).toHaveBeenCalledWith(expect.objectContaining({id:'file-a',type:'draft'})))
    const beforeRestore = vi.mocked(api.fileApi.get).mock.calls.length
    mockProjectContext = createMockProjectContext({selectedItem:{id:'file-a',type:'draft',title:'File A'}})
    rerender(<UnmemoizedEditor />)
    await new Promise(resolve=>setTimeout(resolve,0))
    expect(api.fileApi.get).toHaveBeenCalledTimes(beforeRestore)
    expect(screen.getByTestId('content-input')).toHaveValue('Edited A')
  })

  it('loads a populated file without reading or creating versions', async () => {
    vi.mocked(api.fileVersionApi.getVersions).mockResolvedValue({ total: 0, items: [] })
    mockProjectContext = createMockProjectContext({
      selectedItem: { id: 'file-1', type: 'draft', title: 'Test Chapter' },
      diffReviewState: null,
    })
    render(<Editor />)
    await waitFor(() => {
      expect(screen.getByTestId('content-input')).toHaveValue('Test content')
    })
    expect(api.fileApi.get).toHaveBeenCalledWith('file-1')
    expect(api.fileVersionApi.getVersions).not.toHaveBeenCalled()
    expect(api.fileVersionApi.createVersion).not.toHaveBeenCalled()
    expect(screen.queryByTestId('upgrade-modal')).not.toBeInTheDocument()
  })

  it('loads a legacy file with empty version history without bootstrapping it', async () => {
    vi.mocked(api.fileApi.get).mockResolvedValue({ ...mockFile, id: 'legacy-file' })
    vi.mocked(api.fileVersionApi.getVersions).mockResolvedValue({ total: 0, items: [] })
    mockProjectContext = createMockProjectContext({
      selectedItem: { id: 'legacy-file', type: 'draft', title: 'Test Chapter' },
      diffReviewState: null,
    })
    render(<Editor />)
    await waitFor(() => {
      expect(screen.getByTestId('content-input')).toHaveValue('Test content')
    })
    expect(api.fileApi.get).toHaveBeenCalledWith('legacy-file')
    expect(api.fileVersionApi.getVersions).not.toHaveBeenCalled()
    expect(api.fileVersionApi.createVersion).not.toHaveBeenCalled()
    expect(screen.queryByTestId('upgrade-modal')).not.toBeInTheDocument()
  })

  it('displays streaming content when file is being streamed', async () => {
    mockProjectContext = createMockProjectContext({
      selectedItem: { id: 'file-1', type: 'draft', title: 'Test Chapter' },
      streamingFileId: 'file-1',
      streamingContent: 'Streaming content...',
      diffReviewState: null,
    })
    render(<Editor />)
    await waitFor(() => {
      // The streaming content should be displayed in the editor
      expect(screen.getByTestId('content-display')).toHaveTextContent('Streaming content...')
    })
  })

  it('handles save operation', async () => {
    mockProjectContext = createMockProjectContext({
      selectedItem: { id: 'file-1', type: 'draft', title: 'Test Chapter' },
      diffReviewState: null,
    })
    render(<Editor />)
    await waitFor(() => {
      expect(screen.getByTestId('simple-editor')).toBeInTheDocument()
    })
    const saveButton = screen.getByTestId('save-button')
    fireEvent.click(saveButton)
    await waitFor(() => {
      expect(api.fileApi.update).toHaveBeenCalled()
    })
  })

  it('shows upgrade modal when save is blocked by file version quota', async () => {
    vi.mocked(api.fileApi.update).mockRejectedValue(
      new ApiError(402, 'ERR_QUOTA_FILE_VERSIONS_EXCEEDED')
    )
    mockProjectContext = createMockProjectContext({
      selectedItem: { id: 'file-1', type: 'draft', title: 'Test Chapter' },
      diffReviewState: null,
    })

    render(<Editor />)

    await waitFor(() => {
      expect(screen.getByTestId('simple-editor')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId('save-button'))

    await waitFor(() => {
      expect(screen.getByTestId('upgrade-modal')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText('Dismiss'))
    expect(screen.queryByTestId('upgrade-modal')).not.toBeInTheDocument()
  })

  it('triggers file tree refresh when title changes', async () => {
    const triggerFileTreeRefresh = vi.fn()
    mockProjectContext = createMockProjectContext({
      selectedItem: { id: 'file-1', type: 'draft', title: 'Test Chapter' },
      triggerFileTreeRefresh,
      diffReviewState: null,
    })
    render(<Editor />)
    await waitFor(() => {
      expect(screen.getByTestId('simple-editor')).toBeInTheDocument()
    })
    const titleInput = screen.getByTestId('title-input')
    fireEvent.change(titleInput, { target: { value: 'New Title' } })
    // Click save to trigger the refresh
    const saveButton = screen.getByTestId('save-button')
    fireEvent.click(saveButton)
    await waitFor(() => {
      expect(triggerFileTreeRefresh).toHaveBeenCalled()
    })
  })

  it('saves reviewed content with ai version intent', async () => {
    const triggerFileTreeRefresh = vi.fn()
    const exitDiffReview = vi.fn()
    const applyDiffReviewChanges = vi.fn().mockReturnValue('Reviewed AI content')

    vi.mocked(api.fileVersionApi.getVersions).mockResolvedValue({ total: 1, items: [] })
    mockProjectContext = createMockProjectContext({
      selectedItem: { id: 'file-1', type: 'draft', title: 'Test Chapter' },
      diffReviewState: {
        isReviewing: true,
        fileId: 'file-1',
        originalContent: 'Original content',
        modifiedContent: 'Reviewed AI content',
        pendingEdits: [],
      },
      triggerFileTreeRefresh,
      exitDiffReview,
      applyDiffReviewChanges,
    })

    render(<Editor />)

    await waitFor(() => {
      expect(screen.getByTestId('simple-editor')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId('finish-review-button'))

    await waitFor(() => {
      // base_updated_at：审阅写回同样是整篇覆盖写，必须带乐观并发令牌
      // （mockFile 没有 updated_at，因此这里是 undefined，字段本身必须存在）
      expect(api.fileApi.update).toHaveBeenCalledWith('file-1', {
        content: 'Reviewed AI content',
        change_type: 'ai_edit',
        change_summary: 'AI edit (reviewed)',
        base_updated_at: undefined,
      })
      expect(exitDiffReview).toHaveBeenCalled()
      expect(triggerFileTreeRefresh).toHaveBeenCalled()
    })
  })

  it('exits review without saving or versioning when every edit was rejected', async () => {
    const exitDiffReview = vi.fn()
    const applyDiffReviewChanges = vi.fn().mockReturnValue('　　Original content')

    mockProjectContext = createMockProjectContext({
      selectedItem: { id: 'file-1', type: 'draft', title: 'Test Chapter' },
      diffReviewState: {
        isReviewing: true,
        fileId: 'file-1',
        originalContent: '　　Original content',
        modifiedContent: 'Polished content',
        pendingEdits: [{ id: 'edit-0', op: 'replace', oldText: 'a', newText: 'b', status: 'rejected' }],
      },
      exitDiffReview,
      applyDiffReviewChanges,
    })

    render(<Editor />)
    await waitFor(() => {
      expect(screen.getByTestId('simple-editor')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByTestId('finish-review-button'))

    await waitFor(() => {
      expect(exitDiffReview).toHaveBeenCalledTimes(1)
    })
    expect(api.fileApi.update).not.toHaveBeenCalled()
  })
})
