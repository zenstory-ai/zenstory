import { act, render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { VersionHistoryPanel } from '../VersionHistoryPanel'
import * as React from 'react'
import * as api from '../../lib/api'

const { mockLoggerError, mockLoggerWarn, mockToastError } = vi.hoisted(() => ({
  mockLoggerError: vi.fn(),
  mockLoggerWarn: vi.fn(),
  mockToastError: vi.fn(),
}))

vi.mock('../../lib/toast', () => ({
  toast: { error: mockToastError, success: vi.fn(), info: vi.fn() },
}))

// Mock API calls
vi.mock('../../lib/api', () => ({
  versionApi: {
    getSnapshots: vi.fn(),
    updateSnapshot: vi.fn(),
    rollback: vi.fn(),
  },
}))

vi.mock('../../lib/logger', () => ({
  logger: {
    debug: vi.fn(),
    info: vi.fn(),
    warn: (...args: unknown[]) => mockLoggerWarn(...args),
    error: (...args: unknown[]) => mockLoggerError(...args),
    log: vi.fn(),
  },
  default: {
    debug: vi.fn(),
    info: vi.fn(),
    warn: (...args: unknown[]) => mockLoggerWarn(...args),
    error: (...args: unknown[]) => mockLoggerError(...args),
    log: vi.fn(),
  },
}))

// Mock dependencies
vi.mock('../../lib/dateUtils', () => ({
  formatRelativeTimeWithYear: vi.fn(() => '2 hours ago'),
}))

vi.mock('../../hooks/useMediaQuery', () => ({
  useIsMobile: () => false,
}))

vi.mock('../SnapshotComparisonDialog', () => ({
  SnapshotComparisonDialog: ({ snapshotId1, snapshotId2, onClose }: { snapshotId1: string; snapshotId2: string; onClose: () => void }) => (
    <div data-testid="comparison-dialog">
      Comparing {snapshotId1} and {snapshotId2}
      <button onClick={onClose}>Close</button>
    </div>
  ),
}))

// Mock i18n - create a minimal i18n object
const mockT = vi.fn((key: string) => {
  const translations: Record<string, string> = {
    'editor:versionHistory.title': 'Version History',
    'common:loading': 'Loading...',
    'common:retry': 'Retry',
    'editor:versionHistory.loadMore': 'Load more',
    'editor:versionHistory.updateFailed': 'Description update failed',
    'editor:versionHistory.loadFailed': 'Failed to load versions',
    'editor:versionHistory.empty': 'No versions available',
    'editor:versionHistory.auto': 'Auto',
    'editor:versionHistory.manual': 'Manual',
    'editor:versionHistory.beforeAI': 'Before AI',
    'editor:versionHistory.beforeRollback': 'Before Rollback',
    'editor:versionHistory.currentVersion': 'Current',
    'editor:versionHistory.latestSaved': 'Latest saved snapshot',
    'editor:versionHistory.selectCompare': 'Select for comparison',
    'editor:versionHistory.rollbackTo': 'Rollback to this version',
    'editor:versionHistory.compare': 'Compare',
    'editor:versionHistory.addDescription': 'Add a description',
    'editor:versionHistory.noDescription': 'No description',
    'editor:versionHistory.confirmRollback': 'Are you sure you want to rollback?',
    'editor:versionHistory.confirmRollbackTitle': 'Restore this snapshot?',
    'editor:versionHistory.confirmRollbackButton': 'Restore',
    'common:cancel': 'Cancel',
    'common:close': 'Close panel',
    'editor:versionHistory.rollbackFailed': 'Rollback failed',
    'editor:versionHistory.files': 'files',
    'editor:versionHistory.folders': 'folders',
  }
  return translations[key] || key
})
let activeTranslator = mockT

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: activeTranslator,
    i18n: {
      language: 'en',
      changeLanguage: vi.fn(),
    },
  }),
  I18nextProvider: ({ children }: { children: React.ReactNode }) => React.createElement(React.Fragment, null, children),
}))

const confirmRestore = async () => {
  fireEvent.click(await screen.findByRole('button', { name: 'Restore' }))
}

const mockSnapshots = [
  {
    id: 'snap-1',
    created_at: '2024-01-01T12:00:00Z',
    description: 'First outline pass',
    snapshot_type: 'manual',
    data: JSON.stringify({
      file_versions: [{ id: 'v1', content: 'Version 1 content' }],
      files_metadata: [
        { file_type: 'folder' },
        { file_type: 'draft' },
      ],
    }),
  },
  {
    id: 'snap-2',
    created_at: '2024-01-01T13:00:00Z',
    description: 'After AI edit',
    snapshot_type: 'auto',
    data: JSON.stringify({
      file_versions: [{ id: 'v2', content: 'Version 2 content' }],
      files_metadata: [
        { file_type: 'folder' },
      ],
    }),
  },
]

