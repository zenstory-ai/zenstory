import { renderHook, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { useFileSearch } from '../useFileSearch'
import type { FileTreeNode } from '@/types'

// Helper to create mock file tree nodes
function createMockTree(nodes: Partial<FileTreeNode>[]): FileTreeNode[] {
  return nodes.map((node) => ({
    id: node.id || 'test-id',
    title: node.title || 'Test Title',
    file_type: node.file_type || 'draft',
    parent_id: node.parent_id || null,
    order: node.order || 0,
    content: node.content || '',
    metadata: node.metadata || null,
    children: node.children || [],
  }))
}

describe('useFileSearch', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  describe('initial state', () => {
    it('initializes with empty results', () => {
      const tree = createMockTree([])
      const { result } = renderHook(() =>
        useFileSearch({ tree, query: '' })
      )

      expect(result.current.results).toEqual([])
      expect(result.current.isSearching).toBe(false)
    })

    it('initializes with custom debounce time', () => {
      const tree = createMockTree([
        { id: '1', title: 'Chapter 1', file_type: 'draft' },
      ])
      const { result } = renderHook(() =>
        useFileSearch({ tree, query: 'chapter', debounceMs: 500 })
      )

      expect(result.current.results).toEqual([])
      expect(result.current.isSearching).toBe(true)

      // Should not search before 500ms
      act(() => {
        vi.advanceTimersByTime(400)
      })
      expect(result.current.results).toEqual([])

      // Should search after 500ms
      act(() => {
        vi.advanceTimersByTime(100)
      })
      expect(result.current.results).toHaveLength(1)
    })

    it('initializes with custom max results', () => {
      const tree = createMockTree([
        { id: '1', title: 'Chapter 1', file_type: 'draft' },
        { id: '2', title: 'Chapter 2', file_type: 'draft' },
        { id: '3', title: 'Chapter 3', file_type: 'draft' },
      ])
      const { result } = renderHook(() =>
        useFileSearch({ tree, query: 'Chapter', maxResults: 2 })
      )

      act(() => {
        vi.advanceTimersByTime(300)
      })

      expect(result.current.results).toHaveLength(2)
    })
  })

  describe('search operations', () => {
    it('returns matching results after debounce', () => {
      const tree = createMockTree([
        { id: '1', title: 'Chapter One', file_type: 'draft' },
        { id: '2', title: 'Chapter Two', file_type: 'draft' },
        { id: '3', title: 'Introduction', file_type: 'outline' },
      ])
      const { result } = renderHook(() =>
        useFileSearch({ tree, query: 'Chapter' })
      )

      // Before debounce
      expect(result.current.results).toEqual([])
      expect(result.current.isSearching).toBe(true)

      // After debounce
      act(() => {
        vi.advanceTimersByTime(300)
      })

      expect(result.current.results).toHaveLength(2)
      expect(result.current.isSearching).toBe(false)
    })

    it('clears results when query is cleared', () => {
      const tree = createMockTree([
        { id: '1', title: 'Chapter One', file_type: 'draft' },
      ])
      const { result, rerender } = renderHook(
        ({ query }) => useFileSearch({ tree, query }),
        { initialProps: { query: 'Chapter' } }
      )

      act(() => {
        vi.advanceTimersByTime(300)
      })

      expect(result.current.results).toHaveLength(1)

      // Clear query
      rerender({ query: '' })

      expect(result.current.results).toEqual([])
      expect(result.current.isSearching).toBe(false)
    })

    it('returns empty results when no matches found', () => {
      const tree = createMockTree([
        { id: '1', title: 'Chapter One', file_type: 'draft' },
        { id: '2', title: 'Introduction', file_type: 'outline' },
      ])
      const { result } = renderHook(() =>
        useFileSearch({ tree, query: 'Nonexistent' })
      )

      act(() => {
        vi.advanceTimersByTime(300)
      })

      expect(result.current.results).toEqual([])
      expect(result.current.isSearching).toBe(false)
    })

    it('handles empty query without searching', () => {
      const tree = createMockTree([
        { id: '1', title: 'Chapter One', file_type: 'draft' },
      ])
      const { result } = renderHook(() =>
        useFileSearch({ tree, query: '' })
      )

      act(() => {
        vi.advanceTimersByTime(300)
      })

      expect(result.current.results).toEqual([])
      expect(result.current.isSearching).toBe(false)
    })

    it('handles whitespace-only query without searching', () => {
      const tree = createMockTree([
        { id: '1', title: 'Chapter One', file_type: 'draft' },
      ])
      const { result } = renderHook(() =>
        useFileSearch({ tree, query: '   ' })
      )

      act(() => {
        vi.advanceTimersByTime(300)
      })

      expect(result.current.results).toEqual([])
      expect(result.current.isSearching).toBe(false)
    })

    it('debounces rapid query changes', () => {
      const tree = createMockTree([
        { id: '1', title: 'Chapter One', file_type: 'draft' },
        { id: '2', title: 'Chapter Two', file_type: 'draft' },
      ])
      const { result, rerender } = renderHook(
        ({ query }) => useFileSearch({ tree, query }),
        { initialProps: { query: 'Cha' } }
      )

      // First change
      act(() => {
        vi.advanceTimersByTime(100)
      })
      expect(result.current.results).toEqual([])

      // Second change before debounce completes
      rerender({ query: 'Chap' })
      act(() => {
        vi.advanceTimersByTime(100)
      })
      expect(result.current.results).toEqual([])

      // Third change
      rerender({ query: 'Chapter' })
      act(() => {
        vi.advanceTimersByTime(100)
      })
      expect(result.current.results).toEqual([])

      // After full debounce
      act(() => {
        vi.advanceTimersByTime(200)
      })
      expect(result.current.results).toHaveLength(2)
    })

    it('updates results when tree changes', () => {
      const tree1 = createMockTree([
        { id: '1', title: 'Chapter One', file_type: 'draft' },
      ])
      const { result, rerender } = renderHook(
        ({ tree }) => useFileSearch({ tree, query: 'Chapter' }),
        { initialProps: { tree: tree1 } }
      )

      act(() => {
        vi.advanceTimersByTime(300)
      })

      expect(result.current.results).toHaveLength(1)

      // Update tree with more results
      const tree2 = createMockTree([
        { id: '1', title: 'Chapter One', file_type: 'draft' },
        { id: '2', title: 'Chapter Two', file_type: 'draft' },
      ])
      rerender({ tree: tree2 })

      act(() => {
        vi.advanceTimersByTime(300)
      })

      expect(result.current.results).toHaveLength(2)
    })

    it('clears previous debounce timer on new query', () => {
      const tree = createMockTree([
        { id: '1', title: 'Chapter One', file_type: 'draft' },
      ])
      const { result, rerender } = renderHook(
        ({ query }) => useFileSearch({ tree, query }),
        { initialProps: { query: 'Chap' } }
      )

      // Start debounce
      act(() => {
        vi.advanceTimersByTime(200)
      })
      expect(result.current.results).toEqual([])

      // Change query - should reset timer
      rerender({ query: 'Chapter' })

      // Advance time but not enough for new debounce
      act(() => {
        vi.advanceTimersByTime(200)
      })
      expect(result.current.results).toEqual([])

      // Complete new debounce
      act(() => {
        vi.advanceTimersByTime(100)
      })
      expect(result.current.results).toHaveLength(1)
    })
  })

  describe('fuzzy matching', () => {
    it('matches case-insensitively', () => {
      const tree = createMockTree([
        { id: '1', title: 'CHAPTER ONE', file_type: 'draft' },
        { id: '2', title: 'chapter two', file_type: 'draft' },
      ])
      const { result } = renderHook(() =>
        useFileSearch({ tree, query: 'chapter' })
      )

      act(() => {
        vi.advanceTimersByTime(300)
      })

      expect(result.current.results).toHaveLength(2)
    })

    it('matches partial titles', () => {
      const tree = createMockTree([
        { id: '1', title: 'The Great Adventure', file_type: 'draft' },
        { id: '2', title: 'Adventure Time', file_type: 'draft' },
        { id: '3', title: 'No Match Here', file_type: 'draft' },
      ])
      const { result } = renderHook(() =>
        useFileSearch({ tree, query: 'Adventure' })
      )

      act(() => {
        vi.advanceTimersByTime(300)
      })

      expect(result.current.results).toHaveLength(2)
    })

    it('ranks exact matches highest', () => {
      const tree = createMockTree([
        { id: '1', title: 'Chapter', file_type: 'draft' },
        { id: '2', title: 'Chapter One', file_type: 'draft' },
        { id: '3', title: 'The Chapter', file_type: 'draft' },
      ])
      const { result } = renderHook(() =>
        useFileSearch({ tree, query: 'Chapter' })
      )

      act(() => {
        vi.advanceTimersByTime(300)
      })

      // Exact match should be first
      expect(result.current.results[0].id).toBe('1')
      expect(result.current.results[0].title).toBe('Chapter')
    })

    it('ranks prefix matches over contains matches', () => {
      const tree = createMockTree([
        { id: '1', title: 'Chapter One', file_type: 'draft' },
        { id: '2', title: 'The Chapter', file_type: 'draft' },
      ])
      const { result } = renderHook(() =>
        useFileSearch({ tree, query: 'Chapter' })
      )

      act(() => {
        vi.advanceTimersByTime(300)
      })

      // Prefix match should be first
      expect(result.current.results[0].id).toBe('1')
    })

    it('sorts by title when scores are equal', () => {
      const tree = createMockTree([
        { id: '1', title: 'Zebra Chapter', file_type: 'draft' },
        { id: '2', title: 'Apple Chapter', file_type: 'draft' },
        { id: '3', title: 'Mango Chapter', file_type: 'draft' },
      ])
      const { result } = renderHook(() =>
        useFileSearch({ tree, query: 'Chapter' })
      )

      act(() => {
        vi.advanceTimersByTime(300)
      })

      // All have same score (contains), should be sorted by title
      expect(result.current.results[0].title).toBe('Apple Chapter')
      expect(result.current.results[1].title).toBe('Mango Chapter')
      expect(result.current.results[2].title).toBe('Zebra Chapter')
    })

    it('supports Chinese characters', () => {
      const tree = createMockTree([
        { id: '1', title: '第一章', file_type: 'draft' },
        { id: '2', title: '第二章', file_type: 'draft' },
        { id: '3', title: 'Chapter One', file_type: 'draft' },
      ])
      const { result } = renderHook(() =>
        useFileSearch({ tree, query: '章' })
      )

      act(() => {
        vi.advanceTimersByTime(300)
      })

      expect(result.current.results).toHaveLength(2)
    })
  })

  describe('hierarchical tree structure', () => {
    it('includes parent path in results', () => {
      const tree = createMockTree([
        {
          id: '1',
          title: 'Part One',
          file_type: 'folder',
          children: [
            {
              id: '2',
              title: 'Chapter One',
              file_type: 'draft',
              parent_id: '1',
            } as FileTreeNode,
          ],
        },
      ])
      const { result } = renderHook(() =>
        useFileSearch({ tree, query: 'Chapter' })
      )

      act(() => {
        vi.advanceTimersByTime(300)
      })

      expect(result.current.results).toHaveLength(1)
      expect(result.current.results[0].parentPath).toBe('Part One')
      expect(result.current.results[0].parentId).toBe('1')
    })

    it('searches nested children', () => {
      const tree = createMockTree([
        {
          id: '1',
          title: 'Book',
          file_type: 'folder',
          children: [
            {
              id: '2',
              title: 'Part One',
              file_type: 'folder',
              parent_id: '1',
              children: [
                {
                  id: '3',
                  title: 'Chapter One',
                  file_type: 'draft',
                  parent_id: '2',
                } as FileTreeNode,
              ],
            } as FileTreeNode,
          ],
        },
      ])
      const { result } = renderHook(() =>
        useFileSearch({ tree, query: 'Chapter' })
      )

      act(() => {
        vi.advanceTimersByTime(300)
      })

      expect(result.current.results).toHaveLength(1)
      expect(result.current.results[0].parentPath).toBe('Book > Part One')
    })

    it('includes all file types in search', () => {
      const tree = createMockTree([
        { id: '1', title: 'Story Outline', file_type: 'outline' },
        { id: '2', title: 'Story Draft', file_type: 'draft' },
        { id: '3', title: 'Main Character', file_type: 'character' },
        { id: '4', title: 'World Lore', file_type: 'lore' },
        { id: '5', title: 'Reference Material', file_type: 'material' },
      ])
      const { result } = renderHook(() =>
        useFileSearch({ tree, query: 'Story' })
      )

      act(() => {
        vi.advanceTimersByTime(300)
      })

      expect(result.current.results).toHaveLength(2)
      expect(result.current.results.map((r) => r.fileType)).toContain('outline')
      expect(result.current.results.map((r) => r.fileType)).toContain('draft')
    })
  })

  describe('clearSearch', () => {
    it('clears results when clearSearch is called', () => {
      const tree = createMockTree([
        { id: '1', title: 'Chapter One', file_type: 'draft' },
      ])
      const { result } = renderHook(() =>
        useFileSearch({ tree, query: 'Chapter' })
      )

      act(() => {
        vi.advanceTimersByTime(300)
      })

      expect(result.current.results).toHaveLength(1)

      act(() => {
        result.current.clearSearch()
      })

      expect(result.current.results).toEqual([])
      expect(result.current.isSearching).toBe(false)
    })

    it('clears pending debounce timer', () => {
      const tree = createMockTree([
        { id: '1', title: 'Chapter One', file_type: 'draft' },
      ])
      const { result } = renderHook(() =>
        useFileSearch({ tree, query: 'Chapter' })
      )

      // Start debounce but don't complete
      act(() => {
        vi.advanceTimersByTime(100)
      })

      act(() => {
        result.current.clearSearch()
      })

      // Advance past original debounce time
      act(() => {
        vi.advanceTimersByTime(200)
      })

      // Results should still be empty
      expect(result.current.results).toEqual([])
    })

    it('stops searching state immediately', () => {
      const tree = createMockTree([
        { id: '1', title: 'Chapter One', file_type: 'draft' },
      ])
      const { result } = renderHook(() =>
        useFileSearch({ tree, query: 'Chapter' }
      ))

      expect(result.current.isSearching).toBe(true)

      act(() => {
        result.current.clearSearch()
      })

      expect(result.current.isSearching).toBe(false)
    })
  })

  describe('result limiting', () => {
    it('limits results to maxResults', () => {
      const tree = createMockTree(
        Array.from({ length: 100 }, (_, i) => ({
          id: `file-${i}`,
          title: `Chapter ${i}`,
          file_type: 'draft',
        }))
      )
      const { result } = renderHook(() =>
        useFileSearch({ tree, query: 'Chapter', maxResults: 10 })
      )

      act(() => {
        vi.advanceTimersByTime(300)
      })

      expect(result.current.results).toHaveLength(10)
    })

    it('returns top-scored results when limiting', () => {
      const tree = createMockTree([
        { id: '1', title: 'Chapter', file_type: 'draft' },
        { id: '2', title: 'Chapter One', file_type: 'draft' },
        { id: '3', title: 'The Chapter', file_type: 'draft' },
        { id: '4', title: 'Another Chapter', file_type: 'draft' },
      ])
      const { result } = renderHook(() =>
        useFileSearch({ tree, query: 'Chapter', maxResults: 2 })
      )

      act(() => {
        vi.advanceTimersByTime(300)
      })

      // Should include exact match and prefix match
      expect(result.current.results).toHaveLength(2)
      expect(result.current.results[0].id).toBe('1') // Exact match
    })

    it('uses default maxResults of 50', () => {
      const tree = createMockTree(
        Array.from({ length: 75 }, (_, i) => ({
          id: `file-${i}`,
          title: `Chapter ${i}`,
          file_type: 'draft',
        }))
      )
      const { result } = renderHook(() =>
        useFileSearch({ tree, query: 'Chapter' })
      )

      act(() => {
        vi.advanceTimersByTime(300)
      })

      expect(result.current.results).toHaveLength(50)
    })
  })

  describe('result format', () => {
    it('includes all required fields in results', () => {
      const tree = createMockTree([
        { id: 'test-id', title: 'Test Title', file_type: 'draft', parent_id: 'parent-id' },
      ])
      const { result } = renderHook(() =>
        useFileSearch({ tree, query: 'Test' })
      )

      act(() => {
        vi.advanceTimersByTime(300)
      })

      expect(result.current.results).toHaveLength(1)
      const searchResult = result.current.results[0]
      expect(searchResult).toHaveProperty('id', 'test-id')
      expect(searchResult).toHaveProperty('title', 'Test Title')
      expect(searchResult).toHaveProperty('fileType', 'draft')
      expect(searchResult).toHaveProperty('parentPath', '')
      expect(searchResult).toHaveProperty('parentId', 'parent-id')
    })

    it('formats parent path with > separator', () => {
      const tree = createMockTree([
        {
          id: '1',
          title: 'Book',
          file_type: 'folder',
          children: [
            {
              id: '2',
              title: 'Part',
              file_type: 'folder',
              parent_id: '1',
              children: [
                {
                  id: '3',
                  title: 'Chapter',
                  file_type: 'draft',
                  parent_id: '2',
                } as FileTreeNode,
              ],
            } as FileTreeNode,
          ],
        },
      ])
      const { result } = renderHook(() =>
        useFileSearch({ tree, query: 'Chapter' })
      )

      act(() => {
        vi.advanceTimersByTime(300)
      })

      expect(result.current.results[0].parentPath).toBe('Book > Part')
    })

    it('returns empty parent path for root level files', () => {
      const tree = createMockTree([
        { id: '1', title: 'Root File', file_type: 'draft', parent_id: null },
      ])
      const { result } = renderHook(() =>
        useFileSearch({ tree, query: 'Root' })
      )

      act(() => {
        vi.advanceTimersByTime(300)
      })

      expect(result.current.results[0].parentPath).toBe('')
    })
  })

  describe('cleanup', () => {
    it('clears timeout on unmount', () => {
      const clearTimeoutSpy = vi.spyOn(globalThis, 'clearTimeout')
      const tree = createMockTree([
        { id: '1', title: 'Chapter One', file_type: 'draft' },
      ])
      const { result, unmount } = renderHook(() =>
        useFileSearch({ tree, query: 'Chapter' })
      )

      // Start debounce
      act(() => {
        vi.advanceTimersByTime(100)
      })
      expect(result.current.results).toEqual([])

      // Unmount before debounce completes
      unmount()
      expect(clearTimeoutSpy).toHaveBeenCalled()

      // Advance past debounce time - search must not update after unmount
      act(() => {
        vi.advanceTimersByTime(200)
      })
      expect(result.current.results).toEqual([])
      clearTimeoutSpy.mockRestore()
    })

    it('clears timeout when query changes', () => {
      const tree = createMockTree([
        { id: '1', title: 'Chapter One', file_type: 'draft' },
      ])
      const { result, rerender } = renderHook(
        ({ query }) => useFileSearch({ tree, query }),
        { initialProps: { query: 'Chap' } }
      )

      // Start first debounce
      act(() => {
        vi.advanceTimersByTime(200)
      })

      // Change query - should clear first timeout
      rerender({ query: 'Chapter' })

      // Complete new debounce
      act(() => {
        vi.advanceTimersByTime(300)
      })

      expect(result.current.results).toHaveLength(1)
    })
  })

  describe('edge cases', () => {
    it('handles empty tree', () => {
      const tree: FileTreeNode[] = []
      const { result } = renderHook(() =>
        useFileSearch({ tree, query: 'Anything' })
      )

      act(() => {
        vi.advanceTimersByTime(300)
      })

      expect(result.current.results).toEqual([])
      expect(result.current.isSearching).toBe(false)
    })

    it('handles special characters in query', () => {
      const tree = createMockTree([
        { id: '1', title: 'Chapter [Draft]', file_type: 'draft' },
        { id: '2', title: 'Chapter (Final)', file_type: 'draft' },
      ])
      const { result } = renderHook(() =>
        useFileSearch({ tree, query: '[Draft]' })
      )

      act(() => {
        vi.advanceTimersByTime(300)
      })

      expect(result.current.results).toHaveLength(1)
      expect(result.current.results[0].title).toBe('Chapter [Draft]')
    })

    it('handles unicode characters', () => {
      const tree = createMockTree([
        { id: '1', title: 'Chapter - Test', file_type: 'draft' },
        { id: '2', title: 'Chapter \u2014 Em Dash', file_type: 'draft' },
        { id: '3', title: 'Chapter \u2013 En Dash', file_type: 'draft' },
      ])
      const { result } = renderHook(() =>
        useFileSearch({ tree, query: 'Chapter' })
      )

      act(() => {
        vi.advanceTimersByTime(300)
      })

      expect(result.current.results).toHaveLength(3)
    })

    it('handles very long queries', () => {
      const longQuery = 'Chapter'.repeat(50)
      const tree = createMockTree([
        { id: '1', title: 'Chapter One', file_type: 'draft' },
      ])
      const { result } = renderHook(() =>
        useFileSearch({ tree, query: longQuery })
      )

      act(() => {
        vi.advanceTimersByTime(300)
      })

      // Should not match (query too long)
      expect(result.current.results).toEqual([])
    })

    it('handles deeply nested trees', () => {
      // Test deep nesting instead of circular references
      // Create a tree with 5 levels of nesting
      let currentNode: FileTreeNode = {
        id: 'leaf',
        title: 'Leaf Chapter',
        file_type: 'draft',
        parent_id: null,
        order: 0,
        content: '',
        metadata: null,
        children: [],
      }

      // Build tree from bottom up
      for (let i = 5; i >= 1; i--) {
        currentNode = {
          id: `level-${i}`,
          title: `Level ${i}`,
          file_type: 'folder',
          parent_id: i > 1 ? `level-${i - 1}` : null,
          order: 0,
          content: '',
          metadata: null,
          children: [currentNode],
        }
      }

      const tree = [currentNode]

      const { result } = renderHook(() =>
        useFileSearch({ tree, query: 'Leaf' })
      )

      act(() => {
        vi.advanceTimersByTime(300)
      })

      // Should find the deeply nested leaf
      expect(result.current.results).toHaveLength(1)
      expect(result.current.results[0].title).toBe('Leaf Chapter')
      // Verify parent path is built correctly through all levels
      expect(result.current.results[0].parentPath).toContain('Level')
    })

    it('handles nodes without title gracefully', () => {
      const tree = createMockTree([
        { id: '1', title: '', file_type: 'draft' },
        { id: '2', title: 'Valid Title', file_type: 'draft' },
      ])
      const { result } = renderHook(() =>
        useFileSearch({ tree, query: 'Valid' })
      )

      act(() => {
        vi.advanceTimersByTime(300)
      })

      expect(result.current.results).toHaveLength(1)
    })

    it('handles null/undefined children gracefully', () => {
      const tree = createMockTree([
        {
          id: '1',
          title: 'Parent',
          file_type: 'folder',
          children: null as unknown as FileTreeNode[],
        },
      ])
      const { result } = renderHook(() =>
        useFileSearch({ tree, query: 'Parent' })
      )

      act(() => {
        vi.advanceTimersByTime(300)
      })

      expect(result.current.results).toHaveLength(1)
    })
  })

  describe('isSearching state', () => {
    it('sets isSearching to true when query is set', () => {
      const tree = createMockTree([
        { id: '1', title: 'Chapter One', file_type: 'draft' },
      ])
      const { result } = renderHook(() =>
        useFileSearch({ tree, query: 'Chapter' })
      )

      expect(result.current.isSearching).toBe(true)
    })

    it('sets isSearching to false after search completes', () => {
      const tree = createMockTree([
        { id: '1', title: 'Chapter One', file_type: 'draft' },
      ])
      const { result } = renderHook(() =>
        useFileSearch({ tree, query: 'Chapter' })
      )

      expect(result.current.isSearching).toBe(true)

      act(() => {
        vi.advanceTimersByTime(300)
      })

      expect(result.current.isSearching).toBe(false)
    })

    it('sets isSearching to true again when query changes', () => {
      const tree = createMockTree([
        { id: '1', title: 'Chapter One', file_type: 'draft' },
        { id: '2', title: 'Introduction', file_type: 'outline' },
      ])
      const { result, rerender } = renderHook(
        ({ query }) => useFileSearch({ tree, query }),
        { initialProps: { query: 'Chapter' } }
      )

      act(() => {
        vi.advanceTimersByTime(300)
      })

      expect(result.current.isSearching).toBe(false)

      // Change query
      rerender({ query: 'Intro' })

      expect(result.current.isSearching).toBe(true)

      act(() => {
        vi.advanceTimersByTime(300)
      })

      expect(result.current.isSearching).toBe(false)
    })

    it('sets isSearching to false when query is cleared', () => {
      const tree = createMockTree([
        { id: '1', title: 'Chapter One', file_type: 'draft' },
      ])
      const { result, rerender } = renderHook(
        ({ query }) => useFileSearch({ tree, query }),
        { initialProps: { query: 'Chapter' } }
      )

      expect(result.current.isSearching).toBe(true)

      rerender({ query: '' })

      expect(result.current.isSearching).toBe(false)
    })

    it('sets isSearching to false for whitespace-only query', () => {
      const tree = createMockTree([
        { id: '1', title: 'Chapter One', file_type: 'draft' },
      ])
      const { result, rerender } = renderHook(
        ({ query }) => useFileSearch({ tree, query }),
        { initialProps: { query: 'Chapter' } }
      )

      expect(result.current.isSearching).toBe(true)

      rerender({ query: '   ' })

      expect(result.current.isSearching).toBe(false)
    })
  })

  describe('re-render behavior', () => {
    it('does not recreate results on unrelated re-renders', () => {
      const tree = createMockTree([
        { id: '1', title: 'Chapter One', file_type: 'draft' },
      ])
      const { result, rerender } = renderHook(
        ({ query }) => useFileSearch({ tree, query }),
        { initialProps: { query: 'Chapter' } }
      )

      act(() => {
        vi.advanceTimersByTime(300)
      })

      const firstResults = result.current.results

      // Re-render with same query
      rerender({ query: 'Chapter' })

      act(() => {
        vi.advanceTimersByTime(300)
      })

      // Results should be stable (same reference if content unchanged)
      expect(result.current.results).toEqual(firstResults)
    })
  })
})
