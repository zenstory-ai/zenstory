import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useChatStreaming, type UseChatStreamingDependencies } from '../useChatStreaming'
import { FILE_REMOVED_EVENT, clearFileRemoved } from '../../lib/fileRemovalEvents'

const api = vi.hoisted(() => ({ getTree: vi.fn() }))
vi.mock('../../lib/api', () => ({ fileApi: api }))

const selected = { id: 'surviving-draft', type: 'draft' as const, title: 'Survivor' }
const options = (activeProjectId: string | null = 'race-project') => ({
  activeProjectId, triggerFileTreeRefresh: vi.fn(), triggerEditorRefresh: vi.fn(),
  setSelectedItem: vi.fn(), getCurrentSelectedItem: vi.fn(() => selected),
  appendFileContent: vi.fn(), finishFileStreaming: vi.fn(), startFileStreaming: vi.fn(), streamingFileId: null,
  enterDiffReview: vi.fn(), t: (key: string) => key,
}) satisfies UseChatStreamingDependencies
const settle = async () => { await act(async () => { for (let i = 0; i < 15; i++) await Promise.resolve() }) }
afterEach(() => { vi.restoreAllMocks(); api.getTree.mockReset(); clearFileRemoved('race-project', selected.id); clearFileRemoved('race-project', 'other-draft') })

describe('confirmed deletion ownership', () => {
  it('accepts a nested parallel success with file_id but never clears a different open file', async () => {
    const deps = options(); const { result } = renderHook(() => useChatStreaming()); const callbacks = result.current.getStreamCallbacks(deps)
    api.getTree.mockResolvedValue({ tree: [{ ...selected, children: [] }] })
    act(() => callbacks.onToolResult('parallel_execute', 'success', { tasks: [{ type: 'delete_file', status: 'success', result: { file_id: 'other-draft' } }] }))
    await settle()
    expect(deps.setSelectedItem).not.toHaveBeenCalled()
    expect(deps.triggerEditorRefresh).toHaveBeenCalledWith(selected.id)
  })

  it('does not treat failed or unrelated parallel tasks as deletion evidence', async () => {
    const deps = options(); const removed = vi.fn(); window.addEventListener(FILE_REMOVED_EVENT, removed)
    try {
      const { result } = renderHook(() => useChatStreaming()); const callbacks = result.current.getStreamCallbacks(deps)
      act(() => callbacks.onToolResult('parallel_execute', 'success', { tasks: [{ type: 'delete_file', status: 'failed', result: { id: selected.id } }, { type: 'edit_file', status: 'completed' }] }))
      await settle()
      expect(removed).not.toHaveBeenCalled(); expect(deps.setSelectedItem).not.toHaveBeenCalled(); expect(api.getTree).not.toHaveBeenCalled()
    } finally { window.removeEventListener(FILE_REMOVED_EVENT, removed) }
  })

  it('does not clear a selection that changed synchronously in the removal listener', async () => {
    const deps = options(); let current = selected
    deps.getCurrentSelectedItem.mockImplementation(() => current)
    const onRemoved = () => { current = { ...selected, id: 'new-draft' } }
    window.addEventListener(FILE_REMOVED_EVENT, onRemoved)
    try {
      const { result } = renderHook(() => useChatStreaming()); const callbacks = result.current.getStreamCallbacks(deps)
      api.getTree.mockResolvedValue({ tree: [] })
      act(() => callbacks.onToolResult('delete_file', 'success', true as unknown as Record<string, unknown>))
      await settle()
      expect(current.id).toBe('new-draft'); expect(deps.setSelectedItem).not.toHaveBeenCalled()
    } finally { window.removeEventListener(FILE_REMOVED_EVENT, onRemoved) }
  })

  it('ignores a failed verification read after the author switches the open file', async () => {
    const deps = options(); let reject!: (error: Error) => void
    api.getTree.mockImplementation(() => new Promise((_resolve, fail) => { reject = fail }))
    const { result } = renderHook(() => useChatStreaming()); const callbacks = result.current.getStreamCallbacks(deps)
    act(() => callbacks.onToolResult('delete_file', 'success', {}))
    deps.getCurrentSelectedItem.mockReturnValue({ ...selected, id: 'new-draft' })
    await act(async () => reject(new Error('tree offline'))); await settle()
    expect(deps.triggerEditorRefresh).not.toHaveBeenCalled(); expect(deps.setSelectedItem).not.toHaveBeenCalled()
  })

  it('never broadcasts a removal without a project owner', async () => {
    const deps = options(null); const removed = vi.fn(); window.addEventListener(FILE_REMOVED_EVENT, removed)
    try {
      const { result } = renderHook(() => useChatStreaming()); const callbacks = result.current.getStreamCallbacks(deps)
      act(() => callbacks.onToolResult('delete_file', 'success', { id: selected.id })); await settle()
      expect(removed).not.toHaveBeenCalled(); expect(deps.setSelectedItem).not.toHaveBeenCalled(); expect(api.getTree).not.toHaveBeenCalled()
    } finally { window.removeEventListener(FILE_REMOVED_EVENT, removed) }
  })
})
