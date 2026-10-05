import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MaterialsPane } from '../MaterialsPane'

const mockBatchImport = vi.fn()
const mockGetCharacters = vi.fn()
const mockSearchMaterials = vi.fn()
const mockToastSuccess = vi.fn()
const mockToastError = vi.fn()
const mockToggleNovel = vi.fn()
const mockToggleEntityType = vi.fn()
const mockLoadPreview = vi.fn()
const mockAddMaterial = vi.fn()
const mockRefreshIfStale = vi.fn()
const libraryState = vi.hoisted(() => ({ accessDenied: false }))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown>) => {
      if (key === 'materials:toast.batchImportSuccess') return `Imported ${String(options?.count ?? '')}`
      if (key === 'materials:toast.batchImportPartialFailed') {
        return `Imported ${String(options?.successCount ?? '')}, failed ${String(options?.failedCount ?? '')}`
      }
      return key
    },
  }),
}))

vi.mock('../../../contexts/ProjectContext', () => ({
  useProject: () => ({
    currentProjectId: 'project-1',
  }),
}))

vi.mock('../../../contexts/MaterialAttachmentContext', () => ({
  useMaterialAttachment: () => ({
    addMaterial: mockAddMaterial,
  }),
}))

vi.mock('../../subscription/MaterialsUpgradePrompt', () => ({
  MaterialsUpgradeNotice: ({ source }: { source: string }) => (
    <div data-testid="materials-upgrade-notice">{source}</div>
  ),
}))

vi.mock('../../../contexts/MaterialLibraryContext', () => ({
  useMaterialLibraryContext: () => ({
    isLoading: false,
    isFetching: false,
    error: null,
    accessDenied: libraryState.accessDenied,
    refetch: vi.fn(),
    refreshIfStale: mockRefreshIfStale,
    libraries: libraryState.accessDenied ? [] : [
      {
        id: 1,
        title: 'Novel One',
        status: 'completed',
        counts: {
          characters: 2,
          worldview: 0,
          golden_fingers: 0,
          storylines: 0,
          stories: 0,
          relationships: 2,
        },
      },
    ],
    expandedNovels: new Set([1]),
    expandedTypes: new Map([['1:characters', true]]),
    toggleNovel: mockToggleNovel,
    toggleEntityType: mockToggleEntityType,
    loadPreview: mockLoadPreview,
  }),
}))

vi.mock('../../../config/materials', () => ({
  materialsConfig: {
    relationshipsEnabled: false,
  },
}))

vi.mock('../../../lib/materialsApi', () => ({
  materialsApi: {
    batchImport: (...args: unknown[]) => mockBatchImport(...args),
    getCharacters: (...args: unknown[]) => mockGetCharacters(...args),
    getWorldView: vi.fn(),
    getGoldenFingers: vi.fn(),
    getStoryLines: vi.fn(),
    getStories: vi.fn(),
    getRelationships: vi.fn().mockResolvedValue([]),
    searchMaterials: (...args: unknown[]) => mockSearchMaterials(...args),
    importToProject: vi.fn(),
  },
}))

vi.mock('../../../lib/toast', () => ({
  toast: {
    success: (...args: unknown[]) => mockToastSuccess(...args),
    error: (...args: unknown[]) => mockToastError(...args),
  },
}))

vi.mock('../../../lib/logger', () => ({
  logger: {
    error: vi.fn(),
  },
}))

