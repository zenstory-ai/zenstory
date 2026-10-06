import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
const { getVersions, rollback, compare, toastError, t } = vi.hoisted(() => ({
  getVersions: vi.fn(), rollback: vi.fn(), compare: vi.fn(), toastError: vi.fn(), t: (key: string) => key,
}))
vi.mock('../../lib/api', () => ({ fileVersionApi: { getVersions, rollback, compare } }))
vi.mock('../../lib/toast', () => ({ toast: { error: toastError, success: vi.fn(), info: vi.fn() } }))
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t }) }))
vi.mock('../subscription/UpgradePromptModal', () => ({ UpgradePromptModal: () => null }))
import { FileVersionHistory } from '../FileVersionHistory'

const version = (n: number) => ({ id:`version-${n}`,version_number:n,change_type:'edit',change_source:'user',created_at:'2026-10-04T00:00:00Z',word_count:10,lines_added:1,lines_removed:0 })

describe('FileVersionHistory saved-state boundary', () => {
  beforeEach(() => { vi.clearAllMocks() })
  afterEach(() => { vi.unstubAllGlobals() })

  it('allows restoring the latest saved version when unversioned edits may have advanced', async () => {
    getVersions.mockResolvedValue({ total:1, versions:[version(3)] })
    rollback.mockResolvedValue({})
    vi.stubGlobal('confirm',vi.fn(()=>true))
    const onRollback=vi.fn()
    render(<FileVersionHistory fileId="file-1" fileTitle="Draft" onClose={vi.fn()} onRollback={onRollback} />)
    expect(await screen.findByText('latestSaved')).toBeInTheDocument()
    fireEvent.click(screen.getByTitle('rollback'))
    await waitFor(()=>expect(rollback).toHaveBeenCalledWith('file-1',3))
    await waitFor(()=>expect(onRollback).toHaveBeenCalledWith(3))
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
    vi.stubGlobal('confirm',vi.fn(()=>true))
    const onRollback=vi.fn()
    render(<FileVersionHistory fileId="file-1" fileTitle="Draft" onClose={vi.fn()} onRollback={onRollback} />)
    fireEvent.click(await screen.findByTitle('rollback'))
    await waitFor(()=>expect(toastError).toHaveBeenCalledWith('rollbackFailed'))
    expect(rollback).toHaveBeenCalledWith('file-1',3)
    expect(onRollback).not.toHaveBeenCalled()
    expect(getVersions).toHaveBeenCalledTimes(1)
  })
})
