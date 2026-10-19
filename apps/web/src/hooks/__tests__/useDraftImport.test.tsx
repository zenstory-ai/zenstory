import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, renderHook } from '@testing-library/react'

const mocks = vi.hoisted(() => ({
  uploadDraft: vi.fn(),
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  trackEvent: vi.fn(),
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: { count?: number }) => (options?.count === undefined ? key : `${key}:${options.count}`),
  }),
}))
vi.mock('../../lib/api', () => ({ fileApi: { uploadDraft: mocks.uploadDraft } }))
vi.mock('../../lib/toast', () => ({ toast: mocks.toast }))
vi.mock('../../lib/analytics', () => ({ trackEvent: mocks.trackEvent }))
vi.mock('../../lib/logger', () => ({ logger: { error: vi.fn() } }))

import { useDraftImport } from '../useDraftImport'

const draft = (name: string) => new File(['第一章 开端\n正文'], name, { type: 'text/plain' })
const chapter = (id: string) => ({ id, title: id, file_type: 'draft' })

describe('useDraftImport', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('does nothing for an empty pick', async () => {
    const { result } = renderHook(() => useDraftImport())
    let outcome
    await act(async () => { outcome = await result.current.importFiles('p1', [], 'editor_empty') })
    expect(outcome).toEqual({ files: [], failed: false })
    expect(mocks.uploadDraft).not.toHaveBeenCalled()
    expect(mocks.trackEvent).not.toHaveBeenCalled()
  })

  it('counts chapters from the returned files when the total is missing, and translates per-file errors', async () => {
    mocks.uploadDraft
      .mockResolvedValueOnce({ files: [chapter('c1'), chapter('c2')] })
      .mockResolvedValueOnce({ errors: ['b.txt: ERR_FILE_TOO_LARGE', ': ERR_FILE_TYPE_INVALID', 'disk said no'] })
    const { result } = renderHook(() => useDraftImport())

    let outcome
    await act(async () => { outcome = await result.current.importFiles('p1', [draft('a.txt'), draft('b.txt')], 'file_tree') })

    expect(outcome).toEqual({ files: [chapter('c1'), chapter('c2')], failed: false })
    expect(mocks.toast.error).toHaveBeenCalledWith(
      'b.txt: editor:fileTree.errorFileTooLarge\neditor:fileTree.errorFileTypeInvalid\ndisk said no',
    )
    expect(mocks.toast.success).toHaveBeenCalledWith('editor:fileTree.draftUploadSuccess:2')
    expect(mocks.trackEvent).toHaveBeenCalledWith('draft_imported', {
      project_id: 'p1', entry: 'file_tree', file_count: 2, chapter_count: 2,
    })
    expect(result.current.isImporting).toBe(false)
  })

  it('shows only the errors when no chapter was created', async () => {
    mocks.uploadDraft.mockResolvedValueOnce({ errors: ['a.txt: ERR_VALIDATION_ERROR'] })
    const { result } = renderHook(() => useDraftImport())

    await act(async () => { await result.current.importFiles('p1', [draft('a.txt')], 'dashboard') })

    expect(mocks.toast.error).toHaveBeenCalledWith('a.txt: editor:fileTree.errorValidation')
    expect(mocks.toast.success).not.toHaveBeenCalled()
    expect(mocks.toast.info).not.toHaveBeenCalled()
  })

  it('says the file was empty when nothing came back', async () => {
    mocks.uploadDraft.mockResolvedValueOnce({})
    const { result } = renderHook(() => useDraftImport())

    await act(async () => { await result.current.importFiles('p1', [draft('a.txt')], 'dashboard') })

    expect(mocks.toast.info).toHaveBeenCalledWith('editor:fileTree.draftUploadEmpty')
  })

  it('returns what was created before a failure and falls back to a generic message', async () => {
    mocks.uploadDraft
      .mockResolvedValueOnce({ files: [chapter('c1')], total: 1 })
      .mockRejectedValueOnce('network down')
    const { result } = renderHook(() => useDraftImport())

    let outcome
    await act(async () => { outcome = await result.current.importFiles('p1', [draft('a.txt'), draft('b.txt')], 'file_tree') })

    expect(outcome).toEqual({ files: [chapter('c1')], failed: true })
    expect(mocks.toast.error).toHaveBeenCalledWith('editor:fileTree.uploadFailed')
    expect(mocks.trackEvent).not.toHaveBeenCalled()
  })

  it('stops after the current file and stays quiet once the page is left', async () => {
    let finishFirst: (value: unknown) => void = () => {}
    mocks.uploadDraft.mockReturnValueOnce(new Promise((resolve) => { finishFirst = resolve }))
    const { result, unmount } = renderHook(() => useDraftImport())

    const pending = result.current.importFiles('p1', [draft('a.txt'), draft('b.txt')], 'file_tree')
    unmount()
    finishFirst({ files: [chapter('c1')], total: 1 })

    await expect(pending).resolves.toEqual({ files: [chapter('c1')], failed: false })
    expect(mocks.uploadDraft).toHaveBeenCalledTimes(1)
    expect(mocks.toast.success).not.toHaveBeenCalled()
    expect(mocks.trackEvent).not.toHaveBeenCalled()
  })

  it('stays quiet about a failure after the page is left', async () => {
    let failUpload: (reason: unknown) => void = () => {}
    mocks.uploadDraft.mockReturnValueOnce(new Promise((_resolve, reject) => { failUpload = reject }))
    const { result, unmount } = renderHook(() => useDraftImport())

    const pending = result.current.importFiles('p1', [draft('a.txt')], 'file_tree')
    unmount()
    failUpload(new Error('offline'))

    await expect(pending).resolves.toEqual({ files: [], failed: true })
    expect(mocks.toast.error).not.toHaveBeenCalled()
  })

  it('stays quiet when the page is left during the last upload', async () => {
    let finishOnly: (value: unknown) => void = () => {}
    mocks.uploadDraft.mockReturnValueOnce(new Promise((resolve) => { finishOnly = resolve }))
    const { result, unmount } = renderHook(() => useDraftImport())

    const pending = result.current.importFiles('p1', [draft('a.txt')], 'file_tree')
    unmount()
    finishOnly({ files: [chapter('c1')], total: 1 })

    await expect(pending).resolves.toEqual({ files: [chapter('c1')], failed: false })
    expect(mocks.toast.success).not.toHaveBeenCalled()
  })
})
