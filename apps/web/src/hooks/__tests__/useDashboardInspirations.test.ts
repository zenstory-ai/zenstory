import { renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useDashboardInspirations } from '../useDashboardInspirations'

const mockLoadBundle = vi.fn()
let mockResolvedLanguage = 'en'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    i18n: {
      language: mockResolvedLanguage,
      resolvedLanguage: mockResolvedLanguage,
    },
  }),
}))

vi.mock('../../lib/dashboardInspirationSource', () => ({
  loadDashboardInspirationBundle: (...args: unknown[]) => mockLoadBundle(...args),
  normalizeDashboardInspirationLocale: (language: string) => (language.startsWith('en') ? 'en' : 'zh'),
}))

describe('useDashboardInspirations', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.spyOn(Date, 'now').mockReturnValue(new Date('2026-04-07T00:00:00Z').getTime())
    mockResolvedLanguage = 'en'
    mockLoadBundle.mockResolvedValue({
      locale: 'en',
      homepagePriority: {
        novel: [
          { id: 'n1', title: 'Novel 1', hook: 'Hook 1', tags: [], source: 'seed' },
          { id: 'n2', title: 'Novel 2', hook: 'Hook 2', tags: [], source: 'seed' },
          { id: 'n3', title: 'Novel 3', hook: 'Hook 3', tags: [], source: 'seed' },
        ],
        short: [{ id: 's1', title: 'Short 1', hook: 'Hook', tags: [], source: 'seed' }],
        screenplay: [{ id: 'sc1', title: 'Screenplay 1', hook: 'Hook', tags: [], source: 'seed' }],
      },
      items: {
        novel: [],
        short: [],
        screenplay: [],
      },
    })
  })

  it('returns an empty list for unsupported tabs', () => {
    const { result } = renderHook(() => useDashboardInspirations('unsupported'))
    expect(result.current).toEqual([])
  })

  it('loads, rotates, and limits inspiration items for supported tabs', async () => {
    const { result } = renderHook(() => useDashboardInspirations('novel', 2, 3))

    await waitFor(() => {
      expect(result.current).toHaveLength(2)
    })

    expect(mockLoadBundle).toHaveBeenCalledWith('en')
    expect(result.current.map((item) => item.id)).toEqual(['n3', 'n1'])
  })

  it('clears items when the bundle cannot be loaded and recovers on refresh', async () => {
    mockLoadBundle.mockResolvedValueOnce(null)
    const { result, rerender } = renderHook(
      ({ seed }) => useDashboardInspirations('short', 3, seed),
      { initialProps: { seed: 0 } },
    )

    await waitFor(() => {
      expect(result.current).toEqual([])
    })

    rerender({ seed: 1 })
    await waitFor(() => {
      expect(result.current.map((item) => item.id)).toEqual(['s1'])
    })
  })
})
