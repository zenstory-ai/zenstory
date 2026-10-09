import { act, render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
const { getVersions, getVersionContent, rollback, compare, toastError, upgradeModal, translator } = vi.hoisted(() => ({
  getVersions: vi.fn(), getVersionContent: vi.fn(), rollback: vi.fn(), compare: vi.fn(),
  toastError: vi.fn(), upgradeModal: vi.fn(), translator: { current: (key: string) => key },
}))
vi.mock('../../lib/api', () => ({ fileVersionApi: { getVersions, getVersionContent, rollback, compare } }))
vi.mock('../../lib/toast', () => ({ toast: { error: toastError, success: vi.fn(), info: vi.fn() } }))
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: translator.current }) }))
vi.mock('../subscription/UpgradePromptModal', () => ({
  UpgradePromptModal: (props: { open: boolean }) => {
    upgradeModal(props)
    return props.open ? <div data-testid="upgrade-modal" /> : null
  },
}))
import { FileVersionHistory } from '../FileVersionHistory'
import versionsZh from '../../../public/locales/zh/versions.json'
import versionsEn from '../../../public/locales/en/versions.json'

const confirmRestore = async () => {
  fireEvent.click(await screen.findByRole('button', { name: 'rollbackConfirmButton' }))
}

const version = (n: number) => ({ id:`version-${n}`,version_number:n,change_type:'edit',change_source:'user',created_at:'2026-10-04T00:00:00Z',word_count:10,lines_added:1,lines_removed:0 })

