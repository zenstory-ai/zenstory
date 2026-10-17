import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { FileTreeNode } from '@/types'
import { useFileSearch } from '../useFileSearch'

function file(id: string, title: string, parentId: string | null = null): FileTreeNode {
  return { id, title, file_type: 'draft', parent_id: parentId, order: 0, content: '', metadata: null, children: [] }
}

describe.sequential('useFileSearch repeated work', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers() })

  it('creates one title comparator lazily and reuses it across searches and hook instances', () => {
    const NativeCollator = Intl.Collator
    const constructor = vi.spyOn(Intl, 'Collator').mockImplementation(function (...args) {
      return new NativeCollator(...args)
    })
    const tree = ['Needle Alpha', 'Needle 中', 'Needle É', 'Needle e\u0301', 'Alpha'].map((title, index) => file(String(index), title))
    const hook = renderHook(({ query }) => useFileSearch({ tree, query }), { initialProps: { query: '' } })
    expect(constructor).not.toHaveBeenCalled()

    for (const query of ['missing', '中', 'Alpha']) {
      hook.rerender({ query })
      act(() => vi.advanceTimersByTime(300))
    }
    expect(hook.result.current.results).toHaveLength(2)
    expect(constructor).not.toHaveBeenCalled()

    hook.rerender({ query: 'Needle' })
    expect(constructor).not.toHaveBeenCalled()
    act(() => vi.advanceTimersByTime(300))
    expect(hook.result.current.results).toHaveLength(4)
    expect(constructor).toHaveBeenCalledExactlyOnceWith('zh-CN')
    hook.rerender({ query: 'NEEDLE' })
    act(() => vi.advanceTimersByTime(300))
    hook.unmount()

    const second = renderHook(() => useFileSearch({ tree, query: 'needle' }))
    act(() => vi.advanceTimersByTime(300))
    expect(second.result.current.results).toHaveLength(4)
    expect(constructor).toHaveBeenCalledTimes(1)
    second.unmount()
  })

  it('preserves original localeCompare ordering, stable ties, literal spaces, paths and limits', () => {
    const titles = ['Alpha', 'alpha', 'a', 'z a', ' alpha', '  alpha', 'é', 'e\u0301', 'éclair', 'Éclair', 'tie é', 'tie e\u0301', 'tie é', '章节十', '章节二', '😀开场', '😀', '_notes', 'file2', 'file10', 'İ', 'i', '!draft']
    const parent = file('parent', '父级')
    parent.file_type = 'folder'
    parent.children = titles.map((title, index) => file(`child-${index}`, title, parent.id))
    const entries = [
      { id: parent.id, title: parent.title, fileType: 'folder', parentPath: '', parentId: null },
      ...parent.children.map(node => ({ id: node.id, title: node.title, fileType: node.file_type, parentPath: parent.title, parentId: parent.id })),
    ]
    // Compatibility oracle deliberately retains the original per-file matching
    // and localeCompare, rather than using the optimized comparator.
    const originalResults = (query: string, limit: number) => query.trim() ? entries.map(entry => {
      const title = entry.title.toLowerCase()
      const lowerQuery = query.toLowerCase()
      const score = title === lowerQuery ? 3 : title.startsWith(lowerQuery) ? 2 : title.includes(lowerQuery) ? 1 : 0
      return { entry, score }
    }).filter(match => match.score > 0)
      .sort((a, b) => b.score - a.score || a.entry.title.localeCompare(b.entry.title, 'zh-CN'))
      .slice(0, limit).map(match => match.entry) : []
    const tree = [parent]
    const hook = renderHook(({ query, maxResults }) => useFileSearch({ tree, query, maxResults }), {
      initialProps: { query: '', maxResults: 50 },
    })
    for (const query of ['a', 'é', 'tie', '章节', '😀', 'i', '_', ' a', '  a', '父', '   ', 'not-found']) {
      for (const maxResults of [1, 7, 50]) {
        hook.rerender({ query, maxResults })
        act(() => vi.advanceTimersByTime(300))
        expect(hook.result.current.results, `${JSON.stringify(query)}, limit ${maxResults}`).toEqual(originalResults(query, maxResults))
        expect(hook.result.current.isSearching).toBe(false)
      }
    }
    hook.unmount()
  })
})
