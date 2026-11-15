import { renderHook, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { useExport } from '../useExport'

const mockExportDrafts = vi.fn()

vi.mock('@/lib/api', () => ({
  exportApi: {
    exportDrafts: (...args: unknown[]) => mockExportDrafts(...args),
  },
}))

describe('useExport', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockExportDrafts.mockReset()
  })

  describe('initial state', () => {
    it('initializes with idle state', () => {
      const { result } = renderHook(() => useExport('project-123'))
      expect(result.current.loading).toBe(false)
    })

    it('handles null projectId', () => {
      const { result } = renderHook(() => useExport(null))
      expect(result.current.loading).toBe(false)
    })
  })

  describe('exportDrafts', () => {
    it('returns early when projectId is null', async () => {
      const { result } = renderHook(() => useExport(null))

      await act(async () => {
        await result.current.exportDrafts()
      })

      expect(mockExportDrafts).not.toHaveBeenCalled()
    })

    it('calls export API with correct projectId', async () => {
      mockExportDrafts.mockResolvedValueOnce({ filename: 'drafts.txt' })

      const { result } = renderHook(() => useExport('project-123'))

      await act(async () => {
        await result.current.exportDrafts()
      })

      expect(mockExportDrafts).toHaveBeenCalledWith('project-123')
    })

    it('sets loading to true during export', async () => {
      let resolveExport: () => void
      mockExportDrafts.mockImplementationOnce(
        () => new Promise<void>((resolve) => {
          resolveExport = resolve
        })
      )

      const { result } = renderHook(() => useExport('project-123'))

      let exportPromise: Promise<unknown>
      act(() => {
        exportPromise = result.current.exportDrafts()
      })

      expect(result.current.loading).toBe(true)

      await act(async () => {
        resolveExport!()
        await exportPromise!
      })

      expect(result.current.loading).toBe(false)
    })

    it('re-throws error for callers and resets loading state', async () => {
      mockExportDrafts.mockRejectedValueOnce(new Error('Export failed'))

      const { result } = renderHook(() => useExport('project-123'))

      let thrownError: Error | null = null
      await act(async () => {
        try {
          await result.current.exportDrafts()
        } catch (e) {
          thrownError = e as Error
        }
      })

      expect(thrownError?.message).toBe('Export failed')
      expect(result.current.loading).toBe(false)
    })
  })

  describe('state stability', () => {
    it('returns stable exportDrafts function', () => {
      const { result, rerender } = renderHook(() => useExport('project-123'))
      const firstExportDrafts = result.current.exportDrafts
      rerender()
      expect(result.current.exportDrafts).toBe(firstExportDrafts)
    })
  })
})