const deferred = <T,>() => {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

describe('FileVersionHistory saved-state boundary', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    translator.current = (key: string) => key
  })
  afterEach(() => { vi.unstubAllGlobals() })

  it('allows restoring the version that matches the current text', async () => {
    getVersions.mockResolvedValue({ total:1, versions:[version(3)], current_version_number: 3 })
    rollback.mockResolvedValue({})
    const onRollback=vi.fn()
    render(<FileVersionHistory fileId="file-1" fileTitle="Draft" onClose={vi.fn()} onRollback={onRollback} />)
    expect(await screen.findByText('currentText')).toBeInTheDocument()
    fireEvent.click(screen.getByTitle('rollback'))
    await confirmRestore()
    await waitFor(()=>expect(rollback).toHaveBeenCalledWith('file-1',3))
    await waitFor(()=>expect(onRollback).toHaveBeenCalledWith(3))
  })

  it('still allows closing before restore submission and cancels the pending preparation', async () => {
    const preparing = deferred<void>()
    getVersions.mockResolvedValue({ total: 1, versions: [version(3)] })
    const onClose = vi.fn()
    const { unmount } = render(<FileVersionHistory fileId="file-1" fileTitle="Draft" onClose={onClose} onBeforeRollback={() => preparing.promise} />)
    fireEvent.click(await screen.findByTitle('rollback'))
    await confirmRestore()
    fireEvent.click(screen.getByRole('button', { name: 'common:close' }))
    expect(onClose).toHaveBeenCalledTimes(1)
    unmount()
    await act(async () => { preparing.resolve(); await preparing.promise })
    expect(rollback).not.toHaveBeenCalled()
  })

  it('shows the compareFailed toast and keeps the comparison closed when comparing two versions fails', async () => {
    getVersions.mockResolvedValue({ total:2, versions:[version(2), version(1)] })
    compare.mockRejectedValue(new Error('network down'))
    render(<FileVersionHistory fileId="file-1" fileTitle="Draft" onClose={vi.fn()} />)
    fireEvent.click(await screen.findByText('v2'))
    fireEvent.click(screen.getByText('v1'))
    fireEvent.click(screen.getByRole('button', { name: 'compare' }))
    await waitFor(()=>expect(toastError).toHaveBeenCalledWith('compareFailed'))
    expect(compare).toHaveBeenCalledWith('file-1',1,2)
    // The compare button recovers from its busy state so the author can retry.
    expect(screen.getByRole('button', { name: 'compare' })).not.toBeDisabled()
    expect(screen.queryByText('comparing')).not.toBeInTheDocument()
  })

  it('shows the rollbackFailed toast and does not report a rollback when restoring fails for a non-quota reason', async () => {
    getVersions.mockResolvedValue({ total:1, versions:[version(3)] })
    rollback.mockRejectedValue(new Error('server error'))
    const onRollback=vi.fn()
    render(<FileVersionHistory fileId="file-1" fileTitle="Draft" onClose={vi.fn()} onRollback={onRollback} />)
    fireEvent.click(await screen.findByTitle('rollback'))
    await confirmRestore()
    await waitFor(()=>expect(toastError).toHaveBeenCalledWith('rollbackFailed'))
    expect(rollback).toHaveBeenCalledWith('file-1',3)
    expect(onRollback).not.toHaveBeenCalled()
    expect(getVersions).toHaveBeenCalledTimes(1)
  })

  it('loads additional pages so versions beyond the first 50 remain accessible', async () => {
    const firstPage = Array.from({ length: 50 }, (_, index) => version(51 - index))
    getVersions
      .mockResolvedValueOnce({ total: 51, versions: firstPage })
      .mockResolvedValueOnce({ total: 51, versions: [version(1)] })

    render(<FileVersionHistory fileId="file-1" fileTitle="Draft" onClose={vi.fn()} />)

    expect(await screen.findByText('v2')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'loadMore' }))

    expect(await screen.findByText('v1')).toBeInTheDocument()
    expect(getVersions).toHaveBeenNthCalledWith(1, 'file-1', { limit: 50, offset: 0 })
    expect(getVersions).toHaveBeenNthCalledWith(2, 'file-1', { limit: 50, offset: 50 })
  })

  it('keeps loaded history accessible and lets the next page be retried after failure', async () => {
    getVersions
      .mockResolvedValueOnce({ total: 2, versions: [version(2)] })
      .mockRejectedValueOnce(new Error('transient next-page failure'))
      .mockResolvedValueOnce({ total: 2, versions: [version(1)] })
    render(<FileVersionHistory fileId="file-1" fileTitle="Draft" onClose={vi.fn()} />)
    expect(await screen.findByText('v2')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'loadMore' }))
    expect(await screen.findByText('loadFailed')).toBeInTheDocument()
    expect(screen.getByText('v2')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'loadMore' }))
    expect(await screen.findByText('v1')).toBeInTheDocument()
    expect(screen.queryByText('loadFailed')).not.toBeInTheDocument()
    expect(getVersions).toHaveBeenNthCalledWith(3, 'file-1', { limit: 50, offset: 1 })
  })

  it('does not let a stale request for the previous file replace the current file history', async () => {
    const fileA = deferred<{ total: number; versions: ReturnType<typeof version>[] }>()
    const fileB = deferred<{ total: number; versions: ReturnType<typeof version>[] }>()
    getVersions.mockReturnValueOnce(fileA.promise).mockReturnValueOnce(fileB.promise)

    const { rerender } = render(
      <FileVersionHistory fileId="file-a" fileTitle="A" onClose={vi.fn()} />,
    )
    rerender(<FileVersionHistory fileId="file-b" fileTitle="B" onClose={vi.fn()} />)

    fileB.resolve({ total: 1, versions: [version(2)] })
    expect(await screen.findByText('v2')).toBeInTheDocument()

    fileA.resolve({ total: 1, versions: [version(1)] })
    await waitFor(() => expect(screen.queryByText('v1')).not.toBeInTheDocument())
    expect(screen.getByText('v2')).toBeInTheDocument()
  })

  it('reports when rollback succeeds but the restored state could not be added to history', async () => {
    getVersions.mockResolvedValue({ total: 1, versions: [version(3)] })
    rollback.mockResolvedValue({
      success: true,
      snapshot_created: false,
      version_quota_exceeded: true,
    })

    render(<FileVersionHistory fileId="file-1" fileTitle="Draft" onClose={vi.fn()} />)
    fireEvent.click(await screen.findByTitle('rollback'))
    await confirmRestore()

    await waitFor(() => expect(toastError).toHaveBeenCalledWith('quota.limitDescription'))
    expect(screen.getByTestId('upgrade-modal')).toBeInTheDocument()
  })

  it('shows a visible content preview when no external view callback is supplied', async () => {
    getVersions.mockResolvedValue({ total: 1, versions: [version(3)] })
    getVersionContent.mockResolvedValue({ content: 'Historical draft body' })

    render(<FileVersionHistory fileId="file-1" fileTitle="Draft" onClose={vi.fn()} />)
    fireEvent.click(await screen.findByTitle('viewContent'))

    expect(await screen.findByText('Historical draft body')).toBeInTheDocument()
    expect(getVersionContent).toHaveBeenCalledWith('file-1', 3)
  })

  it('keeps the existing external view callback behavior without opening the fallback preview', async () => {
    getVersions.mockResolvedValue({ total: 1, versions: [version(3)] })
    getVersionContent.mockResolvedValue({ content: 'Historical draft body' })
    const onViewContent = vi.fn()

    render(
      <FileVersionHistory
        fileId="file-1"
        fileTitle="Draft"
        onClose={vi.fn()}
        onViewContent={onViewContent}
      />,
    )
    fireEvent.click(await screen.findByTitle('viewContent'))

    await waitFor(() => expect(onViewContent).toHaveBeenCalledWith('Historical draft body', 3))
    expect(screen.queryByText('Historical draft body')).not.toBeInTheDocument()
  })

  it('surfaces content loading failures instead of silently doing nothing', async () => {
    getVersions.mockResolvedValue({ total: 1, versions: [version(3)] })
    getVersionContent.mockRejectedValue(new Error('network down'))

    render(<FileVersionHistory fileId="file-1" fileTitle="Draft" onClose={vi.fn()} />)
    fireEvent.click(await screen.findByTitle('viewContent'))

    await waitFor(() => expect(toastError).toHaveBeenCalledWith('viewContentFailed'))
  })

  it('ignores a completed rollback after navigation to another file', async () => {
    const rollbackResult = deferred<{
      success: boolean
      snapshot_created: boolean
      version_quota_exceeded: boolean
    }>()
    getVersions
      .mockResolvedValueOnce({ total: 1, versions: [version(3)] })
      .mockResolvedValueOnce({ total: 1, versions: [version(8)] })
    rollback.mockReturnValueOnce(rollbackResult.promise)
    const onRollback = vi.fn()

    const { rerender } = render(
      <FileVersionHistory
        fileId="file-a"
        fileTitle="A"
        onClose={vi.fn()}
        onRollback={onRollback}
      />,
    )
    fireEvent.click(await screen.findByTitle('rollback'))
    await confirmRestore()
    await waitFor(() => expect(rollback).toHaveBeenCalledWith('file-a', 3))
    rerender(
      <FileVersionHistory
        fileId="file-b"
        fileTitle="B"
        onClose={vi.fn()}
        onRollback={onRollback}
      />,
    )
    expect(await screen.findByText('v8')).toBeInTheDocument()

    rollbackResult.resolve({
      success: true,
      snapshot_created: false,
      version_quota_exceeded: true,
    })

    await waitFor(() => expect(rollback).toHaveBeenCalledTimes(1))
    expect(onRollback).not.toHaveBeenCalled()
    expect(toastError).not.toHaveBeenCalled()
    expect(screen.queryByTestId('upgrade-modal')).not.toBeInTheDocument()
    expect(getVersions).toHaveBeenCalledTimes(2)
    expect(screen.getByText('v8')).toBeInTheDocument()
  })

  it('keeps a committed rollback alive when the translator identity changes for the same file', async () => {
    const rollbackResult = deferred<{
      success: boolean
      snapshot_created: boolean
      version_quota_exceeded: boolean
    }>()
    getVersions.mockResolvedValue({ total: 1, versions: [version(3)] })
    rollback.mockReturnValueOnce(rollbackResult.promise)
    const onRollback = vi.fn()

    const { rerender } = render(
      <FileVersionHistory
        fileId="file-1"
        fileTitle="Draft"
        onClose={vi.fn()}
        onRollback={onRollback}
      />,
    )
    fireEvent.click(await screen.findByTitle('rollback'))
    await confirmRestore()
    await waitFor(() => expect(rollback).toHaveBeenCalledWith('file-1', 3))
    expect(getVersions).toHaveBeenCalledTimes(1)

    translator.current = (key: string) => `translated:${key}`
    rerender(
      <FileVersionHistory
        fileId="file-1"
        fileTitle="Draft"
        onClose={vi.fn()}
        onRollback={onRollback}
      />,
    )
    expect(screen.getByText('translated:title')).toBeInTheDocument()
    expect(getVersions).toHaveBeenCalledTimes(1)

    await act(async () => {
      rollbackResult.resolve({
        success: true,
        snapshot_created: true,
        version_quota_exceeded: false,
      })
      await rollbackResult.promise
    })

    await waitFor(() => expect(onRollback).toHaveBeenCalledWith(3))
    expect(getVersions).toHaveBeenCalledTimes(2)
  })

  it('clears obsolete comparison activity when navigating to another file', async () => {
    const comparisonResult = deferred<Record<string, never>>()
    getVersions
      .mockResolvedValueOnce({ total: 2, versions: [version(2), version(1)] })
      .mockResolvedValueOnce({ total: 2, versions: [version(8), version(7)] })
    compare.mockReturnValueOnce(comparisonResult.promise)

    const { rerender } = render(
      <FileVersionHistory fileId="file-a" fileTitle="A" onClose={vi.fn()} />,
    )
    fireEvent.click(await screen.findByText('v2'))
    fireEvent.click(screen.getByText('v1'))
    fireEvent.click(screen.getByRole('button', { name: 'compare' }))

    rerender(<FileVersionHistory fileId="file-b" fileTitle="B" onClose={vi.fn()} />)
    expect(await screen.findByText('v8')).toBeInTheDocument()
    comparisonResult.resolve({})

    fireEvent.click(screen.getByText('v8'))
    fireEvent.click(screen.getByText('v7'))
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'compare' })).not.toBeDisabled()
    })
  })
  describe('version summaries', () => {
    const localeTranslator = (resources: Record<string, unknown>) => (key: string, options?: Record<string, unknown>) => {
      // Summary keys are namespaced (`versions:summary.*`); the component's own keys are not.
      const path = key.includes(':') ? key.split(':')[1] : key
      const value = path.split('.').reduce<unknown>((node, part) => (node as Record<string, unknown> | undefined)?.[part], resources)
      if (typeof value !== 'string') return key
      return value.replace(/\{\{(\w+)\}\}/g, (_, name: string) => String(options?.[name] ?? ''))
    }
    const withSummary = (n: number, change_type: string, change_summary: string) => ({ ...version(n), change_type, change_summary })
    const history = [
      withSummary(9, 'restore', 'Restored from snapshot 3f2b8c1e-9a4d-4e7f-8b21-6c5d4e3f2a10'),
      withSummary(8, 'restore', 'Restored to version 4'),
      withSummary(7, 'ai_edit', 'AI 编辑: 替换, 追加, 插入 等 5 处修改'),
      withSummary(6, 'ai_edit', 'AI edit (reviewed)'),
      withSummary(5, 'edit', 'File updated'),
      withSummary(4, 'create', '创建文件'),
      withSummary(3, 'edit', 'Tightened the opening scene'),
      withSummary(2, 'mystery_type', 'Initial version'),
    ]

    it.each([
      ['zh', versionsZh, ['从项目快照恢复', '恢复到版本 4', 'AI 修改：替换、追加、插入 等 5 处', 'AI 修改（已审阅）'], ['File updated', '创建文件', 'Restored']],
      ['en', versionsEn, ['Restored from a project snapshot', 'Restored to version 4', 'AI edit: replace, append, insert and more (5 changes)', 'AI edit (reviewed)'], ['File updated', '创建文件', 'AI 编辑', '替换']],
    ])('renders system summaries in the %s UI language and keeps user notes', async (_lang, resources, shown, hidden) => {
      translator.current = localeTranslator(resources)
      getVersions.mockResolvedValue({ total: history.length, versions: history })
      const { container } = render(<FileVersionHistory fileId="file-1" fileTitle="Draft" onClose={vi.fn()} />)
      expect(await screen.findByText('v9')).toBeInTheDocument()
      for (const text of shown) expect(screen.getByText(text)).toBeInTheDocument()
      expect(screen.getByText('Tightened the opening scene')).toBeInTheDocument()
      const text = container.textContent ?? ''
      expect(text).not.toContain('3f2b8c1e')
      expect(text).not.toContain('mystery_type')
      for (const fragment of hidden) expect(text).not.toContain(fragment)
    })
  })
})