describe('MaterialsPane', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    libraryState.accessDenied = false
    mockAddMaterial.mockReturnValue(true)
    mockGetCharacters.mockResolvedValue([
      { id: 1, name: 'Hero' },
      { id: 2, name: 'Villain' },
    ])
    mockBatchImport.mockResolvedValue({
      results: [{ file_id: 'file-1', title: 'Hero', folder_name: 'Characters', file_type: 'character' }],
      failed_count: 1,
    })
  })

  it('shows the upgrade notice instead of a load error for plans without the materials library', () => {
    libraryState.accessDenied = true

    render(<MaterialsPane />)

    expect(screen.getByTestId('materials-upgrade-notice')).toHaveTextContent('editor_materials_pane')
    expect(screen.queryByText('materials:libraryLoadFailed')).not.toBeInTheDocument()
  })

  it('refreshes a stale library summary when the pane mounts', () => {
    render(<MaterialsPane />)

    expect(mockRefreshIfStale).toHaveBeenCalled()
  })

  it('keeps the latest material search results when an older request resolves later', async () => {
    vi.useFakeTimers()
    try {
      let resolveOldSearch!: (value: unknown[]) => void
      let resolveNewSearch!: (value: unknown[]) => void
      mockSearchMaterials
        .mockReturnValueOnce(new Promise((resolve) => { resolveOldSearch = resolve }))
        .mockReturnValueOnce(new Promise((resolve) => { resolveNewSearch = resolve }))

      render(<MaterialsPane />)

      const input = screen.getByPlaceholderText('editor:fileTree.searchMaterials')

      fireEvent.change(input, { target: { value: 'old' } })
      await act(async () => {
        vi.advanceTimersByTime(300)
      })
      expect(mockSearchMaterials).toHaveBeenCalledWith('old')

      fireEvent.change(input, { target: { value: 'new' } })
      await act(async () => {
        vi.advanceTimersByTime(300)
      })
      expect(mockSearchMaterials).toHaveBeenCalledWith('new')

      await act(async () => {
        resolveNewSearch([
          { novel_id: 1, novel_title: 'Novel One', entity_type: 'characters', entity_id: 2, name: 'Fresh Hero' },
        ])
        await Promise.resolve()
      })
      expect(screen.getByText('Fresh Hero')).toBeInTheDocument()

      await act(async () => {
        resolveOldSearch([
          { novel_id: 1, novel_title: 'Novel One', entity_type: 'characters', entity_id: 1, name: 'Stale Hero' },
        ])
        await Promise.resolve()
      })

      expect(screen.getByText('Fresh Hero')).toBeInTheDocument()
      expect(screen.queryByText('Stale Hero')).not.toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  })

  it('clears stale material search results when the latest search fails', async () => {
    vi.useFakeTimers()
    try {
      mockSearchMaterials
        .mockResolvedValueOnce([
          { novel_id: 1, novel_title: 'Novel One', entity_type: 'characters', entity_id: 1, name: 'Previous Hero' },
        ])
        .mockRejectedValueOnce(new Error('network failed'))

      render(<MaterialsPane />)

      const input = screen.getByPlaceholderText('editor:fileTree.searchMaterials')

      fireEvent.change(input, { target: { value: 'hero' } })
      await act(async () => {
        vi.advanceTimersByTime(300)
        await Promise.resolve()
      })
      expect(screen.getByText('Previous Hero')).toBeInTheDocument()

      fireEvent.change(input, { target: { value: 'missing' } })
      await act(async () => {
        vi.advanceTimersByTime(300)
        await Promise.resolve()
      })

      expect(screen.queryByText('Previous Hero')).not.toBeInTheDocument()
      expect(screen.getByText('materials:searchLoadFailed')).toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'common:retry' })).toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  })

  it('does not report full success when batch import partially fails', async () => {
    const user = userEvent.setup()
    render(<MaterialsPane />)

    await act(async () => {
      fireEvent.click(screen.getByTitle('editor:fileTree.batchSelect'))
    })
    await act(async () => {
      fireEvent.click(screen.getByText('editor:fileTree.referenceCharacters'))
    })

    expect(await screen.findByText('Hero')).toBeInTheDocument()
    const checkboxes = screen.getAllByRole('checkbox', { name: '' })
    await act(async () => {
      await user.click(checkboxes[0])
      await user.click(checkboxes[1])
    })

    await waitFor(() => {
      expect(screen.getByText('editor:fileTree.batchImport')).toBeInTheDocument()
    })

    await act(async () => {
      fireEvent.click(screen.getByText('editor:fileTree.batchImport'))
      await Promise.resolve()
    })

    await waitFor(() => {
      expect(mockBatchImport).toHaveBeenCalledWith('project-1', [
        { novel_id: 1, entity_type: 'characters', entity_id: 1 },
        { novel_id: 1, entity_type: 'characters', entity_id: 2 },
      ])
    })
    expect(mockToastSuccess).not.toHaveBeenCalled()
    expect(mockToastError).toHaveBeenCalledWith('Imported 1, failed 1')
    expect(screen.getByText('editor:fileTree.batchImport')).toBeInTheDocument()
  })

  it('shows stored historical relationships while the global producer flag is off', () => {
    render(<MaterialsPane />)
    expect(screen.getByText('editor:fileTree.referenceRelationships')).toBeInTheDocument()
  })
})
