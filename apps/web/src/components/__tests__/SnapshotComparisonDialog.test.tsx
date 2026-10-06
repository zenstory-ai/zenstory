import { act, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import SnapshotComparisonDialog from '../SnapshotComparisonDialog'
import type { SnapshotComparison } from '../../types'

const mockCompare = vi.fn()
const mockFileGet = vi.fn()
const loggerError = vi.fn()

const { mockT } = vi.hoisted(() => ({
  mockT: vi.fn((key: string) =>
    (
      {
        'editor:versionHistory.snapshotCompareTitle': 'Snapshot Compare',
        'editor:versionHistory.loadFailed': 'Failed to load comparison',
        'editor:versionHistory.comparing': 'Comparing snapshots',
        'editor:versionHistory.oldVersion': 'Old version',
        'editor:versionHistory.newVersion': 'New version',
        'editor:versionHistory.added': 'added',
        'editor:versionHistory.removed': 'removed',
        'editor:versionHistory.modified': 'modified',
        'editor:versionHistory.noDiff': 'No differences',
        'editor:versionHistory.addedFiles': 'Added files',
        'editor:versionHistory.removedFiles': 'Removed files',
        'editor:versionHistory.modifiedFiles': 'Modified files',
        'editor:versionHistory.versionPrefix': 'Version',
        'common:close': 'Close',
      } as Record<string, string>
    )[key] ?? key),
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: mockT,
  }),
}))

vi.mock('../../lib/api', () => ({
  versionApi: {
    compare: (...args: unknown[]) => mockCompare(...args),
  },
  fileApi: {
    get: (...args: unknown[]) => mockFileGet(...args),
  },
}))

vi.mock('../../lib/dateUtils', () => ({
  formatFullDate: (value: string) => `formatted:${value}`,
}))

vi.mock('../../lib/logger', () => ({
  logger: {
    error: (...args: unknown[]) => loggerError(...args),
  },
}))

vi.mock('../ui/Modal', () => ({
  Modal: ({
    open,
    title,
    footer,
    children,
  }: {
    open: boolean
    title: React.ReactNode
    footer: React.ReactNode
    children?: React.ReactNode
  }) => (open ? <div><div>{title}</div>{children}{footer}</div> : null),
}))

const comparison = (label: string) => ({
  snapshot1: { id: `${label}-old`, created_at: `2026-04-0${label === 'A' ? 1 : 3}T00:00:00Z` },
  snapshot2: { id: `${label}-new`, created_at: `2026-04-0${label === 'A' ? 2 : 4}T00:00:00Z` },
  changes: {
    added: [],
    removed: [],
    modified: [
      {
        file_id: `file-${label}`,
        old_version: 1,
        new_version: 2,
        old_title: `${label} old title`,
        new_title: `${label} new title`,
        old_file_type: 'draft',
        new_file_type: 'draft',
        metadata_changes: {},
      },
    ],
  },
})

const deferred = <T,>() => {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

describe('SnapshotComparisonDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders historical file metadata without fetching live files', async () => {
    const historicalFolder: SnapshotComparison['changes']['added'][number] = {
      file_id: 'folder-added',
      title: 'Historical Folder',
      file_type: 'folder',
      file_metadata: '{"folder_type":"draft"}',
    }
    mockCompare.mockResolvedValue({
      snapshot1: { id: 'snap-1', created_at: '2026-04-01T00:00:00Z' },
      snapshot2: { id: 'snap-2', created_at: '2026-04-02T00:00:00Z' },
      changes: {
        added: [historicalFolder],
        removed: [
          {
            file_id: 'file-removed',
            title: 'Removed Historical Draft',
            file_type: 'draft',
            version_number: 2,
            version_id: 'version-2',
          },
        ],
        modified: [
          {
            file_id: 'file-renamed',
            old_version: 3,
            new_version: 3,
            old_title: 'Before Rename',
            new_title: 'After Rename',
            old_file_type: 'draft',
            new_file_type: 'draft',
            metadata_changes: {
              title: { old: 'Before Rename', new: 'After Rename' },
            },
          },
        ],
      },
    })

    const { container } = render(
      <SnapshotComparisonDialog snapshotId1="snap-1" snapshotId2="snap-2" onClose={vi.fn()} />,
    )

    expect(await screen.findByText('Historical Folder')).toBeInTheDocument()
    expect(screen.getByText('Removed Historical Draft')).toBeInTheDocument()
    expect(screen.getByText('Before Rename')).toBeInTheDocument()
    expect(screen.getByText('After Rename')).toBeInTheDocument()
    expect(screen.queryByText(/Version\s+(?:undefined|null)/)).not.toBeInTheDocument()
    expect(screen.getAllByText(/^Version \d+$/)).toHaveLength(2)
    expect(container.querySelector('.lucide-folder')).toBeInTheDocument()
    expect(mockCompare).toHaveBeenCalledTimes(1)
    expect(mockFileGet).not.toHaveBeenCalled()
  })

  it('ignores an older comparison that resolves after a newer request', async () => {
    const first = deferred<ReturnType<typeof comparison>>()
    const second = deferred<ReturnType<typeof comparison>>()
    mockCompare.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)

    const { rerender } = render(
      <SnapshotComparisonDialog snapshotId1="snap-a1" snapshotId2="snap-a2" onClose={vi.fn()} />,
    )
    rerender(
      <SnapshotComparisonDialog snapshotId1="snap-b1" snapshotId2="snap-b2" onClose={vi.fn()} />,
    )

    await act(async () => {
      second.resolve(comparison('B'))
      await second.promise
    })
    expect(await screen.findByText('B new title')).toBeInTheDocument()

    await act(async () => {
      first.resolve(comparison('A'))
      await first.promise
    })
    expect(screen.getByText('B new title')).toBeInTheDocument()
    expect(screen.queryByText('A new title')).not.toBeInTheDocument()
  })

  it('renders the no-diff and error states', async () => {
    mockCompare.mockResolvedValueOnce({
      snapshot1: { id: 'snap-1', created_at: '2026-04-01T00:00:00Z' },
      snapshot2: { id: 'snap-2', created_at: '2026-04-02T00:00:00Z' },
      changes: {
        added: [],
        removed: [],
        modified: [],
      },
    })

    const { rerender } = render(
      <SnapshotComparisonDialog snapshotId1="snap-1" snapshotId2="snap-2" onClose={vi.fn()} />,
    )

    expect(await screen.findByText('No differences')).toBeInTheDocument()

    mockCompare.mockRejectedValueOnce(new Error('compare failed'))
    rerender(<SnapshotComparisonDialog snapshotId1="snap-a" snapshotId2="snap-b" onClose={vi.fn()} />)

    await waitFor(() => {
      expect(loggerError).toHaveBeenCalledWith('Failed to load comparison:', expect.any(Error))
      expect(screen.getByText('Failed to load comparison')).toBeInTheDocument()
    })
  })
})
