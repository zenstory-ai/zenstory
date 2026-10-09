import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Editor } from '../Editor'

const { getFile, updateFile, getVersions, rollback, toastError, projectContext, t } = vi.hoisted(() => ({
  getFile: vi.fn(), updateFile: vi.fn(), getVersions: vi.fn(), rollback: vi.fn(), toastError: vi.fn(),
  t: (key: string) => key,
  projectContext: {
    currentProjectId: 'project-1', selectedItem: { id: 'file-1', type: 'draft', title: 'Draft' },
    streamingFileId: null, streamingContent: '', editorRefreshVersion: 0, lastEditedFileId: null,
    aiEditingFileId: null, diffReviewState: null, triggerFileTreeRefresh: vi.fn(), setSelectedItem: vi.fn(),
  },
}))
vi.mock('../../lib/api', () => ({
  fileApi: { get: getFile, update: updateFile },
  fileVersionApi: { getVersions, rollback },
}))
vi.mock('../../lib/toast', () => ({ toast: { error: toastError } }))
vi.mock('../../lib/writingStatsApi', () => ({ writingStatsApi: { recordStats: vi.fn() } }))
vi.mock('../../lib/upgradeAnalytics', () => ({ trackUpgradeClick: vi.fn(), trackUpgradeExpose: vi.fn() }))
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t }) }))
vi.mock('../../contexts/ProjectContext', () => ({ useProject: () => projectContext }))
vi.mock('../../contexts/AuthContext', () => ({ useAuth: () => ({ user: { id: 'user-1' } }) }))
vi.mock('../../contexts/MaterialLibraryContext', () => ({ useMaterialLibraryContext: () => ({ preview: null }) }))
vi.mock('../../contexts/MaterialAttachmentContext', () => ({ useMaterialAttachment: () => ({ addMaterial: vi.fn() }) }))
vi.mock('../../contexts/TextQuoteContext', () => ({ useTextQuote: () => ({ addQuote: vi.fn() }) }))
vi.mock('../../hooks/useGestures', () => ({ usePinchZoom: () => ({ zoom: 1, bind: () => ({}), resetZoom: vi.fn() }) }))

const file = { id: 'file-1', project_id: 'project-1', title: 'Draft', file_type: 'draft', content: 'Current draft', updated_at: '2026-10-06T10:00:00.000001' }
const savedVersion = { id: 'v1', file_id: 'file-1', version_number: 1, change_type: 'edit', change_source: 'user', word_count: 2, lines_added: 1, lines_removed: 0, created_at: '2026-10-05T10:00:00' }