describe('VersionHistoryPanel', () => {
  const mockOnClose = vi.fn()
  const mockOnRollback = vi.fn()
  const mockOnCompare = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    activeTranslator = mockT
    vi.mocked(api.versionApi.getSnapshots).mockReset().mockResolvedValue(mockSnapshots)
    vi.mocked(api.versionApi.updateSnapshot).mockReset().mockResolvedValue(undefined)
    vi.mocked(api.versionApi.rollback).mockReset().mockResolvedValue(undefined)
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('renders version history panel', () => {
    render(
      <VersionHistoryPanel
        projectId="project-1"
        onClose={mockOnClose}
      />
    )

    expect(screen.getByText('Version History')).toBeInTheDocument()
  })

  it('displays loading state initially', () => {
    vi.mocked(api.versionApi.getSnapshots).mockImplementation(() => new Promise(() => {}))

    render(
      <VersionHistoryPanel
        projectId="project-1"
        onClose={mockOnClose}
      />
    )

    expect(screen.getByText(/loading/i)).toBeInTheDocument()
  })

  it('displays version list after loading', async () => {
    render(
      <VersionHistoryPanel
        projectId="project-1"
        onClose={mockOnClose}
      />
    )

    await waitFor(() => {
      expect(screen.getByText('First outline pass')).toBeInTheDocument()
      expect(screen.getByText('After AI edit')).toBeInTheDocument()
    })
  })

  it('shows version timestamps', async () => {
    render(
      <VersionHistoryPanel
        projectId="project-1"
        onClose={mockOnClose}
      />
    )

    await waitFor(() => {
      // There are multiple "2 hours ago" texts (one per snapshot)
      const timestamps = screen.getAllByText('2 hours ago')
      expect(timestamps.length).toBeGreaterThan(0)
    })
  })

  it('shows snapshot type labels', async () => {
    render(
      <VersionHistoryPanel
        projectId="project-1"
        onClose={mockOnClose}
      />
    )

    await waitFor(() => {
      expect(screen.getByText(/manual/i)).toBeInTheDocument()
      expect(screen.getByText(/auto/i)).toBeInTheDocument()
    })
  })

  it('displays file and folder counts', async () => {
    render(
      <VersionHistoryPanel
        projectId="project-1"
        onClose={mockOnClose}
      />
    )

    // First wait for the snapshots to load
    await waitFor(() => {
      expect(screen.getByText('First outline pass')).toBeInTheDocument()
    })

    // Then check for file and folder count labels
    // The translation mock returns 'files' and 'folders' for the keys
    // Use getAllByText since there are multiple snapshots showing these labels
    const filesElements = screen.getAllByText(/files/)
    const foldersElements = screen.getAllByText(/folders/)
    expect(filesElements.length).toBeGreaterThan(0)
    expect(foldersElements.length).toBeGreaterThan(0)
  })

  it('shows empty state when no snapshots', async () => {
    vi.mocked(api.versionApi.getSnapshots).mockResolvedValue([])

    render(
      <VersionHistoryPanel
        projectId="project-1"
        onClose={mockOnClose}
      />
    )

    await waitFor(() => {
      expect(screen.getByText(/no.*versions/i)).toBeInTheDocument()
    })
  })

  it('shows error state on load failure', async () => {
    vi.mocked(api.versionApi.getSnapshots).mockRejectedValue(new Error('Failed to load'))

    render(
      <VersionHistoryPanel
        projectId="project-1"
        onClose={mockOnClose}
      />
    )

    await waitFor(() => {
      expect(screen.getByText(/failed/i)).toBeInTheDocument()
      expect(mockLoggerError).toHaveBeenCalled()
    })
  })

  it('selects snapshot for comparison', async () => {
    render(
      <VersionHistoryPanel
        projectId="project-1"
        onClose={mockOnClose}
      />
    )

    await waitFor(() => {
      expect(screen.getByText('First outline pass')).toBeInTheDocument()
    })

    // Click first compare button - use the translated title text
    const compareButtons = screen.getAllByTitle('Select for comparison')
    fireEvent.click(compareButtons[0])

    await waitFor(() => {
      // The button should be selected (have accent class)
      expect(compareButtons[0]).toBeInTheDocument()
    })

    // Click second compare button
    fireEvent.click(compareButtons[1])

    await waitFor(() => {
      // Should show compare button in header
      expect(screen.getByText(/compare/i)).toBeInTheDocument()
    })
  })

  it('opens comparison dialog when two snapshots selected', async () => {
    render(
      <VersionHistoryPanel
        projectId="project-1"
        onClose={mockOnClose}
        onCompare={mockOnCompare}
      />
    )

    await waitFor(() => {
      expect(screen.getByText('First outline pass')).toBeInTheDocument()
    })

    const compareButtons = screen.getAllByTitle('Select for comparison')

    // Select two snapshots
    fireEvent.click(compareButtons[0])
    fireEvent.click(compareButtons[1])

    // Click the compare action button
    const compareActionButton = screen.getByRole('button', { name: /compare/i })
    fireEvent.click(compareActionButton)

    await waitFor(() => {
      expect(screen.getByTestId('comparison-dialog')).toBeInTheDocument()
      expect(mockOnCompare).toHaveBeenCalledWith('snap-1', 'snap-2')
    })
  })

  it('rolls back to selected version', async () => {
    render(
      <VersionHistoryPanel
        projectId="project-1"
        onClose={mockOnClose}
        onRollback={mockOnRollback}
      />
    )

    await waitFor(() => {
      expect(screen.getByText('After AI edit')).toBeInTheDocument()
    })

    // Select the second saved snapshot.
    const rollbackButtons = screen.getAllByTitle('Rollback to this version')
    fireEvent.click(rollbackButtons[1])
    expect(api.versionApi.rollback).not.toHaveBeenCalled()
    await confirmRestore()

    await waitFor(() => {
      expect(api.versionApi.rollback).toHaveBeenCalledWith('snap-2')
      expect(mockOnRollback).toHaveBeenCalledWith('snap-2')
      // The parent reconciles/closes the panel; do not request throwaway history.
      expect(api.versionApi.getSnapshots).toHaveBeenCalledTimes(1)
    })
  })

  it('does not rollback if user cancels confirmation', async () => {
    render(
      <VersionHistoryPanel
        projectId="project-1"
        onClose={mockOnClose}
        onRollback={mockOnRollback}
      />
    )

    await waitFor(() => {
      expect(screen.getByText('After AI edit')).toBeInTheDocument()
    })

    const rollbackButtons = screen.getAllByTitle('Rollback to this version')
    fireEvent.click(rollbackButtons[0])
    expect(await screen.findByText('Are you sure you want to rollback?')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    await waitFor(() => {
      expect(screen.queryByText('Are you sure you want to rollback?')).not.toBeInTheDocument()
    })
    expect(api.versionApi.rollback).not.toHaveBeenCalled()
  })

  it('offers rollback to the latest saved snapshot without claiming it is live state', async () => {
    render(<VersionHistoryPanel projectId="project-1" onClose={mockOnClose} onRollback={mockOnRollback} />)
    await screen.findByText('Latest saved snapshot')
    expect(screen.queryByText('Current')).not.toBeInTheDocument()
    const rollbackButtons=screen.getAllByTitle('Rollback to this version')
    expect(rollbackButtons).toHaveLength(mockSnapshots.length)
    fireEvent.click(rollbackButtons[0])
    await confirmRestore()
    await waitFor(()=>expect(api.versionApi.rollback).toHaveBeenCalledWith('snap-1'))
  })

  it('starts editing description', async () => {
    render(
      <VersionHistoryPanel
        projectId="project-1"
        onClose={mockOnClose}
      />
    )

    await waitFor(() => {
      expect(screen.getByText('First outline pass')).toBeInTheDocument()
    })

    // Get all buttons with Edit2 icon (small w-3.5 h-3.5 icons next to description)
    const allButtons = screen.getAllByRole('button')
    const editButton = allButtons.find(btn => {
      const svg = btn.querySelector('svg')
      if (!svg) return false
      const classAttr = svg.getAttribute('class') || ''
      // Edit2 icon is small (w-3.5 h-3.5) and the button has hover:bg class
      return classAttr.includes('w-3.5') && classAttr.includes('h-3.5')
    })

    expect(editButton).toBeTruthy()
    fireEvent.click(editButton!)

    await waitFor(() => {
      expect(screen.getByPlaceholderText(/add.*description/i)).toBeInTheDocument()
    })
  })

  it('saves edited description', async () => {
    render(
      <VersionHistoryPanel
        projectId="project-1"
        onClose={mockOnClose}
      />
    )

    await waitFor(() => {
      expect(screen.getByText('First outline pass')).toBeInTheDocument()
    })

    // Get all buttons and find edit button by its icon class pattern
    const allButtons = screen.getAllByRole('button')
    const editButton = allButtons.find(btn => {
      const svg = btn.querySelector('svg')
      if (!svg) return false
      const classAttr = svg.getAttribute('class') || ''
      // Edit2 icon is small (w-3.5 h-3.5)
      return classAttr.includes('w-3.5') && classAttr.includes('h-3.5')
    })

    expect(editButton).toBeTruthy()
    fireEvent.click(editButton!)

    const input = await screen.findByPlaceholderText(/add.*description/i)
    fireEvent.change(input, { target: { value: 'Updated description' } })

    // Find save button (check icon) - it's the green button after the input
    const saveButtons = screen.getAllByRole('button').filter(btn => {
      const svg = btn.querySelector('svg')
      if (!svg) return false
      const classAttr = svg.getAttribute('class') || ''
      return classAttr.includes('lucide-check')
    })

    expect(saveButtons.length).toBeGreaterThan(0)
    fireEvent.click(saveButtons[0])

    await waitFor(() => {
      expect(api.versionApi.updateSnapshot).toHaveBeenCalledWith('snap-1', {
        description: 'Updated description',
      })
    })
  })

  it('Escape while editing a description only leaves edit mode', async () => {
    render(
      <VersionHistoryPanel
        projectId="project-1"
        onClose={mockOnClose}
      />
    )
    await waitFor(() => {
      expect(screen.getByText('First outline pass')).toBeInTheDocument()
    })
    const editButton = screen.getAllByRole('button').find(btn => {
      const classAttr = btn.querySelector('svg')?.getAttribute('class') || ''
      return classAttr.includes('w-3.5') && classAttr.includes('h-3.5')
    })
    fireEvent.click(editButton!)
    await screen.findByPlaceholderText(/add.*description/i)

    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => {
      expect(screen.queryByPlaceholderText(/add.*description/i)).not.toBeInTheDocument()
    })
    expect(mockOnClose).not.toHaveBeenCalled()

    fireEvent.keyDown(document, { key: 'Enter' })
    expect(mockOnClose).not.toHaveBeenCalled()
  })

  it('cancels editing description', async () => {
    render(
      <VersionHistoryPanel
        projectId="project-1"
        onClose={mockOnClose}
      />
    )

    await waitFor(() => {
      expect(screen.getByText('First outline pass')).toBeInTheDocument()
    })

    // Get all buttons and find edit button by its icon class pattern
    const allButtons = screen.getAllByRole('button')
    const editButton = allButtons.find(btn => {
      const svg = btn.querySelector('svg')
      if (!svg) return false
      const classAttr = svg.getAttribute('class') || ''
      // Edit2 icon is small (w-3.5 h-3.5)
      return classAttr.includes('w-3.5') && classAttr.includes('h-3.5')
    })

    expect(editButton).toBeTruthy()
    fireEvent.click(editButton!)

    await screen.findByPlaceholderText(/add.*description/i)

    // Find cancel button (X icon) - it's after the save button (in editing mode)
    const cancelButtons = screen.getAllByRole('button').filter(btn => {
      const svg = btn.querySelector('svg')
      if (!svg) return false
      const classAttr = svg.getAttribute('class') || ''
      return classAttr.includes('lucide-x')
    })

    // The last X button should be the cancel button in editing mode
    expect(cancelButtons.length).toBeGreaterThan(0)
    fireEvent.click(cancelButtons[cancelButtons.length - 1])

    await waitFor(() => {
      expect(screen.queryByPlaceholderText(/add.*description/i)).not.toBeInTheDocument()
    })
  })

  it('shows no description placeholder', async () => {
    const snapshotWithoutDescription = {
      id: 'snap-3',
      created_at: '2024-01-01T14:00:00Z',
      description: '',
      snapshot_type: 'auto',
      data: JSON.stringify({
        file_versions: [],
        files_metadata: [],
      }),
    }

    vi.mocked(api.versionApi.getSnapshots).mockResolvedValue([snapshotWithoutDescription])

    render(
      <VersionHistoryPanel
        projectId="project-1"
        onClose={mockOnClose}
      />
    )

    await waitFor(() => {
      expect(screen.getByText(/no.*description/i)).toBeInTheDocument()
    })
  })

  it('closes panel when close button clicked', async () => {
    render(
      <VersionHistoryPanel
        projectId="project-1"
        onClose={mockOnClose}
      />
    )

    // Find close button by its X icon in the header (with rounded-md class)
    const closeButtons = screen.getAllByRole('button').filter(btn => {
      const svg = btn.querySelector('svg')
      if (!svg) return false
      const classAttr = svg.getAttribute('class') || ''
      return classAttr.includes('lucide-x') && btn.className.includes('rounded-md')
    })

    expect(closeButtons.length).toBeGreaterThan(0)
    fireEvent.click(closeButtons[0])
    expect(mockOnClose).toHaveBeenCalled()
  })

  it('filters snapshots by outlineId when provided', async () => {
    render(
      <VersionHistoryPanel
        projectId="project-1"
        outlineId="file-1"
        onClose={mockOnClose}
      />
    )

    await waitFor(() => {
      expect(api.versionApi.getSnapshots).toHaveBeenCalledWith('project-1', {
        fileId: 'file-1',
        limit: 50,
      })
    })
  })

  it('handles different snapshot types', async () => {
    const snapshotTypes = [
      { type: 'pre_ai_edit', label: /before.*ai/i },
      { type: 'pre_rollback', label: /before.*rollback/i },
    ]

    for (const { type, label } of snapshotTypes) {
      vi.clearAllMocks()

      const snapshot = {
        id: `snap-${type}`,
        created_at: '2024-01-01T12:00:00Z',
        description: 'Test',
        snapshot_type: type,
        data: JSON.stringify({
          file_versions: [],
          files_metadata: [],
        }),
      }

      vi.mocked(api.versionApi.getSnapshots).mockResolvedValue([snapshot])

      const { unmount } = render(
        <VersionHistoryPanel
          projectId="project-1"
          onClose={mockOnClose}
        />
      )

      await waitFor(() => {
        expect(screen.getByText(label)).toBeInTheDocument()
      })

      unmount()
    }
  })

  it('loads older snapshot pages after fifty rows and preserves the current selection', async () => {
    const firstPage = Array.from({ length: 50 }, (_, index) => ({ ...mockSnapshots[0], id: `page-${index}`, description: `Page row ${index}` }))
    vi.mocked(api.versionApi.getSnapshots).mockResolvedValueOnce(firstPage).mockResolvedValueOnce([mockSnapshots[1]])
    render(<VersionHistoryPanel projectId="project-1" onClose={mockOnClose} />)
    await screen.findByText('Page row 0')
    fireEvent.click(screen.getAllByTitle('Select for comparison')[0])
    fireEvent.click(screen.getByRole('button', { name: 'Load more' }))
    await screen.findByText('After AI edit')
    expect(api.versionApi.getSnapshots).toHaveBeenLastCalledWith('project-1', { fileId: undefined, limit: 50, offset: 50 })
    expect(screen.getByText('Page row 0')).toBeInTheDocument()
    fireEvent.click(screen.getAllByTitle('Select for comparison')[50])
    expect(screen.getByRole('button', { name: 'Compare' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument()
  })

  it('keeps loaded rows on append failure and retries the same raw offset without duplicates', async () => {
    const firstPage = Array.from({ length: 50 }, (_, index) => ({ ...mockSnapshots[0], id: `page-${index}`, description: `Page row ${index}` }))
    vi.mocked(api.versionApi.getSnapshots).mockResolvedValueOnce(firstPage).mockRejectedValueOnce(new Error('append failed')).mockResolvedValueOnce([firstPage[49], mockSnapshots[1]])
    render(<VersionHistoryPanel projectId="project-1" onClose={mockOnClose} />)
    await screen.findByText('Page row 0')
    fireEvent.click(screen.getByRole('button', { name: 'Load more' }))
    await screen.findByText('Failed to load versions')
    expect(screen.getByText('Page row 0')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await screen.findByText('After AI edit')
    expect(api.versionApi.getSnapshots).toHaveBeenLastCalledWith('project-1', { fileId: undefined, limit: 50, offset: 50 })
    expect(screen.getAllByText('Page row 49')).toHaveLength(1)
  })

  it('ignores an older project response after the current project has loaded', async () => {
    let resolveOld!: (value: typeof mockSnapshots) => void
    const old = new Promise<typeof mockSnapshots>((resolve) => { resolveOld = resolve })
    vi.mocked(api.versionApi.getSnapshots).mockReturnValueOnce(old).mockResolvedValueOnce([{ ...mockSnapshots[1], description: 'Current project' }])
    const view = render(<VersionHistoryPanel projectId="project-1" onClose={mockOnClose} />)
    view.rerender(<VersionHistoryPanel projectId="project-2" onClose={mockOnClose} />)
    await screen.findByText('Current project')
    await act(async () => { resolveOld(mockSnapshots); await old })
    expect(screen.getByText('Current project')).toBeInTheDocument()
    expect(screen.queryByText('First outline pass')).not.toBeInTheDocument()
  })

  it('retries an initial history-load error without closing the panel', async () => {
    vi.mocked(api.versionApi.getSnapshots).mockRejectedValueOnce(new Error('initial failed')).mockResolvedValueOnce(mockSnapshots)
    render(<VersionHistoryPanel projectId="project-1" onClose={mockOnClose} />)
    await screen.findByText('Failed to load versions')
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await screen.findByText('First outline pass')
    expect(mockOnClose).not.toHaveBeenCalled()
  })

  it('keeps the panel mounted and rejects duplicate restore submissions until reconciliation', async () => {
    let finish!: () => void
    const pending = new Promise<void>((resolve) => { finish = resolve })
    vi.mocked(api.versionApi.rollback).mockReturnValueOnce(pending)
    render(<VersionHistoryPanel projectId="project-1" onClose={mockOnClose} onRollback={mockOnRollback} />)
    await screen.findByText('First outline pass')
    const rollback = screen.getAllByTitle('Rollback to this version')[0]
    const close = screen.getAllByRole('button').find((button) => button.querySelector('.lucide-x') && button.className.includes('rounded-md'))!
    try {
      fireEvent.click(rollback)
      await confirmRestore()
      fireEvent.click(rollback)
      expect(screen.queryByRole('button', { name: 'Restore' })).not.toBeInTheDocument()
      expect(close).toBeDisabled()
      fireEvent.click(close)
      expect(mockOnClose).not.toHaveBeenCalled()
      expect(api.versionApi.rollback).toHaveBeenCalledTimes(1)
    } finally {
      await act(async () => { finish(); await pending })
    }
    await waitFor(() => expect(mockOnRollback).toHaveBeenCalledOnce())
    expect(close).toBeEnabled()
  })

  it('shows description update failures instead of silently logging them', async () => {
    vi.mocked(api.versionApi.updateSnapshot).mockRejectedValueOnce(new Error('update failed'))
    render(<VersionHistoryPanel projectId="project-1" onClose={mockOnClose} />)
    await screen.findByText('First outline pass')
    const edit = screen.getAllByRole('button').find((button) => {
      const icon = button.querySelector('svg')
      return icon?.classList.contains('w-3.5') && icon.classList.contains('h-3.5')
    })!
    fireEvent.click(edit)
    fireEvent.change(screen.getByPlaceholderText('Add a description'), { target: { value: 'New description' } })
    const save = screen.getAllByRole('button').find((button) => button.querySelector('.lucide-check'))!
    fireEvent.click(save)
    await screen.findByText('Description update failed')
    expect(screen.getByDisplayValue('New description')).toBeInTheDocument()
  })

  it('does not abandon an in-flight restore when only the translator changes', async () => {
    let finish!: () => void
    const pending = new Promise<void>((resolve) => { finish = resolve })
    vi.mocked(api.versionApi.rollback).mockReturnValueOnce(pending)
    const view = render(<VersionHistoryPanel projectId="project-1" onClose={mockOnClose} onRollback={mockOnRollback} />)
    await screen.findByText('First outline pass')
    try {
      fireEvent.click(screen.getAllByTitle('Rollback to this version')[0])
      await confirmRestore()
      activeTranslator = vi.fn((key: string) => mockT(key))
      view.rerender(<VersionHistoryPanel projectId="project-1" onClose={mockOnClose} onRollback={mockOnRollback} />)
      const close = screen.getAllByRole('button').find((button) => button.querySelector('.lucide-x') && button.className.includes('rounded-md'))!
      expect(close).toBeDisabled()
      expect(api.versionApi.getSnapshots).toHaveBeenCalledTimes(1)
    } finally {
      await act(async () => { finish(); await pending })
    }
    await waitFor(() => expect(mockOnRollback).toHaveBeenCalledOnce())
  })

  it('advances the raw offset before deduplication and allows only one pending page request', async () => {
    const firstPage = Array.from({ length: 50 }, (_, index) => ({ ...mockSnapshots[0], id: `page-${index}`, description: `Page row ${index}` }))
    const secondPage = Array.from({ length: 50 }, (_, index) => ({ ...mockSnapshots[0], id: `page-${49 + index}`, description: `Page row ${49 + index}` }))
    let finish!: (rows: typeof mockSnapshots) => void
    const pending = new Promise<typeof mockSnapshots>((resolve) => { finish = resolve })
    vi.mocked(api.versionApi.getSnapshots).mockResolvedValueOnce(firstPage).mockReturnValueOnce(pending).mockResolvedValueOnce([])
    render(<VersionHistoryPanel projectId="project-1" onClose={mockOnClose} />)
    await screen.findByText('Page row 0')
    const load = screen.getByRole('button', { name: 'Load more' })
    try {
      act(() => {
        fireEvent.click(load)
        fireEvent.click(load)
      })
      expect(api.versionApi.getSnapshots).toHaveBeenCalledTimes(2)
    } finally {
      await act(async () => { finish(secondPage); await pending })
    }
    await screen.findByText('Page row 98')
    expect(screen.getAllByText('Page row 49')).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: 'Load more' }))
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument())
    expect(api.versionApi.getSnapshots).toHaveBeenLastCalledWith('project-1', { fileId: undefined, limit: 50, offset: 100 })
    expect(screen.getAllByTitle('Select for comparison')).toHaveLength(99)
  })

  it.each(['response', 'error'] as const)('discards a stale outline append %s without clearing the new loading state', async (outcome) => {
    const firstPage = Array.from({ length: 50 }, (_, index) => ({ ...mockSnapshots[0], id: `page-${index}`, description: `Page row ${index}` }))
    let finishOld!: (rows: typeof mockSnapshots) => void
    let failOld!: (error: Error) => void
    let finishCurrent!: (rows: typeof mockSnapshots) => void
    const old = new Promise<typeof mockSnapshots>((resolve, reject) => { finishOld = resolve; failOld = reject })
    const current = new Promise<typeof mockSnapshots>((resolve) => { finishCurrent = resolve })
    vi.mocked(api.versionApi.getSnapshots).mockResolvedValueOnce(firstPage).mockReturnValueOnce(old).mockReturnValueOnce(current)
    const view = render(<VersionHistoryPanel projectId="project-1" outlineId="outline-a" onClose={mockOnClose} />)
    await screen.findByText('Page row 0')
    fireEvent.click(screen.getAllByTitle('Select for comparison')[0])
    fireEvent.click(screen.getAllByTitle('Select for comparison')[1])
    fireEvent.click(screen.getByRole('button', { name: 'Compare' }))
    const edit = screen.getAllByRole('button').find((button) => button.querySelector('svg.w-3\\.5.h-3\\.5'))!
    fireEvent.click(edit)
    fireEvent.change(screen.getByPlaceholderText('Add a description'), { target: { value: 'Old outline draft' } })
    fireEvent.click(screen.getByRole('button', { name: 'Load more' }))
    view.rerender(<VersionHistoryPanel projectId="project-1" outlineId="outline-b" onClose={mockOnClose} />)
    try {
      await act(async () => {
        if (outcome === 'error') failOld(new Error('Old append failed'))
        else finishOld(mockSnapshots)
        await old.catch(() => undefined)
      })
      expect(screen.getByText('Loading...')).toBeInTheDocument()
      expect(screen.queryByText('Failed to load versions')).not.toBeInTheDocument()
      expect(screen.queryByTestId('comparison-dialog')).not.toBeInTheDocument()
      expect(screen.queryByPlaceholderText('Add a description')).not.toBeInTheDocument()
    } finally {
      await act(async () => { finishCurrent([{ ...mockSnapshots[1], description: 'Current outline' }]); await current })
    }
    await screen.findByText('Current outline')
    expect(screen.queryByText('Page row 0')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Compare' })).not.toBeInTheDocument()
    expect(api.versionApi.getSnapshots).toHaveBeenLastCalledWith('project-1', { fileId: 'outline-b', limit: 50 })
  })

  it('keeps close and restore disabled until an asynchronous parent reconciliation finishes', async () => {
    let finish!: () => void
    const pending = new Promise<void>((resolve) => { finish = resolve })
    mockOnRollback.mockReturnValueOnce(pending)
    render(<VersionHistoryPanel projectId="project-1" onClose={mockOnClose} onRollback={mockOnRollback} />)
    await screen.findByText('First outline pass')
    fireEvent.click(screen.getAllByTitle('Rollback to this version')[0])
    await confirmRestore()
    try {
      await waitFor(() => expect(mockOnRollback).toHaveBeenCalledOnce())
      const close = screen.getAllByRole('button').find((button) => button.querySelector('.lucide-x') && button.className.includes('rounded-md'))!
      expect(close).toBeDisabled()
      expect(screen.getAllByTitle('Rollback to this version')[0]).toBeDisabled()
      expect(api.versionApi.getSnapshots).toHaveBeenCalledTimes(1)
      fireEvent.click(close)
      expect(mockOnClose).not.toHaveBeenCalled()
    } finally {
      await act(async () => { finish(); await pending })
    }
    await waitFor(() => expect(screen.getAllByTitle('Rollback to this version')[0]).toBeEnabled())
  })

  it('reenables restore after a failed POST without reconciling success', async () => {
    const alert = vi.fn()
    vi.stubGlobal('alert', alert)
    vi.mocked(api.versionApi.rollback).mockRejectedValueOnce(new Error('Restore failed'))
    render(<VersionHistoryPanel projectId="project-1" onClose={mockOnClose} onRollback={mockOnRollback} />)
    await screen.findByText('First outline pass')
    fireEvent.click(screen.getAllByTitle('Rollback to this version')[0])
    await confirmRestore()
    await waitFor(() => expect(mockToastError).toHaveBeenCalledWith('Rollback failed'))
    expect(alert).not.toHaveBeenCalled()
    expect(screen.getAllByTitle('Rollback to this version')[0]).toBeEnabled()
    expect(mockOnRollback).not.toHaveBeenCalled()
    expect(mockOnClose).not.toHaveBeenCalled()
  })

  it('does not reconcile a submitted restore into a different project', async () => {
    let finish!: () => void
    const pending = new Promise<void>((resolve) => { finish = resolve })
    vi.mocked(api.versionApi.rollback).mockReturnValueOnce(pending)
    const view = render(<VersionHistoryPanel projectId="project-1" onClose={mockOnClose} onRollback={mockOnRollback} />)
    await screen.findByText('First outline pass')
    fireEvent.click(screen.getAllByTitle('Rollback to this version')[0])
    await confirmRestore()
    view.rerender(<VersionHistoryPanel projectId="project-2" onClose={mockOnClose} onRollback={mockOnRollback} />)
    await screen.findByText('First outline pass')
    await act(async () => { finish(); await pending })
    expect(mockOnRollback).not.toHaveBeenCalled()
    expect(screen.getAllByTitle('Rollback to this version')[0]).toBeEnabled()
  })

  it.each(['response', 'error'] as const)('ignores a description update %s after changing outline', async (outcome) => {
    let finish!: (snapshot: (typeof mockSnapshots)[number]) => void
    let fail!: (error: Error) => void
    const pending = new Promise<(typeof mockSnapshots)[number]>((resolve, reject) => { finish = resolve; fail = reject })
    vi.mocked(api.versionApi.updateSnapshot).mockReturnValueOnce(pending)
    vi.mocked(api.versionApi.getSnapshots).mockResolvedValueOnce(mockSnapshots).mockResolvedValueOnce([{ ...mockSnapshots[0], description: 'Current outline' }])
    const view = render(<VersionHistoryPanel projectId="project-1" outlineId="outline-a" onClose={mockOnClose} />)
    await screen.findByText('First outline pass')
    const edit = screen.getAllByRole('button').find((button) => button.querySelector('svg.w-3\\.5.h-3\\.5'))!
    fireEvent.click(edit)
    fireEvent.change(screen.getByPlaceholderText('Add a description'), { target: { value: 'Old outline description' } })
    fireEvent.click(screen.getAllByRole('button').find((button) => button.querySelector('.lucide-check'))!)
    view.rerender(<VersionHistoryPanel projectId="project-1" outlineId="outline-b" onClose={mockOnClose} />)
    await screen.findByText('Current outline')
    await act(async () => {
      if (outcome === 'error') fail(new Error('Old description update failed'))
      else finish({ ...mockSnapshots[0], description: 'Old outline description' })
      await pending.catch(() => undefined)
    })
    expect(screen.getByText('Current outline')).toBeInTheDocument()
    expect(screen.queryByText('Old outline description')).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('updates a loaded older snapshot locally without throwing away pages', async () => {
    const firstPage = Array.from({ length: 50 }, (_, index) => ({ ...mockSnapshots[0], id: `page-${index}`, description: `Page row ${index}` }))
    vi.mocked(api.versionApi.getSnapshots).mockResolvedValueOnce(firstPage).mockResolvedValueOnce([mockSnapshots[1]])
    vi.mocked(api.versionApi.updateSnapshot).mockResolvedValueOnce({ ...mockSnapshots[1], description: 'Updated older snapshot' })
    render(<VersionHistoryPanel projectId="project-1" onClose={mockOnClose} />)
    await screen.findByText('Page row 0')
    fireEvent.click(screen.getByRole('button', { name: 'Load more' }))
    await screen.findByText('After AI edit')
    const edits = screen.getAllByRole('button').filter((button) => button.querySelector('svg.w-3\\.5.h-3\\.5'))
    fireEvent.click(edits[50])
    fireEvent.change(screen.getByPlaceholderText('Add a description'), { target: { value: 'Updated older snapshot' } })
    fireEvent.click(screen.getAllByRole('button').find((button) => button.querySelector('.lucide-check'))!)
    await screen.findByText('Updated older snapshot')
    expect(screen.getByText('Page row 0')).toBeInTheDocument()
    expect(api.versionApi.getSnapshots).toHaveBeenCalledTimes(2)
    expect(screen.getAllByTitle('Select for comparison')).toHaveLength(51)
  })

  it('closes on Escape like other dialogs', async () => {
    render(<VersionHistoryPanel projectId="project-1" onClose={mockOnClose} />)
    await screen.findByText('First outline pass')
    expect(screen.getByRole('dialog')).toHaveAccessibleName('Version History')

    fireEvent.keyDown(document, { key: 'Escape' })

    expect(mockOnClose).toHaveBeenCalledOnce()
  })

  it('ignores Escape while a restore is still reconciling', async () => {
    let finish!: () => void
    const pending = new Promise<void>((resolve) => { finish = resolve })
    vi.mocked(api.versionApi.rollback).mockReturnValueOnce(pending)
    render(<VersionHistoryPanel projectId="project-1" onClose={mockOnClose} onRollback={mockOnRollback} />)
    await screen.findByText('First outline pass')
    fireEvent.click(screen.getAllByTitle('Rollback to this version')[0])
    await confirmRestore()
    try {
      fireEvent.keyDown(document, { key: 'Escape' })
      expect(mockOnClose).not.toHaveBeenCalled()
    } finally {
      await act(async () => { finish(); await pending })
    }
  })

  it('confirms restores in an in-app dialog, never the native confirm', async () => {
    const nativeConfirm = vi.fn(() => true)
    vi.stubGlobal('confirm', nativeConfirm)
    render(<VersionHistoryPanel projectId="project-1" onClose={mockOnClose} onRollback={mockOnRollback} />)
    await screen.findByText('First outline pass')
    fireEvent.click(screen.getAllByTitle('Rollback to this version')[0])

    const dialog = await screen.findByRole('dialog', { name: 'Restore this snapshot?' })
    expect(dialog).toHaveTextContent('Are you sure you want to rollback?')
    // Escape dismisses only the confirmation, not the snapshot panel.
    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Restore this snapshot?' })).not.toBeInTheDocument())
    expect(mockOnClose).not.toHaveBeenCalled()
    expect(api.versionApi.rollback).not.toHaveBeenCalled()
    expect(nativeConfirm).not.toHaveBeenCalled()
  })

  it('shows machine-written snapshot descriptions in plain language', async () => {
    vi.mocked(api.versionApi.getSnapshots).mockResolvedValueOnce([
      { ...mockSnapshots[0], description: 'AI 对话完成 - 文件已修改' },
      { ...mockSnapshots[1], description: 'Before rollback to snapshot 0f8fad5b-d9cb-469f-a165-70867728950e' },
    ])
    render(<VersionHistoryPanel projectId="project-1" onClose={mockOnClose} />)
    expect(await screen.findByText('versions:summary.aiRunCheckpoint')).toBeInTheDocument()
    expect(screen.getByText('versions:summary.beforeRestore')).toBeInTheDocument()
    expect(screen.queryByText(/0f8fad5b/)).not.toBeInTheDocument()
  })
})