describe('FileVersionHistory current-text marker and restore confirmation', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    translator.current = (key: string) => key
  })
  afterEach(() => { vi.unstubAllGlobals() })

  it('marks only the version the server reports as matching the current text', async () => {
    getVersions.mockResolvedValue({ total: 2, versions: [version(4), version(3)], current_version_number: 3 })
    render(<FileVersionHistory fileId="file-1" fileTitle="Draft" onClose={vi.fn()} />)
    const marker = await screen.findByText('currentText')
    expect(marker.parentElement).toHaveTextContent('v3')
    expect(screen.getAllByText('currentText')).toHaveLength(1)
  })

  it.each([
    ['has unsaved-to-history edits', { current_version_number: null }],
    ['comes from an older server without the field', {}],
  ])('shows no current-text marker when the live text %s', async (_label, extra) => {
    getVersions.mockResolvedValue({ total: 1, versions: [version(3)], ...extra })
    render(<FileVersionHistory fileId="file-1" fileTitle="Draft" onClose={vi.fn()} />)
    expect(await screen.findByText('v3')).toBeInTheDocument()
    expect(screen.queryByText('currentText')).not.toBeInTheDocument()
  })

  it('asks in an in-app dialog instead of the native confirm, and cancelling keeps the text', async () => {
    const nativeConfirm = vi.fn(() => true)
    vi.stubGlobal('confirm', nativeConfirm)
    getVersions.mockResolvedValue({ total: 1, versions: [version(3)] })
    render(<FileVersionHistory fileId="file-1" fileTitle="Draft" onClose={vi.fn()} />)
    fireEvent.click(await screen.findByTitle('rollback'))

    expect(await screen.findByText('rollbackConfirm')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'common:cancel' }))

    await waitFor(() => expect(screen.queryByText('rollbackConfirm')).not.toBeInTheDocument())
    expect(rollback).not.toHaveBeenCalled()
    expect(nativeConfirm).not.toHaveBeenCalled()
  })

  it('closes only the confirmation on Escape and leaves the history open', async () => {
    getVersions.mockResolvedValue({ total: 1, versions: [version(3)] })
    const onClose = vi.fn()
    render(<FileVersionHistory fileId="file-1" fileTitle="Draft" onClose={onClose} />)
    fireEvent.click(await screen.findByTitle('rollback'))
    await screen.findByText('rollbackConfirm')

    fireEvent.keyDown(document, { key: 'Escape' })

    await waitFor(() => expect(screen.queryByText('rollbackConfirm')).not.toBeInTheDocument())
    expect(onClose).not.toHaveBeenCalled()
    expect(rollback).not.toHaveBeenCalled()
  })

  it('renders line counts once, without a doubled +/- sign next to the icons', async () => {
    const zhTemplates: Record<string, string> = { linesAdded: '{{count}} 行', linesRemoved: '{{count}} 行' }
    translator.current = ((key: string, options?: { count?: number }) =>
      zhTemplates[key]?.replace('{{count}}', String(options?.count)) ?? key) as (key: string) => string
    getVersions.mockResolvedValue({
      total: 1,
      versions: [{ ...version(3), lines_added: 35, lines_removed: 2 }],
    })
    render(<FileVersionHistory fileId="file-1" fileTitle="Draft" onClose={vi.fn()} />)
    const added = await screen.findByText('35 行')
    expect(added.textContent).toBe('35 行')
    expect(document.body.textContent).not.toMatch(/[+-]\s*\d+ 行/)
  })

  it('shows version summaries in plain language instead of raw markers', async () => {
    getVersions.mockResolvedValue({
      total: 1,
      versions: [{ ...version(3), change_summary: 'Before restoring version 2' }],
    })
    render(<FileVersionHistory fileId="file-1" fileTitle="Draft" onClose={vi.fn()} />)
    expect(await screen.findByText('versions:summary.beforeRestore')).toBeInTheDocument()
    expect(screen.queryByText('Before restoring version 2')).not.toBeInTheDocument()
  })
})