describe('actual Editor / SimpleEditor / history restore integration', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    // resetAllMocks also wipes the global matchMedia mock from test/setup.ts (desktop layout).
    vi.mocked(window.matchMedia).mockImplementation((query: string) => ({
      matches: false, media: query, onchange: null,
      addListener: vi.fn(), removeListener: vi.fn(),
      addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn(),
    }) as unknown as MediaQueryList)
    getVersions.mockResolvedValue({ total: 1, versions: [savedVersion] })
  })
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

  it('keeps an already-submitted restore mounted through close/Escape and prevents duplicate submission', async () => {
    let completeRestore!: (value: { success: boolean; snapshot_created: boolean; version_quota_exceeded: boolean }) => void
    const restoring = new Promise<{ success: boolean; snapshot_created: boolean; version_quota_exceeded: boolean }>((resolve) => { completeRestore = resolve })
    const restored = { ...file, content: 'Restored after pending POST', updated_at: '2026-10-06T10:00:00.000002' }
    getFile.mockResolvedValueOnce(file).mockResolvedValueOnce(restored)
    rollback.mockReturnValueOnce(restoring)
    render(<Editor />)
    const textarea = await screen.findByPlaceholderText('editor:placeholder.contentPlaceholder')
    fireEvent.click(screen.getByRole('button', { name: 'editor:history' }))
    fireEvent.click(await screen.findByTitle('rollback'))
    fireEvent.click(await screen.findByRole('button', { name: 'rollbackConfirmButton' }))
    await waitFor(() => expect(rollback).toHaveBeenCalledTimes(1))
    fireEvent.click(screen.getByRole('button', { name: 'common:close' }))
    fireEvent.keyDown(document, { key: 'Escape' })
    fireEvent.click(screen.getByRole('dialog').parentElement!)
    expect(screen.getByText('title')).toBeInTheDocument()
    fireEvent.click(screen.getByTitle('rollback'))
    await act(async () => { await Promise.resolve() })
    expect(rollback).toHaveBeenCalledTimes(1)
    completeRestore({ success: true, snapshot_created: false, version_quota_exceeded: true })
    await waitFor(() => expect(textarea).toHaveValue(restored.content))
    expect(screen.getByRole('dialog', { name: 'quota.limitTitle' })).toBeInTheDocument()
  })

  it('finishes already-started editor writes before submitting a history restore', async () => {
    let completeSave!: (value: typeof file) => void
    const saving = new Promise<typeof file>((resolve) => { completeSave = resolve })
    const restored = { ...file, content: 'Restored after prior save', updated_at: '2026-10-06T10:00:00.000003' }
    getFile.mockResolvedValueOnce(file).mockResolvedValueOnce(restored)
    updateFile.mockReturnValueOnce(saving)
    rollback.mockResolvedValue({ success: true, snapshot_created: true, version_quota_exceeded: false })
    render(<Editor />)
    const textarea = await screen.findByPlaceholderText('editor:placeholder.contentPlaceholder')
    fireEvent.change(textarea, { target: { value: 'Already-started save' } })
    fireEvent.click(screen.getByRole('button', { name: 'editor:save' }))
    await waitFor(() => expect(updateFile).toHaveBeenCalledTimes(1))
    fireEvent.click(screen.getByRole('button', { name: 'editor:history' }))
    fireEvent.click(await screen.findByTitle('rollback'))
    fireEvent.click(await screen.findByRole('button', { name: 'rollbackConfirmButton' }))
    await act(async () => { await Promise.resolve() })
    expect(rollback).not.toHaveBeenCalled()
    completeSave({ ...file, content: 'Already-started save', updated_at: '2026-10-06T10:00:00.000002' })
    await waitFor(() => expect(rollback).toHaveBeenCalledWith('file-1', 1))
    await waitFor(() => expect(textarea).toHaveValue(restored.content))
    expect(screen.queryByText('editor:unsaved')).not.toBeInTheDocument()
  })

  it('waits for the refreshed token/content before using the restored save baseline', async () => {
    let completeRefresh!: (value: typeof file) => void
    const refreshing = new Promise<typeof file>((resolve) => { completeRefresh = resolve })
    const restored = { ...file, content: 'Restored historical draft with substantially different and much longer content', updated_at: '2026-10-06T10:00:00.000002' }
    getFile.mockResolvedValueOnce(file).mockReturnValueOnce(refreshing)
    rollback.mockResolvedValue({ success: true, snapshot_created: true, version_quota_exceeded: false })
    updateFile.mockResolvedValue({ ...restored, content: `${restored.content}!` })
    render(<Editor />)
    const textarea = await screen.findByPlaceholderText('editor:placeholder.contentPlaceholder')
    fireEvent.change(textarea, { target: { value: 'Unsaved draft to discard' } })
    fireEvent.click(screen.getByRole('button', { name: 'editor:history' }))
    fireEvent.keyDown(await screen.findByText('title'), { key: 's', ctrlKey: true })
    await act(async () => { await Promise.resolve() })
    expect(updateFile).not.toHaveBeenCalled()
    fireEvent.click(await screen.findByTitle('rollback'))
    fireEvent.click(await screen.findByRole('button', { name: 'rollbackConfirmButton' }))
    await waitFor(() => expect(getFile).toHaveBeenCalledTimes(2))
    await act(async () => { await Promise.resolve() })
    expect(updateFile).not.toHaveBeenCalled()
    completeRefresh(restored)
    await waitFor(() => expect(textarea).toHaveValue(restored.content))
    fireEvent.click(screen.getByRole('button', { name: 'common:close' }))
    fireEvent.change(textarea, { target: { value: `${restored.content}!` } })
    fireEvent.click(screen.getByRole('button', { name: 'editor:save' }))
    await waitFor(() => expect(updateFile).toHaveBeenCalledWith('file-1', expect.objectContaining({
      content: `${restored.content}!`, base_updated_at: restored.updated_at, skip_version: true,
    })))
  })

  it.each([true, false])('refreshes content/token without destroying omitted-history feedback (quota=%s)', async (quotaExceeded) => {
    const restored = { ...file, content: 'Restored historical draft', updated_at: '2026-10-06T10:00:00.000002' }
    getFile.mockResolvedValueOnce(file).mockResolvedValueOnce(restored)
    rollback.mockResolvedValue({ success: true, snapshot_created: false, version_quota_exceeded: quotaExceeded })
    updateFile.mockResolvedValue({ ...restored, content: 'After restore edit', updated_at: '2026-10-06T10:00:00.000003' })
    const reload = vi.spyOn(window.location, 'reload').mockImplementation(() => {})
    render(<Editor />)
    const textarea = await screen.findByPlaceholderText('editor:placeholder.contentPlaceholder')
    expect(textarea).toHaveValue('Current draft')
    fireEvent.click(screen.getByRole('button', { name: 'editor:history' }))
    fireEvent.click(await screen.findByTitle('rollback'))
    fireEvent.click(await screen.findByRole('button', { name: 'rollbackConfirmButton' }))
    await waitFor(() => expect(rollback).toHaveBeenCalledWith('file-1', 1))
    await waitFor(() => expect(textarea).toHaveValue('Restored historical draft'))
    expect(reload).not.toHaveBeenCalled()
    expect(screen.getByText('title')).toBeInTheDocument()
    if (quotaExceeded) {
      expect(screen.getByRole('dialog', { name: 'quota.limitTitle' })).toBeInTheDocument()
    } else {
      expect(toastError).toHaveBeenCalledWith('rollbackHistoryNotSaved')
    }
    expect(updateFile).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'common:close' }))
    fireEvent.change(textarea, { target: { value: 'After restore edit' } })
    fireEvent.click(screen.getByRole('button', { name: 'editor:save' }))
    await waitFor(() => expect(updateFile).toHaveBeenCalledWith('file-1', expect.objectContaining({
      content: 'After restore edit', base_updated_at: restored.updated_at,
    })))
  })
})
